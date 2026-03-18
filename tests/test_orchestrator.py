"""Tests for the backup orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backup_agent.orchestrator import BackupOrchestrator, _build_sources
from backup_agent.restic import ResticResult
from backup_agent.status import StatusStore


@pytest.fixture
def orchestrator(test_settings, status_store):
    return BackupOrchestrator(status_store, test_settings)


class TestBuildSources:
    def test_all_enabled(self, test_settings):
        sources = _build_sources(test_settings)
        assert len(sources) == 12

    def test_disable_source(self, test_settings):
        test_settings.source_mongodb = False
        test_settings.source_postgres = False
        sources = _build_sources(test_settings)
        names = [s.name for s in sources]
        assert "mongodb" not in names
        assert "postgres" not in names


class TestOrchestrator:
    async def test_already_running_guard(self, orchestrator):
        orchestrator._running = True
        result = await orchestrator.run_backup()
        assert result["status"] == "skipped"

    @patch("backup_agent.orchestrator._build_sources")
    async def test_all_sources_fail(self, mock_build, orchestrator):
        from backup_agent.sources.base import BackupSource, DumpResult

        class FailSource(BackupSource):
            async def dump(self):
                return DumpResult(source_name=self.name, success=False, error="test error")

        mock_build.return_value = [FailSource("fail1", orchestrator.settings.backup_staging_dir)]

        result = await orchestrator.run_backup(triggered_by="test")
        assert result["status"] == "failure"

    @patch("backup_agent.orchestrator._build_sources")
    async def test_successful_run(self, mock_build, orchestrator, tmp_path):
        from backup_agent.sources.base import BackupSource, DumpResult

        dump_file = tmp_path / "test.dump"
        dump_file.write_text("test data")

        class SuccessSource(BackupSource):
            async def dump(self):
                return DumpResult(
                    source_name=self.name,
                    success=True,
                    output_path=str(dump_file),
                    size_bytes=9,
                )

        mock_build.return_value = [SuccessSource("ok1", orchestrator.settings.backup_staging_dir)]

        with patch.object(orchestrator, "_ship_to_nfs", new_callable=AsyncMock, return_value="nfs123"), \
             patch.object(orchestrator, "_ship_to_gcs", new_callable=AsyncMock, return_value="gcs456"), \
             patch.object(orchestrator, "_apply_nfs_retention", new_callable=AsyncMock):

            result = await orchestrator.run_backup(triggered_by="test")
            assert result["status"] == "success"
            assert result["nfs_snapshot"] == "nfs123"
            assert result["gcs_snapshot"] == "gcs456"

    @patch("backup_agent.orchestrator._build_sources")
    async def test_gcs_failure_is_partial(self, mock_build, orchestrator, tmp_path):
        from backup_agent.sources.base import BackupSource, DumpResult

        dump_file = tmp_path / "test.dump"
        dump_file.write_text("test data")

        class SuccessSource(BackupSource):
            async def dump(self):
                return DumpResult(
                    source_name=self.name,
                    success=True,
                    output_path=str(dump_file),
                    size_bytes=9,
                )

        mock_build.return_value = [SuccessSource("ok1", orchestrator.settings.backup_staging_dir)]

        with patch.object(orchestrator, "_ship_to_nfs", new_callable=AsyncMock, return_value="nfs123"), \
             patch.object(orchestrator, "_ship_to_gcs", new_callable=AsyncMock, return_value=None), \
             patch.object(orchestrator, "_apply_nfs_retention", new_callable=AsyncMock):

            result = await orchestrator.run_backup(triggered_by="test")
            assert result["status"] == "partial_failure"
