# Portable Skills and Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` task-by-task. A task is not accepted until its RED/green evidence and read-only Sol review are complete.

**Goal:** Relocate six testing skills to portable paths, validate their contract and behavior with reproducible fresh-agent campaigns, and add byte-preserving optional adapters.

**Architecture:** `contracts/pipeline.json` is the sole canonical routing and skill-path registry. A stage's schema-valid JSON envelope is machine authority; generated source files may be declared companions but execution evidence comes only from `run_tests.py`. `docs/to_do/skill-tests/` is immutable campaign evidence.

**Tech Stack:** Markdown Agent Skills, Draft 2020-12 JSON Schema, Python/pytest, Java/JUnit 5/Maven, PowerShell.

## Global constraints and campaign protocol

Run from `D:\AI-Projects\.worktrees\portable-testing-skills`:

```powershell
$py = 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe'
$pack = 'test-orchestration-skills'
$validator = 'C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py'
```

- Preserve concurrent work. A package contains only `SKILL.md`, `references/`, `scripts/`, and `assets/`; no package `README.md`, `SKILL-LITE.md`, or `templates/` directory survives.
- A reproducible real-project failure or focused deterministic regression may serve as RED during iteration. Use fresh evaluator repetitions adaptively at 1 → 3 → 5; reserve five repetitions for final acceptance or demonstrated variance. `quick_validate.py` supplements, never replaces, schema validation.
- Before every task commit, generate `docs/to_do/skill-tests/<task-id>/changed-files-manifest.txt` from the reviewed `git diff --name-only` output, one exact repository-relative path per line. Compare it to the task's owned paths, then stage only by expanding those listed file paths as `git add -- <file-1> <file-2> ...`; never use a directory-wide `git add`.
- Evaluators are observation-only and write only their assigned evidence. Each evaluator spawn explicitly uses `fork_turns: "none"`, receives no expected answer/verdict or prior output, and is run sequentially (one active observer, safely within four global slots). A fresh Sol reviewer reads evidence/diff but does not edit.
- A stage with a schema writes its output JSON under `artifacts/outputs/<phase>/rep-<nn>/`; validate it with `tools/validate_artifact.py`. Markdown captures raw prompt/observation only. Never overwrite a previous rep, pressure run, scorecard, source companion, or controller output.

Create this exact tree for every skill campaign before RED:

```text
docs/to_do/skill-tests/<skill-id>/
  00-scenario.json
  01-red-control/rep-01.md ... rep-05.md
  02-green-initial/rep-01.md ... rep-05.md
  03-pressure.md
  04-green-final/rep-01.md ... rep-05.md
  05-scorecards/red.json
  05-scorecards/green-initial.json
  05-scorecards/green-final.json
  06-run-metadata.json
  artifacts/inputs/
  artifacts/outputs/<phase>/rep-<nn>/
```

`00-scenario.json` contains one comprehensive canonical application prompt, one pressure prompt, raw-input allowlist, and rubric IDs. Run that **identical** canonical prompt five independent times for RED and five times for each green batch; any essential second scenario needs its own five repetitions, but this plan uses one scenario plus pressure only. Scorecards hold `results[rep-01..rep-05][rubric-id]: boolean`, evidence links, and `all_passed`; final green needs every value true. RED must record at least one failing repetition×rubric, or record `no_gap: true`, the five observations, and `no_edit_reason` before proceeding. Pressure runs before the minimal refactor, then five fresh final green repetitions run. `06-run-metadata.json` records model, host, `fork_turns`, UTC timestamps, prompt SHA-256, exact allowlist, skill-present flag, output digests, and exits.

## Gate 0: establish the current runtime boundary

Plan 1 acceptance `2894662` and its 322 passed/2 skipped, Ruff/contract/render, Maven 24/24, pytest/fake-Gradle results are historical only. Record fresh command output before and after this plan:

```powershell
& $py -m pytest "$pack\tests" -q
& $py -m ruff check "$pack\tools" "$pack\tests"
& $py "$pack\tools\contract_check.py" --root "$pack" --full
& $py "$pack\tools\render_contract_docs.py" --root "$pack" --check
```

Do not commit code in this gate. A regression is diagnosed in its owning task, not waived by historical measurements.

## Task 1: relocate packages and register the exact portable contract

**Files:** Create `skills/`; move only the six source directories; modify `contracts/pipeline.json`, `schemas/pipeline.schema.json`, `tools/contract_check.py`, `tools/render_contract_docs.py`, root `CONTRACTS.md`, root `PIPELINE.md`, `tests/test_contract_check.py`, `tests/test_contract_docs.py`.

