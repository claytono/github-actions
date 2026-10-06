"""Tests for evaluation pass planning and label repair."""

from __future__ import annotations

import random
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from lib.dispatch import (
    apply_repair,
    classify,
    confirm_repair,
    label_repair,
    matrix_for,
    plan_pass,
    read_pr_head,
    require_fingerprint,
    require_stable_head,
    select_work,
    summarize,
    write_outputs,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
WEEK = 7 * 24 * 60 * 60


def _record(
    number=1,
    *,
    state="current",
    label="renovate:safe",
    fingerprint="abc",
    current="abc",
    evaluated_at=NOW - timedelta(hours=1),
    labels=("renovate", "renovate:safe", "renovate:evaluated"),
    checks="passing",
    automerge=False,
    pr_state="OPEN",
    draft=False,
    author="app/renovate",
):
    return {
        "number": number,
        "author": author,
        "state": pr_state,
        "is_draft": draft,
        "head_sha": f"sha{number}",
        "labels": list(labels),
        "automerge": automerge,
        "required_checks": {"state": checks},
        "evaluation": {
            "state": state,
            "label": label,
            "fingerprint": fingerprint,
            "current_fingerprint": current,
            "evaluated_at": evaluated_at.isoformat().replace("+00:00", "Z")
            if isinstance(evaluated_at, datetime)
            else evaluated_at,
        },
    }


def _classify(record):
    return classify(record, now=NOW, max_age_seconds=WEEK)


def test_current_evaluation_with_correct_labels_is_skipped():
    assert _classify(_record()) == {
        "action": "skip",
        "pr_number": 1,
        "reason": "current",
    }


def test_sentinel_without_labels_is_repaired_not_reevaluated():
    decision = _classify(_record(state="invalid", labels=("renovate",)))

    assert decision == {
        "action": "repair",
        "pr_number": 1,
        "add": ["renovate:safe", "renovate:evaluated"],
        "remove": [],
    }


def test_current_evaluation_missing_evaluated_label_is_repaired():
    decision = _classify(_record(labels=("renovate", "renovate:safe")))

    assert decision["action"] == "repair"
    assert decision["add"] == ["renovate:evaluated"]


def test_conflicting_verdict_label_is_removed():
    decision = _classify(
        _record(
            state="invalid",
            labels=("renovate", "renovate:risk", "renovate:evaluated"),
        )
    )

    assert decision["add"] == ["renovate:safe"]
    assert decision["remove"] == ["renovate:risk"]


@pytest.mark.parametrize("state", ["missing", "mismatched", "stale"])
def test_states_needing_evaluation(state):
    decision = _classify(_record(state=state, current="new"))

    assert decision == {
        "action": "evaluate",
        "pr_number": 1,
        "head_sha": "sha1",
        "fingerprint": "new",
        "reason": state,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"fingerprint": "old"},
        {"label": "renovate:unknown"},
        {"evaluated_at": None},
        {"evaluated_at": "not a time"},
        {"evaluated_at": NOW + timedelta(hours=1)},
        {"evaluated_at": NOW - timedelta(days=8)},
    ],
)
def test_unusable_invalid_sentinel_is_reevaluated(overrides):
    decision = _classify(_record(state="invalid", labels=("renovate",), **overrides))

    assert decision["action"] == "evaluate"
    assert decision["reason"] == "invalid"


def test_naive_timestamp_is_treated_as_utc():
    decision = _classify(
        _record(
            state="invalid", labels=("renovate",), evaluated_at="2026-10-05T11:00:00"
        )
    )

    assert decision["action"] == "repair"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"pr_state": "CLOSED"}, "not open"),
        ({"draft": True}, "not open"),
        ({"automerge": True}, "automerge"),
        ({"labels": ("renovate", "automerge")}, "automerge"),
        ({"checks": "pending"}, "checks pending"),
        ({"checks": "unknown"}, "checks unknown"),
        ({"checks": "none"}, "checks none"),
        ({"current": None}, "no fingerprint"),
        ({"state": "unknown"}, "evaluation unknown"),
    ],
)
def test_skip_reasons(overrides, reason):
    decision = _classify(_record(**overrides))

    assert decision["action"] == "skip"
    assert decision["reason"] == reason


