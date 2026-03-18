"""Tests for the restic wrapper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backup_agent.restic import ResticClient, ResticResult


class TestResticClient:
    @pytest.fixture
    def client(self):
        return ResticClient(
            repository="/tmp/test-repo",
            password_file="/dev/null",
        )

    @patch("subprocess.run")
    async def test_backup_success(self, mock_run, client):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"snapshot_id": "abc12345"}',
            stderr="",
        )

        result = await client.backup(["/tmp/test-data"])
        assert result.success
        assert result.json_output == {"snapshot_id": "abc12345"}

        cmd = mock_run.call_args[0][0]
        assert "restic" in cmd
        assert "--no-lock" in cmd
        assert "backup" in cmd
        assert "--json" in cmd

    @patch("subprocess.run")
    async def test_backup_failure(self, mock_run, client):
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Fatal: unable to open repository",
        )

        result = await client.backup(["/tmp/test-data"])
        assert not result.success

    @patch("subprocess.run")
    async def test_forget_with_retention(self, mock_run, client):
        mock_run.return_value = MagicMock(returncode=0, stdout="{}", stderr="")

        result = await client.forget(keep_hourly=24, keep_daily=7, keep_weekly=4, keep_monthly=12)
        assert result.success

        cmd = mock_run.call_args[0][0]
        assert "--keep-hourly" in cmd
        assert "24" in cmd

    @patch("subprocess.run")
    async def test_check_with_subset(self, mock_run, client):
        mock_run.return_value = MagicMock(returncode=0, stdout="no errors", stderr="")

        result = await client.check(read_data_subset="1/4")
        assert result.success

        cmd = mock_run.call_args[0][0]
        assert "--read-data-subset" in cmd
        assert "1/4" in cmd

    @patch("subprocess.run")
    async def test_no_lock_always_used(self, mock_run, client):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        await client.snapshots()
        cmd = mock_run.call_args[0][0]
        assert "--no-lock" in cmd

    @patch("subprocess.run")
    async def test_gcs_credentials_in_env(self, mock_run):
        gcs_client = ResticClient(
            repository="gs:test-bucket:/test-repo",
            password_file="/dev/null",
            google_credentials="/path/to/creds.json",
        )
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        await gcs_client.snapshots()
        env = mock_run.call_args[1]["env"]
        assert env["GOOGLE_APPLICATION_CREDENTIALS"] == "/path/to/creds.json"
        assert env["RESTIC_REPOSITORY"] == "gs:test-bucket:/test-repo"


class TestResticResult:
    def test_success(self):
        r = ResticResult(returncode=0, stdout="ok", stderr="")
        assert r.success

    def test_failure(self):
        r = ResticResult(returncode=1, stdout="", stderr="error")
        assert not r.success