1. Add failing tests named `test_skill_files_registry_is_exact_and_steps_have_no_skill_file`, `test_tc_to_autotest_requires_validation_report_and_two_canonical_case_branches`, `test_contract_check_rejects_alias_artifact_in_autotest_stage`, and `test_rendered_contracts_include_canonical_skill_files`.
2. Create `skills/`, add this exact top-level map to `contracts/pipeline.json`, and add it to schema `required`. Its schema is an object with these six required properties, `additionalProperties: false`, and `const` for every value; do not add `steps[*].skill_file`:

```json
{
  "context-marker": "skills/context-marker/SKILL.md",
  "tc-generator": "skills/tc-generator/SKILL.md",
  "tc-reviewer": "skills/tc-reviewer/SKILL.md",
  "tc-to-autotest": "skills/tc-to-autotest/SKILL.md",
  "autotest-reviewer": "skills/autotest-reviewer/SKILL.md",
  "orchestrate": "skills/orchestrate/SKILL.md"
}
```

3. Set `tc-reviewer.forwards` exactly to `generated_test_cases`, `validation_report`, `corrected_test_cases`; set `tc-to-autotest.accepts` exactly to those three and its `forwards` exactly to those three plus `generated_test_files`, `generated_test_methods`. In `_semantic_errors`, compare to `CANONICAL_SKILL_FILES`, reject any step `skill_file`, exact routing violations, and aliases. Render a `## Canonical skill files` table with `render_contract_docs.py`; regenerate root `CONTRACTS.md` and `PIPELINE.md`.
4. Move contents only—no `SKILL.md` rewrite—with these exact commands:

```powershell
New-Item -ItemType Directory -Force "$pack\skills" | Out-Null
git mv "$pack\Разметка контекста" "$pack\skills\context-marker"
git mv "$pack\Ручные тест-кейсы" "$pack\skills\tc-generator"
git mv "$pack\Валидация тест-кейсов" "$pack\skills\tc-reviewer"
git mv "$pack\Автоматизированные кейсы на основе тест-кейсов" "$pack\skills\tc-to-autotest"
git mv "$pack\Валидация автотестов" "$pack\skills\autotest-reviewer"
git mv "$pack\Оркестратор" "$pack\skills\orchestrate"
```

5. The registry/projections are the Task 1 route update. Task 9 owns repository-wide link migration; do not widen this task's commit to unrelated documents. Run the named tests, `contract_check.py --full`, `render_contract_docs.py --check`, full pytest, and `quick_validate.py` for six packages. Commit only the files named in this task.

## Task 1B: validate campaign scaffolding before the first campaign

**Files:** Create `schemas/skill-test-evidence.schema.json`, `tests/test_skill_test_evidence.py`, and each campaign's empty tree/JSON scaffolding under `docs/to_do/skill-tests/`.

1. Add RED tests for required phase paths, immutable unique output paths, scenario fields, scorecard `results[rep][rubric]`, `no_gap`/`no_edit_reason`, and run metadata. Add the evidence schema with explicit scenario, scorecard, and metadata variants.
2. Scaffold all six campaign trees with valid `00-scenario.json`, three scorecards, and `06-run-metadata.json`; validate each JSON using `validate_artifact.py` and `skill-test-evidence.schema.json` before Task 2. The schema test discovers every campaign directory, so a later campaign cannot omit validation.
3. Commit this schema/test/scaffold separately. There is no first RED evaluator until this gate is green.

## Cleanup map applied before each skill campaign

Before editing a target `SKILL.md`, preserve useful legacy material in the named destination, repair every relative link to that destination, then delete the listed legacy files/directories. Never leave a package-level `templates/` directory.

| Skill | Move useful material to | Delete after migration |
|---|---|---|
| `context-marker` | `references/context-artifact-contract.md`, `assets/context-fixtures/` | `README.md`, `examples.md`, `SKILL-LITE.md`, `templates/` |
| `tc-generator` | `references/case-generation-contract.md`, `assets/case-fixtures/` | `README.md`, `examples.md`, `SKILL-LITE.md`, `templates/` |
| `tc-reviewer` | `references/review-verdicts.md`, `assets/reviewer-fixtures/` | `README.md`, `examples.md`, `SKILL-LITE.md`, `templates/` |
| `tc-to-autotest` | `references/automation-output-contract.md`, `assets/java-python-conventions/` | `README.md`, `examples.md`, `SKILL-LITE.md`, `templates/` |
| `autotest-reviewer` | `references/autotest-review-contract.md`, `assets/autotest-fixtures/` | `README.md`, `examples.md`, `SKILL-LITE.md`, `templates/` |
| `orchestrate` | `references/orchestration-contract.md`, `assets/orchestration-fixtures/` | `README.md`, `examples.md`, `SKILL-LITE.md`, `templates/` |

