# eval-data.json Schema

Write your evaluation data as JSON to the `eval-data.json` path specified below.
This file is the structured input for the report renderer — you do NOT write
markdown reports. A template renders the report from your JSON.

## Schema

```json
{
  "packages": [
    {
      "name": "sonarr",
      "old_version": "4.0.16",
      "new_version": "4.0.17",
      "type": "docker"
    }
  ],
  "label": "renovate:safe",
  "update_scope": "Markdown text describing what is updating and version deltas.",
  "performance_stability": "Markdown text or null",
  "features_ux": "Markdown text or null",
  "security": "Markdown text or null",
  "key_fixes": "Markdown text or null",
  "newer_versions": "Markdown text or null",
  "hazards": "Markdown text (REQUIRED, even if 'None identified...')",
  "follow_up": "Markdown text or null",
  "sources": [
    {
      "label": "Sonarr v4.0.17 release",
      "url": "https://github.com/Sonarr/Sonarr/releases/tag/v4.0.17"
    }
  ],
  "verdict": "1-2 sentence rationale for the label."
}
```

## Field Reference

### packages (required, non-empty array)

Each package being updated in this PR. Fields:

- `name` (string): Package name (e.g., "sonarr", "postgresql")
- `old_version` (string): Version before this PR
- `new_version` (string): Version after this PR
- `type` (string): One of: `docker`, `helm`, `ansible`, `terraform`,
  `pre-commit`, `github-action`, `dependency`

If multiple entries have the same (name, old_version, new_version, type), only
include one.

### label (required, string)

One of:

- `renovate:safe` — routine for this repository's actual usage; normal
  validation may still be required. Security patches or CVE fixes that do not
  introduce a separate concrete concern remain safe because they reduce risk
  rather than adding it.
- `renovate:caution` — concrete, evidenced repo-relevant concern requiring
  targeted validation beyond normal validation.
- `renovate:breaking` — breaking or incompatible change; may require config,
  code, workflow, or operational changes before merge.
- `renovate:risk` — known issues, regressions, low confidence, or insufficient
  evidence for a non-trivial change.

### update_scope (required, string)

What is updating and what the version deltas are. Explicitly state unchanged
components. This renders as the first section under "The Deep Dive."

### performance_stability (string or null)

Performance improvements, stability fixes, and resource usage changes required
by the proportional discovery depth. Link each item to its PR or issue and state
its practical deployment effect in natural prose under a descriptive heading.
Distinguish what deployment evidence establishes from what remains unknown. Set
to `null` only when the selected depth produced no material items.

### features_ux (string or null)

For each feature required by proportional discovery depth: (1) what it does and
why it matters, (2) its availability, (3) whether activation is automatic or
requires action, (4) what deployment evidence establishes about use, and (5)
what remains unknown and why. Use a descriptive heading and natural prose; do
not encode these dimensions as a stacked status prefix. A major release MUST
include every upstream headline feature plus other materially significant
features. Unknown configuration is not grounds for omission. Set to `null` only
when the selected depth produced no qualifying features.

### security (string or null)

CVE IDs with CVSS scores as full markdown links, whether user is affected based
on their config. Set to `null` if not applicable.

### key_fixes (string or null)

Cross-reference bug fixes against actual config and usage patterns. Do not
repeat items already covered in another section. Set to `null` if not
applicable.

### newer_versions (string or null)

Analysis of versions newer than what this PR proposes. Flag regressions in the
proposed version that are fixed later. Always include CVEs or security
advisories introduced by the proposed version and fixed later, even when config
relevance is unknown. Set to `null` if not applicable (but document in evidence
file why it was omitted).

### hazards (required, string)

ALWAYS required. For a major release, cover every documented breaking change,
deprecation, and migration. Related low-level changes may be synthesized by
category, followed by a complete compact inventory. For every item or group,
use a descriptive heading and natural prose to explain the consequence, what
deployment evidence establishes, and any remaining uncertainty. For minor and
patch releases, follow the proportional discovery rules. If there are genuinely
no hazards, write "None identified" with a brief explanation.

### follow_up (string or null)

This renders as the **Further Follow-up** section. Include consequential
unknowns that could not be quantified with the evaluator's
available access. For each item state: (1) what remains unknown, (2) why it
matters, (3) what evidence was already checked, (4) the exact follow-up needed,
and (5) how each possible result changes the verdict. This is not a generic test
checklist. For a consequential schema or data migration, an unverified
recoverable backup and restore path is an unresolved **recovery prerequisite**
and belongs here. Set to `null` when no consequential unknown remains.

### sources (required, non-empty array)

Each source you consulted. Fields:

- `label` (string): Human-readable description (e.g., "Sonarr v4.0.17 release")
- `url` (string): Full URL starting with `http`

### verdict (required, string)

1-2 sentence rationale for the label, plus post-merge follow-up actions if any.

## Rules

- Every factual claim must have a full markdown link `[text](url)` — never bare
  `#123` or unlinked references in any text field.
- `ci_status` is NOT part of this schema — it is injected by the evaluation
  script from live CI checks.
- Set optional sections to `null` (not empty string) when not applicable.
  Document in the evidence file why each was omitted.

## Report Style

The report is rendered as a GitHub PR comment. Optimize the JSON field values
for fast scanning:

- Prefer short bullet lists for sections with multiple facts, changes, fixes,
  risks, sources of evidence, or deployment impacts.
- Use at most one short lead sentence before bullets in a section.
- Keep each bullet focused on one claim plus its deployment-specific impact.
- Avoid dense narrative paragraphs in `performance_stability`, `features_ux`,
  `security`, `key_fixes`, `newer_versions`, and `hazards`.
- `update_scope` may be prose, but keep it compact and specific.
- `verdict` remains 1-2 sentences.

## Validation

After writing eval-data.json, run the validation subcommand:

```bash
python3 $SCRIPT_DIR/renovate_eval.py validate $ARTIFACT_DIR/eval-data.json
```

If it reports errors, fix them before finishing.
