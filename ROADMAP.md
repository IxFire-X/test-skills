# Test Orchestration Skills — Дорожная карта (Roadmap)

> **Версия:** 1.0 (2026-07-08)
> **Назначение:** каталог всех концептуальных идей расширения системы + дорожная карта по версиям.
> **Контекст:** результат мозгового штурма (3 раунда) по развитию мультиагентного тестового пайплайна.

---

## Текущее состояние (v2.x)

```
context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer
                    ↑                              ↑
              Оркестратор (orchestrate)            │
              + Deep Scan + Zero-Config            │
              + Progressive Disclosure (L1→L2)     │
              + Contract Check                     │
```

**6 скиллов, 1 пайплайн (test-pipeline), статичные SKILL.md, нет памяти между запусками.**

---

# Часть 1: Каталог всех идей (3 раунда)

---

## Раунд 1: Новые скиллы и инфраструктурные фичи

### Скиллы

#### 1. `impact-analyzer` — Анализ влияния изменений (Impact Analysis)

| Параметр | Значение |
|----------|----------|
| **Теги** | `@impact`, `@regression`, `@selective-testing` |
| **Вход** | `<source_code_diff>` + `<automation_matrix>` + (опц.) `<source_tree>` |
| **Выход** | `<impact_report>` с секциями `must-run`, `should-run`, `safe-to-skip` |
| **Суть** | Принимает git diff, строит граф зависимостей `файл → класс → метод → ТК-N → автотест`, возвращает ранжированный список тестов для перезапуска. Поддерживает `selective-regression` — минимальный набор тестов для проверки изменений. |
| **Проблема** | Разработчик меняет 3 строки в сервисе — какие тесты перезапускать? Сейчас система только генерирует тесты, но не умеет отвечать на вопрос «что затронуто?». |

#### 2. `tc-prioritizer` — Приоритизация тест-кейсов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@prioritization`, `@risk`, `@triage` |
| **Вход** | `<test_cases>` + `<analytics_documentation>` |
| **Выход** | `<priority_matrix>` с весами: `P0 (must-test)`, `P1 (should-test)`, `P2 (nice-to-have)` |
| **Суть** | Ранжирует ТК по 4 осям: бизнес-критичность, риск отказа, сложность, новизна функциональности. Интегрируется с риск-матрицей проекта (вероятность × последствия). |
| **Проблема** | Сгенерировано 50+ ТК — с чего начать тестирование при ограниченном времени? |

#### 3. `test-data-fabricator` — Генератор тестовых данных

| Параметр | Значение |
|----------|----------|
| **Теги** | `@test-data`, `@fixtures`, `@boundary` |
| **Вход** | `<source_code>` (DTO) + `<analytics_documentation>` |
| **Выход** | `<test_data_suite>` с секциями `valid_equivalence`, `boundary_values`, `invalid_inputs`, `sql_fixtures` |
| **Суть** | Принимает DTO-классы или OpenAPI-схемы. Генерирует: эквивалентные классы, граничные значения, случайные валидные/невалидные данные, SQL-фикстуры. Поддерживает форматы: JSON, SQL INSERT, Testcontainers, Faker. |
| **Проблема** | Автотесты сгенерированы, но используют хардкод-значения. Нужны реалистичные, граничные и случайные данные. |

#### 4. `contract-tester` — Генератор контрактных тестов (Pact-стиль)

| Параметр | Значение |
|----------|----------|
| **Теги** | `@contract`, `@pact`, `@openapi`, `@asyncapi` |
| **Вход** | `<openapi_spec>` + `<asyncapi_spec>` |
| **Выход** | `<contract_test_suite>` + `<contract_test_matrix>` |
| **Суть** | Генерирует Consumer-Driven Contract тесты в стиле Pact. Для REST: валидация request/response схем, статус-кодов, хедеров. Для AsyncAPI: валидация событий (публикация/потребление), схемы сообщений. |
| **Проблема** | В микросервисной архитектуре автотесты проверяют свой сервис, но не контракты между сервисами. |

#### 5. `observability-tester` — Генератор тестов наблюдаемости

| Параметр | Значение |
|----------|----------|
| **Теги** | `@observability`, `@logging`, `@metrics`, `@tracing` |
| **Вход** | `<source_code>` + `<analytics_documentation>` |
| **Выход** | `<observability_test_suite>` + `<observability_coverage_report>` |
| **Суть** | Генерирует тесты, проверяющие: логирование (ERROR с correlationId, INFO с duration), метрики (counter/timer/gauge), трейсы (span/traceId). Интегрируется с Testcontainers (лог-контейнер) + InMemory-метрики. |
| **Проблема** | Автотесты проверяют бизнес-логику, но не проверяют, что система логирует, метрики собираются, трейсы записываются. |

