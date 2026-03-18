"""Tests for backup source handlers."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from backup_agent.sources.base import DumpResult
from backup_agent.sources.directory import DirectorySource, FileSource
from backup_agent.sources.mongodb import MongoDBSource


class TestDirectorySource:
    async def test_dump_success(self, tmp_staging, tmp_path):
        source_dir = tmp_path / "test_data"
        source_dir.mkdir()
        (source_dir / "file1.txt").write_text("hello")
        (source_dir / "file2.txt").write_text("world")

        source = DirectorySource("test_dir", tmp_staging, str(source_dir))
        result = await source.dump()

        assert result.success
        assert result.output_path.endswith(".tar.gz")
        assert os.path.exists(result.output_path)
        assert result.size_bytes > 0

    async def test_dump_missing_dir(self, tmp_staging):
        source = DirectorySource("missing", tmp_staging, "/nonexistent/path")
        result = await source.dump()

        assert not result.success
        assert "does not exist" in result.error

    async def test_verify_empty_file(self, tmp_staging, tmp_path):
        empty_file = tmp_path / "empty.tar.gz"
        empty_file.touch()

        source = DirectorySource("test", tmp_staging, "/fake")
        result = DumpResult(
            source_name="test",
            success=True,
            output_path=str(empty_file),
            size_bytes=0,
        )

        assert not await source.verify_dump(result)


class TestFileSource:
    async def test_dump_success(self, tmp_staging, tmp_path):
        source_file = tmp_path / "config.yaml"
        source_file.write_text("key: value")

        source = FileSource("test_file", tmp_staging, str(source_file))
        result = await source.dump()

        assert result.success
        assert os.path.exists(result.output_path)

    async def test_dump_missing_file(self, tmp_staging):
        source = FileSource("missing", tmp_staging, "/nonexistent/file.yaml")
        result = await source.dump()

        assert not result.success
        assert "does not exist" in result.error


class TestMongoDBSource:
    @patch("subprocess.run")
    async def test_dump_success(self, mock_run, tmp_staging):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="binary-archive-data",
            stderr="",
        )

        source = MongoDBSource(tmp_staging, container="test-mongo", database="testdb")
        result = await source.dump()

        assert result.success
        assert os.path.exists(result.output_path)
        assert result.size_bytes > 0
        mock_run.assert_called_once()

    @patch("subprocess.run")
    async def test_dump_failure(self, mock_run, tmp_staging):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="connection refused",
        )

        source = MongoDBSource(tmp_staging, container="test-mongo", database="testdb")
        result = await source.dump()

        assert not result.success
        assert "connection refused" in result.error
