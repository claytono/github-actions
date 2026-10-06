"""Tests for lib/check_ci.py."""

from __future__ import annotations

import json
import os
import subprocess

from lib.check_ci import check_ci, check_ci_once, fetch_failed_logs, wait_for_ci


class TestCheckCiOnce:
    def test_returns_output_and_code(self, monkeypatch):
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout="all pass", stderr=""
            ),
        )
        output, code = check_ci_once(1234)
        assert code == 0
        assert "all pass" in output

    def test_failure_code(self, monkeypatch):
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 1, stdout="", stderr="fail"
            ),
        )
        _output, code = check_ci_once(1234)
        assert code == 1

    def test_excludes_current_github_run_from_snapshot(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "Renovate Evaluation",
                    "state": "PENDING",
                    "bucket": "pending",
                    "workflow": "Renovate Evaluation",
                    "link": "https://github.com/foo/bar/actions/runs/123/jobs/456",
                },
                {
                    "name": "build",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "workflow": "CI",
                    "link": "https://github.com/foo/bar/actions/runs/122/jobs/455",
                },
            ]
        )

        def mock_run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 8, stdout=checks, stderr="")

        monkeypatch.setattr("lib.check_ci.subprocess.run", mock_run)

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert "Renovate Evaluation" not in output
        assert "build" in output
        assert code == 0

    def test_excludes_current_run_link_without_trailing_slash(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "Renovate Evaluation",
                    "state": "PENDING",
                    "bucket": "pending",
                    "workflow": "Renovate Evaluation",
                    "link": "https://github.com/foo/bar/actions/runs/123",
                },
                {
                    "name": "other run",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "workflow": "CI",
                    "link": "https://github.com/foo/bar/actions/runs/1234",
                },
            ]
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 8, stdout=checks, stderr=""
            ),
        )

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert "Renovate Evaluation" not in output
        assert "other run" in output
        assert code == 0

    def test_preserves_completed_checks_from_current_run(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "Renovate Evaluation",
                    "state": "PENDING",
                    "bucket": "pending",
                    "workflow": "Renovate Evaluation",
                    "link": "https://github.com/foo/bar/actions/runs/123/jobs/456",
                },
                {
                    "name": "test",
                    "state": "SUCCESS",
                    "bucket": "pass",
                    "workflow": "Renovate Evaluation",
                    "link": "https://github.com/foo/bar/actions/runs/123/jobs/455",
                },
            ]
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 8, stdout=checks, stderr=""
            ),
        )

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert "\nRenovate Evaluation\tPENDING" not in output
        assert "\ntest\tSUCCESS" in output
        assert code == 0

    def test_filtered_snapshot_preserves_failure(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "test",
                    "state": "FAILURE",
                    "bucket": "fail",
                    "workflow": "CI",
                    "link": "https://github.com/foo/bar/actions/runs/122/jobs/455",
                }
            ]
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 1, stdout=checks, stderr=""
            ),
        )

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert "test" in output
        assert code == 1

    def test_filtered_snapshot_preserves_other_pending_checks(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "integration",
                    "state": "PENDING",
                    "bucket": "pending",
                    "workflow": "CI",
                    "link": "https://github.com/foo/bar/actions/runs/122/jobs/455",
                }
            ]
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 8, stdout=checks, stderr=""
            ),
        )

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert "integration" in output
        assert code == 8

    def test_filtered_snapshot_handles_only_current_run(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "Renovate Evaluation",
                    "state": "PENDING",
                    "bucket": "pending",
                    "workflow": "Renovate Evaluation",
                    "link": "https://github.com/foo/bar/actions/runs/123/jobs/456",
                }
            ]
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 8, stdout=checks, stderr=""
            ),
        )

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert output == "No checks outside the current GitHub Actions run."
        assert code == 0

    def test_filtered_snapshot_preserves_unparseable_output(self, monkeypatch):
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                cmd, 1, stdout="not json", stderr="gh failed"
            ),
        )

        output, code = check_ci_once(1234, exclude_run_id="123")

        assert output == "not jsongh failed"
        assert code == 1


class TestWaitForCi:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/bin/timeout" if cmd == "timeout" else None
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout="done", stderr=""
            ),
        )
        _output, code = wait_for_ci(1234, timeout=60)
        assert code == 0

    def test_timeout_exit_124(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/bin/timeout" if cmd == "timeout" else None
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 124, stdout="", stderr=""
            ),
        )
        output, code = wait_for_ci(1234, timeout=60)
        assert code == 2
        assert "timed out" in output

    def test_no_timeout_cmd(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda cmd: None)
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout="ok", stderr=""
            ),
        )
        _output, code = wait_for_ci(1234)
        assert code == 0

    def test_failure_not_timeout(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which", lambda cmd: "/usr/bin/timeout" if cmd == "timeout" else None
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 1, stdout="failed", stderr=""
            ),
        )
        _output, code = wait_for_ci(1234)
        assert code == 1


class TestFetchFailedLogs:
    def test_no_failures(self, monkeypatch):
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout="[]", stderr=""
            ),
        )
        assert fetch_failed_logs(1234) == ""

    def test_gh_error(self, monkeypatch):
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, stdout="", stderr=""),
        )
        assert fetch_failed_logs(1234) == ""

    def test_with_failure(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "build",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://github.com/foo/bar/actions/runs/12345/jobs/67890",
                },
            ]
        )

        def mock_run(cmd, **kw):
            if "--json" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout=checks, stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="log output", stderr="")

        monkeypatch.setattr("lib.check_ci.subprocess.run", mock_run)
        result = fetch_failed_logs(1234)
        assert "### build" in result

    def test_no_run_id_in_url(self, monkeypatch):
        checks = json.dumps(
            [
                {
                    "name": "build",
                    "conclusion": "FAILURE",
                    "detailsUrl": "https://example.com/no-run-id",
                },
            ]
        )
        monkeypatch.setattr(
            "lib.check_ci.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=checks, stderr=""
            ),
        )
        result = fetch_failed_logs(1234)
        # No ### section since no run ID extractable
        assert "### build" not in result


