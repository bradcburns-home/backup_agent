"""SQLite-backed status store for backup run history and restore test results.

All writes go through asyncio.to_thread() to avoid blocking the event loop.
Single-writer safety is guaranteed by uvicorn workers=1.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backup_agent.config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backup_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    duration_seconds REAL,
    sources_json TEXT,
    nfs_snapshot_id TEXT,
    gcs_snapshot_id TEXT,
    total_size_bytes INTEGER,
    triggered_by TEXT DEFAULT 'schedule'
);

CREATE TABLE IF NOT EXISTS restore_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result TEXT,
    sources_json TEXT,
    triggered_by TEXT DEFAULT 'schedule'
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON backup_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_tests_started ON restore_tests(started_at);
"""


class StatusStore:
    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or get_settings().status_db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self._db_path)
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def record_run_start(self, triggered_by: str = "schedule") -> int:
        def _insert() -> int:
            conn = self._connect()
            try:
                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "INSERT INTO backup_runs (started_at, triggered_by) VALUES (?, ?)",
                    (now, triggered_by),
                )
                conn.commit()
                return cur.lastrowid  # type: ignore[return-value]
            finally:
                conn.close()

        return await asyncio.to_thread(_insert)

    async def record_run_finish(
        self,
        run_id: int,
        result: str,
        duration_seconds: float,
        sources: dict[str, Any],
        nfs_snapshot_id: str | None = None,
        gcs_snapshot_id: str | None = None,
        total_size_bytes: int = 0,
    ) -> None:
        def _update() -> None:
            conn = self._connect()
            try:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """UPDATE backup_runs SET
                        finished_at=?, result=?, duration_seconds=?,
                        sources_json=?, nfs_snapshot_id=?, gcs_snapshot_id=?,
                        total_size_bytes=?
                    WHERE id=?""",
                    (
                        now, result, duration_seconds,
                        json.dumps(sources), nfs_snapshot_id, gcs_snapshot_id,
                        total_size_bytes, run_id,
                    ),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_update)

    async def get_latest_run(self) -> dict[str, Any] | None:
        def _query() -> dict[str, Any] | None:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM backup_runs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                d = dict(row)
                if d.get("sources_json"):
                    d["sources"] = json.loads(d.pop("sources_json"))
                else:
                    d.pop("sources_json", None)
                    d["sources"] = {}
                return d
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def get_run_history(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM backup_runs ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit + 1, offset),
                ).fetchall()
                results = []
                for row in rows[:limit]:
                    d = dict(row)
                    if d.get("sources_json"):
                        d["sources"] = json.loads(d.pop("sources_json"))
                    else:
                        d.pop("sources_json", None)
                        d["sources"] = {}
                    results.append(d)
                return results
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def get_run_count(self) -> int:
        def _query() -> int:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) FROM backup_runs").fetchone()
                return row[0] if row else 0
            finally:
                conn.close()

        return await asyncio.to_thread(_query)

    async def record_restore_test_start(self, triggered_by: str = "schedule") -> int:
        def _insert() -> int:
            conn = self._connect()
            try:
                now = datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "INSERT INTO restore_tests (started_at, triggered_by) VALUES (?, ?)",
                    (now, triggered_by),
                )
                conn.commit()
                return cur.lastrowid  # type: ignore[return-value]
            finally:
                conn.close()

        return await asyncio.to_thread(_insert)

    async def record_restore_test_finish(
        self,
        test_id: int,
        result: str,
        sources: dict[str, Any],
    ) -> None:
        def _update() -> None:
            conn = self._connect()
            try:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    "UPDATE restore_tests SET finished_at=?, result=?, sources_json=? WHERE id=?",
                    (now, result, json.dumps(sources), test_id),
                )
                conn.commit()
            finally:
                conn.close()

        await asyncio.to_thread(_update)

    async def get_restore_test_history(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        def _query() -> list[dict[str, Any]]:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM restore_tests ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit + 1, offset),
                ).fetchall()
                results = []
                for row in rows[:limit]:
                    d = dict(row)
                    if d.get("sources_json"):
                        d["sources"] = json.loads(d.pop("sources_json"))
                    else:
                        d.pop("sources_json", None)
                        d["sources"] = {}
                    results.append(d)
                return results
            finally:
                conn.close()

        return await asyncio.to_thread(_query)
