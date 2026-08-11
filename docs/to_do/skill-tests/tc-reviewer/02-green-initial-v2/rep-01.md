# GREEN initial v2 — rep-01

## Result

`02-green-initial-v2/rep-01` is a successful active run after RED-v3.  The evaluator received the canonical prompt and the four raw inputs in scenario order, with the skill and its required reference/schema inputs present.  Acceptance remains pending a fresh Sol verdict.

- Evaluator: `codex-app-luna-evaluator` on `local` with `gpt-5.6-luna/max`; `fork_turns = none`.
- Native evaluator thread: `019fe994-e407-7701-86f9-0fbe107bd408`.
- Interval: `2026-08-10T02:51:17Z` to `2026-08-10T02:52:20Z`.
- Native evaluator calls: one `apply_patch`; zero shell commands, validators, and semantic checks.
- Repetition results: the four captured schema validators exited `0`; the captured canonical semantic check exited `0` with `status = pass` and `errors = []`.

## Immutable delivery and outputs

| Artifact | SHA-256 |
| --- | --- |
| `skills/tc-reviewer/SKILL.md` | `62fa4c00972cd5ab5a0deb6d9bafe045c13519b4a91da5f1d6d50fe5ff775a18` |
| `skills/tc-reviewer/references/review-verdicts.md` | `8d01417f6ce67f4b0badecaf624b60459522dbaf15aa42c03d9d2d32abc1ff78` |
| `schemas/tc-reviewer-output.schema.json` | `676035d151e2623308f007ff0b93026f0bb55bb3d1269a7a5ec7f6dee9d6e927` |
| `artifacts/protocol/02-green-initial-v2/rep-01/prompt.txt` | `c40e8330f28f8bec786dc5bcf93fbc51641d799bf78fe8a1de9b79665e5c9bfe` |
| `artifacts/outputs/02-green-initial-v2/rep-01/clean-accepted-tc-reviewer-output.json` | `9ceffcf825a82d64d35e6e3ad00078faac11d51ebf93478c5090ef5ebc56cbe1` |
| `artifacts/outputs/02-green-initial-v2/rep-01/typo-only-tc-reviewer-output.json` | `b8c08d9487dbeb2880bad06d3f201b73cab2f124b2cb7b4993660da2e3178885` |
| `artifacts/outputs/02-green-initial-v2/rep-01/blocking-missing-result-tc-reviewer-output.json` | `421304e8e16d1d470471e2e5d1b166dbe39f015aa59f93bfc09c1b5a8745b0d9` |
| `artifacts/outputs/02-green-initial-v2/rep-01/blocking-fabricated-auth-tc-reviewer-output.json` | `137b9d06a3ddf4d6ff45066556704d01f902f02d8c28ffb8b86681cbc5143cbd` |
| `artifacts/outputs/02-green-initial-v2/rep-01/validation-clean-accepted.json` | `2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01` |
| `artifacts/outputs/02-green-initial-v2/rep-01/validation-typo-only.json` | `2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01` |
| `artifacts/outputs/02-green-initial-v2/rep-01/validation-blocking-missing-result.json` | `2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01` |
| `artifacts/outputs/02-green-initial-v2/rep-01/validation-blocking-fabricated-auth.json` | `2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01` |
| `artifacts/outputs/02-green-initial-v2/rep-01/semantic-result.json` | `63398c34beafb4c3533ead6908fb7f6f5ef421332189d62fd8e7c28f28a551d4` |

Raw-input allowlist, in captured order: `artifacts/inputs/clean-accepted.json`, `artifacts/inputs/typo-only.json`, `artifacts/inputs/blocking-missing-result.json`, and `artifacts/inputs/blocking-fabricated-auth.json`.

## Command record

Controller preflight (separate from repetition commands) exited `0` with cwd `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`:

```json
["C:\\Program Files\\Git\\bin\\sh.exe", "C:/Users/User/.codex/plugins/cache/sol-advisor/sol-advisor/0.5.0/scripts/install-agents.sh", "--check"]
```

Controller delivery verification (also separate from repetition commands) exited `0` with the same repository-root cwd.  It checked the four delivered hashes and confirmed no reserved v2 output target collision:

```json
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe", "-c", "import hashlib,json,pathlib,sys; pairs=list(zip(sys.argv[1:9:2],sys.argv[2:9:2])); bad=[{\"path\":p,\"actual\":hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest(),\"expected\":h} for p,h in pairs if hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()!=h]; collisions=[p for p in sys.argv[9:] if pathlib.Path(p).exists()]; print(json.dumps({\"bad_hashes\":bad,\"collisions\":collisions},sort_keys=True)); raise SystemExit(0 if not bad and not collisions else 1)", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\skills\\tc-reviewer\\SKILL.md", "62fa4c00972cd5ab5a0deb6d9bafe045c13519b4a91da5f1d6d50fe5ff775a18", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\skills\\tc-reviewer\\references\\review-verdicts.md", "8d01417f6ce67f4b0badecaf624b60459522dbaf15aa42c03d9d2d32abc1ff78", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\schemas\\tc-reviewer-output.schema.json", "676035d151e2623308f007ff0b93026f0bb55bb3d1269a7a5ec7f6dee9d6e927", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\protocol\\02-green-initial-v2\\rep-01\\prompt.txt", "c40e8330f28f8bec786dc5bcf93fbc51641d799bf78fe8a1de9b79665e5c9bfe", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\clean-accepted-tc-reviewer-output.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\typo-only-tc-reviewer-output.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\blocking-missing-result-tc-reviewer-output.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\blocking-fabricated-auth-tc-reviewer-output.json"]
```

The repetition's five captured commands, in order, all used cwd `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer` and exited `0`:

```json
[
  ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\tools\\validate_artifact.py", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\schemas\\tc-reviewer-output.schema.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\clean-accepted-tc-reviewer-output.json"],
  ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\tools\\validate_artifact.py", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\schemas\\tc-reviewer-output.schema.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\typo-only-tc-reviewer-output.json"],
  ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\tools\\validate_artifact.py", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\schemas\\tc-reviewer-output.schema.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\blocking-missing-result-tc-reviewer-output.json"],
  ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\tools\\validate_artifact.py", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\schemas\\tc-reviewer-output.schema.json", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01\\blocking-fabricated-auth-tc-reviewer-output.json"],
  ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\check_outputs.py", "--input-dir", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\inputs", "--output-dir", "D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\02-green-initial-v2\\rep-01", "--mode", "canonical"]
]
```

