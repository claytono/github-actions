"""Tests for git-based PR fingerprints against a real repository."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from lib.common import compute_fingerprint_bytes
from lib.git_fingerprint import GitFingerprinter, default_cache_root


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def origin(tmp_path: Path) -> dict:
    """A repository with main, one PR head, and the expected fingerprint."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "version.txt").write_text("old\n")
    _git(repo, "add", "version.txt")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "renovate/update")
    (repo / "version.txt").write_text("new\n")
    _git(repo, "commit", "-q", "-am", "update")
    head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/pull/7/head", head)
    _git(repo, "checkout", "-q", "main")
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", f"main...{head}"],
        cwd=repo,
        check=True,
        capture_output=True,
    ).stdout
    return {"path": repo, "head": head, "fingerprint": compute_fingerprint_bytes(diff)}


def _fingerprinter(origin: dict, tmp_path: Path) -> GitFingerprinter:
    return GitFingerprinter(
        "owner/repo",
        cache_root=str(tmp_path / "cache"),
        remote_url=str(origin["path"]),
    )


def _pr(origin: dict, **overrides) -> dict:
    return {
        "number": 7,
        "baseRefName": "main",
        "headRefOid": origin["head"],
        **overrides,
    }


def test_fingerprint_matches_the_diff_of_the_pr_head(origin, tmp_path):
    fingerprint = _fingerprinter(origin, tmp_path)

    assert fingerprint.prefetch([_pr(origin)]) is True
    assert fingerprint(_pr(origin)) == origin["fingerprint"]


def test_fingerprint_fetches_on_demand_without_prefetch(origin, tmp_path):
    assert _fingerprinter(origin, tmp_path)(_pr(origin)) == origin["fingerprint"]


def test_fingerprint_refetches_a_stale_cached_head(origin, tmp_path):
    fingerprint = _fingerprinter(origin, tmp_path)
    assert fingerprint(_pr(origin)) == origin["fingerprint"]

    repo = origin["path"]
    _git(repo, "checkout", "-q", "renovate/update")
    (repo / "version.txt").write_text("newer\n")
    _git(repo, "commit", "-q", "-am", "rebase")
    new_head = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/pull/7/head", new_head)
    _git(repo, "checkout", "-q", "main")

    assert fingerprint(_pr(origin, headRefOid=new_head)) not in (
        None,
        origin["fingerprint"],
    )


def test_fingerprint_is_unknown_when_the_head_moved(origin, tmp_path):
    assert _fingerprinter(origin, tmp_path)(_pr(origin, headRefOid="0" * 40)) is None


@pytest.mark.parametrize("missing", ["baseRefName", "headRefOid"])
def test_fingerprint_is_unknown_without_base_or_head(origin, tmp_path, missing):
    assert _fingerprinter(origin, tmp_path)(_pr(origin, **{missing: ""})) is None


def test_fingerprint_is_unknown_when_the_diff_fails(origin, tmp_path):
    assert _fingerprinter(origin, tmp_path)(_pr(origin, baseRefName="missing")) is None


def test_clone_failure_makes_fingerprints_unknown(tmp_path):
    fingerprint = GitFingerprinter(
        "owner/repo",
        cache_root=str(tmp_path / "cache"),
        remote_url=str(tmp_path / "does-not-exist"),
    )

    assert fingerprint.prefetch([{"number": 1, "baseRefName": "main"}]) is False
    assert (
        fingerprint({"number": 1, "baseRefName": "main", "headRefOid": "a" * 40})
        is None
    )


def test_prefetch_of_nothing_needs_no_clone(tmp_path):
    fingerprint = GitFingerprinter("owner/repo", cache_root=str(tmp_path / "cache"))

    assert fingerprint.prefetch([]) is True
    assert not (tmp_path / "cache").exists()


def test_existing_clone_is_reused(origin, tmp_path):
    _fingerprinter(origin, tmp_path).prefetch([_pr(origin)])
    reused = _fingerprinter(origin, tmp_path)
    commands = []
    real_run = reused.run

    def run(command, **kwargs):
        commands.append(command)
        return real_run(command, **kwargs)

    reused.run = run

    assert reused(_pr(origin)) == origin["fingerprint"]
    assert not any("clone" in command for command in commands)


def test_git_commands_authenticate_through_gh(origin, tmp_path):
    fingerprint = _fingerprinter(origin, tmp_path)
    commands = []
    real_run = fingerprint.run

    def run(command, **kwargs):
        commands.append(command)
        return real_run(command, **kwargs)

    fingerprint.run = run
    fingerprint(_pr(origin))

    assert commands
    for command in commands:
        assert "credential.helper=!gh auth git-credential" in command


def test_default_cache_root_honors_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("RENOVATE_EVAL_GIT_CACHE", str(tmp_path))
    assert default_cache_root() == str(tmp_path)

    monkeypatch.delenv("RENOVATE_EVAL_GIT_CACHE")
    assert default_cache_root().endswith("renovate-eval-git")


def _fake_gh(repo="owner/repo", view_rc=0, view_out=None, raise_on=None):
    view_out = (
        view_out if view_out is not None else '{"number": 7, "headRefOid": "abc"}'
    )

    def run(command, **kwargs):
        if raise_on and raise_on in command:
            raise OSError("boom")
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{repo}\n", stderr=""
            )
        if command[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                command, view_rc, stdout=view_out, stderr=""
            )
        raise AssertionError(f"unexpected command {command}")

    return run


