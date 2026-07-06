# Renovate Eval Context

## Role of This File

This file provides repository context, discovery hints, validation commands, and
action-menu behavior for Renovate PR evaluation. It does not redefine the shared
`renovate:safe`, `renovate:caution`, `renovate:breaking`, or `renovate:risk`
label semantics.

## Repo Layout

- Reusable Renovate evaluation action: `renovate-eval/action.yml`
- Renovate evaluation Python implementation: `renovate-eval/lib/`
- Renovate evaluation tests: `renovate-eval/tests/`
- Account-specific reusable workflow: `.github/workflows/claytono-renovate-eval-codex.yaml`
- Workflow helper scripts: `.github/scripts/`
- Nix development environment: `flake.nix` and `flake.lock`
- Repository Renovate configuration: `renovate.json`

## Normal Validation Actions

- `nix develop --command ./scripts/lint --all-files` runs repository linting.
- The flake build workflow validates dev shells across Linux amd64, Linux arm64,
  and macOS arm64.
- Renovate-eval Python tests live under `renovate-eval/tests/`; use the repo's
  Nix development shell when running them locally.

## Config Discovery

- Composite action behavior is defined in `renovate-eval/action.yml`.
- Shared workflow behavior is defined in
  `.github/workflows/claytono-renovate-eval-codex.yaml`.
- Eligibility, fingerprinting, and PR skip logic lives in
  `.github/scripts/renovate-eval-check-eligibility.sh`.
- Prompt and report behavior lives under `renovate-eval/prompts/`.

## Notes

- This repository owns the shared workflow used by other claytono repositories.
  Its local renovate-eval caller uses `./.github/workflows/...`, so Renovate PRs
  in this repository evaluate against the in-repo workflow version under test.
- The repository currently does not have `CACHIX_AUTH_TOKEN`; the local
  renovate-eval caller disables Cachix setup for the evaluation job.
- Keep reusable workflow and composite action changes generic. Repo-specific
  behavior belongs in caller workflows or `.claude/renovate-eval.md` files.

## Actions Menu

Include the default shared actions menu. Add a "Test reusable workflow" action
when Renovate changes `.github/workflows/claytono-renovate-eval-codex.yaml`,
`renovate-eval/action.yml`, or `.github/scripts/`, because those changes should
be validated from a real caller repository before merge.
