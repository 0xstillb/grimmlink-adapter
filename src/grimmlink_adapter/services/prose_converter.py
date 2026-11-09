"""Bounded EPUB CFI/XPointer conversion with the Prose Node worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from collections import OrderedDict
from pathlib import Path

from fastapi import HTTPException

from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialClientError
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.progress_service import ProgressService

logger = logging.getLogger(__name__)
MAX_EPUB_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 128 * 1024 * 1024


class ProseConverter:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._worker: asyncio.subprocess.Process | None = None
        self._directory: tempfile.TemporaryDirectory[str] | None = None
        self._files: OrderedDict[tuple[str, int], tuple[Path, int]] = OrderedDict()

    @staticmethod
    def _worker_path() -> Path:
        default = Path(__file__).resolve().parents[3] / "prose_converter" / "worker.mjs"
        return Path(os.getenv("PROSE_WORKER_PATH", str(default))).resolve()

    async def _ensure_worker(self) -> asyncio.subprocess.Process:
        if self._worker is not None and self._worker.returncode is None:
            return self._worker
        self._worker = await asyncio.create_subprocess_exec(
            "node", str(self._worker_path()),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return self._worker

    async def _download(self, book_hash: str, creds: ClientCredentials) -> Path:
        bearer, book_id, _, book_format = await ProgressService._resolve_bearer_book(
            creds, book_hash,
        )
        if str(book_format).upper() != "EPUB":
            raise ValueError("Only an exact EPUB file can be converted")
        cache_key = (book_hash, book_id)
        cached = self._files.get(cache_key)
        if cached is not None and cached[0].is_file():
            self._files.move_to_end(cache_key)
            return cached[0]
        if self._directory is None:
            self._directory = tempfile.TemporaryDirectory(prefix="grimmlink-prose-")
        target = Path(self._directory.name) / hashlib.sha256(f"{book_id}:{book_hash}".encode()).hexdigest()
        client = OfficialGrimmoryClient()
        stream = None
        size = 0
        try:
            stream = await client.download_book_stream(book_id, bearer)
            with target.open("wb") as output:
                async for chunk in stream.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_EPUB_BYTES:
                        raise ValueError("EPUB exceeds converter size limit")
                    output.write(chunk)
            if size == 0:
                raise ValueError("EPUB download was empty")
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            if stream is not None:
                await stream.aclose()
            await client.aclose()
        self._files[cache_key] = (target, size)
        while len(self._files) > 2 or sum(item[1] for item in self._files.values()) > MAX_CACHE_BYTES:
            _, (old_path, _) = self._files.popitem(last=False)
            old_path.unlink(missing_ok=True)
        return target

    async def convert(
        self, book_hash: str, kind: str, locator: str, creds: ClientCredentials,
    ) -> str | None:
        if kind not in {"cfi", "xpointer"} or not locator or len(locator) > 8192:
            return None
        async with self._lock:
            reset_worker = False
            try:
                file = await asyncio.wait_for(self._download(book_hash, creds), timeout=30)
                worker = await self._ensure_worker()
                assert worker.stdin is not None and worker.stdout is not None
                message = {"file": str(file), "hash": book_hash, "kind": kind, "locator": locator}
                worker.stdin.write((json.dumps(message) + "\n").encode())
                await asyncio.wait_for(worker.stdin.drain(), timeout=10)
                response = await asyncio.wait_for(worker.stdout.readline(), timeout=15)
                result = json.loads(response) if response else {}
                if not isinstance(result, dict):
                    raise ValueError("Worker response must be an object")
                converted = result.get("locator")
                if result.get("status") == "ok" and isinstance(converted, str):
                    return converted
                logger.warning("Prose conversion unavailable: %s", result.get("status", "worker-closed"))
                if result.get("status") == "identity-mismatch":
                    for cache_key, (cached_path, _) in list(self._files.items()):
                        if cached_path == file:
                            self._files.pop(cache_key)
                            cached_path.unlink(missing_ok=True)
                            break
            except (OSError, ValueError, HTTPException, OfficialClientError) as exc:
                logger.warning("Prose conversion unavailable: %s", type(exc).__name__)
                reset_worker = isinstance(exc, (TimeoutError, BrokenPipeError, json.JSONDecodeError))
            if reset_worker and self._worker is not None and self._worker.returncode is None:
                self._worker.kill()
                await self._worker.wait()
            if self._worker is not None and self._worker.returncode is not None:
                self._worker = None
            return None


prose_converter = ProseConverter()
