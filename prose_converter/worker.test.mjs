import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import JSZip from 'jszip';
import { JSDOM } from 'jsdom';
import { cfiToXPointer, getKoreaderFileDigest, xPointerToCfi } from '@prose-reader/koreader';

const zip = new JSZip();
zip.file('mimetype', 'application/epub+zip');
zip.file('META-INF/container.xml', '<?xml version="1.0"?><container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>');
zip.file('OEBPS/content.opf', '<?xml version="1.0"?><package><manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/><item id="chapter2" href="chapter2.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="chapter"/><itemref idref="chapter2"/></spine></package>');
zip.file('OEBPS/chapter.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="p">Hello world</p></body></html>');
zip.file('OEBPS/chapter2.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="p">Second chapter</p></body></html>');
const epub = await zip.generateAsync({ type: 'nodebuffer' });
const hash = await getKoreaderFileDigest(new Blob([epub]));
const testRoot = path.resolve('..', '.test-tmp');
await mkdir(testRoot, { recursive: true });
const file = path.join(testRoot, 'prose-synthetic.epub');
const worker = fileURLToPath(new URL('./worker.mjs', import.meta.url));
await writeFile(file, epub);
const doc = new JSDOM('<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="p">Hello world</p></body></html>', { contentType: 'application/xhtml+xml' });
globalThis.Node = doc.window.Node;
const lookup = () => ({ document: doc.window.document, id: 'chapter' });
const cfi = xPointerToCfi('/body/DocFragment[1]/body/p/text().3', lookup);
assert.ok(cfi, 'A synthetic EPUB CFI should resolve in Prose');
assert.ok(cfiToXPointer(cfi, () => ({ document: doc.window.document, id: 'chapter' })));
doc.window.close();
const secondDoc = new JSDOM('<html xmlns="http://www.w3.org/1999/xhtml"><body><p id="p">Second chapter</p></body></html>', { contentType: 'application/xhtml+xml' });
globalThis.Node = secondDoc.window.Node;
const secondCfi = xPointerToCfi('/body/DocFragment[2]/body/p/text().2',
  () => ({ document: secondDoc.window.document, id: 'chapter2' }));
assert.ok(secondCfi);
secondDoc.window.close();
const requests = [
  { file, hash, kind: 'cfi', locator: cfi },
  { file, hash, kind: 'xpointer', locator: '/body/DocFragment[1]/body/p/text().3' },
  { file, hash, kind: 'cfi', locator: secondCfi },
  { file, hash: '0'.repeat(hash.length), kind: 'cfi', locator: cfi },
];
const run = spawnSync(process.execPath, [worker], {
  input: `${requests.map((request) => JSON.stringify(request)).join('\n')}\n`, encoding: 'utf8',
});
assert.equal(run.status, 0, run.stderr);
const [xpointer, back, second, wrong] = run.stdout.trim().split('\n').map((line) => JSON.parse(line));
assert.equal(xpointer.status, 'ok', run.stdout);
assert.equal(back.status, 'ok', run.stdout);
assert.equal(back.locator, cfi);
assert.equal(second.status, 'ok');
assert.equal(second.locator, '/body/DocFragment[2]/body/p/text().2');
assert.equal(wrong.status, 'identity-mismatch');
process.stdout.write('Prose EPUB round trip and identity guard passed\n');
process.exit(0);
