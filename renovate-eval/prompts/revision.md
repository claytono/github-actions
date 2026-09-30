# Renovate Evaluation Data Revision

You are revising your existing eval-data.json based on auditor feedback. This is
NOT a new evaluation — do not start from scratch.

## Your Task

1. Read the auditor feedback at the path specified below
2. Read your previous eval-data.json and evidence at the paths specified below
3. For each auditor issue, either make a targeted fix to the relevant JSON
   field or rebut it. Rebut an issue only when your evidence shows the
   auditor misapplied a rule or misread the evidence. Record each rebuttal in
   `eval-evidence.md` under `## Audit Responses`, naming the issue and citing
   the evidence that answers it, and leave the rebutted content unchanged. Do
   not rebut an issue merely to avoid work, and do not weaken a verdict you
   still believe the evidence supports just to satisfy feedback. Rebuttals
   apply only to auditor findings. Validation errors from
   `validation-feedback.json` are mechanical checks and must always be fixed.
4. If the auditor asks for evidence you don't already have, run appropriate
   commands and append findings to the evidence file
5. Do NOT re-run research you already did — your evidence file has the results
6. Do NOT rewrite fields the auditor didn't flag
7. After all fixes, re-evaluate whether your verdict and label still match the
   updated content
8. Do not reduce required discovery coverage to make the report shorter. Major
   release headline features, breaking-change categories, and complete compact
   inventories must survive revision. If feedback conflicts with this rule,
   improve the synthesis or evidence instead of deleting the item.
9. When feedback identifies repetition, keep the complete analysis in the
   single most appropriate section and remove the repeated details from every
   other section. A brief cross-reference may remain when needed, but it must
   not repeat the factual inventory or impact assessment. Removing duplicated
   detail does not reduce discovery coverage; the item remains fully analyzed
   once in the rendered report.

## Field Mapping

The auditor reviews a rendered markdown report. Here's how rendered sections map
to eval-data.json fields:

| Rendered Section        | JSON Field              |
| ----------------------- | ----------------------- |
| Title (H1)              | `packages` array        |
| Risk line               | `label`                 |
| Update Scope            | `update_scope`          |
| Performance & Stability | `performance_stability` |
| Features & UX           | `features_ux`           |
| Security                | `security`              |
| Key Fixes               | `key_fixes`             |
| Newer Versions          | `newer_versions`        |
| Hazards & Risks         | `hazards`               |
| Further Follow-up       | `follow_up`             |
| Sources                 | `sources` array         |
| Verdict                 | `verdict` + `label`     |

## Reference Files

Read these before starting:

- **Output schema:** The eval-data-schema.md file defines the JSON schema, field
  types, and validation rules your output must follow.
- **Evaluation rubric:** The evaluator.md file defines the research methodology
  and quality standards.

## Self-Validation

After making changes, run the validation subcommand (path provided below). If it
reports errors, fix them before finishing.

## Output

Update the files at the output paths specified below. Only write files that you
actually changed.
