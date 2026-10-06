"""Tests for evaluation-loop failure handling in renovate_eval._run_evaluate."""

from __future__ import annotations

import argparse
import json
import logging
import os

import pytest
import renovate_eval
from lib.common import VALID_LABELS


def _args() -> argparse.Namespace:
    return argparse.Namespace(
        pr=1234,
        mode="post",
        context="ci",
        provider="claude",
        evaluator_model="opus",
        auditor_model="sonnet",
        codex_evaluator_model="",
        codex_auditor_model="",
        codex_reasoning_effort="",
        yolo=False,
        agent_timeout=0,
        wait_for_ci=False,
        ci_timeout=0,
        instructions="",
    )


@pytest.fixture
def loop_env(monkeypatch, tmp_dir, valid_eval_data):
    """Stub everything around the loop and record any GitHub side effects."""
    side_effects = []
    monkeypatch.setattr("lib.fetch_pr_data.fetch_pr_data", lambda pr, d: None)
    monkeypatch.setattr("lib.git_fingerprint.read_pr_ref", lambda pr: None)
    monkeypatch.setattr("lib.git_fingerprint.read_repository", lambda: None)
    monkeypatch.setattr("lib.check_ci.check_ci", lambda *a, **kw: 0)
    monkeypatch.setattr(
        renovate_eval,
        "_post_comment",
        lambda *a, **kw: side_effects.append("comment"),
    )
    monkeypatch.setattr(
        renovate_eval,
        "_manage_labels",
        lambda *a, **kw: side_effects.append("label"),
    )
    return {"side_effects": side_effects, "eval_data": valid_eval_data}


def _run(tmp_dir):
    renovate_eval._run_evaluate(
        _args(),
        artifact_dir=tmp_dir,
        report_dir=tmp_dir,
        repo_root=tmp_dir,
        keep=False,
        total_start=renovate_eval._now(),
        VALID_LABELS=VALID_LABELS,
        build_sentinel=lambda *a: "",
        compute_fingerprint=lambda path: "",
        embed_eval_data=lambda data: "",
        get_ci_status=lambda pr: "passing",
        log=logging.getLogger("test"),
        render_report=lambda data, **kw: "report",
        validate_eval_data=lambda data: [],
    )


def test_evaluator_failure_after_feedback_aborts_without_label(
    monkeypatch, tmp_dir, loop_env
):
    calls = {"evaluator": 0}

    def fake_evaluator(**kwargs):
        calls["evaluator"] += 1
        if calls["evaluator"] > 1:
            raise RuntimeError("claude exited with code 1: session limit")
        with open(os.path.join(tmp_dir, "eval-data.json"), "w") as f:
            json.dump(loop_env["eval_data"], f)
        return {"session_id": "eval-session"}

    def fake_auditor(**kwargs):
        return {"status": "FEEDBACK", "issues": []}

    monkeypatch.setattr("lib.evaluator.run_evaluator", fake_evaluator)
    monkeypatch.setattr("lib.auditor.run_auditor", fake_auditor)

    with pytest.raises(SystemExit) as exc:
        _run(tmp_dir)

    assert exc.value.code == 1
    assert loop_env["side_effects"] == []
    with open(os.path.join(tmp_dir, "result.json")) as f:
        result = json.load(f)
    assert result["status"] == "ERROR"
    assert result["label"] is None


def test_auditor_failure_aborts_without_label(monkeypatch, tmp_dir, loop_env):
    def fake_evaluator(**kwargs):
        with open(os.path.join(tmp_dir, "eval-data.json"), "w") as f:
            json.dump(loop_env["eval_data"], f)
        return {"session_id": "eval-session"}

    def fake_auditor(**kwargs):
        raise RuntimeError("claude exited with code 1: session limit")

    monkeypatch.setattr("lib.evaluator.run_evaluator", fake_evaluator)
    monkeypatch.setattr("lib.auditor.run_auditor", fake_auditor)

    with pytest.raises(SystemExit) as exc:
        _run(tmp_dir)

    assert exc.value.code == 1
    assert loop_env["side_effects"] == []
