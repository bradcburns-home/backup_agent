"""Docker volume backup source — tar via ephemeral alpine container, piped to stdout."""

from __future__ import annotations

import os
import time

from backup_agent.sources.base import BackupSource, DumpResult


class VolumeSource(BackupSource):
    """Backup a Docker named volume by tarring its contents via an ephemeral container.

    Pipes tar output to stdout (binary mode) to avoid needing to mount
    the staging directory — which wouldn't work because the staging path
    is container-internal, not a host path.
    """

    def __init__(self, name: str, staging_dir: str, volume_name: str):
        super().__init__(name, staging_dir)
        self.volume_name = volume_name

    async def dump(self) -> DumpResult:
        output_path = os.path.join(self.staging_dir, f"{self.name}.tar.gz")
        self.logger.info("Starting volume backup: %s", self.volume_name)
        start = time.monotonic()

        try:
            container_name = f"backup-vol-{self.name}"
            proc = await self._run_command(
                [
                    "docker", "run", "--rm",
                    "--name", container_name,
                    "--log-driver", "none",
                    "-v", f"{self.volume_name}:/data:ro",
                    "alpine",
                    "tar", "czf", "-", "-C", "/data", ".",
                ],
                timeout=600,
                binary=True,
            )

            duration = time.monotonic() - start

            if proc.returncode != 0:
                stderr_text = proc.stderr.decode("utf-8", errors="replace") if isinstance(proc.stderr, bytes) else proc.stderr
                self.logger.error("Volume tar failed (rc=%d): %s", proc.returncode, stderr_text.strip())
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
