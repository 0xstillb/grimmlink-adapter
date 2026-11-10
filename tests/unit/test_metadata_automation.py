"""Behavioral regression checks for NAS metadata planning, durability and access."""

import copy
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.automation import LibraryPolicy
from grimmlink_adapter.models.official import OfficialLoginResponse
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.exceptions import OfficialBadResponseError
from grimmlink_adapter.security.auth_extractor import ClientCredentials, require_v2_credentials
from grimmlink_adapter.security.metadata_vault import metadata_cipher
from grimmlink_adapter.services.metadata_automation import (
    MetadataAutomation,
    MetadataBlocked,
    check_storage,
    inspect_sources,
    mapped_book,
    pending_sources,
    remember_book_paths,
    server_key,
)
from grimmlink_adapter.services.metadata_planner import field_plan, read_source
from grimmlink_adapter.services.opf_ingestion import OPFIngestionService
from grimmlink_adapter.state.database import get_db_path, set_db_path
from grimmlink_adapter.state.metadata_automation import MetadataAutomationStore as Store
from grimmlink_adapter.state.migrations import apply_migrations


@pytest_asyncio.fixture
async def metadata_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    previous = get_db_path()
    set_db_path(tmp_path / "state.db")
    monkeypatch.setattr(settings, "GRIMMORY_BASE_URL", "http://metadata.test")
    monkeypatch.setattr(settings, "METADATA_KEY_FILE", str(tmp_path / "key"))
    await apply_migrations()
    yield tmp_path
    set_db_path(previous)


def test_field_policy_respects_manual_edits_locks_and_missing_values():
    result = field_plan(
        {"title": "Source", "authors": ["Author"], "publisher": "", "language": "th"},
        {"title": "Manual", "authors": ["Old"], "authorsLocked": True, "publisher": "Keep", "language": "en"},
        {"title": "Previously synced", "language": "en"},
        ["title", "authors", "publisher", "language"], "check",
    )
    assert result["changes"] == {"language": "th"}
    assert result["skipped"] == {"authors": "locked"}
    assert result["conflicts"]["title"]["grimmory"] == "Manual"


def test_backfill_missing_never_replaces_existing_fields():
    assert field_plan({"title": "New", "language": "th"}, {"title": "Keep"}, {}, ["title", "language"], "backfill_missing")["changes"] == {"language": "th"}


def test_generated_sidecars_do_not_feed_the_input_loop(tmp_path: Path):
    book = tmp_path / "Book.epub"
    book.write_bytes(b"book")
    sidecar = tmp_path / "Book.metadata.json"
    sidecar.write_text(json.dumps({"generatedBy": "grimmlink-adapter", "metadata": {"title": "Generated"}}))
    assert read_source(sidecar) is None
    sidecar.write_text(json.dumps({"metadata": {"title": "External"}}))
    parsed = read_source(sidecar)
    assert parsed is not None and parsed[0].metadata.title == "External"


def test_nas_marker_and_path_escape_are_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(tmp_path)])
    policy = LibraryPolicy(mappings=[{"libraryPathId": 11, "rootIndex": 0, "localSubpath": ".", "marker": ".nas"}])
    with pytest.raises(OSError):
        check_storage(policy)
    (tmp_path / ".nas").write_text("NAS mounted")
    check_storage(policy)
    policy.mappings[0].marker = "../outside"
    with pytest.raises(MetadataBlocked):
        check_storage(policy)


def test_library_subfolder_maps_only_its_own_books(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(tmp_path)])
    (tmp_path / ".nas").write_text("mounted")
    folder = tmp_path / "Books" / "MANGA_NC"
    folder.mkdir(parents=True)
    policy = LibraryPolicy(mappings=[{
        "libraryPathId": 4, "rootIndex": 0, "localSubpath": "Books/MANGA_NC", "marker": ".nas",
    }])
    roots = check_storage(policy)
    root, target = mapped_book(
        {"filePath": "/books/Books/MANGA_NC/Novel.epub"},
        {"libraryPathId": 4},
        {"paths": [{"id": 4, "path": "/books/Books/MANGA_NC"}]},
        policy, roots,
    )
    assert root == folder and target == folder / "Novel.epub"
    policy.mappings[0].localSubpath = None
    with pytest.raises(MetadataBlocked, match="library_folder_not_configured"):
        check_storage(policy)


