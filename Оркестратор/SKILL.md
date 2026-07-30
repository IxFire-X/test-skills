---
name: orchestrate
version: 2.0
description: >
  Координирует цепочки скиллов с контролем итераций, изоляцией контекста и обработкой ошибок.
  Единственный пайплайн:
  - test: context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer
  context-marker вызывается автоматически при обнаружении сырых .md-файлов (SDD-проекты).
triggers:
  - "создай тест-кейсы"
  - "сгенерируй автотесты"
  - "запусти тестовый пайплайн"
  - "создай тесты"
  - "инициализируй проект"
  - "quickstart"
  - "настрой проект"
---

# Скилл: Оркестратор (orchestrate v2.0)

## Назначение

Координация цепочки скиллов для генерации тест-кейсов и автотестов с:
- Контролем качества на каждом этапе
- Изоляцией контекста между агентами (каждый скилл получает только свои теги)
- Соблюдением иерархии источников (Аналитика > Код)
- Обработкой ошибок и retry
- Прозрачностью и объяснимостью решений

## Архитектура

```
┌─────────────────────────────────────────────────────────────────┐
│                        ОРКЕСТРАТОР                              │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │   Pipeline  │  │   Context   │  │   Error     │            │
│  │   Manager   │  │   Isolation │  │   Handler   │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │   Status    │  │   Quality   │  │   Report    │            │
│  │   Tracker   │  │   Gate      │  │   Generator │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│context-marker │───▶│ tc-generator  │───▶│  tc-reviewer  │
│   (опц.)      │    │               │    │               │
└───────────────┘    └───────────────┘    └───────────────┘
                              │
                              ▼
                       ┌───────────────┐
                       │ tc-to-auto-   │
                       │    test       │
                       └───────────────┘
                              │
                              ▼
                       ┌───────────────┐
                       │  autotest-    │
                       │  reviewer     │
                       └───────────────┘
```

## Контракт входа

| Параметр | Обязательность | Описание |
|----------|----------------|----------|
| `<goal>` | ✅ | Целевое действие (например, "создать тест-кейсы для TransferService") |
| `<pipeline>` | ⚠️ | По умолчанию: `test-pipeline` (единственный пайплайн) |
| `<max_iterations>` | ⚠️ | Максимальное количество итераций (по умолчанию: 3) |
| `<context>` | ⚠️ | Дополнительный контекст проекта |
| `<strict_mode>` | ⚠️ | Строгий режим качества (по умолчанию: `false`) |

## Контракт выхода

```xml
<orchestration_result version="2.0">
  <execution_id>UUID — уникальный идентификатор запуска пайплайна</execution_id>
  <status>completed | partial | failed | retry</status>
  <pipeline_name>название пайплайна</pipeline_name>
  <iterations>количество итераций</iterations>
  <steps>
    <step>
      <name>название шага</name>
      <skill>использованный скилл</skill>
      <status>успех/ошибка</status>
      <output>путь к результату</output>
      <duration_ms>длительность шага в миллисекундах</duration_ms>
      <retries>количество retry</retries>
    </step>
  </steps>
  <metrics>
    <total_duration_ms>общая длительность пайплайна</total_duration_ms>
    <total_steps>количество шагов</total_steps>
    <successful_steps>успешных шагов</successful_steps>
    <failed_steps>упавших шагов</failed_steps>
    <total_retries>суммарное количество retry</total_retries>
  </metrics>
  <final_result>путь к финальному результату</final_result>
  <warnings>список предупреждений</warnings>
</orchestration_result>
```

## Пайплайн: `test-pipeline`

**Цель:** Разметить сырую аналитику, сгенерировать тест-кейсы, провалидировать, создать автотесты и проверить их.

```
context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer
```

> **Примечание:** `context-marker` (Разметка контекста) — опциональный препроцессор. Вызывается автоматически при обнаружении сырых `.md`-файлов без XML-разметки (SDD-проекты). Если аналитика уже в XML-тегах — этап пропускается.

**Триггеры:**
- "создай тест-кейсы"
- "сгенерируй автотесты"
- "запусти test pipeline"
- "создай тесты"

**Логика:**
0. `context-marker` (опционально) — разметить сырые `.md`-файлы аналитики в XML-теги. Выход: `<analytics_documentation>` + `<source_code_and_diff>`. Выполняется только при обнаружении нетегированной аналитики.
1. `tc-generator` — сгенерировать ручные тест-кейсы на основе `<analytics_documentation>`. Выход: `<generated_test_cases>`.
2. `tc-reviewer` — провалидировать ТК. Выход: `<validation_report>` + (при `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ`) `<corrected_test_cases>`.
   - Если `VERDICT = ПРИНЯТО` → продолжить с `<generated_test_cases>` как `<test_cases>`.
   - Если `VERDICT = AUTO_FIX_APPLIED` → использовать `<corrected_test_cases>` как `<test_cases>`.
   - Если `VERDICT = ТРЕБУЕТ ДОРАБОТКИ` → приостановить пайплайн и эскалировать пользователю. Повторная передача только после ручной доработки или подтверждения пользователем.
3. `tc-to-autotest` — сгенерировать автотесты. Выход: `<automation_analysis>` + `<automation_matrix>` + файлы автотестов.
4. `autotest-reviewer` — проверить автотесты. Выход: `<autotest_review>` + `<review_verdict>` + (опц.) `<review_comments>` + (при `AUTO_FIX_APPLIED`) `<corrected_autotest_code>`.
   - Если `VERDICT = ПРИНЯТО` → завершить
   - Если `VERDICT = AUTO_FIX_APPLIED` → использовать `<corrected_autotest_code>`, завершить
   - Если `VERDICT = ТРЕБУЕТ ДОРАБОТКИ` → приостановить пайплайн и эскалировать пользователю.

