# Tc-reviewer RED-v3 recovery decision

Fresh Sol selected **fix-first, route B** for the bounded tc-reviewer RED recovery.

## Boundary

- `01-red-control/rep-01` and `01-red-control-v2/rep-01` are immutable, protocol-invalid, and excluded from scoring. Their archived output and evidence bytes remain untouched.
- The only active RED route is `01-red-control-v3/rep-01` with N=1. It uses the existing application-prompt bytes and SHA-256 `6c4bc209686edcdb3f1907170e94cfa83c59740f2af2acc012fa43161b0350ac`, plus the same four raw tc-generator inputs.
- RED-v3 may read only shared `schemas/tc-reviewer-output.schema.json` with SHA-256 `676035d151e2623308f007ff0b93026f0bb55bb3d1269a7a5ec7f6dee9d6e927` as common contract authority. `skills/tc-reviewer/SKILL.md` and `skills/tc-reviewer/references/review-verdicts.md` stay withheld, as do checker code, prior outputs/evidence, and expected decisions.
- Schema `allOf` defines generic verdict-to-findings/corrections semantics; it contains no fixture-specific answers.

## Acceptance

The scenario names v3 as the effective RED phase, reserves only fresh v3 output and protocol scaffolds, and records the unchanged RED phase hash. GREEN `required_skill_inputs` remain the exact three existing entries. This amendment does not generate a v3 output and does not authorize an evaluator, schema validator, or semantic checker. Execution requires a later separately authorized step with literal argv and the absolute campaign cwd recorded in its evidence.
