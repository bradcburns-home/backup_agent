"""MongoDB backup source — mongodump via docker exec."""

from __future__ import annotations

import os
import time

from backup_agent.sources.base import BackupSource, DumpResult


class MongoDBSource(BackupSource):
    def __init__(
        self,
        staging_dir: str,
        container: str = "librechat-mongodb-prod",
        database: str = "librechat",
    ):
        super().__init__("mongodb", staging_dir)
        self.container = container
        self.database = database

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, "mongodb_librechat.archive")
        self.logger.info("Starting MongoDB dump from %s (db=%s)", self.container, self.database)
        start = time.monotonic()

        try:
            proc = await self._run_command(
                [
                    "docker", "exec", self.container,
                    "mongodump", "--db", self.database, "--archive",
                ],
                binary=True,
            )

            duration = time.monotonic() - start

            if proc.returncode != 0:
                stderr_text = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr
                self.logger.error("mongodump failed (rc=%d): %s", proc.returncode, stderr_text.strip())
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=duration,
                    error=stderr_text.strip(),
                )

            with open(output_path, "wb") as f:
                f.write(proc.stdout)

            size = os.path.getsize(output_path)
            self.logger.info(
                "MongoDB dump complete: %.1f MB in %.1fs",
                size / 1_048_576,
                duration,
            )
            return DumpResult(
                source_name=self.name,
                success=True,
                output_path=output_path,
                size_bytes=size,
                duration_seconds=duration,
            )
        except Exception as e:
            duration = time.monotonic() - start
            self.logger.error("MongoDB dump failed: %s", e, exc_info=True)
            return DumpResult(
                source_name=self.name,
                success=False,
                duration_seconds=duration,
                error=str(e),
            )

    async def verify_dump(self, result: DumpResult) -> bool:
        if not await super().verify_dump(result):
            return False

        def _check_header() -> bool:
            with open(result.output_path, "rb") as f:
                header = f.read(4)
            if len(header) < 4:
                self.logger.error("MongoDB archive too small to contain valid header")
                return False
            return True

        import asyncio
        return await asyncio.to_thread(_check_header)
