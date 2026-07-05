#!/usr/bin/env bash
set -euo pipefail

# Used by .github/workflows/claytono-renovate-eval-codex.yaml to decide which
# Renovate PRs should be evaluated before handing work to a Codex runner.

write_output() {
  printf '%s=%s\n' "$1" "$2" >>"$GITHUB_OUTPUT"
}

validate_pr_number() {
  local pr_number=$1
  if [[ ! "$pr_number" =~ ^[0-9]+$ ]]; then
    echo "PR number must be numeric, got: $pr_number" >&2
    exit 1
  fi
}

load_pr_json() {
  local pr_number=$1
  gh pr view "$pr_number" \
    --repo "$GITHUB_REPOSITORY" \
    --json number,author,baseRefName,headRefOid,labels
}

pr_has_label() {
  local pr_json=$1
  local label=$2
  jq -e --arg label "$label" '[.labels[].name] | index($label) != null' <<<"$pr_json" >/dev/null
}

matrix_for_pr() {
  local pr_number=$1
  local head_sha=$2
  local fingerprint=${3:-}
  jq -cn \
    --argjson pr_number "$pr_number" \
    --arg head_sha "$head_sha" \
    --arg fingerprint "$fingerprint" \
    '{include: [{pr_number: $pr_number, head_sha: $head_sha, fingerprint: $fingerprint}]}'
}

hash_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum
  else
    shasum -a 256
  fi | cut -d' ' -f1
}

compute_fingerprint() {
  local base_ref=$1
  local target_repo_path=$2

  if [[ ! -d "$target_repo_path/.git" ]]; then
    echo "Target repository checkout is missing at $target_repo_path" >&2
    exit 1
  fi

  (
    cd "$target_repo_path"
    if ! git rev-parse --verify "origin/$base_ref" >/dev/null 2>&1; then
      git fetch --no-tags --depth=1 origin "$base_ref:refs/remotes/origin/$base_ref"
    fi
    git diff --no-ext-diff "origin/$base_ref"...HEAD
  ) |
    { grep '^[+-]' || true; } |
    { grep -v '^[+-][+-][+-]' || true; } |
    hash_stdin
}

latest_eval_comment_body() {
  local repo_nwo=$1
  local pr_number=$2
  gh api "repos/$repo_nwo/issues/$pr_number/comments" \
    --paginate \
    --jq '.[] | select(.body | contains("<!-- renovate-eval-skill:")) | .body' 2>/dev/null || true
}

latest_eval_comment_id() {
  local repo_nwo=$1
  local pr_number=$2
  gh api "repos/$repo_nwo/issues/$pr_number/comments" \
    --paginate \
    --jq '.[] | select(.body | contains("<!-- renovate-eval-skill:")) | .id' 2>/dev/null | tail -n1 || true
}

sentinel_json_for_pr() {
  local repo_nwo=$1
  local pr_number=$2
  local sentinel
  sentinel=$(
    latest_eval_comment_body "$repo_nwo" "$pr_number" |
      grep -o '<!-- renovate-eval-skill:{[^}]*}' |
      sed 's/<!-- renovate-eval-skill://' |
      tail -n1 || true
  )
  if [[ -z "$sentinel" ]]; then
    echo "{}"
  else
    echo "$sentinel"
  fi
}

append_limit_notice() {
  local repo_nwo=$1
  local pr_number=$2
  local eval_count=$3
  local comment_id existing_body notice new_body

  comment_id=$(latest_eval_comment_id "$repo_nwo" "$pr_number")
  if [[ -z "$comment_id" || "$comment_id" == "null" ]]; then
    echo "No previous renovate-eval comment found; skipping limit notice"
    return 0
  fi

  existing_body=$(gh api "repos/$repo_nwo/issues/comments/$comment_id" --jq '.body')
  if [[ "$existing_body" == *"Automatic evaluation limit reached"* ]]; then
    echo "Limit notice already present"
    return 0
  fi

  notice=$(
    printf '%s %s' \
      "> Note: **Automatic evaluation limit reached ($eval_count/3).**" \
      "Re-trigger the workflow manually, or delete this comment to start fresh."
  )
  new_body=$(
    printf '%s\n\n---\n\n%s' "$existing_body" "$notice"
  )
  gh api --method PATCH "repos/$repo_nwo/issues/comments/$comment_id" -f body="$new_body"
}

