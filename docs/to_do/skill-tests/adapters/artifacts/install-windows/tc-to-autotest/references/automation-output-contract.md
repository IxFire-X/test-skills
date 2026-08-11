# Контракт генерации автотестов

## Содержание

- [Приоритет источников](#приоритет-источников)
- [Выбор проектного шаблона](#выбор-проектного-шаблона)
- [Эквивалентность кейса и метода](#эквивалентность-кейса-и-метода)
- [Инварианты JSON](#инварианты-json)
- [Ошибки, требующие остановки](#ошибки-требующие-остановки)

## Приоритет источников

Использовать источники в следующем порядке:

1. `validation_report` определяет, разрешена ли генерация.
2. `corrected_test_cases` используется только при соответствующем принятом verdict; иначе канон — `generated_test_cases`.
3. Ручные тест-кейсы задают сценарии, данные и oracles.
4. Project manifest и ближайшие тесты задают язык, framework, layout и runtime setup.
5. Production source разрешено использовать только для подтверждения технических имён и фактических seams, но не для добавления новых бизнес-ожиданий.
6. Bundled assets применяются только при отсутствии project-native решения и явном выборе нового workspace.

При конфликте между кейсом и подтверждённым входным требованием остановить генерацию и вернуть конфликт наверх. Не «исправлять» кейс внутри генератора.

## Выбор проектного шаблона

Для существующего проекта собрать минимальный manifest:

| Поле | Источник |
|---|---|
| language/framework | build manifest, lockfile, test config |
| runner command | package/build config или documented project command |
| test root/layout | существующие test-файлы |
| client/harness | ближайший feature/endpoint exemplar |
| fixtures/setup | тот же exemplar и общие test helpers |
| roles/permissions | source-confirmed endpoint test/setup |
| traceability anchor | нативный framework convention |

Не считать отсутствие конкретного файла (`BaseApiTest`, `conftest.py`, `jest.config`) ошибкой само по себе. Ошибка возникает только когда нельзя подтвердить рабочий project-native путь.

Для нового workspace разрешены assets:

- `assets/java-python-conventions/java-junit5.md`;
- `assets/java-python-conventions/python-pytest.md`.

Они не являются требованиями к существующим проектам и не разрешают автоматически добавлять зависимости.

## Эквивалентность кейса и метода

Для каждого кейса построить карту:

```text
TC → REQ → setup → action → test data → oracle → FILE → METHOD
```

Проверить отдельно:

- setup действительно создаёт роль, permission, fixture и состояние, требуемые кейсом;
- action вызывает точную операцию с точными данными;
- каждый oracle имеет конкретный assertion или обоснованный input-supported TODO;
- выбранный harness способен породить наблюдаемое значение заявленного типа/media;
- cleanup не уничтожает проверяемое состояние до assertion;
- параметризованный метод сохраняет идентичность каждой строки `TC-*`.

Helper, fixture и factory не являются самостоятельными executable tests и не добавляются как лишние кейсы.

## Инварианты JSON

Канонический JSON не содержит source bytes. Он связывает companion files по ID, path и digest.

Требовать:

- уникальные `FILE-*` и `METHOD-*`;
- project-relative confined paths без выхода из test root;
- SHA-256 фактических байтов каждого declared file;
- существующий `file_id` для каждого method;
- непустые и существующие `test_case_ids` и `requirement_ids`;
- двунаправленное покрытие matrix ↔ methods;
- отсутствие file/method IDs, не объявленных в соответствующих массивах.

`content_digest` метода описывает объявленный method artifact, но не заменяет file digest и не доказывает выполнение.

## Ошибки, требующие остановки

Не выпускать success envelope при любом из условий:

- входной reviewer verdict блокирует генерацию;
- язык/framework или test harness не подтверждены;
- обязательный runtime setup неизвестен;
- безопасный auth mechanism отсутствует;
- требуется изменить application/config/dependencies;
- входной кейс недетерминирован или противоречив;
- невозможно представить все кейсы без добавления выдуманного поведения;
- companion path выходит за разрешённый test root.

Возвращать одну конкретную причину, отсутствующий источник и безопасный следующий шаг. Не подменять блокировку `warnings` и не заявлять `PASS` без runner evidence.
