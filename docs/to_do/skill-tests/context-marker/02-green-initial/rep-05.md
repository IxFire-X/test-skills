# INITIAL-GREEN report — rep-05

STATUS: partial
OBJECTIVE: Produced one context-marker output from the frozen pre-fix snapshot and raw input only.
CHANGES: D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\02-green-initial\rep-05\context-marker-output.json; D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\02-green-initial\rep-05.md
COMMAND: D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/validate_artifact.py schemas/context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\02-green-initial\rep-05\context-marker-output.json
EXIT: 1
FILES: D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\02-green-initial\rep-05\context-marker-output.json; D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\02-green-initial\rep-05.md
VERIFIED: One required validator invocation returned `status: invalid`; it reported unexpected top-level `analytics_documentation`, `format`, and `source_code_and_diff`; missing required `artifacts`, `schema_version`, and `stage`; and non-string warning entries.
JUDGMENT CALLS: Treated the frozen snapshot's pre-processing-only behavior as preserving the three supplied facts and two source observations, with no inferred absent policy.
GAPS: Output is not schema-valid. Per the one-run/no-repair constraint, no repair or rerun was performed.
