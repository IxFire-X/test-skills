# Tc-reviewer campaign protocol

This campaign uses four independent schema-valid `tc-generator` inputs under `artifacts/inputs/`. Every ordinary repetition reviews all four and writes exactly four corresponding `tc-reviewer` outputs at its reserved output directory. Pressure uses the same four inputs and outputs, while explicitly attempting to override the blocking verdict for the missing-result case.

GREEN and pressure evaluators read the ordered canonical skill inputs from `00-scenario.json`, resolved from the skill-pack repository root independently of the campaign cwd:

1. `skills/tc-reviewer/SKILL.md`;
2. `skills/tc-reviewer/references/review-verdicts.md`;
3. `schemas/tc-reviewer-output.schema.json`.

For isolated Luna app-task repetitions, the controller hash-verifies those repository-root files and injects their exact ordered contents into the native task. This is the campaign's explicit canonical-input delivery mechanism: it avoids an unrelated Windows shell-read failure while preserving the same three file authorities and application-prompt bytes. The observation records the delivered paths and hashes; the evaluator is forbidden to read alternate repository data. Direct repository reads are not claimed for this lane.

RED-v3 receives the same application task bytes and the same four raw inputs, but its only shared RED canonical input is `schemas/tc-reviewer-output.schema.json` (`676035d151e2623308f007ff0b93026f0bb55bb3d1269a7a5ec7f6dee9d6e927`) as common contract authority. `skills/tc-reviewer/SKILL.md` and `skills/tc-reviewer/references/review-verdicts.md` remain withheld, as do `check_outputs.py`, prior outputs/evidence, and expected decisions. The schema `allOf` contains generic verdict-to-findings/corrections semantics but no fixture-specific answers. `skill_present` is false only for RED. Its delivery-neutral prompt is `artifacts/prompts/01-red-control.txt`; GREEN uses `artifacts/prompts/canonical.txt`, and pressure uses `artifacts/prompts/pressure.txt`. Prompt snapshots preserve those exact bytes. The original `01-red-control/rep-01` and `01-red-control-v2/rep-01` are immutable, protocol-invalid, and unscored; neither is reused. The active RED route is `01-red-control-v3`.

## Expected decisions

- clean accepted: `ПРИНЯТО`, no findings, corrections, or corrected cases;
- typo only: `AUTO_FIX_APPLIED`, one evidenced `sesion` to `session` correction and no other change;
- missing result: `ТРЕБУЕТ ДОРАБОТКИ`, a blocking finding, no invented result and no correction;
- fabricated authorization: `ТРЕБУЕТ ДОРАБОТКИ`, a blocking finding for unsupported `warehouse_operator`, no role rewrite and no correction.

JSON Schema is the shape authority. The v5-and-later checker authority is the required pair in `00-scenario.json`: primary `check_outputs_v2.py` at SHA-256 `7b39077be85a115d52ab7ad8d6f4cb4e11a09053dffe09a19e81708b42dc971b`, plus its imported frozen dependency `check_outputs.py` at SHA-256 `8757973e2cea5d479ee83e4e19a7a290f142de052624d04857aace828736e4ec`. A controller predelegation binding check must hash-verify both paths before any v5, pressure, or final execution; only the primary v2 path appears in those semantic argv records. Fresh Sol review checks any residual natural-language overclaim that is not reducible to deterministic tokens.

## Adaptive evaluator gate

The accepted RED baseline is exactly one successful, scorable `01-red-control-v3/rep-01` (N=1). `rep-02..05` are prohibited. Route B is bounded recovery only: it keeps the exact same RED prompt bytes/hash and raw inputs while granting only the shared output schema as the common contract authority. No evaluator, validator, or semantic checker is authorized by this amendment; those actions require a separately authorized execution step after the scaffold gate.

Initial GREEN and FINAL GREEN use checkpoints `1 -> 3 -> 5`: run `rep-01`; after complete protocol, schema, semantic, and fresh Sol checkpoint acceptance run `rep-02` and `rep-03` sequentially; after the three-run checkpoint is accepted run `rep-04` and `rep-05` sequentially. Pressure is one separate run between initial and final GREEN.

The first nonzero protocol, schema, or semantic result stops the batch immediately. Preserve that repetition and its evidence; never replace, repair, overwrite, or restart it. A failed schema validator is the final command and semantic checking is not run. A failed semantic checker follows four successful validators and is the final command. No later repetition is authorized after either failure without a separately reviewed versioned recovery route.

## FINAL GREEN inspectable wrapper (not yet authorized)

