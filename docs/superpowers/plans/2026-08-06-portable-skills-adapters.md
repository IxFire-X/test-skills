# Portable Skills and Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Make the six testing skills portable, prove their schema contracts in a fresh-agent campaign, and provide byte-preserving installation adapters.

**Architecture:** `pipeline.json` is the sole registry for canonical skill locations and artifact routing. Each skill keeps its interface in `SKILL.md`, references, scripts, and assets only. The deterministic controller, not a reviewer or a Markdown report, validates every machine artifact and execution trace.

**Tech Stack:** Markdown skills, JSON Schema Draft 2020-12, Python 3.11, PowerShell, pytest, Maven, pytest (Python projects).

## Global constraints and campaign protocol

Work from the worktree root and use these variables in every command:

```powershell
$py = 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe'
$pack = 'test-orchestration-skills'
$validator = 'C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py'
```

- Preserve unrelated changes. Do not add package `README.md`, `SKILL-LITE.md`, or a `templates/` directory. A portable package contains only `SKILL.md`, `references/`, `scripts/`, and `assets/`.
- Do not change a skill's wording while relocating it in Task 1. `git mv` moves contents; subsequent skill tasks make the only intentional skill-content edits.
- Run TDD red/green/refactor for every production change. Add the named narrow test before implementation, demonstrate its failure, then make it pass.
- Every changed JSON envelope is validated with `tools/validate_artifact.py`; `quick_validate.py` is supplemental skill-package validation only.
- An evaluator is observation-only: it may write only its assigned evidence and artifact files. The implementation owner applies fixes after a batch. A fresh evaluator receives no expected verdict, scorecard conclusion, or previous evaluator output.

### One campaign layout for every `context-marker`, `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer`, and `orchestrate` campaign

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
  artifacts/outputs/
