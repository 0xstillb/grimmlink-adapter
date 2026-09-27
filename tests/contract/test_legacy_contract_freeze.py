"""Executable contract tests verifying the frozen legacy GrimmLink contract.

Validates that all frozen wire fixtures match Pydantic schema specifications,
units, timestamp formats, route paths, methods, and error shapes.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import BaseModel, ValidationError

from grimmlink_adapter.models.grimmlink import (
    GrimmlinkAuthErrorResponse,
    GrimmlinkAuthResponse,
    GrimmlinkBookSummary,
    GrimmlinkCapabilitiesResponse,
    GrimmlinkErrorResponse,
    GrimmlinkMetadataBatchResponse,
    GrimmlinkMetadataPullResponse,
    GrimmlinkMetadataSyncRequest,
    GrimmlinkMetadataSyncResponse,
    GrimmlinkReadingSessionBatchRequest,
    GrimmlinkReadingSessionBatchResponse,
    GrimmlinkReadingSessionResponse,
    GrimmlinkReadingSessionSingleRequest,
    GrimmlinkReadStatusesResponse,
    GrimmlinkReadStatusRequest,
    GrimmlinkReadStatusResponse,
    GrimmlinkShelfRemovalResponse,
    GrimmlinkShelfSummary,
    KoreaderProgressPayload,
)
from grimmlink_adapter.models.official import OfficialBookDTO

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> Any:
    path = FIXTURES_DIR / filename
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class TestContractFixturesValidation:
    """Validate all fixture JSON files against the frozen Pydantic models."""

    def test_auth_response_fixture(self) -> None:
        data = load_fixture("auth_response.json")
        model = GrimmlinkAuthResponse.model_validate(data)
        assert model.status == "ok"
        assert model.username == "koreader_user"
        assert model.userId == 42
        assert model.syncEnabled is True
        assert model.syncWithWebReader is True

    def test_capabilities_response_fixture(self) -> None:
        data = load_fixture("capabilities_response.json")
        model = GrimmlinkCapabilitiesResponse.model_validate(data)
        assert model.apiVersion == "v1"
        assert model.webUiProgress is True
        assert model.progressSync is True
        assert model.pdfBridge is False
        assert model.readingSessions is True
        assert model.metadataSync is True
        assert model.shelves is True

    def test_books_by_hash_response_fixture(self) -> None:
        data = load_fixture("books_by_hash_response.json")
        model = OfficialBookDTO.model_validate(data)
        assert model.id == 42
        assert model.title == "The Count of Monte Cristo"
        assert model.readStatus == "READING"
        assert model.personalRating == 8
        assert model.primaryFile is not None
        assert model.primaryFile.fileName == "The Count of Monte Cristo.epub"
        assert model.primaryFile.fileSizeKb == 1250

    def test_book_summary_fixture(self) -> None:
        data = load_fixture("book_summary.json")
        model = GrimmlinkBookSummary.model_validate(data)
        assert model.bookId == 42
        assert model.bookFileId == 101
        assert model.title == "The Count of Monte Cristo"
        assert model.author == "Alexandre Dumas"
        assert model.fileName == "The Count of Monte Cristo.epub"
        assert model.fileFormat == "EPUB"
        assert model.fileSizeKb == 1250
        assert model.fileSize == 1250 * 1024
        assert model.bookHash == "d41d8cd98f00b204e9800998ecf8427e"
        assert model.seriesName == "Classics"
        assert model.seriesNumber == 1.0

    def test_read_statuses_response_fixture(self) -> None:
        data = load_fixture("read_statuses_response.json")
        model = GrimmlinkReadStatusesResponse.model_validate(data)
        expected = ["UNREAD", "READING", "READ", "PAUSED", "ABANDONED", "RE_READING"]
        assert model.statuses == expected

    def test_read_status_put_fixtures(self) -> None:
        req_data = load_fixture("read_status_put_request.json")
        req_model = GrimmlinkReadStatusRequest.model_validate(req_data)
        assert req_model.status == "COMPLETED"

        resp_data = load_fixture("read_status_put_response.json")
        resp_model = GrimmlinkReadStatusResponse.model_validate(resp_data)
        assert resp_model.bookId == 42
        assert resp_model.status == "COMPLETED"
        assert resp_model.updated is True

    def test_shelves_list_fixture(self) -> None:
        data = load_fixture("shelves_list_response.json")
        assert isinstance(data, list)
        models = [GrimmlinkShelfSummary.model_validate(item) for item in data]
        assert len(models) == 3

        regular_shelf = models[0]
        assert regular_shelf.id == 1
        assert regular_shelf.type == "regular"
        assert regular_shelf.visibility == "personal"
        assert regular_shelf.bookCount == 15

        public_shelf = models[1]
        assert public_shelf.id == 2
        assert public_shelf.type == "regular"
        assert public_shelf.visibility == "public"
        assert public_shelf.bookCount == 42

        magic_shelf = models[2]
        assert magic_shelf.id == 100
        assert magic_shelf.type == "magic"
        assert magic_shelf.description == "Rule-based Magic Shelf"
        assert magic_shelf.bookCount == 8

    def test_shelf_books_fixture(self) -> None:
        data = load_fixture("shelf_books_response.json")
        assert isinstance(data, list)
        models = [GrimmlinkBookSummary.model_validate(item) for item in data]
        assert len(models) == 2
        assert models[0].bookId == 42
        assert models[1].bookId == 43
        assert models[1].author == "Alexandre Dumas"

    def test_shelf_removal_fixtures(self) -> None:
        reg_data = load_fixture("shelf_removal_regular_response.json")
        reg_model = GrimmlinkShelfRemovalResponse.model_validate(reg_data)
        assert reg_model.shelfId == 1
        assert reg_model.bookId == 42
        assert reg_model.shelfType == "regular"
        assert reg_model.removed is True
        assert reg_model.status == "removed"

        magic_data = load_fixture("shelf_removal_magic_response.json")
        magic_model = GrimmlinkShelfRemovalResponse.model_validate(magic_data)
        assert magic_model.shelfId == 100
        assert magic_model.bookId == 42
        assert magic_model.shelfType == "magic"
        assert magic_model.removed is False
        assert magic_model.status == "unsupported"
        assert "rule-based" in (magic_model.message or "").lower()

    def test_progress_get_fixture(self) -> None:
        data = load_fixture("progress_get_response.json")
        model = KoreaderProgressPayload.model_validate(data)
        assert model.timestamp == 1774526400
        assert model.bookId == 42
        assert model.bookHash == "d41d8cd98f00b204e9800998ecf8427e"
        assert model.fileFormat == "EPUB"
        assert model.percentage == 42.5
        assert model.progress == "/6/4[chap03]!/4/2/10/1:24"
        assert model.location == "/6/4[chap03]!/4/2/10/1:24"
        assert model.device == "Kobo Clara 2E"
        assert model.device_id == "kobo-uuid-12345"

    def test_progress_put_fixtures(self) -> None:
        reflow_data = load_fixture("progress_put_request_reflowable.json")
        reflow_model = KoreaderProgressPayload.model_validate(reflow_data)
        assert reflow_model.bookHash == "d41d8cd98f00b204e9800998ecf8427e"
        assert reflow_model.fileFormat == "EPUB"
        assert reflow_model.location is not None
        assert reflow_model.percentage == 42.5

        pdf_data = load_fixture("progress_put_request_pdf.json")
        pdf_model = KoreaderProgressPayload.model_validate(pdf_data)
        assert pdf_model.bookHash == "e99a18c428cb38d5f260853678922e03"
        assert pdf_model.fileFormat == "PDF"
        assert pdf_model.currentPage == 55
        assert pdf_model.totalPages == 250
        assert pdf_model.percentage == 22.0

    def test_metadata_sync_push_fixtures(self) -> None:
        req_data = load_fixture("metadata_sync_push_request.json")
        req_model = GrimmlinkMetadataSyncRequest.model_validate(req_data)
        assert req_model.schemaVersion == 1
        assert req_model.syncMode == "push"
        assert req_model.bookId == 42
        assert req_model.rating is not None
        assert req_model.rating.value == 8
        assert req_model.rating.scale == 10
        assert len(req_model.annotations) == 1
        ann = req_model.annotations[0]
        assert ann.type == "highlight"
        assert ann.location is not None
        assert ann.location.pos0 == "/6/4[chap117]!/4/2/10/1:0"
        assert ann.location.pageno == 500
        assert len(req_model.bookmarks) == 1
        bm = req_model.bookmarks[0]
        assert bm.title == "Important bookmark"
        assert bm.deleted is False

        resp_data = load_fixture("metadata_sync_push_response.json")
        resp_model = GrimmlinkMetadataSyncResponse.model_validate(resp_data)
        assert resp_model.bookId == 42
        assert resp_model.ok is True
        assert resp_model.results is not None

    def test_metadata_pull_fixture(self) -> None:
        data = load_fixture("metadata_pull_response.json")
        model = GrimmlinkMetadataPullResponse.model_validate(data)
        assert model.bookId == 42
        assert model.bookFileId == 101
        assert model.ok is True
        assert model.since == "2026-09-20T00:00:00Z"
        assert model.nextCursor == "2026-09-25T15:00:00Z"
        assert model.limit == 50
        assert len(model.items) == 2
        rating_item = model.items[0]
        assert rating_item.type == "rating"
        assert rating_item.id == "grimmory-personal-rating"
        assert rating_item.payload is not None
        assert rating_item.payload["value"] == 8

    def test_metadata_batch_fixtures(self) -> None:
        req_data = load_fixture("metadata_batch_request.json")
        req_model = GrimmlinkMetadataSyncRequest.model_validate(req_data)
        assert req_model.syncMode == "incremental"
        assert req_model.cursor == "2026-09-20T00:00:00Z"

        resp_data = load_fixture("metadata_batch_response.json")
        resp_model = GrimmlinkMetadataBatchResponse.model_validate(resp_data)
        assert resp_model.ok is True
        assert resp_model.push is not None
        assert resp_model.pull is not None
        assert resp_model.push.ok is True
        assert resp_model.pull.ok is True

    def test_reading_session_single_fixtures(self) -> None:
        req_data = load_fixture("reading_session_single_request.json")
        req_model = GrimmlinkReadingSessionSingleRequest.model_validate(req_data)
        assert req_model.bookId == 42
        assert req_model.durationSeconds == 3600
        assert req_model.startProgress == 40.0
        assert req_model.endProgress == 42.5
        assert req_model.progressDelta == 2.5

        list_data = load_fixture("reading_sessions_list_response.json")
        assert isinstance(list_data, list)
        models = [GrimmlinkReadingSessionResponse.model_validate(item) for item in list_data]
        assert len(models) == 1
        assert models[0].id == 1
        assert models[0].durationSeconds == 3600

    def test_reading_session_batch_fixtures(self) -> None:
        req_data = load_fixture("reading_session_batch_request.json")
        req_model = GrimmlinkReadingSessionBatchRequest.model_validate(req_data)
        assert req_model.bookId == 42
        assert len(req_model.sessions) == 2
        assert req_model.sessions[0].durationSeconds == 1800
        assert req_model.sessions[1].durationSeconds == 1800

        resp_data = load_fixture("reading_session_batch_response.json")
        resp_model = GrimmlinkReadingSessionBatchResponse.model_validate(resp_data)
        assert resp_model.totalRequested == 2
        assert resp_model.successCount == 2
        assert len(resp_model.results) == 2

    def test_error_shape_fixtures(self) -> None:
        auth_err_data = load_fixture("error_auth_401.json")
        auth_err = GrimmlinkAuthErrorResponse.model_validate(auth_err_data)
        assert auth_err.status == "error"
        assert "invalid" in auth_err.message.lower()

        api_err_data = load_fixture("error_api_400.json")
        api_err = GrimmlinkErrorResponse.model_validate(api_err_data)
        assert api_err.status == 400
        assert "magic" in api_err.message.lower()


class TestWireSerialization:
    """Check canonical output keys and fork DTO validation boundaries."""

    @pytest.mark.parametrize(
        ("fixture", "model_type", "expected_key", "forbidden_key"),
        [
            ("metadata_batch_request.json", GrimmlinkMetadataSyncRequest, "deviceId", "device_id"),
            ("reading_session_single_request.json", GrimmlinkReadingSessionSingleRequest, "deviceId", "device_id"),
            ("reading_session_batch_request.json", GrimmlinkReadingSessionBatchRequest, "deviceId", "device_id"),
            ("progress_put_request_reflowable.json", KoreaderProgressPayload, "bookHash", "book_hash"),
            ("progress_put_request_pdf.json", KoreaderProgressPayload, "currentPage", "current_page"),
        ],
    )
    def test_canonical_wire_keys(
        self, fixture: str, model_type: type[BaseModel], expected_key: str, forbidden_key: str
    ) -> None:
        payload = model_type.model_validate(load_fixture(fixture)).model_dump(
            by_alias=True, exclude_unset=True
        )
        assert expected_key in payload
        assert forbidden_key not in payload

    def test_legacy_input_aliases_serialize_to_fork_keys(self) -> None:
        metadata = GrimmlinkMetadataSyncRequest.model_validate({"device_id": "reader-1"})
        assert metadata.model_dump(by_alias=True, exclude_unset=True) == {"deviceId": "reader-1"}

        progress = KoreaderProgressPayload.model_validate(
            {"deviceId": "reader-1", "book_hash": "hash-1", "book_id": 42}
        )
        assert progress.model_dump(by_alias=True, exclude_unset=True) == {
            "device_id": "reader-1",
            "bookHash": "hash-1",
            "bookId": 42,
        }

    def test_shelf_removal_does_not_invent_success(self) -> None:
        fixture = load_fixture("shelf_removal_regular_response.json")
        serialized = GrimmlinkShelfRemovalResponse.model_validate(fixture).model_dump(
            by_alias=True, exclude_unset=True
        )
        assert serialized == fixture

    def test_batch_response_does_not_invent_counts(self) -> None:
        fixture = load_fixture("reading_session_batch_response.json")
        serialized = GrimmlinkReadingSessionBatchResponse.model_validate(fixture).model_dump(
            by_alias=True, exclude_unset=True
        )
        assert serialized == fixture

    @pytest.mark.parametrize(
        "payload",
        [
            {"sessions": [{"startTime": "a", "endTime": "b", "durationSeconds": 1}]},
            {"bookId": 42, "sessions": []},
            {"bookId": 42, "sessions": [{"startTime": "a", "endTime": "b"}]},
            {
                "bookId": 42,
                "sessions": [{"startTime": "a", "endTime": "b", "durationSeconds": 1}] * 501,
            },
        ],
    )
    def test_batch_rejects_invalid_fork_payloads(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            GrimmlinkReadingSessionBatchRequest.model_validate(payload)

    def test_single_requires_duration(self) -> None:
        with pytest.raises(ValidationError):
            GrimmlinkReadingSessionSingleRequest.model_validate(
                {"bookId": 42, "startTime": "a", "endTime": "b"}
            )

    def test_by_hash_fixture_uses_bookfile_dto_fields(self) -> None:
        fixture = load_fixture("books_by_hash_response.json")
        assert "currentHash" not in fixture["primaryFile"]
        assert "initialHash" not in fixture["primaryFile"]
        book = OfficialBookDTO.model_validate(fixture)
        assert book.primaryFile is not None
        assert book.primaryFile.bookId == book.id
        assert book.model_dump(exclude_unset=True) == fixture


class TestRouteContractRegistry:
    """Verify that all 19 frozen route patterns are registered in the FastAPI app."""

    def test_frozen_routes_registered_in_app(self) -> None:
        from grimmlink_adapter.main import app

        expected_routes = [
            ("GET", "/api/grimmlink/v1/auth"),
            ("GET", "/api/grimmlink/v1/capabilities"),
            ("GET", "/api/grimmlink/v1/books/by-hash/{book_hash}"),
            ("GET", "/api/grimmlink/v1/books/{book_id}/download"),
            ("GET", "/api/grimmlink/v1/books/read-statuses"),
            ("PUT", "/api/grimmlink/v1/books/{book_id}/status"),
            ("GET", "/api/grimmlink/v1/shelves"),
            ("GET", "/api/grimmlink/v1/shelves/{shelf_id}/books"),
            ("GET", "/api/grimmlink/v1/shelves/{shelf_type}/{shelf_id}/books"),
            ("POST", "/api/grimmlink/v1/shelves/{shelf_id}/books/{book_id}/remove"),
            ("POST", "/api/grimmlink/v1/shelves/{shelf_type}/{shelf_id}/books/{book_id}/remove"),
            ("GET", "/api/grimmlink/v1/syncs/progress/{book_hash}"),
            ("PUT", "/api/grimmlink/v1/syncs/progress"),
            ("POST", "/api/grimmlink/v1/syncs/metadata"),
            ("GET", "/api/grimmlink/v1/syncs/metadata"),
            ("POST", "/api/grimmlink/v1/syncs/metadata/batch"),
            ("GET", "/api/grimmlink/v1/reading-sessions"),
            ("POST", "/api/grimmlink/v1/reading-sessions"),
            ("POST", "/api/grimmlink/v1/reading-sessions/batch"),
        ]

        openapi_paths = app.openapi()["paths"]

        for method, path in expected_routes:
            assert path in openapi_paths, f"Missing route path: {path}"
            assert method.lower() in openapi_paths[path], f"Missing method {method} on {path}"

    def test_openapi_uses_fork_request_and_response_dtos(self) -> None:
        from grimmlink_adapter.main import app

        schema = app.openapi()
        paths = schema["paths"]
        by_hash = paths["/api/grimmlink/v1/books/by-hash/{book_hash}"]["get"]
        assert by_hash["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/OfficialBookDTO"
        )

        single = paths["/api/grimmlink/v1/reading-sessions"]["post"]
        assert single["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith(
            "/GrimmlinkReadingSessionSingleRequest"
        )
        single_schema = schema["components"]["schemas"]["GrimmlinkReadingSessionSingleRequest"]
        assert {"bookId", "startTime", "endTime", "durationSeconds"} <= set(
            single_schema["required"]
        )

        batch_schema = schema["components"]["schemas"]["GrimmlinkReadingSessionBatchRequest"]
        assert {"bookId", "sessions"} <= set(batch_schema["required"])
        assert batch_schema["properties"]["sessions"]["minItems"] == 1
        assert batch_schema["properties"]["sessions"]["maxItems"] == 500

    @pytest.mark.asyncio
    async def test_auth_rejection_on_all_protected_routes(self, test_client: AsyncClient) -> None:
        """Contract requirement: all protected routes reject requests missing authentication."""
        protected_endpoints = [
            ("GET", "/api/grimmlink/v1/auth"),
            ("GET", "/api/grimmlink/v1/shelves"),
            ("GET", "/api/grimmlink/v1/books/by-hash/dummy_hash"),
            ("GET", "/api/grimmlink/v1/books/42/download"),
            ("GET", "/api/grimmlink/v1/books/read-statuses"),
            ("PUT", "/api/grimmlink/v1/books/42/status"),
            ("GET", "/api/grimmlink/v1/syncs/progress/dummy_hash"),
            ("PUT", "/api/grimmlink/v1/syncs/progress"),
            ("GET", "/api/grimmlink/v1/syncs/metadata"),
            ("POST", "/api/grimmlink/v1/syncs/metadata"),
            ("POST", "/api/grimmlink/v1/syncs/metadata/batch"),
            ("GET", "/api/grimmlink/v1/reading-sessions?bookId=42"),
            ("POST", "/api/grimmlink/v1/reading-sessions"),
            ("POST", "/api/grimmlink/v1/reading-sessions/batch"),
        ]

        for method, path in protected_endpoints:
            if method == "GET":
                resp = await test_client.get(path)
            elif method == "POST":
                resp = await test_client.post(path, json={})
            elif method == "PUT":
                resp = await test_client.put(path, json={})
            assert resp.status_code == 401, f"{method} {path} did not reject unauthenticated caller"

    @pytest.mark.asyncio
    async def test_capabilities_is_public(self, test_client: AsyncClient) -> None:
        """Capabilities route is accessible without credentials for preflight checks."""
        resp = await test_client.get("/api/grimmlink/v1/capabilities")
        assert resp.status_code == 200