#### 6. `gherkin-exporter` — Экспорт ТК в Gherkin-формат (.feature)

| Параметр | Значение |
|----------|----------|
| **Теги** | `@gherkin`, `@bdd`, `@cucumber`, `@export` |
| **Вход** | `<test_cases>` (Zephyr Markdown) |
| **Выход** | `<gherkin_suite>` + `.feature` файлы + `<feature_matrix>` |
| **Суть** | Конвертирует каждый `ТК-N` в Gherkin-сценарий: `Given/When/Then`, `Scenario Outline` + `Examples`. Группирует по фичам → `.feature` файлы. Добавляет теги: `@positive`, `@negative`, `@boundary`, `@security`, `@P0`. |
| **Проблема** | Ручные ТК лежат в Zephyr Markdown, но команда хочет BDD (Cucumber/Behave/SpecFlow). |

#### 7. `flakiness-detector` — Детектор нестабильных тестов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@flakiness`, `@stability`, `@ci` |
| **Вход** | `<test_run_history>` (результаты N прогонов) + `<autotest_code>` |
| **Выход** | `<flakiness_report>` + `<review_verdict>` + (опц.) `<corrected_autotest_code>` |
| **Суть** | Анализирует паттерны нестабильности: order-dependent, time-dependent, data-dependent, network-dependent. Классифицирует причины и предлагает автофиксы. |
| **Проблема** | Flaky tests — главная боль CI/CD. Некоторые тесты «мигают» без изменений кода. |

#### 8. `test-estimator` — Оценка трудозатрат на тестирование

| Параметр | Значение |
|----------|----------|
| **Теги** | `@estimation`, `@planning`, `@management` |
| **Вход** | `<test_cases>` + `<project_context>` |
| **Выход** | `<estimation_report>` с секциями `manual_effort_hours`, `automation_effort_hours`, `total_cycle_hours`, `confidence_level` |
| **Суть** | Оценивает: ручное тестирование (время × сложность), автоматизацию (по историческим данным), общее время цикла. |
| **Проблема** | Менеджер спрашивает: «сколько времени займёт тестирование этой фичи?» Ответа нет. |

### Инфраструктурные фичи

#### A. Режим «Diff-пайплайн» (Incremental Pipeline)

Сейчас пайплайн всегда запускается с нуля. Идея: Оркестратор получает на вход `<previous_run_id>` и `<baseline_artifacts>`, сравнивает `source_hash` каждого тега. Если аналитика не изменилась → скилл пропускается. Если изменилась частично → diff-режим.

**Новый контракт:**
```xml
<orchestration_request>
  <goal>...</goal>
  <previous_run_id>UUID предыдущего запуска</previous_run_id>
  <baseline_artifacts>
    <analytics_documentation source_hash="abc123">...</analytics_documentation>
    <automation_matrix source_hash="def456">...</automation_matrix>
  </baseline_artifacts>
</orchestration_request>
```

#### B. Кросс-пайплайн (Cross-Pipeline Traceability)

Оркестратор поддерживает межсервисную трассировку: если сервис A вызывает сервис B (по OpenAPI), то при изменении контракта B — Оркестратор предлагает запустить пайплайн для A. Deep Scan парсит `openapi.yaml` на предмет `$ref`-зависимостей и строит сервисный граф.

#### C. Плагин-архитектура для языков/фреймворков

Вынести генерацию кода в подключаемые шаблоны. `.skillsrc` получает секцию:
```yaml
templates:
  autotest: "templates/junit5-restassured"
  dto: "templates/java-lombok-dto"
```
Любой желающий может добавить `templates/kotlin-kotest/` — и система подхватит без изменения скиллов.

---

## Раунд 2: SDD-углубление и Память (Memory Bank)

### SDD-скиллы

#### 9. `spec-validator` — Валидатор спецификаций перед пайплайном

| Параметр | Значение |
|----------|----------|
| **Теги** | `@spec`, `@validation`, `@openapi`, `@spectral` |
| **Вход** | `<openapi_spec>` + `<asyncapi_spec>` + (опц.) `<source_tree>` |
| **Выход** | `<spec_validation_report>` с секциями `schema_errors`, `completeness_gaps`, `code_spec_divergence` |
| **Суть** | Валидирует спецификации по Spectral-правилам (OWASP, Zalando, Google API Design Guide). Проверяет полноту и согласованность с кодом. Блокирует пайплайн до исправления спецификации. |
| **Ключевая фишка** | В SDD спецификация — источник правды. Если она невалидна — всё остальное теряет смысл. |

