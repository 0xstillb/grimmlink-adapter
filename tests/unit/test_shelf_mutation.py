"""Session 05 mutation, retry, and local cleanup safety coverage."""

import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from grimmlink_adapter.models.internal import ManagedFileRecord
from grimmlink_adapter.official.exceptions import (
    OfficialAuthError,
    OfficialPermissionError,
    OfficialTimeoutError,
)
from grimmlink_adapter.security.auth_extractor import ClientCredentials
from grimmlink_adapter.services import shelf_service as shelf_module
from grimmlink_adapter.services.shelf_service import ShelfService
from grimmlink_adapter.state.cache import ManagedFileCache, ShelfOwnershipCache
from grimmlink_adapter.state.outbox import OutboxManager


@pytest.fixture
def verified_bearer(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    resolver = AsyncMock(return_value="verified")
    monkeypatch.setattr(shelf_module, "get_official_bearer", resolver)
    return resolver


def creds() -> ClientCredentials:
    return ClientCredentials(username="session05-reader", bearer_token="token")


OWNER_KEY = f"token:{hashlib.sha256(b'verified').hexdigest()[:24]}"


@pytest.mark.asyncio
async def test_regular_unassign_posts_bulk_payload_then_removes_local_ownership(
    verified_bearer: AsyncMock,
) -> None:
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    await ShelfOwnershipCache.record_ownership(9001, 91, "regular", OWNER_KEY)

    response = await ShelfService(client).remove_book_from_shelf("regular", 91, 9001, creds())

    assert response.status == "removed"
    client.assign_shelves_to_books.assert_awaited_once_with([9001], [], [91], "verified")
    assert not await ShelfOwnershipCache.is_tracked_in_other_shelves(9001, -1, "none", OWNER_KEY)
    verified_bearer.assert_awaited_once()


@pytest.mark.asyncio
async def test_magic_unassign_is_rejected_without_remote_call(verified_bearer: AsyncMock) -> None:
    client = AsyncMock()

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).remove_book_from_shelf("magic", 92, 9002, creds())

    assert caught.value.status_code == 400
    client.assign_shelves_to_books.assert_not_awaited()
    verified_bearer.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_leaves_pending_outbox_and_retry_reuses_mutation(
    verified_bearer: AsyncMock,
) -> None:
    client = AsyncMock()
    client.assign_shelves_to_books.side_effect = [OfficialTimeoutError("temporary"), {}]
    service = ShelfService(client)

    with pytest.raises(HTTPException) as first:
        await service.remove_book_from_shelf("regular", 93, 9003, creds())
    assert first.value.status_code == 502
    pending = await OutboxManager.get_by_idempotency_key(f"SHELF_UNASSIGN:{OWNER_KEY}:93:9003")
    assert pending is not None
    assert pending.status == "PENDING"
    assert pending.retry_count == 1

    response = await service.remove_book_from_shelf("regular", 93, 9003, creds())
    assert response.removed is True
    assert client.assign_shelves_to_books.await_count == 2
    assert (await OutboxManager.get_by_idempotency_key(
        f"SHELF_UNASSIGN:{OWNER_KEY}:93:9003"
    )).status == "COMPLETED"


@pytest.mark.asyncio
async def test_completed_remove_does_not_suppress_later_cycles_or_reuse_completed_action(
    verified_bearer: AsyncMock,
) -> None:
    client = AsyncMock()
    client.assign_shelves_to_books.side_effect = [{}, {}, OfficialTimeoutError("third cycle")]
    service = ShelfService(client)

    await service.remove_book_from_shelf("regular", 931, 90031, creds())
    await service.remove_book_from_shelf("regular", 931, 90031, creds())
    with pytest.raises(HTTPException) as caught:
        await service.remove_book_from_shelf("regular", 931, 90031, creds())

    assert caught.value.status_code == 502
    assert client.assign_shelves_to_books.await_count == 3
    base = await OutboxManager.get_by_idempotency_key(f"SHELF_UNASSIGN:{OWNER_KEY}:931:90031")
    assert base is not None and base.status == "COMPLETED"
    pending = [item for item in await OutboxManager.get_pending() if item.payload.get("book_id") == 90031]
    assert len(pending) == 1
    assert pending[0].status == "PENDING"


