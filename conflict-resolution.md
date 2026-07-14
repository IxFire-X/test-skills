# Стратегии разрешения конфликтов в .skillsrc

> **Назначение:** Описывает правила слияния и разрешения конфликтов между источниками конфигурации проекта.
> Связан с `.skillsrc`, `tag-registry.md`, `CONTRACTS.md`

**Версия:** 1.0

---

## Источники конфигурации (в порядке приоритета)

```
1. <project_context> (явный, передан пользователем/оркестратором)
2. .skillsrc (автообнаруженный манифест проекта)
3. Значения по умолчанию (хардкод в скиллах)
```

## Правило слияния

При наличии нескольких источников применяется **стратегия «приоритет вышестоящего с переиспользованием нижестоящего»**:

```python
def merge_config(explicit_context, skillsrc, defaults):
    result = defaults.copy()
    result.update(skillsrc)           # .skillsrc перекрывает defaults
    result.update(explicit_context)   # <project_context> перекрывает всё
    return result
```

**Пример:**

| Параметр | defaults | .skillsrc | project_context | Итог |
|----------|----------|-----------|-----------------|------|
| language | java | kotlin | (не задан) | **kotlin** |
| test.framework | junit5 | (не задан) | (не задан) | **junit5** (default) |
| project.type | platform-billing | sdk-library | microservice | **microservice** |

## Стратегии для конфликтующих полей

### 1. language + test.framework

**Проблема:** `.skillsrc` говорит `language: kotlin`, но `test.framework: junit5` несовместим с Kotlin в некоторых контекстах.

**Стратегия:** приоритет у `language`. Если `language=go`, `test.framework=junit5` → игнорировать test.framework, использовать `testing`.

**Логи:**
```
⚠️ .skillsrc: test.framework=junit5 несовместим с language=go. Использую testing.
```

### 2. Дублирующиеся скиллы в pipeline.preferred

**Проблема:** два скилла заявлены на один шаг пайплайна.

**Стратегия:** приоритет у первого в массиве. Остальные игнорируются с warning.

```
⚠️ .skillsrc: pipeline.docs[1] имеет 2 скилла [A, B]. Использую A.
```

### 3. Неизвестные ключи

**Проблема:** в `.skillsrc` есть ключ, не описанный в `schemas/skillsrc.schema.json` (JSON Schema для `.skillsrc`).

**Стратегия:** сохранить, передать в `<project_context>` как есть, не валидировать жёстко.

```
ℹ️ .skillsrc: ключ custom.ci_provider не из схемы, сохранён как есть.
```

### 4. Конфликт automation_matrix

**Проблема:** ТК-5 есть в старой matrix, но отсутствует в новых test_cases.

**Стратегия:** (описана в `tc-to-autotest/SKILL.md` §РАЗРЕШЕНИЕ КОНФЛИКТОВ):
- ТК есть в обоих → сохранить метод
- ТК только в matrix → удалить метод (removed_from_tc)
- ТК только в test_cases → сгенерировать новый
- ТК расщеплён → переименовать
- ТК объединены → слить в @ParameterizedTest

## Правила валидации .skillsrc

1. Все обязательные поля (`name`, `language`) должны присутствовать.
2. `project.type` должен быть из допустимого enum: `platform-billing`, `sdk-library`, `microservice`, `web-app`, `cli-tool`.
3. `test.framework` проверяется на совместимость с `language` (таблица совместимости ниже).
4. `pipeline.*` — допустимы только имена скиллов из `.skillsrc` (секция `skills_registry`) или из `CONTRACTS.md` §2.

### Таблица совместимости language ↔ test.framework

| language | Совместимые frameworks |
|----------|----------------------|
| java | junit5, testng |
| kotlin | junit5, kotest |
| python | pytest, unittest |
| go | testing |
| typescript | jest, mocha |

---

*Связанные файлы: `.skillsrc` (манифест), `tag-registry.md` (реестр тегов), `CONTRACTS.md` (канон контрактов), [`schemas/skillsrc.schema.json`](schemas/skillsrc.schema.json) (формальная схема манифеста), `schemas/README.md` (инструкция по валидации JSON Schema).*
