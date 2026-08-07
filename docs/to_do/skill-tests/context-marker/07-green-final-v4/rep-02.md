# Context-marker final v4 — rep-02

Runtime UTC: `2026-08-07T01:34:52.426Z`

## Prompt (verbatim)

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.
```

## Evidence read

- `artifacts/inputs/order-change.md` — OC-1 through OC-3 are the supported order-creation requirements; OC-4 explicitly leaves manager approval and retention unspecified.
- `artifacts/inputs/source-diff.patch` — `CreateOrderService.create` validates the customer and lines, saves the order, and publishes `OrderCreated`; its comment contains no approval or retention branch.
- `skills/context-marker/SKILL.md` and its directly linked `references/context-artifact-contract.md` — require the single schema-valid envelope, provenance-preserving requirements, source strings, and warnings for unsupported policies.
- `schemas/context-marker-output.schema.json` — declared validation schema.

## Result

The envelope contains three analytics requirements, preserving anchors for OC-1, OC-2, and OC-3. The source context records only the operations visible in the supplied diff. Manager approval, retention, and authorization are represented as gaps rather than requirements.

## Required validation command

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-02\context-marker-output.json
```

Validation result: `{"errors":[],"status":"valid"}` (exit code 0).