@pytest.mark.asyncio
async def test_unauthorized_mutation_maps_to_401(verified_bearer: AsyncMock) -> None:
    client = AsyncMock()
    client.assign_shelves_to_books.side_effect = OfficialAuthError("expired", status_code=401)

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).remove_book_from_shelf("regular", 932, 90032, creds())

    assert caught.value.status_code == 401


@pytest.mark.asyncio
async def test_forbidden_mutation_preserves_403_and_ownership(
    verified_bearer: AsyncMock,
) -> None:
    client = AsyncMock()
    client.assign_shelves_to_books.side_effect = OfficialPermissionError("forbidden", status_code=403)
    await ShelfOwnershipCache.record_ownership(9004, 94, "regular", OWNER_KEY)

    with pytest.raises(HTTPException) as caught:
        await ShelfService(client).remove_book_from_shelf("regular", 94, 9004, creds())

    assert caught.value.status_code == 403
    assert await ShelfOwnershipCache.is_tracked_in_other_shelves(9004, -1, "none", OWNER_KEY)


@pytest.mark.asyncio
async def test_cleanup_deletes_only_adapter_owned_file_after_complete_snapshot(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "managed.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=9005, book_file_id=95, tracked_path=str(tracked), downloaded_by_grimmlink=True,
        expected_size=4,
    ))
    await ShelfOwnershipCache.record_ownership(9005, 95, "regular", OWNER_KEY)
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 95}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    response = await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 95, 9005, creds(),
    )

    assert response.removed is True
    assert not tracked.exists()
    assert await ManagedFileCache.get(9005, 95, OWNER_KEY) is None


@pytest.mark.asyncio
async def test_cleanup_retains_file_when_another_shelf_references_book(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "shared.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=9006, book_file_id=96, tracked_path=str(tracked), downloaded_by_grimmlink=True,
        expected_size=4,
    ))
    await ShelfOwnershipCache.record_ownership(9006, 96, "regular", OWNER_KEY)
    await ShelfOwnershipCache.record_ownership(9006, 97, "regular", OWNER_KEY)
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 96}, {"id": 97}]
    client.get_shelf_books.side_effect = [[], [{"id": 9006}]]
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 96, 9006, creds(),
    )

    assert tracked.exists()
    assert await ManagedFileCache.get(9006, 96, OWNER_KEY) is not None


@pytest.mark.asyncio
async def test_cleanup_retains_file_when_magic_shelf_references_book(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "magic-shared.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=90061, book_file_id=961, tracked_path=str(tracked),
        downloaded_by_grimmlink=True, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 961}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = [{"id": 1961}]
    magic_page = {
        "content": [{"id": 90061, "title": "Book", "authors": [], "primaryFileId": 961,
                     "primaryFileType": "EPUB", "primaryFileName": "magic-shared.epub", "fileSizeKb": 1}],
        "page": 0, "size": 50, "totalElements": 1, "totalPages": 1,
        "hasNext": False, "hasPrevious": False,
    }
    client.get_magic_shelf_books.return_value = magic_page

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 961, 90061, creds(),
    )

    assert tracked.exists()


@pytest.mark.asyncio
async def test_cleanup_retains_user_file_after_complete_snapshot(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "user-complete.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=90062, book_file_id=962, tracked_path=str(tracked),
        downloaded_by_grimmlink=False, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 962}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 962, 90062, creds(),
    )

    assert tracked.exists()


@pytest.mark.asyncio
async def test_cleanup_retains_provider_referenced_file(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "provider.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=90063, book_file_id=963, tracked_path=str(tracked),
        downloaded_by_grimmlink=True, provider_reference_count=1, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 963}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 963, 90063, creds(),
    )

    assert tracked.exists()


