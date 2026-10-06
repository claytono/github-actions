"""Fingerprint Renovate PR diffs with git instead of the GitHub diff API.

GitHub's pull-request diff API refuses diffs over 20,000 lines (HTTP 406), so
PRs that vendor large dependencies could never be fingerprinted. git has no
such limit and produces the same added and removed lines as the API, so the
inventory and the evaluator both fingerprint from a cached partial clone.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterable
from typing import Any

from .common import compute_fingerprint_bytes, isolated_git_env

Run = Callable[..., subprocess.CompletedProcess]

# Authenticate through gh, which reads GH_TOKEN or the user's gh login, so no
# credential is written to the clone's configuration.
GIT_AUTH = [
    "-c",
    "credential.helper=",
    "-c",
    "credential.helper=!gh auth git-credential",
]


def default_cache_root() -> str:
    """Directory holding cached clones, overridable for CI or tests."""
    return os.environ.get("RENOVATE_EVAL_GIT_CACHE") or os.path.join(
        tempfile.gettempdir(), "renovate-eval-git"
    )


class GitFingerprinter:
    """Compute PR fingerprints from a partial clone of one repository."""

    def __init__(
        self,
        repository: str,
        *,
        run: Run | None = None,
        cache_root: str | None = None,
        remote_url: str | None = None,
    ) -> None:
        self.repository = repository
        self.remote_url = remote_url or f"https://github.com/{repository}.git"
        self.run = run or subprocess.run
        self.path = os.path.join(
            cache_root or default_cache_root(), *repository.split("/")
        )
        self._lock = threading.Lock()
        self._cloned = False

    def _execute(self, command: list[str], timeout: int) -> subprocess.CompletedProcess:
        # A missing git or a stalled GitHub makes fingerprints unknown, which
        # leaves PRs unqualified, instead of aborting the whole inventory.
        try:
            return self.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
                env=isolated_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return subprocess.CompletedProcess(
                command, 1, stdout=b"", stderr=str(exc).encode()
            )

    def _git(self, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        return self._execute(["git", *GIT_AUTH, "-C", self.path, *args], timeout)

    def _ensure_clone(self) -> bool:
        if self._cloned or os.path.isdir(os.path.join(self.path, ".git")):
            self._cloned = True
            return True
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
        except OSError:
            return False
        result = self._execute(
            [
                "git",
                *GIT_AUTH,
                "clone",
                "--quiet",
                "--filter=blob:none",
                "--no-checkout",
                self.remote_url,
                self.path,
            ],
            600,
        )
        self._cloned = result.returncode == 0
        return self._cloned

    def prefetch(self, prs: Iterable[dict[str, Any]]) -> bool:
        """Fetch every base branch and PR head in one call."""
        prs = list(prs)
        if not prs:
            return True
        with self._lock:
            if not self._ensure_clone():
                return False
            bases = sorted({str(pr.get("baseRefName") or "") for pr in prs} - {""})
            refspecs = [
                f"+refs/heads/{base}:refs/remotes/origin/{base}" for base in bases
            ]
            refspecs += [
                f"+refs/pull/{int(pr['number'])}/head:refs/pr/{int(pr['number'])}"
                for pr in prs
            ]
            return (
                self._git(
                    "fetch", "--quiet", "origin", *refspecs, timeout=600
                ).returncode
                == 0
            )

    def _fetched_head(self, number: int) -> str | None:
        result = self._git(
            "rev-parse", "--verify", "--quiet", f"refs/pr/{number}^{{commit}}"
        )
        return result.stdout.decode().strip() if result.returncode == 0 else None

    def __call__(self, pr: dict[str, Any]) -> str | None:
        """Fingerprint one PR at its recorded head, or None if that is impossible.

        The PR head must match the head the caller recorded; a newer push makes
        the fingerprint unknown rather than describing a different commit.
        """
        number = int(pr["number"])
        base = str(pr.get("baseRefName") or "")
        head = str(pr.get("headRefOid") or "")
        if not base or not head:
            return None
        if self._fetched_head(number) != head and (
            not self.prefetch([pr]) or self._fetched_head(number) != head
        ):
            return None
        diff = self._git(
            "diff", "--no-ext-diff", "--no-color", f"origin/{base}...{head}"
        )
        if diff.returncode != 0:
            return None
        return compute_fingerprint_bytes(diff.stdout)


def read_repository(*, run: Run | None = None) -> str | None:
    """The owner/name of the repository gh is pointed at, or None."""
    from .common import gh_repo_view_command

    run = run or subprocess.run
    try:
        result = run(
            gh_repo_view_command("--json", "nameWithOwner", "-q", ".nameWithOwner"),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def read_pr_ref(
    pr_number: int | str, *, run: Run | None = None
) -> dict[str, Any] | None:
    """A PR's number, base branch, and head commit, or None."""
    import json

    run = run or subprocess.run
    try:
        result = run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,baseRefName,headRefOid",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        ref = json.loads(result.stdout) if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return ref if isinstance(ref, dict) and ref.get("headRefOid") else None


def evaluated_fingerprint(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    patch_path: str,
    fingerprinter: Callable[[dict[str, Any]], str | None],
) -> str | None:
    """Fingerprint exactly what an evaluation saw, or None.

    ``before`` and ``after`` are the PR refs read around fetching its data.
    If the head moved in between, the fetched data may mix commits, so there
    is no fingerprint and a later pass evaluates the PR again. Otherwise the
    fetched patch, when GitHub returned one, is the evaluated artifact: its
    hash is the fingerprint, and the git fingerprint of the pinned head must
    agree with it. Without a patch, as for diffs over GitHub's limit, the git
    fingerprint of the unchanged head is used.
    """
    from .common import compute_fingerprint

    if not before or not after or before.get("headRefOid") != after.get("headRefOid"):
        return None
    git_fingerprint = fingerprinter(before)
    if not os.path.isfile(patch_path):
        return git_fingerprint
    patch_fingerprint = compute_fingerprint(patch_path)
    if git_fingerprint is not None and git_fingerprint != patch_fingerprint:
        return None
    return patch_fingerprint
