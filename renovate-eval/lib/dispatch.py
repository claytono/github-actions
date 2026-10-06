"""Choose which Renovate PRs an evaluation pass should evaluate or repair.

The dispatcher replaces per-PR event triggers. It reads the complete
inventory, evaluates only PRs whose evaluation is missing, stale, mismatched,
or unusable once their required checks finish, and repairs labels when a
trusted sentinel already matches the current diff. Repairs cover evaluations
that posted their report but stopped before applying labels.
"""

from __future__ import annotations

import json
import random
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from lib.common import VALID_LABELS
from lib.inventory import is_renovate_author

Run = Callable[..., subprocess.CompletedProcess]
EVALUATED_LABEL = "renovate:evaluated"
EVALUATE_STATES = {"missing", "mismatched", "stale"}
TERMINAL_CHECK_STATES = {"passing", "failing", "none"}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _sentinel_matches(
    evaluation: dict[str, Any], *, now: datetime, max_age_seconds: int
) -> bool:
    """Whether the latest trusted sentinel still describes the current diff."""
    evaluated_at = _parse_time(evaluation.get("evaluated_at"))
    return (
        evaluation.get("label") in VALID_LABELS
        and evaluation.get("fingerprint") is not None
        and evaluation.get("fingerprint") == evaluation.get("current_fingerprint")
        and evaluated_at is not None
        and evaluated_at <= now
        and (now - evaluated_at).total_seconds() <= max_age_seconds
    )


def label_repair(labels: list[str], verdict: str) -> dict[str, list[str]] | None:
    """Return the label edits that make labels agree with the sentinel."""
    add = [label for label in (verdict, EVALUATED_LABEL) if label not in labels]
    remove = sorted(
        label for label in labels if label in VALID_LABELS and label != verdict
    )
    if not add and not remove:
        return None
    return {"add": add, "remove": remove}


def classify(
    record: dict[str, Any],
    *,
    now: datetime,
    max_age_seconds: int,
    allow_pending_checks: bool = False,
    require_renovate_author: bool = False,
) -> dict[str, Any]:
    """Classify one inventory record as evaluate, repair, or skip.

    ``require_renovate_author`` guards automatic single-PR requests, whose PR
    comes from an event rather than the Renovate-only repository listing.

    ``allow_pending_checks`` serves per-PR event callers, which have no later
    trigger to catch a PR once its CI finishes; their evaluation waits for CI
    instead of being skipped.
    """
    number = record["number"]
    labels = list(record.get("labels") or [])
    evaluation = record.get("evaluation") or {}
    state = evaluation.get("state")
    if record.get("state") != "OPEN" or record.get("is_draft"):
        return {"action": "skip", "pr_number": number, "reason": "not open"}
    if require_renovate_author and not is_renovate_author(record.get("author") or ""):
        return {"action": "skip", "pr_number": number, "reason": "not a Renovate PR"}
    if record.get("automerge") or "automerge" in labels:
        return {"action": "skip", "pr_number": number, "reason": "automerge"}
    checks = (record.get("required_checks") or {}).get("state")
    if checks not in TERMINAL_CHECK_STATES and not allow_pending_checks:
        return {"action": "skip", "pr_number": number, "reason": f"checks {checks}"}
    if not evaluation.get("current_fingerprint"):
        return {"action": "skip", "pr_number": number, "reason": "no fingerprint"}

    if state in {"current", "invalid"} and _sentinel_matches(
        evaluation, now=now, max_age_seconds=max_age_seconds
    ):
        repair = label_repair(labels, evaluation["label"])
        if repair is None:
            return {"action": "skip", "pr_number": number, "reason": "current"}
        return {"action": "repair", "pr_number": number, **repair}
    if state in EVALUATE_STATES or state == "invalid":
        return {
            "action": "evaluate",
            "pr_number": number,
            "head_sha": record.get("head_sha") or "",
            "fingerprint": evaluation["current_fingerprint"],
            "reason": state,
        }
    return {"action": "skip", "pr_number": number, "reason": f"evaluation {state}"}


