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
            async with connection.execute("SELECT COUNT(*) AS count FROM metadata_accounts WHERE server=? AND blocked_reason IS NOT NULL", (settings.GRIMMORY_BASE_URL.rstrip("/"),)) as cursor:
                accounts = [dict(row) for row in await cursor.fetchall()]
        checks.append(_check("metadataWorker", "warning" if accounts[0]["count"] else "info",
                             "Metadata automation queue summary (no field values or credentials).",
                             workerEnabled=settings.METADATA_WORKER_ENABLED, queue=rows,
                             blockedAccounts=accounts[0]["count"]))
    except Exception as exc:
        checks.append(_check("metadataWorker", "warning", "Metadata automation state is unavailable.",
                             "Start the Adapter to apply its database migrations.", errorCode=type(exc).__name__))
    checks.append(_check(
        "sessionRuntime", "info",
        "Adapter bearer sessions are held in process memory and expire after eight hours.",
        "Use one worker and one replica; restarting the Adapter requires readers to sign in again.",
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
    if readonly:
        return _check(
            "metadataRoots", "warning", "Metadata folders are readable, but sidecar fallback cannot write beside books.",
            "This is fine for API-only metadata updates. If you need sidecar fallback, mount the selected folder writable.",
            roots=roots,
        )
    return _check("metadataRoots", "ok", "All configured metadata folders are readable.", roots=roots)


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
