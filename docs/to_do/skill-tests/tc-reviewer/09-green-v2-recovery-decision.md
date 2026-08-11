# GREEN v2 recovery decision

`02-green-initial/rep-01` is stopped and excluded as `green-initial-rep-01-semantic-checker-false-negative`. It is not repaired, retried, replaced, or followed by unversioned `rep-02..05`. The archive preserves byte-identical evaluator outputs, validator records, semantic result, and canonical prompt. Its archived run protocol binds the original reserved output paths; the test helper verifies ordered archive-to-reserved byte equality and SHA-256 values.

The recovery route is `02-green-initial-v2/rep-01..05`. The canonical prompt hash remains `c40e8330f28f8bec786dc5bcf93fbc51641d799bf78fe8a1de9b79665e5c9bfe` and the four raw inputs remain in their fixture order. Only the checker/reserved phase changes.

## Controller diagnostic ledger

These are controller records, not repetition commands.

- Before delegation: `argv = ["C:\Program Files\Git\bin\sh.exe", "C:/Users/User/.codex/plugins/cache/sol-advisor/sol-advisor/0.5.0/scripts/install-agents.sh", "--check"]`; `cwd = D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`; `exit_code = 0`.
- Parent schema diagnostic: `argv = ["D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe", "D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py", "D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\skill-test-evidence.schema.json", "D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\protocol\01-red-control-v3\rep-01\run-protocol.json"]`; `cwd = D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`; `exit_code = 1`; status `invalid` solely because `controller_preflight` was an additional property.

The recovery's archived repetition commands remain exactly four validators followed by the canonical semantic checker; controller preflight is not inserted into that repetition command list.
