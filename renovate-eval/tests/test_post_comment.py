"""Tests for PR comment posting and updating."""

from __future__ import annotations

import subprocess

import pytest

import renovate_eval


def assert_only_trusted_authors(jq_expression):
    assert '.user.login == "github-actions[bot]"' in jq_expression
    for association in ("OWNER", "MEMBER", "COLLABORATOR"):
        assert f'.author_association == "{association}"' in jq_expression
    assert '.author_association == "CONTRIBUTOR"' not in jq_expression


def test_get_prev_eval_count_only_reads_trusted_author_comments(monkeypatch):
    commands = []

    def mock_run(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=(
                '<!-- renovate-eval-skill:{"version":4,"eval_count":2} -->\n'
            ),
            stderr="",
        )

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    assert renovate_eval._get_prev_eval_count(123) == 2
    jq_expression = commands[0][commands[0].index("--jq") + 1]
    assert_only_trusted_authors(jq_expression)


def test_post_comment_only_updates_trusted_author_comments(monkeypatch, tmp_dir):
    commands = []

    def mock_run(cmd, **kwargs):
        commands.append(cmd)
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="owner/repo\n", stderr=""
            )
        if cmd[:2] == ["gh", "api"] and "issues/123/comments" in cmd[2]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout='{"id":456,"body":"old"}\n', stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    renovate_eval._post_comment(123, "new body", tmp_dir)

    lookup = next(
        cmd
        for cmd in commands
        if cmd[:2] == ["gh", "api"] and "issues/123/comments" in cmd[2]
    )
    jq_expression = lookup[lookup.index("--jq") + 1]
    assert_only_trusted_authors(jq_expression)
    assert any(
        cmd[:4] == ["gh", "api", "--method", "PATCH"]
        and "issues/comments/456" in cmd[4]
        for cmd in commands
    )


def test_post_comment_rejects_failed_repo_lookup_without_creating(monkeypatch, tmp_dir):
    commands = []

    def mock_run(cmd, **kwargs):
        commands.append(cmd)
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="repository unavailable"
            )
        if cmd[:3] == ["gh", "pr", "comment"]:
            raise AssertionError("must not create a comment without repo detection")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad route")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(RuntimeError, match="Failed to detect repo for comment posting"):
        renovate_eval._post_comment(123, "body", tmp_dir)

    assert all(cmd[:3] != ["gh", "pr", "comment"] for cmd in commands)


def test_post_comment_rejects_comment_search_failure_without_creating(
    monkeypatch, tmp_dir
):
    commands = []

    def mock_run(cmd, **kwargs):
        commands.append(cmd)
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="owner/repo\n", stderr=""
            )
        if cmd[:2] == ["gh", "api"] and "issues/123/comments" in cmd[2]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="comments API failed"
            )
        if cmd[:3] == ["gh", "pr", "comment"]:
            raise AssertionError("must not create a comment when search failed")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(
        RuntimeError, match="Failed to find existing renovate-eval comment"
    ):
        renovate_eval._post_comment(123, "body", tmp_dir)

    assert all(cmd[:3] != ["gh", "pr", "comment"] for cmd in commands)


def test_post_comment_wraps_comment_search_timeout(monkeypatch, tmp_dir):
    def mock_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                cmd, 0, stdout="owner/repo\n", stderr=""
            )
        if cmd[:2] == ["gh", "api"] and "issues/123/comments" in cmd[2]:
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
        if cmd[:3] == ["gh", "pr", "comment"]:
            raise AssertionError("must not create a comment when search timed out")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(renovate_eval.subprocess, "run", mock_run)

    with pytest.raises(
        RuntimeError, match="Failed to find existing renovate-eval comment"
    ):
        renovate_eval._post_comment(123, "body", tmp_dir)
