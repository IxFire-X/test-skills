# Context-marker final evaluator report

Observation: D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\07-green-final-v4\rep-01.md
JSON: D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-01\context-marker-output.json

- Runtime UTC: `2026-08-07T01:31:37Z`
- Result: valid

## Prompt (verbatim)

```text
APPLICATION INSTRUCTION - context-marker

Read only these supplied inputs:
1. artifacts/inputs/order-change.md
2. artifacts/inputs/source-diff.patch

Classify the supported order-creation facts into analytics requirements and source-code context. Preserve each fact's provenance anchor. Do not invent a manager-approval rule, retention rule, authorization policy, or any other absent requirement; record those topics as warnings/gaps only when relevant.

Write exactly one machine envelope named context-marker-output.json that validates against the declared context-marker output schema: schema_version "2.1.0", stage "context-marker", artifacts.analytics_documentation.requirements with REQ-* ids, text, and provenance, artifacts.source_code_and_diff.sources, and warnings. Return the envelope content and a concise source-grounded summary.
```

## Source-grounded summary

The envelope records three explicit order-creation requirements: accepted request data, rejected invalid order lines, and successful creation behavior. Each uses its supplied `order-change.md#OC-*` provenance anchor. The diff is retained as two source observations without inferring additional behavior. Manager approval and retention are recorded only as an unspecified-policy warning; no authorization or other policy was introduced.

## Required validation command

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\07-green-final-v4\rep-01\context-marker-output.json
```

Validator transcript:

```json
{"errors":[],"status":"valid"}
```
