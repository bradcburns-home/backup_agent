"""Shared fixtures for backup agent tests."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backup_agent.config import Settings
from backup_agent.restic import ResticClient, ResticResult
from backup_agent.status import StatusStore


@pytest.fixture
def tmp_staging(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    return str(staging)


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test_status.db")


@pytest.fixture
def status_store(tmp_db):
    return StatusStore(db_path=tmp_db)


@pytest.fixture
def test_settings(tmp_staging, tmp_db):
    return Settings(
        backup_staging_dir=tmp_staging,
        restic_nfs_repository="/tmp/test-nfs-repo",
        restic_gcs_repository="gs:test-bucket:/test-repo",
        restic_password_file="/dev/null",
        google_application_credentials="/dev/null",
        status_db_path=tmp_db,
        environment_name="test",
    )


@pytest.fixture
def mock_restic_success():
    return ResticResult(
        returncode=0,
        stdout='{"snapshot_id": "abc12345"}',
        stderr="",
        json_output={"snapshot_id": "abc12345"},
    )


@pytest.fixture
def mock_restic_failure():
    return ResticResult(
        returncode=1,
        stdout="",
        stderr="Fatal: unable to open repository",
    )


@pytest.fixture
def mock_docker_exec():
    """Patch subprocess.run to simulate docker exec calls."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="mock output",
            stderr="",
        )
        yield mock_run