#### 10. `spec-coverage-analyzer` — Покрытие спецификации тестами

| Параметр | Значение |
|----------|----------|
| **Теги** | `@coverage`, `@spec`, `@traceability`, `@heatmap` |
| **Вход** | `<openapi_spec>` + `<test_cases>` + `<automation_matrix>` |
| **Выход** | `<spec_coverage_report>` с heatmap-матрицей и разделом `uncovered_risk` |
| **Суть** | Строит матрицу покрытия: `Operation × Status Code`, `Schema Property × Boundary`, `Security Scheme`. Подсвечивает spec-coverage-gaps. Для AsyncAPI: события, которые не тестируются. |

#### 11. `breaking-change-detector` — Детектор ломающих изменений в спецификации

| Параметр | Значение |
|----------|----------|
| **Теги** | `@breaking-change`, `@openapi-diff`, `@service-graph`, `@downstream` |
| **Вход** | `<openapi_spec>` (новая) + `<baseline_openapi_spec>` (старая) + `<service_graph>` |
| **Выход** | `<breaking_change_report>` с уровнями `CRITICAL` / `WARNING` / `INFO` |
| **Суть** | Детектирует Breaking/Non-breaking изменения по OpenAPI Diff. Строит карту поражённых downstream-сервисов через граф зависимостей. |

#### 12. `spec-to-tc-direct` — Прямая трансляция OpenAPI → аналитика для ТК

| Параметр | Значение |
|----------|----------|
| **Теги** | `@spec-to-analytics`, `@openapi`, `@auto-context` |
| **Вход** | `<openapi_spec>` |
| **Выход** | `<analytics_documentation>` — полноценная аналитика, синтезированная из OpenAPI |
| **Суть** | Альтернативный вход в пайплайн. Из `operation.description` → описание фичи, из `requestBody.schema.required` → AC, из `responses.*.schema` → ожидаемые результаты, из `enum` → граничные условия. |
| **Проблема** | У команды есть только OpenAPI и код, нет текстовой аналитики. Сейчас `tc-generator` переходит в ручной режим. |

#### 13. `bpmn-scenario-extractor` — Извлечение тестовых сценариев из BPMN-схем

| Параметр | Значение |
|----------|----------|
| **Теги** | `@bpmn`, `@process-flow`, `@scenario-extraction` |
| **Вход** | BPMN-файлы (XML) из `docs/domain/` |
| **Выход** | `<bpmn_scenario_analysis>` + `<generated_test_cases>` с traceability `ТК-N → BPMN Element ID` |
| **Суть** | Извлекает из BPMN все возможные пути (happy path + альтернативные ветки + исключения), граничные события, состояния ожидания. Генерирует аналитику и ТК. |
| **Проблема** | BPMN-схемы в `docs/domain/` есть, но НЕ используются тестовым пайплайном вообще. |

#### 14. `adr-compliance-checker` — Проверка соответствия Architecture Decision Records

| Параметр | Значение |
|----------|----------|
| **Теги** | `@adr`, `@architecture`, `@compliance`, `@governance` |
| **Вход** | ADR-документы из `docs/architecture/` + `<automation_matrix>` + `<test_cases>` |
| **Выход** | `<adr_compliance_report>` — traceability `ADR-N → требование → ТК-N (или GAP)` |
| **Суть** | Извлекает проверяемые утверждения из ADR. Сопоставляет с тестами: покрыто ли каждое архитектурное решение тестами. |
| **Проблема** | Команда приняла ADR-003 «Все ошибки должны возвращать ProblemDetail (RFC 7807)». А тесты проверяют это? |

### Memory Bank

#### 15. `project-memory-bank` — Банк памяти проекта

**Файл:** `.memory-bank/` — директория, живущая рядом со скиллами, накапливает знания между запусками.