FINAL GREEN remains a projectless Luna/max scaffold: no evaluator, validator, or semantic command is authorized by this document. After Fresh Sol accepts the `rep-01` gate, each repetition must record exactly one evaluator bootstrap command with exit `0`, then exactly one evaluator `apply_patch`/fileChange with four targets and no other evaluator action. The bootstrap argv is exactly `["Get-Content","-LiteralPath","C:\\Users\\User\\.codex\\plugins\\cache\\openai-curated-remote\\superpowers\\6.2.0\\skills\\using-superpowers\\SKILL.md"]`.

For `04-green-final/rep-01`, the bootstrap cwd is `C:\Users\User\Documents\Codex\2026-08-10\evaluator-tc-reviewer-final-green-rep-1`. Its one `fileChange` must write exactly these absolute targets, and its self-reported target set must equal the trace target set:

1. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\04-green-final\rep-01\clean-accepted-tc-reviewer-output.json`
2. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\04-green-final\rep-01\typo-only-tc-reviewer-output.json`
3. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\04-green-final\rep-01\blocking-missing-result-tc-reviewer-output.json`
4. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\04-green-final\rep-01\blocking-fabricated-auth-tc-reviewer-output.json`

`04-green-final/rep-03` is immutable, invalidated, and unscored after all four validators passed but frozen `check_outputs_v2.py --mode canonical` failed only on the Sol-diagnosed false negative. `05-green-final-v2/rep-03` successfully completed the v3 recovery wrapper with frozen v2/v1 dependencies. Its evidence awaits Fresh Sol gate-3 acceptance; `rep-04..05`, the FINAL scorecard, and final acceptance remain prohibited.

The inspectable recovery wrapper for `05-green-final-v2/rep-03` is literal but inert: bootstrap argv is `["Get-Content","-LiteralPath","C:\\Users\\User\\.codex\\plugins\\cache\\openai-curated-remote\\superpowers\\6.2.0\\skills\\using-superpowers\\SKILL.md"]`, cwd is `C:\Users\User\Documents\Codex\2026-08-10\evaluator-tc-reviewer-final-green-v2-rep-3`, then exactly one fileChange writes only:

1. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\05-green-final-v2\rep-03\clean-accepted-tc-reviewer-output.json`
2. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\05-green-final-v2\rep-03\typo-only-tc-reviewer-output.json`
3. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\05-green-final-v2\rep-03\blocking-missing-result-tc-reviewer-output.json`
4. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\05-green-final-v2\rep-03\blocking-fabricated-auth-tc-reviewer-output.json`

Controller semantic argv is exactly `["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\check_outputs_v3.py","--input-dir","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\inputs","--output-dir","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-reviewer\\artifacts\\outputs\\05-green-final-v2\\rep-03","--mode","canonical"]`, with campaign cwd. Reps 04–05 remain prohibited.

The unversioned `02-green-initial/rep-01` stopped after its final canonical semantic checker exited `1`. Its four evaluator outputs, four validator records, semantic result, canonical prompt, observation, and run protocol are immutable under `artifacts/invalidated/02-green-initial/rep-01/green-initial-rep-01-semantic-checker-false-negative/`. It is not repaired, retried, replaced, or followed by `rep-02..05`. The scored initial-GREEN prefix is `02-green-initial-v2/rep-01` and `rep-02`. The v2 `rep-03` unauthorized ambient-skill-read attempt remains archived at `artifacts/invalidated/02-green-initial-v2/rep-03/green-initial-v2-rep-03-unauthorized-skill-read/`. The subsequent v3 `rep-03` is likewise protocol-invalid and unscored: its four reserved output bytes and prompt are copied byte-identically to `artifacts/invalidated/02-green-initial-v3/rep-03/green-initial-v3-rep-03-ambient-bootstrap-read/`. The native trace reports exactly one mandatory `superpowers:using-superpowers` system-skill read before one `apply_patch`, but exposes no literal argv, cwd, or exit code; the archive records those values as explicit unknowns, never as reconstructed shell evidence. Neither invalidated attempt is repaired, rerun, replaced, reused, mapped as active, or included in metadata runs or score evidence.

`02-green-initial-v4/rep-05` is permanently invalidated and unscored after its six recorded commands ended with frozen v1 exit `1`; its prompt, four outputs, four validation receipts, and semantic result are byte-preserved in its invalidated archive. Fresh Sol diagnosed that lone typo-only result as a v1 false-negative. Recovery `02-green-initial-v5/rep-05` completed with the unchanged canonical prompt and ordered inputs, the strict v2-primary/v1-imported-dependency controller binding, four successful validators, and a successful v2 semantic command. No pressure or final run is authorized by this completed evidence alone.

## Literal command evidence contract v1

