"""Tests for lib/fetch_pr_data.py."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from lib.fetch_pr_data import (
    detect_repo,
    fetch_body,
    fetch_files,
    fetch_metadata,
    fetch_pr_data,
    fetch_related_issues,
)


class TestDetectRepo:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout="owner/repo\n", stderr=""
            ),
        )
        assert detect_repo() == "owner/repo"

    def test_failure(self, monkeypatch):
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=""),
        )
        with pytest.raises(SystemExit):
            detect_repo()

    def test_timeout_exits_cleanly(self, monkeypatch):
        def timeout_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", timeout_run)

        with pytest.raises(SystemExit, match="ERROR: Not in a git repository"):
            detect_repo()


class TestFetchPrData:
    def test_writes_files(self, monkeypatch, tmp_dir):
        monkeypatch.setattr("lib.fetch_pr_data.detect_repo", lambda: "owner/repo")
        monkeypatch.setattr(
            "lib.fetch_pr_data.run_diff", lambda pr, out: open(out, "w").close()
        )

        data = {
            "number": 1,
            "title": "T",
            "author": {"login": "b"},
            "state": "OPEN",
            "url": "u",
            "baseRefName": "main",
            "headRefName": "h",
            "additions": 0,
            "deletions": 0,
            "changedFiles": 0,
        }

        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "body" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="body text", stderr=""
                )
            if "files" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps({"files": []}), stderr=""
                )
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(data), stderr=""
            )

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)
        fetch_pr_data(1, tmp_dir)
        assert os.path.isfile(os.path.join(tmp_dir, "pr-data.md"))
        assert os.path.isfile(os.path.join(tmp_dir, "pr-diff.patch"))


class TestFetchMetadata:
    def test_success(self, monkeypatch):
        data = {
            "number": 42,
            "title": "Update foo",
            "author": {"login": "bot"},
            "state": "OPEN",
            "url": "https://github.com/a/b/pull/42",
            "baseRefName": "main",
            "headRefName": "renovate/foo",
            "additions": 10,
            "deletions": 5,
            "changedFiles": 2,
        }
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=json.dumps(data), stderr=""
            ),
        )
        result = fetch_metadata(42)
        assert "Update foo" in result
        assert "+10" in result

    def test_failure(self, monkeypatch):
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 1, stdout="", stderr="err"
            ),
        )
        result = fetch_metadata(42)
        assert "ERROR" in result

    def test_timeout_returns_error_markdown(self, monkeypatch):
        def timeout_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", timeout_run)

        result = fetch_metadata(42)

        assert "ERROR: Failed to fetch PR metadata" in result
        assert "timed out" in result


class TestFetchBody:
    def test_error(self, monkeypatch):
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 1, stdout="", stderr="err"
            ),
        )
        result = fetch_body(42)
        assert "ERROR" in result

    def test_timeout_returns_error_markdown(self, monkeypatch):
        def timeout_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", timeout_run)

        result = fetch_body(42)

        assert "ERROR: Failed to fetch PR body" in result
        assert "timed out" in result

    def test_strips_html_comments(self, monkeypatch):
        body = "Before <!-- comment --> After"
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=body, stderr=""
            ),
        )
        result = fetch_body(42)
        assert "Before" in result
        assert "After" in result
        assert "comment" not in result

    def test_multiline_html_comment(self, monkeypatch):
        body = "Before <!-- multi\nline\ncomment --> After"
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=body, stderr=""
            ),
        )
        result = fetch_body(42)
        assert "multi" not in result


class TestFetchFiles:
    def test_with_diff_offsets(self, monkeypatch, tmp_dir):
        diff_path = os.path.join(tmp_dir, "test.patch")
        with open(diff_path, "wb") as f:
            f.write(b"diff --git a/foo.txt b/foo.txt\n+added\n")

        files_data = {"files": [{"path": "foo.txt", "additions": 1, "deletions": 0}]}
        monkeypatch.setattr(
            "lib.fetch_pr_data.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=json.dumps(files_data), stderr=""
            ),
        )
        result = fetch_files(42, diff_path)
        assert "foo.txt" in result
        assert "[L1]" in result

    def test_timeout_returns_error_markdown(self, monkeypatch, tmp_dir):
        def timeout_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", timeout_run)

        result = fetch_files(42, os.path.join(tmp_dir, "missing.patch"))

        assert "ERROR: Failed to fetch files data" in result
        assert "timed out" in result


class TestFetchRelatedIssues:
    def test_no_issues(self, monkeypatch):
        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)
        result = fetch_related_issues(42, "owner/repo")
        assert "No linked" in result

    def test_closing_issues_timeout_returns_error_markdown(self, monkeypatch):
        def timeout_run(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", timeout_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "ERROR: Failed to fetch linked issues" in result
        assert "timed out" in result

    def test_closing_issues_nonzero_exit_returns_error_markdown(self, monkeypatch):
        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="gh pr view failed"
                )
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "ERROR: Failed to fetch linked issues" in result
        assert "gh pr view failed" in result
        assert "No linked or referencing issues found." not in result

    def test_closing_issue_detail_nonzero_exit_returns_error_markdown(
        self, monkeypatch
    ):
        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="99\n", stderr="")
            if "issue" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="gh issue view failed"
                )
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "ERROR: Failed to fetch issue #99" in result
        assert "gh issue view failed" in result

    def test_issue_body_timeout_preserves_related_issue_summary(self, monkeypatch):
        xrefs = json.dumps(
            [
                {"number": 10, "title": "Related", "state": "OPEN", "type": "Issue"},
            ]
        )

        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout=xrefs, stderr="")
            if "issue" in cmd_str and "view" in cmd_str:
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "Issue #10: Related [OPEN]" in result
        assert "ERROR: Failed to fetch issue #10 body" in result
        assert "timed out" in result

    def test_cross_reference_issue_body_nonzero_exit_preserves_summary(
        self, monkeypatch
    ):
        xrefs = json.dumps(
            [
                {"number": 10, "title": "Related", "state": "OPEN", "type": "Issue"},
            ]
        )

        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout=xrefs, stderr="")
            if "issue" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="gh issue view failed"
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "Issue #10: Related [OPEN]" in result
        assert "ERROR: Failed to fetch issue #10 body" in result
        assert "gh issue view failed" in result

    def test_cross_reference_timeout_returns_error_markdown(self, monkeypatch):
        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "ERROR: Failed to fetch cross-references" in result
        assert "timed out" in result
        assert "No linked or referencing issues found." not in result

    def test_cross_reference_nonzero_exit_returns_error_markdown(self, monkeypatch):
        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr="gh api failed"
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "ERROR: Failed to fetch cross-references" in result
        assert "gh api failed" in result
        assert "No linked or referencing issues found." not in result

    def test_cross_reference_bad_json_returns_error_markdown(self, monkeypatch):
        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="{bad json", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)

        result = fetch_related_issues(42, "owner/repo")

        assert "ERROR: Failed to parse cross-references" in result
        assert "No linked or referencing issues found." not in result

    def test_with_cross_references(self, monkeypatch):
        xrefs = json.dumps(
            [
                {"number": 10, "title": "Related", "state": "OPEN", "type": "Issue"},
            ]
        )

        def mock_run(cmd, **kw):
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout=xrefs, stderr="")
            if "issue" in cmd_str and "view" in cmd_str:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="Issue body text", stderr=""
                )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)
        result = fetch_related_issues(42, "owner/repo")
        assert "Related" in result

    def test_with_closing_issue(self, monkeypatch):
        call_count = [0]

        def mock_run(cmd, **kw):
            call_count[0] += 1
            cmd_str = " ".join(str(c) for c in cmd)
            if "closingIssuesReferences" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="99\n", stderr="")
            if "issue" in cmd_str and "view" in cmd_str:
                data = {
                    "number": 99,
                    "title": "Fix bug",
                    "body": "Details",
                    "state": "OPEN",
                }
                return subprocess.CompletedProcess(
                    cmd, 0, stdout=json.dumps(data), stderr=""
                )
            if "timeline" in cmd_str:
                return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("lib.fetch_pr_data.subprocess.run", mock_run)
        result = fetch_related_issues(42, "owner/repo")
        assert "Issue #99" in result