## Deep Scan — полное сканирование проекта (v1.3)

> **Принцип:** не полагаться исключительно на `.skillsrc` (может быть неактуален), а **первично просканировать весь проект** — код, документацию, спецификации. Особенно важно для SDD-проектов, где спецификации — источник правды.

### Зачем нужен Deep Scan

| Без Deep Scan | С Deep Scan |
|---------------|-------------|
| Полагаемся на `.skillsrc` (статичный, может устареть) | Сканируем реальное состояние проекта |
| Не видим документацию (OpenAPI, AsyncAPI, ADR) | Обнаруживаем все спецификации |
| Не знаем методологию (SDD/TDD/BDD) | Определяем методологию из структуры проекта |
| Пропускаем скрытые зависимости | Находим все точки входа |

### Что сканирует Deep Scan

```
Deep Scan:
  1. КОД:
     ├── build-файлы: pom.xml, build.gradle, go.mod, package.json, requirements.txt, pyproject.toml
     ├── конфигурации: application.yml, .env, docker-compose.yml
     ├── точки входа: main-классы, роутеры, контроллеры
     ├── тесты: структура тестовых директорий
     └── observability: micrometer, prometheus, opentelemetry, jaeger

  2. ДОКУМЕНТАЦИЯ:
     ├── README.md, CONTRIBUTING.md, ARCHITECTURE.md
     ├── *.md в docs/ (ADR-решения, BPMN-схемы, словари)
     ├── openapi.yaml / swagger.json (REST-контракты)
     ├── asyncapi.yaml (асинхронные события)
     └── gherkin/*.feature (BDD-сценарии)

  3. МЕТОДОЛОГИЯ (определяется из структуры):
     ├── Есть docs/ + openapi.yaml → SDD (Specification-Driven)
     ├── Есть tests/ + coverage → TDD (Test-Driven)
     ├── Есть *.feature + step_definitions → BDD (Behavior-Driven)
     └── Другое → mixed
```

### Как работает слияние Deep Scan + .skillsrc

```
1. ВЫПОЛНИТЬ Deep Scan → scan_result
2. ЕСЛИ найден .skillsrc → ЗАГРУЗИТЬ .skillsrc
3. ПРИМЕНИТЬ стратегию слияния (skillsrc_vs_scan из .skillsrc):
   ├── scan_wins (по умолчанию): scan_result перекрывает .skillsrc
   ├── skillsrc_wins: .skillsrc перекрывает scan_result
   ├── ask_user: показать разницу, спросить пользователя
   └── merge: объединить, дубликаты → из scan_result
4. РАССЧИТАТЬ итоговый <project_context>
5. ЕСЛИ расхождение > 30% → ВЫВЕСТИ diff, ЗАПРОСИТЬ подтверждение
```

### Пример: SDD-проект

**Deep Scan обнаружил:**
```
✅ build_tool: maven (из pom.xml)
✅ language: java (из структуры src/main/java)
✅ framework: spring-boot (из @SpringBootApplication)
✅ methodology: sdd (docs/api/openapi.yaml + docs/domain/ + docs/architecture/)
✅ openapi: docs/api/openapi.yaml (контракты API)
✅ domain: docs/domain/ (DDD-словарь, BPMN-схемы)
✅ architecture: docs/architecture/ (C4-диаграммы, ADR)
✅ observability: micrometer (из pom.xml)
```

**`.skillsrc` говорит:**
```
language: java
framework: spring-boot
methodology: sdd
```

**Результат слияния (scan_wins):**
```
→ Использую данные Deep Scan (актуальнее)
→ .skillsrc подтверждает базовые параметры
→ Расхождение: 5% (допустимо)
```

### Что попадает в `<project_context>`

После Deep Scan + слияния формируется `<project_context>` v1.2:

```xml
<project_context>
  <manifest>
    <name>subscription-renewal-service</name>
    <language>java</language>
    <framework>spring-boot</framework>
    <build_tool>maven</build_tool>
    <methodology>sdd</methodology>
  </manifest>
  <source_tree>
    <source>src/main/java</source>
    <tests>src/test/java</tests>
    <resources>src/main/resources</resources>
    <docs>docs/</docs>
  </source_tree>
  <conventions>
    <!-- Из .editorconfig, checkstyle.xml, eslint.config.js -->
  </conventions>
  <methodology>
    <type>sdd</type>
    <specs>
      <openapi>docs/api/openapi.yaml</openapi>
      <domain>docs/domain/</domain>
      <architecture>docs/architecture/</architecture>
    </specs>
  </methodology>
  <observability>
    <metrics>micrometer</metrics>
    <tracing>opentelemetry</tracing>
  </observability>
  <scan_metadata>
    <scanned_at>2026-07-03T16:00:00+05:00</scanned_at>
    <files_scanned>247</files_scanned>
    <skillsrc_applied>true</skillsrc_applied>
    <merge_strategy>scan_wins</merge_strategy>
    <divergence_pct>5</divergence_pct>
  </scan_metadata>
</project_context>
```

## Zero-Config Mode — автообнаружение стека (v1.2)

Если `.skillsrc` НЕ найден в корне проекта — Оркестратор запускает автообнаружение.

