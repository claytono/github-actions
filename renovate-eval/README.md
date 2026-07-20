# renovate-eval

AI-powered evaluation of Renovate dependency update PRs. Produces structured
reports with risk assessments, evidence documentation, and GitHub labels.

## How It Works

```text
renovate_eval.py evaluate
  ├── fetch_pr_data      # Collect PR metadata, diff, CI status
  ├── check_ci           # Check/wait for CI results
  │
  ├── Round 1: Evaluator (provider-backed, Claude by default)
  │   ├── Reads PR data, repo config, upstream release notes
  │   ├── Cross-references changes against local project config
  │   ├── Writes: eval-data.json (structured), eval-evidence.md
  │   └── Validated against JSON schema (retries on failure)
  │
  ├── Round 1: Auditor (provider-backed, isolated/no-shell where supported)
  │   ├── Reviews rendered report against quality criteria
  │   ├── Checks evidence supports claims
  │   └── Outputs: PASS or FEEDBACK with specific issues
  │
  ├── Round 2+ (if FEEDBACK): Resume evaluator session
  │   ├── Reads auditor feedback + revision.md guidelines
  │   ├── Makes targeted fixes to eval-data.json
  │   └── Auditor re-reviews (resumes its session too)
  │
  └── Output
      ├── Template renders eval-data.json → markdown report
      ├── dry-run: Print report to stdout, clean up temp files
      └── post: Comment on PR, apply labels (renovate:safe/caution/breaking/risk)
```

## Usage

### Interactive (Claude Code skill)

```text
/renovate-eval
```

Lists open Renovate PRs, evaluates selected PR, shows actions menu.

### CLI

The default local installation path is `$HOME/.claude/skills/renovate-eval`. Set
`RENOVATE_EVAL_DIR` to this checkout when developing the shared action directly.

```bash
RENOVATE_EVAL_DIR="${RENOVATE_EVAL_DIR:-$HOME/.claude/skills/renovate-eval}"

# Dry run (prints report, cleans up)
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --dry-run --context local

# Post to GitHub (comment + labels)
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --post --context local

# Post after an external CI gate, taking a snapshot without waiting again
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --post --context ci --no-wait-for-ci

# Use Codex locally
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --dry-run --context local --provider codex

# Use Codex with explicit model, reasoning-effort, and timeout overrides
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --dry-run --context local --provider codex \
  --codex-evaluator-model gpt-5.2 --codex-auditor-model gpt-5.2 \
  --codex-reasoning-effort xhigh --agent-timeout 1800

# Quick status check (live CI + existing eval)
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" status --pr 1234

# Validate eval-data.json
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" validate path/to/eval-data.json

# Render eval-data.json to markdown
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" render path/to/eval-data.json --ci-status passing
```

### GitHub Actions

This directory includes a composite GitHub Action in `action.yml`. The action
installs the selected provider CLI (`claude` or `codex`). It expects the runner
image or setup workflow to provide `git`, `gh`, and `python3`. When Superpowers
installation is enabled, the runner must also provide `curl`.

```yaml
jobs:
  renovate-eval:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      issues: write
      pull-requests: write
    steps:
      - uses: actions/checkout@v6
      - uses: claytono/github-actions/renovate-eval@main
        with:
          pr_number: ${{ github.event.pull_request.number }}
          mode: post
          provider: claude
          github_token: ${{ secrets.GITHUB_TOKEN }}
          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
```

The `provider` input accepts `claude` or `codex`. If omitted, provider
resolution order is explicit `provider` input, then `RENOVATE_EVAL_PROVIDER`,
then Claude. In Codex mode, `codex_version` defaults to `latest` and optional
`codex_evaluator_model` / `codex_auditor_model` inputs can override the Codex
CLI default model. `codex_reasoning_effort` defaults to empty so the composite
action uses the Codex CLI default unless a caller overrides it. `agent_timeout`
defaults to `0`, which disables the subprocess timeout. Callers that need a
bounded run can pass a positive timeout in seconds. The action passes `--yolo`
by default through `yolo: true`; direct local script runs do not use yolo mode
unless `--yolo` is passed.

Post-mode evaluations wait for CI by default. Set the composite action's
`wait_for_ci` input to `false` only when the caller has already completed an
external CI wait. The evaluator still captures a one-time CI snapshot and, in
GitHub Actions, excludes checks belonging to its current run from that snapshot.

`install_superpowers` defaults to `true`. `superpowers_version` defaults to
`latest`, which resolves the latest `obra/superpowers` GitHub release tag. Pass
a release tag such as `v6.0.3`, a bare version such as `6.0.3`, or another git
ref to pin the installed checkout. Claude uses the pinned checkout through
`--plugin-dir`; Codex installs a temporary local marketplace backed by the same
checkout.

The action is intended for Linux runners with Python 3.11 or newer available.
Provisioning `gh`, working Codex subscription auth, persistent `CODEX_HOME`, and
private runner state is out of scope for the action. That infrastructure is
managed separately.

## Labels

| Label                | Meaning                                                                 |
| -------------------- | ----------------------------------------------------------------------- |
| `renovate:safe`      | Routine for actual usage; normal validation may still be required       |
| `renovate:caution`   | Concrete concern requiring targeted validation beyond normal validation |
| `renovate:breaking`  | Breaking or incompatible change needing remediation                     |
| `renovate:risk`      | Known issues, regressions, low confidence, or thin evidence             |
| `renovate:evaluated` | PR has been evaluated (always added)                                    |

## Key Design Decisions

- **Structured JSON output**: The evaluator produces `eval-data.json`; a Python
  template renders the markdown report deterministically.
- **Evidence file**: The evaluator documents commands run and their output so
  the auditor can verify claims without tool access.
- **Validation retries**: If the evaluator produces invalid JSON, it gets
  synthetic feedback and retries (up to 3 times) without counting as an audit
  round.
- **Session resume**: Round 2+ reuses the evaluator/auditor session for faster
  revisions with warm context.
- **Repo config drives behavior**: All repo-specific details (config paths,
  tools, actions menu) come from `.claude/renovate-eval.md`.
- **Conservative default**: When uncertain, the evaluator labels as
  `renovate:risk`.
