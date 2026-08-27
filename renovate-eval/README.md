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

### Interactive skill (Codex example)

```text
/renovate-eval
```

Lists open Renovate PRs, evaluates selected PR, shows actions menu.

Natural-language list constraints are passed through as `gh pr list`
arguments. For example, `renovate-eval 5 prs labeled with safe` runs:

```bash
RENOVATE_EVAL_DIR="${RENOVATE_EVAL_DIR:-$HOME/.codex/skills/renovate-eval}"
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" init \
  --gh-pr-list-args='--label renovate:safe --limit 5'
```

The reusable skill uses the evaluator bundled alongside the invoked `SKILL.md`,
keeping its instructions and implementation on the same installation.
Evaluation commands pass the selected provider explicitly.

Caller-provided author, app, state, and limit options replace the defaults.
Repository and output-format options are rejected because evaluation remains
scoped to the current repository and consumes a fixed JSON response.

### CLI

For direct CLI use, Codex installations normally use
`$HOME/.codex/skills/renovate-eval` and Claude installations normally use
`$HOME/.claude/skills/renovate-eval`. Set `RENOVATE_EVAL_DIR` to this checkout
when developing the shared action directly. Set `RENOVATE_EVAL_PROVIDER` to
`claude` or `codex`; these examples default to Codex.

```bash
RENOVATE_EVAL_PROVIDER="${RENOVATE_EVAL_PROVIDER:-codex}"
RENOVATE_EVAL_CANDIDATES=()
if [[ -n "${RENOVATE_EVAL_DIR:-}" ]]; then
  RENOVATE_EVAL_CANDIDATES+=("$RENOVATE_EVAL_DIR")
fi
if [[ "$RENOVATE_EVAL_PROVIDER" == "codex" ]]; then
  RENOVATE_EVAL_CANDIDATES+=(
    "$HOME/.codex/skills/renovate-eval"
    "$HOME/.claude/skills/renovate-eval"
  )
else
  RENOVATE_EVAL_CANDIDATES+=(
    "$HOME/.claude/skills/renovate-eval"
    "$HOME/.codex/skills/renovate-eval"
  )
fi
RENOVATE_EVAL_RESOLVED_DIR=""
for RENOVATE_EVAL_CANDIDATE in "${RENOVATE_EVAL_CANDIDATES[@]}"; do
  if [[ -f "$RENOVATE_EVAL_CANDIDATE/renovate_eval.py" ]]; then
    RENOVATE_EVAL_RESOLVED_DIR="$RENOVATE_EVAL_CANDIDATE"
    break
  fi
done
: "${RENOVATE_EVAL_RESOLVED_DIR:?renovate-eval installation not found}"
RENOVATE_EVAL_DIR="$RENOVATE_EVAL_RESOLVED_DIR"

# Dry run (prints report, cleans up)
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --dry-run --context local \
  --provider "$RENOVATE_EVAL_PROVIDER"

# Post to GitHub (comment + labels)
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --post --context local \
  --provider "$RENOVATE_EVAL_PROVIDER"

# Post after an external CI gate, taking a snapshot without waiting again
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" \
  evaluate --pr 1234 --post --context ci --no-wait-for-ci \
  --provider "$RENOVATE_EVAL_PROVIDER"

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

# Machine-readable inventory of every open Renovate PR
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" inventory

# Wait for stable CI, then classify one PR without reloading the queue
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" inventory --pr 1234

# Low-cost diagnostic snapshot of one PR's head and required checks
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" observe --pr 1234

# Validate eval-data.json
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" validate path/to/eval-data.json

# Render eval-data.json to markdown
python3 "$RENOVATE_EVAL_DIR/renovate_eval.py" render path/to/eval-data.json --ci-status passing
```

`inventory` is the read-only integration boundary for batch maintenance tools.
Without `--pr`, it returns every open PR from the Renovate GitHub App, including
active auto-merges as an informational queue snapshot. Callers must use the
targeted form as the authoritative pre-write gate. With `--pr`, it waits up to
30 minutes for required checks to settle, follows replacement heads created by
Renovate or CI, and classifies only after the same terminal head is observed
before and after the safety read.
The target branch name and revision must remain stable across the same read. It
never fingerprints a merely pending head. Both forms include exact head/base
metadata, changed paths, labels, required-check state, mergeability, trusted
evaluation freshness and fingerprint state, and a generic `safety_qualified`
decision with explicit reasons. They do not expose evaluation report bodies or
individual required-check records. A PR qualifies only when its trusted v4 safe
sentinel matches the current diff, is at most seven days old, all nonempty
required checks pass, its labels agree, and GitHub reports a clean mergeable
state. The inventory conditionally follows GitHub's REST pagination when the
GraphQL changed-path or comment collections reach their response limits; if a
complete safety-relevant collection cannot be confirmed, the PR does not
qualify. Use
`--evaluation-max-age-seconds` to change the freshness window for a caller with
a different policy.

`observe --pr` is the low-cost diagnostic boundary used internally by targeted
inventory. It returns only the PR number, open/closed state, current head SHA,
target branch name and SHA, required checks, and whether both revisions remained
stable while the checks were read. It does not read comments or download the
diff, and its output is not a safety decision.

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
GitHub Actions, excludes pending checks belonging to its current run from that
snapshot while preserving completed results.

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
- **Repo config drives behavior**: Renovate-specific repository policy comes
  from the provider-neutral root `.renovate-eval.md`. General repository
  guidance remains in the active agent's native project instructions.
- **Conservative default**: When uncertain, the evaluator labels as
  `renovate:risk`.
