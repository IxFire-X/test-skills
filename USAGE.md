# Использование Test Skills V3

## Подключение и границы

Храните пакет отдельно от целевого проекта либо сохраняйте его структуру целиком.
В целевом проекте `.skillsrc` описывает язык, build tool и пути; его нормативная
форма — `schemas/skillsrc.schema.json`. Этот манифест не разрешает менять рабочий
исходный код, существующие тесты, конфигурацию, зависимости или lock files.

Если `.skillsrc` отсутствует, проект можно read-only обследовать:

```bash
python <skill-pack>/tools/scan_project.py --project <project> --target <feature-relative-path>
```

Постоянный `--output` для scanner допустим только в точном `docs/to_do`. Каждый
логический запуск использует новый каталог внутри
`<project>/docs/to_do/test-pipeline/<feature>/<attempt>/`; не перезаписывайте
неудачный attempt. Не передавайте bearer-токены, cookie, пароли, ключи или реальные
секреты. Для runtime применяйте только существующие безопасные project mechanisms.

Проверьте пакет до работы:

```powershell
python tools\doctor.py --root .
python tools\contract_check.py --root . --full
```

## Артефакты и публикация

`bare canonical JSON` — источник истины. Он хранит requirements, cases и
произвольное число ordered steps с human actions/results, typed data flow,
assertions, manual reasons и exact blockers. Markdown и Zephyr CSV — immutable
derived projections, которые downstream не парсит.

Для валидной revision publisher создаёт ровно три файла:

```text
<document-id>.r<revision>.json
<document-id>.r<revision>.md
<document-id>.r<revision>.zephyr-scale.csv
```

Используйте только module form:

```powershell
python -m tools.publish_test_case_bundle `
  --input <bare-canonical.json> `
  --output-dir <bundle-dir> `
  --csv-profile zephyr-scale-step-row-24-v1

python -m tools.publish_test_case_bundle `
  --input <bare-canonical.json> `
  --output-dir <bundle-dir> `
  --csv-profile zephyr-scale-step-row-24-v1 `
  --verify-only
```

Markdown — human projection with title, goal, preconditions and arbitrary steps.
Каждый шаг показывает `Action` и `Expected Result` в точной трёхколоночной таблице
`№`, `Действие`, `Ожидаемый результат`; отдельной Test Data column нет.

CSV profile `zephyr-scale-step-row-24-v1` содержит exactly 24 headers, one row per
canonical step и case metadata only on the first case row. Его cells formula-safe и
human-readable. Это неполная внешняя проекция: JSON из неё не восстанавливается.
Workbook/export structure was observed, but a real tenant import round trip remains unverified;
не заявляйте универсальную tenant import compatibility.

## Review и effective selection

Candidate schema+semantic validated и published до review. `ПРИНЯТО` выбирает
candidate; `AUTO_FIX_APPLIED` обязан вернуть полный valid successor с новой revision
и сохранёнными stable identities, который публикуется отдельно; `ТРЕБУЕТ ДОРАБОТКИ`
не получает effective document. Candidate/successor bundles остаются immutable audit
evidence, а exactly one `effective revision` и receipt идут downstream.

```powershell
python -m tools.orchestrate_test_case_revision `
  --candidate <bare-candidate.json> `
  --review <tc-reviewer-output.json> `
  --output-dir <bundle-dir> `
  --csv-profile zephyr-scale-step-row-24-v1
```

## Automation, runner и trace

Automation получает только selected canonical document и описывает generated files,
symbols с locator, atomic operation/assertion relations и manual dispositions. Its
runtime identity is `(file_id, symbol_id)`; all relations assigned to the same target
are AND. Global provider/adapter preflight happens before any target process/symbol,
and autotest review statically covers every required pair.

```powershell
python -m tools.run_tests `
  --project <isolated-project> `
  --language <python|java> `
  --canonical-document <effective-bare.json> `
  --automation-artifact <automation.json>

python -m tools.build_trace_document `
  --canonical-document <effective-bare.json> `
  --automation-artifact <automation.json> `
  --run-result <run-result.json> `
  --output <trace.json>

python -m tools.trace_check <trace.json> --require-execution
```

Only `--run-result` is omitted for a valid BLOCKED/manual no-run branch. Keep
`--require-execution` on every terminal trace check: it enforces the branch’s exact
execution obligation and accepts null execution only for the applicable no-run
semantics. There is no finalization CLI and no trace-check orchestrator-artifact flag.

Exact trace chain:

```text
requirement -> case -> step -> expectation -> assertion -> file -> symbol -> current-run evidence
```

Final statuses are exactly `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`,
`BLOCKED`, `FAIL`, `NOT_RUNNABLE`. BLOCKED/manual zero-pair branches skip runner
only; every terminal branch still builds and validates trace. FAIL and NOT_RUNNABLE
remain audited rather than dropped.

The Python finalization seam is
`orchestrate_revision(candidate, review_artifact, output_dir, csv_profile, ...)`,
`validate_trace_document(trace, document, automation, run_result=None)`, and
`finalize_orchestration(effective_document, effective_bundle_receipt,
automation_artifact, autotest_review_artifact, run_result, trace_document)`.

## V2.1 is unsupported

V2.1 input is rejected with a breaking-change diagnostic. There is no automatic
semantic migration and no mixed V2.1/V3 lifecycle.