class TestCheckCi:
    def test_non_wait(self, monkeypatch, tmp_dir):
        monkeypatch.setattr(
            "lib.check_ci.check_ci_once", lambda pr: ("checks output", 0)
        )
        monkeypatch.setattr("lib.check_ci.fetch_failed_logs", lambda pr: "")
        outfile = os.path.join(tmp_dir, "ci.md")
        code = check_ci(1234, output_file=outfile)
        assert code == 0
        assert os.path.isfile(outfile)

    def test_wait_mode(self, monkeypatch, tmp_dir):
        monkeypatch.setattr(
            "lib.check_ci.wait_for_ci",
            lambda pr, timeout, exclude_run_id=None: ("waited", 0),
        )
        monkeypatch.setattr("lib.check_ci.fetch_failed_logs", lambda pr: "")
        outfile = os.path.join(tmp_dir, "ci.md")
        code = check_ci(1234, wait=True, timeout=60, output_file=outfile)
        assert code == 0

    def test_stdout_when_no_file(self, monkeypatch, capsys):
        monkeypatch.setattr(
            "lib.check_ci.check_ci_once", lambda pr: ("stdout output", 0)
        )
        monkeypatch.setattr("lib.check_ci.fetch_failed_logs", lambda pr: "")
        check_ci(1234)
        assert "stdout output" in capsys.readouterr().out

    def test_non_wait_snapshot_excludes_current_run(self, monkeypatch, tmp_dir):
        calls = []

        def mock_check_once(pr, exclude_run_id=None):
            calls.append((pr, exclude_run_id))
            return "snapshot", 0

        monkeypatch.setattr("lib.check_ci.check_ci_once", mock_check_once)
        monkeypatch.setattr("lib.check_ci.fetch_failed_logs", lambda pr: "")

        check_ci(
            1234,
            wait=False,
            output_file=os.path.join(tmp_dir, "ci.md"),
            exclude_run_id="123",
        )

        assert calls == [(1234, "123")]


def test_wait_with_exclude_polls_until_other_checks_finish(monkeypatch):
    from lib import check_ci as mod

    results = iter([("pending", 8), ("pending", 8), ("all done", 0)])
    calls = []
    monkeypatch.setattr(
        mod,
        "check_ci_once",
        lambda pr, exclude_run_id=None, pending_first=False: (
            calls.append((exclude_run_id, pending_first)) or next(results)
        ),
    )
    sleeps = []

    output, code = mod.wait_for_ci(
        12, timeout=600, exclude_run_id="99", sleep=sleeps.append, monotonic=lambda: 0
    )

    assert (output, code) == ("all done", 0)
    assert calls == [("99", True)] * 3
    assert sleeps == [15, 15]


def test_wait_with_exclude_times_out(monkeypatch):
    from lib import check_ci as mod

    monkeypatch.setattr(
        mod,
        "check_ci_once",
        lambda pr, exclude_run_id=None, pending_first=False: ("pending", 8),
    )
    clock = iter([0, 5, 700])

    output, code = mod.wait_for_ci(
        12,
        timeout=600,
        exclude_run_id="99",
        sleep=lambda _: None,
        monotonic=lambda: next(clock),
    )

    assert code == 2
    assert "timed out after 600s" in output


def test_check_ci_wait_passes_exclude_run_id(monkeypatch, tmp_path):
    from lib import check_ci as mod

    seen = {}

    def fake_wait(pr, timeout, exclude_run_id=None):
        seen["exclude"] = exclude_run_id
        return "ok", 0

    monkeypatch.setattr(mod, "wait_for_ci", fake_wait)
    monkeypatch.setattr(mod, "fetch_failed_logs", lambda pr: "")

    assert (
        mod.check_ci(
            12, wait=True, output_file=str(tmp_path / "ci.md"), exclude_run_id="99"
        )
        == 0
    )
    assert seen["exclude"] == "99"


def test_wait_sleeps_at_most_the_remaining_time(monkeypatch):
    from lib import check_ci as mod

    monkeypatch.setattr(
        mod,
        "check_ci_once",
        lambda pr, exclude_run_id=None, pending_first=False: ("pending", 8),
    )
    clock = iter([0, 595, 600])
    sleeps = []

    output, code = mod.wait_for_ci(
        12,
        timeout=600,
        exclude_run_id="99",
        sleep=sleeps.append,
        monotonic=lambda: next(clock),
    )

    assert code == 2
    assert "timed out after 600s" in output
    assert sleeps == [5]


def test_pending_first_reports_pending_even_with_a_failure(monkeypatch):
    import json
    import subprocess

    from lib import check_ci as mod

    checks = [
        {
            "name": "a",
            "bucket": "fail",
            "state": "FAILURE",
            "workflow": "w",
            "link": "x",
        },
        {
            "name": "b",
            "bucket": "pending",
            "state": "PENDING",
            "workflow": "w",
            "link": "y",
        },
    ]
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 1, stdout=json.dumps(checks), stderr=""
        ),
    )

    assert mod.check_ci_once(1, exclude_run_id="99")[1] == 1
    assert mod.check_ci_once(1, exclude_run_id="99", pending_first=True)[1] == 8
