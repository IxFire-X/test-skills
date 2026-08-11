# Контракт независимого ревью автотестов

## Содержание

- [Порядок проверки](#порядок-проверки)
- [Таблица семантической эквивалентности](#таблица-семантической-эквивалентности)
- [Finding codes](#finding-codes)
- [Вердикты](#вердикты)

## Порядок проверки

1. Проверить JSON schema и exact input set.
2. Проверить path confinement, существование companion files и SHA-256.
3. Проверить уникальность и связность TC/REQ/FILE/METHOD IDs.
4. Построить ожидаемое множество executable cases из ручных ТК.
5. Построить фактическое множество из matrix, methods и language-native source anchors.
6. Найти пропущенные и лишние executable tests.
7. Для каждого сопоставленного кейса проверить semantic equivalence.
8. Проверить project-native setup/stack и секреты.
9. Сформировать findings и только затем verdict.

Нельзя начинать с verdict и подбирать под него evidence.

## Таблица семантической эквивалентности

| Элемент | Что доказать | Типовой blocking defect |
|---|---|---|
| setup | роль, permission, fixture и состояние реально создаются | base class есть, endpoint permission отсутствует |
| action | вызвана точная operation/endpoint/function | тест вызывает соседний endpoint или иной method |
| data | различены omitted/null/empty/boundary и сохранены значения | поле сериализуется как `null`, хотя кейс требует отсутствия |
| transport | media, headers и serialization соответствуют harness | plain string трактуется как JSON response |
| oracle | каждый ожидаемый результат имеет точный assertion | проверен только status, body/state oracle пропущен |
| side effect | проверяется только input-supported effect | добавлена проверка audit/payment/log без требования |
| cleanup | тест изолирован и не стирает state до проверки | teardown выполняется до assertion |

Для параметризации проверить каждую строку отдельно. Общий method не доказывает, что конкретный `TC-*` получает свои данные и oracle.

## Finding codes

Использовать стабильные коды, когда они применимы:

- `MISSING_TEST_CASE` — входной кейс не имеет executable implementation;
- `EXTRA_EXECUTABLE_TEST` — executable test не связан с входным кейсом;
- `TRACEABILITY_MISMATCH` — matrix/method/source anchor расходятся;
- `RUNTIME_SETUP_MISSING` — отсутствует подтверждённый setup/permission/fixture;
- `ACTION_MISMATCH` — реализовано другое действие;
- `TEST_DATA_MISMATCH` — данные, omission/null или boundary не соответствуют кейсу;
- `ASSERTION_MISSING` — oracle не проверяется;
- `HARNESS_ORACLE_MISMATCH` — harness не может породить заявленный media/value/state;
- `UNSUPPORTED_BEHAVIOR` — добавлена неподтверждённая проверка;
- `PROJECT_STACK_MISMATCH` — код не соответствует manifest/exemplar;
- `SECRET_PERSISTED` — secret или credential записан в artifact;
- `UNSUPPORTED_EXECUTION_CLAIM` — заявлен запуск без runner evidence.

Сообщение должно назвать expected и actual. Evidence должно указывать конкретный field, source fragment или ID; одно лишь название code недостаточно.

## Вердикты

`ПРИНЯТО` допустимо только после полного review всех declared files/methods и при пустых `findings`/`corrections`.

`AUTO_FIX_APPLIED` допустимо только для механических изменений, не меняющих test behavior: например, исправление ошибочной metadata-ссылки при однозначном source evidence. Не применять auto-fix к setup, action, data, assertions, auth или test count.

`ТРЕБУЕТ ДОРАБОТКИ` обязателен при любом blocking defect. Не включать исправленный код и не маскировать defect warning’ом.

Assets `assets/autotest-fixtures/` предназначены для локальной forward-проверки различия корректного project-native теста и теста с пропущенным runtime setup; они не являются production requirements.
