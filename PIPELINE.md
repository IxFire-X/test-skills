# Пайплайн для работы с тестами

> **v2.0 (2026-07-03):** удалены documentation-pipeline скиллы (concept-analysis, docs-review, doc-fix). Оставлен единственный test-pipeline: tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer.
> См. также: **`CONTRACTS.md`** — единственный источник истины по именам тегов и статус-маркеров.

Сквозной процесс генерации тест-кейсов, их валидации и автоматизации.

---

## Пайплайн: Тест-кейсы → Автотесты (4 этапа)

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────────┐
│ tc-generator │ ───→ │ tc-reviewer  │ ───→ │tc-to-autotest│ ───→ │autotest-reviewer │
│   v2.3       │      │   v2.2       │      │   v3.1       │      │   v1.3           │
└──────────────┘      └──────────────┘      └──────────────┘      └──────────────────┘
      │                      │                      │                       │
      ▼                      ▼                      ▼                       ▼
<generated_tc>     <validation_report>        <automation_        <autotest_review>
                                    +              matrix>        + <review_verdict>
                         <corrected_tc>     + <automation_        + <review_comments>
                                              analysis>           + <corrected_autotest_code>
```

### Этап 1: Генерация ручных тест-кейсов (tc-generator v2.3)

- **SKILL:** [Ручные тест-кейсы/SKILL.md](Ручные тест-кейсы/SKILL.md)
- **Примеры:** [Ручные тест-кейсы/examples.md](Ручные%20тест-кейсы/examples.md)

**Вход:** `<analytics_documentation>` + `<source_code_and_diff>` (или ручной сбор)

**Выход:**
- `<analysis>` — анализ покрытия и конфликтов кода/аналитики
- `<generated_test_cases>` — тест-кейсы в Zephyr Markdown

**Контракт:** `<generated_test_cases>` → следующий этап (tc-reviewer)

### Этап 2: Валидация тест-кейсов (tc-reviewer v2.2)

- **SKILL:** [Валидация тест-кейсов/SKILL.md](Валидация%20тест-кейсов/SKILL.md)
- **Примеры:** [Валидация тест-кейсов/examples.md](Валидация%20тест-кейсов/examples.md)

**Вход:** `<generated_test_cases>` (от tc-generator) + `<analytics_documentation>` + `<source_code_and_diff>`

**Выход (канон, см. `CONTRACTS.md` §2):**
- `<validation_report>` — отчёт валидации с вердиктом
- `<review_verdict>` — `ПРИНЯТО` | `AUTO_FIX_APPLIED` | `ТРЕБУЕТ ДОРАБОТКИ`
- `<review_comments>` — пояснения по каждой ошибке (только при `ТРЕБУЕТ ДОРАБОТКИ`)
- `<corrected_test_cases>` — исправленные ТК (при `AUTO_FIX_APPLIED` или `ТРЕБУЕТ ДОРАБОТКИ`)

**Контракт:**
- `ПРИНЯТО` → `<generated_test_cases>` как `<test_cases>` → tc-to-autotest
- `AUTO_FIX_APPLIED` → `<corrected_test_cases>` (приоритет) → tc-to-autotest
- `ТРЕБУЕТ ДОРАБОТКИ` → `<corrected_test_cases>` (приоритет) → tc-to-autotest (только после подтверждения пользователя)

### Этап 3: Генерация автотестов (tc-to-autotest v3.1)

- **SKILL:** [Автоматизированные кейсы на основе тест-кейсов/SKILL.md](Автоматизированные%20кейсы%20на%20основе%20тест-кейсов/SKILL.md)
- **Примеры:** [Автоматизированные кейсы на основе тест-кейсов/examples.md](Автоматизированные%20кейсы%20на%20основе%20тест-кейсов/examples.md)

**Вход:** `<test_cases>` или `<corrected_test_cases>` (от tc-reviewer, либо `<generated_test_cases>` напрямую от tc-generator)

**Выход (канон):**
- `<automation_analysis>` — инвентаризация ТК, группировка, TODO, `<conflict_resolution>` (опц.)
- `<automation_matrix>` — traceability `ТК-N → java-метод`
- Java-файлы: DTO запроса/ответа, тест-класс (JUnit 5 / RestAssured / AssertJ / WireMock)

### Этап 4: Валидация автотестов (autotest-reviewer v1.3)

- **SKILL:** [Валидация автотестов/SKILL.md](Валидация%20автотестов/SKILL.md)
- **Примеры:** [Валидация автотестов/examples.md](Валидация%20автотестов/examples.md)

**Вход:** `<test_cases>` / `<corrected_test_cases>` + `<automation_matrix>` + Java-код автотестов (от tc-to-autotest)

**Выход (канон):**
- `<autotest_review>` — разбор по 5 категориям (traceability, анти-паттерны, TODO, WireMock, стек)
- `<review_verdict>` — `ПРИНЯТО` | `AUTO_FIX_APPLIED` | `ТРЕБУЕТ ДОРАБОТКИ`
- `<review_comments>` — пояснения по каждой ошибке (при `ТРЕБУЕТ ДОРАБОТКИ` и `AUTO_FIX_APPLIED`)
- `<corrected_autotest_code>` — исправленный код (при `AUTO_FIX_APPLIED`)

**Важно:** autotest-reviewer применяет `AUTO_FIX` только для исправимых предупреждений (например, добавление `.as(...)`, замена `@Test` на `@ParameterizedTest`, объединение assert'ов в `SoftAssertions`). Критические дефекты (Thread.sleep, хардкод URL/порт, пропущенный `ТК-N`, расхождение с аналитикой) → `ТРЕБУЕТ ДОРАБОТКИ` и возврат в `tc-to-autotest`.

**Границы:** только смысловые проверки (traceability, анти-паттерны, TODO, стек). Компиляция, синтаксис, стиль — зона `javac` / Checkstyle.

---

## Оркестратор (координация)

```
┌─────────────────────────────────────────────────────────────────┐
│                        ОРКЕСТРАТОР                              │
│                    (orchestrate v2.0)                           │
├─────────────────────────────────────────────────────────────────┤
│  Поддерживаемый пайплайн:                                       │
│  • test-pipeline: tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer │
└─────────────────────────────────────────────────────────────────┘
```

- **SKILL:** [Оркестратор/SKILL.md](Оркестратор/SKILL.md)
- **Примеры:** [Оркестратор/examples.md](Оркестратор/examples.md)

**Возможности:**
- Автоматический запуск test-pipeline по цели
- Контроль итераций (по умолчанию: 3)
- Изоляция контекста между шагами
- Contract Check (исполнимый чеклист по `CONTRACTS.md`)
- Обработка ошибок и retry
- Генерация отчёта о выполнении

---

## Быстрый запуск

> **Программный запуск пайплайна** (IDE/CLI): описан в [Оркестратор/SKILL.md](Оркестратор/SKILL.md) — выбор пайплайна по цели, контроль итераций, изоляция контекста, retry, Contract Check.

### Тест-кейсы и автотесты

| Сценарий | Команда |
|---|---|
| Только генерация ТК | `tc-generator` (остановка после) |
| Генерация + валидация ТК | `tc-generator` → `tc-reviewer` (остановка после) |
| Генерация + автотесты (без валидации) | `tc-generator` → `tc-to-autotest` (остановка после) |
| Полный пайплайн (4 этапа) | `tc-generator` → `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` |
| Только автотесты из готовых ТК | `tc-to-autotest` (передать `<test_cases>`) |
| Только валидация автотестов | `autotest-reviewer` (передать `<test_cases>` + `<automation_matrix>` + код) |

---

## Контракты передачи данных

> **Канон:** имена тегов и статус-маркеров определены в `CONTRACTS.md` (этот документ — **единственный источник истины**). Таблицы ниже — справочное сокращение.

### Тест-кейсы → Автотесты

| От | Кому | Корневой блок | Примечание |
|---|---|---|---|
| `tc-generator` | `tc-reviewer` | `<generated_test_cases>` | Всегда |
| `tc-reviewer` (ПРИНЯТО) | `tc-to-autotest` | `<generated_test_cases>` → `<test_cases>` | Без изменений |
| `tc-reviewer` (AUTO_FIX) | `tc-to-autotest` | `<corrected_test_cases>` (приоритет) | Исправленная версия |
| `tc-reviewer` (ДОРАБОТКИ) | `tc-to-autotest` | `<corrected_test_cases>` (приоритет) | Исправленная версия |
| `tc-to-autotest` | `autotest-reviewer` | `<automation_matrix>` + `<automation_analysis>` + Java-код | Всегда |
| `autotest-reviewer` (ПРИНЯТО) | CI/CD | `<autotest_review>` + `<review_verdict>` | Код готов к запуску |
| `autotest-reviewer` (AUTO_FIX) | CI/CD | `<corrected_autotest_code>` → готов к запуску | Исправленные предупреждения |
| `autotest-reviewer` (ДОРАБОТКИ) | `tc-to-autotest` | `<review_comments>` → повторная генерация | Неисправимые дефекты |

---

## Единый словарь тегов

> **Канон:** полный реестр тегов и статус-маркеров — в `CONTRACTS.md` (разделы §2 и §3). Этот раздел — справочное сокращение для быстрого поиска.

### Выходные корневые блоки (по скиллам)

| Тег | Производитель | Потребитель |
|---|---|---|
| `<generated_test_cases>` | `tc-generator` | `tc-reviewer` |
| `<validation_report>` | `tc-reviewer` | Оркестратор / лог |
| `<corrected_test_cases>` | `tc-reviewer` | `tc-to-autotest` |
| `<automation_analysis>` | `tc-to-autotest` | `autotest-reviewer` / лог |
| `<automation_matrix>` | `tc-to-autotest` | `autotest-reviewer` + следующий запуск `tc-to-autotest` |
| `<autotest_review>` | `autotest-reviewer` | Оркестратор / лог |
| `<review_verdict>` | `tc-reviewer`, `autotest-reviewer` | Оркестратор |
| `<review_comments>` | `tc-reviewer`, `autotest-reviewer` | Оркестратор / пользователь |
| `<corrected_autotest_code>` | `autotest-reviewer` | CI/CD / Пользователь |
| `<orchestration_result>` | `orchestrate` | пользователь |

### Входные теги (общие)

| Тег | Кто принимает | Что внутри |
|---|---|---|
| `<analytics_documentation>` | `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer` | Бизнес-требования, AC, контракты API |
| `<source_code_and_diff>` | `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer` | Исходный код, diff из PR, SQL |
| `<generated_test_cases>` | `tc-reviewer` | ТК, сгенерированные `tc-generator` |
| `<test_cases>` | `tc-to-autotest`, `autotest-reviewer` | Утверждённые ТК (от reviewer или напрямую) |
| `<corrected_test_cases>` | `tc-to-autotest` (через Оркестратор) | ТК, исправленные `tc-reviewer` |
| `<automation_matrix>` (вход) | `tc-to-autotest` | Матрица `ТК-N → java-метод` от предыдущего запуска |
| `<existing_project_context>` | `tc-to-autotest` | Архитектурный шаблон существующего проекта |
| `<goal>` | `orchestrate` | Цель в свободной форме |
| `<pipeline>` | `orchestrate` | Имя пайплайна (опц., иначе — `test-pipeline`) |
| `<max_iterations>` | `orchestrate` | Лимит итераций (по умолчанию `3`) |
| `<strict_mode>` | `orchestrate` | `true` / `false` (по умолчанию `false`) |
| `<context>` | `orchestrate` | Дополнительный контекст проекта |

### Статус-маркеры (канон, см. `CONTRACTS.md` §3)

| Маркер | Источник | Означает |
|---|---|---|
| `failed` | любой | Критическая ошибка |
| `ПРИНЯТО` | `tc-reviewer`, `autotest-reviewer` | OK |
| `AUTO_FIX_APPLIED` | `tc-reviewer`, `autotest-reviewer` | Скилл сам внёс правки |
| `ТРЕБУЕТ ДОРАБОТКИ` | `tc-reviewer`, `autotest-reviewer` | FAIL без автофикса |

**Правила именования:**
- `<generated_test_cases>` используется только на выходе `tc-generator` и на входе `tc-reviewer`.
- После `tc-reviewer` рабочий набор ТК называется `<test_cases>` (если вердикт `ПРИНЯТО`) или `<corrected_test_cases>` (если были исправления).
- `<corrected_test_cases>` всегда имеет приоритет над `<test_cases>` при передаче в `tc-to-autotest`.
- `<analytics_documentation>` сохраняется на всём протяжении пайплайна как источник правды.

---

## Структура папок

```
test-orchestration-skills/
├── PIPELINE.md                    # Этот файл
├── Instruction.md                 # Общая инструкция
├── CONTRACTS.md                   # Канон тегов и статус-маркеров
├── .skillsrc                      # Манифест проекта
├── tag-registry.md                # Реестр тегов
├── conflict-resolution.md         # Стратегии разрешения конфликтов
│
├── Ручные тест-кейсы/             # tc-generator v2.3
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
├── Валидация тест-кейсов/         # tc-reviewer v2.2
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
├── Автоматизированные кейсы.../   # tc-to-autotest v3.1
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
├── Валидация автотестов/          # autotest-reviewer v1.3
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
├── Оркестратор/                   # orchestrate v2.0
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
├── schemas/                       # JSON Schema для выходных тегов
├── shared/                        # Общие утилиты (sub-agent compaction, stub-helper и др.)
└── Автоматизированные кейсы.../templates/  # Шаблоны тестов для разных языков
```

---

## Версионирование

| Скилл | Версия | Последнее изменение |
|---|---|---|
| tc-generator | 2.3 | 2026-07-03 — переименован SKILL_tc_generator_v2.3.md → SKILL.md |
| tc-reviewer | 2.2 | 2026-07-03 — унифицирована структура каталогов |
| tc-to-autotest | 3.1 | 2026-07-03 — унифицирована структура каталогов, создан DIFF_v2_to_v3.md |
| autotest-reviewer | 1.3 | 2026-07-03 — унификация с CONTRACTS.md |
| orchestrate | 2.0 | 2026-07-03 — удалены doc-скиллы, оставлен только test-pipeline |

---

*См. также: `CONTRACTS.md` (канон тегов и статусов), `Оркестратор/SKILL.md` (quality-gate и Contract Check), `Instruction.md` (словарь XML-тегов).*