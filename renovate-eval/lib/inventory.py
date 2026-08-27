"""Machine-readable inventory of open Renovate pull requests."""

from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, Callable

from .common import (
    VALID_LABELS,
    compute_fingerprint_bytes,
    is_trusted_comment_author,
    parse_sentinel,
)

DEFAULT_EVALUATION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DISQUALIFYING_LABELS = {
    "renovate:caution",
    "renovate:breaking",
    "renovate:risk",
}

Run = Callable[..., subprocess.CompletedProcess]


def _run_json(
    run: Run,
    command: list[str],
    *,
    timeout: int = 30,
) -> Any:
    result = run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{' '.join(command)} returned invalid JSON") from exc


def _run_text(run: Run, command: list[str], *, timeout: int = 30) -> str:
    result = run(command, capture_output=True, text=True, timeout=timeout)
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        detail = result.stderr.strip() or value or "no output"
        raise RuntimeError(f"{' '.join(command)} failed: {detail}")
    return value


def _normalize_labels(pr: dict[str, Any]) -> list[str]:
    return [
        label["name"] if isinstance(label, dict) else str(label)
        for label in pr.get("labels") or []
    ]


def _normalize_files(pr: dict[str, Any]) -> list[str]:
    return [
        file["path"] if isinstance(file, dict) else str(file)
        for file in pr.get("files") or []
    ]


def _author_login(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or "")
    return str(value or "")


def _comment_association(comment: dict[str, Any]) -> str:
    return str(
        comment.get("authorAssociation")
        or comment.get("author_association")
        or ""
    )


