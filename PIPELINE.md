# Пайплайн для работы с тестами

> **v2.1 (2026-07-07):** добавлен опциональный препроцессор `context-marker` (Разметка контекста). Полный пайплайн: context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer.
> **v2.0 (2026-07-03):** удалены documentation-pipeline скиллы (concept-analysis, docs-review, doc-fix). Оставлен единственный test-pipeline.
> См. также: **`CONTRACTS.md`** — единственный источник истины по именам тегов и статус-маркеров.

Сквозной процесс генерации тест-кейсов, их валидации и автоматизации.

---

## Пайплайн: Тест-кейсы → Автотесты (5 этапов)

```
┌──────────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────────┐
│ context-marker   │ ───→ │ tc-generator │ ───→ │ tc-reviewer  │ ───→ │tc-to-autotest│ ───→ │autotest-reviewer │
│   v1.0 (опц.)    │      │   v2.4       │      │   v2.2       │      │   v3.1       │      │   v1.3           │
└──────────────────┘      └──────────────┘      └──────────────┘      └──────────────┘      └──────────────────┘

      │                          │                      │                      │                       │
      ▼                          ▼                      ▼                      ▼                       ▼
<analytics_doc>         <generated_tc>     <validation_report>        <automation_        <autotest_review>
+ <source_code>                                             +              matrix>        + <review_verdict>
                                                <corrected_tc>     + <automation_        + <review_comments>
                                                                     analysis>           + <corrected_autotest_code>
```

### Этап 0 (опционально): Разметка контекста (context-marker v1.0)

- **SKILL:** [Разметка контекста/SKILL.md](Разметка%20контекста/SKILL.md)

**Вход:** сырые `.md`-файлы аналитики без XML-разметки (SDD-проекты)

**Выход:** `<analytics_documentation>` + `<source_code_and_diff>`

**Контракт:** `<analytics_documentation>` + `<source_code_and_diff>` → следующий этап (tc-generator). Если аналитика уже в XML-тегах — этап пропускается.

### Этап 1: Генерация ручных тест-кейсов (tc-generator v2.4)

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
│  • test-pipeline: context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer │
│    (context-marker — опциональный препроцессор, запускается автоматически)      │
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

### Lite-версия (orchestrate v2.0-lite)

- **SKILL:** [Оркестратор/SKILL-LITE.md](Оркестратор/SKILL-LITE.md)

**Назначение:** облегчённая версия оркестратора для моделей с эффективным контекстом < 32K токенов (Qwen, DeepSeek, Llama 7B–13B). Ключевое отличие — Progressive Disclosure (L1→L2): оркестратор читает только `.skillsrc` → отбирает нужный скилл → загружает его `SKILL.md`. Экономия токенов на диспетчеризации: 40–60%.

**Когда использовать Lite:**
- Модели с ограниченным контекстом (< 32K)
- Отсутствует необходимость в Deep Scan / Zero-Config / Quickstart
- Достаточно базовой обработки Fallback-ситуаций (A–J)

**Когда использовать полную версию:** требуется Deep Scan (автообнаружение стека), Zero-Config Mode (работа без `.skillsrc`), Quickstart (пропуск валидации), детальная диагностика Fallback или Contract Check по `CONTRACTS.md` §5. Подробнее: [Оркестратор/SKILL.md](Оркестратор/SKILL.md).

---

## Быстрый запуск

> **Программный запуск пайплайна** (IDE/CLI): описан в [Оркестратор/SKILL.md](Оркестратор/SKILL.md) — выбор пайплайна по цели, контроль итераций, изоляция контекста, retry, Contract Check.

### Тест-кейсы и автотесты

| Сценарий | Команда |
|---|---|
| Только генерация ТК | `tc-generator` (остановка после) |
| Генерация + валидация ТК | `tc-generator` → `tc-reviewer` (остановка после) |
| Генерация + автотесты (без валидации) | `tc-generator` → `tc-to-autotest` (остановка после) |
| Полный пайплайн (5 этапов) | `context-marker` → `tc-generator` → `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` |
| Только автотесты из готовых ТК | `tc-to-autotest` (передать `<test_cases>`) |
| Только валидация автотестов | `autotest-reviewer` (передать `<test_cases>` + `<automation_matrix>` + код) |

---

## Контракты передачи данных

> **Канон:** имена тегов и статус-маркеров определены в `CONTRACTS.md` — **единственном источнике истины**.
> Полный реестр: см. `CONTRACTS.md` §2 (выходные теги), §2.3 (правило переименования `<generated_test_cases>` → `<test_cases>`), §3 (статус-маркеры), §4 (граф передачи).

---

## Словарь тегов и статус-маркеров

> **Канон:** полный реестр — в `CONTRACTS.md`:
> - §2 — выходные корневые блоки и внутренние теги
> - §2.3 — правило переименования `<generated_test_cases>` → `<test_cases>`
> - §3 — статус-маркеры и их семантика
> - §4 — граф передачи контрактов
>
> Этот файл не дублирует таблицы — все отсылки идут к единственному источнику истины.

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
├── Разметка контекста/            # context-marker v1.0 (опциональный препроцессор)
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
├── Ручные тест-кейсы/             # tc-generator v2.4
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
├── Оркестратор/                   # orchestrate v2.0 + v2.0-lite
│   ├── README.md
│   ├── SKILL.md
│   ├── SKILL-LITE.md
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
| tc-generator | 2.4 | 2026-07-03 — переименован SKILL_tc_generator_v2.3.md → SKILL.md, версия SKILL.md повышена до 2.4 |
| tc-reviewer | 2.2 | 2026-07-03 — унифицирована структура каталогов |
| tc-to-autotest | 3.1 | 2026-07-03 — унифицирована структура каталогов, создан DIFF_v2_to_v3.md |
| autotest-reviewer | 1.3 | 2026-07-03 — унификация с CONTRACTS.md |
| context-marker | 1.0 | 2026-07-07 — новый скилл: разметка сырых .md-файлов аналитики в XML-теги |
| orchestrate | 2.0 | 2026-07-03 — удалены doc-скиллы, оставлен только test-pipeline |
| orchestrate-lite | 2.0-lite | 2026-07-07 — облегчённая версия для моделей < 32K токенов (Progressive Disclosure) |

---

*См. также: `CONTRACTS.md` (канон тегов и статусов), `Оркестратор/SKILL.md` (quality-gate и Contract Check), `Instruction.md` (словарь XML-тегов).*