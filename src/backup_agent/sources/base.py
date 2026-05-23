"""Abstract base class for backup source handlers."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class DumpResult:
    source_name: str
    success: bool
    output_path: str = ""
    size_bytes: int = 0
    duration_seconds: float = 0.0
    error: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class BackupSource(ABC):
    """Base class for all backup source handlers.

    Subclasses must implement dump(), verify_dump(), and cleanup().
    """

    def __init__(self, name: str, staging_dir: str):
        self.name = name
        self.staging_dir = staging_dir
        self.logger = logging.getLogger(f"backup_agent.sources.{name}")

    @abstractmethod
    async def dump(self) -> DumpResult:
        """Extract data from the source to the staging directory."""

    async def verify_dump(self, result: DumpResult) -> bool:
        """Verify the dump file is valid. Default: check exists and non-zero."""
        if not result.success or not result.output_path:
            return False

        def _check() -> bool:
            path = Path(result.output_path)
            if not path.exists():
                self.logger.error("Dump file does not exist: %s", result.output_path)
                return False
            size = path.stat().st_size
            if size == 0:
                self.logger.error("Dump file is empty: %s", result.output_path)
                return False
            return True

        return await asyncio.to_thread(_check)

    async def cleanup(self, result: DumpResult) -> None:
        """Clean up the dump file after shipping."""
        if result.output_path and os.path.exists(result.output_path):
            await asyncio.to_thread(os.remove, result.output_path)

    async def _run_command(
        self, cmd: list[str], timeout: int = 300, binary: bool = False,
    ) -> subprocess.CompletedProcess:
        """Run a shell command via asyncio.to_thread."""
        def _exec() -> subprocess.CompletedProcess:
            return subprocess.run(
                cmd, capture_output=True, text=not binary, timeout=timeout,
            )

        return await asyncio.to_thread(_exec)
