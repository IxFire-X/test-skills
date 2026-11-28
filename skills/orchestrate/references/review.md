# Review: fresh session, subagent, canaries, budget

The stage — §4 of `skills/orchestrate/SKILL.md`, the consent to a review in the same session (SELF) — its §1; the flags — `python -m tools.api prepare --help`, `submit --help`.

**Fresh session.** The reviewer's `--model-id` is a label for the report, as for the generator; a repeated `prepare` — with the same one. `prepare --stage tc-reviewer:canonical` prints `for_human` — the instruction to the human (a new session or `/clear`, paste «проведи ревью test-skills
<run_id>», the reviewer's answer is a file in `answer_slot`): show it as is and wait for the file, then `submit`. If the reviewer is a subagent (below): do not show `for_human` to the human and do not wait for a file, the subagent writes the answer into `answer_slot` itself. The code put the control errors into the reviewer's view:
do not read the canary map and do not show it to the reviewer. The case reviewer names in `codeword_seen` any word from the generator's tasks that is in its context (with SELF — the word of any batch), otherwise `null`; `submit` reports `review_canaries` and
`codeword`. An answer whose edits all rest on control errors is accepted as is (`ПРИНЯТО`, the set unchanged), nothing to repeat; `REVIEW_CANARY_MISMATCH` — do not substitute your own files, repeat `prepare`. «Ревью слабое: пропущено N из M» and
`REVIEW_UNRELIABLE` — tell the human to reread the set themself.

**Reviewer subagent.** In the task name `answer_slot` and `reviewer_view` (the view of the cases) and allow it to read the project files. Do not give it: the generator's dialogue and reasoning, the canary map,
`nonce`, other runs. The package commands of the reviewer role (for cases — `python -m tools.review_edits`) are allowed to the subagent: they only read and print; give no others (`prepare` builds the view). Do not close the run files with a blanket ban: the provenance check is required; but do not give it `incoming/tc-generator-*` (the generator's output). Project files beyond the package — the top-level key `evidence: ["path", …]`
of the answer file (paths from the attempt's inventory; they do not fit into the budget — `REVIEW_CONTEXT_LIMIT`). The answer schema does not list this key: the code takes it out before the schema check, do not remove the key. The key is only in the case review (`tc-reviewer:canonical`); the rework and autotest reviews have none, their schema rejects it. No files read beyond the package — do not write the key: an empty list is rejected.

One logical stage invocation: `transport_attempts` 1 or 2; a repeat — for each model stage, only on a schema or validation error of the answer,
check the input with the same `step_size`; there is no third attempt — stop. In the session zero or more C-lite `EVIDENCE_REQUESTED/EVIDENCE_PROVIDED` are allowed;
per-batch/hierarchical reviewers, a second session and more than one authoritative verdict are forbidden. The budget is not enough for the evidence — `submit --abort REVIEW_CONTEXT_LIMIT`;
the answer failed the check twice and the session waits for a verdict — the same with `REVIEW_UNUSABLE`; then `finish`.

**Outcome and verdict.** The outcome (`ACCEPTED_WHOLE`/`PARTIAL`/`REWORK`) is decided by the BLOCKING findings on the real cases of the set. A finding on a control error (a canary) does not reject a case, so the reviewer's verdict «ТРЕБУЕТ ДОРАБОТКИ» with the outcome `ACCEPTED_WHOLE` is consistent: it is not your error and no reason to repeat the review.

**SELF in advance.** The human's consent to a review in the same session can be given before `prepare` of the review: `scan --accept-self-review` (in the output — `details.self_review_scan`) or `review: {"self": true}` in `.skillsrc` (the human sets it).
