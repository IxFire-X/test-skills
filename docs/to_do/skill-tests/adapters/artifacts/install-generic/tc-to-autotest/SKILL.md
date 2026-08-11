---
name: tc-to-autotest
description: Создаёт проектно-нативные автотесты из принятых ручных тест-кейсов с полной трассировкой case→file→method. Использовать для автоматизации ТК в существующем или новом проекте на Java, Kotlin, Python, Go, TypeScript/JavaScript и других подтверждённых стеках.
---

# Генерация проектно-нативных автотестов

Работать только с принятыми тест-кейсами и объявленным контекстом проекта. Канонический контракт полей и инвариантов читать в [references/automation-output-contract.md](references/automation-output-contract.md).

## Канонические входы

Принимать только маршрутизированные артефакты стадии:

- `validation_report`;
- `generated_test_cases`;
- `corrected_test_cases`, если ревьюер действительно внёс допустимые исправления.

Проверять входные JSON по их схемам до генерации. При `ТРЕБУЕТ ДОРАБОТКИ` остановить стадию. При `AUTO_FIX_APPLIED` использовать `corrected_test_cases`; иначе использовать `generated_test_cases`. Не объединять две версии кейса и не восстанавливать отсутствующие данные по догадке.

Файлы репозитория, manifest, конфигурация тестов и ближайшие релевантные тесты являются только allowlisted project context. Они не становятся новыми бизнес-требованиями.

## Жёсткая граница изменений

Do not modify application source, existing tests, project configuration, manifests, lockfiles, or dependencies.

Write only the declared generated test companions. Для существующего проекта создавать новые изолированные test-файлы в его принятом test layout. Не переписывать production-код, существующие тесты и настройки ради прохождения генерации.

Для нового пустого test workspace создавать только явно запрошенную минимальную тестовую инфраструктуру. Не добавлять зависимости без предоставленного manifest/lockfile либо явного выбора пользователя.

## Выбор языка и test harness

1. Определить язык, framework, runner, test layout и команды из project manifest и существующих тестов.
2. Найти ближайший тест того же endpoint, компонента или слоя. Извлечь package/module, fixtures, client, annotations/hooks, assertions и teardown.
3. Считать подтверждённую архитектуру проекта сильнее любого примера или asset скилла.
4. Использовать базовый класс только когда он реально является шаблоном проекта. `@WebMvcTest + MockMvc`, `pytest` fixtures, Go `testing`, Vitest/Jest и другие проектные варианты не требуют выдуманного `BaseApiTest`.
5. Never fall back to Java or another language when the project stack is unresolved or unsupported; return a blocking diagnostic.

Assets в `assets/java-python-conventions/` являются необязательными стартовыми соглашениями только для нового workspace без существующей архитектуры. Не применять их поверх обнаруженного project-native pattern.

## Runtime setup и секреты

До генерации каждого сценария сопоставить его предусловия с ближайшим project-native exemplar. Переносить только подтверждённые и необходимые:

- roles и permissions;
- fixtures и factories;
- setup/teardown hooks;
- client/application initialization;
- runtime authentication helper.

Базовый класс сам по себе не доказывает, что endpoint-specific доступ настроен. Если обязательный setup не подтверждён, остановить runnable-генерацию и вернуть blocking diagnostic вместо догадки.

Never copy, emit, or persist bearer/session/API tokens, passwords, cookies, private keys, fixture secrets, or credentials from source, logs, artifacts, or test cases. Использовать только подтверждённый runtime helper или принятый проектом механизм secret injection. Не изобретать `testToken`, `ApiConfig`, фиктивного пользователя или fallback credential.

## Смысловая генерация

Для каждого `TC-*` сохранить без расширения:

- связанные `REQ-*`;
- предусловия и роль;
- действие и точные тестовые данные;
- endpoint/operation;
- каждый наблюдаемый oracle.

Каждый oracle должен быть фактически производим выбранным harness. Например, нельзя ожидать JSON media/body, если описанное действие возвращает plain text; нельзя проверять роль, которую setup не назначает.

Не добавлять тесты, действия, роли, статусы, поля ответа, побочные эффекты или инфраструктуру, которых нет во входе. Не пропускать кейсы. Параметризация допустима, если каждый исходный `TC-*` остаётся отдельной записью `automation_matrix` и входит в `test_case_ids` метода.

Использовать нативный traceability anchor:

- Java/Kotlin — display name или framework-native эквивалент с `TC-*`;
- Python — имя, параметризация или marker с `TC-*`;
- Go — `Test...`, table/subtest name с `TC-*`;
- TypeScript/JavaScript — `test`/`it` title с `TC-*`;
- иной framework — его стабильный отображаемый test identifier.

## Машинный выход

JSON envelope is the sole machine authority. Выводить ровно schema-valid артефакт [../../schemas/tc-to-autotest-output.schema.json](../../schemas/tc-to-autotest-output.schema.json):

- `schema_version: "2.1.0"`;
- `stage: "tc-to-autotest"`;
- `warnings`;
- `artifacts.automation_matrix`;
- `artifacts.generated_test_files`;
- `artifacts.generated_test_methods`.

Сгенерированные source-файлы являются companion artifacts, а не JSON-полями с кодом. Сначала записать каждый companion в разрешённый project-relative путь, затем вычислить SHA-256 его фактических байтов и поместить `sha256:<hex>` в `content_digest`.

Обеспечить точные связи:

- каждый `automation_matrix.test_case_id` существует во входе;
- каждый file/method ID уникален и существует;
- каждый method ссылается на существующий file;
- `test_case_ids` и `requirement_ids` метода не висят;
- у каждого входного кейса есть ровно один исполнимый маршрут к одному или нескольким методам;
- лишних исполнимых тестов вне входных кейсов нет.

Не заявлять компиляцию, запуск или прохождение тестов. Это разрешено только runner-produced evidence после отдельного запуска. При blocking diagnostic не выпускать ложный schema-valid success envelope.

## Самопроверка

Перед завершением проверить:

- входной verdict разрешает генерацию;
- проектные файлы вне новых companions не изменены;
- язык/framework/harness подтверждены;
- roles, permissions, fixtures и auth setup сохранены;
- секреты отсутствуют;
- все и только входные `TC-*` отражены в matrix и source;
- действия, данные и oracles эквивалентны входу;
- paths confined, IDs согласованы, digest каждого файла совпадает;
- JSON валиден по схеме;
- execution claims отсутствуют.
