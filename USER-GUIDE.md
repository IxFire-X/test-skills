# Руководство пользователя

## Полная цепочка

Одна фраза:

> Создай тест-кейсы и автотесты для этой фичи, проверь их, запусти и покажи trace.

Канонический маршрут:

1. context-marker нормализует требования и source observations.
2. tc-generator создаёт JSON test cases.
3. exporter всегда дублирует test cases в lossless CSV.
4. tc-reviewer проверяет полноту, лишние/неверные кейсы, data, roles, setup и observable oracles.
5. tc-to-autotest создаёт project-native generated tests.
6. autotest-reviewer независимо проверяет source, coverage и смысл.
7. run_tests.py выполняет generated methods.
8. build_trace_document.py строит REQ → TC → FILE → METHOD → RUN.
9. trace_check.py подтверждает execution-backed trace.
10. orchestrate выдаёт schema-valid final envelope.

JSON — машинная истина. CSV нужен человеку и для последующего Jira Zephyr-ориентированного переноса. Generated source — companion, а не доказательство запуска.

## Отдельные skills

| Задача | Skill | Канонический вход | Канонический выход |
|---|---|---|---|
| Нормализовать контекст | context-marker | raw_content | analytics_documentation + source_code_and_diff |
| Создать ручные кейсы | tc-generator | два предыдущих артефакта | generated_test_cases JSON + CSV companion |
| Проверить ручные кейсы | tc-reviewer | generated_test_cases | validation_report + original/corrected authority |
| Создать автотесты | tc-to-autotest | cases + validation_report | automation_matrix + generated files/methods |
| Проверить автотесты | autotest-reviewer | cases + automation artifacts/source | autotest_review |
| Выполнить всю цепочку | orchestrate | raw project/feature context | runner verdict + evidence + trace audit |

Используй пути только из contracts/pipeline.json.

## Что проверяют reviewers

tc-reviewer проверяет не только формальную структуру:

- все ли требования покрыты и нет ли лишних test cases;
- точны ли setup, action, data и expected result;
- подтверждены ли roles/permissions/auth;
- может ли заявленный harness фактически произвести ожидаемый status/body/media type/state;
- нет ли выдуманных правил и наблюдаемости.

autotest-reviewer проверяет:

- каждый accepted TC реализован и нет лишних executable tests;
- source соответствует exact setup/action/data/oracle;
- используется project-native architecture, fixtures и authorization;
- trace anchors соответствуют языку;
- secrets отсутствуют;
- нет неподтверждённых execution claims.

Reviewer acceptance ещё не доказывает, что код запускается. Это делает только runner.

## Вердикты

| Источник | Продолжить | Остановиться |
|---|---|---|
| schema validator | exit 0 | любой nonzero |
| reviewer | ПРИНЯТО; AUTO_FIX_APPLIED с corrected authority | ТРЕБУЕТ ДОРАБОТКИ; неизвестный verdict |
| run_tests.py | PASS, exit 0 | FAIL; NOT_RUNNABLE; nonzero |
| trace_check.py | PASS, exit 0 | FAIL; mismatch; nonzero |

Java/Python должны реально выполниться. NOT_RUNNABLE можно честно сохранить для unsupported capability, но нельзя назвать финальным успехом.

## Работа с проектом

Система изучает нужный bounded scope, а не весь проект без причины. При этом она должна проверить соседние setup/fixtures/permissions и project-native examples, необходимые для корректного теста.

Разрешено:

- read-only source inspection;
- isolated generated test companion;
- внешний test environment;
- targeted и затем project-native regression/full suite, если среда готова.

Запрещено:

- менять production source или behavior под test cases;
- переписывать existing tests;
- менять configuration, lockfiles или dependencies ради зелёного результата;
- сохранять реальные credentials/tokens;
- называть весь проект PASS, если прошёл только generated slice.

## Ошибки и повтор

При semantic/schema/protocol failure:

1. останови downstream stages;
2. сохрани artifact и receipts без перезаписи;
3. объясни root cause понятным языком;
4. исправь source input, skill или controller contract;
5. запусти новую attempt, сохранив старую.

Тот же command можно повторить только при host/transport failure до появления stage output.

## CSV и Jira Zephyr

Рядом с каждым валидным tc-generator JSON должен появиться CSV с колонками:

test_case_id, requirement_links, title, priority, categories, preconditions, test_data, ordered_steps, expected_outcome.

Structured cells содержат compact JSON и обратно восстанавливаются без потерь. Конкретный import mapping Zephyr зависит от версии/plugin configuration; перед массовым импортом проверь одну запись и при необходимости добавь отдельный adapter, не меняя canonical JSON.

## Установка

Прямое использование не требует adapter. Для копирования:

~~~text
python adapters/generic/install_skills.py --source skills --destination <dir>
~~~

На Windows:

~~~powershell
pwsh -NoProfile -File adapters/windows/install.ps1 -SkillPackRoot . -Destination <dir>
~~~

Подробности: [Instruction.md](Instruction.md). Машинные контракты: [CONTRACTS.md](CONTRACTS.md) и [PIPELINE.md](PIPELINE.md).
