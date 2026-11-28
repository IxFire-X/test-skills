# Mode B — `local-pilot-v1` (in deliveries with mode B)

The route: readiness → cases and review → autotests → `materialize` → the run by the human → `ingest` → closing. Read it together with `SKILL.md`; the roles — `tc-to-autotest` and `autotest-reviewer`.
The profile `local-pilot-v1` is allowed only by an explicit request of the human for the full pipeline in this run; a folder, a scan or a request for test cases does not allow it. `accepted=true` — only after a live PASS of all cases.
The request «Напиши автотесты по кейсам изменения <имя> из <csv>» with the permission to write into the test root is this profile: `ready` and `scan` with `--cases <csv> --profile local-pilot-v1`, without `--docs` (the source is the team's cases, the code builds the requirements from the CSV).

## 1. Readiness

- Warnings of the block «Нужно только для исполнения» do not stop: tell the human once «сборка не готова: <message>; можно исправить сейчас (<fix>) до первой задачи модели, иначе этот прогон — только кейсы» and go on.
- Do not create a wrapper (`mvnw`, `gradlew`): it downloads the build tool from the network. Do not install Maven or a wrapper automatically. Switching `.skillsrc` to the system `mvn` (`execution:skillsrc-switch`) — only by the command from `fix`, on the human's confirmation.
- `scan` returned `requested_policy_profile: local-pilot-v1` and `policy_profile: cases-only-v1` — the run is downgraded: pass the human `execution_skipped.reason` and `fix`; after the build is fixed a new run is needed. A project with documents only: `NO_BUILD_MODULE`.
- A module with tests only, the application next to it: `ready` asks `module:<id>:paths.context`; the answer is the application directory from `options` (the package only reads it). The package does not start the application: the human starts the stand before running the command from `materialize`,
  the address is in an environment variable whose name stands in `.skillsrc` at the module (`service_url_env`); the package does not poll the stand. Without it the run is downgraded to cases.
- Do not add an undeclared execution input found late to the baseline: close the branch as `NOT_RUNNABLE/BASELINE_INCOMPLETE`; the continuation is a new scan of the same request.

## 2. Cases and review

The same stages as in `SKILL.md`. A refusal of the review or an abort before the verdict — do not call the automation: close the branch by `finish` (`materialization=execution=NOT_APPLICABLE`, `accepted=false`). `existing-tests.md` in `read` of a generator batch — give it to the generator together with the inputs.

## 3. Autotests

The same cycle as for cases: `status` → `prepare --run-root <run> --stage <id>` → the answer into `answer_slot` → `submit` → `status`; `<id>` with the revision number (`tc-to-autotest:r1`) is named by `status`.

1. `prepare --stage tc-to-autotest:rN` prints `read`, `answer_slot`, `budget`; in `read` — `java_level` (the Java level of the module), `junit_params` (whether junit-jupiter-params is there for `param_group`) and `occupied_paths` (occupied paths — name new tests otherwise): pass them to the generator; `files` holds `pom.xml`/`build.gradle*` of the module (the basis `файл сборки`) and the file `окружение тестов` (the imports of the project's tests) — pass them to the generator and to the autotest reviewer. Into the slot — the full artifact: `source.document_id` and `source.revision` are written by the model — as in the view of the set from `read`;
   `source_digest`, `effective_bundle_receipt_digest`, `content_digest` and the predecessor's digest are put by `submit`. `submit --dry-run` checks the answer and its compatibility with the module's runner before writing; a refusal writes no answer and no journal event (only a line for the report) — fix the slot and repeat (`--transport-attempts 2` — the only repeat).
2. `prepare --stage autotest-reviewer:rN --fresh-context yes|no [--invocation-id <label>]`: the boundary, `REVIEW_REQUESTED` and the request are one call (a repeat completes what was cut off). `yes` — the reviewer is a separate session, `--invocation-id` is its label: not the generator's label and,
   for r2, not the label of the r1 review. `no` — the same session, only after the human's consent to SELF (§1 of `SKILL.md`), name no label. The output has `read`, `answer_slot`, `for_human`, `generator_checks`. The digest fields of the reviewer's answer are put by `submit` before the schema check (it requires them in the finished document) — leave the keys out; the autotest reviewer has no `codeword_seen`. Allow the subagent `python -m tools.autotest_hints` from the reviewer role: it reads and prints.
   Allowed: initial + one complete correction.
3. After the review `status` names `materialize` (accepted) or, after `AUTO_FIX_APPLIED` or `ТРЕБУЕТ ДОРАБОТКИ` of the r1 review, `prepare --stage tc-to-autotest:r2` — one correction by the findings of the r1 review, then the r2 review (label and `--fresh-context` — item 2); after the r2 review and after `materialize` there is no third revision. You do not take up r2 — `finish` closes the attempt (`AUTOMATION_REJECTED`). You took r2 and did not finish it — `finish --abandon` with the code from `fix`.

## 4. `materialize`

After an accepted static review call `materialize` at once (no accepted review — `EXECUTION_NOT_PREPARED`). It publishes the execution-inputs itself and materializes the accepted automation into the project, builds the closed execution request and prints the command for the **human**.
Do not run the command from `materialize` yourself in any form (`command`, `command_text`, `command_text_powershell`). Show the human `files_to_review`, `note`, `service_note` and both forms of the command as is: `command_text` — for cmd and sh,
`command_text_powershell` — for PowerShell. A path with `% ^ " ! $` does not get into the command (`COMMAND_NOT_PRINTABLE`, before the tests are written): the project is moved to another directory and the run is started anew (`scan --new-run`).

## 5. The run by the human and `ingest`

The human runs the build and the tests with the printed command — once, the exact reviewed targets; the human starts the stand. Maven `test` reads Surefire: the project needs the Failsafe/integration-test lifecycle — this route is not enough, keep the blocker.
Wait for the human's words that they reviewed the files, ran the command and where the reports are — only then `ingest`. Do not edit the tests after `materialize` (`INGEST_TESTS_CHANGED`; to restore — `materialize --restore`) and do not fix them after a runtime `FAIL`.

```powershell
python -m tools.run_pipeline materialize --project "$project" --module "$moduleId" --run "$runId"
python -m tools.run_pipeline ingest --project "$project" --module "$moduleId" --run "$runId"
```

`ingest` takes the reports by `report_location` from `materialize` and from there only: `--reports` is not needed (another folder is refused). It checks the reports against the attempt and closes it; the build did not reach the tests — `ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED|RESULT_UNAVAILABLE`. An interrupted `ingest` is repeated with the same reports.
No report — `UNKNOWN`; a timeout of an exact test by the framework's report — `FAIL`. After `EXECUTION_STARTED` do not restart an interruption automatically; the continuation is a new scan.

## 6. Closing

`ingest` goes through the closing itself; a repeated call returns the same outcome. `finish` at the wrong time — `FINISH_FACT_UNDETERMINED` with what is missing: do that step. The automation chain does not go on — `finish --abandon <code>` from `fix`. An outcome with `FINALIZATION_INVALID` — the run is closed invalid, `accepted=false`: pass it as is, do not repeat `finish`, do not edit files.
Partial automation (cases with canon blockers are manual): `ingest` prints `automation_split` — pass the human its `summary` as is; the outcome is `PARTIAL`/`AUTOMATION_PARTIAL`, `accepted=false`.

The fate of the files (the code checks it): `PASS` — `RETAINED`; `FAIL` — findings: the failed methods stay active and get the mark `test-skills-finding`, the passed ones stay; failed ones that cannot be matched — a cleanup with `QUARANTINE_UNRESOLVED`; `NOT_RUNNABLE` — a cleanup of the byte-identical files of the package;
`UNKNOWN` — cleanup is forbidden. After `FAIL` show the human the list `quarantine` (file, method, ref, kind) and the line `quarantine_unresolved` as is; do not remove the marks, do not fix the tests. `scan --disposition-policy cleanup` — only at the human's request.
Pass `style_checks_skipped` and `format_hint` to the human as is; do not format the files.

## 7. The outcome of the run

`report.md` has the line «Уровень автотестов: …» — `in-process` or `stand` (by `service_url_env`): pass it to the human as is. The level is derived, not set (`TEST_LEVEL_DERIVED`) — say that the line `test.level: <level>` in the block `test` of the module in `.skillsrc` makes it explicit; the human edits the file.

## What mode B writes and how to roll it back

Beyond `<project>/.pilot-runs/`: generated tests in the module's test root (`materialize`), finding marks (`test-skills-finding`) after `FAIL` and `.skillsrc` — only by the switch to the system `mvn` on the human's confirmation. Rollback: `finish`/`ingest` remove the tests and annotations by the receipts; files changed after `materialize` are restored by `materialize --restore`; the directory `<run>` is deleted whole. For mode B this replaces the rule "the project's code is not run": the human runs the tests and the build.
