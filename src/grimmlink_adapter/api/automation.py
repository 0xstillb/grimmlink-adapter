"""Authenticated operator endpoints for multi-library metadata runs."""

import asyncio
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from grimmlink_adapter.config import settings
from grimmlink_adapter.models.automation import (
    LibraryPolicy,
    MetadataRunRequest,
    ResolveMetadataConflict,
)
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.security.auth_extractor import (
    ClientCredentials,
    get_adapter_session,
    require_v2_credentials,
)
from grimmlink_adapter.services.metadata_automation import (
    MetadataBlocked,
    check_storage,
    metadata_automation,
    metadata_execution_lock,
    server_key,
)
from grimmlink_adapter.state.adapter_sessions import AdapterSession, AdapterSessionStore
from grimmlink_adapter.state.metadata_automation import MetadataAutomationStore as Store

router = APIRouter(prefix="/local/automation", tags=["Metadata automation"], dependencies=[Depends(require_v2_credentials)])


async def operator(session: tuple[str, AdapterSession] = Depends(get_adapter_session)) -> int:
    return session[1].user_id


async def owned(library_id: int, owner_id: int) -> dict[str, Any]:
    rows = await Store.query("SELECT * FROM metadata_libraries WHERE server=? AND library_id=? AND owner_id=?", (server_key(), library_id, owner_id))
    if not rows:
        raise HTTPException(404, "Library configuration not found for this account")
    return rows[0]


@router.get("/libraries")
async def libraries(response: Response, owner_id: int = Depends(operator), creds: ClientCredentials = Depends(require_v2_credentials)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    async with OfficialGrimmoryClient() as client:
        upstream = await client.get_libraries(creds.bearer_token or "")
    policies = {row["library_id"]: row for row in await Store.query("SELECT * FROM metadata_libraries WHERE server=? AND owner_id=?", (server_key(), owner_id))}
    accounts = await Store.query("SELECT blocked_reason FROM metadata_accounts WHERE server=? AND user_id=?", (server_key(), owner_id))
    for library in upstream:
        row = policies.get(library["id"])
        library["policy"] = json.loads(row["config"]) if row else None
        library["nextDue"] = row["next_due"] if row and library["policy"]["autoEnabled"] else None
    return {"libraries": upstream, "roots": [{"index": index, "path": path} for index, path in enumerate(settings.METADATA_IMPORT_ROOTS)],
            "accountConnected": bool(accounts), "accountBlocked": accounts[0]["blocked_reason"] if accounts else None,
            "workerEnabled": settings.METADATA_WORKER_ENABLED}


@router.put("/account")
async def bind_account(owner_id: int = Depends(operator), creds: ClientCredentials = Depends(require_v2_credentials), session: tuple[str, AdapterSession] = Depends(get_adapter_session)) -> dict[str, str]:
    try:
        current = await AdapterSessionStore.get(session[0])
        await metadata_automation.bind_account(
            owner_id, creds.bearer_token or "",
            username=current.username if current and current.official_access_token else None,
            refresh_token=current.official_refresh_token if current and current.official_access_token else None,
        )
    except MetadataBlocked as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"status": "connected"}


@router.put("/libraries/{library_id}")
async def save_policy(library_id: int, policy: LibraryPolicy, owner_id: int = Depends(operator), creds: ClientCredentials = Depends(require_v2_credentials)) -> dict[str, Any]:
    async with OfficialGrimmoryClient() as client:
        library = await client.get_library(library_id, creds.bearer_token or "")
    paths = {path["id"] for path in library.get("paths", [])}
    if any(mapping.libraryPathId not in paths for mapping in policy.mappings):
        raise HTTPException(422, "Mapping does not belong to this library")
    for mapping in policy.mappings:
        if mapping.rootIndex >= len(settings.METADATA_IMPORT_ROOTS):
            raise HTTPException(422, "Adapter import root is no longer configured")
        mapping.rootPath = settings.METADATA_IMPORT_ROOTS[mapping.rootIndex]
    try:
        await asyncio.to_thread(check_storage, policy)
    except (OSError, MetadataBlocked) as exc:
        raise HTTPException(422, str(exc) if isinstance(exc, MetadataBlocked) else "storage_unavailable") from exc
    if policy.autoEnabled:
        if not settings.METADATA_WORKER_ENABLED or not settings.GRIMMORY_DB_ENABLED:
            raise HTTPException(422, "Enable the metadata worker and read-only identity database lookup first")
        try:
            await metadata_automation.bearer(owner_id)
        except MetadataBlocked as exc:
            raise HTTPException(422, str(exc)) from exc
    # Owner checks and update are serialized by a short SQLite transaction.
    from grimmlink_adapter.state.database import get_connection

    async with get_connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        async with conn.execute("SELECT owner_id FROM metadata_libraries WHERE server=? AND library_id=?", (server_key(), library_id)) as cursor:
            existing = await cursor.fetchone()
        if existing and existing["owner_id"] != owner_id:
            raise HTTPException(409, "This library is managed by another linked account")
        await conn.execute("""INSERT INTO metadata_libraries(server,library_id,owner_id,name,config,enrolled_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(server,library_id) DO UPDATE SET name=excluded.name,config=excluded.config""",
                           (server_key(), library_id, owner_id, library.get("name", str(library_id)), policy.model_dump_json(), time.time()))
        await conn.commit()
    metadata_automation.wake.set()
    if not policy.autoEnabled:
        await Store.execute("UPDATE metadata_runs SET cancel_requested=1,followup=0,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END WHERE server=? AND library_id=? AND requested_by='auto' AND state IN ('queued','running')", (server_key(), library_id))
    return {"status": "saved", "policy": policy.model_dump()}


