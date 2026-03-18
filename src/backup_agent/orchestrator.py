"""Backup orchestrator — runs the dump → ship → verify → report pipeline."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from backup_agent.config import Settings, get_settings
from backup_agent.restic import ResticClient
from backup_agent.sources.base import BackupSource, DumpResult
from backup_agent.sources.directory import DirectorySource, FileSource
from backup_agent.sources.mongodb import MongoDBSource
from backup_agent.sources.postgres import PostgresSource
from backup_agent.sources.sqlite import SQLiteSource
from backup_agent.sources.volume import VolumeSource
from backup_agent.status import StatusStore

logger = logging.getLogger(__name__)


def _build_sources(settings: Settings) -> list[BackupSource]:
    """Build the list of enabled backup sources."""
    staging = settings.backup_staging_dir
    sources: list[BackupSource] = []

    if settings.source_mongodb:
        sources.append(MongoDBSource(staging))

    if settings.source_npm_sqlite:
        sources.append(SQLiteSource(
            name="npm_sqlite",
            staging_dir=staging,
            container="nginx-proxy-manager",
            db_path_in_container="/data/database.sqlite",
        ))

    if settings.source_gateway_sqlite:
        sources.append(VolumeSource("gateway_sqlite", staging, "mcp-gateway_gateway-data"))

    if settings.source_chromadb:
        sources.append(VolumeSource("chromadb", staging, "docs_api_chromadb_data"))

    if settings.source_postgres:
        sources.append(PostgresSource(staging))

    if settings.source_portainer:
        sources.append(DirectorySource("portainer", staging, "/srv/portainer/portainer_data"))

    if settings.source_ha_config:
        sources.append(DirectorySource(
            "ha_config", staging, "/srv/homeassistant/config",
            excludes=["home-assistant_v2.db", "home-assistant_v2.db-shm", "home-assistant_v2.db-wal"],
        ))

    if settings.source_mcp_identities:
        sources.append(DirectorySource(
            "mcp_identities", staging,
            "/srv/shared/mcp_endpoints/issue_manager/identities",
        ))

    if settings.source_maintenance_tasks:
        sources.append(FileSource(
            "maintenance_tasks", staging,
            "/srv/shared/mcp_endpoints/home_maintenance/config/tasks.yaml",
        ))

    if settings.source_mosquitto:
        sources.append(DirectorySource("mosquitto", staging, "/srv/shared/mosquitto/data"))

    if settings.source_loki_noise:
        sources.append(VolumeSource("loki_noise", staging, "loki_logs_loki_logs_data"))

    if settings.source_letsencrypt:
        sources.append(DirectorySource(
            "letsencrypt", staging, "/srv/nginx-proxy-manager/letsencrypt",
        ))

    return sources


class BackupOrchestrator:
    def __init__(self, status_store: StatusStore, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.status = status_store
        self._running = False

        self.nfs_client = ResticClient(
            repository=self.settings.restic_nfs_repository,
            password_file=self.settings.restic_password_file,
        )
        self.gcs_client = ResticClient(
            repository=self.settings.restic_gcs_repository,
            password_file=self.settings.restic_password_file,
            google_credentials=self.settings.google_application_credentials,
        )

    @property
    def is_running(self) -> bool:
        return self._running

    async def run_backup(self, triggered_by: str = "schedule") -> dict[str, Any]:
        """Execute a full backup cycle: dump → ship → verify → report."""
        if self._running:
            logger.warning("Backup already in progress, skipping")
            return {"status": "skipped", "reason": "already_running"}

        self._running = True
        run_id = await self.status.record_run_start(triggered_by)
        start = time.monotonic()
        sources = _build_sources(self.settings)
        source_results: dict[str, Any] = {}
        dump_results: list[DumpResult] = []
        total_size = 0

        try:
            os.makedirs(self.settings.backup_staging_dir, exist_ok=True)

            for source in sources:
                logger.info("Dumping source: %s", source.name)
                result = await source.dump()
                dump_results.append(result)

                if result.success:
                    verified = await source.verify_dump(result)
                    source_results[source.name] = {
                        "status": "success" if verified else "verify_failed",
                        "size_bytes": result.size_bytes,
                        "duration_seconds": round(result.duration_seconds, 2),
                    }
                    if not verified:
                        logger.warning("Verification failed for %s", source.name)
                    total_size += result.size_bytes
                else:
                    source_results[source.name] = {
                        "status": "failed",
                        "error": result.error,
                        "duration_seconds": round(result.duration_seconds, 2),
                    }
                    logger.warning("Source %s failed: %s", source.name, result.error)

            successful_dumps = [r for r in dump_results if r.success and r.output_path]
            if not successful_dumps:
                logger.error("All source dumps failed — nothing to ship")
                duration = time.monotonic() - start
                await self.status.record_run_finish(
                    run_id, "failure", duration, source_results, total_size_bytes=0,
                )
                return {"status": "failure", "sources": source_results}

            dump_paths = [r.output_path for r in successful_dumps]

            nfs_snapshot_id = await self._ship_to_nfs(dump_paths)
            gcs_snapshot_id = await self._ship_to_gcs(dump_paths)

            for dr in dump_results:
                for source in sources:
                    if source.name == dr.source_name:
                        await source.cleanup(dr)

            if nfs_snapshot_id:
                await self._apply_nfs_retention()

            failed_sources = [name for name, info in source_results.items() if info["status"] != "success"]
            if not nfs_snapshot_id and not gcs_snapshot_id:
                overall = "failure"
            elif failed_sources:
                overall = "partial_failure"
            elif not gcs_snapshot_id:
                overall = "partial_failure"
            else:
                overall = "success"

            duration = time.monotonic() - start
            await self.status.record_run_finish(
                run_id, overall, duration, source_results,
                nfs_snapshot_id=nfs_snapshot_id,
                gcs_snapshot_id=gcs_snapshot_id,
                total_size_bytes=total_size,
            )

            logger.info(
                "Backup run complete: %s (%.1fs, %d sources, %d failed, NFS=%s, GCS=%s)",
                overall, duration, len(sources), len(failed_sources),
                "ok" if nfs_snapshot_id else "FAILED",
                "ok" if gcs_snapshot_id else "FAILED",
            )

            return {
                "status": overall,
                "duration_seconds": round(duration, 2),
                "sources": source_results,
                "nfs_snapshot": nfs_snapshot_id,
                "gcs_snapshot": gcs_snapshot_id,
                "total_size_bytes": total_size,
            }
        except Exception:
            duration = time.monotonic() - start
            logger.exception("Backup run failed with unhandled exception")
            await self.status.record_run_finish(
                run_id, "failure", duration, source_results, total_size_bytes=0,
            )
            return {"status": "failure", "sources": source_results}
        finally:
            self._running = False

    async def _ship_to_nfs(self, paths: list[str]) -> str | None:
        """Ship dump files to the NFS restic repository."""
        logger.info("Shipping to NFS: %s", self.settings.restic_nfs_repository)
        try:
            result = await self.nfs_client.backup(paths, tags=["backup-agent"])
            if result.success:
                snapshot_id = None
                if result.json_output and isinstance(result.json_output, dict):
                    snapshot_id = result.json_output.get("snapshot_id")
                logger.info("NFS backup succeeded (snapshot=%s)", snapshot_id)
                return snapshot_id
            logger.error("NFS backup failed: %s", result.stderr.strip())
            return None
        except Exception:
            logger.exception("NFS backup failed with exception")
            return None

    async def _ship_to_gcs(self, paths: list[str]) -> str | None:
        """Ship dump files to the GCS restic repository."""
        logger.info("Shipping to GCS: %s", self.settings.restic_gcs_repository)
        try:
            result = await self.gcs_client.backup(paths, tags=["backup-agent"])
            if result.success:
                snapshot_id = None
                if result.json_output and isinstance(result.json_output, dict):
                    snapshot_id = result.json_output.get("snapshot_id")
                logger.info("GCS backup succeeded (snapshot=%s)", snapshot_id)
                return snapshot_id
            logger.warning("GCS backup failed (offsite copy missed): %s", result.stderr.strip())
            return None
        except Exception:
            logger.exception("GCS backup failed with exception")
            return None

    async def _apply_nfs_retention(self) -> None:
        """Apply retention policy to the NFS repository."""
        s = self.settings
        logger.info("Applying NFS retention: %dh/%dd/%dw/%dm",
                     s.nfs_retention_hourly, s.nfs_retention_daily,
                     s.nfs_retention_weekly, s.nfs_retention_monthly)
        try:
            result = await self.nfs_client.forget(
                keep_hourly=s.nfs_retention_hourly,
                keep_daily=s.nfs_retention_daily,
                keep_weekly=s.nfs_retention_weekly,
                keep_monthly=s.nfs_retention_monthly,
            )
            if not result.success:
                logger.warning("NFS retention failed: %s", result.stderr.strip())
        except Exception:
            logger.exception("NFS retention failed with exception")