def test_pending_sources_stays_in_library_subfolder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(tmp_path)])
    (tmp_path / ".nas").write_text("mounted")
    selected = tmp_path / "Books" / "MANGA_NC"
    unrelated = tmp_path / "@Recycle" / "Books" / "MANGA_NC"
    for folder in (selected, unrelated):
        folder.mkdir(parents=True)
        (folder / "Novel.epub").write_bytes(b"book")
        (folder / "Novel.opf").write_text("metadata")
    policy = LibraryPolicy(mappings=[{
        "libraryPathId": 4, "rootIndex": 0, "localSubpath": "Books/MANGA_NC", "marker": ".nas",
    }])
    assert pending_sources(set(), check_storage(policy), policy) == ["Novel.epub"]


@pytest.mark.asyncio
async def test_manual_scan_ignores_other_nas_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from grimmlink_adapter.api import v2

    selected = tmp_path / "Books" / "MANGA_NC"
    unrelated = tmp_path / "@Recycle" / "Books" / "MANGA_NC"
    for folder in (selected, unrelated):
        folder.mkdir(parents=True)
        (folder / "Novel [รักนี้ผีจัดให้].opf").write_text("metadata")
        (folder / "Novel [รักนี้ผีจัดให้].pdf").write_bytes(b"book")
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(tmp_path)])

    async def scoped_folders(*_args):
        return {0: {selected}}

    monkeypatch.setattr(v2, "_library_metadata_folders", scoped_folders)
    sources = await v2.list_metadata_sources(library_id=4, creds=None, session=None)
    assert [source["sourcePath"] for source in sources] == ["Books/MANGA_NC/Novel [รักนี้ผีจัดให้].opf"]
    assert sources[0]["bookCandidates"] == ["Books/MANGA_NC/Novel [รักนี้ผีจัดให้].pdf"]


@pytest.mark.asyncio
async def test_nas_folder_dropdown_browses_one_level_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi import HTTPException

    from grimmlink_adapter.api.automation import browse_nas_folders

    (tmp_path / "Books" / "MANGA_NC").mkdir(parents=True)
    (tmp_path / "@Recycle").mkdir()
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(tmp_path)])
    root = await browse_nas_folders(root_index=0, parent=".", session=None)
    assert root["folders"] == ["Books"]
    books = await browse_nas_folders(root_index=0, parent="Books", session=None)
    assert books["folders"] == ["Books/MANGA_NC"]
    with pytest.raises(HTTPException) as error:
        await browse_nas_folders(root_index=0, parent="../outside", session=None)
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_manual_enqueue_works_while_auto_is_active_and_deduplicates(metadata_db):
    first = await Store.enqueue(server_key(), 9, 1, "check", "auto")
    await Store.execute("UPDATE metadata_runs SET state='running' WHERE id=?", (first,))
    second = await Store.enqueue(server_key(), 9, 1, "check", "manual")
    third = await Store.enqueue(server_key(), 9, 1, "check", "manual")
    other = await Store.enqueue(server_key(), 10, 1, "check", "manual")
    assert first == second == third and other != first
    row = (await Store.query("SELECT followup FROM metadata_runs WHERE id=?", (first,)))[0]
    assert row["followup"] == 1


@pytest.mark.asyncio
async def test_vault_survives_reload_and_has_no_plaintext(metadata_db):
    encrypted = metadata_cipher().encrypt(b"a-private-token")
    assert b"a-private-token" not in encrypted
    assert metadata_cipher().decrypt(encrypted) == b"a-private-token"