def _flatten_paginated_response(value: Any, *, endpoint: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RuntimeError(f"gh api {endpoint} returned invalid paginated data")
    if not value:
        return []
    pages = value if all(isinstance(page, list) for page in value) else [value]
    items = [item for page in pages for item in page]
    if not all(isinstance(item, dict) for item in items):
        raise RuntimeError(f"gh api {endpoint} returned invalid paginated data")
    return items


def _fetch_paginated_items(run: Run, endpoint: str) -> list[dict[str, Any]]:
    response = _run_json(
        run,
        ["gh", "api", endpoint, "--paginate", "--slurp"],
        timeout=120,
    )
    return _flatten_paginated_response(response, endpoint=endpoint)


def _complete_changed_paths(
    run: Run,
    repository: str,
    pr: dict[str, Any],
) -> tuple[list[str], bool]:
    paths = _normalize_files(pr)
    try:
        changed_files = int(pr["changedFiles"])
    except (KeyError, TypeError, ValueError):
        return paths, False
    if changed_files < 0:
        return paths, False
    if len(paths) == changed_files:
        return paths, True
    if len(paths) > changed_files:
        return paths, False

    endpoint = f"repos/{repository}/pulls/{int(pr['number'])}/files"
    try:
        files = _fetch_paginated_items(run, endpoint)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return paths, False
    complete_paths = [str(file.get("filename") or "") for file in files]
    if len(complete_paths) != changed_files or any(not path for path in complete_paths):
        return paths, False
    return complete_paths, True


def _complete_comments(
    run: Run,
    repository: str,
    pr: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    comments = pr.get("comments") or []
    if not isinstance(comments, list):
        return [], False
    if len(comments) != 100:
        return comments, True

    endpoint = f"repos/{repository}/issues/{int(pr['number'])}/comments"
    try:
        api_comments = _fetch_paginated_items(run, endpoint)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return comments, False
    normalized = [
        {
            "author": comment.get("user") or comment.get("author"),
            "authorAssociation": comment.get("author_association")
            or comment.get("authorAssociation"),
            "body": comment.get("body") or "",
            "createdAt": comment.get("created_at") or comment.get("createdAt") or "",
        }
        for comment in api_comments
    ]
    return normalized, True


def _complete_bounded_collections(
    run: Run,
    repository: str,
    pr: dict[str, Any],
) -> dict[str, Any]:
    completed = dict(pr)
    paths, paths_complete = _complete_changed_paths(run, repository, pr)
    comments, comments_complete = _complete_comments(run, repository, pr)
    completed["files"] = paths
    completed["comments"] = comments
    completed["_files_complete"] = paths_complete
    completed["_comments_complete"] = comments_complete
    return completed


def _is_renovate_author(author: str) -> bool:
    return author in {"app/renovate", "renovate", "renovate[bot]"}


def _required_checks(run: Run, pr_number: int) -> dict[str, Any]:
    command = [
        "gh",
        "pr",
        "checks",
        str(pr_number),
        "--required",
        "--json",
        "name,bucket,state,workflow,link",
    ]
    try:
        result = run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return {"state": "unknown", "checks": []}

    try:
        checks = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {"state": "unknown", "checks": []}

    if not isinstance(checks, list):
        return {"state": "unknown", "checks": []}
    if not checks:
        return {"state": "none", "checks": []}

    buckets = {str(check.get("bucket") or "") for check in checks}
    if buckets & {"fail", "cancel"}:
        state = "failing"
    elif buckets <= {"pass", "skipping"}:
        state = "passing"
    else:
        state = "pending"
    return {"state": state, "checks": checks}


def _current_fingerprint(run: Run, pr_number: int) -> str | None:
    try:
        result = run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not isinstance(result.stdout, bytes):
        return None
    return compute_fingerprint_bytes(result.stdout)


def observe_pr(pr_number: int, *, run: Run | None = None) -> dict[str, Any]:
    """Fetch one PR's mutable head, base, and required-check state."""
    if pr_number <= 0:
        raise ValueError("PR number must be positive")
    run = run or subprocess.run
    command = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--json",
        "number,state,baseRefName,baseRefOid,headRefOid",
    ]
    before = _run_json(run, command)
    if not isinstance(before, dict):
        raise RuntimeError("gh pr view returned invalid PR data")
    required_checks = _required_checks(run, pr_number)
    after = _run_json(run, command)
    if not isinstance(after, dict):
        raise RuntimeError("gh pr view returned invalid PR data")
    stable = (
        before.get("headRefOid") == after.get("headRefOid")
        and before.get("state") == after.get("state")
        and before.get("baseRefName") == after.get("baseRefName")
        and before.get("baseRefOid") == after.get("baseRefOid")
    )
    return {
        "number": int(after.get("number") or pr_number),
        "state": after.get("state") or "",
        "base_ref": after.get("baseRefName") or "",
        "base_sha": after.get("baseRefOid") or "",
        "head_sha": after.get("headRefOid") or "",
        "stable": stable,
        "required_checks": required_checks
        if stable
        else {"state": "unknown", "checks": []},
    }


def _latest_trusted_comment(pr: dict[str, Any]) -> str | None:
    comments = []
    for comment in pr.get("comments") or []:
        author = _author_login(comment.get("author"))
        body = comment.get("body") or ""
        if is_trusted_comment_author(
            author, _comment_association(comment)
        ) and "<!-- renovate-eval-skill:" in body:
            comments.append(comment)
    if not comments:
        return None
    latest = max(comments, key=lambda comment: comment.get("createdAt") or "")
    return str(latest.get("body") or "")


def _parse_evaluated_at(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _evaluation(
    pr: dict[str, Any],
    *,
    labels: list[str],
    current_fingerprint: str | None,
    now: datetime,
    max_age_seconds: int,
) -> dict[str, Any]:
    body = _latest_trusted_comment(pr)
    sentinel = parse_sentinel(body) if body else None
    result = {
        "state": "missing" if body is None else "invalid",
        "label": sentinel.get("label") if sentinel else None,
        "version": sentinel.get("version") if sentinel else None,
        "fingerprint": sentinel.get("fingerprint") if sentinel else None,
        "current_fingerprint": current_fingerprint,
        "evaluated_at": sentinel.get("evaluated_at") if sentinel else None,
    }
    if not pr.get("_comments_complete", True):
        result["state"] = "unknown"
        return result
    if sentinel is None:
        return result
    if current_fingerprint is None:
        result["state"] = "unknown"
        return result

    label = sentinel.get("label")
    evaluated_at = _parse_evaluated_at(sentinel.get("evaluated_at"))
    if (
        label not in VALID_LABELS
        or label not in labels
        or evaluated_at is None
        or evaluated_at > now
    ):
        return result
    if sentinel.get("fingerprint") != current_fingerprint:
        result["state"] = "mismatched"
    elif (now - evaluated_at).total_seconds() > max_age_seconds:
        result["state"] = "stale"
    else:
        result["state"] = "current"
    return result


def _qualification_reasons(
    pr: dict[str, Any],
    *,
    author: str,
    labels: list[str],
    required_checks: dict[str, Any],
    evaluation: dict[str, Any],
) -> list[str]:
    reasons = []
    if not pr.get("_files_complete", True):
        reasons.append("changed paths are incomplete")
    if not pr.get("_comments_complete", True):
        reasons.append("evaluation comments are incomplete")
    if not _is_renovate_author(author):
        reasons.append("author is not Renovate")
    if pr.get("state") != "OPEN":
        reasons.append("PR is not open")
    if pr.get("isDraft"):
        reasons.append("PR is a draft")
    if pr.get("baseRefName") != "main":
        reasons.append("base branch is not main")

    for required_label in ("renovate", "renovate:evaluated", "renovate:safe"):
        if required_label not in labels:
            reasons.append(f"missing label: {required_label}")
    for label in sorted(DISQUALIFYING_LABELS & set(labels)):
        reasons.append(f"disqualifying label: {label}")

    evaluation_state = evaluation["state"]
    if evaluation_state != "current":
        reasons.append(f"evaluation is {evaluation_state}")
    if evaluation.get("label") != "renovate:safe":
        reasons.append("evaluation verdict is not renovate:safe")

    checks_state = required_checks["state"]
    if checks_state != "passing":
        checks_reason = "missing" if checks_state == "none" else checks_state
        reasons.append(f"required checks are {checks_reason}")

    mergeable = str(pr.get("mergeable") or "UNKNOWN")
    if mergeable != "MERGEABLE":
        reasons.append(f"PR is not mergeable ({mergeable})")
    merge_state = str(pr.get("mergeStateStatus") or "UNKNOWN")
    if merge_state != "CLEAN":
        reasons.append(f"merge state is not clean ({merge_state})")
    return reasons


def _inventory_pr(
    run: Run,
    pr: dict[str, Any],
    *,
    now: datetime,
    max_age_seconds: int,
    fingerprint_only_terminal_checks: bool = False,
) -> dict[str, Any]:
    number = int(pr["number"])
    labels = _normalize_labels(pr)
    author = _author_login(pr.get("author"))
    required_checks = _required_checks(run, number)
    current_fingerprint = None
    if not fingerprint_only_terminal_checks or required_checks["state"] not in {
        "pending",
        "unknown",
    }:
        current_fingerprint = _current_fingerprint(run, number)
    evaluation = _evaluation(
        pr,
        labels=labels,
        current_fingerprint=current_fingerprint,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    reasons = _qualification_reasons(
        pr,
        author=author,
        labels=labels,
        required_checks=required_checks,
        evaluation=evaluation,
    )
    return {
        "number": number,
        "title": pr.get("title") or "",
        "url": pr.get("url") or "",
        "author": author,
        "state": pr.get("state") or "",
        "is_draft": bool(pr.get("isDraft")),
        "base_ref": pr.get("baseRefName") or "",
        "base_sha": pr.get("baseRefOid") or "",
        "head_sha": pr.get("headRefOid") or "",
        "files": _normalize_files(pr),
        "files_complete": bool(pr.get("_files_complete", True)),
        "labels": labels,
        "mergeable": pr.get("mergeable") or "UNKNOWN",
        "merge_state_status": pr.get("mergeStateStatus") or "UNKNOWN",
        "automerge": pr.get("autoMergeRequest") is not None,
        "required_checks": {"state": required_checks["state"]},
        "evaluation": evaluation,
        "safety_qualified": not reasons,
        "reasons": reasons,
    }


def build_inventory(
    *,
    pr_number: int | None = None,
    evaluation_max_age_seconds: int = DEFAULT_EVALUATION_MAX_AGE_SECONDS,
    now: datetime | None = None,
    run: Run | None = None,
    fingerprint_only_terminal_checks: bool = False,
) -> dict[str, Any]:
    """Fetch and classify every open Renovate PR or one selected PR."""
    if evaluation_max_age_seconds < 0:
        raise ValueError("evaluation_max_age_seconds must not be negative")
    if pr_number is not None and pr_number <= 0:
        raise ValueError("PR number must be positive")
    run = run or subprocess.run
    now = now or datetime.now(UTC)

    repository = _run_text(
        run,
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
    )

    fields = (
        "number,title,url,author,state,isDraft,baseRefName,baseRefOid,headRefOid,"
        "changedFiles,files,labels,"
        "mergeable,mergeStateStatus,autoMergeRequest,comments"
    )
    if pr_number is not None:
        pr = _run_json(
            run,
            ["gh", "pr", "view", str(pr_number), "--json", fields],
        )
        if not isinstance(pr, dict):
            raise RuntimeError("gh pr view returned invalid PR data")
        prs = [pr]
    else:
        # Avoid gh's search filters here. Their cached schema feature-detection
        # response can replay a rate-limit error after the quota has reset.
        prs = _run_json(
            run,
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                fields,
                "--limit",
                "9999",
            ],
        )
        if not isinstance(prs, list):
            raise RuntimeError("gh pr list returned an invalid PR list")
        prs = [
            pr
            for pr in prs
            if _is_renovate_author(_author_login(pr.get("author")))
        ]

    def classify(pr: dict[str, Any]) -> dict[str, Any]:
        pr = _complete_bounded_collections(run, repository, pr)
        return _inventory_pr(
            run,
            pr,
            now=now,
            max_age_seconds=evaluation_max_age_seconds,
            fingerprint_only_terminal_checks=fingerprint_only_terminal_checks,
        )

    if prs:
        with ThreadPoolExecutor(max_workers=min(8, len(prs))) as executor:
            records = list(executor.map(classify, prs))
    else:
        records = []

    return {
        "repository": repository,
        "evaluation_max_age_seconds": evaluation_max_age_seconds,
        "prs": records,
    }


def build_settled_inventory(
    *,
    pr_number: int,
    evaluation_max_age_seconds: int = DEFAULT_EVALUATION_MAX_AGE_SECONDS,
    now: datetime | None = None,
    run: Run | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    poll_interval_seconds: float = 30,
    timeout_seconds: float = 30 * 60,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Wait for one PR's checks and classify only a stable head and base."""
    if pr_number <= 0:
        raise ValueError("PR number must be positive")
    if poll_interval_seconds <= 0:
        raise ValueError("poll_interval_seconds must be positive")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    run = run or subprocess.run
    deadline = monotonic() + timeout_seconds
    last_observed_head: str | None = None
    unknown_delay = poll_interval_seconds

    def ensure_before_deadline() -> float:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"PR #{pr_number} did not settle within "
                f"{timeout_seconds:g} seconds; safety is unverified"
            )
        return remaining

    def wait(delay: float) -> None:
        remaining = ensure_before_deadline()
        sleep(min(delay, remaining))

    def wait_for_observation(observation: dict[str, Any]) -> bool:
        nonlocal last_observed_head, unknown_delay
        if not observation["stable"]:
            last_observed_head = observation["head_sha"]
            unknown_delay = poll_interval_seconds
            if progress is not None:
                progress(
                    f"PR #{pr_number} changed while required checks were read; "
                    f"retrying in {poll_interval_seconds:g} seconds"
                )
            wait(poll_interval_seconds)
            return True
        if observation["state"] != "OPEN":
            return False
        head_sha = observation["head_sha"]
        if head_sha != last_observed_head:
            last_observed_head = head_sha
            unknown_delay = poll_interval_seconds
        checks_state = observation["required_checks"]["state"]
        if checks_state == "unknown":
            if progress is not None:
                progress(
                    f"PR #{pr_number} head {head_sha[:12]} has unknown required-check "
                    f"state; retrying in {unknown_delay:g} seconds"
                )
            wait(unknown_delay)
            unknown_delay = min(unknown_delay * 2, 120)
            return True
        if checks_state == "pending":
            unknown_delay = poll_interval_seconds
            if progress is not None:
                progress(
                    f"PR #{pr_number} head {head_sha[:12]} has pending required "
                    f"checks; retrying in {poll_interval_seconds:g} seconds"
                )
            wait(poll_interval_seconds)
            return True
        unknown_delay = poll_interval_seconds
        return False

    while True:
        ensure_before_deadline()
        observation = observe_pr(pr_number, run=run)
        if wait_for_observation(observation):
            continue

        expected_head = observation["head_sha"]
        expected_base = (observation["base_ref"], observation["base_sha"])
        inventory = build_inventory(
            pr_number=pr_number,
            evaluation_max_age_seconds=evaluation_max_age_seconds,
            now=now,
            run=run,
            fingerprint_only_terminal_checks=True,
        )
        final_observation = observe_pr(pr_number, run=run)
        ensure_before_deadline()
        record = inventory["prs"][0]

        head_changed = (
            record["head_sha"] != expected_head
            or final_observation["head_sha"] != expected_head
        )
        base_changed = (
            (record["base_ref"], record["base_sha"]) != expected_base
            or (final_observation["base_ref"], final_observation["base_sha"])
            != expected_base
        )
        if head_changed or base_changed:
            if progress is not None:
                changed_identity = "head" if head_changed else "base"
                progress(
                    f"PR #{pr_number} {changed_identity} changed while safety "
                    "evidence was read; "
                    "discarding it and restarting"
                )
            wait_for_observation(final_observation)
            continue
        classification_observation = {
            "state": record["state"],
            "base_ref": record["base_ref"],
            "base_sha": record["base_sha"],
            "head_sha": record["head_sha"],
            "stable": True,
            "required_checks": record["required_checks"],
        }
        if (
            classification_observation["state"] != final_observation["state"]
            or classification_observation["required_checks"]["state"]
            != final_observation["required_checks"]["state"]
        ):
            if progress is not None:
                progress(
                    f"PR #{pr_number} state changed while safety evidence was read; "
                    "discarding it and restarting"
                )
            wait_for_observation(final_observation)
            continue
        if wait_for_observation(classification_observation):
            continue
        if wait_for_observation(final_observation):
            continue
        return inventory