**Структура:**
```
.memory-bank/
├── project-context.md        # Актуальный <project_context> (обновляется после каждого Deep Scan)
├── decisions-log.md          # Журнал решений: когда пользователь выбрал "analytics_wins" при конфликте
├── run-history.json          # История всех запусков пайплайна: execution_id, статус, метрики
├── learned-rules.json        # Правила, выученные на паттернах отказов (см. rejection-pattern-learner)
├── style-profile.json        # Стилистический профиль тестов проекта (см. style-drift-detector)
├── domain-dictionary.json    # Словарь предметной области (см. domain-dictionary-builder)
├── auto-fix-stats.json       # Статистика успешности автофиксов (см. confidence-calibrator)
├── prompt-variants/          # Эволюционные варианты SKILL.md (см. evolutionary-prompt-tuner)
├── trust-boundaries.json     # Границы доверия (см. gradual-trust-escalator)
└── fingerprint.json          # Отпечаток проекта (см. project-fingerprinter)
```

---

## Раунд 3: Самообучение и Zero-Friction Onboarding

### Самообучение

#### 16. `rejection-pattern-learner` — Обучение на паттернах отказов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@learning`, `@patterns`, `@feedback-loop` |
| **Хранилище** | `.memory-bank/learned-rules.json` |
| **Суть** | Каждый раз, когда ревьюер возвращает `ТРЕБУЕТ ДОРАБОТКИ`, система классифицирует ошибку в паттерн. После 3 отказов одного класса — выучивает правило и автоматически инжектит его в генератор. |
| **Пример** | 3 отказа «нет null-проверки для поля» → правило: «ВСЕ поля DTO должны иметь тест на null-значение». |

#### 17. `style-drift-detector` — Подстройка под стиль проекта

| Параметр | Значение |
|----------|----------|
| **Теги** | `@style`, `@adaptation`, `@code-review` |
| **Хранилище** | `.memory-bank/style-profile.json` |
| **Суть** | Анализирует существующие тесты в проекте и извлекает стилистический профиль: assertion library, assertion style, test structure, naming convention, mock style, grouping, setup style, wiremock style, soft assertions. При следующей генерации `tc-to-autotest` получает `<style_profile>` и генерирует код, неотличимый от написанного командой. |

#### 18. `domain-dictionary-builder` — Автословарь предметной области

| Параметр | Значение |
|----------|----------|
| **Теги** | `@domain`, `@dictionary`, `@terminology` |
| **Хранилище** | `.memory-bank/domain-dictionary.json` |
| **Суть** | После каждого прогона извлекает ключевые сущности, бизнес-правила и терминологию из аналитики, кода, ТК. Строит доменный граф. При генерации новых ТК — использует терминологию команды и учитывает известные бизнес-правила. |
| **Пример** | Команда говорит «пролонгация» (не «продление»), `amount ∈ [100, 1_000_000]`, «подписка продлевается за 3 дня до окончания». |

#### 19. `confidence-calibrator` — Калибровка уверенности автофиксов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@calibration`, `@auto-fix`, `@confidence` |
| **Хранилище** | `.memory-bank/auto-fix-stats.json` |
| **Суть** | Для каждого типа автофикса ведётся статистика: applied / accepted / rejected_later. При confidence < 0.8 → пометка `⚠️ LOW_CONFIDENCE`. При confidence < 0.5 → автофикс не применяется, возвращается `ТРЕБУЕТ ДОРАБОТКИ`. |
| **Пример** | `add_missing_null_check`: 47/47 accepted → confidence 1.0. `split_parameterized_test`: 2/3 accepted → confidence 0.67 → LOW_CONFIDENCE. |

#### 20. `evolutionary-prompt-tuner` — Эволюционная оптимизация промптов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@evolution`, `@prompt-engineering`, `@ab-testing` |
| **Хранилище** | `.memory-bank/prompt-variants/` |
| **Суть** | Для каждого скилла поддерживается базовый SKILL.md и N экспериментальных вариантов. При запуске случайно выбирается вариант. Результат оценивается через ревьюера (ПРИНЯТО = success). После 10+ запусков — лучший вариант становится новым базовым. |
| **Бредовая ценность** | Буквально эволюция скиллов под конкретный проект: через 50 запусков SKILL.md адаптируется под то, что лучше работает именно здесь. |

### Zero-Friction Onboarding

#### 21. `project-fingerprinter` — Снятие отпечатка проекта

| Параметр | Значение |
|----------|----------|
| **Теги** | `@fingerprint`, `@discovery`, `@profile` |
| **Хранилище** | `shared/fingerprints/` — библиотека профилей |
| **Суть** | Deep Scan дополняется fingerprinting: spring-boot-3.2-junit5-restassured-wiremock-postgresql-maven-lombok-sdd. Сравнение с базой известных fingerprint'ов → загрузка преднастроенного профиля (naming conventions, mock-стратегия, паттерны assert'ов, DTO-шаблоны). |
| **Проблема** | Не спрашивать пользователя о стеке, а узнавать проект в лицо. |