@pytest.mark.parametrize("checks", ["passing", "failing"])
def test_terminal_check_states_are_eligible(checks):
    assert _classify(_record(state="missing", checks=checks))["action"] == "evaluate"


def test_label_repair_returns_none_when_consistent():
    assert (
        label_repair(["renovate:safe", "renovate:evaluated"], "renovate:safe") is None
    )


def test_select_work_shuffles_and_limits_batch():
    inventory = {
        "prs": [_record(n, state="missing") for n in range(1, 6)]
        + [_record(9, state="invalid", labels=("renovate",))]
        + [_record(10)]
    }

    plan = select_work(
        inventory, batch_size=2, max_age_seconds=WEEK, now=NOW, rng=random.Random(1)
    )

    expected = list(range(1, 6))
    random.Random(1).shuffle(expected)
    assert [d["pr_number"] for d in plan["evaluate"]] == expected[:2]
    assert [d["pr_number"] for d in plan["deferred"]] == expected[2:]
    assert [d["pr_number"] for d in plan["repair"]] == [9]
    assert [d["pr_number"] for d in plan["skipped"]] == [10]


def test_select_work_defaults_and_validation():
    assert (
        select_work({"prs": []}, batch_size=6, max_age_seconds=WEEK)["evaluate"] == []
    )
    with pytest.raises(ValueError, match="batch_size"):
        select_work({"prs": []}, batch_size=-1, max_age_seconds=WEEK)


def test_matrix_for_batch():
    batch = [{"pr_number": 3, "head_sha": "s", "fingerprint": "f", "reason": "x"}]

    assert matrix_for(batch) == {
        "include": [{"pr_number": 3, "head_sha": "s", "fingerprint": "f"}]
    }


def test_plan_pass_sweep_uses_select_work():
    plan = plan_pass(
        {"prs": [_record(state="missing")]},
        batch_size=6,
        max_age_seconds=WEEK,
        now=NOW,
        rng=random.Random(0),
    )

    assert [d["pr_number"] for d in plan["evaluate"]] == [1]


def test_plan_pass_manual_pr_evaluates_regardless_of_state():
    plan = plan_pass(
        {"prs": [_record(7)]}, batch_size=6, max_age_seconds=WEEK, force_pr=7, now=NOW
    )

    assert plan["evaluate"] == [
        {
            "action": "evaluate",
            "pr_number": 7,
            "head_sha": "sha7",
            "fingerprint": "abc",
            "reason": "manual",
        }
    ]


def test_plan_pass_manual_pr_without_fingerprint_still_evaluates():
    plan = plan_pass(
        {"prs": [_record(7, current=None, checks="pending")]},
        batch_size=6,
        max_age_seconds=WEEK,
        force_pr=7,
        now=NOW,
    )

    assert plan["evaluate"][0]["fingerprint"] == ""


def test_plan_pass_manual_pr_must_be_open():
    plan = plan_pass(
        {"prs": [_record(7, pr_state="MERGED")]},
        batch_size=6,
        max_age_seconds=WEEK,
        force_pr=7,
        now=NOW,
    )

    assert plan["evaluate"] == []
    assert plan["skipped"][0]["reason"] == "not open"


def test_plan_pass_missing_pr():
    plan = plan_pass({"prs": []}, batch_size=6, max_age_seconds=WEEK, force_pr=7)

    assert plan["skipped"] == [
        {"action": "skip", "pr_number": 7, "reason": "not found"}
    ]


@pytest.mark.parametrize(
    ("record", "bucket"),
    [
        (_record(7, state="missing"), "evaluate"),
        (_record(7), "skipped"),
        (_record(7, state="invalid", labels=("renovate",)), "repair"),
    ],
)
def test_plan_pass_recheck_only_evaluates_when_still_needed(record, bucket):
    plan = plan_pass(
        {"prs": [record]},
        batch_size=6,
        max_age_seconds=WEEK,
        force_pr=7,
        recheck=True,
        now=NOW,
    )

    assert [d["pr_number"] for d in plan[bucket]] == [7]
    assert sum(len(plan[key]) for key in plan) == 1


