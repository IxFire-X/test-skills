# GREEN initial v4 — rep-03

`02-green-initial-v4/rep-03` is a successful active run. Acceptance remains pending the fresh Sol checkpoint.

- Native thread: `019fe9e3-3038-7f61-9fac-9a2c6aa440fb`; `codex-app-luna-evaluator`, local, `gpt-5.6-luna/max`, `fork_turns = none`.
- Interval: `2026-08-10T04:16:48Z` to `2026-08-10T04:18:24Z`.
- The fixed v4 harness captured exactly one allowed bootstrap command, then one `apply_patch` that created the four reserved outputs. No other native tools ran.
- Controller records capture four successful schema validators and one successful canonical semantic check (`status = pass`, `errors = []`).

The evaluator bootstrap command was recorded literally with cwd `C:\Users\User\Documents\Codex\2026-08-10\evaluator-tc-reviewer-initial-green-v4-rep-3`:

```json
["Get-Content", "-LiteralPath", "C:\\Users\\User\\.codex\\plugins\\cache\\openai-curated-remote\\superpowers\\6.2.0\\skills\\using-superpowers\\SKILL.md"]
```

The bootstrap is the v4 harness input, separate from the canonical application inputs. Its complete command record, controller preflight, delivery check, output hashes, validator records, and semantic record are immutable under `artifacts/protocol/02-green-initial-v4/rep-03/`.