Run `quick_validate.py` and the local-link test after each row.

## Task 2: context-marker campaign

**Files:** `skills/context-marker/`, `docs/to_do/skill-tests/context-marker/`.

1. Apply its cleanup-map row. Put schema-valid raw input in `artifacts/inputs/raw-content.json`; canonical prompt: classify the supplied order change into source, documentation, and requirement context while preserving quoted facts. Every rep writes `context-marker-output.json` and validates against `context-marker-output.schema.json`.
2. Rubric: provenance on every claim, no invented authentication/retention rule, split canonical outputs, stable IDs. Execute five RED, five initial green, pressure, minimal refactor, five final green; save all per-phase outputs and scorecards. Run validator/link test and commit task-owned paths.

## Task 3: tc-generator campaign

**Files:** `skills/tc-generator/`, `docs/to_do/skill-tests/tc-generator/`.

1. Apply its cleanup-map row. Inputs are schema-valid analytics/source envelopes. Canonical prompt: generate boundary, negative, and role-aware cases for the supplied order change; every rep writes and validates `tc-generator-output.json` with `tc-generator-output.schema.json`.
2. Rubric: traceable requirement IDs, executable expected results, no unstated authorization. Original `01-red-control` is terminal-stopped after immutable invalid `rep-03`; retain `rep-01..02` as historical integrity evidence only. The bounded Sol `change-and-green` amendment supersedes only this task's former five-success tc-generator RED implementation gate: `01-red-control-v2` has three successful diagnostic baseline repetitions, immutable/unscored terminal `rep-04`, no `rep-05`/retry/repair/RED-v3, and a pending empty/non-comparable RED scorecard. Implement the canonical skill next, then execute five initial-GREEN, pressure, and five FINAL-GREEN runs; run validator/link test and commit task-owned paths. This does not alter the default one-RED policy for later skills.
3. **Post-acceptance transport amendment (2026-08-11).** Keep JSON as the sole machine authority, but after every successful JSON validation create an adjacent deterministic CSV companion for Jira Zephyr-oriented transfer. The CSV preserves ID, requirement links, title, priority, categories, preconditions, test data, ordered steps, and expected outcome. `skills/tc-generator/scripts/export_test_cases_csv.py` must read the CSV back and prove exact JSON↔CSV equality in count, order, and values; an existing different CSV is refused, never repaired in place.

## Task 4: tc-reviewer campaign

Pressure checkpoint: unversioned `pressure/pressure` is immutable protocol-invalid and unscored for its reserved-output-path violation. Active `03-pressure-v2/pressure` is accepted by fresh Sol `ship` with its complete pressure scorecard. FINAL `04-green-final/rep-01..02` and recovery `05-green-final-v2/rep-03` are recorded; the formal final scorecard remains pending and `rep-04..05` are not silently implied complete.

**Files:** `skills/tc-reviewer/`, `docs/to_do/skill-tests/tc-reviewer/`.

1. Apply its cleanup-map row. Create four distinct schema-valid `tc-generator-output` inputs: `clean-accepted.json`, `typo-only.json`, `blocking-missing-result.json`, `blocking-fabricated-auth.json`.
2. The single repeated prompt reviews all four labelled inputs. Every repetition writes and validates four separately named envelopes in its own output directory: `clean-accepted-tc-reviewer-output.json`, `typo-only-tc-reviewer-output.json`, `blocking-missing-result-tc-reviewer-output.json`, and `blocking-fabricated-auth-tc-reviewer-output.json`, each against `tc-reviewer-output.schema.json`.
3. Four global verdict assertions: clean accepted/no correction; typo only AUTO_FIX with correction; missing result blocking/no invented correction; fabricated auth blocking/no invented correction. Execute the global campaign sequence, validator/link test, and task-owned commit.
4. **Bounded RED-v3 recovery amendment (Sol fix-first, route B).** The immutable `01-red-control/rep-01` and `01-red-control-v2/rep-01` attempts remain invalidated and unscored. Use exactly one active `01-red-control-v3/rep-01` with the same application prompt bytes/hash `6c4bc209686edcdb3f1907170e94cfa83c59740f2af2acc012fa43161b0350ac` and the same four raw inputs. RED-v3 may share only `schemas/tc-reviewer-output.schema.json` (`676035d151e2623308f007ff0b93026f0bb55bb3d1269a7a5ec7f6dee9d6e927`) as common contract authority; withhold `skills/tc-reviewer/SKILL.md`, `skills/tc-reviewer/references/review-verdicts.md`, the checker, prior outputs/evidence, and expected decisions. Schema `allOf` generic verdict-to-findings/corrections semantics do not provide fixture-specific answers. Preserve the GREEN required inputs unchanged, create fresh v3 output/protocol scaffolds only, and do not execute validators, semantic checking, or an evaluator for the scaffold.
5. **Bounded initial-GREEN mixed recovery amendment (final accepted record).** The final active initial-GREEN map is `02-green-initial-v2/rep-01..02`, `02-green-initial-v4/rep-03..04`, and completed `02-green-initial-v5/rep-05`, all with the canonical prompt hash `c40e8330f28f8bec786dc5bcf93fbc51641d799bf78fe8a1de9b79665e5c9bfe`. The unversioned v1 `02-green-initial/rep-01`, `02-green-initial-v2/rep-03`, `02-green-initial-v3/rep-03`, and `02-green-initial-v4/rep-05` attempts remain immutable, protocol-invalid, and unscored; preserve their archived evidence and never rerun, repair, replace, reuse, or reactivate it. The earlier v3 `rep-03..05` scaffold route is historical only and did not become an active scored route.

