"""Contract checks for the reusable Renovate evaluation workflow."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github/workflows"
WORKFLOW = WORKFLOWS_DIR / "claytono-renovate-eval.yaml"
CODEX_WRAPPER = WORKFLOWS_DIR / "claytono-renovate-eval-codex.yaml"
LOCAL_CALLER = WORKFLOWS_DIR / "renovate-eval.yaml"
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


def test_reusable_workflow_configures_automatic_evaluation_frequency():
    workflow = WORKFLOW.read_text()
    inputs = yaml.load(workflow, Loader=yaml.BaseLoader)["on"]["workflow_call"][
        "inputs"
    ]

    assert inputs["max_automatic_evaluations"] == {
        "description": (
            "Maximum automatic evaluations per PR as a non-negative integer; "
            "0 is unlimited"
        ),
        "required": "false",
        "type": "number",
        "default": "0",
    }
    assert (
        "INPUT_MAX_AUTOMATIC_EVALUATIONS: "
        "${{ inputs.max_automatic_evaluations }}" in workflow
    )
    assert inputs["fingerprint_ttl_seconds"] == {
        "description": (
            "Non-negative integer seconds before an unchanged fingerprint is "
            "evaluated again"
        ),
        "required": "false",
        "type": "number",
        "default": "604800",
    }
    assert (
        "INPUT_FINGERPRINT_TTL_SECONDS: ${{ inputs.fingerprint_ttl_seconds }}"
        in workflow
    )


def test_reusable_workflow_checks_out_main_action_with_wait_override_support():
    workflow = WORKFLOW.read_text()

    assert "repository: claytono/github-actions" in workflow
    assert "ref: main" in workflow
    assert "path: .github-actions" in workflow
    assert "uses: ./.github-actions/renovate-eval" in workflow


def test_renovate_ignores_this_repository_self_reference():
    config = json.loads(RENOVATE_CONFIG.read_text())

    assert "claytono/github-actions" in config["ignoreDeps"]


def _jobs(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)["jobs"]


def test_reusable_workflow_resolves_provider_from_input_then_variable():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
    provider_input = workflow["on"]["workflow_call"]["inputs"]["provider"]
    gate = workflow["jobs"]["gate"]
    step = next(s for s in gate["steps"] if s.get("id") == "provider")

    assert provider_input["default"] == ""
    assert gate["outputs"]["provider"] == "${{ steps.provider.outputs.provider }}"
    assert step["env"] == {
        "INPUT_PROVIDER": "${{ inputs.provider }}",
        "VAR_PROVIDER": "${{ vars.RENOVATE_EVAL_PROVIDER }}",
    }
    assert 'provider="${INPUT_PROVIDER:-${VAR_PROVIDER:-claude}}"' in step["run"]
    assert "claude|codex) ;;" in step["run"]


def test_reusable_workflow_routes_evaluation_by_resolved_provider():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    steps = {step["name"]: step for step in evaluate["steps"]}

    assert evaluate["runs-on"] == (
        "${{ needs.gate.outputs.provider == 'codex' && "
        "inputs.codex_runner_label || inputs.claude_runner_label }}"
    )
    assert steps["Set Codex home"]["if"] == "needs.gate.outputs.provider == 'codex'"
    evaluate_with = steps["Evaluate"]["with"]
    assert evaluate_with["provider"] == "${{ needs.gate.outputs.provider }}"
    assert evaluate_with["claude_code_oauth_token"] == (
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    )
    assert evaluate_with["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"


def test_reusable_workflow_reads_model_settings_from_variables():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    step = next(s for s in evaluate["steps"] if s["name"] == "Evaluate")

    for name in (
        "RENOVATE_EVAL_EVALUATOR_MODEL",
        "RENOVATE_EVAL_AUDITOR_MODEL",
        "RENOVATE_EVAL_CODEX_EVALUATOR_MODEL",
        "RENOVATE_EVAL_CODEX_AUDITOR_MODEL",
        "RENOVATE_EVAL_CODEX_REASONING_EFFORT",
    ):
        assert step["env"][name] == f"${{{{ vars.{name} }}}}"


def test_codex_wrapper_pins_codex_provider_for_existing_callers():
    job = _jobs(CODEX_WRAPPER)["renovate-eval"]

    assert job["uses"] == (
        "claytono/github-actions/.github/workflows/claytono-renovate-eval.yaml@main"
    )
    assert job["with"]["provider"] == "codex"
    assert job["with"]["agent_timeout"] == "${{ inputs.codex_agent_timeout }}"
    assert job["secrets"] == {"CACHIX_AUTH_TOKEN": "${{ secrets.cachix_auth_token }}"}


def test_local_caller_passes_only_needed_secrets():
    job = _jobs(LOCAL_CALLER)["renovate-eval"]

    assert job["uses"] == "./.github/workflows/claytono-renovate-eval.yaml"
    assert job["secrets"] == {
        "CLAUDE_CODE_OAUTH_TOKEN": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    }
    assert job["with"]["provider"] == "${{ inputs.provider }}"