#### 22. `gradual-trust-escalator` — Постепенное наращивание доверия

| Параметр | Значение |
|----------|----------|
| **Теги** | `@trust`, `@autonomy`, `@advisory` |
| **Хранилище** | `.memory-bank/trust-boundaries.json` |
| **Суть** | Новый проект → advisory mode (только предлагает). День 3 → semi-auto (делает, но показывает). День 7 → auto (делает без подтверждения). День 30 → полный auto, кроме отмеченных пользователем зон. |
| **Этапы** | `advisory → semi-auto → auto → full-auto` с накоплением статистики успешных решений. |

#### 23. `self-healing-configurator` — Самовосстанавливающаяся конфигурация

| Параметр | Значение |
|----------|----------|
| **Теги** | `@self-healing`, `@config`, `@drift-detection` |
| **Суть** | Если `.skillsrc` говорит `test.framework: junit5`, а в проекте все тесты — pytest → система сама находит и исправляет расхождение. Авто-диагностика + предложение автоисправления. |
| **Проблема** | «Я скопировал скиллы из проекта A в проект B, и всё сломалось». |

#### 24. `plugin-discovery-protocol` — Автообнаружение плагинов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@plugin`, `@discovery`, `@extensibility` |
| **Хранилище** | `plugins/` — директория с пользовательскими скиллами |
| **Суть** | Любой разработчик может написать скилл, положить в `plugins/` с `plugin.json` (метаданные: имя, теги, контракты, pipeline_hook, priority). Оркестратор при старте сканирует `plugins/` и автоматически встраивает их в пайплайн. Никакой правки `.skillsrc` или `PIPELINE.md`. |
| **plugin.json** | `{ "name": "...", "pipeline_hook": "after:tc-reviewer", "priority": "append" }` |

#### 25. `skeleton-scaffolder` — Генератор скиллов под проект

| Параметр | Значение |
|----------|----------|
| **Теги** | `@scaffold`, `@personalization`, `@bootstrap` |
| **Команда** | `orchestrate scaffold --from-scratch` |
| **Суть** | Одноразовый запуск: Deep Scan → генерация персонализированных SKILL.md под каждый компонент (с упоминанием КОНКРЕТНЫХ DTO, контроллеров, эндпоинтов). Генерирует `.skillsrc`, `.memory-bank/`, начальный `project_context`. |
| **Результат** | Скиллы, которые выглядят так, как будто их писали разработчики этого конкретного проекта, а не абстрактный AI. |

---

## Раунд 4: Meta-Testing — Тестирование самих скиллов

### 26. `skill-tester` — Фреймворк тестирования скиллов (Meta-Testing Framework)

| Параметр | Значение |
|----------|----------|
| **Теги** | `@meta-testing`, `@quality`, `@regression`, `@skill-qa` |
| **Вход** | `<skill_under_test>` (SKILL.md) + `<golden_dataset>` (эталонные входы) + `<expected_contracts>` |
| **Выход** | `<skill_test_report>` с секциями `contract_compliance`, `semantic_quality`, `behavioral_stability`, `regression_verdict` |
| **Суть** | Тестирует другие скиллы по трём ортогональным осям качества. **Ось 1 — Контрактная (структурная):** парсится ли выходной XML, все ли обязательные теги присутствуют, не добавил ли мусорных тегов вне контракта. Автоматизируемо на 100% через JSON Schema (уже есть в `schemas/`). **Ось 2 — Семантическая (качество):** на эталонных датасетах проверяет, что ТК покрывают AC из аналитики, автотесты компилируются и запускаются, нет галлюцинаций (выдуманных эндпоинтов/полей), ревьюер ловит реальные ошибки, а не шум. Метрики: `coverage_of_ac (%)`, `compilation_pass_rate`, `hallucination_rate`. **Ось 3 — Поведенческая (стабильность):** после изменения SKILL.md сравнивает качество на эталонных входах — не деградировал ли скилл. Ключевой кейс для эволюции скиллов. |
| **Проблема** | Скиллы эволюционируют (меняются SKILL.md, добавляются правила, правится порядок инструкций), но нет способа проверить, что changes не сломали их. Система тестирует продукты скиллов (ТК, автотесты), но не тестирует сами скиллы. Это meta-testing: тесты для тестировщика. |

