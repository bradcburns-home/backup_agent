"""SQLite backup source — uses local sqlite3 binary on host-mounted database files."""

from __future__ import annotations

import os
import time

from backup_agent.sources.base import BackupSource, DumpResult


class SQLiteSource(BackupSource):
    """Backup a SQLite database accessible via a host-mounted path.

    Since /srv is mounted into the backup agent container, we can access
    SQLite files directly and use the container's own sqlite3 to perform
    an online backup (safe even if the database is in use).
    """

    def __init__(
        self,
        name: str,
        staging_dir: str,
        host_db_path: str,
    ):
        super().__init__(name, staging_dir)
        self.host_db_path = host_db_path

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, f"{self.name}.sqlite")
        self.logger.info("Starting SQLite backup: %s", self.host_db_path)
        start = time.monotonic()

        try:
            if not os.path.exists(self.host_db_path):
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=time.monotonic() - start,
                    error=f"Database file not found: {self.host_db_path}",
                )

            proc = await self._run_command([
                "sqlite3", self.host_db_path,
                f".backup '{output_path}'",
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

            size = os.path.getsize(output_path)
            self.logger.info(
                "SQLite backup complete: %.1f KB in %.1fs",
                size / 1024, duration,
            )
            return DumpResult(
                source_name=self.name,
                success=True,
                output_path=output_path,
                size_bytes=size,
                duration_seconds=duration,
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
