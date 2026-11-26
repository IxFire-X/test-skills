---
name: orchestrate
description: Use when the user asks for test cases of an application from agreed requirements, a whole OpenSpec specification or a selected change with its related regression.
permissions:
  read: "project files by inventory with exclusions; --docs, --reference and --cases only inside the project"
  write: "<project>/.pilot-runs/<run_id>/; <project>/.skillsrc when created; publication directory: <run>/published"
  network: "none"
  external_delivery: "none"
  dependencies: "the package installs nothing; the agent does not run pip"
  rollback: "delete <project>/.pilot-runs/<run_id>/ and the created <project>/.skillsrc"
---

# Trassa orchestration — test cases

You are the controller in a model-enabled CLI. Do not launch the model from Python and do not pass model credentials. Python tools check and publish
artifacts; they do not run the build or tests. Cases go with the profile `cases-only-v1`; autotests (`local-pilot-v1`, mode B) are in the delivery of the profile `mode-b` (`doctor` names it): run them by `references/mode-b.md`; `cases-only` has none.
Talk to the human in Russian. Say the texts in «…» word for word; codes, keys and commands stay as they are.

Human request → mode: [requests.md](references/requests.md). This is a map of the stages. A command's arguments — `python -m tools.api <command> --help`; what to do next — `state` and `next` in `status`.
Before work — `python -m tools.doctor --root <pack-root>`. Call tools with the package as cwd and an absolute `--project`; do not import the package's functions.
`prepare` and `submit` — `python -m tools.api <command> --run-root <run> --stage <id>`; `scan`, `status`, `finish`, `report` — `python -m tools.run_pipeline <name>` (in full below). The answer is one line of JSON: `status: ok` and `result`,
`invalid` (code 1) or `error` (code 2: `code`, `message`, `fix`). You may write only to `<project>/.pilot-runs/`, `.skillsrc` and `<run>/published`.
In `--model-id` write the actual model ID of your CLI. Do not create a clone, worktree, sandbox, dependency, CI/cron, commit, push or PR.

Do not cut a step's input and do not switch the model to make it fit. `STEP_OVER_BUDGET`, `STEP_OUTPUT_LARGE`, `STEP_INPUT_UNRESOLVED` are not a stop: one
line to the human (with `hint`), go on; do not split or rewrite the answer. `MODEL_OUTPUT_TRUNCATED` is a refusal: show the human the bytes and the hint, repeat the stage once only, by `fix`.

## 0. Readiness

`python -m tools.ready --project <abs> --docs <path> [--docs …] --profile cases-only-v1 [--module <ID>]` only reads: show the human the table, fix every `СТОП`
by its `fix` (show the human the command that installs dependencies, do not run it yourself); code 0 — you may start. A project with documents only:
one module `root` with the root `.` and `stack.language: documents`, `.skillsrc` is created by itself; the only mode is `cases-only-v1`, all steps `manual_only`, the outcome `coverage: MANUAL_ONLY`, `accepted: false` is the norm, not an error.

## 1. Run and review mode

Scope for OpenSpec — [openspec.md](references/openspec.md). Each gap goes into `warnings` in four parts: the requirement and its source; the missing condition;
the blocked checks; the question. An undefined requirement stays in the set; a substantial gap forbids claiming the full scope. `python -m tools.run_pipeline scan --project <abs> --docs <path> [--docs …] --profile <profile> [--module <ID>]` creates the run (`run_id` is the last line of the output; do not write the run's files and events by hand).
`SCAN_RUN_IN_PROGRESS` — go on by `fix`; `--new-run` — only at the human's request or if `fix` names it. An open run whose project files changed (drift) is not continued by `scan`: it starts a new one and names the old one in `abandoned_runs`.
Do not search for documentation of outside dependencies yourself and do not save it into the project tree: take what the human gave; if it is not enough — one line to the human.

`INDEPENDENT` review: a fresh context is required (a subagent or a call without the generation history). `SELF`: the same session, no new one is required, it is not called independent. `review:isolation` in `ready` hints whether the CLI has a subagent.
If not — before the first review ask the human word for word: «Ревьюер будет работать в той же сессии, без свежего контекста. Принять такой прогон с пометкой SELF?». After an explicit «да» — `prepare` of the review with `--fresh-context no --accept-self-review`
(no repeated scan is needed; one consent per run, the other reviews go with `no` without the flag). Without consent — the refusal `INDEPENDENCE_UNVERIFIED`.

## 2. Module and context

Module, `.skillsrc`, exclusions, `--docs path#Section`, a repeat of the marking — [scan.md](references/scan.md). The analysis is incomplete while a required dependency is unread: do not widen the scope on your own; excluding a file by policy does not prove it irrelevant. The input, the dependencies and the snapshot after scan do not change until the attempt is closed.

## 3. Canonical from batches

The cycle of each model stage: `python -m tools.api prepare --run-root <run> --stage <id>` prints `read` — the summary of the model's files `files_summary` with `list_path` (one size call
per batch; the measure — `step_size`), `answer_slot` and `budget`; the model's answer goes as is into `answer_slot`; `python -m tools.api submit --run-root <run> --stage <id>` accepts it (`--dry-run` — checks only; `--transport-attempts 2` —
the only repeat) and prints the receipt (`result.path`, `content_digest`, `output_bytes`, `warning`), not the body. `<id>` with the revision number (`…:r1`) is named by `status`. The stage's role — `skills/<role>/SKILL.md` (the contract and the answer schema are there): `context-marker`, `tc-generator`, `tc-reviewer`.