evaluate_auto_pr() {
  local pr_number=$1
  local pr_json author base_ref head_sha labels fingerprint sentinel_json eval_count previous_fingerprint

  validate_pr_number "$pr_number"
  pr_json=$(load_pr_json "$pr_number")
  author=$(jq -r '.author.login // ""' <<<"$pr_json")
  case "$author" in
    renovate\[bot\] | app/renovate) ;;
    *)
      echo "Skipping: PR author is $author, not Renovate"
      write_output should_evaluate false
      return 0
      ;;
  esac

  if pr_has_label "$pr_json" automerge; then
    echo "Skipping: PR has automerge label"
    write_output should_evaluate false
    return 0
  fi

  base_ref=$(jq -r '.baseRefName' <<<"$pr_json")
  head_sha=$(jq -r '.headRefOid' <<<"$pr_json")
  labels=$(jq -r '[.labels[].name] | join(",")' <<<"$pr_json")
  echo "Evaluating automatic Renovate PR #$pr_number"
  echo "Base ref: $base_ref"
  echo "Head SHA: $head_sha"
  echo "Labels: $labels"

  fingerprint=$(compute_fingerprint "$base_ref" "${TARGET_REPO_PATH:?}")
  echo "PR fingerprint: $fingerprint"

  sentinel_json=$(sentinel_json_for_pr "$GITHUB_REPOSITORY" "$pr_number")
  eval_count=$(jq -r '.eval_count // 0' <<<"$sentinel_json" 2>/dev/null || echo 0)
  previous_fingerprint=$(jq -r '.fingerprint // empty' <<<"$sentinel_json" 2>/dev/null || echo "")

  if [[ -n "$previous_fingerprint" && "$fingerprint" == "$previous_fingerprint" ]]; then
    echo "Skipping: PR content unchanged since last evaluation"
    write_output should_evaluate false
    return 0
  fi

  if ((eval_count >= 3)); then
    echo "Eval count is $eval_count (>= 3)"
    if [[ "${INPUT_DRY_RUN:-false}" == "true" ]]; then
      echo "Dry run: would append automatic evaluation limit notice"
    else
      append_limit_notice "$GITHUB_REPOSITORY" "$pr_number" "$eval_count"
    fi
    write_output should_evaluate false
    return 0
  fi

  write_output matrix "$(matrix_for_pr "$pr_number" "$head_sha" "$fingerprint")"
  write_output should_evaluate true
}

evaluate_manual_pr() {
  local pr_number=$1
  local pr_json head_sha

  validate_pr_number "$pr_number"
  pr_json=$(load_pr_json "$pr_number")
  head_sha=$(jq -r '.headRefOid' <<<"$pr_json")
  write_output matrix "$(matrix_for_pr "$pr_number" "$head_sha" "")"
  write_output should_evaluate true
}

evaluate_all_manual_prs() {
  local prs filtered count

  prs=$(
    gh pr list \
      --repo "$GITHUB_REPOSITORY" \
      --author "app/renovate" \
      --state open \
      --json number,labels,headRefOid \
      --limit 100
  )
  filtered=$(
    jq -c '{
    include: [
      .[]
      | (.labels | map(.name)) as $labels
      | select(
        ($labels | index("automerge") | not) and
        ($labels | index("renovate:evaluated") | not)
      )
      | {pr_number: .number, head_sha: .headRefOid, fingerprint: ""}
    ]
  }' <<<"$prs"
  )
  count=$(jq '.include | length' <<<"$filtered")
  write_output matrix "$filtered"
  if [[ "$count" -gt 0 ]]; then
    write_output should_evaluate true
  else
    write_output should_evaluate false
  fi
}

case "${INPUT_TRIGGER:-manual}" in
  auto | manual) ;;
  *)
    echo "trigger must be 'auto' or 'manual', got: ${INPUT_TRIGGER:-}" >&2
    exit 1
    ;;
esac
case "${INPUT_DRY_RUN:-false}" in
  true | false | '') ;;
  *)
    echo "dry_run must be true or false, got: ${INPUT_DRY_RUN:-}" >&2
    exit 1
    ;;
esac

write_output trigger "${INPUT_TRIGGER:-manual}"

if [[ "${INPUT_TRIGGER:-manual}" == "auto" ]]; then
  if [[ "${INPUT_PR_NUMBER:?}" == "all" ]]; then
    echo "Skipping: auto trigger requires a specific PR number"
    write_output should_evaluate false
    exit 0
  fi
  evaluate_auto_pr "$INPUT_PR_NUMBER"
else
  if [[ "${INPUT_PR_NUMBER:?}" == "all" ]]; then
    evaluate_all_manual_prs
  else
    evaluate_manual_pr "$INPUT_PR_NUMBER"
  fi
fi