```
ШАГ 0: DISCOVER PROJECT STACK
  1. Проверить pom.xml → language=java, build_tool=maven
     Если spring-boot-starter в зависимостях → framework=spring-boot
  2. Проверить build.gradle(.kts) → language=java/kotlin, build_tool=gradle
  3. Проверить package.json → language=typescript
     Если express → framework=express, jest → test_framework=jest
  4. Проверить requirements.txt/pyproject.toml → language=python
     Если fastapi → framework=fastapi, pytest → test_framework=pytest
  5. Проверить go.mod → language=go, build_tool=go-mod
  6. Если ничего не найдено → Спросить пользователя 3 ключевых поля:
     - language (java | python | go | typescript)
     - framework (spring-boot | fastapi | gin | express | django)
     - paths.source (например, "src/main/java")

РЕЗУЛЬТАТ → <project_manifest> (эквивалент .skillsrc в памяти)
```

**Результат:** 80% проектов запускаются без `.skillsrc` и какой-либо настройки.

## Quickstart-режим (v1.2)

**Триггеры:** `"инициализируй проект"`, `"quickstart"`, `"настрой проект"`

**Алгоритм:**

```
1. ВЫПОЛНИТЬ Zero-Config (автообнаружение стека)
2. ЕСЛИ .skillsrc не найден → ПРЕДЛОЖИТЬ создать автоматически:
   "Обнаружен стек: Java 17, Spring Boot 3.2, JUnit 5, Maven.
    Создать .skillsrc для будущих запусков? [Y/n]"
3. СОЗДАТЬ .skillsrc (если пользователь согласен)
4. СФОРМИРОВАТЬ <project_context>:
   - manifest: содержимое .skillsrc
   - source_tree: дерево каталогов src/
   - dependencies: список зависимостей из pom.xml/package.json
   - existing_tests: список существующих тестовых файлов
   - conventions: camelCase, 4 spaces, *Test.java
5. ПРОСКАНИРОВАТЬ существующие артефакты:
   - Тест-кейсы (docs/to_do/*.md) → кол-во
   - Автотесты (src/test/**) → кол-во
6. ВЫВЕСТИ чек-лист пользователю:
   "✅ Java 17, Spring Boot 3.2
    ✅ Maven, JUnit 5, PostgreSQL
    🧪 12 автотестов
    📋 0 тест-кейсов
    
    Доступные команды:
    - 'создай тест-кейсы'
    - 'сгенерируй автотесты'
    - 'запусти test pipeline'"
```

## Единый контекст проекта (`<project_context>`)

После инициализации (из `.skillsrc` или Zero-Config) Оркестратор формирует `<project_context>` — **единый блок, передаваемый всем скиллам пайплайна**:

```xml
<project_context>
  <manifest>
    project.name: subscription-renewal-service
    project.language: java
    project.framework: spring-boot
    paths.source: src/main/java
    paths.tests: src/test/java
    test.framework: junit5
    test.api_client: rest-assured
    resolution.analytics_vs_code: analytics_wins
  </manifest>
  <source_tree>
    src/main/java/com/example/billing/
    ├── service/RenewalService.java
    ├── web/SubscriptionRenewalController.java
    └── ...
  </source_tree>
  <dependencies>
    spring-boot-starter-web: 3.2.x
    spring-boot-starter-data-jpa: 3.2.x
    ...
  </dependencies>
  <existing_tests>
    src/test/java/.../SubscriptionRenewalServiceTest.java
    src/test/java/.../SubscriptionRenewalClientApiTest.java
    ...
  </existing_tests>
  <conventions>
    naming: camelCase
    indent: 4 spaces
    test_class_suffix: Test
    api_prefix: /api
  </conventions>
</project_context>
```

**Важно:** `<project_context>` добавляется к каждому шагу пайплайна через механизм изоляции контекста.

## Progressive Disclosure — двухфазная диспетчеризация (v1.4)

> **Источник:** Anthropic Agent Skills (Progressive Disclosure).

### Проблема старого подхода

Ранее Оркестратор читал полный `SKILL.md` **каждого** скилла для принятия решения о диспетчеризации. Это:
- Расходует 40–60% токенов впустую
- Загрязняет контекст лишними инструкциями
- Замедляет принятие решения

### Решение: L1 → L2 Progressive Disclosure

```
ФАЗА 1 (L1 — Метаданные, ~50 токенов на скилл):
  1. ПРОЧИТАТЬ .skillsrc → секция skills_registry
  2. ДЛЯ каждого скилла в реестре — прочитать:
     - name (человекочитаемое имя)
     - description (1 предложение: что делает скилл)
     - tags (категории: @positive, @review, @automation...)
     - input_contract, output_contract (формат входа/выхода)
  3. СОПОСТАВИТЬ <goal> пользователя с description + tags
  4. ВЫБРАТЬ целевой скилл (или несколько для пайплайна)
  5. ЕСЛИ есть неоднозначность (несколько скиллов подходят):
     → ВЫВЕСТИ пользователю список кандидатов с description
     → ЗАПРОСИТЬ уточнение (Google Gemini)

ФАЗА 2 (L2 — Полные инструкции, только для выбранного):
  1. ПОСТРОИТЬ план выполнения цепочки (Z.ai Plan-Before-Execution)
  2. ЗАГРУЗИТЬ полный SKILL.md целевого скилла
  3. ВЫПОЛНИТЬ скилл
  4. ПЕРЕДАТЬ контекст следующему скиллу (если пайплайн)
```

### Отображение tags на скиллы (из skills_registry)

| Тег | Скилл | Описание (1 предложение) |
|-----|-------|--------------------------|
| `@test-cases`, `@positive`, `@negative` | `tc-generator` | Генерирует тест-кейсы из аналитики |
| `@review`, `@validation`, `@test-cases` | `tc-reviewer` | Валидирует тест-кейсы по 6 категориям |
| `@automation`, `@code-generation` | `tc-to-autotest` | Генерирует автотесты (Java/Python/Go/TS) |
| `@review`, `@autotest`, `@traceability` | `autotest-reviewer` | Валидирует автотесты по 5 категориям |
| `@context`, `@markup`, `@preprocessing` | `context-marker` | Размечает сырые `.md`-файлы в XML-теги |
| `@orchestration`, `@pipeline` | `orchestrator` | Координирует цепочки скиллов |

