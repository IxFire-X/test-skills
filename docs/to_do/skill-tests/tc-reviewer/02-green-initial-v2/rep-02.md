# GREEN initial v2 — rep-02

## Result

`02-green-initial-v2/rep-02` is a successful active run. The evaluator used the canonical prompt, required skill inputs, and the four raw inputs in scenario order. Acceptance remains pending.

- Evaluator: `codex-app-luna-evaluator` on `local`, `gpt-5.6-luna/max`, `fork_turns = none`.
- Native thread: `019fe9a1-7c08-7d11-93e2-1fe0af538ab1`.
- Interval: `2026-08-10T03:05:02Z` to `2026-08-10T03:05:59Z`.
- Native calls: one `apply_patch`; zero shell commands, validators, and semantic checks.
- The captured four schema validators and canonical semantic command each exited `0`; semantic result is `pass` with `errors = []`.

## SHA-256 record

| Artifact | SHA-256 |
| --- | --- |
| `artifacts/protocol/02-green-initial-v2/rep-02/prompt.txt` | `c40e8330f28f8bec786dc5bcf93fbc51641d799bf78fe8a1de9b79665e5c9bfe` |
| `clean-accepted-tc-reviewer-output.json` | `edd177ab4647dfb266c4309ebdc59705cff64ea95b9e80b2479955d00e00501c` |
| `typo-only-tc-reviewer-output.json` | `254a1a9877f1546d1d7bacc8425bd729fb1cfe5b68c429fb336ad6eb6bd21413` |
| `blocking-missing-result-tc-reviewer-output.json` | `3a59a32176c3beda6464c74b35ebbff12756ffcaed46cd7bfe61fea5b10a4377` |
| `blocking-fabricated-auth-tc-reviewer-output.json` | `aaf2e5656b9b9587f985854a60c8efd230ffafa76c9edc1f4326425c66cfe621` |
| `validation-*.json` (each of four) | `2f007b09dde69187ebd8b39e318e7a81c5648897efb5da616c7e86d4dcf42b01` |
| `semantic-result.json` | `ed03b1881667b5488a1204dc31da57110a7f2ba28614f7c7f7a5a75e4b70a836` |

The preflight and hash/collision delivery check are controller records only. Their literal argv, repository-root cwd, and exit `0` are recorded in `artifacts/protocol/02-green-initial-v2/rep-02/observation.json`. The repetition protocol records exactly four validator argv followed by the canonical semantic argv, all with campaign cwd and exit `0`.

