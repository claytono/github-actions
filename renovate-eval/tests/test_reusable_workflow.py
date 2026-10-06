"""Contract checks for the reusable Renovate evaluation workflow."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parents[2] / ".github/workflows"
WORKFLOW = WORKFLOWS_DIR / "claytono-renovate-eval.yaml"
LOCAL_CALLER = WORKFLOWS_DIR / "renovate-eval.yaml"
RENOVATE_CONFIG = Path(__file__).resolve().parents[2] / "renovate.json"


def test_reusable_workflow_runs_passes_without_cancelling_evaluations():
    workflow = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)

    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    group = workflow["concurrency"]["group"]
    assert group.startswith("claytono-renovate-eval-${{ github.repository }}")
    # Single-PR requests get their own group so a pass cannot replace them.
    # Manual and per-PR event requests use separate groups.
    assert "format('-{0}-{1}', inputs.trigger == 'auto' && 'pr' || 'manual'," in group
    assert set(workflow["jobs"]) == {"gate", "evaluate-pr"}


def test_reusable_workflow_caps_parallel_evaluations_without_fail_fast():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    inputs = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)["on"][
        "workflow_call"
    ]["inputs"]

    assert evaluate["needs"] == "gate"
    assert evaluate["strategy"] == {
        "fail-fast": "false",
        "max-parallel": "${{ inputs.max_parallel }}",
        "matrix": "${{ fromJson(needs.gate.outputs.matrix) }}",
    }
    assert inputs["max_parallel"]["default"] == "2"
    assert inputs["batch_size"]["default"] == "6"
    assert inputs["usage_threshold"]["default"] == "0.8"
    assert inputs["pr_number"]["required"] == "false"


def test_gate_plans_pass_and_checks_usage_before_evaluating():
    gate = _jobs(WORKFLOW)["gate"]
    steps = {step["name"]: step for step in gate["steps"]}

    assert "dispatch" in steps["Plan evaluation pass"]["run"]
    assert steps["Plan evaluation pass"]["env"]["GH_REPO"] == "${{ github.repository }}"
    usage = steps["Check Claude usage"]
    assert "usage-check --threshold" in usage["run"]
    assert usage["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == (
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    )
    assert gate["outputs"]["should_evaluate"] == (
        "${{ steps.usage.outputs.allowed == 'false' && 'false' || "
        "steps.plan.outputs.should_evaluate }}"
    )


def test_evaluation_rechecks_need_and_waits_for_ci_only_when_manual():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    steps = {step["name"]: step for step in evaluate["steps"]}

    assert "--recheck" in steps["Confirm evaluation is still needed"]["run"]
    assert steps["Evaluate"]["if"] == "steps.recheck.outputs.should_evaluate != 'false'"
    evaluate_with = steps["Evaluate"]["with"]
    assert evaluate_with["wait_for_ci"] == (
        "${{ needs.gate.outputs.manual == 'true' || "
        "needs.gate.outputs.legacy == 'true' }}"
    )
    assert evaluate_with["usage_threshold"] == "${{ inputs.usage_threshold }}"
    assert "EVAL_FINGERPRINT" not in steps["Evaluate"]["env"]


def test_reusable_workflow_has_configurable_long_evaluation_timeout():
    workflow = WORKFLOW.read_text()

    assert "evaluation_timeout_minutes:" in workflow
    assert "default: 90" in workflow
    assert "timeout-minutes: ${{ inputs.evaluation_timeout_minutes }}" in workflow


def test_reusable_workflow_configures_evaluation_freshness():
    workflow = WORKFLOW.read_text()
    inputs = yaml.load(workflow, Loader=yaml.BaseLoader)["on"]["workflow_call"][
        "inputs"
    ]

    assert "max_automatic_evaluations" not in inputs
    # Kept for callers that still run per PR event.
    assert inputs["trigger"]["default"] == ""
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


def test_reusable_workflow_checks_out_evaluation_action():
    workflow = WORKFLOW.read_text()

    assert "repository: claytono/github-actions" in workflow
    assert "ref: ${{ inputs.helpers_ref }}" in workflow
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
    assert steps["Set Codex home"]["if"] == (
        "needs.gate.outputs.provider == 'codex' && "
        "steps.recheck.outputs.should_evaluate != 'false'"
    )
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


def test_local_caller_runs_passes_instead_of_per_pr_events():
    caller = yaml.load(LOCAL_CALLER.read_text(), Loader=yaml.BaseLoader)

    assert "pull_request" not in caller["on"]
    assert caller["on"]["schedule"] == [{"cron": "23 * * * *"}]
    assert caller["on"]["workflow_run"]["types"] == ["completed"]
    assert (
        "startsWith(github.event.workflow_run.head_branch, 'renovate/')"
        in (caller["jobs"]["renovate-eval"]["if"])
    )


def test_local_caller_passes_only_needed_secrets():
    job = _jobs(LOCAL_CALLER)["renovate-eval"]

    assert job["uses"] == "./.github/workflows/claytono-renovate-eval.yaml"
    assert job["secrets"] == {
        "CLAUDE_CODE_OAUTH_TOKEN": "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
    }
    assert job["with"]["provider"] == "${{ inputs.provider }}"


def test_helpers_follow_helpers_ref():
    workflow = WORKFLOW.read_text()
    inputs = yaml.load(workflow, Loader=yaml.BaseLoader)["on"]["workflow_call"][
        "inputs"
    ]

    assert inputs["helpers_ref"]["default"] == "main"
    assert workflow.count("ref: ${{ inputs.helpers_ref }}") == 3
    assert "repository: claytono/github-actions\n          ref: main" not in workflow


def test_evaluation_checks_out_head_confirmed_by_recheck():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    checkout = next(s for s in evaluate["steps"] if s["name"] == "Checkout")

    assert checkout["with"]["ref"] == (
        "${{ steps.recheck.outputs.head_sha || matrix.head_sha }}"
    )


def test_usage_gate_distinguishes_closed_gate_from_errors():
    gate = _jobs(WORKFLOW)["gate"]
    usage = next(s for s in gate["steps"] if s["name"] == "Check Claude usage")
    action = yaml.load(
        (Path(__file__).resolve().parents[1] / "action.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    action_usage = next(s for s in action["runs"]["steps"] if s.get("id") == "usage")

    for run in (usage["run"], action_usage["run"]):
        assert "1)" in run
        assert 'exit "$rc"' in run


def test_local_caller_tests_its_own_helpers():
    job = _jobs(LOCAL_CALLER)["renovate-eval"]

    assert job["with"]["helpers_ref"] == "${{ github.sha }}"
    assert job["with"]["batch_size"] == "${{ fromJSON(inputs.batch_size || '6') }}"
    caller = yaml.load(LOCAL_CALLER.read_text(), Loader=yaml.BaseLoader)
    # A string input keeps an explicit 0, which `||` would replace if numeric.
    assert caller["on"]["workflow_dispatch"]["inputs"]["batch_size"]["type"] == (
        "string"
    )


def test_action_skips_setup_when_usage_gate_is_closed():
    action = yaml.load(
        (Path(__file__).resolve().parents[1] / "action.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    steps = {step["name"]: step for step in action["runs"]["steps"]}

    for name in ("Check runner tools", "Install Superpowers", "Run evaluation"):
        assert steps[name]["if"] == "steps.usage.outputs.allowed != 'false'"


def test_manual_requests_also_refresh_the_checkout_head():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    steps = {step["name"]: step for step in evaluate["steps"]}
    recheck = steps["Confirm evaluation is still needed"]

    # Both paths read the current head; only passes skip PRs that no longer
    # need evaluation.
    assert "if" not in recheck
    assert "if" not in steps["Checkout recheck helpers"]
    assert recheck["env"]["INPUT_MANUAL"] == "${{ needs.gate.outputs.manual }}"
    assert (
        'if [[ "$INPUT_MANUAL" != "true" ]]; then\n  args+=(--recheck)'
        in (recheck["run"])
    )


def test_per_pr_event_callers_get_legacy_mode():
    gate = _jobs(WORKFLOW)["gate"]
    plan = next(s for s in gate["steps"] if s["name"] == "Plan evaluation pass")
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]
    recheck = next(
        s
        for s in evaluate["steps"]
        if s["name"] == "Confirm evaluation is still needed"
    )

    assert plan["env"]["INPUT_TRIGGER"] == "${{ inputs.trigger }}"
    assert 'if [[ "$INPUT_TRIGGER" == "auto" ]]; then' in plan["run"]
    assert "args+=(--recheck --allow-pending-checks)" in plan["run"]
    assert gate["outputs"]["legacy"] == "${{ steps.plan.outputs.legacy }}"
    assert recheck["env"]["INPUT_LEGACY"] == "${{ needs.gate.outputs.legacy }}"
    assert "args+=(--allow-pending-checks)" in recheck["run"]


def test_trigger_values_are_validated():
    gate = _jobs(WORKFLOW)["gate"]
    plan = next(s for s in gate["steps"] if s["name"] == "Plan evaluation pass")

    assert "''|auto|manual) ;;" in plan["run"]
    assert "trigger must be 'auto', 'manual', or empty" in plan["run"]


def test_evaluations_of_one_pr_are_serialized():
    evaluate = _jobs(WORKFLOW)["evaluate-pr"]

    assert evaluate["concurrency"] == {
        "group": "claytono-renovate-eval-${{ github.repository }}-eval-${{ matrix.pr_number }}",
        "cancel-in-progress": "false",
    }