```

`00-scenario.json` stores one canonical comprehensive application prompt, one separate pressure prompt, its raw-input allowlist, and rubric IDs. Run the **identical canonical prompt** five times for RED (`rep-01` through `rep-05`) and five times for each GREEN phase. Do not turn repetitions into five different samples. If a true variant is later needed, it is a second scenario and receives its own five repetitions; this plan deliberately uses one comprehensive scenario plus one pressure prompt.

Spawn every evaluator with `fork_turns: "none"`. Run each five-repetition batch sequentially (one active evaluator at a time), so the root plus observer work cannot exceed the global four-agent limit. Nested evaluators are feasible because they are short-lived fresh observation tasks; the owner waits for every evaluator before making a fix. A fresh Sol reviewer reads the completed scorecard and artifacts only; it does not edit files.

Each repetition Markdown file contains the raw prompt, raw observation, and command transcript only. If the stage has an output schema, the evaluator also writes the required schema-valid JSON envelope to `artifacts/outputs/<phase>/rep-<nn>/`; every quoted output filename below is relative to that phase-and-repetition directory unless it is explicitly a final deterministic-controller result. The controller validates it, for example:

```powershell
& $py "$pack\tools\validate_artifact.py" "$pack\schemas\tc-generator-output.schema.json" "$pack\docs\to_do\skill-tests\tc-generator\artifacts\outputs\02-green-initial\rep-01\tc-generator-output.json"
```

Use a unique output filename for labelled fixture outputs; never overwrite any evidence, output, scorecard, or metadata file. `06-run-metadata.json` records evaluator identity/model/host, `fork_turns`, UTC timestamps, prompt SHA-256, exact allowlist, skill-present flag, output file digests, and command exit codes. Each scorecard is JSON with one boolean per rubric ID, links to the five rep files, and `all_passed`; acceptance requires all five booleans true in the final GREEN scorecard. Task 9 adds and validates the evidence/scorecard schema.

## Gate 0: preserve the accepted runtime baseline

Plan 1 was accepted at `2894662`; its historical evidence (322 passed, 2 skipped; Ruff, contract/render, Maven 24/24, real pytest and fake Gradle) is context, not a frozen acceptance claim. Before and after this plan, record current measurements:

```powershell
& $py -m pytest "$pack\tests" -q
& $py -m ruff check "$pack\tools" "$pack\tests"
& $py "$pack\tools\contract_check.py" --root "$pack"
& $py "$pack\tools\render_pipeline.py" --root "$pack"
```

Commit no code in this gate. Continue only if any regression is diagnosed and resolved in its owning task.

## Task 1: establish the portable registry and artifact route

**Files:** Create `skills/`; move the six existing skill directories to `skills/<id>/`; modify `contracts/pipeline.json`, `schemas/pipeline.schema.json`, `tools/contract_check.py`, `tools/render_pipeline.py`, `docs/CONTRACTS.md`, `docs/PIPELINE.md`, `tests/test_contract_check.py`, `tests/test_contract_docs.py`.

1. Create the parent before moving: `New-Item -ItemType Directory -Force "$pack\skills" | Out-Null`.
2. Add red tests named `test_skill_files_registry_is_exact_and_steps_have_no_skill_file`, `test_tc_to_autotest_requires_validation_report_and_two_canonical_case_branches`, `test_contract_check_rejects_alias_artifact_in_autotest_stage`, and `test_rendered_contracts_include_canonical_skill_files`; run only these tests and capture their failure.
3. Add this exact `skill_files` object at the top level of `contracts/pipeline.json`:

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

4. In `pipeline.schema.json`, add `skill_files` to top-level `required`, and add an object property with `additionalProperties: false`, the same six-key `required` list, and one `const` property per exact path above. Do not add `steps[*].skill_file`.
5. Set `tc-reviewer.forwards` exactly to `generated_test_cases`, `validation_report`, `corrected_test_cases`. Set `tc-to-autotest.accepts` exactly to `validation_report`, `generated_test_cases`, `corrected_test_cases`; set its `forwards` exactly to those three plus `generated_test_files`, `generated_test_methods`. Keep canonical artifact names only.
6. Add `CANONICAL_SKILL_FILES` and, in `_semantic_errors`, reject a non-identical map, any step containing `skill_file`, and the three exact routing-set violations. Keep artifact-registry alias rejection. Add the contract table headed `## Canonical skill files` in `render_contracts`, regenerate `docs/CONTRACTS.md` and `docs/PIPELINE.md`, and make the renderer projection test assert the table.
7. Move without rewriting: `git mv "$pack\Разметка контекста" "$pack\skills\context-marker"`, then similarly move the five existing source directories to `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer`, and `orchestrate`. The `skill_files` registry and regenerated contract projections are the Task 1 route update; do not edit additional direct documentation links in this commit. Task 9 owns the repository-wide `rg -n "Разметка контекста|Генерация тест-кейсов|Валидация тест-кейсов|Автоматизированные кейсы|Валидация автотестов|Оркестратор" "$pack"` cleanup and its explicit historical allowlist. Do not invent step-level routes.
8. Run the four tests, `contract_check`, `render_pipeline`, the full test suite, and `& $py $validator "$pack\skills\<id>"` for all six packages. Request a fresh Sol review. Commit only the listed paths with `git add -- "test-orchestration-skills/skills" "test-orchestration-skills/contracts/pipeline.json" "test-orchestration-skills/schemas/pipeline.schema.json" "test-orchestration-skills/tools/contract_check.py" "test-orchestration-skills/tools/render_pipeline.py" "test-orchestration-skills/docs/CONTRACTS.md" "test-orchestration-skills/docs/PIPELINE.md" "test-orchestration-skills/tests/test_contract_check.py" "test-orchestration-skills/tests/test_contract_docs.py"`.

## Task 2: campaign the context marker

**Files:** `skills/context-marker/SKILL.md`, its `references/`, `scripts/`, and `assets/`; `docs/to_do/skill-tests/context-marker/`.

1. Put a schema-valid raw-content fixture in `artifacts/inputs/raw-content.json` and the canonical prompt in `00-scenario.json`: classify a supplied order-management change into source code, documentation, and requirement context while preserving quoted source facts. Require `context-marker-output.json`.
2. Run five RED evaluators without the skill, then five fresh GREEN evaluators with only this skill and its declared files; validate every output against `context-marker-output.schema.json`.
3. Score provenance preservation, no invented authentication/retention requirements, and a valid envelope. Apply minimal skill/reference fixes, run five fresh final GREEN repetitions, then run the separate pressure prompt and validate its output. Run `quick_validate.py` and commit evidence and package changes.

## Task 3: campaign the test-case generator

**Files:** `skills/tc-generator/`; `docs/to_do/skill-tests/tc-generator/`.

1. Store a schema-valid analytics/source fixture and one canonical prompt: generate boundary, negative, and role-aware cases for the supplied order change. Require `artifacts/outputs/tc-generator-output.json`.
2. Execute the global five RED, five initial GREEN, pressure, and five final GREEN protocol; validate each output with `tc-generator-output.schema.json`.
3. Score traceable requirement IDs, executable expected results, and no unstated authorization rules. Fix only the smallest missing instruction/reference, rerun final GREEN, run `quick_validate.py`, and commit.

