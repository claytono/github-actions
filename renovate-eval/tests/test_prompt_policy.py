"""Regression tests for the discovery-first evaluation policy."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


PROMPTS_DIR = Path(__file__).parents[1] / "prompts"
SKILL_PATH = Path(__file__).parents[1] / "SKILL.md"


def _prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def _skill_python_commands() -> list[str]:
    skill = SKILL_PATH.read_text()
    blocks = re.findall(
        r"^[ \t]*```bash\n(.*?)\n[ \t]*```", skill, re.DOTALL | re.MULTILINE
    )
    return [block for block in blocks if block.lstrip().startswith("python3 ")]


def test_evaluator_requires_proportional_discovery_and_preserves_unknowns():
    evaluator = _prompt("evaluator.md")

    assert "Proportional discovery depth" in evaluator
    assert "Unknown is not evidence of absence" in evaluator
    assert "complete compact inventory" in evaluator
    assert "headline feature" in evaluator
    assert "If an item is disabled, unconfigured" not in evaluator


def test_schema_and_report_format_require_major_release_discovery():
    schema = _prompt("eval-data-schema.md")
    report_format = _prompt("report-format.md")

    for content in (schema, report_format):
        assert "descriptive heading" in content
        assert "natural prose" in content
        assert "Further Follow-up" in content
        assert "headline feature" in content


def test_applicability_is_analysis_not_a_stacked_status_prefix():
    evaluator = _prompt("evaluator.md")
    report_format = _prompt("report-format.md")
    auditor = _prompt("auditor.md")

    for content in (evaluator, report_format):
        assert "Do not stack" in content
        assert "availability" in content
        assert "activation" in content
        assert "deployment evidence" in content

    assert "stacked pseudo-status headings" in auditor
    assert "Do not require the literal words" in auditor


def test_consequential_migrations_require_recovery_follow_up():
    evaluator = _prompt("evaluator.md")
    schema = _prompt("eval-data-schema.md")
    auditor = _prompt("auditor.md")

    for content in (evaluator, schema, auditor):
        assert re.search(r"recovery\s+prerequisite", content)


def test_auditor_checks_omissions_without_penalizing_analysis():
    auditor = _prompt("auditor.md")

    assert "Discovery completeness" in auditor
    assert "unknown as disabled or unconfigured" in auditor
    assert "Treat evidence-only dismissals in the rendered report as FEEDBACK" not in auditor
    assert "A section that explains how to enable a feature" not in auditor


def test_auditor_authorizes_repository_context_requirements():
    auditor = _prompt("auditor.md")

    assert re.search(
        r"Repository Context, when present, is authoritative\s+for\s+"
        r"repository-specific\s+evaluation\s+requirements",
        auditor,
    )
    assert re.search(r"Judge\s+factual claims about the PR", auditor)


def test_skill_commands_support_evaluator_path_with_spaces(tmp_path):
    script = tmp_path / "checkout with spaces" / "renovate_eval.py"
    script.parent.mkdir(parents=True)
    script.write_text("print(__file__)\n")
    commands = _skill_python_commands()

    assert len(commands) == 6
    for command in commands:
        expanded = command.replace("RENOVATE_EVAL_PY", str(script)).replace(
            "CURRENT_CHAT_PROVIDER", "codex"
        )
        result = subprocess.run(
            ["bash", "-c", expanded],
            check=False,
            capture_output=True,
            env={**os.environ, "PR": "32"},
            text=True,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(script)


def test_skill_provider_policy_only_applies_to_evaluation_commands():
    skill = SKILL_PATH.read_text()
    commands = _skill_python_commands()

    assert "Always pass `--provider` explicitly" in skill
    assert re.search(
        r"The `init` and `status` commands\s+are provider-independent", skill
    )
    for command in commands:
        if "evaluate --pr" in command:
            assert "--provider" in command
        else:
            assert "--provider" not in command


def test_revision_prompt_forbids_reducing_required_coverage():
    evaluator = _prompt("evaluator.md")
    revision = _prompt("revision.md")

    assert "Do not reduce required discovery coverage" in revision
    assert "single most appropriate section" in evaluator
    assert "single most appropriate section" in revision
    assert re.search(
        r"Removing duplicated\s+detail does not reduce discovery coverage", revision
    )
    assert "Further Follow-up" in revision