| Параметр | Значение |
|----------|----------|
| **Требуемая инфраструктура** | `.memory-bank/golden-datasets/` — эталонные входы-выходы для каждого скилла; `.memory-bank/skill-test-history.json` — история всех тестовых прогонов; `.memory-bank/quality-thresholds.json` — пороги приёмки: `contract=100%`, `semantic≥80%`, `behavioral_stability≥90%` |
| **CI-интеграция** | `orchestrate test-skill --skill=tc-generator --golden-dataset=default` → возвращает `<skill_test_report>` с вердиктом `PROMOTE` / `WARN` / `BLOCK`. При `BLOCK` — CI падает, деплой SKILL.md блокируется. |
| **Почему это сложно** | Скилл — не функция с детерминированным `f(x) → y`. Это AI-агент: один и тот же вход может дать разный (но корректный) выход. Качество зависит от формулировки SKILL.md, порядка инструкций, примеров. Нельзя написать `assertEquals(expected, actual)` — нет единственного правильного ответа. Поэтому тестирование трёхосевое: контракт (жёсткое), семантика (метрики), поведение (сравнение с baseline). |

### 27. `golden-dataset-curator` — Куратор эталонных датасетов

| Параметр | Значение |
|----------|----------|
| **Теги** | `@golden-dataset`, `@curation`, `@baseline` |
| **Вход** | `<skill_output_history>` (N успешных прогонов скилла) + `<review_verdicts>` |
| **Выход** | `<golden_dataset>` — эталонный набор входов и ожидаемых выходов |
| **Суть** | Автоматически формирует эталонные датасеты для `skill-tester`. Отбирает прогоны с вердиктом `ПРИНЯТО` от ревьюера, нормализует (убирает несущественные вариации), сохраняет как baseline. Обновляется при каждом принятом прогоне. |
| **Хранилище** | `.memory-bank/golden-datasets/{skill-name}/` — по одному датасету на скилл |

---

# Часть 2: Дорожная карта (Roadmap)

## v3.0 — «Memory & Trust» (фундамент)