def test_apply_repair_uses_issue_label_endpoints():
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["check"] is True
        return subprocess.CompletedProcess(command, 0)

    apply_repair(
        "o/r",
        {
            "pr_number": 5,
            "add": ["renovate:safe", "renovate:evaluated"],
            "remove": ["renovate:risk"],
        },
        run=run,
    )

    assert calls == [
        [
            "gh",
            "api",
            "--method",
            "DELETE",
            "repos/o/r/issues/5/labels/renovate%3Arisk",
        ],
        [
            "gh",
            "api",
            "--method",
            "POST",
            "repos/o/r/issues/5/labels",
            "-f",
            "labels[]=renovate:safe",
            "-f",
            "labels[]=renovate:evaluated",
        ],
    ]


def test_apply_repair_remove_only():
    calls = []
    apply_repair(
        "o/r",
        {"pr_number": 5, "add": [], "remove": ["renovate:risk"]},
        run=lambda command, **kwargs: calls.append(command),
    )

    assert len(calls) == 1


def test_write_outputs(tmp_path):
    output = tmp_path / "out"
    plan = {
        "evaluate": [{"pr_number": 3, "head_sha": "s", "fingerprint": "f"}],
        "deferred": [],
        "repair": [],
        "skipped": [],
    }

    write_outputs(plan, str(output))
    write_outputs({**plan, "evaluate": []}, str(output))
    write_outputs(plan, None)

    assert output.read_text().splitlines() == [
        'matrix={"include":[{"pr_number":3,"head_sha":"s","fingerprint":"f"}]}',
        "should_evaluate=true",
        "deferred=0",
        "head_sha=s",
        'matrix={"include":[]}',
        "should_evaluate=false",
        "deferred=0",
    ]


def test_write_outputs_reports_deferred_count(tmp_path):
    output = tmp_path / "out"
    plan = {"evaluate": [], "deferred": [{}, {}, {}], "repair": [], "skipped": []}

    write_outputs(plan, str(output))

    assert "deferred=3" in output.read_text().splitlines()


def test_write_outputs_omits_head_for_multi_pr_batch(tmp_path):
    output = tmp_path / "out"
    item = {"pr_number": 3, "head_sha": "s", "fingerprint": "f"}
    plan = {
        "evaluate": [item, {**item, "pr_number": 4}],
        "deferred": [],
        "repair": [],
        "skipped": [],
    }

    write_outputs(plan, str(output))

    assert not any(
        line.startswith("head_sha=") for line in output.read_text().splitlines()
    )


def test_confirm_repair_uses_fresh_inventory():
    planned = {"pr_number": 7, "add": ["renovate:safe"], "remove": []}
    fresh = {"prs": [_record(7, state="invalid", labels=("renovate", "renovate:risk"))]}

    assert confirm_repair(planned, fresh, max_age_seconds=WEEK, now=NOW) == {
        "action": "repair",
        "pr_number": 7,
        "add": ["renovate:safe", "renovate:evaluated"],
        "remove": ["renovate:risk"],
    }


@pytest.mark.parametrize(
    "fresh_record",
    [
        _record(7, state="mismatched", current="pushed", labels=("renovate",)),
        _record(7),
    ],
)
def test_confirm_repair_skips_when_pr_changed_or_already_fixed(fresh_record):
    planned = {"pr_number": 7, "add": ["renovate:safe"], "remove": []}

    assert (
        confirm_repair(planned, {"prs": [fresh_record]}, max_age_seconds=WEEK, now=NOW)
        is None
    )


def test_summarize():
    text = summarize(
        {
            "evaluate": [{"pr_number": 3, "reason": "missing"}],
            "deferred": [{}, {}],
            "repair": [
                {"pr_number": 4, "add": ["renovate:safe"], "remove": ["renovate:risk"]},
            ],
            "skipped": [
                {"reason": "current"},
                {"reason": "current"},
                {"reason": "checks pending"},
            ],
        }
    )

    assert text.splitlines() == [
        "Evaluate: #3 (missing)",
        "Deferred to a later pass: 2",
        "Label repairs: #4 (+renovate:safe; -renovate:risk)",
        "Skipped: 1 checks pending, 2 current",
    ]


