"""Tests for verification and restore testing."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from backup_agent.restic import ResticClient, ResticResult
from backup_agent.status import StatusStore
from backup_agent.verification import (
    _select_latest_snapshot,
    _validate_restored_files,
    run_integrity_check,
)


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

    def test_nested_layout_matches_restic_restore(self, tmp_path):
        """Regression: restic restore preserves absolute paths.

        Files land at ``<restore_dir>/staging/<filename>`` rather than
        directly at the top level. The validator must walk the full tree,
        not just ``os.listdir(restore_dir)``.
        """
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "mongodb.archive").write_bytes(b"data" * 100)
        (staging / "chromadb.tar.gz").write_bytes(b"tar" * 50)
        (staging / "ha_config.tar.gz").write_bytes(b"yaml" * 25)

        results = _validate_restored_files(str(tmp_path))

        assert "_no_files" not in results
        assert results[os.path.join("staging", "mongodb.archive")]["status"] == "pass"
        assert results[os.path.join("staging", "chromadb.tar.gz")]["status"] == "pass"
        assert results[os.path.join("staging", "ha_config.tar.gz")]["status"] == "pass"

    def test_nested_layout_detects_empty_file(self, tmp_path):
        staging = tmp_path / "staging"
        staging.mkdir()
        (staging / "good.archive").write_bytes(b"data" * 100)
        (staging / "empty.archive").touch()

        results = _validate_restored_files(str(tmp_path))

        assert results[os.path.join("staging", "good.archive")]["status"] == "pass"
        assert results[os.path.join("staging", "empty.archive")]["status"] == "fail"
        assert results[os.path.join("staging", "empty.archive")]["reason"] == "empty_file"


class TestSelectLatestSnapshot:
    def test_picks_newest_across_multiple_buckets(self):
        """Regression: ``restic snapshots --latest 1`` returns one snapshot per
        (host, paths) tuple, sorted ascending by time. Because our source set
        grows over time, multiple buckets exist and the naive ``[0]`` pick
        returns the OLDEST "latest" — a stale partial snapshot.
        """
        snapshots = [
            {"short_id": "ed6995fb", "time": "2026-03-18T00:32:37Z", "paths": ["/a"] * 5},
            {"short_id": "349b2771", "time": "2026-03-18T00:37:32Z", "paths": ["/a"] * 10},
            {"short_id": "7e19c7e7", "time": "2026-03-18T00:44:25Z", "paths": ["/a"] * 11},
            {"short_id": "ea8f3f52", "time": "2026-05-07T02:09:40Z", "paths": ["/a"] * 13},
            {"short_id": "79c47dab", "time": "2026-03-27T16:26:54Z", "paths": ["/a"] * 11},
        ]

        assert _select_latest_snapshot(snapshots) == "ea8f3f52"

    def test_single_snapshot(self):
        snapshots = [{"short_id": "abc12345", "time": "2026-05-07T02:00:00Z"}]
        assert _select_latest_snapshot(snapshots) == "abc12345"

    def test_falls_back_to_id_when_short_id_missing(self):
        snapshots = [{"id": "long-hash", "time": "2026-05-07T02:00:00Z"}]
        assert _select_latest_snapshot(snapshots) == "long-hash"

    def test_empty_list_returns_latest_sentinel(self):
        assert _select_latest_snapshot([]) == "latest"

    def test_non_list_returns_latest_sentinel(self):
        assert _select_latest_snapshot(None) == "latest"
        assert _select_latest_snapshot({"unexpected": "shape"}) == "latest"


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
