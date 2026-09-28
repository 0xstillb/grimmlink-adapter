"""Session 06 progress normalization and conflict coverage."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from grimmlink_adapter.models.grimmlink import KoreaderProgressPayload
from grimmlink_adapter.models.internal import ProgressSnapshot
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services.progress_service import (
    ProgressService,
    official_fraction_to_display_percent,
    snapshot_from_grimmlink,
)
from grimmlink_adapter.services.request_auth import VerifiedBearer
from grimmlink_adapter.state.cache import ProgressStateCache

CREDS = ClientCredentials(username="reader", md5_key="md5")
BEARER_CREDS = ClientCredentials(bearer_token="jwt-token")


def test_official_fraction_and_legacy_percent_are_not_double_scaled() -> None:
    assert official_fraction_to_display_percent(0.33) == 33.0
    assert official_fraction_to_display_percent(33.0) == 33.0


def test_epub_native_location_and_page_ratio_round_trip() -> None:
    snapshot = snapshot_from_grimmlink(KoreaderProgressPayload(
        bookHash="epub-hash",
        fileFormat="EPUB",
        progress="/body/DocFragment[3]/body/p[35]/text().0",
        location="/body/DocFragment[3]/body/p[35]/text().0",
        percentage=33.0,
        currentPage=55,
        totalPages=16653,
    ))
    assert snapshot.native_location is not None
    assert snapshot.display_percent == pytest.approx(0.3303, abs=0.0001)
    assert snapshot.official_fraction == pytest.approx(0.003303, abs=0.000001)


def test_epub_rejects_numeric_only_location() -> None:
    with pytest.raises(HTTPException) as caught:
        snapshot_from_grimmlink(KoreaderProgressPayload(
            bookHash="epub-hash", fileFormat="EPUB", progress="55", percentage=33.0,
        ))
    assert caught.value.status_code == 400


def test_pdf_projection_preserves_pages_and_percent() -> None:
    snapshot = snapshot_from_grimmlink(KoreaderProgressPayload(
        bookHash="pdf-hash", fileFormat="PDF", progress="55", currentPage=55, totalPages=250,
    ))
    assert snapshot.current_page == 55
    assert snapshot.total_pages == 250
    assert snapshot.display_percent == 22.0
    assert snapshot.official_fraction == 0.22


@pytest.mark.asyncio
async def test_progress_get_maps_official_fraction_and_preserves_native_location() -> None:
    client = AsyncMock()
    client.get_koreader_progress.return_value = {
        "bookHash": "epub-hash",
        "fileFormat": "EPUB",
        "percentage": 0.33,
        "progress": "/body/3",
        "location": "/body/3",
        "timestamp": 100,
    }
    result = await ProgressService(client).get_progress("epub-hash", CREDS)
    assert result.percentage == 33.0
    assert result.progress == "/body/3"
    assert result.location == "/body/3"
    client.get_koreader_progress.assert_awaited_once_with("epub-hash", "reader", "md5")


@pytest.mark.asyncio
async def test_progress_put_converts_percent_to_official_fraction() -> None:
    client = AsyncMock()
    client.get_koreader_progress.return_value = {}
    client.update_koreader_progress.return_value = {}
    result = await ProgressService(client).update_progress(
        KoreaderProgressPayload(
            bookHash="epub-hash", fileFormat="EPUB", progress="/body/3", location="/body/3",
            percentage=22.0, timestamp=100,
        ),
        CREDS,
    )
    assert result["status"] == "progress updated"
    payload = client.update_koreader_progress.await_args.args[0]
    assert payload["percentage"] == 0.22
    assert payload["progress"] == "/body/3"


@pytest.mark.asyncio
async def test_md5_pdf_without_book_id_uses_native_route_without_bearer_projection() -> None:
    client = AsyncMock()
    client.update_koreader_progress.return_value = {"status": "progress updated"}
    client.update_book_progress.return_value = {}
    client.get_app_book_progress.return_value = {"readStatus": "UNREAD"}
    service = ProgressService(client)
    with patch.object(
        ProgressService,
        "_resolve_bearer_book",
        new=AsyncMock(return_value=("verified-bearer", 25, 25, "PDF")),
    ) as resolve_book:
        result = await service.update_progress(
            KoreaderProgressPayload(
                bookHash="pdf-hash-missing-id", fileFormat="PDF", progress="12",
                currentPage=12, totalPages=663, timestamp=100,
            ),
            CREDS,
        )

    assert result["status"] == "progress updated"
    assert result["projection"] == "koreader-native+official-app+reading-status"
    resolve_book.assert_awaited_once_with(CREDS, "pdf-hash-missing-id", None)
    client.get_koreader_progress.assert_not_awaited()
    client.update_read_status.assert_awaited_once_with(25, "READING", "verified-bearer")
    client.update_book_progress.assert_awaited_once_with(
        25, {"pdfProgress": {"page": 12, "percentage": pytest.approx(1.81)}}, "verified-bearer",
    )
    client.update_koreader_progress.assert_awaited_once_with(
        {
            "document": "pdf-hash-missing-id",
            "bookHash": "pdf-hash-missing-id",
            "fileFormat": "PDF",
            "progress": "12",
            "percentage": pytest.approx(0.0181),
            "currentPage": 12,
            "totalPages": 663,
            "timestamp": 100,
        },
        "reader",
        "md5",
    )


@pytest.mark.asyncio
async def test_progress_get_rejects_empty_official_snapshot() -> None:
    client = AsyncMock()
    client.get_koreader_progress.return_value = {
        "document": "pdf-hash",
        "timestamp": 100,
        "progress": None,
        "percentage": None,
    }
    with pytest.raises(HTTPException) as caught:
        await ProgressService(client).get_progress("pdf-hash", CREDS)
    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_native_progress_get_preserves_official_wire_payload() -> None:
    client = AsyncMock()
    native_payload = {
        "timestamp": 100,
        "document": "pdf-hash",
        "percentage": 0.0181,
        "progress": "12",
        "device": "cph2797",
        "device_id": "device-1",
    }
    client.get_koreader_progress.return_value = native_payload
    result = await ProgressService(client).get_native_progress("pdf-hash", CREDS)
    assert result == native_payload
    client.get_koreader_progress.assert_awaited_once_with("pdf-hash", "reader", "md5")


@pytest.mark.asyncio
async def test_progress_put_rejects_stale_timestamp_and_force_overrides() -> None:
    client = AsyncMock()
    client.get_koreader_progress.side_effect = [
        {"bookHash": "epub-hash", "fileFormat": "EPUB", "progress": "/body/4", "percentage": 0.8, "timestamp": 200},
        {"bookHash": "epub-hash", "fileFormat": "EPUB", "progress": "/body/4", "percentage": 0.8, "timestamp": 200},
    ]
    client.update_koreader_progress.return_value = {}
    service = ProgressService(client)
    stale = KoreaderProgressPayload(
        bookHash="epub-hash", fileFormat="EPUB", progress="/body/3", location="/body/3", percentage=22.0, timestamp=100,
    )
    conflict = await service.update_progress(stale, CREDS)
    assert conflict["conflictDetected"] is True
    client.update_koreader_progress.assert_not_awaited()

    forced = stale.model_copy(update={"force": True})
    await service.update_progress(forced, CREDS)
    client.update_koreader_progress.assert_awaited_once()


@pytest.mark.asyncio
async def test_newer_manual_status_blocks_progress_replay() -> None:
    await ProgressStateCache.set_manual_status("user:reader", "epub-hash", "COMPLETED", 200)
    client = AsyncMock()
    service = ProgressService(client)
    result = await service.update_progress(
        KoreaderProgressPayload(
            bookHash="epub-hash", fileFormat="EPUB", progress="/body/3", location="/body/3", percentage=22.0, timestamp=100,
        ),
        CREDS,
    )
    assert result["conflictDetected"] is True
    client.update_koreader_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_bearer_progress_round_trips_through_official_app_projection() -> None:
    client = AsyncMock()
    client.get_app_book_progress.return_value = {
        "lastReadTime": "2026-09-27T12:00:00Z",
        "pdfProgress": {"page": 55, "percentage": 22.0},
    }
    with patch.object(
        ProgressService,
        "_resolve_bearer_book",
        new=AsyncMock(return_value=("verified-bearer", 42, 101, "PDF")),
    ), patch(
        "grimmlink_adapter.services.progress_service.get_official_bearer",
        new=AsyncMock(return_value=VerifiedBearer("jwt-token", 7)),
    ):
        result = await ProgressService(client).get_progress("pdf-hash", BEARER_CREDS)
    assert result.bookId == 42
    assert result.currentPage == 55
    assert result.percentage == 22.0
    client.get_app_book_progress.assert_awaited_once_with(42, "verified-bearer")


@pytest.mark.asyncio
async def test_bearer_progress_update_writes_pdf_projection() -> None:
    client = AsyncMock()
    client.update_book_progress.return_value = {}
    service = ProgressService(client)
    with patch.object(
        ProgressService,
        "_resolve_bearer_book",
        new=AsyncMock(return_value=("verified-bearer", 42, 101, "PDF")),
    ), patch(
        "grimmlink_adapter.services.progress_service.get_official_bearer",
        new=AsyncMock(return_value=VerifiedBearer("jwt-token", 7)),
    ):
        result = await service.update_progress(
            KoreaderProgressPayload(
                bookHash="pdf-hash", bookId=42, bookFileId=101, fileFormat="PDF",
                progress="55", currentPage=55, totalPages=250, timestamp=100,
            ),
            BEARER_CREDS,
        )
    assert result["projection"] == "official-app"
    client.update_book_progress.assert_awaited_once_with(
        42, {"pdfProgress": {"page": 55, "percentage": 22.0}}, "verified-bearer",
    )


@pytest.mark.asyncio
async def test_force_does_not_override_newer_manual_status() -> None:
    await ProgressStateCache.set_manual_status("user:reader", "pdf-hash", "READ", 200)
    client = AsyncMock()
    result = await ProgressService(client).update_progress(
        KoreaderProgressPayload(
            bookHash="pdf-hash", fileFormat="PDF", progress="55", percentage=22.0,
            timestamp=100, force=True,
        ),
        CREDS,
    )
    assert result["conflictDetected"] is True
    client.update_koreader_progress.assert_not_awaited()


def test_app_percentages_are_already_display_units() -> None:
    snapshot = ProgressService._app_progress_snapshot(
        {"pdfProgress": {"page": 55, "percentage": 0.3303}},
        "pdf-subpercent", 42, 101,
    )
    assert snapshot is not None
    assert snapshot.display_percent == pytest.approx(0.3303)


def test_epub_projection_rejects_xpointer_and_accepts_cfi() -> None:
    with pytest.raises(HTTPException) as caught:
        ProgressService._projection_payload(ProgressSnapshot(
            book_hash="epub-xpointer", format="EPUB", native_location="/6/4[chap03]",
            display_percent=42.0,
        ))
    assert caught.value.status_code == 501
    payload = ProgressService._projection_payload(ProgressSnapshot(
        book_hash="epub-cfi", format="EPUB", native_location="epubcfi(/6/4[chap03])",
        display_percent=42.0,
    ))
    assert payload["epubProgress"]["cfi"].startswith("epubcfi(")


@pytest.mark.asyncio
async def test_bearer_stale_app_timestamp_returns_conflict() -> None:
    client = AsyncMock()
    client.get_app_book_progress.return_value = {
        "lastReadTime": "1970-01-01T00:03:20Z",
        "pdfProgress": {"page": 55, "percentage": 22.0},
    }
    with patch.object(
        ProgressService,
        "_resolve_bearer_book",
        new=AsyncMock(return_value=("verified-bearer", 42, 101, "PDF")),
    ), patch(
        "grimmlink_adapter.services.progress_service.get_official_bearer",
        new=AsyncMock(return_value=VerifiedBearer("jwt-token", 7)),
    ):
        result = await ProgressService(client).update_progress(
            KoreaderProgressPayload(
                bookHash="pdf-stale", bookId=42, bookFileId=101, fileFormat="PDF",
                progress="55", currentPage=55, totalPages=250, timestamp=100,
            ),
            BEARER_CREDS,
        )
    assert result["conflictDetected"] is True
    client.update_book_progress.assert_not_awaited()


@pytest.mark.asyncio
async def test_md5_pdf_native_route_delegates_hash_verification_to_official() -> None:
    client = AsyncMock()
    client.update_koreader_progress.return_value = {}
    result = await ProgressService(client).update_progress(
        KoreaderProgressPayload(
            bookHash="pdf-unmapped", fileFormat="PDF", progress="55",
            currentPage=55, totalPages=250, timestamp=100,
        ),
        CREDS,
    )
    assert result["projection"] == "koreader-native"
    client.update_koreader_progress.assert_awaited_once()
    client.update_book_progress.assert_not_awaited()

