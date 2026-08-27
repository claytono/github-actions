"""Tests for machine-readable Renovate PR inventory."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest

import renovate_eval
from lib import inventory as inventory_module
from lib.inventory import build_inventory


NOW = datetime(2026, 8, 26, 16, 0, tzinfo=UTC)
DIFF = b"--- a/file\n+++ b/file\n-old\n+new\n"
FINGERPRINT = hashlib.sha256(b"-old\n+new\n").hexdigest()


def sentinel(
    *,
    label: str = "renovate:safe",
    fingerprint: str = FINGERPRINT,
    evaluated_at: str = "2026-08-26T15:00:00Z",
    version: int = 4,
) -> str:
    payload = {
        "version": version,
        "label": label,
        "rounds": 1,
        "ci_status": "passing",
        "eval_count": 1,
        "fingerprint": fingerprint,
        "evaluated_at": evaluated_at,
    }
    return f"<!-- renovate-eval-skill:{json.dumps(payload)} -->"


def pr_data(**overrides):
    data = {
        "number": 123,
        "title": "Update example to 2.0.0",
        "url": "https://github.com/claytono/infra/pull/123",
        "author": {"login": "renovate[bot]"},
        "state": "OPEN",
        "isDraft": False,
        "baseRefName": "main",
        "baseRefOid": "0" * 40,
        "headRefOid": "a" * 40,
        "changedFiles": 1,
        "files": [{"path": "kubernetes/example/values.yaml"}],
        "labels": [
            {"name": "renovate"},
            {"name": "renovate:evaluated"},
            {"name": "renovate:safe"},
        ],
        "mergeable": "MERGEABLE",
        "mergeStateStatus": "CLEAN",
        "autoMergeRequest": None,
        "comments": [
            {
                "author": {"login": "github-actions"},
                "body": sentinel(),
                "createdAt": "2026-08-26T15:00:01Z",
            }
        ],
    }
    data.update(overrides)
    return data


class FakeGh:
    def __init__(
        self,
        prs,
        *,
        checks=None,
        diffs=None,
        api_files=None,
        api_comments=None,
    ):
        self.prs = prs
        self.checks = checks or {
            pr["number"]: [{"name": "test", "bucket": "pass", "state": "SUCCESS"}]
            for pr in prs
        }
        self.diffs = diffs or {pr["number"]: DIFF for pr in prs}
        self.api_files = api_files or {}
        self.api_comments = api_comments or {}
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="claytono/infra\n", stderr=""
            )
        if command[:3] == ["gh", "pr", "view"]:
            number = int(command[3])
            pr = next((pr for pr in self.prs if pr["number"] == number), None)
            if pr is None:
                return subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="pull request not found"
                )
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(pr), stderr=""
            )
        if command[:3] == ["gh", "pr", "list"]:
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(self.prs), stderr=""
            )
        if command[:3] == ["gh", "pr", "checks"]:
            checks = self.checks[int(command[3])]
            if isinstance(checks, subprocess.CompletedProcess):
                return checks
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(checks), stderr=""
            )
        if command[:3] == ["gh", "pr", "diff"]:
            diff = self.diffs[int(command[3])]
            if isinstance(diff, subprocess.CompletedProcess):
                return diff
            return subprocess.CompletedProcess(command, 0, stdout=diff, stderr=b"")
        if command[:2] == ["gh", "api"]:
            endpoint = command[2]
            number = int(endpoint.split("/")[-2])
            source = (
                self.api_files if endpoint.endswith("/files") else self.api_comments
            )
            response = source[number]
            if isinstance(response, subprocess.CompletedProcess):
                return response
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps([response]), stderr=""
            )
        raise AssertionError(f"unexpected command: {command}")


class SettlingGh(FakeGh):
    """Serve sequenced lightweight observations before full classifications."""

    def __init__(self, observations, classifications, *, classification_checks=None):
        super().__init__(classifications)
        self.observations = list(observations)
        self.classifications = list(classifications)
        self.classification_checks = list(classification_checks or [])
        self._active_checks = None
        self._active_observation = None

    def __call__(self, command, **kwargs):
        if command[:3] == ["gh", "pr", "view"]:
            fields = command[command.index("--json") + 1]
            if fields in {
                "number,state,headRefOid",
                "number,state,baseRefName,baseRefOid,headRefOid",
            }:
                if self._active_observation is None:
                    observation = self.observations.pop(0)
                    self._active_observation = observation
                    self._active_checks = observation["checks"]
                    head_sha = observation["head_sha"]
                    state = observation.get("state", "OPEN")
                    base_ref = observation.get("base_ref", "main")
                    base_sha = observation.get("base_sha", "0" * 40)
                else:
                    observation = self._active_observation
                    self._active_observation = None
                    head_sha = observation.get(
                        "head_after_sha", observation["head_sha"]
                    )
                    state = observation.get(
                        "state_after", observation.get("state", "OPEN")
                    )
                    base_ref = observation.get(
                        "base_after_ref", observation.get("base_ref", "main")
                    )
                    base_sha = observation.get(
                        "base_after_sha", observation.get("base_sha", "0" * 40)
                    )
                self.commands.append(command)
                data = {
                    "number": int(command[3]),
                    "state": state,
                    "baseRefName": base_ref,
                    "baseRefOid": base_sha,
                    "headRefOid": head_sha,
                }
                return subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(data), stderr=""
                )
            self.prs = [self.classifications.pop(0)]
        if command[:3] == ["gh", "pr", "checks"] and self._active_checks is not None:
            checks = self._active_checks
            self._active_checks = None
            self.commands.append(command)
            if isinstance(checks, subprocess.CompletedProcess):
                return checks
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(checks), stderr=""
            )
        if command[:3] == ["gh", "pr", "checks"] and self.classification_checks:
            checks = self.classification_checks.pop(0)
            self.commands.append(command)
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(checks), stderr=""
            )
        return super().__call__(command, **kwargs)


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def inventory_for(pr, *, checks=None, diff=DIFF):
    check_map = None if checks is None else {pr["number"]: checks}
    fake_gh = FakeGh([pr], checks=check_map, diffs={pr["number"]: diff})
    result = build_inventory(pr_number=pr["number"], now=NOW, run=fake_gh)
    return result["prs"][0], fake_gh


def test_inventory_includes_all_prs_and_qualifies_current_safe_head():
    managed = pr_data(autoMergeRequest={"mergeMethod": "REBASE"})
    unevaluated = pr_data(
        number=124,
        title="Update another example",
        headRefOid="b" * 40,
        comments=[],
        labels=[{"name": "renovate"}],
    )
    fake_gh = FakeGh([managed, unevaluated])

    result = build_inventory(now=NOW, run=fake_gh)

    assert result["repository"] == "claytono/infra"
    assert result["evaluation_max_age_seconds"] == 604800
    assert [pr["number"] for pr in result["prs"]] == [123, 124]
    assert result["prs"][0]["automerge"] is True
    assert result["prs"][0]["safety_qualified"] is True
    assert result["prs"][0]["reasons"] == []
    assert result["prs"][0]["evaluation"] == {
        "state": "current",
        "label": "renovate:safe",
        "version": 4,
        "fingerprint": FINGERPRINT,
        "current_fingerprint": FINGERPRINT,
        "evaluated_at": "2026-08-26T15:00:00Z",
    }
    assert result["prs"][1]["safety_qualified"] is False
    assert "evaluation is missing" in result["prs"][1]["reasons"]

    list_command = next(
        command for command in fake_gh.commands if command[:3] == ["gh", "pr", "list"]
    )
    assert list_command[:5] == [
        "gh",
        "pr",
        "list",
        "--state",
        "open",
    ]
    assert "--app" not in list_command
    assert list_command[-2:] == ["--limit", "9999"]


def test_inventory_returns_only_aggregate_required_check_state():
    checks = [
        {
            "name": "Lint",
            "bucket": "pass",
            "state": "SUCCESS",
            "workflow": "CI",
            "link": "https://example.test/check",
        }
    ]

    record, _ = inventory_for(pr_data(), checks=checks)

    assert record["required_checks"] == {"state": "passing"}


def test_inventory_filters_non_renovate_prs_before_per_pr_reads():
    renovate_pr = pr_data()
    human_pr = pr_data(
        number=124,
        author={"login": "octocat"},
        headRefOid="b" * 40,
    )
    fake_gh = FakeGh([renovate_pr, human_pr])

    result = build_inventory(now=NOW, run=fake_gh)

    assert [pr["number"] for pr in result["prs"]] == [123]
    per_pr_commands = [
        command
        for command in fake_gh.commands
        if command[:3] in (["gh", "pr", "checks"], ["gh", "pr", "diff"])
    ]
    assert {int(command[3]) for command in per_pr_commands} == {123}


def test_targeted_inventory_fetches_and_classifies_only_requested_pr():
    first = pr_data()
    selected = pr_data(number=124, headRefOid="b" * 40)
    fake_gh = FakeGh([first, selected])

    result = build_inventory(pr_number=124, now=NOW, run=fake_gh)

    assert [pr["number"] for pr in result["prs"]] == [124]
    assert any(
        command[:4] == ["gh", "pr", "view", "124"]
        for command in fake_gh.commands
    )
    assert not any(
        command[:3] == ["gh", "pr", "list"] for command in fake_gh.commands
    )
    assert {
        int(command[3])
        for command in fake_gh.commands
        if command[:3] in (["gh", "pr", "checks"], ["gh", "pr", "diff"])
    } == {124}


def test_inventory_fetches_every_changed_path_when_graphql_files_are_truncated():
    graphql_files = [
        {"path": f"kubernetes/example/{index}.yaml"} for index in range(100)
    ]
    api_files = [
        {"filename": f"kubernetes/example/{index}.yaml"} for index in range(100)
    ] + [{"filename": "opentofu/example/main.tf"}]
    selected = pr_data(changedFiles=101, files=graphql_files)
    fake_gh = FakeGh([selected], api_files={123: api_files})

    record = build_inventory(pr_number=123, now=NOW, run=fake_gh)["prs"][0]

    assert len(record["files"]) == 101
    assert record["files"][-1] == "opentofu/example/main.tf"
    assert record["files_complete"] is True
    assert [
        command for command in fake_gh.commands if command[:2] == ["gh", "api"]
    ] == [
        [
            "gh",
            "api",
            "repos/claytono/infra/pulls/123/files",
            "--paginate",
            "--slurp",
        ]
    ]


def test_inventory_disqualifies_pr_when_changed_paths_cannot_be_completed():
    graphql_files = [
        {"path": f"kubernetes/example/{index}.yaml"} for index in range(100)
    ]
    failed_api = subprocess.CompletedProcess(
        ["gh", "api"], 1, stdout="", stderr="rate limited"
    )
    selected = pr_data(changedFiles=101, files=graphql_files)
    fake_gh = FakeGh([selected], api_files={123: failed_api})

    record = build_inventory(pr_number=123, now=NOW, run=fake_gh)["prs"][0]

    assert record["files_complete"] is False
    assert record["safety_qualified"] is False
    assert "changed paths are incomplete" in record["reasons"]


@pytest.mark.parametrize("changed_files", [None, -1, 0])
def test_inventory_disqualifies_inconsistent_changed_file_metadata(changed_files):
    selected = pr_data(changedFiles=changed_files)
    fake_gh = FakeGh([selected])

    record = build_inventory(pr_number=123, now=NOW, run=fake_gh)["prs"][0]

    assert record["files_complete"] is False
    assert record["safety_qualified"] is False
    assert "changed paths are incomplete" in record["reasons"]
    assert not any(command[:2] == ["gh", "api"] for command in fake_gh.commands)


def test_inventory_disqualifies_malformed_paginated_file_response():
    graphql_files = [
        {"path": f"kubernetes/example/{index}.yaml"} for index in range(100)
    ]
    malformed_api = subprocess.CompletedProcess(
        ["gh", "api"], 0, stdout="not JSON", stderr=""
    )
    selected = pr_data(changedFiles=101, files=graphql_files)
    fake_gh = FakeGh([selected], api_files={123: malformed_api})

    record = build_inventory(pr_number=123, now=NOW, run=fake_gh)["prs"][0]

    assert record["files_complete"] is False
    assert record["safety_qualified"] is False
    assert "changed paths are incomplete" in record["reasons"]


def test_inventory_fetches_all_comments_at_the_graphql_page_boundary():
    graphql_comments = [
        {
            "author": {"login": f"user-{index}"},
            "authorAssociation": "NONE",
            "body": "not an evaluation",
            "createdAt": f"2026-08-25T12:{index % 60:02d}:00Z",
        }
        for index in range(100)
    ]
    api_comments = [
        {
            "user": {"login": f"user-{index}"},
            "author_association": "NONE",
            "body": "not an evaluation",
            "created_at": f"2026-08-25T12:{index % 60:02d}:00Z",
        }
        for index in range(100)
    ] + [
        {
            "user": {"login": "claytono"},
            "author_association": "OWNER",
            "body": sentinel(),
            "created_at": "2026-08-26T15:00:01Z",
        }
    ]
    selected = pr_data(comments=graphql_comments)
    fake_gh = FakeGh([selected], api_comments={123: api_comments})

    record = build_inventory(pr_number=123, now=NOW, run=fake_gh)["prs"][0]

    assert record["evaluation"]["state"] == "current"
    assert record["safety_qualified"] is True
    assert [
        command for command in fake_gh.commands if command[:2] == ["gh", "api"]
    ] == [
        [
            "gh",
            "api",
            "repos/claytono/infra/issues/123/comments",
            "--paginate",
            "--slurp",
        ]
    ]


def test_inventory_treats_truncated_unavailable_comments_as_unknown():
    comments = [
        {
            "author": {"login": "github-actions"},
            "authorAssociation": "NONE",
            "body": sentinel(),
            "createdAt": "2026-08-26T15:00:01Z",
        }
        for _ in range(100)
    ]
    failed_api = subprocess.CompletedProcess(
        ["gh", "api"], 1, stdout="", stderr="rate limited"
    )
    fake_gh = FakeGh(
        [pr_data(comments=comments)], api_comments={123: failed_api}
    )

    record = build_inventory(pr_number=123, now=NOW, run=fake_gh)["prs"][0]

    assert record["evaluation"]["state"] == "unknown"
    assert record["safety_qualified"] is False
    assert "evaluation comments are incomplete" in record["reasons"]


def test_observe_pr_reads_only_head_and_required_checks():
    selected = pr_data(number=124, headRefOid="b" * 40)
    fake_gh = FakeGh([selected])

    result = inventory_module.observe_pr(124, run=fake_gh)

    assert result == {
        "number": 124,
        "state": "OPEN",
        "base_ref": "main",
        "base_sha": "0" * 40,
        "head_sha": "b" * 40,
        "stable": True,
        "required_checks": {
            "state": "passing",
            "checks": [{"name": "test", "bucket": "pass", "state": "SUCCESS"}],
        },
    }
    view_commands = [
        command for command in fake_gh.commands if command[:3] == ["gh", "pr", "view"]
    ]
    assert view_commands == 2 * [
        [
            "gh",
            "pr",
            "view",
            "124",
            "--json",
            "number,state,baseRefName,baseRefOid,headRefOid",
        ]
    ]
    assert not any(
        command[:3] in (["gh", "pr", "list"], ["gh", "pr", "diff"])
        for command in fake_gh.commands
    )


def test_observe_pr_marks_checks_unstable_when_head_changes_during_read():
    old_head = "a" * 40
    new_head = "b" * 40
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    fake_gh = SettlingGh(
        observations=[
            {
                "head_sha": old_head,
                "head_after_sha": new_head,
                "checks": passing,
            }
        ],
        classifications=[],
    )

    result = inventory_module.observe_pr(123, run=fake_gh)

    assert result == {
        "number": 123,
        "state": "OPEN",
        "base_ref": "main",
        "base_sha": "0" * 40,
        "head_sha": new_head,
        "stable": False,
        "required_checks": {"state": "unknown", "checks": []},
    }


def test_observe_pr_marks_checks_unstable_when_base_name_changes_during_read():
    head = "a" * 40
    base_sha = "0" * 40
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    fake_gh = SettlingGh(
        observations=[
            {
                "head_sha": head,
                "base_ref": "main",
                "base_after_ref": "release",
                "base_sha": base_sha,
                "checks": passing,
            }
        ],
        classifications=[],
    )

    result = inventory_module.observe_pr(123, run=fake_gh)

    assert result == {
        "number": 123,
        "state": "OPEN",
        "base_ref": "release",
        "base_sha": base_sha,
        "head_sha": head,
        "stable": False,
        "required_checks": {"state": "unknown", "checks": []},
    }


def test_targeted_inventory_retries_when_base_sha_changes_between_reads():
    head = "a" * 40
    old_base = "0" * 40
    new_base = "1" * 40
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "base_sha": old_base, "checks": passing},
            {"head_sha": head, "base_sha": new_base, "checks": passing},
            {"head_sha": head, "base_sha": new_base, "checks": passing},
            {"head_sha": head, "base_sha": new_base, "checks": passing},
        ],
        classifications=[
            pr_data(headRefOid=head, baseRefOid=old_base),
            pr_data(headRefOid=head, baseRefOid=new_base),
        ],
    )
    messages = []

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=lambda _seconds: None,
        progress=messages.append,
    )

    assert result["prs"][0]["base_sha"] == new_base
    assert result["prs"][0]["safety_qualified"] is True
    assert messages == [
        "PR #123 base changed while safety evidence was read; "
        "discarding it and restarting"
    ]
    assert sum(
        command[:3] == ["gh", "pr", "diff"] for command in fake_gh.commands
    ) == 2


def test_targeted_inventory_retries_an_observation_that_mixes_heads():
    old_head = "a" * 40
    new_head = "b" * 40
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    fake_gh = SettlingGh(
        observations=[
            {
                "head_sha": old_head,
                "head_after_sha": new_head,
                "checks": passing,
            },
            {"head_sha": new_head, "checks": passing},
            {"head_sha": new_head, "checks": passing},
        ],
        classifications=[pr_data(headRefOid=new_head)],
    )
    sleeps = []
    messages = []

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=sleeps.append,
        progress=messages.append,
    )

    assert result["prs"][0]["head_sha"] == new_head
    assert result["prs"][0]["safety_qualified"] is True
    assert sleeps == [30]
    assert messages == [
        "PR #123 changed while required checks were read; retrying in 30 seconds"
    ]
    assert sum(
        command[:3] == ["gh", "pr", "diff"] for command in fake_gh.commands
    ) == 1


def test_targeted_inventory_waits_for_a_settled_head_before_fingerprinting():
    pending = [{"name": "Render Helm Charts", "bucket": "pending", "state": "PENDING"}]
    passing = [{"name": "Render Helm Charts", "bucket": "pass", "state": "SUCCESS"}]
    settled_head = "b" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": "a" * 40, "checks": pending},
            {"head_sha": settled_head, "checks": pending},
            {"head_sha": settled_head, "checks": passing},
            {"head_sha": settled_head, "checks": passing},
        ],
        classifications=[pr_data(headRefOid=settled_head)],
    )
    sleeps = []

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=sleeps.append,
    )

    assert result["prs"][0]["head_sha"] == settled_head
    assert result["prs"][0]["evaluation"]["current_fingerprint"] == FINGERPRINT
    assert result["prs"][0]["safety_qualified"] is True
    assert sleeps == [30, 30]
    diff_positions = [
        index
        for index, command in enumerate(fake_gh.commands)
        if command[:3] == ["gh", "pr", "diff"]
    ]
    check_positions = [
        index
        for index, command in enumerate(fake_gh.commands)
        if command[:3] == ["gh", "pr", "checks"]
    ]
    assert len(diff_positions) == 1
    assert diff_positions[0] > check_positions[2]


def test_targeted_inventory_discards_classification_when_head_changes_mid_read():
    pending = [{"name": "Render Helm Charts", "bucket": "pending", "state": "PENDING"}]
    passing = [{"name": "Render Helm Charts", "bucket": "pass", "state": "SUCCESS"}]
    old_head = "b" * 40
    settled_head = "c" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": old_head, "checks": passing},
            {"head_sha": settled_head, "checks": pending},
            {"head_sha": settled_head, "checks": passing},
            {"head_sha": settled_head, "checks": passing},
        ],
        classifications=[
            pr_data(headRefOid=old_head),
            pr_data(headRefOid=settled_head),
        ],
    )
    sleeps = []

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=sleeps.append,
    )

    assert result["prs"][0]["head_sha"] == settled_head
    assert result["prs"][0]["safety_qualified"] is True
    assert sleeps == [30]
    assert sum(
        command[:3] == ["gh", "pr", "diff"] for command in fake_gh.commands
    ) == 2


def test_targeted_inventory_times_out_as_unverified_when_checks_stay_unknown():
    unknown = subprocess.CompletedProcess(
        ["gh", "pr", "checks"], 1, stdout="not json", stderr="rate limited"
    )
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": unknown},
            {"head_sha": head, "checks": unknown},
            {"head_sha": head, "checks": unknown},
        ],
        classifications=[pr_data(headRefOid=head)],
    )
    clock = FakeClock()

    with pytest.raises(
        TimeoutError,
        match="PR #123 did not settle within 60 seconds; safety is unverified",
    ):
        inventory_module.build_settled_inventory(
            pr_number=123,
            now=NOW,
            run=fake_gh,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            timeout_seconds=60,
        )

    assert clock.sleeps == [30, 30]
    assert not any(
        command[:3] == ["gh", "pr", "diff"] for command in fake_gh.commands
    )


def test_targeted_inventory_backs_off_repeated_unknown_observations():
    unknown = subprocess.CompletedProcess(
        ["gh", "pr", "checks"], 1, stdout="not json", stderr="rate limited"
    )
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": unknown},
            {"head_sha": head, "checks": unknown},
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing},
        ],
        classifications=[pr_data(headRefOid=head)],
    )
    clock = FakeClock()

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert result["prs"][0]["safety_qualified"] is True
    assert clock.sleeps == [30, 60]


def test_targeted_inventory_retries_when_classification_sees_pending_checks():
    pending = [{"name": "Render Helm Charts", "bucket": "pending", "state": "PENDING"}]
    passing = [{"name": "Render Helm Charts", "bucket": "pass", "state": "SUCCESS"}]
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing},
        ],
        classifications=[pr_data(headRefOid=head), pr_data(headRefOid=head)],
        classification_checks=[pending, passing],
    )
    sleeps = []

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=sleeps.append,
    )

    assert result["prs"][0]["safety_qualified"] is True
    assert sleeps == []
    assert sum(
        command[:3] == ["gh", "pr", "diff"] for command in fake_gh.commands
    ) == 1


def test_targeted_inventory_retries_when_final_checks_change_to_failing():
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    failing = [{"name": "Lint", "bucket": "fail", "state": "FAILURE"}]
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": failing},
            {"head_sha": head, "checks": failing},
            {"head_sha": head, "checks": failing},
        ],
        classifications=[pr_data(headRefOid=head), pr_data(headRefOid=head)],
        classification_checks=[passing, failing],
    )

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=lambda _seconds: None,
    )

    record = result["prs"][0]
    assert record["required_checks"]["state"] == "failing"
    assert record["safety_qualified"] is False
    assert "required checks are failing" in record["reasons"]


def test_targeted_inventory_retries_when_final_pr_state_changes_to_closed():
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing, "state": "CLOSED"},
            {"head_sha": head, "checks": passing, "state": "CLOSED"},
            {"head_sha": head, "checks": passing, "state": "CLOSED"},
        ],
        classifications=[
            pr_data(headRefOid=head),
            pr_data(headRefOid=head, state="CLOSED"),
        ],
        classification_checks=[passing, passing],
    )

    result = inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=lambda _seconds: None,
    )

    record = result["prs"][0]
    assert record["state"] == "CLOSED"
    assert record["safety_qualified"] is False
    assert "PR is not open" in record["reasons"]


def test_targeted_inventory_times_out_if_evidence_read_crosses_deadline():
    passing = [{"name": "Lint", "bucket": "pass", "state": "SUCCESS"}]
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing},
        ],
        classifications=[pr_data(headRefOid=head)],
    )
    clock = FakeClock()

    def slow_gh(command, **kwargs):
        result = fake_gh(command, **kwargs)
        if command[:3] == ["gh", "pr", "diff"]:
            clock.now = 31
        return result

    with pytest.raises(
        TimeoutError,
        match="PR #123 did not settle within 30 seconds; safety is unverified",
    ):
        inventory_module.build_settled_inventory(
            pr_number=123,
            now=NOW,
            run=slow_gh,
            sleep=clock.sleep,
            monotonic=clock.monotonic,
            timeout_seconds=30,
        )


def test_targeted_inventory_reports_why_it_is_waiting():
    pending = [{"name": "Render Helm Charts", "bucket": "pending", "state": "PENDING"}]
    passing = [{"name": "Render Helm Charts", "bucket": "pass", "state": "SUCCESS"}]
    head = "a" * 40
    fake_gh = SettlingGh(
        observations=[
            {"head_sha": head, "checks": pending},
            {"head_sha": head, "checks": passing},
            {"head_sha": head, "checks": passing},
        ],
        classifications=[pr_data(headRefOid=head)],
    )
    messages = []

    inventory_module.build_settled_inventory(
        pr_number=123,
        now=NOW,
        run=fake_gh,
        sleep=lambda _seconds: None,
        progress=messages.append,
    )

    assert messages == [
        "PR #123 head aaaaaaaaaaaa has pending required checks; "
        "retrying in 30 seconds"
    ]


def test_inventory_handles_an_empty_queue():
    assert build_inventory(now=NOW, run=FakeGh([]))["prs"] == []


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_inventory_trusts_evaluations_posted_by_repository_members(association):
    record, _ = inventory_for(
        pr_data(
            comments=[
                {
                    "author": {"login": "claytono"},
                    "authorAssociation": association,
                    "body": sentinel(),
                    "createdAt": "2026-08-26T15:00:01Z",
                }
            ]
        )
    )

    assert record["evaluation"]["state"] == "current"
    assert record["safety_qualified"] is True


def test_inventory_rejects_evaluation_posted_by_contributor():
    record, _ = inventory_for(
        pr_data(
            comments=[
                {
                    "author": {"login": "octocat"},
                    "authorAssociation": "CONTRIBUTOR",
                    "body": sentinel(),
                    "createdAt": "2026-08-26T15:00:01Z",
                }
            ]
        )
    )

    assert record["evaluation"]["state"] == "missing"
    assert record["safety_qualified"] is False


@pytest.mark.parametrize(
    ("mutation", "expected_state", "expected_reason"),
    [
        (
            {"comments": []},
            "missing",
            "evaluation is missing",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "octocat"},
                        "body": sentinel(),
                        "createdAt": "2026-08-26T15:30:00Z",
                    }
                ]
            },
            "missing",
            "evaluation is missing",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "github-actions[bot]"},
                        "body": sentinel(version=3),
                        "createdAt": "2026-08-26T15:00:01Z",
                    }
                ]
            },
            "invalid",
            "evaluation is invalid",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "github-actions"},
                        "body": sentinel(evaluated_at="not-a-timestamp"),
                        "createdAt": "2026-08-26T15:00:01Z",
                    }
                ]
            },
            "invalid",
            "evaluation is invalid",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "github-actions"},
                        "body": sentinel(evaluated_at="2026-08-26T15:00:00"),
                        "createdAt": "2026-08-26T15:00:01Z",
                    }
                ]
            },
            "invalid",
            "evaluation is invalid",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "github-actions"},
                        "body": sentinel(fingerprint="wrong"),
                        "createdAt": "2026-08-26T15:00:01Z",
                    }
                ]
            },
            "mismatched",
            "evaluation is mismatched",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "github-actions"},
                        "body": sentinel(evaluated_at="2026-08-18T15:00:00Z"),
                        "createdAt": "2026-08-18T15:00:01Z",
                    }
                ]
            },
            "stale",
            "evaluation is stale",
        ),
        (
            {
                "comments": [
                    {
                        "author": {"login": "github-actions"},
                        "body": sentinel(evaluated_at="2026-08-26T17:00:00Z"),
                        "createdAt": "2026-08-26T17:00:01Z",
                    }
                ]
            },
            "invalid",
            "evaluation is invalid",
        ),
    ],
)
def test_inventory_rejects_untrusted_or_noncurrent_evaluations(
    mutation, expected_state, expected_reason
):
    record, _ = inventory_for(pr_data(**mutation))

    assert record["evaluation"]["state"] == expected_state
    assert record["safety_qualified"] is False
    assert expected_reason in record["reasons"]


@pytest.mark.parametrize(
    ("checks", "expected_state", "expected_reason"),
    [
        ([], "none", "required checks are missing"),
        (
            [{"name": "test", "bucket": "fail", "state": "FAILURE"}],
            "failing",
            "required checks are failing",
        ),
        (
            [{"name": "test", "bucket": "pending", "state": "PENDING"}],
            "pending",
            "required checks are pending",
        ),
        (
            subprocess.CompletedProcess(
                ["gh", "pr", "checks"],
                8,
                stdout=json.dumps(
                    [{"name": "test", "bucket": "pending", "state": "PENDING"}]
                ),
                stderr="",
            ),
            "pending",
            "required checks are pending",
        ),
        (
            subprocess.CompletedProcess(
                ["gh", "pr", "checks"],
                1,
                stdout=json.dumps(
                    [{"name": "test", "bucket": "fail", "state": "FAILURE"}]
                ),
                stderr="",
            ),
            "failing",
            "required checks are failing",
        ),
        (
            subprocess.CompletedProcess(
                ["gh", "pr", "checks"], 1, stdout="not json", stderr="boom"
            ),
            "unknown",
            "required checks are unknown",
        ),
        (
            subprocess.CompletedProcess(
                ["gh", "pr", "checks"], 1, stdout="[]", stderr="no checks"
            ),
            "none",
            "required checks are missing",
        ),
    ],
)
def test_inventory_requires_nonempty_passing_required_checks(
    checks, expected_state, expected_reason
):
    record, _ = inventory_for(pr_data(), checks=checks)

    assert record["required_checks"]["state"] == expected_state
    assert record["safety_qualified"] is False
    assert expected_reason in record["reasons"]


def test_inventory_reports_generic_gate_failures_and_hides_comment_body():
    record, _ = inventory_for(
        pr_data(
            author={"login": "octocat"},
            state="CLOSED",
            isDraft=True,
            baseRefName="develop",
            labels=[
                {"name": "renovate:evaluated"},
                {"name": "renovate:caution"},
            ],
            mergeable="CONFLICTING",
            mergeStateStatus="DIRTY",
        )
    )

    assert record["safety_qualified"] is False
    assert record["reasons"] == [
        "author is not Renovate",
        "PR is not open",
        "PR is a draft",
        "base branch is not main",
        "missing label: renovate",
        "missing label: renovate:safe",
        "disqualifying label: renovate:caution",
        "evaluation is invalid",
        "PR is not mergeable (CONFLICTING)",
        "merge state is not clean (DIRTY)",
    ]
    assert "comments" not in record
    assert "body" not in json.dumps(record)


def test_inventory_treats_unavailable_diff_as_unknown_evaluation():
    failed_diff = subprocess.CompletedProcess(
        ["gh", "pr", "diff"], 1, stdout=b"", stderr=b"boom"
    )
    record, _ = inventory_for(pr_data(), diff=failed_diff)

    assert record["evaluation"]["state"] == "unknown"
    assert record["evaluation"]["current_fingerprint"] is None
    assert "evaluation is unknown" in record["reasons"]


def test_inventory_treats_command_exceptions_as_unknown_evidence():
    fake_gh = FakeGh([pr_data()])

    def raising_gh(command, **kwargs):
        if command[:3] in (["gh", "pr", "checks"], ["gh", "pr", "diff"]):
            raise OSError("command unavailable")
        return fake_gh(command, **kwargs)

    record = build_inventory(now=NOW, run=raising_gh)["prs"][0]

    assert record["required_checks"] == {"state": "unknown"}
    assert record["evaluation"]["state"] == "unknown"


def test_inventory_rejects_negative_freshness_window():
    with pytest.raises(ValueError, match="must not be negative"):
        build_inventory(evaluation_max_age_seconds=-1, run=FakeGh([]))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"pr_number": 0}, "PR number must be positive"),
        (
            {"pr_number": 123, "poll_interval_seconds": 0},
            "poll_interval_seconds must be positive",
        ),
    ],
)
def test_settled_inventory_rejects_nonpositive_inputs(kwargs, message):
    with pytest.raises(ValueError, match=message):
        inventory_module.build_settled_inventory(run=FakeGh([]), **kwargs)


@pytest.mark.parametrize("command", ["evaluate", "status", "inventory", "observe"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_cli_rejects_nonpositive_pr_numbers(monkeypatch, capsys, command, value):
    monkeypatch.setattr("sys.argv", ["renovate_eval.py", command, "--pr", value])

    with pytest.raises(SystemExit, match="2"):
        renovate_eval.main()

    assert "must be a positive integer" in capsys.readouterr().err


def test_cmd_inventory_prints_json(monkeypatch, capsys):
    expected = {
        "repository": "claytono/infra",
        "evaluation_max_age_seconds": 60,
        "prs": [],
    }
    monkeypatch.setattr(renovate_eval, "require_inventory_prerequisites", lambda: None)
    received = {}

    def fake_build_inventory(**kwargs):
        received.update(kwargs)
        return expected

    monkeypatch.setattr(renovate_eval, "build_inventory", fake_build_inventory)

    renovate_eval.cmd_inventory(
        argparse.Namespace(evaluation_max_age_seconds=60, pr=None)
    )

    assert json.loads(capsys.readouterr().out) == expected
    assert received == {"evaluation_max_age_seconds": 60, "pr_number": None}


def test_cmd_targeted_inventory_uses_settled_classification(monkeypatch, capsys):
    expected = {
        "repository": "claytono/infra",
        "evaluation_max_age_seconds": 60,
        "prs": [{"number": 123, "head_sha": "b" * 40}],
    }
    monkeypatch.setattr(renovate_eval, "require_inventory_prerequisites", lambda: None)
    monkeypatch.setattr(
        renovate_eval,
        "build_inventory",
        lambda **kwargs: pytest.fail("targeted CLI bypassed settling"),
    )
    received = {}

    def fake_build_settled_inventory(**kwargs):
        kwargs.pop("progress")("waiting for stable CI")
        received.update(kwargs)
        return expected

    monkeypatch.setattr(
        renovate_eval,
        "build_settled_inventory",
        fake_build_settled_inventory,
        raising=False,
    )

    renovate_eval.cmd_inventory(
        argparse.Namespace(evaluation_max_age_seconds=60, pr=123)
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out) == expected
    assert captured.err == "waiting for stable CI\n"
    assert received == {"evaluation_max_age_seconds": 60, "pr_number": 123}


def test_cmd_targeted_inventory_reports_unverified_timeout_without_traceback(
    monkeypatch,
):
    monkeypatch.setattr(renovate_eval, "require_inventory_prerequisites", lambda: None)

    def time_out(**_kwargs):
        raise TimeoutError(
            "PR #123 did not settle within 1800 seconds; safety is unverified"
        )

    monkeypatch.setattr(renovate_eval, "build_settled_inventory", time_out)

    with pytest.raises(
        SystemExit,
        match="PR #123 did not settle within 1800 seconds; safety is unverified",
    ):
        renovate_eval.cmd_inventory(
            argparse.Namespace(evaluation_max_age_seconds=60, pr=123)
        )


def test_cmd_observe_prints_compact_json(monkeypatch, capsys):
    expected = {
        "number": 123,
        "state": "OPEN",
        "head_sha": "a" * 40,
        "required_checks": {"state": "pending", "checks": []},
    }
    monkeypatch.setattr(renovate_eval, "require_inventory_prerequisites", lambda: None)
    monkeypatch.setattr(renovate_eval, "observe_pr", lambda pr_number: expected)

    renovate_eval.cmd_observe(argparse.Namespace(pr=123))

    assert json.loads(capsys.readouterr().out) == expected
