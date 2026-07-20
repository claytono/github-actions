"""Tests for evaluation CI-wait selection."""

from __future__ import annotations

from renovate_eval import should_wait_for_ci


def test_post_mode_waits_by_default():
    assert should_wait_for_ci("post", enabled=True)


def test_post_mode_can_take_snapshot_without_waiting():
    assert not should_wait_for_ci("post", enabled=False)


def test_dry_run_never_waits():
    assert not should_wait_for_ci("dry-run", enabled=True)
