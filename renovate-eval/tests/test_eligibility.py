"""Behavior tests for automatic Renovate evaluation eligibility."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / ".github/scripts/renovate-eval-check-eligibility.sh"
)


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _target_repo(tmp_path: Path) -> tuple[Path, str]:
    target = tmp_path / "target"
    target.mkdir(parents=True)
    _run("git", "init", "-b", "main", cwd=target)
    _run("git", "config", "user.name", "Test", cwd=target)
    _run("git", "config", "user.email", "test@example.com", cwd=target)
    (target / "version.txt").write_text("old\n")
    _run("git", "add", "version.txt", cwd=target)
    _run("git", "commit", "-m", "base", cwd=target)
    base_sha = _run("git", "rev-parse", "HEAD", cwd=target)
    _run("git", "update-ref", "refs/remotes/origin/main", base_sha, cwd=target)
    (target / "version.txt").write_text("new\n")
    _run("git", "add", "version.txt", cwd=target)
    _run("git", "commit", "-m", "update", cwd=target)

    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "origin/main...HEAD"],
        cwd=target,
        check=True,
        capture_output=True,
    ).stdout
    changed_lines = b"".join(
        line
        for line in diff.splitlines(keepends=True)
        if line.startswith((b"+", b"-"))
        and not line.startswith((b"+++", b"---"))
    )
    return target, hashlib.sha256(changed_lines).hexdigest()


def _fake_gh(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1 $2" == "pr view" ]]; then
  printf '%s\n' '{"number":123,"author":{"login":"renovate[bot]"},"baseRefName":"main","headRefOid":"head-sha","labels":[]}'
elif [[ "$1" == "api" && "$*" == *"issues/123/comments"* ]]; then
  if [[ "$*" == *"created_at"* ]]; then
    if [[ "$*" == *'github-actions[bot]'* ]]; then
      printf '%s\n' "${COMMENT_CREATED_AT:-}"
    else
      printf '%s\n' "${UNTRUSTED_COMMENT_CREATED_AT:-${COMMENT_CREATED_AT:-}}"
    fi
  else
    if [[ "$*" == *'github-actions[bot]'* ]]; then
      printf '%s\n' "${COMMENT_BODY:-}"
    else
      printf '%s\n' "${UNTRUSTED_COMMENT_BODY:-${COMMENT_BODY:-}}"
    fi
  fi
elif [[ "$1" == "api" ]]; then
  exit 0
else
  echo "unexpected gh invocation: $*" >&2
  exit 1
fi
"""
    )
    gh.chmod(0o755)
    python3 = bin_dir / "python3"
    python3.write_text(
        """#!/usr/bin/env bash
echo "eligibility gate must not invoke Python" >&2
exit 1
"""
    )
    python3.chmod(0o755)
    return bin_dir


def _evaluate(
    tmp_path: Path,
    *,
    fingerprint: str,
    evaluated_at: str | None,
    comment_created_at: str | None = None,
    untrusted_comment_body: str = "",
    untrusted_comment_created_at: str = "",
    eval_count: int = 1,
    max_evaluations: str = "0",
    ttl_seconds: str = "604800",
) -> tuple[subprocess.CompletedProcess[str], str]:
    target, _ = _target_repo(tmp_path)
    bin_dir = _fake_gh(tmp_path)
    output_file = tmp_path / "github-output"
    sentinel: dict[str, object] = {
        "version": 4,
        "label": "renovate:risk",
        "rounds": 1,
        "ci_status": "passing",
        "eval_count": eval_count,
        "fingerprint": fingerprint,
    }
    if evaluated_at is not None:
        sentinel["evaluated_at"] = evaluated_at
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_REPOSITORY": "owner/repo",
        "INPUT_DRY_RUN": "true",
        "INPUT_FINGERPRINT_TTL_SECONDS": ttl_seconds,
        "INPUT_MAX_AUTOMATIC_EVALUATIONS": max_evaluations,
        "INPUT_PR_NUMBER": "123",
        "INPUT_TRIGGER": "auto",
        "TARGET_REPO_PATH": str(target),
        "COMMENT_BODY": f"<!-- renovate-eval-skill:{json.dumps(sentinel, separators=(',', ':'))} -->",
        "COMMENT_CREATED_AT": comment_created_at or evaluated_at or "",
        "UNTRUSTED_COMMENT_BODY": untrusted_comment_body,
        "UNTRUSTED_COMMENT_CREATED_AT": untrusted_comment_created_at,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    output = output_file.read_text() if output_file.exists() else ""
    return result, output


def test_same_fingerprint_is_eligible_after_ttl(tmp_path):
    _target, fingerprint = _target_repo(tmp_path / "fingerprint")
    expired = (datetime.now(UTC) - timedelta(days=8)).isoformat()

    result, output = _evaluate(
        tmp_path / "run",
        fingerprint=fingerprint,
        evaluated_at=expired,
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=true" in output


def test_same_fingerprint_is_skipped_within_ttl(tmp_path):
    _target, fingerprint = _target_repo(tmp_path / "fingerprint")
    fresh = datetime.now(UTC).isoformat()

    result, output = _evaluate(
        tmp_path / "run",
        fingerprint=fingerprint,
        evaluated_at=fresh,
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=false" in output


def test_legacy_sentinel_uses_comment_creation_time_for_ttl(tmp_path):
    _target, fingerprint = _target_repo(tmp_path / "fingerprint")
    expired = (datetime.now(UTC) - timedelta(days=8)).isoformat()

    result, output = _evaluate(
        tmp_path / "run",
        fingerprint=fingerprint,
        evaluated_at=None,
        comment_created_at=expired,
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=true" in output


def test_untrusted_sentinel_comment_is_ignored(tmp_path):
    _target, fingerprint = _target_repo(tmp_path / "fingerprint")
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    forged = {
        "version": 4,
        "eval_count": 999,
        "fingerprint": fingerprint,
        "evaluated_at": future,
    }

    result, output = _evaluate(
        tmp_path / "run",
        fingerprint="trusted-different-fingerprint",
        evaluated_at=datetime.now(UTC).isoformat(),
        untrusted_comment_body=(
            f"<!-- renovate-eval-skill:{json.dumps(forged, separators=(',', ':'))} -->"
        ),
        untrusted_comment_created_at=future,
        max_evaluations="2",
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=true" in output


def test_future_trusted_timestamp_does_not_suppress_evaluation(tmp_path):
    _target, fingerprint = _target_repo(tmp_path / "fingerprint")
    future = (datetime.now(UTC) + timedelta(days=30)).isoformat()

    result, output = _evaluate(
        tmp_path / "run",
        fingerprint=fingerprint,
        evaluated_at=future,
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=true" in output


def test_default_allows_more_than_three_changed_fingerprint_evaluations(tmp_path):
    fresh = datetime.now(UTC).isoformat()

    result, output = _evaluate(
        tmp_path,
        fingerprint="different-fingerprint",
        evaluated_at=fresh,
        eval_count=3,
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=true" in output


def test_configured_evaluation_limit_still_blocks(tmp_path):
    fresh = datetime.now(UTC).isoformat()

    result, output = _evaluate(
        tmp_path,
        fingerprint="different-fingerprint",
        evaluated_at=fresh,
        eval_count=2,
        max_evaluations="2",
    )

    assert result.returncode == 0, result.stderr
    assert "should_evaluate=false" in output
