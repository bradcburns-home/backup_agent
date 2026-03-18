"""Restic subprocess wrapper.

All subprocess calls run via asyncio.to_thread() to avoid blocking the
event loop. The wrapper handles password file, --no-lock, and JSON output
parsing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ResticResult:
    returncode: int
    stdout: str
    stderr: str
    json_output: Any = None
    success: bool = field(init=False)

    def __post_init__(self) -> None:
        self.success = self.returncode == 0


class ResticClient:
    """Thin wrapper around the restic CLI."""

    def __init__(
        self,
        repository: str,
        password_file: str,
        google_credentials: str | None = None,
    ):
        self.repository = repository
        self.password_file = password_file
        self.google_credentials = google_credentials

    def _base_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["RESTIC_REPOSITORY"] = self.repository
        env["RESTIC_PASSWORD_FILE"] = self.password_file
        if self.google_credentials and self.repository.startswith("gs:"):
            env["GOOGLE_APPLICATION_CREDENTIALS"] = self.google_credentials
        return env

    def _run_sync(self, args: list[str], json_output: bool = False) -> ResticResult:
        cmd = ["restic", "--no-lock", *args]
        if json_output:
            cmd.append("--json")

        logger.debug("Running: %s", " ".join(cmd))
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=self._base_env(),
            timeout=1800,
        )

        parsed_json = None
        if json_output and proc.returncode == 0 and proc.stdout.strip():
            try:
                parsed_json = json.loads(proc.stdout)
            except json.JSONDecodeError:
                logger.warning("Failed to parse restic JSON output")

        result = ResticResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            json_output=parsed_json,
        )

        if not result.success:
            logger.error(
                "restic %s failed (rc=%d): %s",
                args[0] if args else "?",
                proc.returncode,
                proc.stderr.strip(),
            )

        return result

    async def init(self) -> ResticResult:
        """Initialize the restic repository."""
        return await asyncio.to_thread(self._run_sync, ["init"])

    async def backup(self, paths: list[str], tags: list[str] | None = None) -> ResticResult:
        """Run restic backup on the given paths."""
        args = ["backup", *paths]
        if tags:
            for tag in tags:
                args.extend(["--tag", tag])
        return await asyncio.to_thread(self._run_sync, args, True)

    async def snapshots(self, latest: int | None = None) -> ResticResult:
        """List snapshots."""
        args = ["snapshots"]
        if latest:
            args.extend(["--latest", str(latest)])
        return await asyncio.to_thread(self._run_sync, args, True)

    async def forget(
        self,
        keep_hourly: int = 24,
        keep_daily: int = 7,
        keep_weekly: int = 4,
        keep_monthly: int = 12,
    ) -> ResticResult:
        """Run restic forget with retention policy."""
        args = [
            "forget",
            "--prune",
            "--keep-hourly", str(keep_hourly),
            "--keep-daily", str(keep_daily),
            "--keep-weekly", str(keep_weekly),
            "--keep-monthly", str(keep_monthly),
        ]
        return await asyncio.to_thread(self._run_sync, args, True)

    async def check(self, read_data_subset: str | None = None) -> ResticResult:
        """Run restic check for repository integrity."""
        args = ["check"]
        if read_data_subset:
            args.extend(["--read-data-subset", read_data_subset])
        return await asyncio.to_thread(self._run_sync, args)

    async def restore(self, snapshot_id: str, target: str) -> ResticResult:
        """Restore a snapshot to the target directory."""
        args = ["restore", snapshot_id, "--target", target]
        return await asyncio.to_thread(self._run_sync, args)

    async def stats(self) -> ResticResult:
        """Get repository statistics."""
        return await asyncio.to_thread(self._run_sync, ["stats"], True)