> **Цель:** система перестаёт быть амнезичной. Появляется память между запусками и адаптация к проекту.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 1 | **`project-memory-bank`** (#15) | Инфраструктура | 🔴 Critical |
| 2 | **`style-drift-detector`** (#17) | Самообучение | 🔴 Critical |
| 3 | **`rejection-pattern-learner`** (#16) | Самообучение | 🟠 High |
| 4 | **`domain-dictionary-builder`** (#18) | Самообучение | 🟠 High |
| 5 | **`gradual-trust-escalator`** (#22) | Zero-Friction | 🟠 High |
| 6 | **`self-healing-configurator`** (#23) | Zero-Friction | 🟡 Medium |

**Новые контракты:**
- `<learned_rules>` — передаётся в генераторы при каждом запуске
- `<style_profile>` — передаётся в `tc-to-autotest`
- `<domain_dictionary>` — передаётся во все скиллы как часть `<project_context>`
- `<trust_level>` — уровень автономности: `advisory | semi-auto | auto | full-auto`

**Новые файлы:**
- `.memory-bank/` (директория)
- `shared/fingerprints/` (директория профилей)

---

## v3.1 — «Quality Gates & Feedback Loops»

> **Цель:** замкнуть петлю обратной связи. Ошибки не просто фиксятся — они предотвращаются.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 7 | **`confidence-calibrator`** (#19) | Самообучение | 🟠 High |
| 8 | **`flakiness-detector`** (#7) | Новый скилл | 🔴 Critical |
| 9 | **`impact-analyzer`** (#1) | Новый скилл | 🟠 High |
| 10 | **Diff-пайплайн (Incremental Pipeline)** (#A) | Инфраструктура | 🟠 High |

**Новые контракты:**
- `<previous_run_id>` + `<baseline_artifacts>` — для инкрементального пайплайна
- `<flakiness_report>` + `<review_verdict>` — выход flakiness-detector

---

## v3.2 — «Skill Quality Assurance» (Meta-Testing)

> **Цель:** система тестирует не только продукты, но и саму себя. Каждый скилл проходит CI-пайплайн качества перед деплоем.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 11 | **`skill-tester`** (#26) | Meta-Testing | 🔴 Critical |
| 12 | **`golden-dataset-curator`** (#27) | Инфраструктура | 🟠 High |

**Новый пайплайн:** `skill-qa-pipeline`
```
golden-dataset-curator → skill-tester (3-stage)
                              ├─ Stage 1: Contract Compliance    (JSON Schema, 100% required)
                              ├─ Stage 2: Semantic Quality       (coverage_of_ac, compilation, hallucinations)
                              └─ Stage 3: Behavioral Stability   (regression vs baseline)
                                       ↓
                              <skill_test_report> → PROMOTE / WARN / BLOCK
```

**Новые контракты:**
- `<skill_test_report>` с вердиктом `PROMOTE` / `WARN` / `BLOCK` и секциями `contract_compliance`, `semantic_quality`, `behavioral_stability`
- `<golden_dataset>` — эталонный набор входов-выходов для каждого скилла

**Новые файлы:**
- `.memory-bank/golden-datasets/{skill-name}/` — эталонные датасеты
- `.memory-bank/skill-test-history.json` — история тестовых прогонов
- `.memory-bank/quality-thresholds.json` — пороги приёмки

**CI-интеграция:**
```
# Запуск при изменении SKILL.md любого скилла:
orchestrate test-skill --skill=tc-generator --golden-dataset=default
orchestrate test-skill --skill=tc-reviewer --golden-dataset=default
orchestrate test-skill --skill=tc-to-autotest --golden-dataset=default

# При BLOCK → CI падает, деплой SKILL.md блокируется
# При WARN → CI проходит с предупреждением, требует ручного ревью
# При PROMOTE → автоматический деплой
```

---

## v4.0 — «SDD-Native»

> **Цель:** спецификация становится не просто источником контекста, а активным участником пайплайна.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 13 | **`spec-validator`** (#9) | SDD-скилл | 🔴 Critical |
| 14 | **`spec-coverage-analyzer`** (#10) | SDD-скилл | 🟠 High |
| 15 | **`breaking-change-detector`** (#11) | SDD-скилл | 🟠 High |
| 16 | **`spec-to-tc-direct`** (#12) | SDD-скилл | 🟡 Medium |
| 17 | **`bpmn-scenario-extractor`** (#13) | SDD-скилл | 🟡 Medium |
| 18 | **`adr-compliance-checker`** (#14) | SDD-скилл | 🟢 Low |

**Новый пайплайн:** `sdd-pipeline`
```
spec-validator → spec-to-tc-direct → tc-generator → ... → spec-coverage-analyzer
                                                                    ↓
                                                          adr-compliance-checker
```

**Новые контракты:**
- `<spec_validation_report>` с вердиктом `SPEC_READY` / `SPEC_NEEDS_FIX`
- `<spec_coverage_report>` с heatmap-матрицей
- `<breaking_change_report>` с уровнями `CRITICAL` / `WARNING`
- `<bpmn_scenario_analysis>` с traceability к BPMN Element ID
- `<adr_compliance_report>` с traceability `ADR-N → ТК-N (или GAP)`

---

## v4.1 — «Cross-Cutting Concerns»

> **Цель:** покрыть смежные области тестирования — контракты, данные, наблюдаемость.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 19 | **`contract-tester`** (#4) | Новый скилл | 🟠 High |
| 20 | **`test-data-fabricator`** (#3) | Новый скилл | 🟠 High |
| 21 | **`observability-tester`** (#5) | Новый скилл | 🟡 Medium |
| 22 | **Кросс-пайплайн (Cross-Pipeline Traceability)** (#B) | Инфраструктура | 🟡 Medium |

**Новые контракты:**
- `<contract_test_suite>` + `<contract_test_matrix>`
- `<test_data_suite>` с `valid_equivalence`, `boundary_values`, `invalid_inputs`, `sql_fixtures`
- `<observability_test_suite>` + `<observability_coverage_report>`
- `<service_graph>` — граф зависимостей между сервисами

---

## v5.0 — «Ecosystem & Plugins»

> **Цель:** система становится платформой. Любой может расширить её без правки ядра.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 23 | **`plugin-discovery-protocol`** (#24) | Zero-Friction | 🔴 Critical |
| 24 | **`project-fingerprinter`** (#21) | Zero-Friction | 🟠 High |
| 25 | **`skeleton-scaffolder`** (#25) | Zero-Friction | 🟠 High |
| 26 | **Плагин-архитектура для языков** (#C) | Инфраструктура | 🟡 Medium |
| 27 | **`evolutionary-prompt-tuner`** (#20) | Самообучение | 🟢 Low |

**Новые артефакты:**
- `plugins/` — директория с пользовательскими скиллами
- `plugin.json` — стандарт метаданных плагина
- `templates/` — расширяемая библиотека языковых шаблонов

---

## v5.1 — «Planning & Communication»

> **Цель:** система общается с менеджментом и другими командами.

| # | Фича | Тип | Приоритет |
|---|------|-----|-----------|
| 28 | **`tc-prioritizer`** (#2) | Новый скилл | 🟡 Medium |
| 29 | **`test-estimator`** (#8) | Новый скилл | 🟢 Low |
| 30 | **`gherkin-exporter`** (#6) | Новый скилл | 🟢 Low |

**Новые контракты:**
- `<priority_matrix>` с `P0/P1/P2`
- `<estimation_report>` с `manual_effort_hours`, `automation_effort_hours`
- `<gherkin_suite>` + `.feature` файлы

---

# Часть 3: Визуальная дорожная карта

```
2026 Q3 ────────────────────────────────────────────────────────────────
│ v3.0 «Memory & Trust»
│  ├─ project-memory-bank      ████████████████████████ (фундамент)
│  ├─ style-drift-detector     ████████████████████████
│  ├─ rejection-pattern-learner███████████████████
│  ├─ domain-dictionary-builder███████████████████
│  ├─ gradual-trust-escalator  ██████████████████
│  └─ self-healing-configurator████████████████
│
│ v3.1 «Quality Gates & Feedback Loops»
│  ├─ confidence-calibrator    █████████████████
│  ├─ flakiness-detector       ████████████████████████ (боль CI/CD)
│  ├─ impact-analyzer          ████████████████████
│  └─ Diff-пайплайн            ████████████████████
│
│ v3.2 «Skill Quality Assurance»
│  ├─ skill-tester             ████████████████████████ (meta-testing)
│  └─ golden-dataset-curator   ████████████████████
│
2026 Q4 ────────────────────────────────────────────────────────────────
│ v4.0 «SDD-Native»
│  ├─ spec-validator           ████████████████████████ (gatekeeper)
│  ├─ spec-coverage-analyzer   ████████████████████
│  ├─ breaking-change-detector ████████████████████
│  ├─ spec-to-tc-direct        █████████████████
│  ├─ bpmn-scenario-extractor  █████████████████
│  └─ adr-compliance-checker   ████████████
│
│ v4.1 «Cross-Cutting Concerns»
│  ├─ contract-tester          ████████████████████
│  ├─ test-data-fabricator     ████████████████████
│  ├─ observability-tester     █████████████████
│  └─ Cross-Pipeline           █████████████████
│
2027 Q1 ────────────────────────────────────────────────────────────────
│ v5.0 «Ecosystem & Plugins»
│  ├─ plugin-discovery-protocol████████████████████████ (платформа)
│  ├─ project-fingerprinter    ████████████████████
│  ├─ skeleton-scaffolder      ████████████████████
│  ├─ языковые шаблоны         █████████████████
│  └─ evolutionary-prompt-tuner█████████████
│
│ v5.1 «Planning & Communication»
│  ├─ tc-prioritizer           █████████████████
│  ├─ test-estimator           ████████████
│  └─ gherkin-exporter         ████████████
│
2027 Q2+ ───────────────────────────────────────────────────────────────
│  🚀 Полная экосистема: 30 фич, 6 скиллов → 20+ скиллов,
│     самообучающаяся, самовосстанавливающаяся, plug-and-play
```

---

## Сводка: метрики роста

| Метрика | v2.x (сейчас) | v5.1 (цель) |
|---------|---------------|-------------|
| Скиллов | 6 | 20+ |
| Пайплайнов | 1 (test-pipeline) | 5+ (test, skill-qa, sdd, contract, observability) |
| Память между запусками | ❌ | ✅ .memory-bank/ |
| Адаптация под проект | Ручная (.skillsrc) | Автоматическая (style-drift, domain-dictionary) |
| Самообучение | ❌ | ✅ (rejection-patterns, prompt-evolution) |
| Meta-Testing (тесты для скиллов) | ❌ | ✅ (skill-tester, golden-datasets) |
| Zero-Config | 80% проектов | 95%+ проектов |
| Расширяемость | Правка ядра | Плагины в plugins/ |
| Контрактов в CONTRACTS.md | 10 выходных тегов | 30+ выходных тегов |

---

*Связанные файлы: `.skillsrc` (манифест), `CONTRACTS.md` (канон контрактов), `PIPELINE.md` (схема пайплайнов), `Instruction.md` (инструкция), `USER-GUIDE.md` (руководство пользователя).*