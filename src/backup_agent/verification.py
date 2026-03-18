"""Backup verification — integrity checks and automated restore tests."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from datetime import datetime
from typing import Any

from backup_agent.config import Settings, get_settings
from backup_agent.restic import ResticClient
from backup_agent.sources.base import BackupSource
from backup_agent.status import StatusStore

logger = logging.getLogger(__name__)


async def run_integrity_check(
    nfs_client: ResticClient,
    gcs_client: ResticClient | None = None,
    subset: str | None = None,
) -> dict[str, Any]:
    """Run restic check on the NFS repo (and optionally GCS)."""
    results: dict[str, Any] = {}

    logger.info("Running NFS integrity check (subset=%s)", subset)
    nfs_result = await nfs_client.check(read_data_subset=subset)
    results["nfs"] = {
        "status": "pass" if nfs_result.success else "fail",
        "output": nfs_result.stdout[:2000] if nfs_result.stdout else "",
        "error": nfs_result.stderr[:2000] if not nfs_result.success else "",
    }
    if nfs_result.success:
        logger.info("NFS integrity check passed")
    else:
        logger.error("NFS integrity check FAILED: %s", nfs_result.stderr[:500])

    if gcs_client:
        logger.info("Running GCS integrity check")
        gcs_result = await gcs_client.check()
        results["gcs"] = {
            "status": "pass" if gcs_result.success else "fail",
            "output": gcs_result.stdout[:2000] if gcs_result.stdout else "",
            "error": gcs_result.stderr[:2000] if not gcs_result.success else "",
        }
        if gcs_result.success:
            logger.info("GCS integrity check passed")
        else:
            logger.error("GCS integrity check FAILED: %s", gcs_result.stderr[:500])

    return results


async def run_restore_test(
    nfs_client: ResticClient,
    status_store: StatusStore,
    triggered_by: str = "schedule",
) -> dict[str, Any]:
    """Restore the latest NFS snapshot and validate each source.

    Uses an ephemeral temp directory that is cleaned up after validation.
    """
    test_id = await status_store.record_restore_test_start(triggered_by)
    source_results: dict[str, Any] = {}
    temp_dir = tempfile.mkdtemp(prefix="backup_restore_test_")

    try:
        snapshots_result = await nfs_client.snapshots(latest=1)
        if not snapshots_result.success or not snapshots_result.json_output:
            logger.error("Cannot list NFS snapshots for restore test")
            await status_store.record_restore_test_finish(test_id, "failure", {"error": "no_snapshots"})
            return {"status": "failure", "error": "cannot_list_snapshots"}

        snapshot_list = snapshots_result.json_output
        if isinstance(snapshot_list, list) and snapshot_list:
            snapshot_id = snapshot_list[0].get("short_id") or snapshot_list[0].get("id", "latest")
        else:
            snapshot_id = "latest"

        logger.info("Restoring snapshot %s to %s for validation", snapshot_id, temp_dir)
        restore_result = await nfs_client.restore(snapshot_id, temp_dir)
        if not restore_result.success:
            logger.error("Restore failed: %s", restore_result.stderr[:500])
            await status_store.record_restore_test_finish(
                test_id, "failure", {"error": restore_result.stderr[:500]},
            )
            return {"status": "failure", "error": "restore_failed"}

        source_results = _validate_restored_files(temp_dir)

        failed = [k for k, v in source_results.items() if v.get("status") != "pass"]
        overall = "pass" if not failed else "fail"

        if failed:
            logger.error("Restore test FAILED for sources: %s", ", ".join(failed))
        else:
            logger.info("Restore test passed — all sources validated")

        await status_store.record_restore_test_finish(test_id, overall, source_results)
        return {"status": overall, "sources": source_results}

    except Exception:
        logger.exception("Restore test failed with unhandled exception")
        await status_store.record_restore_test_finish(test_id, "failure", {"error": "exception"})
        return {"status": "failure", "error": "exception"}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _validate_restored_files(restore_dir: str) -> dict[str, Any]:
    """Walk the restored directory and validate each known source file."""
    results: dict[str, Any] = {}

    for filename in os.listdir(restore_dir):
        filepath = os.path.join(restore_dir, filename)
        if not os.path.isfile(filepath):
            continue

        size = os.path.getsize(filepath)
        if size == 0:
            results[filename] = {"status": "fail", "reason": "empty_file", "size": 0}
            continue

        results[filename] = {"status": "pass", "size": size}

    if not results:
        results["_no_files"] = {"status": "fail", "reason": "no_files_restored"}

    return results
