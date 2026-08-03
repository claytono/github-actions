"""Tests for interactive session initialization."""

from __future__ import annotations

import argparse
import json
import subprocess

import pytest

import renovate_eval


@pytest.fixture
def run_init(monkeypatch, capsys):
    commands = []

    def fake_which(tool):
        return "/usr/bin/gh" if tool == "gh" else None

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:2] == ["gh", "api"]:
            return subprocess.CompletedProcess(command, 0, stdout="true\n", stderr="")
        if command[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(renovate_eval.shutil, "which", fake_which)
    monkeypatch.setattr(renovate_eval.subprocess, "run", fake_run)
    monkeypatch.setattr(renovate_eval, "get_repo_root", lambda: "/repo")

    def run(gh_pr_list_args):
        renovate_eval.cmd_init(
            argparse.Namespace(gh_pr_list_args=gh_pr_list_args)
        )
        return next(cmd for cmd in commands if cmd[:3] == ["gh", "pr", "list"])

    return run


def test_init_forwards_gh_pr_list_args_without_default_limit(run_init, capsys):
    pr_list_command = run_init('--label "renovate:safe" --limit 5')

    assert pr_list_command == [
        "gh",
        "pr",
        "list",
        "--author",
        "app/renovate",
        "--state",
        "open",
        "--json",
        "number,title,createdAt,autoMergeRequest,statusCheckRollup,labels",
        "--label",
        "renovate:safe",
        "--limit",
        "5",
    ]
    assert json.loads(capsys.readouterr().out)["prs"] == []


def test_init_prefers_user_scalar_filters(run_init):
    pr_list_command = run_init("-A octocat -s all -L 5")

    assert pr_list_command == [
        "gh",
        "pr",
        "list",
        "--json",
        "number,title,createdAt,autoMergeRequest,statusCheckRollup,labels",
        "-A",
        "octocat",
        "-s",
        "all",
        "-L",
        "5",
    ]


@pytest.mark.parametrize(
    ("gh_pr_list_args", "expected_tail"),
    [
        (
            "--author=octocat --state=all --limit=5",
            [
                "--json",
                "number,title,createdAt,autoMergeRequest,statusCheckRollup,labels",
                "--author=octocat",
                "--state=all",
                "--limit=5",
            ],
        ),
        (
            "-Aoctocat -sall -L5",
            [
                "--json",
                "number,title,createdAt,autoMergeRequest,statusCheckRollup,labels",
                "-Aoctocat",
                "-sall",
                "-L5",
            ],
        ),
        (
            "--app dependabot",
            [
                "--state",
                "open",
                "--json",
                "number,title,createdAt,autoMergeRequest,statusCheckRollup,labels",
                "--limit",
                "100",
                "--app",
                "dependabot",
            ],
        ),
    ],
)
def test_init_recognizes_alternate_scalar_filter_forms(
    run_init, gh_pr_list_args, expected_tail
):
    pr_list_command = run_init(gh_pr_list_args)

    assert pr_list_command == [
        "gh",
        "pr",
        "list",
        *expected_tail,
    ]


@pytest.mark.parametrize(
    "gh_pr_list_args",
    [
        "--json number",
        "--jq .",
        "-q .",
        "--template '{{.number}}'",
        "-t '{{.number}}'",
        "--web",
        "-w",
        "--repo owner/repo",
        "--repo=owner/repo",
        "-Rowner/repo",
        "--help",
        "-h",
    ],
)
def test_init_rejects_non_selection_arguments(run_init, gh_pr_list_args):
    with pytest.raises(SystemExit, match="Unsupported gh pr list argument"):
        run_init(gh_pr_list_args)


def test_init_rejects_malformed_gh_pr_list_args(run_init):

    with pytest.raises(SystemExit, match="Invalid --gh-pr-list-args"):
        run_init("--label 'renovate:safe")
