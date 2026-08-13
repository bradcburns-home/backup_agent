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
        test_settings.source_postgres = True
        sources = _build_sources(test_settings)
        assert len(sources) == 19  # +variant (2026-08, Variant forecasting pipeline)

    def test_every_flag_builds_a_source(self, test_settings):
        """A flag with no source behind it reports coverage that does not exist.

        The enable flags, the builder, and the MCP tool's report were three
        hand-maintained lists of the same thing, and they had drifted: six sources
        were backed up while `get_source_config` said they were not configured.
        The tool now derives its report from the flags, and this holds the flags to
        the builder from the other side.
        """
        test_settings.source_postgres = True
        flags = {
            name.removeprefix("source_")
            for name in type(test_settings).model_fields
            if name.startswith("source_")
        }
        built = {source.name for source in _build_sources(test_settings)}
        # `postgres_*` flags fan out to per-database sources named for the database,
        # and hermes_agent's source is named for the directory it archives.
        aliases = {"hermes_agent": "hermes_agent_data"}
        unbuilt = {
            flag
            for flag in flags
            if aliases.get(flag, flag) not in built and not flag.startswith("postgres")
        }
        assert not unbuilt, f"flags with no source behind them: {sorted(unbuilt)}"

    def test_the_claim_workspace_is_covered(self, test_settings):
        """It is a local-only git repo with no remote, so restic is the only copy."""
        sources = {s.name: s for s in _build_sources(test_settings)}
        assert sources["claim_packet"].source_path == "/srv/claim_packet_data"

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

        with patch.object(orchestrator, "_ship_to_nfs", new_callable=AsyncMock, return_value=(True, "nfs123")), \
             patch.object(orchestrator, "_ship_to_gcs", new_callable=AsyncMock, return_value=(True, "gcs456")), \
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

        with patch.object(orchestrator, "_ship_to_nfs", new_callable=AsyncMock, return_value=(True, "nfs123")), \
             patch.object(orchestrator, "_ship_to_gcs", new_callable=AsyncMock, return_value=(False, None)), \
             patch.object(orchestrator, "_apply_nfs_retention", new_callable=AsyncMock):

            result = await orchestrator.run_backup(triggered_by="test")
            assert result["status"] == "partial_failure"