## Task 4: campaign the test-case reviewer with branch-consistent fixtures

**Files:** `skills/tc-reviewer/`; `docs/to_do/skill-tests/tc-reviewer/`.

1. Create four separate schema-valid `tc-generator-output` envelopes in `artifacts/inputs/`: `clean-accepted.json`, `typo-only.json`, `blocking-missing-result.json`, and `blocking-fabricated-auth.json`. Label the case set inside each envelope.
2. The one repeated canonical prompt says to review all four labelled envelopes and write four outputs in its phase-and-repetition output directory: `clean-accepted-tc-reviewer-output.json`, `typo-only-tc-reviewer-output.json`, `blocking-missing-result-tc-reviewer-output.json`, and `blocking-fabricated-auth-tc-reviewer-output.json`. Validate all four with `tc-reviewer-output.schema.json` on every repetition.
3. The rubric requires: clean is accepted with no correction; typo-only is safely AUTO_FIX with a corrected case; missing-result and fabricated-auth are blocking, rejected, and do not silently invent a correction. Run the common RED/GREEN/pressure/final protocol, `quick_validate.py`, and commit.

## Task 5: prove tc-to-autotest through real execution and deterministic trace construction

**Files:** `skills/tc-to-autotest/`; `tools/build_trace_document.py`; `tests/test_build_trace_document.py`; `docs/to_do/skill-tests/tc-to-autotest/`.

1. Add red tests for `build_trace_document.py`: it accepts only canonical requirement/case/file/method IDs plus runner JSON, copies runner evidence verbatim, and rejects missing or fabricated execution evidence. Keep existing convention examples in `artifacts/inputs/existing-conventions/java/` and `.../python/`; keep destination trees separately empty in `artifacts/projects/java/` and `.../python/` before generation.
2. Store accepted reviewer output, generated cases, and source conventions as distinct inputs. The canonical prompt directs the agent to emit one machine-authority envelope, `tc-to-autotest-output.json`, plus the Java/Python source files declared by its `generated_test_files` entries in the separately empty destination trees. The envelope is the only machine-authority artifact: it contains the matrix, file paths, languages/frameworks, and SHA-256 file digests; the schema intentionally does not contain source bytes. The companion source files are generated outputs, not execution evidence and not a second JSON authority. The agent must not run tests or append evidence.
3. Use the campaign protocol and validate that sole generator artifact after every repetition:

```powershell
& $py "$pack\tools\validate_artifact.py" "$pack\schemas\tc-to-autotest-output.schema.json" "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-output.json"
```

4. After final GREEN, the deterministic controller verifies every declared companion path is inside its language project, exists, and byte-hashes to `generated_test_files[*].content_digest`; it rejects every undeclared source file. It then copies only those verified declared files into the empty Java/Python destination projects and invokes the real runner twice. `run_tests.py --automation-artifact` repeats the schema/path/digest/matrix/method binding before Maven or pytest:

```powershell
& $py "$pack\tools\run_tests.py" --project "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\projects\java" --language java --automation-artifact "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-output.json" | Set-Content "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\controller\run-result-java.json" -Encoding utf8
& $py "$pack\tools\run_tests.py" --project "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\projects\python" --language python --automation-artifact "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\04-green-final\rep-05\tc-to-autotest-output.json" | Set-Content "$pack\docs\to_do\skill-tests\tc-to-autotest\artifacts\outputs\controller\run-result-python.json" -Encoding utf8
```

5. Validate both runner envelopes with `run-tests-output.schema.json`. Run the new deterministic builder with the canonical case artifact and each runner result to create `trace-document-java.json` and `trace-document-python.json`; validate both with `trace-document.schema.json`, then run `trace_check.py <trace-document> --require-execution`. The builder never accepts manually appended evidence.
6. For TypeScript or Go capability preflight, emit no generator artifact. Instead emit and validate `orchestrator-output.json` using the existing `orchestrator-output` schema: `run_tests_verdict.verdict` is `NOT_RUNNABLE` with a capability reason and no command/runner/exit code, `execution_evidence` is empty, and `trace_audit` is `FAIL` with no mappings. This existing branch is sufficient; do not claim generator-stage `NOT_RUNNABLE` and do not add a new schema branch.
7. Score schema-only generator output, real Maven/pytest evidence, and trace evidence copied only from the runner. Run `quick_validate.py`, request Sol review, and commit.

## Task 6: campaign autotest review before run-tests

**Files:** `skills/autotest-reviewer/`; `docs/to_do/skill-tests/autotest-reviewer/`.

