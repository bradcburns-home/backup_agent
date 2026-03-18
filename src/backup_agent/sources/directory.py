"""Directory and file backup sources — tar or copy of bind mounts and files."""

from __future__ import annotations

import os
import shutil
import time

from backup_agent.sources.base import BackupSource, DumpResult


class DirectorySource(BackupSource):
    """Backup a host directory as a tar archive."""

    def __init__(self, name: str, staging_dir: str, source_path: str, excludes: list[str] | None = None):
        super().__init__(name, staging_dir)
        self.source_path = source_path
        self.excludes = excludes or []

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, f"{self.name}.tar.gz")
        self.logger.info("Starting directory backup: %s", self.source_path)
        start = time.monotonic()

        try:
            if not os.path.isdir(self.source_path):
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=time.monotonic() - start,
                    error=f"Source directory does not exist: {self.source_path}",
                )

            cmd = ["tar", "czf", output_path, "-C", os.path.dirname(self.source_path), os.path.basename(self.source_path)]
            for exc in self.excludes:
                cmd.insert(2, f"--exclude={exc}")

            proc = await self._run_command(cmd, timeout=600)
            duration = time.monotonic() - start

            if proc.returncode != 0:
                self.logger.error("tar failed (rc=%d): %s", proc.returncode, proc.stderr.strip())
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=duration,
                    error=proc.stderr.strip(),
                )

            size = os.path.getsize(output_path)
            self.logger.info(
                "Directory backup complete: %s → %.1f MB in %.1fs",
                self.source_path, size / 1_048_576, duration,
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
            self.logger.error("Directory backup failed: %s", e, exc_info=True)
            return DumpResult(
                source_name=self.name,
                success=False,
                duration_seconds=duration,
                error=str(e),
            )


class FileSource(BackupSource):
    """Backup a single file by copying it."""

    def __init__(self, name: str, staging_dir: str, source_path: str):
        super().__init__(name, staging_dir)
        self.source_path = source_path

    async def dump(self) -> DumpResult:
        filename = os.path.basename(self.source_path)
        output_path = os.path.join(self.staging_dir, f"{self.name}_{filename}")
        self.logger.info("Starting file backup: %s", self.source_path)
        start = time.monotonic()

        try:
            if not os.path.isfile(self.source_path):
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=time.monotonic() - start,
                    error=f"Source file does not exist: {self.source_path}",
                )

            import asyncio
            await asyncio.to_thread(shutil.copy2, self.source_path, output_path)
            duration = time.monotonic() - start
            size = os.path.getsize(output_path)

            self.logger.info(
                "File backup complete: %s → %.1f KB in %.1fs",
                self.source_path, size / 1024, duration,
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
            self.logger.error("File backup failed: %s", e, exc_info=True)
            return DumpResult(
                source_name=self.name,
                success=False,
                duration_seconds=duration,
                error=str(e),
            )