def test_read_repository_and_pr_ref():
    from lib.git_fingerprint import read_pr_ref, read_repository

    assert read_repository(run=_fake_gh()) == "owner/repo"
    assert read_pr_ref(7, run=_fake_gh()) == {"number": 7, "headRefOid": "abc"}


@pytest.mark.parametrize(
    "run",
    [_fake_gh(repo=""), _fake_gh(raise_on="repo")],
)
def test_read_repository_is_none_when_unreadable(run):
    from lib.git_fingerprint import read_repository

    assert read_repository(run=run) is None


def test_read_repository_is_none_on_gh_failure():
    from lib.git_fingerprint import read_repository

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="no")

    assert read_repository(run=run) is None


@pytest.mark.parametrize(
    "run",
    [
        _fake_gh(view_rc=1),
        _fake_gh(view_out="not json"),
        _fake_gh(view_out='{"number": 7}'),
        _fake_gh(raise_on="view"),
    ],
)
def test_read_pr_ref_is_none_when_unreadable(run):
    from lib.git_fingerprint import read_pr_ref

    assert read_pr_ref(7, run=run) is None


REF = {"number": 7, "baseRefName": "main", "headRefOid": "abc"}


def _patch(tmp_path, content=b"+new\n-old\n"):
    path = tmp_path / "pr-diff.patch"
    path.write_bytes(content)
    return str(path), compute_fingerprint_bytes(content)


def test_evaluated_fingerprint_uses_the_fetched_patch_when_git_agrees(tmp_path):
    from lib.git_fingerprint import evaluated_fingerprint

    path, patch_fp = _patch(tmp_path)

    assert (
        evaluated_fingerprint(
            REF, REF, patch_path=path, fingerprinter=lambda pr: patch_fp
        )
        == patch_fp
    )


def test_evaluated_fingerprint_keeps_patch_when_git_is_unavailable(tmp_path):
    from lib.git_fingerprint import evaluated_fingerprint

    path, patch_fp = _patch(tmp_path)

    assert (
        evaluated_fingerprint(REF, REF, patch_path=path, fingerprinter=lambda pr: None)
        == patch_fp
    )


def test_evaluated_fingerprint_rejects_a_patch_that_disagrees_with_the_head(tmp_path):
    from lib.git_fingerprint import evaluated_fingerprint

    path, _ = _patch(tmp_path)

    assert (
        evaluated_fingerprint(
            REF, REF, patch_path=path, fingerprinter=lambda pr: "other"
        )
        is None
    )


def test_evaluated_fingerprint_uses_git_without_a_patch(tmp_path):
    from lib.git_fingerprint import evaluated_fingerprint

    seen = []

    def fingerprinter(pr):
        seen.append(pr)
        return "gitfp"

    assert (
        evaluated_fingerprint(
            REF, REF, patch_path=str(tmp_path / "missing"), fingerprinter=fingerprinter
        )
        == "gitfp"
    )
    assert seen == [REF]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (REF, {**REF, "headRefOid": "pushed"}),
        (None, REF),
        (REF, None),
    ],
)
def test_evaluated_fingerprint_is_none_when_the_head_moved_or_is_unknown(
    tmp_path, before, after
):
    from lib.git_fingerprint import evaluated_fingerprint

    path, patch_fp = _patch(tmp_path)

    assert (
        evaluated_fingerprint(
            before, after, patch_path=path, fingerprinter=lambda pr: patch_fp
        )
        is None
    )


@pytest.mark.parametrize(
    "error", [OSError("no git"), subprocess.TimeoutExpired("git", 600)]
)
def test_git_failures_make_fingerprints_unknown_instead_of_raising(tmp_path, error):
    def run(command, **kwargs):
        raise error

    fingerprint = GitFingerprinter(
        "owner/repo", run=run, cache_root=str(tmp_path / "cache")
    )
    pr = {"number": 1, "baseRefName": "main", "headRefOid": "a" * 40}

    assert fingerprint.prefetch([pr]) is False
    assert fingerprint(pr) is None


def test_unwritable_cache_makes_fingerprints_unknown(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    fingerprint = GitFingerprinter("owner/repo", cache_root=str(blocker / "cache"))

    assert fingerprint.prefetch([{"number": 1, "baseRefName": "main"}]) is False


def test_fingerprint_ignores_user_git_config(origin, tmp_path, monkeypatch):
    config = tmp_path / "gitconfig"
    config.write_text(
        "[color]\n\tui = always\n\tdiff = always\n[diff]\n\talgorithm = patience\n"
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))

    assert _fingerprinter(origin, tmp_path)(_pr(origin)) == origin["fingerprint"]


def test_isolated_git_env_disables_global_and_system_config(monkeypatch):
    import os

    from lib.common import isolated_git_env

    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/somewhere/gitconfig")
    monkeypatch.setenv("GH_TOKEN", "set-after-import")
    env = isolated_git_env()

    assert env["GIT_CONFIG_GLOBAL"] == os.devnull
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GH_TOKEN"] == "set-after-import"