### Пример: пайплайн «полный цикл тестирования»

```
1. L1: <goal> = "запусти test-pipeline"
2. Определяю цепочку по PIPELINE.md: context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer
3. Для КАЖДОГО шага:
   a. L1: проверяю description в skills_registry (быстрый взгляд)
   b. L2: загружаю полный SKILL.md только для текущего шага
   c. Выполняю шаг
   d. Изолирую контекст перед следующим шагом
```

## Протокол диспетчеризации

Оркестратор вызывает скиллы через передачу контекста LLM (prompt-based диспетчеризация):

1. **Выбор скилла:** Оркестратор читает `skills_registry` из `.skillsrc` (L1-метаданные), отбирает подходящий скилл по `name` + `description` + `tags`.
2. **Загрузка SKILL.md:** Оркестратор загружает полный `SKILL.md` выбранного скилла (L2).
3. **Формирование system prompt:** `SKILL.md` передаётся как system prompt LLM вместе с входными XML-тегами из контекста пайплайна.
4. **Изоляция контекста (Sub-Agent):** каждый скилл получает **только** свои входные теги (см. секцию «Контракт входа» соответствующего SKILL.md). Выходные теги предыдущего скилла не смешиваются с другими данными.
5. **Получение результата:** Оркестратор парсит выходные XML-теги из ответа LLM и передаёт их следующему скиллу согласно контрактам `CONTRACTS.md`.

> **Примечание:** при наличии production-хоста скиллов (отдельный рантайм) вызов может осуществляться через REST API с передачей контекста в теле запроса. Текущая реализация — prompt-based, через LLM-диспетчеризацию.

### Health-Check скиллов

Перед каждым вызовом скилла Оркестратор проверяет его доступность:
- Если файл `SKILL.md` отсутствует в директории скилла → `critical_failure`: пайплайн блокируется с `status = failed`.
- Если директория скилла не найдена → `critical_failure`: пайплайн блокируется с `status = failed`.
- Результат health-check фиксируется в `step_results[i].health_check`.

---

## Алгоритм работы

### Шаг 0: Инициализация (Deep Scan + Zero-Config / Quickstart)

```
ЕСЛИ триггер = "quickstart" | "инициализируй" | "настрой":
  → ВЫПОЛНИТЬ Quickstart-режим (см. выше)
  → ВЕРНУТЬ чек-лист пользователю
  → ЗАВЕРШИТЬ (ожидание следующей команды)

ИНАЧЕ:
  → ВЫПОЛНИТЬ Deep Scan (см. §"Deep Scan — полное сканирование проекта")
  → ЕСЛИ .skillsrc найден → ЗАГРУЗИТЬ .skillsrc + ПРИМЕНИТЬ стратегию слияния
  → ЕСЛИ .skillsrc НЕ найден → ВЫПОЛНИТЬ Zero-Config как fallback для базовых полей
  → СФОРМИРОВАТЬ <project_context> v1.2 (результат слияния Deep Scan + .skillsrc)
  → ПРОДОЛЖИТЬ к Шагу 1
```

### Шаг 1: Запуск пайплайна

```
АНАЛИЗ <goal>:
  ЕСЛИ передан <pipeline>:
    → pipeline = <pipeline> (единственный валидный: test-pipeline)
  ИНАЧЕ:
    → pipeline = "test-pipeline" (по умолчанию)
```

### Шаг 2: Инициализация контекста + Contract Check

```
СОЗДАТЬ контекст выполнения:
  - execution_id: UUID (генерируется в начале пайплайна, сквозной trace ID для всех шагов)
  - goal: <цель>
  - pipeline: "test-pipeline"
  - max_iterations: <лимит>
  - current_iteration: 0
  - step_results: []
  - errors: []
  - warnings: []
  - started_at: текущая временная метка

ВЫПОЛНИТЬ contract_check (см. §"Контрактное версионирование"):
  - Для каждой пары (skill_N → skill_N+1) проверить имена тегов и статусов по CONTRACTS.md
  - При несовпадении → status = failed, contract_mismatch, БЛОКИРОВАТЬ пайплайн
```

### Шаг 3: Выполнение пайплайна

**Цикл выполнения:**

```
ПОКА current_iteration < max_iterations:
  current_iteration++
  
  ДЛЯ КАЖДОГО шага в пайплайне:
    1. ПОДГОТОВИТЬ вход для шага
       - Собрать данные из предыдущих шагов
       - Применить изоляцию контекста
    
    2. ВЫПОЛНИТЬ шаг
       - Вызвать соответствующий скилл
       - Получить результат
    
    3. ПРОВЕРИТЬ результат
       - Извлечь корневой блок (по CONTRACTS.md §2)
       - Извлечь статус-маркер (по CONTRACTS.md §3)
       - Если маркер не из реестра → Fallback B (unknown_status)
    
    4. ЗАФИКСИРОВАТЬ результат
       - Добавить в step_results
       - Обновить статус
    
    5. ПРИНЯТЬ решение (по таблице §"ТАБЛИЦА РЕШЕНИЙ")
```

### Шаг 4: Контроль итераций

```
ЕСЛИ current_iteration >= max_iterations:
  → status = "retry"
  → Добавить предупреждение: "Достигнут лимит итераций"
  → Предложить пользователю:
    - Увеличить max_iterations
    - Вмешаться вручную
    - Принять текущий результат
```

### Шаг 5: Обработка ошибок

**Типы ошибок и реакции:**

