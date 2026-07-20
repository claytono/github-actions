"""Contract checks for the reusable Renovate evaluation workflow."""

from __future__ import annotations

import json
from pathlib import Path


WORKFLOW = (
    Path(__file__).resolve().parents[2]
    / ".github/workflows/claytono-renovate-eval-codex.yaml"
)
RENOVATE_CONFIG = Path(__file__).resolve().parents[2] / "renovate.json"


def test_reusable_workflow_only_skips_second_wait_after_automatic_gate():
    workflow = WORKFLOW.read_text()

    assert "wait-for-checks:" in workflow
    assert "wait_for_ci: ${{ needs.gate.outputs.trigger != 'auto' }}" in workflow


def test_reusable_workflow_has_configurable_long_evaluation_timeout():
    workflow = WORKFLOW.read_text()

    assert "evaluation_timeout_minutes:" in workflow
    assert "default: 90" in workflow
    assert "timeout-minutes: ${{ inputs.evaluation_timeout_minutes }}" in workflow


def test_reusable_workflow_checks_out_main_action_with_wait_override_support():
    workflow = WORKFLOW.read_text()

    assert "repository: claytono/github-actions" in workflow
    assert "ref: main" in workflow
    assert "path: .github-actions" in workflow
    assert "uses: ./.github-actions/renovate-eval" in workflow


def test_renovate_ignores_this_repository_self_reference():
    config = json.loads(RENOVATE_CONFIG.read_text())

    assert "claytono/github-actions" in config["ignoreDeps"]
