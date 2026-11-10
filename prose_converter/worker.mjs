import { createInterface } from 'node:readline';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import JSZip from 'jszip';
import { JSDOM } from 'jsdom';
import {
  cfiToXPointer,
  getKoreaderFileDigest,
  parseXPointer,
  xPointerToCfi,
} from '@prose-reader/koreader';

const MAX_EPUB_BYTES = 64 * 1024 * 1024;
const MAX_XML_BYTES = 4 * 1024 * 1024;

const spineIndexOf = (kind, locator) => {
  if (kind === 'xpointer') return parseXPointer(locator)?.spineItemIndex;
  const match = /^epubcfi\(\/6\/(\d+)(?:\[|\/|!)/i.exec(locator);
  const step = match ? Number(match[1]) : NaN;
  return Number.isSafeInteger(step) && step > 0 && step % 2 === 0
    ? step / 2 - 1 : undefined;
};

const readZipText = async (zip, name) => {
  const entry = zip.file(name);
  if (!entry || entry.dir || entry._data.uncompressedSize > MAX_XML_BYTES) {
    throw new Error('EPUB spine resource is missing or too large');
  }
  const data = await entry.async('nodebuffer');
  if (data.length > MAX_XML_BYTES) throw new Error('EPUB spine resource is too large');
  return data.toString('utf8');
};

const xmlDocument = (source) => {
  const dom = new JSDOM(source, { contentType: 'application/xhtml+xml' });
  if (dom.window.document.querySelector('parsererror')) {
    dom.window.close();
    throw new Error('EPUB XML cannot be parsed');
  }
  return dom;
};

const zipPath = (base, href) => {
  const decoded = decodeURIComponent(href.split('#', 1)[0]);
  const joined = path.posix.normalize(path.posix.join(base, decoded));
  if (joined.startsWith('../') || joined.startsWith('/') || joined === '..') {
    throw new Error('EPUB spine path escapes the archive');
  }
  return joined;
};

let cachedBook;

const loadBook = async (file, hash) => {
  const info = await stat(file);
  if (!info.isFile() || info.size > MAX_EPUB_BYTES) throw new Error('EPUB is too large');
  if (cachedBook && cachedBook.file === file && cachedBook.hash === hash &&
      cachedBook.size === info.size && cachedBook.mtimeMs === info.mtimeMs) {
    return cachedBook;
  }
  cachedBook?.spineDom?.window.close();
  cachedBook = undefined;
  const data = await readFile(file);
  const digest = await getKoreaderFileDigest(new Blob([data]));
  if (digest?.toLowerCase() !== hash.toLowerCase()) {
    return undefined;
  }
  const zip = await JSZip.loadAsync(data, { checkCRC32: false });
  const container = xmlDocument(await readZipText(zip, 'META-INF/container.xml'));
  try {
    const rootfile = container.window.document.querySelector('rootfile');
    const opfPath = rootfile?.getAttribute('full-path');
    if (!opfPath) throw new Error('EPUB package path is missing');
    const opf = xmlDocument(await readZipText(zip, opfPath));
    try {
      const itemrefs = [...opf.window.document.querySelectorAll('spine > itemref')];
      const manifest = new Map([...opf.window.document.querySelectorAll('manifest > item')]
        .map((item) => [item.getAttribute('id'), item.getAttribute('href')]));
      cachedBook = {
        file, hash, size: info.size, mtimeMs: info.mtimeMs, zip,
        spine: itemrefs.map((item) => {
          const id = item.getAttribute('idref');
          const href = manifest.get(id);
          return id && href ? { id, path: zipPath(path.posix.dirname(opfPath), href) } : undefined;
        }),
      };
      return cachedBook;
    } finally {
      opf.window.close();
    }
  } finally {
    container.window.close();
  }
};

const convert = async ({ file, hash, kind, locator }) => {
  if (!['cfi', 'xpointer'].includes(kind) || typeof locator !== 'string' ||
      typeof file !== 'string' || typeof hash !== 'string') {
    throw new Error('Invalid conversion request');
  }
  const index = spineIndexOf(kind, locator);
  if (index === undefined) return { status: 'unsupported' };
  const book = await loadBook(file, hash);
  if (!book) return { status: 'identity-mismatch' };
  const item = book.spine[index];
  if (!item) return { status: 'unsupported' };
  if (book.spineIndex !== index || !book.spineDom) {
    book.spineDom?.window.close();
    book.spineDom = xmlDocument(await readZipText(book.zip, item.path));
    book.spineIndex = index;
  }
  // @prose-reader/cfi uses the browser's global Node constants.
  globalThis.Node = book.spineDom.window.Node;
  const lookup = (requested) => requested === index
    ? { document: book.spineDom.window.document, id: item.id } : undefined;
  const converted = kind === 'cfi'
    ? cfiToXPointer(locator, lookup)
    : xPointerToCfi(locator, lookup);
  return converted ? { status: 'ok', locator: converted } : { status: 'unsupported' };
};

for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  try {
    const result = await convert(JSON.parse(line));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({ status: 'failed', reason: String(error?.message || error) })}\n`);
  }
}