| Тип ошибки | Реакция |
|------------|---------|
| Скилл не найден | → Пропустить шаг, добавить warning |
| Ошибка входа | → Запросить у пользователя |
| Ошибка выполнения | → Retry шаг (до 2 раз) |
| Критическая ошибка | → Остановить пайплайн |

### Шаг 6: Генерация отчёта

Создать файл `docs/to_do/orchestration-report-<TIMESTAMP>.md`:

```markdown
---
execution_id: <UUID>
status: completed | partial | failed | retry
pipeline: test-pipeline
started_at: YYYY-MM-DD HH:MM:SS
completed_at: YYYY-MM-DD HH:MM:SS
iterations: X
---

# Отчёт оркестратора

## Contract Check
- context-marker → tc-generator: PASS (ожидается <analytics_documentation> + <source_code_and_diff>, опциональный шаг)
- tc-generator → tc-reviewer: PASS (ожидается <generated_test_cases>)
- tc-reviewer → tc-to-autotest: PASS (ожидается <corrected_test_cases>/<test_cases>, статус ПРИНЯТО/AUTO_FIX_APPLIED/ТРЕБУЕТ ДОРАБОТКИ)
- tc-to-autotest → autotest-reviewer: PASS (ожидается <automation_matrix> + файлы)
- autotest-reviewer → завершение: PASS (ожидается <autotest_review> + <review_verdict>)

## Цель
<описание цели>

## Пайплайн
test-pipeline (context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer)

## Выполнение

### Итерация 1

#### Шаг 1: <название>
- **Скилл:** <skill_name>
- **Статус:** ✅ Успешно
- **Вход:** <входные данные>
- **Выход:** <путь к результату>

#### Шаг 2: <название>
...

### Итерация 2 (если была)
...

## Финальный результат

- **Статус:** completed | partial | failed
- **Путь к результату:** <путь>

## Предупреждения
- Предупреждение 1

## Рекомендации
1. Рекомендация 1

---
*Сгенерировано: orchestrate v2.0*
*Дата: YYYY-MM-DD HH:MM:SS*
```

## Механизм изоляции контекста

**Принцип:**
Каждый шаг выполняется с "чистым" контекстом, содержащим только:
1. Входные данные для шага
2. Результаты предыдущих шагов (через контракты)
3. Системные инструкции

**Реализация:**

```
ДЛЯ КАЖДОГО шага:
  1. СОЗДАТЬ новый контекст
  2. ИЗВЛЕЧЬ результат предыдущего шага (например, <corrected_test_cases>)
  3. ПЕРЕИМЕНОВАТЬ его во входной тег следующего скилла (если требуется по CONTRACTS.md)
  4. ОБЕСПЕЧИТЬ ЧИСТОТУ: удалить все служебные теги предыдущего шага
     (<thought>, <analysis>, <validation_report>, <review_comments>, <autotest_review>).
     Следующий скилл должен видеть только целевой артефакт и исходную аналитику.
  5. ДОБАВИТЬ в контекст:
     - Инструкции скилла
     - Входные данные
     - Результаты предыдущих шагов (только контракты)
     - <analytics_documentation> (если доступна) с пометкой "Источник правды"
  6. ВЫПОЛНИТЬ шаг
  7. ИЗВЛЕЧЬ результат (только корневой блок по CONTRACTS.md §2)
  8. УДАЛИТЬ контекст
```

**Приоритет аналитики:**
Если на вход поступили противоречивые данные из разных этапов, Оркестратор должен явно указать следующему скиллу на приоритет `<analytics_documentation>` над кодом и тест-кейсами.

**Преимущества:**
- Нет "загрязнения" контекста
- Каждый шаг работает "с нуля"
- Предсказуемое поведение
- Легче отлаживать

> **Sub-Agent Spawn & Compaction:** формальный протокол делегирования шагов изолированным sub-agent'ам и сжатия (compaction) результатов описан в [`shared/sub-agent-compaction.md`](shared/sub-agent-compaction.md). Каждый шаг пайплайна — это spawn sub-agent с чистым контекстом с последующим compaction: извлечение ТОЛЬКО контрактных тегов по [`CONTRACTS.md`](CONTRACTS.md) §2 и отбрасывание служебных блоков (`<thought>`, `<analysis>`, `<draft>`, `<notes>` и т.д.).

## Механизм контроля качества

**Quality Gate:**

```
ДЛЯ КАЖДОГО шага:
  1. ПОЛУЧИТЬ статус-маркер
  2. ПРОВЕРИТЬ соответствие ожидаемому (по CONTRACTS.md §3)
  3. ЕСЛИ маркер не из реестра:
     → Fallback B (unknown_status, эскалация)
  4. ЕСЛИ не соответствует ожидаемой реакции:
     - Определить действие (retry, fix, escalate)
     - Зафиксировать отклонение
```

### Execution Gate (Оракул исполнения — Опора 1, BACKLOG)

> **Жёсткое правило:** `autotest-reviewer` **НЕ выдаёт `ПРИНЯТО`**, пока детерминированный раннер `tools/run_tests.py` не вернул `PASS`. Это закрывает корневую причину провала InvenTree (0/25), где `AUTO_FIX_APPLIED` выставлялся на код, который физически ни разу не запускался. LLM-вердикт о качестве кода **недостаточен** без факта исполнения.

**После `autotest-reviewer` (перед финальным `ПРИНЯТО`) Оркестратор обязан:**

