"""Tests for the BackupApp module."""

from __future__ import annotations

import pytest

from backup_agent.app import _integrity_check_subset


class TestIntegrityCheckSubset:
    """Regression coverage for bradcburns-home/Birmingham-Ops#329.

    The original ``(day - 1) // 7 + 1`` formula produced ``5`` for days
    29-31 of any month, which restic rejects (``--read-data-subset=5/4``
    is invalid because ``n`` must be ``<= t``). When the weekly check ran
    on Sunday March 29 2026 the whole check aborted.
    """

    @pytest.mark.parametrize(
        "day,expected",
        [
            (1, "1/4"),
            (7, "1/4"),
            (8, "2/4"),
            (14, "2/4"),
            (15, "3/4"),
            (21, "3/4"),
            (22, "4/4"),
            (28, "4/4"),
        ],
    )
    def test_normal_days_map_to_weeks_one_through_four(self, day, expected):
        assert _integrity_check_subset(day) == expected

    @pytest.mark.parametrize("day", [29, 30, 31])
    def test_overflow_days_clamp_to_week_four(self, day):
        """Days 29-31 must NOT produce 5/4 — restic rejects that."""
        assert _integrity_check_subset(day) == "4/4"