1. Create separate Java valid/defective and Python valid/defective fixtures containing only generated cases, matrix, files, and methods. Do not place a run verdict or execution evidence in canonical inputs.
2. The repeated canonical prompt reviews all four labelled fixtures and writes schema-valid `autotest-reviewer-output.json`; validate it with `autotest-reviewer-output.schema.json` for every repetition.
3. The pressure prompt includes a user's unsupported textual claim that tests pass. The rubric requires rejecting that claim as evidence, detecting the defective fixtures, and accepting only the valid fixtures on generated-artifact quality. Run RED/GREEN/pressure/final, `quick_validate.py`, and commit.

## Task 7: campaign canonical orchestration and trace authority

**Files:** `skills/orchestrate/`; `docs/to_do/skill-tests/orchestrate/`.

1. Put schema-valid stage inputs in `artifacts/inputs/`. The GREEN allowlist is this orchestration skill, its declared files, and the direct lower skill paths/tools named in `pipeline.json`; no plugin is allowed.
2. The canonical prompt directs the orchestrator to persist `artifacts/outputs/orchestrator-output.json`, `trace-document.json`, and `trace-result.json`; a human Markdown report is optional and never authoritative.
3. For every repetition validate every stage envelope with `validate_artifact.py`, validate the orchestrator output with `orchestrator-output.schema.json`, validate the trace document with `trace-document.schema.json`, and run `trace_check.py <trace-document> --require-execution`. Trace execution evidence must be runner-produced.
4. Score exact canonical routing, validated machine authority, and fail-closed behavior: `NOT_RUNNABLE`, `FAIL`, or rework is not final acceptance. Run the common campaign, `quick_validate.py`, and commit.

## Task 8: create byte-preserving adapters

**Files:** `adapters/generic/install_skills.py`, `adapters/windows/install.ps1`, optional `adapters/codex/README.md`; `tests/test_adapters.py`; `docs/to_do/skill-tests/adapters/`.

1. Add six exact adapter tests: `test_generic_install_copies_canonical_bytes`, `test_generic_second_install_same_destination_is_unchanged`, `test_generic_dry_run_creates_no_destination`, `test_windows_whatif_creates_no_destination`, `test_windows_install_copies_canonical_bytes`, and `test_direct_generic_install_requires_no_host_adapter`.
2. Implement `adapters/generic/install_skills.py` with explicit source and destination roots, `--dry-run`, deterministic copy, and a second invocation that does not mutate bytes or timestamps when files already match. Implement `adapters/windows/install.ps1` using the same canonical source and `-WhatIf`; keep `adapters/codex/README.md` manual/optional only.
3. In each test compare each installed destination byte-for-byte with `skills/<id>/`, install twice into the **same** destination, assert no second-install mutation, assert Python dry-run creates no destination, assert PowerShell `-WhatIf` creates no destination, and prove generic direct use without any adapter. Run the six tests, full suite, and commit.

## Task 9: final portable-package and evidence audit

**Files:** `schemas/skill-test-evidence.schema.json`; `tests/test_portable_package.py`; root documentation links and `docs/to_do/skill-tests/` evidence.

1. Add red tests that assert exactly the six canonical `skills/<id>/SKILL.md` paths; no localized canonical path reference or legacy XML authority outside explicit historical migration allowlist `docs/superpowers/plans/2026-08-06-portable-skills-adapters.md`; every local Markdown link resolves; and no package README, SKILL-LITE, or templates clutter exists.
2. Add `skill-test-evidence.schema.json` for scenario, scorecard, and run-metadata required fields. Validate all `00-scenario.json`, three scorecards, and `06-run-metadata.json` files in every campaign directory; assert required phase directories/files exist and that schema-stage outputs are validated by their stage schema.
3. Update root documentation links to the six canonical paths, run the portable-package tests, all artifact validations, contract/render checks, full pytest and Ruff, and record current measured results in the acceptance report. Do not copy historical counts as current evidence.
4. Self-review this plan and implementation diff: verify headings Gate 0 and Tasks 1–9, the exact evidence names/phases, all canonical output names, no `steps[*].skill_file`, no Markdown-only authority, and no unvalidated schema-stage artifact. Commit narrow paths only.

## Final acceptance

The implementation is accepted only when all final GREEN scorecards are all-pass, each schema-stage output and campaign metadata validates, Maven and pytest runner evidence produces execution-required traces, adapters pass the six byte/dry-run/idempotency tests, package cleanup tests pass, and the worktree is clean. A `NOT_RUNNABLE`, failed run, rework verdict, invalid envelope, or unresolved Sol finding is a failed acceptance, not a substitute for GREEN.