```
1. ЗАПУСТИТЬ tools/run_tests.py --project <путь> --skillsrc .skillsrc
   (стек берётся из .skillsrc или --language)
2. ПРОЧИТАТЬ verdict из JSON-выхода (по схеме schemas/run-tests-output.schema.json)
3. РАЗВЕТВИТЬСЯ ПО verdict:
   - PASS          → разрешить autotest-reviewer выдать ПРИНЯТО, завершить пайплайн
   - FAIL          → autotest-reviewer обязан выдать ТРЕБУЕТ ДОРАБОТКИ;
                     root_cause[] из выхода — это.machine-readable причина падения,
                     её можно передать дорогой модели (Kimi K3) для диагностики
                     или вернуть tc-to-autotest для исправления
   - NOT_RUNNABLE  → ЧЕСТНЫЙ ответ «окружение недоступно, не могу проверить»;
                     НЕ ПРИНЯТО и НЕ фейковый PASS; зафиксировать в отчёте
                     и запросить окружение у пользователя
```

**Принцип:** вердикт раннера **не интерпретируется LLM** — `PASS` означает реальный запуск и успех. `NOT_RUNNABLE` — это честный отказ, а не основание доверять коду.

---

## ТАБЛИЦА РЕШЕНИЙ ПО STATUS-МАРКЕРАМ

> **Единый реестр реакций оркестратора на статус-маркеры, выдаваемые зависимыми скиллами.**
> Источник канона: `CONTRACTS.md` §3.

| status-маркер | Источник | Означает | Реакция оркестратора |
|---|---|---|---|
| `ПРИНЯТО` | `tc-reviewer`, `autotest-reviewer` | Все проверки пройдены | **Продолжить** к следующему шагу |
| `AUTO_FIX_APPLIED` | `tc-reviewer`, `autotest-reviewer` | Дефекты исправлены автофиксом | **Использовать** `<corrected_test_cases>` / `<corrected_autotest_code>` вместо исходного |
| `ТРЕБУЕТ ДОРАБОТКИ` | `tc-reviewer`, `autotest-reviewer` | Есть FAIL без автофикса | **Приостановить** пайплайн; вывести `<review_comments>` пользователю. Повторный запуск — только после подтверждения или изменения входных данных |
| `failed` | любой скилл | Критическая ошибка выполнения | **Остановить** пайплайн; `status = failed`; `escalate to user` |

### Правила ветвления по вердиктам ревьюеров

- **`ПРИНЯТО`** → продолжить пайплайн с исходным артефактом.
- **`AUTO_FIX_APPLIED`** → продолжить с исправленным артефактом; в отчёте указать `auto_fix: true`.
- **`ТРЕБУЕТ ДОРАБОТКИ`** (test-pipeline):
  - после `tc-reviewer` → **приостановить** пайплайн, вывести пользователю `<review_comments>` и `<corrected_test_cases>` с TODO-маркерами. Повторная генерация через `tc-generator` возможна только после ручной правки или явного подтверждения пользователя.
  - после `autotest-reviewer` → **приостановить** пайплайн, вывести пользователю `<review_comments>` и частично исправленный `<corrected_autotest_code>` (если есть). Повторная генерация через `tc-to-autotest` возможна только после ручной правки или явного подтверждения пользователя.
  - **ЗАПРЕЩЕНО** автоматически перезапускать тот же скилл на тех же данных — это нарушает изоляцию контекста и создаёт зацикливание. Только после подтверждения пользователя или явного изменения входных данных.
  - При исчерпании `max_iterations` → `status = partial`, эскалация.

### Правило приоритета `<analytics_documentation>`

На протяжении всего пайплайна `<analytics_documentation>` сохраняется как **источник правды**. Если на любом шаге обнаружено расхождение:
- **Аналитика > Тест-кейсы:** ТК признаются ошибочными → вернуть на `tc-generator`/`tc-reviewer`.
- **Аналитика > Автотесты:** код признаётся ошибочным → вернуть на `tc-to-autotest`.
- Оркестратор обязан явно передать этот приоритет следующему скиллу через пометку в контексте.

### Правила идемпотентности (декларативная спецификация)

> **Важно:** правила идемпотентности — **декларативная спецификация**, а не гарантия исполнения. Исполнение зависит от LLM-провайдера / хоста скиллов.

- Повторный запуск шага с теми же входными данными **должен** давать идентичный результат.
- Признак «уже выполнено» — наличие целевого артефакта с теми же `source_hash` / `goal` в frontmatter.
- `source_hash` ключевого тега вычисляется как `sha256` и пишется в `step_results[i].input_hash` — это **рекомендация**, не обязанность.

---

## КОНТРАКТ ПЕРЕДАЧИ КОНТЕКСТА МЕЖДУ СКИЛЛАМИ

> **Канон:** имена выходных тегов и статус-маркеров определены в `CONTRACTS.md` (этот документ — **единственный источник истины**). Оркестратор передаёт теги «как есть» на вход следующего скилла.

### Обязательные выходные теги по скиллам

| Скилл | Выходной корневой блок | Принимающий скилл |
|---|---|---|
| `tc-generator` | `<generated_test_cases>` | `tc-reviewer` |
| `tc-reviewer` | `<validation_report>` + (при `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ`) `<corrected_test_cases>` | `tc-to-autotest` |
| `tc-to-autotest` | `<automation_analysis>` + `<automation_matrix>` + файлы автотестов | `autotest-reviewer` |
| `autotest-reviewer` | `<autotest_review>` + `<review_verdict>` + (опц.) `<review_comments>` + (при `AUTO_FIX_APPLIED`) `<corrected_autotest_code>` | (завершение) |

### Правила передачи

1. **Прямая передача:** корневой блок передаётся «как есть», без переформатирования.
2. **Контекст проекта:** при наличии `<project_context>` от пользователя — он добавляется к каждому шагу через `<context>`.
3. **Hash артефакта (опц.):** `sha256` ключевого тега пишется в `step_results[i].input_hash`.
4. **Таймаут передачи:** если артефакт не получен за разумное время → Fallback §A.