1. **Marking.** `context-marker:baseline`, or in parts `:requirements`, then `:batch-NNN`. Digest — the `sha256` of the `list_path` line (not `digest`; `build_context` gives it too; the split — for OpenSpec). Import — [import.md](references/import.md).
2. **Pause.** Gaps without an observation channel, an expected result or access — `state: scope_pause` (`scope_gaps`). `prepare --stage scope` prints the gaps and the answer forms.
   Stop: show the human the list, ask «исключить или продолжать»; the human's words go into `answer_slot`, then `submit --stage scope`. Without an answer `submit --stage plan`
   refuses with `SCOPE_PENDING`. The decision is the human's (exclusion is possible without gaps too, before the plan), anything else is a new run; `scan --no-pause` is the human's request, you do not add it.
3. **Plan** (`--stage plan`). In `answer_slot` — `{"subject": "<название предмета проверки по-русски>"}` and nothing more. Show the human `notes` and `context` of the output as is.
4. **Generator.** Batches in turn. `prepare` gets `--model-id` and `--invocation-id` of the session that writes the cases (the independence of the review depends on the label); `prepare` prints
   `nonce_note` once — do as it says. **The fragment** is a bare object without the `artifacts` envelope; the sample —
   [fragment-sample.md](references/fragment-sample.md), the schema — `schemas/candidate-fragment.schema.json`; `context_receipt` and the digest fields are put by the code, do not write them. Batch 2 onward: the fragment's `operation_capabilities` carry copies of the capabilities
   of past batches it uses (from the input `prior_capabilities`, word for word). The answer is a data file: build it in parts, case by case; write no scripts that import or call the package. A refusal — the only repeat, with `retry_prompt`.
5. **Assembly.** It is done by `submit` of the last batch before the answer is written. `MANUAL_SHARE_HIGH` — tell the human: most steps are manual, there will be no autotests for them, the review may have no control errors — see the report. Check
   the completeness against the source document; what is not proven is a gap. Fields for the human — in Russian; conditions of the work on tests (stubs, run mode) are not written into preconditions, steps and expectations. Many numbered items — sections: [sections.md](references/sections.md).

## 4. Review

1. `prepare --stage tc-reviewer:canonical --model-id <reviewer model> --invocation-id <reviewer label> --fresh-context yes|no` prints `answer_slot`, `reviewer_view`, `for_human`. `yes` — the label is required
   and differs from the generator's label; `no` — after the human's consent to SELF (§1).
2. `tc-reviewer` in a fresh invocation reads `reviewer_view` and writes into `answer_slot`; project files beyond the package — the top-level key `evidence: ["path", …]` (a key outside the answer schema: the code takes it out before the check).
   Do not read the canary map and do not show it to the reviewer; the role, canaries, budget — [review.md](references/review.md).
3. `submit --stage tc-reviewer:canonical` puts the set into `<run>/published`. If the review cannot reach a verdict — `submit --abort REVIEW_CONTEXT_LIMIT` or `REVIEW_UNUSABLE`, then `finish`.

By the `outcome` of the `submit` answer (BLOCKING findings on the real cases decide it; a finding on a control error does not reject a case: «ТРЕБУЕТ ДОРАБОТКИ» with `ACCEPTED_WHOLE` is possible) tell the human: `ACCEPTED_WHOLE` — the set is accepted, `finish`. `PARTIAL` — accepted in part: the good cases are in the set, the rejected ones apart (`*.REJECTED-CASES.*`, do not import them into Zephyr);
if `status` names `prepare tc-generator:rework-1` — one rework round (`tc-generator:rework-1`, then `tc-reviewer:rework-1`, `--fresh-context` as in the first, with `no` without a label), do not call `finish` before it; it does not work out —
`submit --stage tc-generator:rework-1 --abort REWORK_UNUSABLE`: the set goes out without the rework, the run goes on. `REWORK` — rejected whole, `finish` closes the run without a CSV. Without host isolation evidence do not accept the branch (`independence_unverified`).

## 5. Closing and report

`python -m tools.run_pipeline finish --project <abs> --run <run_id>` goes through the closing itself; `FINISH_FACT_UNDETERMINED` names what is missing — do that step. The chain
does not go on — `finish --abandon <code>` from `fix`. The result — `verification=NOT_APPLICABLE`, `accepted=false` and exit code 1: in a cases-only run this is the norm; tell the human `human_summary` as is, do not say «не принят». Read every field; never make a PASS out of manual coverage. An outcome with `FINALIZATION_INVALID` — the run is closed invalid, `accepted=false`: pass it to the human as is, do not repeat `finish` and do not edit files.
`projection.zephyr.template` is set — the export for Zephyr after the closing: [import.md](references/import.md).
At the end, once, `python -m tools.run_pipeline report --project <abs> --run <id>` — `report.md`, `report.json`, `questions-for-analyst.md`; name the paths. `review_notes` — the number and the path in one line; do not change the cases, do not revise the verdict.

## Resume and drift

The context is more than half full — after a finished step ask the human for `/clear` and «продолжай test-skills <run_id>»; then `python -m tools.run_pipeline status --project <abs> --run <run_id>`:
go on from `state` and `next` (`waiting_for: model` — the answer to the request from `pending_request_path`; `human` — wait); `recovery` — tell the human. Do not repeat what the journal confirms. `state: left` (`RUN_LEFT`) —
`finish --abandon LEFT_BEFORE_REVIEW`, then `scan_command`. `status: drift` — pass the human `drift.changed` and `drift.fix`, do not edit files, do not start a run yourself. After `finish` with `next_run` — the command `next_run.command` as is.

## Stop conditions

Stop without a guess: invalid/unsupported version, ambiguous module, unsafe path, required invention, unavailable tool/model, schema/semantic/provenance failure, secret exposure,
baseline drift, stale digest/readback, unproved reviewer isolation, going beyond the authorized scope. Keep the factual partial evidence; do not pass `implemented_unverified` off as verified readiness.
