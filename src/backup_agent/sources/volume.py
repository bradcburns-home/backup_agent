"""Docker volume backup source — tar via ephemeral alpine container."""

from __future__ import annotations

import os
import time

from backup_agent.sources.base import BackupSource, DumpResult


class VolumeSource(BackupSource):
    """Backup a Docker named volume by tarring its contents via an ephemeral container."""

    def __init__(self, name: str, staging_dir: str, volume_name: str):
        super().__init__(name, staging_dir)
        self.volume_name = volume_name

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, f"{self.name}.tar.gz")
        self.logger.info("Starting volume backup: %s", self.volume_name)
        start = time.monotonic()

        try:
            staging_abs = os.path.abspath(self.staging_dir)
            filename = f"{self.name}.tar.gz"

            proc = await self._run_command(
                [
                    "docker", "run", "--rm",
                    "-v", f"{self.volume_name}:/data:ro",
                    "-v", f"{staging_abs}:/backup",
                    "alpine",
                    "tar", "czf", f"/backup/{filename}", "-C", "/data", ".",
                ],
                timeout=600,
            )

            duration = time.monotonic() - start

            if proc.returncode != 0:
                self.logger.error("Volume tar failed (rc=%d): %s", proc.returncode, proc.stderr.strip())
                return DumpResult(
                    source_name=self.name,
                    success=False,
                    duration_seconds=duration,
                    error=proc.stderr.strip(),
                )

            size = os.path.getsize(output_path)
            self.logger.info(
                "Volume backup complete: %s → %.1f MB in %.1fs",
                self.volume_name, size / 1_048_576, duration,
            )
            return DumpResult(
                source_name=self.name,
                success=True,
                output_path=output_path,
                size_bytes=size,
                duration_seconds=duration,
            )
        except Exception as e:
            self.logger.error("Volume backup failed: %s", e, exc_info=True)
            return DumpResult(
                source_name=self.name,
                success=False,
                duration_seconds=time.monotonic() - start,
                error=str(e),
            )