### Связь с изоляцией контекста

Передача **только** контрактных тегов — не всего XML-документа. Каждый шаг получает «чистый» сэндвич из:
- системный промпт скилла;
- входные теги (контракт предыдущего шага);
- `<analytics_documentation>` — **источник правды** (сохраняется на всём протяжении пайплайна);
- `<context>` (если передан).

Внутренние рассуждения, черновики, `<notes>`, `<validation_report>`, `<review_comments>`, `<autotest_review>` и т.п. **не передаются**.

---

## FALLBACK-СТРАТЕГИИ

> **Цель:** определить поведение оркестратора при сбоях, конфликтах и нестандартных ситуациях.

| Ситуация | Обнаружение | Стратегия |
|---|---|---|
| **A. Шаг не вернул ожидаемый корневой блок** | Отсутствует ожидаемый XML-блок из `CONTRACTS.md` §2 | Retry шага (до 2 раз) с теми же входными. Если не помогло → `status = failed`, эскалация |
| **B. Неизвестный status-маркер** | Статус не из `CONTRACTS.md` §3 | **Остановить** пайплайн, вывести `unknown_status: <mark>`, запросить подтверждение пользователя |
| **C. Конфликт версий скилла** | `version` SKILL.md < минимально требуемой | Вывести предупреждение `version_mismatch: skill=<name> have=<X> need=<Y>`. Продолжить, если `strict_mode = false`; иначе — стоп |
| **D. Циклическая зависимость** | Шаг N+1 ссылается на контекст шага N, который ещё не выполнен | Прервать с `status = failed`, вывести `cycle_detected: [N, N+1, ...]` |
| **E. Артефакт не найден** | Целевой артефакт отсутствует на диске | Если это вход пользователя → `status = failed` + запросить. Если это результат шага → `fallback: повторить шаг` |
| **F. Пользователь отменил** | Получен сигнал отмены | **Graceful stop**: сохранить `partial_results` в `docs/to_do/orchestration-report-partial-<TS>.md` |
| **G. Таймаут шага** (декларативный) | Шаг выполняется дольше `step_timeout` (опциональный параметр) | Retry (1 раз). Затем — `status = failed` + эскалация |
| **H. Несовместимый артефакт** | Скилл вернул XML, который не проходит базовую валидацию (отсутствуют обязательные теги по `CONTRACTS.md` §2) | Стратегия **A** (retry), затем — `status = failed` |
| **I. Переполнение контекста** | Суммарный XML-вход следующего шага > N токенов | Стратегия: сократить через `<context_summary>` (внешний шаг суммаризации) — `fallback: summarize` |
| **J. Расхождение в `<automation_matrix>`** | `tc-to-autotest` сообщает о `removed` / `renamed` | Если `strict_mode = true` → стоп, эскалация. Иначе — продолжить, зафиксировать в отчёте |

### Правила retry (декларативные)

> **Декларативная спецификация:** рекомендация LLM/хосту. Не является гарантией исполнения (см. `CONTRACTS.md` §6).

- Retry только для **транзитных** сбоев (A, G, H, I). Для **логических** (B, D, J в strict-режиме) — retry запрещён.
- Между retry — рекомендуемая задержка 5–10 секунд (эмуляция «остывания» модели).
- Каждый retry логируется: `step_results[i].retries`.

### Эскалация

При `status = failed` или достижении `max_iterations`:
1. Сохранить отчёт в `docs/to_do/orchestration-report-failed-<TS>.md`.
2. Вывести пользователю:
   - сводку: какие шаги прошли, какие упали;
   - рекомендуемые действия (увеличить `max_iterations`, исправить входные данные, изменить пайплайн).

---

## Пример использования

**Вход:**
```
Создай тест-кейсы и автотесты для TransferService
```

**Выполнение:**
```
Contract Check: PASS

Итерация 1:
  Шаг 0 (опционально): context-marker
    → <analytics_documentation>: аналитика размечена (пропущен — данные уже в XML)

  Шаг 1: tc-generator
    → <generated_test_cases>: 12 ТК (8 positive, 3 negative, 1 boundary)

  Шаг 2: tc-reviewer
    → <review_verdict>: AUTO_FIX_APPLIED
    → <corrected_test_cases>: 12 ТК (исправлены лимиты в ТК-3, ТК-7)

  Шаг 3: tc-to-autotest
    → <automation_analysis>: 12 ТК → 8 тестовых методов, 2 DTO
    → <automation_matrix>: ТК-1 → shouldCreateTransfer_ValidRequest()...
    → Java-файлы: TransferRequestDto.java, TransferResponseDto.java, TransferApiTest.java

  Шаг 4: autotest-reviewer
    → <review_verdict>: ПРИНЯТО
    → <autotest_review>: Traceability 100%, анти-паттернов нет, WireMock покрытие 100%

Результат: completed
```

**Выход:**
```xml
<orchestration_result version="2.0">
  <status>completed</status>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>1</iterations>
  <steps>
    <step>
      <name>Генерация тест-кейсов</name>
      <skill>tc-generator</skill>
      <status>success</status>
      <output>docs/to_do/test-cases-TransferService.md</output>
    </step>
    <step>
      <name>Валидация тест-кейсов</name>
      <skill>tc-reviewer</skill>
      <status>success (auto_fix)</status>
      <output>docs/to_do/test-cases-review-TransferService.md</output>
    </step>
    <step>
      <name>Генерация автотестов</name>
      <skill>tc-to-autotest</skill>
      <status>success</status>
      <output>src/test/java/.../TransferApiTest.java</output>
    </step>
    <step>
      <name>Валидация автотестов</name>
      <skill>autotest-reviewer</skill>
      <status>success</status>
      <output>docs/to_do/autotest-review-TransferService.md</output>
    </step>
  </steps>
  <final_result>src/test/java/.../TransferApiTest.java</final_result>
  <warnings>[]</warnings>
</orchestration_result>
```