@pytest.mark.asyncio
async def test_local_grimmory_login_connects_background_without_kosync(metadata_db, test_client, monkeypatch: pytest.MonkeyPatch):
    from grimmlink_adapter.state.adapter_sessions import AdapterSessionStore

    monkeypatch.setattr(OfficialGrimmoryClient, "login", AsyncMock(return_value=OfficialLoginResponse(
        token="web-access", refreshToken="web-refresh", userId=7, username="web-user",
    )))
    monkeypatch.setattr(OfficialGrimmoryClient, "get_current_user", AsyncMock(return_value={"id": 7, "username": "web-user"}))
    monkeypatch.setattr(OfficialGrimmoryClient, "get_libraries", AsyncMock(return_value=[]))
    base = "/api/grimmlink/v2"
    login = await test_client.post(base + "/local/auth/session", json={"username": "web-user", "password": "web-password"})
    assert login.status_code == 200
    token = login.json()["accessToken"]
    session = await AdapterSessionStore.get(token)
    assert session is not None and session.md5_key is None
    headers = {"Authorization": f"Bearer {token}"}
    libraries = await test_client.get(base + "/local/automation/libraries", headers=headers)
    assert libraries.status_code == 200
    bound = await test_client.put(base + "/local/automation/account", headers=headers)
    assert bound.status_code == 200
    encrypted = (await Store.query("SELECT encrypted_credentials FROM metadata_accounts WHERE user_id=7"))[0]["encrypted_credentials"]
    assert "web-refresh" not in encrypted
    saved = json.loads(metadata_cipher().decrypt(encrypted.encode()))
    assert saved == {"username": "web-user", "access": "web-access", "refresh": "web-refresh", "source": "direct"}
    assert (await test_client.delete(base + "/auth/session", headers=headers)).status_code == 204
    assert await AdapterSessionStore.get(token) is None


@pytest.mark.asyncio
async def test_linked_tokens_are_encrypted_and_legacy_rows_upgrade(metadata_db):
    from grimmlink_adapter.state.linked_accounts import LinkedAccount, LinkedAccountStore

    await LinkedAccountStore.put(LinkedAccount(server_key(), "1", "reader", "digest", "private-access", "private-refresh"))
    raw = (await Store.query("SELECT access_token,refresh_token FROM linked_accounts"))[0]
    assert "private-access" not in raw["access_token"] and "private-refresh" not in raw["refresh_token"]
    account = await LinkedAccountStore.get(server_key(), "1")
    assert account.access_token == "private-access" and account.refresh_token == "private-refresh"
    await Store.execute("UPDATE linked_accounts SET access_token='legacy-access',refresh_token='legacy-refresh'")
    await LinkedAccountStore.encrypt_existing()
    assert (await LinkedAccountStore.get(server_key(), "1")).access_token == "legacy-access"
    assert (await Store.query("SELECT access_token FROM linked_accounts"))[0]["access_token"].startswith("encrypted:v1:")


@pytest.mark.asyncio
async def test_diagnostics_does_not_create_missing_state_database(metadata_db, monkeypatch: pytest.MonkeyPatch):
    from grimmlink_adapter.official.identity_lookup import GrimmoryIdentityLookup
    from grimmlink_adapter.services.diagnostics import collect_diagnostics

    absent = metadata_db / "absent.db"
    monkeypatch.setattr(settings, "SQLITE_DB_PATH", str(absent))
    monkeypatch.setattr(OfficialGrimmoryClient, "check_health", AsyncMock(return_value={"version": "mock"}))
    monkeypatch.setattr(GrimmoryIdentityLookup, "diagnose", AsyncMock(return_value={"name": "identity", "status": "info", "summary": "mock"}))
    report = await collect_diagnostics()
    assert report["security"]["secretsIncluded"] is False
    assert not absent.exists()


