# Контракт оркестрации

## Авторитеты

| Назначение | Канонический источник |
|---|---|
| Порядок, skill paths, accepts/forwards/transitions | `contracts/pipeline.json` |
| Контракт pipeline | `schemas/pipeline.schema.json` |
| Stage output | `schemas/<stage>-output.schema.json` |
| Исполнение | `tools/run_tests.py` + `schemas/run-tests-output.schema.json` |
| Trace topology | `tools/build_trace_document.py` + `schemas/trace-document.schema.json` |
| Trace semantics | `tools/trace_check.py` |
| Финальный envelope | `schemas/orchestrator-output.schema.json` |

Если prose расходится с этими файлами, остановись и сообщи contract mismatch.

## Маршрут артефактов

| Этап | Вход | Выход и решение |
|---|---|---|
| context-marker | `raw_content` | `analytics_documentation`, `source_code_and_diff` |
| tc-generator | оба предыдущих JSON-блока | `generated_test_cases`; затем обязательный CSV companion |
| tc-reviewer | `generated_test_cases` | original при `ПРИНЯТО`; `corrected_test_cases` при `AUTO_FIX_APPLIED`; stop при rework |
| tc-to-autotest | выбранные cases + validation report | `automation_matrix`, `generated_test_files`, `generated_test_methods` |
| autotest-reviewer | cases + automation artifacts + source companions | accepted/rework `autotest_review`; без execution claims |
| run-tests | validated automation artifact + isolated project | `run_tests_verdict`, method-level `execution_evidence` |
| trace-check | requirements, cases, files, methods, runner evidence | `trace_audit` |

CSV нужен для Jira Zephyr-ориентированного переноса и просмотра человеком. Он обязан точно восстанавливаться в исходный ordered `test_cases`, но downstream stage всегда читает JSON.

## Исполнимый порядок

Используй абсолютные пути в фактическом command ledger и абсолютный cwd. В командах ниже `<root>` — абсолютный корень package.

1. Валидируй stage artifact:

   `python <root>/tools/validate_artifact.py <root>/schemas/<stage>-output.schema.json <artifact.json>`

2. После tc-generator:

   `python <root>/skills/tc-generator/scripts/export_test_cases_csv.py --input <tc-generator-output.json> --output <tc-generator-output.csv>`

   `python <root>/skills/tc-generator/scripts/export_test_cases_csv.py --input <tc-generator-output.json> --output <tc-generator-output.csv> --verify-only`

3. После принятого autotest review:

   `python <root>/tools/run_tests.py --project <isolated-project> --language <java|python> --automation-artifact <tc-to-autotest-output.json>`

   Сохрани stdout без изменения как run-result JSON и валидируй `run-tests-output.schema.json`.

4. Построй trace:

   `python <root>/tools/build_trace_document.py --requirements <context-marker-output.json> --test-cases <tc-generator-output.json> --automation-artifact <tc-to-autotest-output.json> --run-result <run-result.json> --output <trace-document.json>`

5. Получи trace audit:

   `python <root>/tools/trace_check.py <trace-document.json> --require-execution`

6. Собери `orchestrator-output.json` только из сохранённых run/trace facts, провалидируй его и выполни cross-check:

   `python <root>/tools/trace_check.py <trace-document.json> --orchestrator-artifact <orchestrator-output.json> --require-execution`

Native exit каждого command сохраняй отдельно от stdout. Не синтезируй runner или trace receipts вручную.

## Project boundary

Разрешены read-only source inspection и новый generated test companion в заранее выбранном изолированном каталоге. Не разрешены изменения production source, существующих tests, configuration, lockfiles, dependencies, permissions или application behavior ради прохождения теста.

Если generated test не совместим с проектом, исправляй генератор/входной контракт в новой попытке. Никогда не подстраивай проект под тест-кейс.

## Контекст этапа

Передавай:

- declared JSON inputs;
- необходимые файлы generated source;
- только source-backed project conventions/setup/roles/permissions/auth/oracles;
- точный output path.

Не передавай:

- ответы предыдущего evaluator;
- скрытый oracle или scorecard;
- reasoning/черновики;
- лишние project files;
- credentials и environment secrets.

## Финальный envelope

`orchestrator-output.json` должен пройти `schemas/orchestrator-output.schema.json`.

- `run_tests_verdict` копирует verdict/reason/command/runner/exit из runner result.
- `execution_evidence` нормализует только сохранённые method-level runner records.
- `trace_audit` копируется из `trace_check.py`.
- `PASS` требует exit 0, непустое evidence и trace `PASS`.
- `FAIL` и `NOT_RUNNABLE` — честные terminal states, не acceptance.

Минимальный PASS и честный `NOT_RUNNABLE` находятся в `../assets/orchestration-fixtures/`.