@pytest.mark.asyncio
async def test_cleanup_retains_file_with_provider_reference_from_another_owner(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "provider-other-owner.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=900631, book_file_id=9631, tracked_path=str(tracked),
        downloaded_by_grimmlink=True, expected_size=4,
    ))
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key="user:other", book_id=900631, book_file_id=9631, tracked_path=str(tracked),
        downloaded_by_grimmlink=False, provider_reference_count=1, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 9631}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 9631, 900631, creds(),
    )

    assert tracked.exists()


@pytest.mark.asyncio
async def test_snapshot_scope_does_not_erase_another_user_reference(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "scoped.epub"
    tracked.write_bytes(b"book")
    await ShelfOwnershipCache.record_ownership(90064, 964, "regular", "user:other")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=90064, book_file_id=964, tracked_path=str(tracked),
        downloaded_by_grimmlink=True, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 964}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 964, 90064, creds(),
    )

    assert tracked.exists()
    assert await ShelfOwnershipCache.is_tracked_in_other_shelves(90064, -1, "none", "user:other")


@pytest.mark.asyncio
async def test_cleanup_retains_replaced_or_size_changed_file(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "changed.epub"
    tracked.write_bytes(b"different")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=90065, book_file_id=965, tracked_path=str(tracked),
        downloaded_by_grimmlink=True, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 965}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 965, 90065, creds(),
    )

    assert tracked.exists()


@pytest.mark.asyncio
async def test_bearer_cannot_select_foreign_username_scope(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "foreign.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key="user:foreign", book_id=90066, book_file_id=966, tracked_path=str(tracked),
        downloaded_by_grimmlink=True, expected_size=4,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 966}]
    client.get_shelf_books.return_value = []
    client.get_magic_shelves.return_value = []

    await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 966, 90066,
        ClientCredentials(username="foreign", bearer_token="token"),
    )

    assert tracked.exists()


@pytest.mark.asyncio
async def test_registration_requires_existing_file_and_records_actual_size(tmp_path: Path) -> None:
    service = ShelfService(AsyncMock(), cleanup_policy=True, managed_file_root=tmp_path)
    with pytest.raises(HTTPException) as missing:
        await service.register_managed_file(90067, 967, tmp_path / "missing.epub")
    assert missing.value.status_code == 400

    tracked = tmp_path / "actual.epub"
    tracked.write_bytes(b"actual")
    await service.register_managed_file(90067, 967, tracked)
    record = await ManagedFileCache.get(90067, 967)
    assert record is not None
    assert record.expected_size == 6


@pytest.mark.asyncio
async def test_cleanup_skips_user_file_and_incomplete_snapshot(
    tmp_path: Path,
    verified_bearer: AsyncMock,
) -> None:
    tracked = tmp_path / "user.epub"
    tracked.write_bytes(b"book")
    await ManagedFileCache.put(ManagedFileRecord(
        owner_key=OWNER_KEY, book_id=9007, book_file_id=97, tracked_path=str(tracked), downloaded_by_grimmlink=False,
    ))
    client = AsyncMock()
    client.assign_shelves_to_books.return_value = {}
    client.get_regular_shelves.return_value = [{"id": 97}]
    client.get_shelf_books.side_effect = OfficialTimeoutError("snapshot timeout")

    response = await ShelfService(client, cleanup_policy=True, managed_file_root=tmp_path).remove_book_from_shelf(
        "regular", 97, 9007, creds(),
    )

    assert response.removed is True
    assert "skipped" in (response.message or "")
    assert tracked.exists()


@pytest.mark.asyncio
async def test_cleanup_requires_managed_root_and_registration_is_explicit(tmp_path: Path) -> None:
    tracked = tmp_path / "registered.epub"
    tracked.write_bytes(b"book")
    service = ShelfService(AsyncMock(), cleanup_policy=True)

    with pytest.raises(HTTPException) as missing_root:
        await service.register_managed_file(9008, 98, tracked)
    assert missing_root.value.status_code == 501

    service = ShelfService(AsyncMock(), cleanup_policy=True, managed_file_root=tmp_path)
    await service.register_managed_file(9008, 98, tracked, expected_size=4)
    record = await ManagedFileCache.get(9008, 98)
    assert record is not None
    assert record.downloaded_by_grimmlink is True
    assert record.expected_size == 4