def test_readonly_nas_is_ready_for_api_only_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import os

    from grimmlink_adapter.services import diagnostics

    original_access = diagnostics.os.access
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(tmp_path)])
    monkeypatch.setattr(settings, "METADATA_INGESTION_MODE", "api_only")
    monkeypatch.setattr(settings, "METADATA_FALLBACK", "none")
    monkeypatch.setattr(diagnostics.os, "access", lambda path, mode: False if mode == os.W_OK else original_access(path, mode))
    assert diagnostics._check_metadata_roots()["status"] == "ok"
    monkeypatch.setattr(settings, "METADATA_INGESTION_MODE", "sidecar_only")
    assert diagnostics._check_metadata_roots()["status"] == "warning"


@pytest_asyncio.fixture
async def configured_book(metadata_db, monkeypatch: pytest.MonkeyPatch):
    root = metadata_db / "nas"
    root.mkdir()
    (root / ".nas").write_text("available")
    (root / "Book.epub").write_bytes(b"original ebook bytes")
    (root / "Book.opf").write_text('<package><metadata><title>Source title</title><language>th</language></metadata></package>')
    monkeypatch.setattr(settings, "METADATA_IMPORT_ROOTS", [str(root)])
    policy = LibraryPolicy(mappings=[{"libraryPathId": 11, "rootIndex": 0, "localSubpath": ".", "marker": ".nas"}])
    book = {"id": 12, "libraryId": 9, "libraryPath": {"id": 11}, "title": "Old title", "addedOn": "2099-01-01T00:00:00Z", "primaryFile": {"id": 13, "filePath": "/books/Book.epub", "fileName": "Book.epub"}, "metadata": {"title": "Old title", "language": "en", "allMetadataLocked": False}}
    library = {"id": 9, "paths": [{"id": 11, "path": "/books"}]}
    await Store.execute("INSERT INTO metadata_libraries(server,library_id,owner_id,name,config,enrolled_at) VALUES(?,?,?,?,?,?)", (server_key(), 9, 1, "Library", policy.model_dump_json(), time.time()))
    row = (await Store.query("SELECT * FROM metadata_libraries"))[0]
    client = AsyncMock(spec=OfficialGrimmoryClient)
    client.get_book_by_id.side_effect = lambda *args: copy.deepcopy(book)

    async def write_metadata(book_id, bearer, payload):
        book["metadata"].update({key: value for key, value in payload["metadata"].items() if key != "bookId"})
        return copy.deepcopy(book["metadata"])

    client.update_book_metadata.side_effect = write_metadata

    async def identity(self, *args):
        return {"book_id": 12, "book_file_id": 13}, copy.deepcopy(book)

    monkeypatch.setattr(OPFIngestionService, "_resolve_identity", identity)
    monkeypatch.setattr(OPFIngestionService, "_source_file_writes_disabled", AsyncMock(return_value=True))
    worker = MetadataAutomation()
    await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check")
    item = await Store.item(server_key(), 9, 12)
    item["observation"]["stable_since"] -= 40
    await Store.save_item(server_key(), 9, 12, 13, item)
    return worker, client, row, policy, library, book, root


@pytest.mark.asyncio
async def test_manual_run_without_auto_updates_and_verifies(configured_book):
    worker, client, row, policy, library, book, root = configured_book
    assert policy.autoEnabled is False
    state = await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check")
    assert state == "updated"
    assert book["metadata"]["title"] == "Source title"
    assert (root / "Book.epub").read_bytes() == b"original ebook bytes"
    item = await Store.item(server_key(), 9, 12)
    assert item["baseline"]["title"] == "Source title" and not item["intent"]
    reads = client.get_book_by_id.await_count
    await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check")
    assert client.update_book_metadata.await_count == 1
    assert client.get_book_by_id.await_count == reads


@pytest.mark.asyncio
async def test_preview_never_writes_metadata_or_adopts_baseline(configured_book):
    worker, client, row, policy, library, book, root = configured_book
    assert await worker.process(client, "token", row, policy, library, book, "preview") == "preview"
    client.update_book_metadata.assert_not_awaited()
    assert (await Store.item(server_key(), 9, 12))["baseline"] == {}


