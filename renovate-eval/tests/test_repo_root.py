"""Tests for repository root discovery."""

from __future__ import annotations

import subprocess
import time

import pytest

import renovate_eval


def test_get_repo_root_returns_stripped_stdout(monkeypatch):
    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="/repo\n", stderr="")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    assert renovate_eval.get_repo_root() == "/repo"


def test_get_repo_root_rejects_nonzero_exit(monkeypatch):
    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 128, stdout="", stderr="fatal")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Unable to determine git repository root"):
        renovate_eval.get_repo_root()


def test_get_repo_root_rejects_empty_stdout(monkeypatch):
    def mock_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="\n", stderr="")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Unable to determine git repository root"):
        renovate_eval.get_repo_root()


def test_get_repo_root_wraps_missing_git(monkeypatch):
    def mock_run(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Unable to determine git repository root"):
        renovate_eval.get_repo_root()


def test_get_repo_root_wraps_os_error(monkeypatch):
    def mock_run(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Unable to determine git repository root"):
        renovate_eval.get_repo_root()


def test_get_repo_root_wraps_timeout(monkeypatch):
    def mock_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Unable to determine git repository root"):
        renovate_eval.get_repo_root()


def test_now_uses_monotonic_clock(monkeypatch):
    def fail_time():
        raise AssertionError("wall-clock time should not be used for elapsed timing")

    monkeypatch.setattr(time, "time", fail_time)
    monkeypatch.setattr(time, "monotonic", lambda: 1234.9)

    assert renovate_eval._now() == 1234
