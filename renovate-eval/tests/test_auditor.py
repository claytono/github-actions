"""Tests for lib/auditor.py."""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from lib.auditor import (
    _strip_output_section,
    build_round_one_prompt,
    build_revision_prompt,
    run_auditor,
)


class TestStripOutputSection:
    def test_strips_output(self):
        md = "## Research\nGood stuff\n## Output\nWrite files\n## Next\nMore"
        result = _strip_output_section(md)
        assert "## Research" in result
        assert "Good stuff" in result
        assert "## Output" not in result
        assert "Write files" not in result
        assert "## Next" in result

    def test_ignores_heading_in_code_fence(self):
        md = "## Research\n```\n## Claim: something\n```\n## Output\nWrite files"
        result = _strip_output_section(md)
        assert "## Research" in result
        assert "## Claim: something" in result
        assert "## Output" not in result
        assert "Write files" not in result

    def test_strips_to_eof(self):
        md = "## Research\nStuff\n## Output\nWrite files\nMore output"
        result = _strip_output_section(md)
        assert "Write files" not in result
        assert "More output" not in result

    def test_no_output_section(self):
        md = "## Research\nStuff\n## Conclusion\nDone"
        result = _strip_output_section(md)
        assert result == md


class TestBuildRoundOnePrompt:
    def test_assembles_prompt(self, tmp_dir):
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        with open(os.path.join(prompts_dir, "auditor.md"), "w") as f:
            f.write("Preamble\n---\nAudit instructions")
        with open(os.path.join(prompts_dir, "evaluator.md"), "w") as f:
            f.write("## Research\nRules\n## Output\nJSON stuff")
        with open(os.path.join(prompts_dir, "report-format.md"), "w") as f:
            f.write("Format spec")

        prompt = build_round_one_prompt(
            script_dir=tmp_dir,
            artifact_dir="/tmp/art",
            report="# Report content",
            evidence="## Evidence",
            yolo=True,
        )
        assert "Preamble" in prompt
        assert "## Research" in prompt
        assert "## Output" not in prompt
        assert "Evaluator yolo mode was enabled" in prompt
        assert "Format spec" in prompt
        assert "# Report content" in prompt
        assert "## Evidence" in prompt
        assert "Audit instructions" in prompt
        assert "Required Superpowers Usage" in prompt
        assert "Targeted Revision Superpowers Usage" in prompt
        assert (
            "You MUST use relevant Superpowers skills if they are available" in prompt
        )
        assert "Superpowers skill(s) you used" in prompt
        assert "dispatching-parallel-agents" not in prompt


class TestBuildRevisionPrompt:
    def test_includes_round_and_report(self):
        prompt = build_revision_prompt(
            round_num=2,
            report="# Revised report",
            evidence="## Updated evidence",
        )
        assert "2" in prompt
        assert "# Revised report" in prompt
        assert "## Updated evidence" in prompt


