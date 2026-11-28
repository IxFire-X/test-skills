---
name: tc-to-autotest
description: Use when the selected effective canonical test-case document must be translated into one project-native automation version with explicit operation and assertion coverage.
permissions:
  read: "step inputs from the controller"
  write: "role answer in <run>/incoming/ only"
  network: "none"
  external_delivery: "none"
  dependencies: "none"
  rollback: "delete the run directory"
---

# Project-native automation generation

Use only the selected effective canonical document and its digest. Read the document through its autotest view `review-views/<sha>.autotest.md` (`prepare` builds it and lists it in `read`): the canonical requirements with their testing-work constraints, the machine part whole — every ID, target, type and literal exactly, a secret as its handle — and the capabilities with their whole test boundary; without source requirements and test data. Open the full JSON only to diagnose a validator failure; never open the `effective-canonicals` record (it repeats the document). Read the [automation contract](references/automation-output-contract.md), the [answer sample](references/artifact-sample.md), output schema and required authorized project inputs. The meaning of the answer and its static fit to the runner are checked by `submit` before the write (`submit --dry-run` shows the diagnostics and writes no answer); read the validators' source only to diagnose a failure.

Emit a complete proposed generated file set; the controller alone materializes it in the
selected module's active test root after accepted static review. This role does not use an
attempt-local/isolated project copy and never authorizes execution.

## Procedure

1. Validate the selected document and discover the project only within authorized paths. Preserve project-native layout, framework, setup, and runtime-only secret handles. The generated framework must equal the selected `.skillsrc` module framework; a Java file path must be the class FQN under that module's selected native test root, never an attempt-local substitute. Не добавляй зависимости.
   No `@DisplayName` (or `@DisplayNameGeneration`) on a Java test class or method: the report matches the method name.
   Java: only the API of the project's level — `java_level` in the `read` of `prepare`; at 8 no `List.of`, `Map.of`, `var`, `orElseThrow()` without an argument, `isBlank`, `strip`, `repeat`, `Path.of`. Newer is refused by `submit` (`RUNNER_API_LEVEL`).
2. For direct canonical/provider-backed execution, run global provider/adapter preflight before a test process or symbol. An exact accepted generated-source chain is instead gated by its static binding review and then the project-native test process; do not fabricate its runtime bindings.
3. Emit `GENERATED` only when every automated operation, assertion and setup has its atomic operation/assertion/setup relation to a generated `(file_id, symbol_id)` pair. Preserve every canonical literal and binding exactly in source. Multiple required pairs for one target mean AND, not an alternative. `automation_revision=1` has null predecessor fields; the sole permitted `automation_revision=2` is a complete fresh artifact bound to the exact revision-1 automation digest and the exact `AUTO_FIX_APPLIED` review digest (the code writes both digests, you do not).
   Automation must not recover request values from `action`, `test_data`, Markdown, reviewer prose, helper defaults, or an old revision. If a request-affecting value has no selected canonical structured input, stop for rework.
4. Emit exactly one manual disposition for every manual step. Emit `BLOCKED` only for a canonical blocker with empty generated/relation/disposition arrays; a project-generation problem without a canonical blocker stops without fabricating a BLOCKED artifact.
   Partial automation: when some cases carry canonical blockers, emit `GENERATED` for the other cases and list exactly the cases with blockers, in canonical order, in `artifacts.excluded_cases` (`[{"case_id": …}]`; the autotest view names them). An excluded case gets no relations and no manual dispositions, even for its ready steps; every other case keeps full operation and assertion coverage. The validator compares the list with the canon (`AUTOMATION_EXCLUSION_MISSING`, `AUTOMATION_EXCLUSION_WITHOUT_BLOCKER`, `AUTOMATION_EXCLUSION_ORDER`, `AUTOMATION_EXCLUDED_CASE_TARGET`); when every case has a blocker, emit `BLOCKED` as before (`AUTOMATION_NOTHING_TO_AUTOMATE`).
5. Check the answer with `submit --dry-run` (meaning and runner fit; the answer is not written). Each `generated_files` row carries exact UTF-8 `content` and its matching SHA-256 `content_digest` (do not compute it, nor `source.*`, nor in revision 2 the predecessor digests: a missing one is put by the code; in revision 1 write both predecessor keys as `null`); the complete artifact has one deterministic digest for independent review. Never write project files or claim execution. Revision 2 corrects once and reads r1 as the reviewer did (`read` lists it; r1 as JSON and the whole view when those files cannot be built): files of code, `tables.txt` (symbols, relations, operations and ASSERT of the related steps), the view of the cases in parts (legend in part 1) and the review of r1; assemble a step from the view and the table by its id; change the code of r1 only by the corrections (`corrections`) and the `BLOCKING` findings of the review; `WARNING` and `INFO` are notes for the person, leave the code alone for them; make no check stricter than a `BLOCKING` finding or a correction asks, and never use a value the case does not have. Never create a third version, a partial/destructive correction, or any regeneration after runtime `FAIL`.

Generate functional application tests for the accepted cases. Use the declared application
boundary: prefer the external API when both routes are available; MockMvc with real
application components is also allowed unless this run explicitly requires the external
API. A module whose `.skillsrc` has `paths.context` holds only tests: no MockMvc and no application classes
(they are not on its classpath). The HTTP client the build already has (RestAssured), else
`java.net.http.HttpClient`; no new dependency. Reuse the project's client, configuration and setup/cleanup; credentials only as runtime secret handles. An
isolated class or method test cannot substitute for an application case. One request is
enough when it exercises that case and observes its required outcome.

Each generated test must reach the real behavior named by its operation relation and
assert the corresponding observed value or state. Do not replace the subject under test
with its expected answer, assert a constant against itself, swallow an unexpected
exception, or add skip/xfail/disabled markers to hide an unimplemented scenario.
Отказ — конкретный класс исключения и поле ошибки (`assertThatThrownBy` — всегда с `isInstanceOf` точного класса); точное значение вместо contains; без
проверок, которые всегда истинны (`>= 0`, единственный not-null). Mock only
an external boundary when the selected canonical scenario and project conventions permit
it; never mock the behavior the test claims to cover. Use deterministic data, explicit
setup and teardown through existing fixtures, order-independent tests, and bounded waits
for observed conditions instead of arbitrary sleeps. Missing fixtures/setup are rework.
Keep generated paths and process assumptions valid on Linux and Windows; use the selected
project runtime and native framework APIs, never a hardcoded host Python path.
Setup checks nothing: each `setup` of a case is a helper symbol (`setup` relation) called before the first step; it throws `SetupFailed` with its SETUP id and the response when it fails. В сквозном тесте первая упавшая проверка не скрывает остальные (`assertAll` или эквивалент из зависимостей проекта). Импорты, аннотации и API тестового фреймворка (JUnit, AssertJ, Spring Boot) пиши по версиям из файла сборки модуля (`pom.xml` / `build.gradle`; он главнее) и по импортам тестов проекта, не по памяти: оба — в `read` (основания `файл сборки` и `окружение тестов`). Для этого только читай файлы из `read`: без команд сборки, ничего вне проекта (`~/.m2`, кэш Gradle).
Missing URL, access, fixture or oracle is a concrete gap: identify its source, affected
checks and required clarification. Do not invent it or replace the case with a unit test.

Markdown/CSV and their human prose are never automation inputs.

## Stop conditions

Stop on invalid or schema_version 2.1.0 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Also stop when discovery, preflight, runtime setup, or a required project change is unresolved.