def select_work(
    inventory: dict[str, Any],
    *,
    batch_size: int,
    max_age_seconds: int,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Split an inventory into a shuffled evaluation batch and label repairs."""
    if batch_size < 0:
        raise ValueError("batch_size must not be negative")
    now = now or datetime.now(UTC)
    rng = rng or random.Random()
    decisions = [
        classify(record, now=now, max_age_seconds=max_age_seconds)
        for record in inventory.get("prs") or []
    ]
    candidates = [d for d in decisions if d["action"] == "evaluate"]
    rng.shuffle(candidates)
    batch = candidates[:batch_size]
    return {
        "evaluate": batch,
        "deferred": candidates[batch_size:],
        "repair": [d for d in decisions if d["action"] == "repair"],
        "skipped": [d for d in decisions if d["action"] == "skip"],
    }


def matrix_for(batch: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the GitHub Actions matrix for an evaluation batch."""
    return {
        "include": [
            {
                "pr_number": item["pr_number"],
                "head_sha": item["head_sha"],
                "fingerprint": item["fingerprint"],
            }
            for item in batch
        ]
    }


def apply_repair(repository: str, repair: dict[str, Any], *, run: Run) -> None:
    """Make one PR's labels agree with its sentinel through the issues API."""
    number = int(repair["pr_number"])
    for label in repair["remove"]:
        run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{repository}/issues/{number}/labels/{quote(label, safe='')}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    if repair["add"]:
        command = [
            "gh",
            "api",
            "--method",
            "POST",
            f"repos/{repository}/issues/{number}/labels",
        ]
        for label in repair["add"]:
            command.extend(["-f", f"labels[]={label}"])
        run(command, check=True, capture_output=True, text=True, timeout=30)


def confirm_repair(
    repair: dict[str, Any],
    fresh_inventory: dict[str, Any],
    *,
    max_age_seconds: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Re-derive a planned repair from a fresh single-PR inventory.

    Renovate can push between planning and repair. Labels are changed only if
    the fresh inventory still shows a sentinel matching the current diff, and
    the edits come from that fresh view rather than the plan.
    """
    plan = plan_pass(
        fresh_inventory,
        batch_size=0,
        max_age_seconds=max_age_seconds,
        force_pr=int(repair["pr_number"]),
        recheck=True,
        now=now,
    )
    return plan["repair"][0] if plan["repair"] else None


def plan_pass(
    inventory: dict[str, Any],
    *,
    batch_size: int,
    max_age_seconds: int,
    force_pr: int | None = None,
    recheck: bool = False,
    allow_pending_checks: bool = False,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Plan one dispatcher pass.

    A sweep evaluates a shuffled batch. ``force_pr`` evaluates that open PR
    regardless of its evaluation state, as a manual request. ``recheck``
    with ``force_pr`` evaluates the PR only if it still needs evaluation,
    which lets a queued matrix job skip work another pass already did.
    """
    now = now or datetime.now(UTC)
    if force_pr is None:
        return select_work(
            inventory,
            batch_size=batch_size,
            max_age_seconds=max_age_seconds,
            now=now,
            rng=rng,
        )
    records = [r for r in inventory.get("prs") or [] if r["number"] == force_pr]
    plan: dict[str, Any] = {"evaluate": [], "deferred": [], "repair": [], "skipped": []}
    if not records:
        plan["skipped"].append(
            {"action": "skip", "pr_number": force_pr, "reason": "not found"}
        )
        return plan
    decision = classify(
        records[0],
        now=now,
        max_age_seconds=max_age_seconds,
        allow_pending_checks=allow_pending_checks,
        require_renovate_author=recheck,
    )
    if not recheck:
        record = records[0]
        if record.get("state") != "OPEN":
            plan["skipped"].append(
                {"action": "skip", "pr_number": force_pr, "reason": "not open"}
            )
            return plan
        evaluation = record.get("evaluation") or {}
        decision = {
            "action": "evaluate",
            "pr_number": force_pr,
            "head_sha": record.get("head_sha") or "",
            "fingerprint": evaluation.get("current_fingerprint") or "",
            "reason": "manual",
        }
    plan[decision["action"] if decision["action"] != "skip" else "skipped"].append(
        decision
    )
    return plan


def require_fingerprint(plan: dict[str, Any]) -> None:
    """Fail when a single-PR request could not fingerprint its PR.

    Per-PR event callers have no later trigger, so silently skipping would
    leave the PR unevaluated; failing lets the run be retried.
    """
    for item in plan["skipped"]:
        if item["reason"] == "no fingerprint":
            raise RuntimeError(
                f"could not fingerprint PR #{item['pr_number']}; re-run to retry"
            )


def read_pr_head(pr_number: int, *, run: Run) -> str:
    """Read a PR's current head, failing loudly when GitHub cannot be read.

    A transient API failure must not look like a moved head, which would skip
    the evaluation and report success.
    """
    result = run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--json",
            "headRefOid",
            "-q",
            ".headRefOid",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    head = (result.stdout or "").strip()
    if result.returncode != 0 or not head:
        detail = (result.stderr or "").strip() or "no output"
        raise RuntimeError(f"failed to read head of PR #{pr_number}: {detail}")
    return head


def require_stable_head(
    plan: dict[str, Any], current_head: str | None
) -> dict[str, Any]:
    """Skip a single-PR recheck whose head moved while it was being read.

    The inventory reads the head and the diff in separate calls. If Renovate
    pushed in between, the head and fingerprint may describe different
    commits, so the evaluation waits for the next pass instead.
    """
    if len(plan["evaluate"]) != 1 or plan["evaluate"][0]["head_sha"] == current_head:
        return plan
    moved = plan["evaluate"][0]
    return {
        **plan,
        "evaluate": [],
        "skipped": [
            *plan["skipped"],
            {
                "action": "skip",
                "pr_number": moved["pr_number"],
                "reason": "head changed during recheck",
            },
        ],
    }


def write_outputs(plan: dict[str, Any], github_output: str | None) -> None:
    """Write the matrix and should_evaluate outputs for the workflow."""
    lines = [
        f"matrix={json.dumps(matrix_for(plan['evaluate']), separators=(',', ':'))}",
        f"should_evaluate={'true' if plan['evaluate'] else 'false'}",
    ]
    if len(plan["evaluate"]) == 1:
        # A single-PR recheck reports the head it confirmed, so the evaluation
        # checks out the same commit whose diff it evaluates.
        lines.append(f"head_sha={plan['evaluate'][0]['head_sha']}")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")


def summarize(plan: dict[str, Any]) -> str:
    """Human-readable summary of a dispatcher pass."""
    parts = [
        "Evaluate: "
        + (
            ", ".join(f"#{d['pr_number']} ({d['reason']})" for d in plan["evaluate"])
            or "none"
        ),
        f"Deferred to a later pass: {len(plan['deferred'])}",
        "Label repairs: "
        + (
            ", ".join(
                f"#{d['pr_number']} ("
                + "; ".join(
                    [f"+{label}" for label in d["add"]]
                    + [f"-{label}" for label in d["remove"]]
                )
                + ")"
                for d in plan["repair"]
            )
            or "none"
        ),
    ]
    reasons: dict[str, int] = {}
    for item in plan["skipped"]:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    parts.append(
        "Skipped: "
        + (
            ", ".join(f"{count} {reason}" for reason, count in sorted(reasons.items()))
            or "none"
        )
    )
    return "\n".join(parts)