@pytest.mark.asyncio
async def test_unsafe_persistence_blocks_write(configured_book, monkeypatch: pytest.MonkeyPatch):
    worker, client, row, policy, library, book, root = configured_book
    monkeypatch.setattr(OPFIngestionService, "_source_file_writes_disabled", AsyncMock(return_value=False))
    with pytest.raises(MetadataBlocked, match="disable_grimmory"):
        await worker.process(client, "token", row, policy, library, book, "check")
    client.update_book_metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_change_preserves_manual_grimmory_change(configured_book):
    worker, client, row, policy, library, book, root = configured_book
    await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check")
    book["metadata"]["title"] = "User edit"
    (root / "Book.opf").write_text('<package><metadata><title>Changed source</title></metadata></package>')
    assert await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check") == "waiting_stable"
    item = await Store.item(server_key(), 9, 12)
    item["observation"]["stable_since"] -= 40
    await Store.save_item(server_key(), 9, 12, 13, item)
    assert await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check") == "needs_review"
    assert book["metadata"]["title"] == "User edit"
    assert client.update_book_metadata.await_count == 1


@pytest.mark.asyncio
async def test_interrupted_write_is_read_back_before_replay(configured_book):
    worker, client, row, policy, library, book, root = configured_book
    item = await Store.item(server_key(), 9, 12)
    item["intent"] = {"title": {"before": "Old title", "after": "Source title"}, "language": {"before": "en", "after": "th"}}
    book["metadata"].update(title="Source title", language="th")
    await Store.save_item(server_key(), 9, 12, 13, item)
    assert await worker.process(client, "token", row, policy, library, book, "check") == "in_sync"
    client.update_book_metadata.assert_not_awaited()
    assert not (await Store.item(server_key(), 9, 12))["intent"]


@pytest.mark.asyncio
async def test_sources_wait_until_book_is_in_library(configured_book):
    worker, client, row, policy, library, book, root = configured_book
    roots = check_storage(policy)
    known: set[Path] = set()
    assert pending_sources(known, roots, policy) == ["Book.epub"]
    remember_book_paths(known, [book], library, policy, roots)
    assert pending_sources(known, roots, policy) == []
    wrong = copy.deepcopy(book)
    wrong["primaryFile"]["filePath"] = "/elsewhere/Book.epub"
    with pytest.raises(ValueError):
        inspect_sources(wrong, library, policy)


@pytest.mark.asyncio
async def test_partial_readback_keeps_unverified_intent(configured_book):
    worker, client, row, policy, library, book, root = configured_book

    async def partial_write(*args):
        book["metadata"]["language"] = "th"

    client.update_book_metadata.side_effect = partial_write
    assert await worker.process(client, "token", row, policy, library, copy.deepcopy(book), "check") == "partial"
    item = await Store.item(server_key(), 9, 12)
    assert "title" not in item["baseline"]
    assert item["baseline"]["language"] == "th"
    assert set(item["intent"]) == {"title"}


@pytest.mark.asyncio
async def test_worker_yields_between_libraries_and_honors_stop(configured_book, monkeypatch: pytest.MonkeyPatch):
    import grimmlink_adapter.services.metadata_automation as module

    worker, client, row, policy, library, book, root = configured_book
    worker.bearer = AsyncMock(return_value="token")
    worker.process = AsyncMock(return_value="in_sync")
    client.__aenter__.return_value = client
    client.get_library.return_value = library
    async def pages(*_):
        yield [{"id": value, "libraryId": 9} for value in range(1, 26)]

    client.iter_library_book_pages.side_effect = pages
    monkeypatch.setattr(module, "OfficialGrimmoryClient", lambda: client)
    first = await Store.enqueue(server_key(), 9, 1, "check", "manual")
    second = await Store.enqueue(server_key(), 10, 1, "check", "manual")
    run = (await Store.query("SELECT * FROM metadata_runs WHERE id=?", (first,)))[0]
    await worker.run(run)
    assert worker.process.await_count == 20
    client.get_book_by_id.assert_not_awaited()
    assert len(await Store.book_batch(first, 20, 20)) == 5
    queued = await Store.query("SELECT * FROM metadata_runs WHERE state='queued' ORDER BY updated_at")
    assert queued[0]["id"] == second
    await Store.execute("UPDATE metadata_runs SET cancel_requested=1 WHERE id=?", (first,))
    await worker.run((await Store.query("SELECT * FROM metadata_runs WHERE id=?", (first,)))[0])
    assert (await Store.query("SELECT state FROM metadata_runs WHERE id=?", (first,)))[0]["state"] == "cancelled"
    assert worker.process.await_count == 20
    assert not await Store.book_batch(first, 0, 20)