class TestRunAuditor:
    def test_initial_prompt_includes_repo_context_contents(self, monkeypatch, tmp_dir):
        output = {
            "result": '---JSON_START---\n{"status":"PASS","issues":[]}\n---JSON_END---',
            "usage": {},
        }
        called_with = {}

        def mock_run(cmd, **kw):
            called_with["input"] = kw.get("input", "")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(output), stderr=""
            )

        monkeypatch.setattr("lib.agent_runner.subprocess.run", mock_run)
        repo_root = os.path.join(tmp_dir, "repo")
        os.makedirs(repo_root)
        with open(os.path.join(repo_root, ".renovate-eval.md"), "w") as f:
            f.write("Repo-specific auditor guidance")
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for filename in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, filename), "w") as f:
                f.write("content\n---\ninstructions")

        run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
            repo_root=repo_root,
        )

        assert "Repo-specific auditor guidance" in called_with["input"]

    def test_revision_prompt_includes_repo_context_contents(self, monkeypatch, tmp_dir):
        output = {
            "result": '---JSON_START---\n{"status":"PASS","issues":[]}\n---JSON_END---',
            "usage": {},
        }
        called_with = {}

        def mock_run(cmd, **kw):
            called_with["input"] = kw.get("input", "")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(output), stderr=""
            )

        monkeypatch.setattr("lib.agent_runner.subprocess.run", mock_run)
        repo_root = os.path.join(tmp_dir, "repo")
        os.makedirs(repo_root)
        with open(os.path.join(repo_root, ".renovate-eval.md"), "w") as f:
            f.write("Repo-specific auditor guidance")
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Revised report")

        run_auditor(
            round_num=2,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
            repo_root=repo_root,
            session_id="aud123",
        )

        assert "Repo-specific auditor guidance" in called_with["input"]

    @pytest.mark.parametrize("provider", ("codex", "claude"))
    @pytest.mark.parametrize(
        ("round_num", "session_id"),
        ((1, ""), (2, "aud123")),
        ids=("initial", "revision"),
    )
    def test_repo_context_uses_shared_file_for_all_providers(
        self,
        monkeypatch,
        tmp_dir,
        provider,
        round_num,
        session_id,
    ):
        called_with = {}

        def mock_run_agent(**kwargs):
            called_with.update(kwargs)
            return {
                "result": '---JSON_START---\n{"status":"PASS","issues":[]}\n---JSON_END---',
                "usage": {},
            }

        monkeypatch.setattr("lib.auditor.run_agent", mock_run_agent)
        repo_root = os.path.join(tmp_dir, "repo")
        os.makedirs(repo_root)
        with open(os.path.join(repo_root, ".renovate-eval.md"), "w") as f:
            f.write("shared context")
        for provider_dir in (".codex", ".claude"):
            context_dir = os.path.join(repo_root, provider_dir)
            os.makedirs(context_dir)
            with open(os.path.join(context_dir, "renovate-eval.md"), "w") as f:
                f.write("legacy context")
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for filename in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, filename), "w") as f:
                f.write("content\n---\ninstructions")

        run_auditor(
            round_num=round_num,
            artifact_dir=tmp_dir,
            model="model",
            script_dir=tmp_dir,
            repo_root=repo_root,
            provider=provider,
            session_id=session_id,
        )

        assert "shared context" in called_with["prompt"]
        assert "legacy context" not in called_with["prompt"]

    @pytest.mark.parametrize("provider_dir", (".codex", ".claude"))
    def test_provider_specific_repo_context_is_ignored(
        self, monkeypatch, tmp_dir, provider_dir
    ):
        called_with = {}

        def mock_run_agent(**kwargs):
            called_with.update(kwargs)
            return {
                "result": '---JSON_START---\n{"status":"PASS","issues":[]}\n---JSON_END---',
                "usage": {},
            }

        monkeypatch.setattr("lib.auditor.run_agent", mock_run_agent)
        repo_root = os.path.join(tmp_dir, "repo")
        context_dir = os.path.join(repo_root, provider_dir)
        os.makedirs(context_dir)
        with open(os.path.join(context_dir, "renovate-eval.md"), "w") as f:
            f.write("legacy context")
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for filename in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, filename), "w") as f:
                f.write("content\n---\ninstructions")

        run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="model",
            script_dir=tmp_dir,
            repo_root=repo_root,
            provider=provider_dir.removeprefix("."),
        )

        assert "legacy context" not in called_with["prompt"]

    def test_empty_repo_root_does_not_read_context_from_cwd(
        self, monkeypatch, tmp_dir
    ):
        called_with = {}

        def mock_run_agent(**kwargs):
            called_with.update(kwargs)
            return {
                "result": '---JSON_START---\n{"status":"PASS","issues":[]}\n---JSON_END---',
                "usage": {},
            }

        monkeypatch.setattr("lib.auditor.run_agent", mock_run_agent)
        with open(os.path.join(tmp_dir, ".renovate-eval.md"), "w") as f:
            f.write("ambient context")
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for filename in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, filename), "w") as f:
                f.write("content\n---\ninstructions")
        monkeypatch.chdir(tmp_dir)

        run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="model",
            script_dir=tmp_dir,
            repo_root="",
        )

        assert "ambient context" not in called_with["prompt"]

    @pytest.mark.parametrize(
        ("round_num", "session_id"),
        ((1, ""), (2, "aud123")),
        ids=("initial", "revision"),
    )
    def test_missing_repo_context_omits_context_and_keeps_tools_disabled(
        self, monkeypatch, tmp_dir, round_num, session_id
    ):
        called_with = {}

        def mock_run_agent(**kwargs):
            called_with.update(kwargs)
            return {
                "result": '---JSON_START---\n{"status":"PASS","issues":[]}\n---JSON_END---',
                "usage": {},
            }

        monkeypatch.setattr("lib.auditor.run_agent", mock_run_agent)
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")

        run_auditor(
            round_num=round_num,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
            repo_root=os.path.join(tmp_dir, "repo-without-context"),
            session_id=session_id,
        )

        assert "## Repository Context" not in called_with["prompt"]
        assert called_with["disable_tools"] is True

    def test_success(self, monkeypatch, tmp_dir):
        audit_json = '{"status":"PASS","issues":[]}'
        output = {
            "result": f"---JSON_START---\n{audit_json}\n---JSON_END---",
            "session_id": "aud123",
            "total_cost_usd": 0.3,
            "usage": {},
        }
        monkeypatch.setattr(
            "lib.agent_runner.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0],
                0,
                stdout=json.dumps(output),
                stderr="",
            ),
        )
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for f in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, f), "w") as fh:
                fh.write(f"# {f}\n---\nInstructions")
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")

        result = run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
        )
        assert result["status"] == "PASS"
        assert os.path.isfile(os.path.join(tmp_dir, "audit-result.json"))

    def test_nonzero_exit_raises(self, monkeypatch, tmp_dir):
        monkeypatch.setattr(
            "lib.agent_runner.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0], 1, stdout="", stderr="error"
            ),
        )
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for f in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, f), "w") as fh:
                fh.write("content\n---\ninstructions")

        with pytest.raises(RuntimeError, match="claude exited"):
            run_auditor(
                round_num=1,
                artifact_dir=tmp_dir,
                model="sonnet",
                script_dir=tmp_dir,
            )

    def test_no_json_sentinels(self, monkeypatch, tmp_dir):
        output = {"result": "No JSON here", "total_cost_usd": 0.1, "usage": {}}
        monkeypatch.setattr(
            "lib.agent_runner.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0],
                0,
                stdout=json.dumps(output),
                stderr="",
            ),
        )
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for f in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, f), "w") as fh:
                fh.write("content\n---\ninstructions")

        result = run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
        )
        assert result == {}

    def test_evidence_fallback(self, monkeypatch, tmp_dir):
        """When eval-evidence.md doesn't exist, should use fallback text."""
        audit_json = '{"status":"PASS","issues":[]}'
        output = {
            "result": f"---JSON_START---\n{audit_json}\n---JSON_END---",
            "total_cost_usd": 0.1,
            "usage": {},
        }
        called_with = {}

        def mock_run(cmd, **kw):
            called_with["input"] = kw.get("input", "")
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(output), stderr=""
            )

        monkeypatch.setattr("lib.agent_runner.subprocess.run", mock_run)
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for f in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, f), "w") as fh:
                fh.write("content\n---\ninstructions")

        run_auditor(
            round_num=1, artifact_dir=tmp_dir, model="sonnet", script_dir=tmp_dir
        )
        assert "No evidence file provided" in called_with["input"]

    def test_timeout_override(self, monkeypatch, tmp_dir):
        audit_json = '{"status":"PASS","issues":[]}'
        output = {
            "result": f"---JSON_START---\n{audit_json}\n---JSON_END---",
            "total_cost_usd": 0.1,
            "usage": {},
        }
        called = {}

        def mock_run(cmd, **kw):
            called["timeout"] = kw["timeout"]
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(output), stderr=""
            )

        monkeypatch.setattr("lib.agent_runner.subprocess.run", mock_run)
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for f in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, f), "w") as fh:
                fh.write("content\n---\ninstructions")

        run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
            timeout=1800,
        )
        assert called["timeout"] == 1800

    def test_cost_info_handles_none_usage(self, monkeypatch, tmp_dir):
        audit_json = '{"status":"PASS","issues":[]}'
        output = {
            "result": f"---JSON_START---\n{audit_json}\n---JSON_END---",
            "session_id": "aud123",
            "total_cost_usd": 0.3,
            "usage": None,
        }
        monkeypatch.setattr(
            "lib.agent_runner.subprocess.run",
            lambda *a, **kw: subprocess.CompletedProcess(
                a[0],
                0,
                stdout=json.dumps(output),
                stderr="",
            ),
        )
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        prompts_dir = os.path.join(tmp_dir, "prompts")
        os.makedirs(prompts_dir)
        for f in ("auditor.md", "evaluator.md", "report-format.md"):
            with open(os.path.join(prompts_dir, f), "w") as fh:
                fh.write("content\n---\ninstructions")

        run_auditor(
            round_num=1,
            artifact_dir=tmp_dir,
            model="sonnet",
            script_dir=tmp_dir,
        )

        with open(os.path.join(tmp_dir, "auditor-cost-r1.json")) as f:
            cost_data = json.load(f)
        assert cost_data == {
            "cost_usd": 0.3,
            "input_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "output_tokens": 0,
        }

    def test_revision_requires_session(self, tmp_dir):
        with open(os.path.join(tmp_dir, "eval-report.md"), "w") as f:
            f.write("# Report")
        with pytest.raises(RuntimeError, match="cannot resume"):
            run_auditor(
                round_num=2,
                artifact_dir=tmp_dir,
                model="sonnet",
                script_dir="/tmp",
                session_id="",
            )