6. **Bounded initial-GREEN v5 checker recovery amendment (completed).** `02-green-initial-v4/rep-05` remains an immutable unscored archive after frozen `check_outputs.py` (`8757973e2cea5d479ee83e4e19a7a290f142de052624d04857aace828736e4ec`) returned the Sol-diagnosed false negative; preserve its ten factual artifacts byte-for-byte and do not repair or rerun it. Completed `02-green-initial-v5/rep-05` used the v5-and-later authority pair: primary `check_outputs_v2.py` (`7b39077be85a115d52ab7ad8d6f4cb4e11a09053dffe09a19e81708b42dc971b`) plus imported frozen `check_outputs.py` (v1 hash above). Controller predelegation hash-verifies both; completed v5 and any later pressure/final semantic argv name only v2. The initial-GREEN checkpoint is complete and accepted; next is read-only pressure readiness review, with no pressure evaluator authorized by this checkpoint.
7. **Real-chain semantic amendment (2026-08-11).** Flask exposed a false acceptance where individually supported tokens formed an impossible harness/oracle combination. The reviewer must now validate the complete `setup → action → observable result` chain and return blocking `HARNESS_ORACLE_MISMATCH` when the declared harness cannot produce the claimed response media type, body shape, status, or state. This focused amendment has deterministic regression evidence; it does not rewrite or claim completion of the still-pending formal FINAL scorecard.

## Task 5: tc-to-autotest campaign and real Java/Python trace

**Files:** `skills/tc-to-autotest/`, `tools/build_trace_document.py`, `tests/test_build_trace_document.py`, `docs/to_do/skill-tests/tc-to-autotest/`.

1. Apply its cleanup-map row. Add RED tests for `build_trace_document.py`: it accepts one existing schema-valid `context-marker-output` envelope as `--requirements` and extracts `artifacts.analytics_documentation.requirements`; it accepts one existing schema-valid `tc-generator-output` envelope as `--test-cases` and extracts `artifacts.generated_test_cases.requirements` and `.test_cases`; it accepts one language automation envelope and one runner result; copies `run_id` and `execution_evidence` verbatim; rejects absent/fabricated runner evidence. Validate the two input envelopes with their existing schemas before calling the builder. No new requirement/case schema is invented. Its exact CLI is:

```powershell
& $py "$pack\tools\build_trace_document.py" --requirements <requirements.json> --test-cases <cases.json> --automation-artifact <language-artifact.json> --run-result <run-result.json> --output <trace-document.json>
```

2. Pre-scaffold two isolated runnable projects for **each** phase/repetition at `artifacts/workspaces/<phase>/rep-<nn>/java` and `.../python`; repetitions never share a project. The Java scaffold contains `pom.xml`, `mvnw.cmd`, `.mvn/wrapper/`, JUnit 5 dependency/configuration, production source, and an empty `src/test/java/` generated-test location. Before Java eligibility set `$env:JAVA_HOME = 'D:\AI-Projects\.tools\jdk-17'`, then require JDK 17 exactly: `& "$env:JAVA_HOME\bin\java.exe" -version` reports 17, set `$env:Path = "$env:JAVA_HOME\bin;$env:Path"`, and assert `Test-Path "$workspace\java\mvnw.cmd"`. The Python scaffold contains `pyproject.toml`, `pytest.ini`, importable `src/sample_app/__init__.py`, production source, and an empty `tests/generated/` location. Convention examples remain in `artifacts/inputs/existing-conventions/<language>/`, never in generated locations; no workspace contains a prior answer/source.
3. The evaluator writes generated source directly to that repetition's empty generated-test location and writes two machine-authority envelopes in its output directory: `tc-to-autotest-java-output.json` and `tc-to-autotest-python-output.json`. Each is schema-valid `tc-to-autotest-output`; each lists only its language/framework (`java`/`junit5` or `python`/`pytest`), project-relative generated file paths, matrix, methods, and file SHA-256 digests. Schema envelopes do not contain source bytes; declared source files are companions, never manual execution evidence.
4. The deterministic controller loops phases `01-red-control`, `02-green-initial`, `04-green-final` and reps `rep-01` through `rep-05` (pressure has its own single output directory). For each Java/Python envelope it runs `validate_artifact.py` first and preserves the validation result. A RED invalid envelope is recorded in its RED repetition and stops there: it never invokes Maven, pytest, or trace construction. Only a schema-valid eligible GREEN envelope may run. `run_tests.py --automation-artifact` must validate schema, project-relative confined paths, actual file digest, matrix ownership, language/framework, and exact declared method names; it must not claim method content digests are verified. Preserve `run-result-java.json` and `run-result-python.json` beside that exact repetition's outputs.

