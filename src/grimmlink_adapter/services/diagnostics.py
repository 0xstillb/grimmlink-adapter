"""Safe, read-only diagnostics shared by the UI and command line."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from grimmlink_adapter import __version__
from grimmlink_adapter.config import settings
from grimmlink_adapter.official.client import OfficialGrimmoryClient
from grimmlink_adapter.official.identity_lookup import GrimmoryIdentityLookup
from grimmlink_adapter.services.prose_converter import ProseConverter


def _check(name: str, status: str, summary: str, action: str | None = None, **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {"name": name, "status": status, "summary": summary}
    if action:
        result["action"] = action
    if details:
        result["details"] = details
    return result


async def collect_diagnostics(mdns_status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run bounded, read-only checks without returning credentials or tokens."""
    checks: list[dict[str, Any]] = []
    checks.append(_check(
        "adapter", "ok", "Adapter is running.",
        version=__version__, python=platform.python_version(), platform=sys.platform,
    ))

    checks.append(await _check_sqlite())
    checks.append(await _check_grimmory())
    checks.append(await GrimmoryIdentityLookup().diagnose())
    checks.append(_check_prose_converter())
    checks.append(_check_metadata_roots())
    try:
        path = Path(settings.SQLITE_DB_PATH).expanduser().resolve()
        async with aiosqlite.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            connection.row_factory = aiosqlite.Row
            async with connection.execute("SELECT state,COUNT(*) AS count,MAX(updated_at) AS last_activity FROM metadata_runs WHERE server=? GROUP BY state", (settings.GRIMMORY_BASE_URL.rstrip("/"),)) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            async with connection.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN blocked_reason IS NOT NULL THEN 1 ELSE 0 END) AS blocked FROM metadata_accounts WHERE server=?", (settings.GRIMMORY_BASE_URL.rstrip("/"),)) as cursor:
                account_row = await cursor.fetchone()
                account = dict(account_row) if account_row else {"total": 0, "blocked": 0}
            async with connection.execute("SELECT COUNT(*) FROM metadata_libraries WHERE server=?", (settings.GRIMMORY_BASE_URL.rstrip("/"),)) as cursor:
                library_row = await cursor.fetchone()
                library_count = library_row[0] if library_row else 0
        connected = account["total"] or 0
        blocked = account["blocked"] or 0
        if blocked:
            status, summary, action = "warning", "บัญชีงาน Metadata บางบัญชีต้องเชื่อมต่อใหม่", "เปิดหน้า Metadata แล้วเชื่อมบัญชีงานเบื้องหลังใหม่"
        elif library_count and not settings.METADATA_WORKER_ENABLED:
            status, summary, action = "warning", "Worker ของ Metadata ปิดอยู่", "เปิด METADATA_WORKER_ENABLED เพื่อให้ Auto และงาน Manual ทำงาน"
        elif library_count and not connected:
            status, summary, action = "warning", "ยังไม่ได้เชื่อมบัญชีงาน Metadata เบื้องหลัง", "เปิดหน้า Metadata แล้วกดเชื่อมบัญชีงานเบื้องหลัง"
        elif connected and settings.METADATA_WORKER_ENABLED:
            status, summary, action = "ok", "Worker พร้อมทำงาน ทั้ง Auto และการนำเข้า Manual", None
        else:
            status, summary, action = "info", "ยังไม่มี Library ที่ตั้งงาน Metadata", None
        checks.append(_check("metadataWorker", status, summary, action,
                             workerEnabled=settings.METADATA_WORKER_ENABLED, queue=rows,
                             connectedAccounts=connected, blockedAccounts=blocked, configuredLibraries=library_count))
    except Exception as exc:
        checks.append(_check("metadataWorker", "warning", "Metadata automation state is unavailable.",
                             "Start the Adapter to apply its database migrations.", errorCode=type(exc).__name__))
    checks.append(_check(
        "sessionRuntime", "info",
        "เซสชันหน้าเว็บมีอายุ 8 ชั่วโมง; งาน Auto ใช้บัญชีเบื้องหลังแยกต่างหาก",
    ))

    if mdns_status is None:
        mdns_status = {"enabled": settings.MDNS_ENABLED, "running": False, "inspected": False}
    if not mdns_status.get("enabled"):
        checks.append(_check("mdns", "warning", "mDNS advertising is disabled.", "Enable MDNS_ENABLED to publish Grimmlink.local."))
    elif mdns_status.get("running"):
        checks.append(_check(
            "mdns", "ok", "The Adapter is advertising its local address over mDNS.",
            host=mdns_status.get("host"), address=mdns_status.get("address"), port=mdns_status.get("port"),
        ))
    elif mdns_status.get("inspected"):
        checks.append(_check(
            "mdns", "fail", "mDNS could not be advertised.",
            "For Docker bridge networking, set MDNS_IPV4 to the host's LAN address and allow multicast DNS on the LAN.",
            errorCode=mdns_status.get("errorCode"),
        ))
    else:
        checks.append(_check(
            "mdns", "warning", "The command-line diagnostic is not attached to a running Adapter service.",
            "Check mDNS from the authenticated UI or open http://<adapter-host>:<port> directly.",
        ))

    statuses = [item["status"] for item in checks]
    overall = "failed" if "fail" in statuses else "needs_setup" if "warning" in statuses else "ready"
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "overall": overall,
        "checks": checks,
        "security": {
            "secretsIncluded": False,
            "grimmoryDatabaseAccess": "read-only SELECT probes only",
        },
    }


