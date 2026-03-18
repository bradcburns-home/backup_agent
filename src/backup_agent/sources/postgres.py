"""PostgreSQL backup source — pg_dump via docker exec."""

from __future__ import annotations

import os
import time

from backup_agent.sources.base import BackupSource, DumpResult


class PostgresSource(BackupSource):
    """Backup a PostgreSQL database using pg_dump in custom format."""

    def __init__(
        self,
        staging_dir: str,
        container: str = "vectordb",
        database: str = "mydatabase",
        user: str = "myuser",
    ):
        super().__init__("postgres", staging_dir)
        self.container = container
        self.database = database
        self.user = user

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, "postgres_dump.Fc")
        dump_path_in_container = "/tmp/pg_dump.Fc"
        self.logger.info(
            "Starting PostgreSQL dump from %s (db=%s, user=%s)",
            self.container, self.database, self.user,
        )
        start = time.monotonic()

        try:
            proc = await self._run_command([
                "docker", "exec", self.container,
                "pg_dump", "-U", self.user, "-d", self.database,
                "-Fc", "-f", dump_path_in_container,
            ])

            if proc.returncode != 0:
                self.logger.error("pg_dump failed (rc=%d): %s", proc.returncode, proc.stderr.strip())
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=time.monotonic() - start,
                    error=proc.stderr.strip(),
                )

            cp_proc = await self._run_command([
                "docker", "cp",
                f"{self.container}:{dump_path_in_container}",
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
                "docker", "exec", self.container, "rm", "-f", dump_path_in_container,
            ])

            size = os.path.getsize(output_path)
            duration = time.monotonic() - start
            self.logger.info("PostgreSQL dump complete: %.1f MB in %.1fs", size / 1_048_576, duration)
            return DumpResult(
                source_name=self.name,
                success=True,
                output_path=output_path,
                size_bytes=size,
                duration_seconds=duration,
            )
        except Exception as e:
            self.logger.error("PostgreSQL dump failed: %s", e, exc_info=True)
            return DumpResult(
                source_name=self.name,
                success=False,
                duration_seconds=time.monotonic() - start,
                error=str(e),
            )

    async def verify_dump(self, result: DumpResult) -> bool:
        if not await super().verify_dump(result):
            return False

        proc = await self._run_command(["pg_restore", "--list", result.output_path])
        if proc.returncode != 0:
            self.logger.error("pg_restore --list failed: %s", proc.stderr.strip())
            return False
        return True