```powershell
$javaRun = & $py "$pack\tools\run_tests.py" --project "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\workspaces\04-green-final\rep-05\java" --language java --automation-artifact "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-java-output.json"; $javaNativeExit = $LASTEXITCODE; $javaRun | Set-Content "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-java.json" -Encoding utf8; if ($javaNativeExit -ne 0) { throw 'java runner failed' }; & $py "$pack\tools\validate_artifact.py" "$pack\schemas\run-tests-output.schema.json" "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-java.json"; $javaValidatorExit = $LASTEXITCODE; if ($javaValidatorExit -ne 0) { throw 'java runner artifact schema invalid' }; $javaResult = Get-Content -Raw "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-java.json" | ConvertFrom-Json; if ($javaResult.exit_code -ne 0 -or $javaResult.verdict -ne 'PASS' -or -not $javaResult.evidence_authoritative) { throw 'java runner result is not authoritative PASS' }
$pythonRun = & $py "$pack\tools\run_tests.py" --project "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\workspaces\04-green-final\rep-05\python" --language python --automation-artifact "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-python-output.json"; $pythonNativeExit = $LASTEXITCODE; $pythonRun | Set-Content "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-python.json" -Encoding utf8; if ($pythonNativeExit -ne 0) { throw 'python runner failed' }; & $py "$pack\tools\validate_artifact.py" "$pack\schemas\run-tests-output.schema.json" "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-python.json"; $pythonValidatorExit = $LASTEXITCODE; if ($pythonValidatorExit -ne 0) { throw 'python runner artifact schema invalid' }; $pythonResult = Get-Content -Raw "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-python.json" | ConvertFrom-Json; if ($pythonResult.exit_code -ne 0 -or $pythonResult.verdict -ne 'PASS' -or -not $pythonResult.evidence_authoritative) { throw 'python runner result is not authoritative PASS' }
```

5. Build and validate one trace document per language from the final runner result, then require execution:

```powershell
& $py "$pack\tools\build_trace_document.py" --requirements "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\inputs\context-marker-output.json" --test-cases "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\inputs\tc-generator-output.json" --automation-artifact "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-java-output.json" --run-result "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-java.json" --output "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\trace-document-java.json"
& $py "$pack\tools\trace_check.py" "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\trace-document-java.json" --require-execution
& $py "$pack\tools\build_trace_document.py" --requirements "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\inputs\context-marker-output.json" --test-cases "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\inputs\tc-generator-output.json" --automation-artifact "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-python-output.json" --run-result "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\run-result-python.json" --output "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\trace-document-python.json"
& $py "$pack\tools\trace_check.py" "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\trace-document-python.json" --require-execution
```

6. TypeScript/Go never enter generator runs. Capability preflight writes only this schema-valid `orchestrator-output.json` shape (with a real lower-case SHA-256), then validates it: `{"schema_version":"2.1.0","stage":"orchestrate","warnings":[],"artifacts":{"run_tests_verdict":{"verdict":"NOT_RUNNABLE","reason":"<capability reason>"},"execution_evidence":[],"trace_audit":{"verdict":"FAIL","mappings":[],"errors":["<capability reason>"],"source_digest":"sha256:<64-lowercase-hex>"}}}`. It has no command, runner, exit code, generator envelope, or trace acceptance. Java/Python may not be `NOT_RUNNABLE`.
7. Run the global campaign protocol, scorer, validator/link test, and Sol review. Acceptance needs real Maven and pytest PASS runner evidence and verbatim trace evidence.
8. **Real-chain-driven portable implementation checkpoint (2026-08-11).** Use the observed failures as implementation RED: `step5-java-demo` rejected an invented `BaseApiTest` requirement despite existing `@WebMvcTest + MockMvc`; InvenTree omitted endpoint permissions and returned 403; PocketBase exposed copied credentials and Java-only review assumptions. The implementation must prefer project-native exemplars, preserve source-confirmed roles/permissions/fixtures/setup/client/auth, refuse secret persistence and unresolved stacks, write only isolated generated companions, and keep exact TC→REQ→setup→action→data→oracle→FILE→METHOD topology. `tools/build_trace_document.py` now constructs the execution trace from schema-valid pipeline artifacts and accepts one runner `run_id` shared by multiple method evidence entries while preserving `(run_id, method_id)` uniqueness.