async def _check_sqlite() -> dict[str, Any]:
    path = Path(settings.SQLITE_DB_PATH).expanduser().resolve()
    try:
        if not path.is_file():
            return _check("adapterDatabase", "fail", "Adapter SQLite file does not exist yet.", "Start the Adapter once so it can create and migrate its state database.", path=str(path))
        async with aiosqlite.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
            async with connection.execute("PRAGMA quick_check") as cursor:
                row = await cursor.fetchone()
        if row and row[0] == "ok":
            return _check("adapterDatabase", "ok", "Adapter SQLite passed read-only quick_check.", path=str(path))
        return _check("adapterDatabase", "fail", "Adapter SQLite reported an integrity issue.", "Stop the Adapter and inspect its SQLite file before syncing.")
    except Exception as exc:
        return _check(
            "adapterDatabase", "fail", "Adapter SQLite could not be checked.",
            "Check that SQLITE_DB_PATH exists and is writable by the Adapter process.",
            errorCode=type(exc).__name__,
        )


async def _check_grimmory() -> dict[str, Any]:
    client = OfficialGrimmoryClient()
    try:
        response = await client.check_health()
        version = response.get("version") if isinstance(response, dict) else None
        return _check("grimmoryApi", "ok", "Grimmory healthcheck responded.", version=version)
    except Exception as exc:
        return _check(
            "grimmoryApi", "fail", "Grimmory healthcheck did not succeed.",
            "Check GRIMMORY_BASE_URL, DNS/network access, TLS certificate settings, and that Grimmory is running.",
            errorCode=type(exc).__name__,
        )
    finally:
        await client.aclose()


def _check_metadata_roots() -> dict[str, Any]:
    roots: list[dict[str, Any]] = []
    for index, configured in enumerate(settings.METADATA_IMPORT_ROOTS):
        path = Path(configured).expanduser()
        exists = path.is_dir()
        roots.append({
            "index": index,
            "name": path.name or f"root-{index}",
            "available": exists,
            "readable": exists and os.access(path, os.R_OK),
            "writable": exists and os.access(path, os.W_OK),
        })
    if not roots:
        return _check("metadataRoots", "warning", "No metadata import folders are configured.", "Set METADATA_IMPORT_ROOTS to folders visible inside the Adapter/container.", roots=[])
    unavailable = sum(not item["available"] or not item["readable"] for item in roots)
    if unavailable:
        return _check("metadataRoots", "fail", "One or more metadata folders are unavailable to the Adapter.", "Check the container volume mapping and folder permissions.", roots=roots)
    readonly = sum(not item["writable"] for item in roots)
    needs_sidecar_write = settings.METADATA_INGESTION_MODE == "sidecar_only" or (settings.METADATA_INGESTION_MODE == "api_preferred" and settings.METADATA_FALLBACK == "sidecar")
    if readonly and needs_sidecar_write:
        return _check(
            "metadataRoots", "warning", "อ่านโฟลเดอร์ Metadata ได้ แต่โหมด sidecar ต้องเขียนไฟล์ข้างหนังสือ",
            "ให้สิทธิ์เขียนโฟลเดอร์ NAS ที่เลือก หรือเปลี่ยนเป็นโหมดอัปเดตผ่าน API",
            roots=roots,
        )
    return _check("metadataRoots", "ok", "โฟลเดอร์ Metadata อ่านได้และพร้อมใช้งาน" if not readonly else "โฟลเดอร์ NAS อ่านได้ พร้อมอัปเดต Metadata ผ่าน API", roots=roots)


def _check_prose_converter() -> dict[str, Any]:
    worker = ProseConverter._worker_path()
    installed = (
        shutil.which("node") is not None
        and worker.is_file()
        and (worker.parent / "node_modules" / "@prose-reader" / "koreader").is_dir()
    )
    if installed:
        return _check("proseEpubConversion", "ok", "Prose EPUB position converter is installed.")
    return _check(
        "proseEpubConversion", "fail", "Prose EPUB position converter is unavailable.",
        "Install the pinned Node dependencies or rebuild the Adapter image with the Prose worker.",
    )


async def main() -> None:
    """CLI entry point: uv run python -m grimmlink_adapter.diagnostics"""
    import json

    report = await collect_diagnostics()
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
