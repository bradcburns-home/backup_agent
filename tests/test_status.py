"""Tests for the SQLite status store."""

from __future__ import annotations

import pytest

from backup_agent.status import StatusStore


@pytest.fixture
def store(tmp_db):
    return StatusStore(db_path=tmp_db)


async def test_record_and_retrieve_run(store: StatusStore):
    run_id = await store.record_run_start("test")
    assert run_id >= 1

    await store.record_run_finish(
        run_id=run_id,
        result="success",
        duration_seconds=12.5,
        sources={"mongodb": {"status": "success", "size_bytes": 1000}},
        nfs_snapshot_id="abc123",
        gcs_snapshot_id="def456",
        total_size_bytes=1000,
    )

    latest = await store.get_latest_run()
    assert latest is not None
    assert latest["result"] == "success"
    assert latest["duration_seconds"] == 12.5
    assert latest["nfs_snapshot_id"] == "abc123"
    assert latest["sources"]["mongodb"]["status"] == "success"


async def test_run_history(store: StatusStore):
    for i in range(5):
        rid = await store.record_run_start("test")
        await store.record_run_finish(rid, "success", float(i), {})

    history = await store.get_run_history(limit=3)
    assert len(history) == 3

    count = await store.get_run_count()
    assert count == 5


async def test_restore_test_lifecycle(store: StatusStore):
    test_id = await store.record_restore_test_start("manual")
    assert test_id >= 1

    await store.record_restore_test_finish(
        test_id=test_id,
        result="pass",
        sources={"mongodb.archive": {"status": "pass", "size": 1000}},
    )

    history = await store.get_restore_test_history(limit=10)
    assert len(history) == 1
    assert history[0]["result"] == "pass"
    assert history[0]["sources"]["mongodb.archive"]["status"] == "pass"


async def test_empty_store(store: StatusStore):
    latest = await store.get_latest_run()
    assert latest is None

    history = await store.get_run_history()
    assert history == []

    count = await store.get_run_count()
    assert count == 0