def test_summarize_empty():
    text = summarize({"evaluate": [], "deferred": [], "repair": [], "skipped": []})

    assert "Evaluate: none" in text
    assert "Label repairs: none" in text
    assert "Skipped: none" in text


def _single(head="sha7"):
    return {
        "evaluate": [{"pr_number": 7, "head_sha": head, "fingerprint": "f"}],
        "deferred": [],
        "repair": [],
        "skipped": [],
    }


def test_require_stable_head_keeps_unchanged_head():
    plan = _single()

    assert require_stable_head(plan, "sha7") is plan


@pytest.mark.parametrize("current", ["pushed", None])
def test_require_stable_head_skips_when_head_moved_or_unreadable(current):
    plan = require_stable_head(_single(), current)

    assert plan["evaluate"] == []
    assert plan["skipped"] == [
        {"action": "skip", "pr_number": 7, "reason": "head changed during recheck"}
    ]


def test_require_stable_head_ignores_multi_pr_plans():
    plan = _single()
    plan["evaluate"].append({"pr_number": 8, "head_sha": "x", "fingerprint": "g"})

    assert require_stable_head(plan, "other") is plan


def test_read_pr_head_returns_current_head():
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")

    assert read_pr_head(7, run=run) == "abc123"
    assert calls[0][:4] == ["gh", "pr", "view", "7"]


@pytest.mark.parametrize(
    ("returncode", "stdout", "stderr", "detail"),
    [
        (1, "", "HTTP 502", "HTTP 502"),
        (0, "", "", "no output"),
        (1, None, None, "no output"),
    ],
)
def test_read_pr_head_fails_loudly(returncode, stdout, stderr, detail):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode, stdout=stdout, stderr=stderr
        )

    with pytest.raises(RuntimeError, match=f"failed to read head of PR #7: {detail}"):
        read_pr_head(7, run=run)


@pytest.mark.parametrize("checks", ["pending", "unknown"])
def test_allow_pending_checks_evaluates_before_ci_finishes(checks):
    decision = classify(
        _record(state="missing", checks=checks),
        now=NOW,
        max_age_seconds=WEEK,
        allow_pending_checks=True,
    )

    assert decision["action"] == "evaluate"


def test_allow_pending_checks_still_skips_current_evaluations():
    decision = classify(
        _record(checks="pending"),
        now=NOW,
        max_age_seconds=WEEK,
        allow_pending_checks=True,
    )

    assert decision == {"action": "skip", "pr_number": 1, "reason": "current"}


def test_plan_pass_recheck_passes_allow_pending_checks():
    plan = plan_pass(
        {"prs": [_record(7, state="missing", checks="pending")]},
        batch_size=6,
        max_age_seconds=WEEK,
        force_pr=7,
        recheck=True,
        allow_pending_checks=True,
        now=NOW,
    )

    assert [d["pr_number"] for d in plan["evaluate"]] == [7]


def test_recheck_skips_non_renovate_prs():
    plan = plan_pass(
        {"prs": [_record(7, state="missing", author="someone")]},
        batch_size=6,
        max_age_seconds=WEEK,
        force_pr=7,
        recheck=True,
        now=NOW,
    )

    assert plan["skipped"][0]["reason"] == "not a Renovate PR"


def test_manual_request_ignores_author():
    plan = plan_pass(
        {"prs": [_record(7, author="someone")]},
        batch_size=6,
        max_age_seconds=WEEK,
        force_pr=7,
        now=NOW,
    )

    assert [d["pr_number"] for d in plan["evaluate"]] == [7]


def test_require_fingerprint_fails_when_pr_could_not_be_fingerprinted():
    plan = {
        "evaluate": [],
        "deferred": [],
        "repair": [],
        "skipped": [{"action": "skip", "pr_number": 7, "reason": "no fingerprint"}],
    }

    with pytest.raises(RuntimeError, match="could not fingerprint PR #7"):
        require_fingerprint(plan)


def test_require_fingerprint_allows_other_outcomes():
    plan = {
        "evaluate": [],
        "deferred": [],
        "repair": [],
        "skipped": [{"action": "skip", "pr_number": 7, "reason": "current"}],
    }

    require_fingerprint(plan)
