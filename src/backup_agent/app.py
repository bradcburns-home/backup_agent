"""Backup Agent application — FastMCP server + scheduler + orchestrator."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from backup_agent.config import Settings, get_settings
from backup_agent.mcp_tools import register_tools
from backup_agent.orchestrator import BackupOrchestrator
from backup_agent.status import StatusStore
from backup_agent.verification import run_integrity_check, run_restore_test

logger = logging.getLogger(__name__)


class BackupApp:
    """Container for the backup agent's shared state."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.status = StatusStore(self.settings.status_db_path)
        self.orchestrator = BackupOrchestrator(self.status, self.settings)

        self.mcp = FastMCP(
            "Backup Agent",
            host="0.0.0.0",
            port=self.settings.port,
            instructions=(
                "Backup agent for Burns Lab infrastructure. Manages hourly backups of "
                "databases (MongoDB, SQLite, PostgreSQL, ChromaDB), Docker volumes, and "
                "configuration files to two restic repositories (NFS local + GCS offsite).\n\n"
                "READ tools (no side effects):\n"
                "- get_backup_status: current health and last/next run times\n"
                "- get_backup_history: recent runs with per-source results\n"
                "- get_source_config: what's being backed up\n"
                "- get_restore_test_history: recent restore test results\n\n"
                "WRITE tools (trigger operations):\n"
                "- trigger_backup: start a backup cycle (async)\n"
                "- run_restore_test: start a restore test (async)\n"
                "- check_integrity: run restic check (async)\n\n"
                "Call write tools one at a time — never in parallel.\n\n"
                "TIMESTAMPS: All timestamps MUST include a timezone offset "
                "(e.g. 2026-03-16T09:00:00-04:00). All returned timestamps include "
                "timezone offsets (America/Detroit) — echo them back as-is."
            ),
            stateless_http=True,
        )

        register_tools(self)
        self._register_health_route()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._restore_test_task: asyncio.Task[None] | None = None
        self._integrity_task: asyncio.Task[None] | None = None

    def _register_health_route(self) -> None:
        @self.mcp.custom_route("/health", methods=["GET"])
        async def health(request: Request) -> JSONResponse:
            data = await self.get_health_data()
            status_code = 200 if data["status"] == "healthy" else 503
            return JSONResponse(data, status_code=status_code)

    async def get_health_data(self) -> dict[str, Any]:
        latest = await self.status.get_latest_run()

        nfs_reachable = os.path.isdir(self.settings.restic_nfs_repository) or os.path.isdir(
            os.path.dirname(self.settings.restic_nfs_repository)
        )

        if latest is None:
            status = "degraded"
            last_result = "no_runs"
        elif latest.get("result") == "success":
            status = "healthy" if nfs_reachable else "degraded"
            last_result = "success"
        elif latest.get("result") == "partial_failure":
            status = "degraded"
            last_result = "partial_failure"
        else:
            status = "unhealthy"
            last_result = latest.get("result", "unknown")

        return {
            "status": status,
            "service": "backup-agent",
            "last_run": latest.get("started_at") if latest else None,
            "last_run_result": last_result,
            "nfs_reachable": nfs_reachable,
            "sources_failed": [
                name for name, info in (latest.get("sources", {}) if latest else {}).items()
                if isinstance(info, dict) and info.get("status") != "success"
            ],
        }

    async def start_scheduler(self) -> None:
        """Start the background scheduler tasks."""
        self._scheduler_task = asyncio.create_task(self._backup_loop())
        self._restore_test_task = asyncio.create_task(self._restore_test_loop())
        self._integrity_task = asyncio.create_task(self._integrity_loop())
        logger.info(
            "Scheduler started: backup every %ds, restore test daily at %02d:00, "
            "integrity check on %s",
            self.settings.backup_interval_seconds,
            self.settings.restore_test_hour,
            self.settings.integrity_check_day,
        )

    async def stop_scheduler(self) -> None:
        for task in (self._scheduler_task, self._restore_test_task, self._integrity_task):
            if task and not task.done():
                task.cancel()

    async def _backup_loop(self) -> None:
        await asyncio.sleep(5)
        while True:
            try:
                await self.orchestrator.run_backup()
            except Exception:
                logger.exception("Backup loop iteration failed")
            await asyncio.sleep(self.settings.backup_interval_seconds)

    async def _restore_test_loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                now = datetime.now(timezone.utc)
                if now.hour == self.settings.restore_test_hour:
                    logger.info("Starting daily restore test")
                    await run_restore_test(
                        self.orchestrator.nfs_client, self.status, triggered_by="schedule",
                    )
                    await asyncio.sleep(3600)
                else:
                    await asyncio.sleep(300)
            except Exception:
                logger.exception("Restore test loop failed")
                await asyncio.sleep(300)

    async def _integrity_loop(self) -> None:
        await asyncio.sleep(60)
        while True:
            try:
                now = datetime.now(timezone.utc)
                day_name = now.strftime("%A")
                if day_name == self.settings.integrity_check_day and now.hour == 3:
                    week_of_month = (now.day - 1) // 7 + 1
                    subset = f"{week_of_month}/4"
                    logger.info("Starting weekly integrity check (subset=%s)", subset)
                    await run_integrity_check(
                        nfs_client=self.orchestrator.nfs_client,
                        gcs_client=self.orchestrator.gcs_client,
                        subset=subset,
                    )
                    await asyncio.sleep(7200)
                else:
                    await asyncio.sleep(600)
            except Exception:
                logger.exception("Integrity check loop failed")
                await asyncio.sleep(600)


_app_instance: BackupApp | None = None


def get_app() -> BackupApp:
    global _app_instance
    if _app_instance is None:
        _app_instance = BackupApp()
    return _app_instance


def create_asgi_app() -> Any:
    """Create the ASGI application with lifespan management.

    Wraps the inner MCP app's lifespan so the task group is properly
    initialized, while also starting/stopping our scheduler.
    """
    backup_app = get_app()

    inner_app = backup_app.mcp.streamable_http_app()

    class _LifespanWrapper:
        def __init__(self, asgi_app: Any):
            self._app = asgi_app

        async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
            if scope["type"] == "lifespan":
                async def wrapped_receive() -> Any:
                    return await receive()

                async def wrapped_send(message: Any) -> None:
                    if message["type"] == "lifespan.startup.complete":
                        await backup_app.start_scheduler()
                    elif message["type"] == "lifespan.shutdown.complete":
                        await backup_app.stop_scheduler()
                    await send(message)

                await self._app(scope, wrapped_receive, wrapped_send)
            else:
                await self._app(scope, receive, send)

    return _LifespanWrapper(inner_app)