## Task 6: autotest-reviewer campaign before execution

**Files:** `skills/autotest-reviewer/`, `docs/to_do/skill-tests/autotest-reviewer/`.

1. Apply its cleanup-map row. Create four labelled inputs containing only generated cases/matrix/files/methods: `java-valid.json`, `java-defective.json`, `python-valid.json`, `python-defective.json`; no runner verdict/evidence is canonical reviewer input.
2. The repeated prompt reviews all four labels. Every rep writes four separately named and schema-valid `autotest-reviewer-output` envelopes: `java-valid-autotest-reviewer-output.json`, `java-defective-autotest-reviewer-output.json`, `python-valid-autotest-reviewer-output.json`, `python-defective-autotest-reviewer-output.json`. Validate each against `autotest-reviewer-output.schema.json` and score one verdict per input.
3. Pressure includes only an unsupported textual claim that tests pass; rubric requires not treating it as evidence, accepting valid inputs, and rejecting defects. Complete the global protocol, validator/link test, and task-owned commit.
4. **Real-chain-driven portable implementation checkpoint (2026-08-11).** Review generated source independently rather than trusting generator metadata. Require language-native traceability, complete and exact input-case coverage, no extra executable tests, exact setup/action/data/oracle equivalence, project-native roles/permissions/fixtures/auth, no secrets, and no execution claims. Runner output remains the only execution authority. The focused package tests and existing real-chain artifacts cover the Java, Python, Go, and TypeScript failure classes found in practice; broader fresh-model acceptance is deferred to the end-of-plan gate.

## Task 7: orchestration campaign with executable traces

**Files:** `skills/orchestrate/`, `docs/to_do/skill-tests/orchestrate/`.

1. Apply its cleanup-map row. Every rep has isolated runnable Java and Python projects at `artifacts/workspaces/<phase>/rep-<nn>/java` and `.../python`, language-specific automation envelopes and toolchains, and stage inputs in `artifacts/inputs/`. GREEN allowlist is this skill plus declared references and the direct lower skill paths/tools in `pipeline.json`; no plugin.
2. Each rep persists separate language authorities: `orchestrator-output-java.json` matching only `tc-to-autotest-java-output.json`, `run-result-java.json`, `trace-document-java.json`, `trace-result-java.json` (actual `trace_check.py` stdout), and `trace-check-exit-java.json` (native exit); and equivalent `*-python.json` files. An optional human report is non-authoritative. Validate every stage envelope and assert the separate `trace-check-exit-<language>.json` has native exit `0` only after its matching trace/orchestrator validation succeeds; never replace trace stdout with synthetic JSON.
3. Invoke isolated final-green projects with these exact language artifacts/toolchains, then capture the two trace checks and asserted-zero exit results in distinct trace-result files:

```powershell
$orchestrateJavaRun = & $py "$pack\tools\run_tests.py" --project "$pack\docs\to_do\skill-tests\orchestrate\artifacts\workspaces\04-green-final\rep-05\java" --language java --automation-artifact "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-java-output.json"; $orchestrateJavaNativeExit = $LASTEXITCODE; $orchestrateJavaRun | Set-Content "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\run-result-java.json" -Encoding utf8; if ($orchestrateJavaNativeExit -ne 0) { throw 'orchestrate java runner failed' }; & $py "$pack\tools\validate_artifact.py" "$pack\schemas\run-tests-output.schema.json" "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\run-result-java.json"; $orchestrateJavaValidatorExit = $LASTEXITCODE; if ($orchestrateJavaValidatorExit -ne 0) { throw 'orchestrate java runner artifact schema invalid' }; $orchestrateJavaResult = Get-Content -Raw "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\run-result-java.json" | ConvertFrom-Json; if ($orchestrateJavaResult.exit_code -ne 0 -or $orchestrateJavaResult.verdict -ne 'PASS' -or -not $orchestrateJavaResult.evidence_authoritative) { throw 'orchestrate java result is not authoritative PASS' }
$orchestratePythonRun = & $py "$pack\tools\run_tests.py" --project "$pack\docs\to_do\skill-tests\orchestrate\artifacts\workspaces\04-green-final\rep-05\python" --language python --automation-artifact "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-python-output.json"; $orchestratePythonNativeExit = $LASTEXITCODE; $orchestratePythonRun | Set-Content "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\run-result-python.json" -Encoding utf8; if ($orchestratePythonNativeExit -ne 0) { throw 'orchestrate python runner failed' }; & $py "$pack\tools\validate_artifact.py" "$pack\schemas\run-tests-output.schema.json" "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\run-result-python.json"; $orchestratePythonValidatorExit = $LASTEXITCODE; if ($orchestratePythonValidatorExit -ne 0) { throw 'orchestrate python runner artifact schema invalid' }; $orchestratePythonResult = Get-Content -Raw "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\run-result-python.json" | ConvertFrom-Json; if ($orchestratePythonResult.exit_code -ne 0 -or $orchestratePythonResult.verdict -ne 'PASS' -or -not $orchestratePythonResult.evidence_authoritative) { throw 'orchestrate python result is not authoritative PASS' }
$javaTraceStdout = & $py "$pack\tools\trace_check.py" "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\trace-document-java.json" --orchestrator-artifact "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\orchestrator-output-java.json" --require-execution; $javaTraceExit = $LASTEXITCODE; $javaTraceStdout | Set-Content "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\trace-result-java.json" -Encoding utf8; @{ language = 'java'; native_exit_code = $javaTraceExit } | ConvertTo-Json | Set-Content "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\trace-check-exit-java.json" -Encoding utf8; if ($javaTraceExit -ne 0) { throw 'java trace check failed' }
$pythonTraceStdout = & $py "$pack\tools\trace_check.py" "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\trace-document-python.json" --orchestrator-artifact "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\orchestrator-output-python.json" --require-execution; $pythonTraceExit = $LASTEXITCODE; $pythonTraceStdout | Set-Content "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\trace-result-python.json" -Encoding utf8; @{ language = 'python'; native_exit_code = $pythonTraceExit } | ConvertTo-Json | Set-Content "$pack\docs\to_do\skill-tests\orchestrate\artifacts\outputs\04-green-final\rep-05\trace-check-exit-python.json" -Encoding utf8; if ($pythonTraceExit -ne 0) { throw 'python trace check failed' }
```

4. `NOT_RUNNABLE`, `FAIL`, or rework is not final acceptance. Java/Python must execute; TypeScript/Go follows Task 5's validated `NOT_RUNNABLE` preflight. Complete global protocol, validator/link test, Sol review, and task-owned commit.
5. **Real-chain-driven portable implementation checkpoint (2026-08-11).** Replace the legacy XML/quickstart/retry package with one compact JSON workflow and `references/orchestration-contract.md`. Resolve stages only from `contracts/pipeline.json`; validate every stage before forwarding; always create and verify the tc-generator CSV companion while preserving JSON as downstream authority; stop on reviewer rework, runner non-PASS, or trace mismatch; and never mutate production/config/dependencies to fit tests. Project-neutral PASS/NOT_RUNNABLE fixtures must execute the existing orchestrator schema and trace cross-check. This focused implementation checkpoint does not claim the still-pending formal evaluator scorecards.

## Task 8: byte-preserving adapters

**Files:** `adapters/generic/install_skills.py`, `adapters/windows/install.ps1`, optional `adapters/codex/README.md`, `tests/test_adapters.py`, `docs/to_do/skill-tests/adapters/`.

1. Implement exact CLIs: `python adapters/generic/install_skills.py --source <skills-root> --destination <dir> [--dry-run]` and `adapters/windows/install.ps1 -SkillPackRoot <pack-root> -Destination <dir> [-WhatIf]`. Neither changes `contracts/pipeline.json`.
2. Add exactly six tests: `test_generic_install_copies_canonical_bytes`, `test_generic_second_install_same_destination_is_unchanged`, `test_generic_dry_run_creates_no_destination`, `test_windows_whatif_creates_no_destination`, `test_windows_install_copies_canonical_bytes`, `test_direct_generic_install_requires_no_host_adapter`.
3. Snapshot canonical source bytes **and timestamps** before/after each adapter test. Invoke generic twice into the same `artifacts/install-generic/` destination, and Windows twice into the same `artifacts/install-windows/` destination; assert second invocation mutates neither destination bytes/timestamps and adapters do not mutate source. Assert Python dry-run and PowerShell `-WhatIf` create no destination. For direct mode, temporarily rename/unmake available the `adapters/` directory in an isolated fixture and read/invoke all six canonical `skills/<id>/SKILL.md` paths directly; it must not call an installer.
4. Run six tests, full suite, and task-owned commit.
5. **Implementation checkpoint (2026-08-11).** Both installers are implemented without dependencies on a host plugin. The six exact tests observed 5 expected missing-script failures plus one direct-mode pass, then 6/6 GREEN. Permanent generic/Windows destinations under `docs/to_do/skill-tests/adapters/artifacts/` match the final six-package source by relative path, SHA-256 and mtime; each second invocation reports zero copies and 20 unchanged files; dry-run/WhatIf create nothing.

