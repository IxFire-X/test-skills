# FINAL GREEN v2 recovery — rep-03

`05-green-final-v2/rep-03` successfully recovered the immutable, unscored `04-green-final/rep-03` v2-checker false negative. FINAL scorecard and acceptance remain pending Fresh Sol gate 3; `rep-04..05` remain prohibited.

- Native thread: `019feb11-063a-72a2-b43b-3761d9705fb2`; `codex-app-luna-evaluator`, local, `gpt-5.6-luna/max`, `fork_turns = none`.
- Interval: `2026-08-10T09:46:29Z` to `2026-08-10T09:48:15Z`.
- The evaluator used exactly one allowed bootstrap and one four-target `apply_patch`/fileChange; no other evaluator action ran.
- Controller preflight and delivery hash/collision checks passed without bad hashes or pre-existing output collisions.
- Four schema validators and canonical `check_outputs_v3.py` exited `0`; semantic status is `pass` with no errors.

The immutable command, controller binding, output, validation, and semantic records are under `artifacts/protocol/05-green-final-v2/rep-03/` and `artifacts/outputs/05-green-final-v2/rep-03/`.