## Ограничения (Scope)

**НЕ входит в scope:**
- Выполнение бизнес-логики (только координация)
- Изменение исходного кода
- Принятие бизнес-решений
- Работа без подключённых скиллов

## Зависимости

Оркестратор зависит от следующих скиллов:
- `context-marker` — для разметки сырых `.md`-файлов аналитики в XML-теги (вызывается автоматически при обнаружении SDD-проекта с сырой аналитикой)
- `tc-generator` — для генерации тест-кейсов
- `tc-reviewer` — для валидации тест-кейсов
- `tc-to-autotest` — для генерации автотестов
- `autotest-reviewer` — для валидации автотестов

## Чеклист качества перед выводом

- [ ] Все шаги выполнены
- [ ] Статусы соответствуют ожидаемым
- [ ] Contract Check выполнен (в отчёте зафиксирован)
- [ ] Отчёт сгенерирован
- [ ] Предупреждения зафиксированы
- [ ] Финальный результат верифицирован
- [ ] XML-выход валиден
- [ ] Версии контрактов всех скиллов проверены на совместимость

## Контрактное версионирование

> Contract Check (исполнимый чеклист) определён в `CONTRACTS.md` §5.

### Contract Check (исполнимый чеклист)

Перед выполнением любого шага пайплайна оркестратор обязан выполнить `contract_check`:

```text
ДЛЯ КАЖДОЙ пары (skill_N → skill_N+1) в пайплайне:
  1. По CONTRACTS.md §2 найти ожидаемый корневой блок
  2. По SKILL.md skill_N+1 найти «Контракт входа» — проверить, что имя блока совпадает
  3. По SKILL.md skill_N+1 найти «Контракт выхода» — проверить, что выходной блок skill_N+1 снова есть в CONTRACTS.md §2
  4. По CONTRACTS.md §3 найти все статус-маркеры, которые может вернуть skill_N — проверить, что они в реестре
   5. ЕСЛИ любая проверка не прошла:
        → status = failed
        → error = "contract_mismatch: skill=<N> → <N+1>: missing <tag> | unknown status <mark>"
        → БЛОКИРОВАТЬ пайплайн
```

**Алгоритм блокировки при `contract_mismatch`:**

```text
ЕСЛИ contract_mismatch:
  1. НЕМЕДЛЕННО прекратить выполнение текущего пайплайна.
  2. Записать в `<orchestration_result>`:
     <status>failed</status>
     <reason>contract_mismatch</reason>
     <detail>skill=<N> → <N+1>: missing <tag> | unknown status <mark></detail>
  3. Сгенерировать экстренный отчёт `docs/to_do/orchestration-report-<TS>.md` с секцией «Contract Check: FAILED».
  4. Зафиксировать в отчёте конкретную пару скиллов и суть расхождения:
     - Ожидаемый тег (из CONTRACTS.md §2)
     - Фактический тег (из SKILL.md скилла)
     - Рекомендация по исправлению
  5. ЭСКАЛИРОВАТЬ пользователю: вывести `<orchestration_result>` с явным указанием, что пайплайн заблокирован до исправления контракта.
  6. ЗАПРЕТИТЬ автоматическое продолжение или retry — пайплайн остаётся в состоянии `failed` до ручного вмешательства.
```

**Чеклист фиксируется в начале `docs/to_do/orchestration-report-<TS>.md`** отдельной секцией «Contract Check» (см. шаблон в §"Шаг 6: Генерация отчёта").

### Ожидаемые пары (для справки)

| Пара | Ожидаемый корневой блок от N | Принимается скиллом N+1 как | Возможные статусы |
|---|---|---|---|
| `context-marker` → `tc-generator` | `<analytics_documentation>` + `<source_code_and_diff>` | `<analytics_documentation>` + `<source_code_and_diff>` | (отсутствует status) |
| `tc-generator` → `tc-reviewer` | `<generated_test_cases>` | `<generated_test_cases>` | (отсутствует status) |
| `tc-reviewer` → `tc-to-autotest` | `<validation_report>` + `<corrected_test_cases>` | `<corrected_test_cases>` / `<test_cases>` | `ПРИНЯТО` / `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ` |
| `tc-to-autotest` → `autotest-reviewer` | `<automation_analysis>` + `<automation_matrix>` + файлы | `<automation_matrix>` + файлы | (отсутствует status) |
| `autotest-reviewer` → (завершение) | `<autotest_review>` + `<review_verdict>` + (опц.) `<review_comments>` + (опц.) `<corrected_autotest_code>` | (завершение) | `ПРИНЯТО` / `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ` |

### Версионирование самого Оркестратора (отдельно от контрактов)

- **major** версия SKILL.md Оркестратора меняется при изменении **структуры** контрактов (новые обязательные теги, изменение семантики).
- **minor** версия — при добавлении опциональных возможностей (новые пайплайны, новые fallback-стратегии).
- Изменения, не затрагивающие контракты, фиксируются в git-истории скилла.

---

## Дополнительные ресурсы

- JSON-схема контракта `<orchestration_result>`: [`../schemas/orchestrator-output.schema.json`](../schemas/orchestrator-output.schema.json)

---

*См. также: `CONTRACTS.md` (канон тегов и статусов), `PIPELINE.md` (схема пайплайна), `Instruction.md` (словарь XML-тегов).*
