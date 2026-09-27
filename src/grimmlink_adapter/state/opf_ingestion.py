"""Adapter-local OPF ingestion dedupe state."""

from __future__ import annotations

from dataclasses import dataclass

from grimmlink_adapter.state.database import get_connection


@dataclass(frozen=True)
class OPFState:
    source_opf_path: str
    source_fingerprint: str
    cover_fingerprint: str | None
    book_id: int
    metadata_hash: str
    delivery_mode: str
    last_api_result: str | None
    last_sidecar_result: str | None
    last_result_status: str
    fallback_reason: str | None
    last_success_at: str | None


class OPFStateStore:
    @staticmethod
    async def get(source_opf_path: str) -> OPFState | None:
        async with get_connection() as conn, conn.execute(
            "SELECT source_opf_path, source_fingerprint, book_id, metadata_hash, "
            "cover_fingerprint, delivery_mode, last_api_result, last_sidecar_result, "
            "last_result_status, fallback_reason, last_success_at "
            "FROM opf_ingestion WHERE source_opf_path = ?",
            (source_opf_path,),
        ) as cursor:
            row = await cursor.fetchone()
            return OPFState(**dict(row)) if row else None

    @staticmethod
    async def put(
        *, source_opf_path: str, source_fingerprint: str, cover_fingerprint: str | None,
        book_id: int, metadata_hash: str, delivery_mode: str, api_result: str | None,
        sidecar_result: str | None, result_status: str, fallback_reason: str | None,
        success: bool,
    ) -> None:
        async with get_connection() as conn:
            await conn.execute(
                """INSERT INTO opf_ingestion
                   (source_opf_path, source_fingerprint, cover_fingerprint, book_id,
                    metadata_hash, delivery_mode, last_api_result, last_sidecar_result,
                    last_result_status, fallback_reason,
                    last_success_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
                   ON CONFLICT(source_opf_path) DO UPDATE SET
                     source_fingerprint=excluded.source_fingerprint,
                     cover_fingerprint=excluded.cover_fingerprint,
                     book_id=excluded.book_id,
                     metadata_hash=excluded.metadata_hash,
                     delivery_mode=excluded.delivery_mode,
                     last_api_result=excluded.last_api_result,
                     last_sidecar_result=excluded.last_sidecar_result,
                     last_result_status=excluded.last_result_status,
                     fallback_reason=excluded.fallback_reason,
                     last_success_at=excluded.last_success_at,
                     updated_at=CURRENT_TIMESTAMP""",
                (source_opf_path, source_fingerprint, cover_fingerprint, book_id,
                 metadata_hash, delivery_mode, api_result, sidecar_result,
                 result_status, fallback_reason, int(success)),
            )
            await conn.commit()