@router.post("/runs", status_code=202)
async def start_runs(request: MetadataRunRequest, owner_id: int = Depends(operator), creds: ClientCredentials = Depends(require_v2_credentials)) -> dict[str, Any]:
    if not settings.METADATA_WORKER_ENABLED:
        raise HTTPException(409, "Metadata worker is disabled")
    # Validate all selected libraries before creating any runs. No implicit all-libraries authority.
    async with OfficialGrimmoryClient() as client:
        for library_id in set(request.libraryIds):
            await owned(library_id, owner_id)
            await client.get_library(library_id, creds.bearer_token or "")
    try:
        await metadata_automation.bearer(owner_id)
    except MetadataBlocked as exc:
        raise HTTPException(422, str(exc)) from exc
    runs = [await Store.enqueue(server_key(), library_id, owner_id, request.mode, "manual") for library_id in set(request.libraryIds)]
    metadata_automation.wake.set()
    return {"runIds": runs}


@router.get("/activity")
async def activity(
    response: Response, library_id: int, summary: bool = False, compact: bool = False,
    owner_id: int = Depends(operator), creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    await owned(library_id, owner_id)
    async with OfficialGrimmoryClient() as client:
        await client.get_library(library_id, creds.bearer_token or "")
    runs = await Store.query("SELECT id,mode,state,requested_by,created_at,updated_at,result,followup,available_at,attempts FROM metadata_runs WHERE server=? AND library_id=? AND owner_id=? ORDER BY created_at DESC LIMIT 20", (server_key(), library_id, owner_id))
    for run in runs:
        run["result"] = json.loads(run["result"])
        run["result"].pop("_bookIds", None)
    if summary:
        return {"runs": runs}
    if compact:
        items = await Store.query(
            """SELECT book_id,file_id,updated_at,
               json_object('title', substr(json_extract(result,'$.title'),1,512),
                           'state', json_extract(result,'$.state'),
                           'reason', json_extract(result,'$.reason'),
                           'cover', json_extract(result,'$.cover')) AS result
               FROM metadata_items WHERE server=? AND library_id=? ORDER BY updated_at DESC LIMIT 200""",
            (server_key(), library_id),
        )
        events = await Store.query(
            """SELECT id,book_id,created_at,json_object('state', json_extract(result,'$.state')) AS result
               FROM metadata_events WHERE server=? AND library_id=? ORDER BY id DESC LIMIT 100""",
            (server_key(), library_id),
        )
        for row in [*items, *events]:
            row["result"] = json.loads(row["result"])
        return {"runs": runs, "items": items, "events": events}
    items = await Store.query("SELECT book_id,file_id,result,updated_at FROM metadata_items WHERE server=? AND library_id=? ORDER BY updated_at DESC LIMIT 200", (server_key(), library_id))
    events = await Store.query("SELECT id,book_id,result,created_at FROM metadata_events WHERE server=? AND library_id=? ORDER BY id DESC LIMIT 100", (server_key(), library_id))
    for row in [*items, *events]:
        row["result"] = json.loads(row["result"])
    return {"runs": runs, "items": items, "events": events}


@router.get("/libraries/{library_id}/books/{book_id}/activity")
async def book_activity(
    response: Response, library_id: int, book_id: int, owner_id: int = Depends(operator),
    creds: ClientCredentials = Depends(require_v2_credentials),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    await owned(library_id, owner_id)
    async with OfficialGrimmoryClient() as client:
        await client.get_library(library_id, creds.bearer_token or "")
    rows = await Store.query(
        "SELECT result FROM metadata_items WHERE server=? AND library_id=? AND book_id=?",
        (server_key(), library_id, book_id),
    )
    if not rows:
        raise HTTPException(404, "Book activity not found")
    return {"result": json.loads(rows[0]["result"])}


@router.post("/runs/{run_id}/stop")
async def stop_run(run_id: str, owner_id: int = Depends(operator)) -> dict[str, str]:
    rows = await Store.query("SELECT id FROM metadata_runs WHERE id=? AND server=? AND owner_id=?", (run_id, server_key(), owner_id))
    if not rows:
        raise HTTPException(404, "Run not found")
    await Store.execute("UPDATE metadata_runs SET cancel_requested=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,followup=0 WHERE id=?", (run_id,))
    return {"status": "stop_requested"}


@router.post("/libraries/{library_id}/books/{book_id}/resolve")
async def resolve_conflict(library_id: int, book_id: int, request: ResolveMetadataConflict, owner_id: int = Depends(operator), creds: ClientCredentials = Depends(require_v2_credentials)) -> dict[str, Any]:
    await owned(library_id, owner_id)
    async with OfficialGrimmoryClient() as client:
        detail = await client.get_book_by_id(book_id, creds.bearer_token or "")
    if detail.get("libraryId") != library_id:
        raise HTTPException(409, "Book has moved libraries")
    async with metadata_execution_lock:
        item = await Store.item(server_key(), library_id, book_id)
        result = item["result"]
        conflicts = result.get("conflicts", {})
        if result.get("revision") != request.revision or not set(request.fields).issubset(conflicts):
            raise HTTPException(409, "Preview changed; refresh the activity before resolving")
        choices = item["observation"].setdefault("resolutions", {"revision": request.revision, "fields": {}})
        if choices["revision"] != request.revision:
            choices = {"revision": request.revision, "fields": {}}
            item["observation"]["resolutions"] = choices
        for field in request.fields:
            choices["fields"][field] = {"choice": request.choice, "before": conflicts[field]["grimmory"]}
        await Store.save_item(server_key(), library_id, book_id, item["file_id"], item)
    run_id = await Store.enqueue(server_key(), library_id, owner_id, "check", "manual")
    metadata_automation.wake.set()
    return {"runId": run_id}