@pytest.mark.asyncio
async def test_worker_resumes_staged_books_after_restart(configured_book, monkeypatch: pytest.MonkeyPatch):
    import grimmlink_adapter.services.metadata_automation as module

    worker, client, _, _, library, _, _ = configured_book
    worker.bearer = AsyncMock(return_value="token")
    worker.process = AsyncMock(return_value="no_source")
    client.__aenter__.return_value = client
    client.get_library.return_value = library
    monkeypatch.setattr(module, "OfficialGrimmoryClient", lambda: client)
    run_id = await Store.enqueue(server_key(), 9, 1, "check", "manual")
    await Store.add_book_page(run_id, 0, [{"id": 12, "libraryId": 9}, {"id": 13, "libraryId": 9, "title": "Missing metadata book"}])
    progress = {"checked": 1, "total": 2, "position": 1, "phase": "processing", "counts": {"no_source": 1}}
    await Store.execute("UPDATE metadata_runs SET result=? WHERE id=?", (json.dumps(progress), run_id))
    await worker.run((await Store.query("SELECT * FROM metadata_runs WHERE id=?", (run_id,)))[0])
    worker.process.assert_awaited_once()
    assert worker.process.await_args.args[5]["id"] == 13
    client.iter_library_book_pages.assert_not_called()
    final = (await Store.query("SELECT state,result FROM metadata_runs WHERE id=?", (run_id,)))[0]
    assert final["state"] == "completed"
    assert json.loads(final["result"])["checked"] == 2
    assert json.loads(final["result"])["missingMetadata"] == [{"bookId": 13, "title": "Missing metadata book"}]
    assert not await Store.book_batch(run_id, 0, 20)


@pytest.mark.asyncio
async def test_restart_recovers_running_queue_without_discarding_intent(metadata_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "METADATA_WORKER_ENABLED", False)
    run_id = await Store.enqueue(server_key(), 9, 1, "check", "manual")
    await Store.execute("UPDATE metadata_runs SET state='running' WHERE id=?", (run_id,))
    await Store.save_item(server_key(), 9, 12, 13, {"intent": {"title": {"before": "old", "after": "new"}}})
    worker = MetadataAutomation()
    await worker.start()
    assert (await Store.query("SELECT state FROM metadata_runs WHERE id=?", (run_id,)))[0]["state"] == "queued"
    assert (await Store.item(server_key(), 9, 12))["intent"]["title"]["after"] == "new"
    await worker.stop()


