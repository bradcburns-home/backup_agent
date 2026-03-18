"""Tests for verification and restore testing."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from backup_agent.restic import ResticClient, ResticResult
from backup_agent.status import StatusStore
from backup_agent.verification import _validate_restored_files, run_integrity_check


class TestValidateRestoredFiles:
    def test_valid_files(self, tmp_path):
        (tmp_path / "mongodb.archive").write_bytes(b"data" * 100)
        (tmp_path / "config.tar.gz").write_bytes(b"tar" * 50)

        results = _validate_restored_files(str(tmp_path))
        assert results["mongodb.archive"]["status"] == "pass"
        assert results["config.tar.gz"]["status"] == "pass"

    def test_empty_file(self, tmp_path):
        (tmp_path / "empty.dump").touch()

        results = _validate_restored_files(str(tmp_path))
        assert results["empty.dump"]["status"] == "fail"
        assert results["empty.dump"]["reason"] == "empty_file"

    def test_empty_directory(self, tmp_path):
        results = _validate_restored_files(str(tmp_path))
        assert "_no_files" in results
        assert results["_no_files"]["status"] == "fail"


class TestIntegrityCheck:
    async def test_nfs_check_pass(self):
        mock_client = AsyncMock(spec=ResticClient)
        mock_client.check.return_value = ResticResult(
            returncode=0, stdout="no errors found", stderr=""
        )

        results = await run_integrity_check(mock_client, subset="1/4")
        assert results["nfs"]["status"] == "pass"

    async def test_nfs_check_fail(self):
        mock_client = AsyncMock(spec=ResticClient)
        mock_client.check.return_value = ResticResult(
            returncode=1, stdout="", stderr="pack abc123 is damaged"
        )

        results = await run_integrity_check(mock_client)
        assert results["nfs"]["status"] == "fail"

    async def test_gcs_check_included(self):
        nfs_client = AsyncMock(spec=ResticClient)
        gcs_client = AsyncMock(spec=ResticClient)
        nfs_client.check.return_value = ResticResult(returncode=0, stdout="ok", stderr="")
        gcs_client.check.return_value = ResticResult(returncode=0, stdout="ok", stderr="")

        results = await run_integrity_check(nfs_client, gcs_client)
        assert "nfs" in results
        assert "gcs" in results
        assert results["gcs"]["status"] == "pass"
