"""Reader-facing progress must select the newest exact EPUB locator."""

import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from grimmlink_adapter.api import v2
from grimmlink_adapter.models.grimmlink import KoreaderProgressPayload
from grimmlink_adapter.models.v2 import V2ProgressPosition, V2ProgressRequest
from grimmlink_adapter.security.auth_extractor import ClientCredentials


class FakeService:
    def __init__(self, book_time: str, native_time: int) -> None:
        self.book_time = book_time
        self.native_time = native_time
        self.written = None

    async def get_progress(self, book_hash, creds):
        return KoreaderProgressPayload(
            document=book_hash, fileFormat="EPUB", cfi="epubcfi(/6/2[chapter]!/4/2/1:3)",
            percentage=20, updatedAt=self.book_time,
        )

    async def get_native_progress(self, book_hash, creds):
        return {
            "document": book_hash, "progress": "/body/DocFragment[1]/body/p/text().7",
            "percentage": 0.5, "timestamp": self.native_time,
        }

    async def update_progress(self, payload, creds):
        self.written = payload
        return {"status": "progress updated", "projection": "official-book-api+koreader-native"}


class NativeOnlyPageService(FakeService):
    async def get_progress(self, book_hash, creds):
        raise HTTPException(status_code=404, detail="No Book API progress")

    async def get_native_progress(self, book_hash, creds):
        return {"document": book_hash, "progress": "7", "percentage": 0.35, "timestamp": 1794225600}

    async def _resolve_bearer_book(self, creds, book_hash):
        return "jwt", 1, 2, "PDF"


class FakeConverter:
    def __init__(self, result: str | None) -> None:
        self.result = result
        self.calls = []

    async def convert(self, book_hash, kind, locator, creds):
        self.calls.append((kind, locator))
        return self.result


class V2ProseProgressTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.creds = ClientCredentials(username="reader", md5_key="md5", bearer_token="jwt")

    async def test_web_reader_newer_is_converted_for_koreader(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794225000)
        converter = FakeConverter("/body/DocFragment[1]/body/p/text().3")
        with patch.object(v2, "_progress_service", service), patch.object(v2, "prose_converter", converter):
            result = await v2.get_v2_progress("hash", "koreader", self.creds)
        self.assertEqual(result["source"], "book-api")
        self.assertEqual(result["position"]["xpointer"], converter.result)
        self.assertEqual(result["fraction"], 0.2)
        self.assertEqual(converter.calls[0][0], "cfi")

    async def test_native_newer_is_converted_for_readest(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794226000)
        converter = FakeConverter("epubcfi(/6/2[chapter]!/4/2/1:7)")
        with patch.object(v2, "_progress_service", service), patch.object(v2, "prose_converter", converter):
            result = await v2.get_v2_progress("hash", "readest", self.creds)
        self.assertEqual(result["source"], "koreader-native")
        self.assertEqual(result["position"]["cfi"], converter.result)
        self.assertEqual(result["fraction"], 0.5)

    async def test_conversion_failure_does_not_return_stale_page(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794225000)
        with patch.object(v2, "_progress_service", service), patch.object(v2, "prose_converter", FakeConverter(None)):
            with self.assertRaises(HTTPException) as caught:
                await v2.get_v2_progress("hash", "koreader", self.creds)
        self.assertEqual(caught.exception.status_code, 424)

    async def test_xpointer_push_projects_both_formats(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794225000)
        converter = FakeConverter("epubcfi(/6/2[chapter]!/4/2/1:7)")
        request = V2ProgressRequest(
            operationId=uuid4(), source="koreader", updatedAt=datetime(2026, 11, 9, 12, tzinfo=UTC),
            fraction=0.5, position=V2ProgressPosition(
                format="EPUB", xpointer="/body/DocFragment[1]/body/p/text().7",
                cfi="epubcfi(/6/2[chapter]!/4/2/1:1)", page=70, totalPages=100,
            ),
        )
        with patch.object(v2, "_progress_service", service), patch.object(v2, "prose_converter", converter):
            result = await v2.put_v2_progress("hash", request, self.creds)
        self.assertEqual(service.written.cfi, converter.result)
        self.assertEqual(service.written.xpointer, request.position.xpointer)
        self.assertEqual(service.written.progress, request.position.xpointer)
        self.assertIsNone(service.written.currentPage)
        self.assertEqual(service.written.percentage, 50.0)
        self.assertEqual(result["epubConversion"], "synced")

    async def test_xpointer_push_requires_cfi_for_continue_reading(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794225000)
        request = V2ProgressRequest(
            operationId=uuid4(), source="koreader", updatedAt=datetime(2026, 11, 9, 12, tzinfo=UTC),
            fraction=0.0003, position=V2ProgressPosition(
                format="EPUB", xpointer="/body/DocFragment[1]/body/p/text().7", page=3,
            ),
        )
        with patch.object(v2, "_progress_service", service), patch.object(v2, "prose_converter", FakeConverter(None)):
            with self.assertRaises(HTTPException) as caught:
                await v2.put_v2_progress("hash", request, self.creds)
        self.assertEqual(caught.exception.status_code, 424)
        self.assertIsNone(service.written)

    async def test_status_failure_does_not_claim_complete_v2_sync(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794225000)

        async def failed_status(payload, creds):
            service.written = payload
            return {"status": "progress updated", "projection": "official-book-api", "readStatus": {"status": "failed"}}

        service.update_progress = failed_status
        request = V2ProgressRequest(
            operationId=uuid4(), source="readest", updatedAt=datetime(2026, 11, 9, 12, tzinfo=UTC),
            fraction=0.0003, position=V2ProgressPosition(format="PDF", page=3, totalPages=10000),
        )
        with patch.object(v2, "_progress_service", service):
            with self.assertRaises(HTTPException) as caught:
                await v2.put_v2_progress("hash", request, self.creds)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIsNotNone(service.written)

    async def test_readest_push_replaces_unverified_xpointer(self):
        service = FakeService("2026-11-09T12:00:00Z", 1794225000)
        converter = FakeConverter("/body/DocFragment[1]/body/p/text().8")
        request = V2ProgressRequest(
            operationId=uuid4(), source="readest", updatedAt=datetime(2026, 11, 9, 12, tzinfo=UTC),
            fraction=0.2, position=V2ProgressPosition(
                format="EPUB", cfi="epubcfi(/6/2[chapter]!/4/2/1:8)",
                xpointer="/body/DocFragment[1]/body/p/text().1",
            ),
        )
        with patch.object(v2, "_progress_service", service), patch.object(v2, "prose_converter", converter):
            result = await v2.put_v2_progress("hash", request, self.creds)
        self.assertEqual(service.written.xpointer, converter.result)
        self.assertEqual(result["epubConversion"], "synced")

    async def test_pdf_native_page_remains_available(self):
        with patch.object(v2, "_progress_service", NativeOnlyPageService("", 0)):
            result = await v2.get_v2_progress("hash", "koreader", self.creds)
        self.assertEqual(result["position"]["format"], "PDF")
        self.assertEqual(result["position"]["page"], 7)
        self.assertEqual(result["fraction"], 0.35)


if __name__ == "__main__":
    unittest.main()
