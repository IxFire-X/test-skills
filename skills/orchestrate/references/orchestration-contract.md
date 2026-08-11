# Контракт оркестрации

## Источники истины

| Назначение | Канонический источник |
|---|---|
| порядок, пути скиллов, `accepts`, `forwards` и переходы | `contracts/pipeline.json` |
| контракт пайплайна | `schemas/pipeline.schema.json` |
| результат этапа | `schemas/<stage>-output.schema.json` |
| выполнение | `tools/run_tests.py` и `schemas/run-tests-output.schema.json` |
| структура трассировки | `tools/build_trace_document.py` и `schemas/trace-document.schema.json` |
| смысл трассировки | `tools/trace_check.py` |
| итоговый артефакт | `schemas/orchestrator-output.schema.json` |

Если поясняющий текст расходится с этими файлами, остановись и сообщи о
несоответствии контракта.

## Маршрут артефактов

| Этап | Вход | Результат и решение |
|---|---|---|
| `context-marker` | `raw_content` | `analytics_documentation`, `source_code_and_diff` |
| `tc-generator` | оба предыдущих блока JSON | `generated_test_cases`, затем обязательная CSV-копия |
| `tc-reviewer` | `generated_test_cases` | исходные тест-кейсы при `ПРИНЯТО`; `corrected_test_cases` при `AUTO_FIX_APPLIED`; остановка при запросе доработки |
| `tc-to-autotest` | выбранные тест-кейсы и отчёт проверки | `automation_matrix`, `generated_test_files`, `generated_test_methods` |
| `autotest-reviewer` | тест-кейсы, артефакты автоматизации и сопутствующий исходный код | принятый результат или запрос доработки в `autotest_review`, без утверждений о выполнении |
| `run-tests` | проверенный артефакт автоматизации и изолированный проект | `run_tests_verdict`, доказательства выполнения `execution_evidence` на уровне методов |
| `trace-check` | требования, тест-кейсы, файлы, методы и доказательства средства запуска | `trace_audit` |

CSV нужен для переноса в Jira Zephyr и просмотра человеком. Из него должны без
потерь восстанавливаться исходные упорядоченные `test_cases`, но каждый последующий
этап всегда читает JSON.

## Исполнимый порядок

В фактическом журнале команд используй абсолютные пути и абсолютный рабочий
каталог. В командах ниже `<root>` означает абсолютный корень пакета.

1. Проверь артефакт этапа:

   `python <root>/tools/validate_artifact.py <root>/schemas/<stage>-output.schema.json <artifact.json>`

2. После `tc-generator`:

   `python <root>/skills/tc-generator/scripts/export_test_cases_csv.py --input <tc-generator-output.json> --output <tc-generator-output.csv>`

   `python <root>/skills/tc-generator/scripts/export_test_cases_csv.py --input <tc-generator-output.json> --output <tc-generator-output.csv> --verify-only`

3. После принятой проверки автотестов:

   `python <root>/tools/run_tests.py --project <isolated-project> --language <java|python> --automation-artifact <tc-to-autotest-output.json>`

   Сохрани стандартный вывод без изменений как JSON результата запуска и проверь
   его по `run-tests-output.schema.json`.

4. Построй трассировку:

   `python <root>/tools/build_trace_document.py --requirements <context-marker-output.json> --test-cases <tc-generator-output.json> --automation-artifact <tc-to-autotest-output.json> --run-result <run-result.json> --output <trace-document.json>`

5. Выполни аудит трассировки:

   `python <root>/tools/trace_check.py <trace-document.json> --require-execution`

6. Собери `orchestrator-output.json` только из сохранённых фактов запуска и трассировки, проверь его по схеме и выполни перекрёстную проверку:

   `python <root>/tools/trace_check.py <trace-document.json> --orchestrator-artifact <orchestrator-output.json> --require-execution`

Сохраняй штатный код завершения каждой команды отдельно от стандартного вывода.
Не составляй квитанции средства запуска или трассировки вручную.

## Граница проекта

Разрешены чтение исходного кода без изменений и новый файл сгенерированных тестов
в заранее выбранном изолированном каталоге. Запрещено менять рабочий исходный код,
существующие тесты, конфигурацию, файлы блокировки версий, зависимости, разрешения
или поведение приложения ради прохождения теста.

Если сгенерированный тест несовместим с проектом, исправляй генератор или входной
контракт в новой попытке. Никогда не подстраивай проект под тест-кейс.

## Контекст этапа

Передавай:

- объявленные входные JSON;
- необходимые файлы сгенерированного исходного кода;
- только подтверждённые исходным кодом проектные соглашения, подготовку, роли, разрешения, аутентификацию и ожидаемые результаты;
- точный путь результата.

Не передавай:

- ответы предыдущего проверяющего;
- скрытый ожидаемый результат или оценочную карточку;
- внутренние рассуждения и черновики;
- лишние файлы проекта;
- учётные данные и секреты окружения.

## Итоговый артефакт

`orchestrator-output.json` должен пройти `schemas/orchestrator-output.schema.json`.

- `run_tests_verdict` копирует вердикт, причину, команду, средство запуска и код завершения из результата запуска.
- `execution_evidence` нормализует только сохранённые записи средства запуска на уровне методов.
- `trace_audit` копируется из `trace_check.py`.
- `PASS` требует кода завершения 0, непустых доказательств и результата трассировки `PASS`.
- `FAIL` и `NOT_RUNNABLE` — честные конечные состояния, а не принятие.

Минимальный `PASS` и честный `NOT_RUNNABLE` находятся в
`../assets/orchestration-fixtures/`.
