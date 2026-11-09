"""Durable metadata policies, run queue, and verified per-field baselines."""

import json
import time
from typing import Any
from uuid import uuid4

from grimmlink_adapter.state.database import get_connection


class MetadataAutomationStore:
    @staticmethod
    async def query(sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        async with get_connection() as conn:
            async with conn.execute(sql, args) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def execute(sql: str, args: tuple[Any, ...] = ()) -> None:
        async with get_connection() as conn:
            await conn.execute(sql, args)
            await conn.commit()

    @staticmethod
    async def add_book_page(run_id: str, position: int, books: list[dict[str, Any]]) -> None:
        async with get_connection() as conn:
            await conn.executemany(
                "INSERT INTO metadata_run_books(run_id,position,book) VALUES(?,?,?)",
                (
                    (run_id, offset, json.dumps(book, ensure_ascii=False))
                    for offset, book in enumerate(books, start=position)
                ),
            )
            await conn.commit()

    @staticmethod
    async def book_batch(run_id: str, position: int, size: int) -> list[dict[str, Any]]:
        rows = await MetadataAutomationStore.query(
            "SELECT book FROM metadata_run_books WHERE run_id=? AND position>=? ORDER BY position LIMIT ?",
            (run_id, position, size),
        )
        return [json.loads(row["book"]) for row in rows]

    @staticmethod
    async def enqueue(server: str, library_id: int, owner_id: int, mode: str, requested_by: str) -> str:
        async with get_connection() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            async with conn.execute(
                "SELECT id, state FROM metadata_runs WHERE server=? AND library_id=? AND mode=? AND state IN ('queued','running') AND cancel_requested=0 ORDER BY created_at LIMIT 1",
                (server, library_id, mode),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                if requested_by != "auto":
                    if existing["state"] == "running":
                        await conn.execute("UPDATE metadata_runs SET followup=1,requested_by='manual' WHERE id=?", (existing["id"],))
                    else:
                        await conn.execute("UPDATE metadata_runs SET available_at=0,requested_by='manual' WHERE id=?", (existing["id"],))
                await conn.commit()
                return str(existing["id"])
            run_id, now = str(uuid4()), time.time()
            await conn.execute(
                "INSERT INTO metadata_runs(id,server,library_id,owner_id,mode,requested_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (run_id, server, library_id, owner_id, mode, requested_by, now, now),
            )
            await conn.commit()
            return run_id

    @staticmethod
    async def item(server: str, library_id: int, book_id: int) -> dict[str, Any]:
        rows = await MetadataAutomationStore.query(
            "SELECT * FROM metadata_items WHERE server=? AND library_id=? AND book_id=?", (server, library_id, book_id),
        )
        if not rows:
            return {"baseline": {}, "intent": {}, "observation": {}, "result": {}}
        row = rows[0]
        for key in ("baseline", "intent", "observation", "result"):
            row[key] = json.loads(row[key])
        return row

    @staticmethod
    async def save_item(server: str, library_id: int, book_id: int, file_id: int, item: dict[str, Any]) -> None:
        key = (server, library_id, book_id)
        baseline, intent, observation, result = (
            json.dumps(item.get(field, {}), ensure_ascii=False)
            for field in ("baseline", "intent", "observation", "result")
        )
        now = time.time()
        async with get_connection() as conn:
            await conn.execute(
                """INSERT INTO metadata_events(server,library_id,book_id,result,created_at)
                   SELECT ?,?,?,?,? WHERE NOT EXISTS (
                       SELECT 1 FROM metadata_items WHERE server=? AND library_id=? AND book_id=? AND result=?
                   )""",
                (*key, result, now, *key, result),
            )
            await conn.execute(
                """INSERT INTO metadata_items(server,library_id,book_id,file_id,baseline,intent,observation,result,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(server,library_id,book_id) DO UPDATE SET
                   file_id=excluded.file_id,baseline=excluded.baseline,intent=excluded.intent,
                   observation=excluded.observation,result=excluded.result,updated_at=excluded.updated_at
                   WHERE metadata_items.file_id != excluded.file_id OR metadata_items.baseline != excluded.baseline
                      OR metadata_items.intent != excluded.intent OR metadata_items.observation != excluded.observation
                      OR metadata_items.result != excluded.result""",
                (*key, file_id, baseline, intent, observation, result, now),
            )
            await conn.commit()
