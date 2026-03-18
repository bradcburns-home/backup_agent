"""SQLite backup source — sqlite3 .backup via docker exec."""

from __future__ import annotations

import os
import time

from backup_agent.sources.base import BackupSource, DumpResult


class SQLiteSource(BackupSource):
    """Backup a SQLite database inside a running container using the backup API."""

    def __init__(
        self,
        name: str,
        staging_dir: str,
        container: str,
        db_path_in_container: str,
        host_data_dir: str | None = None,
    ):
        super().__init__(name, staging_dir)
        self.container = container
        self.db_path_in_container = db_path_in_container
        self.host_data_dir = host_data_dir

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, f"{self.name}.sqlite")
        backup_path_in_container = f"{self.db_path_in_container}.backup"
        self.logger.info("Starting SQLite backup from %s:%s", self.container, self.db_path_in_container)
        start = time.monotonic()

        try:
            proc = await self._run_command([
                "docker", "exec", self.container,
                "sqlite3", self.db_path_in_container,
                f".backup '{backup_path_in_container}'",
            ])

            duration = time.monotonic() - start

            if proc.returncode != 0:
                self.logger.error(
                    "sqlite3 .backup failed (rc=%d): %s",
                    proc.returncode, proc.stderr.strip(),
                )
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=duration,
                    error=proc.stderr.strip(),
                )

            cp_proc = await self._run_command([
                "docker", "cp",
                f"{self.container}:{backup_path_in_container}",
                output_path,
            ])

            if cp_proc.returncode != 0:
                self.logger.error("docker cp failed: %s", cp_proc.stderr.strip())
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=time.monotonic() - start,
                    error=cp_proc.stderr.strip(),
                )

            await self._run_command([
                "docker", "exec", self.container, "rm", "-f", backup_path_in_container,
            ])

            size = os.path.getsize(output_path)
            self.logger.info(
                "SQLite backup complete: %.1f KB in %.1fs",
                size / 1024, time.monotonic() - start,
            )
            return DumpResult(
                source_name=self.name,
                success=True,
                output_path=output_path,
                size_bytes=size,
                duration_seconds=time.monotonic() - start,
            )
        except Exception as e:
            self.logger.error("SQLite backup failed: %s", e, exc_info=True)
            return DumpResult(
                source_name=self.name,
                success=False,
                duration_seconds=time.monotonic() - start,
                error=str(e),
            )

    async def verify_dump(self, result: DumpResult) -> bool:
        if not await super().verify_dump(result):
            return False

        proc = await self._run_command([
            "sqlite3", result.output_path, "PRAGMA integrity_check;",
        ])
        if proc.returncode != 0 or "ok" not in proc.stdout.lower():
            self.logger.error("SQLite integrity check failed: %s", proc.stdout.strip())
            return False
        return True