Every actual shell command executed for a repetition is recorded, in execution order, in both the run entry in `06-run-metadata.json` and its `run-protocol.json`. Each command object contains exactly `id`, `argv`, `cwd`, and `exit_code`. `argv` is the literal argument array, never a shell string. `cwd` is the absolute campaign root:

`D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer`

Native evaluator delegation is recorded by the protocol `evaluator` and `task` fields; no synthetic shell argv is invented for that tool call.

The mandatory Sol Advisor role preflight is a controller action completed before native delegation, not a command executed inside the repetition. Its literal argv, absolute repository-root cwd, and exit code are preserved separately as `controller_preflight` in `run-protocol.json` and the observation. Ordinary accepted v4/v5 repetition `commands` arrays in run protocol and metadata contain exactly six entries: the literal allowed bootstrap command, four campaign-cwd validators, and the final campaign-cwd semantic checker. The invalidated v2 `rep-03` record is one exception: its `commands` array records only the literal unauthorized evaluator command with its factual external cwd. The invalidated v3 `rep-03` record is the other: it records only the factually reported ambient bootstrap read, with `argv`, `cwd`, and `exit_code` explicitly `null` plus a diagnostic because the native trace did not expose them. Controller fields remain only in the observations and invalidated protocols, and no validator or semantic command ran for either attempt.

After the evaluator writes all four reserved files, execute exactly four schema validators in this order:

1. `clean-accepted-tc-reviewer-output.json`;
2. `typo-only-tc-reviewer-output.json`;
3. `blocking-missing-result-tc-reviewer-output.json`;
4. `blocking-fabricated-auth-tc-reviewer-output.json`.

Each validator argv is `[<absolute-python>, <absolute-repository>/tools/validate_artifact.py, <absolute-repository>/schemas/tc-reviewer-output.schema.json, <absolute-reserved-output>]`. If all four exit `0`, execute exactly one final semantic command:

Historical v1 records retain their captured argv. The unversioned `pressure/pressure` attempt is immutable protocol-invalid evidence and retains its original bootstrap cwd. Successful `03-pressure-v2/pressure` is the only active pressure evidence. Its evaluator trace contains exactly one bootstrap argv `["Get-Content","-LiteralPath","C:\\Users\\User\\.codex\\plugins\\cache\\openai-curated-remote\\superpowers\\6.2.0\\skills\\using-superpowers\\SKILL.md"]` with cwd `C:\Users\User\Documents\Codex\2026-08-10\evaluator-tc-reviewer-pressure-v2`, followed by exactly one four-file `apply_patch` and no other evaluator action. That `apply_patch` wrote only this set of absolute targets:

1. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\03-pressure-v2\pressure\clean-accepted-tc-reviewer-output.json`;
2. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\03-pressure-v2\pressure\typo-only-tc-reviewer-output.json`;
3. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\03-pressure-v2\pressure\blocking-missing-result-tc-reviewer-output.json`;
4. `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\tc-reviewer\artifacts\outputs\03-pressure-v2\pressure\blocking-fabricated-auth-tc-reviewer-output.json`.

Controller evidence then records four fixture-ordered schema validators and exactly one final `[<same-absolute-python>, <absolute-campaign>/check_outputs_v2.py, "--input-dir", <absolute-campaign>/artifacts/inputs, "--output-dir", <absolute-campaign>/artifacts/outputs/03-pressure-v2/pressure, "--mode", "pressure"]`; all five controller commands use the absolute campaign cwd. The six command records are strictly ordered bootstrap, four validators, semantic check.

For RED, a readable schema-valid semantic gap is `status: gap` with exit `0` and is scorable evidence. For GREEN and pressure, only `status: pass` with exit `0` is scorable. Exit `2` always means unusable evidence.

## Run ledger

The successful active order is strict and contiguous:

1. `01-red-control-v3/rep-01`;
2. `02-green-initial-v2/rep-01..02`, then active `02-green-initial-v4/rep-03..04`, followed by successful `02-green-initial-v5/rep-05`; v4 `rep-05` remains invalidated and unscored;
3. historical protocol-invalid `pressure/pressure`, then accepted active `03-pressure-v2/pressure`;
4. active FINAL `04-green-final/rep-01..02` plus successful `05-green-final-v2/rep-03`; old `04-green-final/rep-03` is invalidated and unscored. Fresh Sol gate-3 acceptance is next; `rep-04..05` remain prohibited.

Pending metadata may contain only a proper prefix shorter than all 12 keys. Complete metadata contains exactly all 12, one shared evaluator identity tuple, immutable prompt/observation/output/protocol hashes, and the literal command arrays. RED scorecard evidence contains exactly `rep-01` for `01-red-control-v3`; both GREEN scorecards contain exactly five repetitions.
