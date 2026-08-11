---
name: autotest-reviewer
description: >
  Use when запрос звучит как «проверь сгенерированные автотесты»,
  «отвалидируй Java-код автотестов» или нужен независимый review после
  tc-to-autotest до фактического запуска.
---

# Независимое ревью автотестов

Проверять результат `tc-to-autotest` как недоверенный артефакт. Канонические finding codes, verdict rules и порядок проверки читать в [references/autotest-review-contract.md](references/autotest-review-contract.md).

## Канонические входы

Принимать только:

- `generated_test_cases` либо `corrected_test_cases`;
- `automation_matrix`;
- `generated_test_files`;
- `generated_test_methods`;
- фактические companion source-файлы по объявленным confined paths.

Проверить схемы, ID-связи, существование файлов и их SHA-256 до смыслового review. Не принимать pasted prose, self-report генератора или утверждение пользователя о прохождении тестов как execution evidence.

## Независимая проверка смысла

Do not trust tc-to-autotest for assertion semantics. Для каждого входного `TC-*` independently reconstruct цепочку `setup → action → test data → oracle`, затем найти её реализацию в source.

Проверить:

- предусловия, роль, permissions, fixtures и исходное состояние;
- точную operation/endpoint/function и порядок действий;
- точные граничные/негативные данные и различие отсутствующего поля от `null`;
- каждый status, error code, response/body/header/state oracle;
- способность выбранного harness фактически породить этот oracle;
- cleanup/teardown и отсутствие межтестового состояния.

Считать blocking defect любой missing test case, любой extra executable test без входного `TC-*`, пропущенный oracle, выдуманное поведение или oracle that the generated harness cannot produce. Helper/fixture/factory не считать executable test.

## Project-native gate

Определить язык/framework по manifest, artifact и расширению файла. Сравнить код с ближайшим подтверждённым test exemplar.

Проверить необходимые roles, permissions, fixtures, setup hooks, client/application initialization и auth mechanism. Do not accept a base class name as sufficient evidence: если endpoint-specific setup отсутствует, запрос может завершиться 401/403 до проверяемого поведения.

Использовать нативные traceability anchors:

- Java/Kotlin — display name или framework-native эквивалент;
- Python — имя, marker или parameter ID;
- Go — `Test...`, table/subtest name;
- TypeScript/JavaScript — `test`/`it` title;
- иной framework — его стабильный отображаемый ID.

Не применять Java-only правила к другим языкам. Проверять stack fit относительно фактического проекта, а не фиксированного списка библиотек.

## Безопасность и границы

Отмечать `BLOCKING`, если generated source:

- содержит или копирует token/password/cookie/private key/fixture secret;
- изобретает auth helper, user, role или credential;
- меняет application behavior вместо его проверки;
- требует изменения manifest, lockfile, production source или существующих тестов;
- использует hardcoded environment endpoint вопреки project-native configuration.

Do not modify application or project files. Не исправлять generated source внутри review. `corrections` описывают только безопасные механические поправки metadata/traceability; semantic и blocking defects возвращать в `tc-to-autotest`.

## Что остаётся runner’у

Не заявлять и не подменять:

- компиляцию/интерпретацию;
- фактический запуск;
- pass/fail, coverage, timing;
- flaky/runtime behavior, недоказуемое статически.

При этом очевидные unresolved placeholders, отсутствующий assertion и противоречивый source являются статическими review defects. Фраза «тесты проходят» без runner-produced evidence ничего не меняет.

## Машинный выход

Выводить только schema-valid [../../schemas/autotest-reviewer-output.schema.json](../../schemas/autotest-reviewer-output.schema.json):

- `schema_version: "2.1.0"`;
- `stage: "autotest-reviewer"`;
- `warnings`;
- `artifacts.autotest_review` с `verdict`, `reviewed_file_ids`, `reviewed_method_ids`, `findings`, `corrections`.

Verdict:

- `ПРИНЯТО` — полный review, нет findings и corrections;
- `AUTO_FIX_APPLIED` — только неблокирующие механические corrections, без `BLOCKING`;
- `ТРЕБУЕТ ДОРАБОТКИ` — минимум один `BLOCKING`, corrections пусты.

Каждый finding должен ссылаться на конкретные `TC-*`, `REQ-*`, `FILE-*` или `METHOD-*` и содержать проверяемое evidence. Не использовать общий текст без location/field/ожидания.

## Самопроверка

Перед завершением проверить:

- все declared digests и ID-связи;
- все и только входные executable cases;
- точность setup, action, data и каждого oracle;
- project-native roles/permissions/fixtures/auth;
- язык и traceability anchor;
- отсутствие секретов и project mutations;
- отсутствие unsupported execution claims;
- согласованность verdict/findings/corrections;
- JSON по схеме.