@pytest.mark.asyncio
async def test_library_api_never_reveals_other_owners_activity(metadata_db, test_client):
    from grimmlink_adapter.api.automation import operator

    test_client._transport.app.dependency_overrides[operator] = lambda: 2
    test_client._transport.app.dependency_overrides[require_v2_credentials] = lambda: ClientCredentials(bearer_token="token")
    await Store.execute("INSERT INTO metadata_libraries VALUES(?,?,?,?,?,?,?)", (server_key(), 9, 1, "Private", "{}", 0, time.time()))
    try:
        response = await test_client.get("/api/grimmlink/v2/local/automation/activity?library_id=9")
        assert response.status_code == 404
    finally:
        test_client._transport.app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_paginated_library_uses_grimmory_metadata_title():
    def handle(_request):
        return httpx.Response(200, json={
            "content": [{"id": 28, "libraryId": 4, "metadata": {"title": "Real book title"},
                         "primaryFile": {"id": 29, "filePath": "/books/Book.epub", "fileName": "Book.epub"}}],
            "page": {"totalElements": 1},
        })

    async with httpx.AsyncClient(base_url="http://metadata.test", transport=httpx.MockTransport(handle)) as transport:
        async with OfficialGrimmoryClient(base_url="http://metadata.test", client=transport) as client:
            books = await client.get_library_books(4, "token")
    assert books[0]["title"] == "Real book title"


@pytest.mark.asyncio
async def test_paginated_library_rejects_duplicate_or_incomplete_snapshot():
    calls = []

    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"content": [{"id": 1, "libraryId": 9}], "page": {"totalElements": 2}, "links": [{"rel": ["next"], "href": "https://other-host.invalid/api/v1/books/page?cursor=next"}]})

    async with httpx.AsyncClient(base_url="http://metadata.test", transport=httpx.MockTransport(handle)) as transport:
        async with OfficialGrimmoryClient(base_url="http://metadata.test", client=transport) as client:
            with pytest.raises(OfficialBadResponseError):
                await client.get_library_books(9, "token")
    assert len(calls) == 2 and all(url.startswith("http://metadata.test/") for url in calls)


@pytest.mark.asyncio
async def test_incomplete_discovery_does_not_apply_staged_books(configured_book, monkeypatch: pytest.MonkeyPatch):
    import grimmlink_adapter.services.metadata_automation as module

    worker, client, _, _, library, book, _ = configured_book
    worker.bearer = AsyncMock(return_value="token")
    worker.process = AsyncMock()
    client.__aenter__.return_value = client
    client.get_library.return_value = library

    async def incomplete(*_):
        yield [book]
        raise OfficialBadResponseError("Incomplete library snapshot")

    client.iter_library_book_pages.side_effect = incomplete
    monkeypatch.setattr(module, "OfficialGrimmoryClient", lambda: client)
    run_id = await Store.enqueue(server_key(), 9, 1, "check", "manual")
    await worker.run((await Store.query("SELECT * FROM metadata_runs WHERE id=?", (run_id,)))[0])
    worker.process.assert_not_awaited()
    assert not await Store.book_batch(run_id, 0, 20)
    assert (await Store.query("SELECT state FROM metadata_runs WHERE id=?", (run_id,)))[0]["state"] == "failed"


@pytest.mark.asyncio
async def test_activity_summary_and_compact_defer_large_diffs(configured_book, test_client, monkeypatch: pytest.MonkeyPatch):
    from grimmlink_adapter.api.automation import operator

    _, _, _, _, library, _, _ = configured_book
    monkeypatch.setattr(OfficialGrimmoryClient, "get_library", AsyncMock(return_value=library))
    overrides = test_client._transport.app.dependency_overrides
    overrides[operator] = lambda: 1
    overrides[require_v2_credentials] = lambda: ClientCredentials(bearer_token="token")
    result = {"state": "needs_review", "title": "Book", "conflicts": {"description": {"source": "a" * 50_000}}}
    await Store.save_item(server_key(), 9, 12, 13, {"result": result})
    base = "/api/grimmlink/v2/local/automation"
    try:
        summary = await test_client.get(base + "/activity?library_id=9&summary=true")
        assert summary.status_code == 200 and set(summary.json()) == {"runs"}
        compact = await test_client.get(base + "/activity?library_id=9&compact=true")
        assert compact.status_code == 200 and len(compact.content) < 1_000
        assert compact.json()["items"][0]["result"]["state"] == "needs_review"
        detail = await test_client.get(base + "/libraries/9/books/12/activity")
        assert detail.status_code == 200 and detail.json()["result"] == result
    finally:
        overrides.clear()
