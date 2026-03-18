"""FastMCP tool definitions for the backup agent."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Annotated

from pydantic import Field

if TYPE_CHECKING:
    from backup_agent.app import BackupApp

logger = logging.getLogger(__name__)


def register_tools(app: BackupApp) -> None:
    """Register all MCP tools on the FastMCP instance."""
    mcp = app.mcp

    @mcp.tool()
    async def get_backup_status() -> str:
        """Quick health check on the backup system. Shows overall status, last run
        result, per-source pass/fail, and next scheduled run time."""
        try:
            latest = await app.status.get_latest_run()
            health = await app.get_health_data()
            return json.dumps({
                "health": health,
                "latest_run": latest,
            }, default=str)
        except Exception as e:
            return _error_response("Failed to get backup status", e)

    @mcp.tool()
    async def get_backup_history(
        limit: Annotated[int, Field(description="Max runs to return (default 20).")] = 20,
        offset: Annotated[int, Field(description="Skip first N runs for pagination.")] = 0,
    ) -> str:
        """Recent backup runs with duration, size, and per-source pass/fail.
        Use when investigating patterns or checking if a source has been flaky."""
        try:
            runs = await app.status.get_run_history(limit=limit, offset=offset)
            total = await app.status.get_run_count()
            return json.dumps({
                "runs": runs,
                "total": total,
                "has_more": offset + limit < total,
            }, default=str)
        except Exception as e:
            return _error_response("Failed to get backup history", e)

    @mcp.tool()
    async def trigger_backup() -> str:
        """Start a backup cycle without waiting for the schedule. Returns immediately;
        check get_backup_status for results."""
        try:
            if app.orchestrator.is_running:
                return json.dumps({
                    "status": "already_running",
                    "message": "A backup is already in progress. Check get_backup_status for results.",
                })
            import asyncio
            asyncio.create_task(app.orchestrator.run_backup(triggered_by="manual"))
            return json.dumps({
                "status": "started",
                "message": "Backup cycle started. Use get_backup_status to check progress.",
            })
        except Exception as e:
            return _error_response("Failed to trigger backup", e)

    @mcp.tool()
    async def get_source_config() -> str:
        """List all configured backup sources and whether they are enabled.
        Use when you want to see what's being backed up."""
        try:
            from backup_agent.config import get_settings
            s = get_settings()
            sources = {
                "mongodb": s.source_mongodb,
                "npm_sqlite": s.source_npm_sqlite,
                "gateway_sqlite": s.source_gateway_sqlite,
                "chromadb": s.source_chromadb,
                "postgres": s.source_postgres,
                "portainer": s.source_portainer,
                "ha_config": s.source_ha_config,
                "mcp_identities": s.source_mcp_identities,
                "maintenance_tasks": s.source_maintenance_tasks,
                "mosquitto": s.source_mosquitto,
                "loki_noise": s.source_loki_noise,
                "letsencrypt": s.source_letsencrypt,
            }
            return json.dumps({
                "sources": sources,
                "interval_seconds": s.backup_interval_seconds,
                "nfs_repo": s.restic_nfs_repository,
                "gcs_repo": s.restic_gcs_repository,
            })
        except Exception as e:
            return _error_response("Failed to get source config", e)

    @mcp.tool()
    async def check_integrity() -> str:
        """Run restic check on the NFS repository to verify data integrity.
        Use when you want to confirm the backup repository hasn't corrupted."""
        try:
            from backup_agent.verification import run_integrity_check
            results = await run_integrity_check(app.orchestrator.nfs_client)
            return json.dumps(results, default=str)
        except Exception as e:
            return _error_response("Failed to run integrity check", e)

    @mcp.tool()
    async def run_restore_test() -> str:
        """Restore the latest snapshot and validate every source. Use when you
        want to prove the latest backup is actually restorable right now."""
        try:
            from backup_agent.verification import run_restore_test as _run_test
            import asyncio
            asyncio.create_task(
                _run_test(app.orchestrator.nfs_client, app.status, triggered_by="manual")
            )
            return json.dumps({
                "status": "started",
                "message": "Restore test started. Use get_restore_test_history to check results.",
            })
        except Exception as e:
            return _error_response("Failed to trigger restore test", e)

    @mcp.tool()
    async def get_restore_test_history(
        limit: Annotated[int, Field(description="Max results to return (default 20).")] = 20,
        offset: Annotated[int, Field(description="Skip first N results for pagination.")] = 0,
    ) -> str:
        """Recent restore test results with per-source pass/fail detail.
        Use when auditing whether restore tests have been passing."""
        try:
            tests = await app.status.get_restore_test_history(limit=limit, offset=offset)
            return json.dumps({
                "tests": tests,
                "has_more": len(tests) == limit,
            }, default=str)
        except Exception as e:
            return _error_response("Failed to get restore test history", e)


def _error_response(human_message: str, exc: Exception) -> str:
    """Three-layer error response per DESIGN_PRINCIPLES."""
    import traceback
    return json.dumps({
        "status": "error",
        "message": human_message,
        "guidance": f"This may be a transient error. If it persists, check the backup-agent container logs.",
        "exception": type(exc).__name__,
        "detail": str(exc),
        "traceback": traceback.format_exc(),
    })