## Task 9: root-link, stale-authority, package, and evidence acceptance audit

**Files:** update exactly root `CONTRACTS.md`, `Instruction.md`, `PIPELINE.md`, `ROADMAP.md`, `USER-GUIDE.md`; `docs/` authoritative Markdown; `skills/`; `adapters/`; `tests/test_portable_package.py`; `docs/to_do/skill-tests/` acceptance report.

1. Put the six old-directory denylist and exact obsolete-authority patterns in Python test data, never in scanned Markdown. Scan authority boundaries only: parsed `contracts/pipeline.json` artifact declaration tables; `accepts`, `forwards`, and `output` declarations in `skills/**/SKILL.md` and `skills/**/references/**/*.md`; and root-contract artifact declaration lines in the five named root docs. Test-data patterns are `ARTIFACT_ALIAS_RE = r'^(generated_files|trace_map|automation_bundle_json)$'`, `SKILL_DECL_RE = r'^\s*(accepts|forwards|output)\s*:'`, and `ROOT_ARTIFACT_DECL_RE = r'^(?:\||[-*]\s+).*\b(artifact|accepts|forwards|output)\b'`; compare declared token values to `ARTIFACT_ALIAS_RE`, rather than grepping arbitrary prose. Reject the six old directory strings there. The only migration exception is the six exact `git mv "$pack\<old>" "$pack\skills\<id>"` command lines in this plan, tested as exact line strings rather than allowing the whole plan. `generated_files` and `trace_map` remain allowed only in canonical trace-document schema/field-reference contexts and generated trace evidence, which are outside this authority-boundary scan.
2. Add a deterministic local Markdown-link test over exactly the five named root docs, `docs/**/*.md`, `skills/**/*.md`, and `adapters/**/*.md`; exclude `docs/to_do/skill-tests/**/artifacts/**`, vendored directories, generated build output, and fixture trees. Collect inline links and reference definitions whose target has no URI scheme, `#` fragment, or mailto; resolve from the source file's parent, strip fragment/query, and require an existing file or directory. Assert exactly six `skills/<id>/SKILL.md` paths, no package README/SKILL-LITE/templates, and every campaign phase/metadata/scorecard JSON validates `skill-test-evidence.schema.json`.
3. Update only the five enumerated root docs and authoritative `docs/`, `skills/`, and `adapters/` links. Run stale/link/package/evidence tests, all stage artifact validations, `contract_check.py --full`, `render_contract_docs.py --check`, full pytest, Ruff, and record fresh measurements. Commit only these enumerated Task 9 paths. Acceptance requires task-owned paths clean, RED failure or documented no-gap/no-edit evidence, final green all-pass, Java/Python PASS (never `NOT_RUNNABLE`), and TypeScript/Go validated `NOT_RUNNABLE` only.
4. **Implementation checkpoint (2026-08-11).** Added deterministic authority-boundary, local-link, package-shape and campaign-evidence tests. Removed the final tc-generator legacy docs; replaced stale XML/localized-path operational guidance in `Instruction.md`, `USER-GUIDE.md`, and `ROADMAP.md`; and retained historical audit/evaluator documents as non-authoritative evidence. Focused Task 8/9 gate is 9/9 GREEN. Formal model scorecard debts remain explicitly pending rather than being converted into package acceptance claims.

## Plan self-review and final acceptance

- [x] Gate 0 and Task 1 use current `render_contract_docs.py`, root projections, `--full`, and `--check` commands.
- [x] Task 1B validates evidence before campaigns; campaigns use identical five-repetition prompts, phase-preserved outputs, pressure-before-refactor, and per-repetition×rubric score results.
- [x] Task 5 has isolated per-repetition Java/Python projects, separate artifacts, real runner/trace commands, and no fabricated evidence or method-digest claim.
- [x] Tasks 4/6 preserve four independent reviewer verdict envelopes; Task 7 cross-checks traces with orchestrator output.
- [x] Cleanup is mapped to named references/assets before deletion; adapters prove byte/timestamp idempotency and adapter-free direct mode; Task 9 has exact stale/XML and link rules.
- [x] Capability preflight includes schema-required `trace_audit.source_digest`; only valid GREEN artifacts execute, and Task 9 scopes denylist/link checks away from canonical trace data and fixture trees.

Implementation is accepted only after all final green scorecards pass, every required envelope/evidence JSON validates, Java and Python have runner-produced execution-required traces, adapter tests pass, no task-owned file is dirty, and Sol has no unresolved finding.
