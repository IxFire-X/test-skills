# Инструкция по настройке и использованию скиллов

> **Версия:** v0.6.0 (2026-07-07) — добавлен скилл `context-marker` (разметка сырых `.md`-файлов аналитики в XML-теги). Теперь осталось 6 скиллов (4 тестовых + Оркестратор + context-marker).
> **Аудитория:** инженер / аналитик / QA-лид, который впервые разворачивает скиллы у себя в проекте.

> **Канон контрактов и статус-маркеров:** см. `CONTRACTS.md` (этот документ — **единственный источник истины**). Все скиллы обязаны ссылаться на `CONTRACTS.md` для уточнения имён тегов и статусов.
> **Руководство по эксплуатации (промпты / сценарии / cheat sheet):** см. `USER-GUIDE.md` — быстрый старт без чтения SKILL.md.

---

## Что это за скиллы

Набор из **6 специализированных AI-скиллов**, которые работают вместе как **1 предопределённый пайплайн** + 1 скилл-координатор + 1 скилл разметки контекста.

### Пайплайн

| # | Пайплайн | Цепочка | Когда использовать |
|---|---|---|---|
| 1 | **`test-pipeline`** | `tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer` | Нужны ручные ТК + автотесты |

Пайплайн управляется скиллом **`orchestrate`** (Оркестратор), который автоматически:

- выбирает пайплайн по триггер-фразе;
- передаёт контекст между шагами (через XML-контракты, см. `CONTRACTS.md`);
- обрабатывает ошибки и retry;
- пишет отчёт `docs/to_do/orchestration-report-<TIMESTAMP>.md`;
- перед стартом выполняет **Contract Check** по `CONTRACTS.md` §5.

---

## Состав скиллов

### Тест-пайплайн

| Скилл | Что делает | Вход → Выход (канон) |
|---|---|---|
| **tc-generator** (`Ручные тест-кейсы/SKILL.md`) | Генерирует ручные тест-кейсы из аналитики и кода | `<analytics_documentation>` + `<source_code_and_diff>` → `<generated_test_cases>` |
| **tc-reviewer** (`Валидация тест-кейсов/SKILL.md`) | Проверяет ТК на полноту/корректность, может править автофиксом | `<generated_test_cases>` → `<validation_report>` + (опц.) `<corrected_test_cases>` |
| **tc-to-autotest** (`Автоматизированные кейсы на основе тест-кейсов/SKILL.md`) | Превращает ТК в автотесты; идемпотентен (поддерживает `<automation_matrix>` для diff'а) | `<test_cases>` + (опц.) `<automation_matrix>` + (опц.) `<existing_project_context>` → файлы автотестов + `<automation_matrix>` + `<automation_analysis>` + `<conflict_resolution>` (опц.) |
| **autotest-reviewer** (`Валидация автотестов/SKILL.md`) | Проверяет качество автотестов | `<test_cases>` + `<automation_matrix>` + `<autotest_code>` → `<autotest_review>` + `<review_verdict>` (`ПРИНЯТО` / `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ`) + (опц.) `<review_comments>` + (опц.) `<corrected_autotest_code>` |

### Препроцессор контекста

| Скилл | Что делает | Вход → Выход (канон) |
|---|---|---|
| **context-marker** (`Разметка контекста/SKILL.md`) | Размечает сырые `.md`-файлы аналитики/кода/ТК в XML-теги для пайплайна. Автоопределяет тип контента (analytics / source_code / test_cases). Поддерживает batch-разметку директорий. | `<raw_content>` + `<content_type>` (опц.) + `<file_path>` (опц.) → `<analytics_documentation>` / `<source_code_and_diff>` / `<test_cases>` |

### Координатор

| Скилл | Что делает | Вход → Выход (канон) |
|---|---|---|
| **orchestrate** (`Оркестратор/SKILL.md`) | Управляет пайплайнами, маршрутизирует статусы, делает retry, эскалирует | `<goal>` + (опц.) `<pipeline>`, `<max_iterations>`, `<context>`, `<strict_mode>` → `<orchestration_result>` (`completed` / `partial` / `failed` / `retry`) |

Каждый скилл можно использовать **отдельно** (без Оркестратора), но максимальная польза — при последовательном запуске через `orchestrate`.

---

## Предварительные требования

### 1. Доступ к AI-ассистенту

Скиллы работают через AI-ассистента (Claude, ChatGPT или аналог). Убедитесь, что у вас есть:

- Поддержка загрузки файлов или вставки больших текстов
- Достаточное context window (рекомендуется ≥ 64K токенов для больших фич)
- Возможность передавать `SKILL.md` как системный промпт / контекст

### 2. Подготовленные данные

| Данные | Обязательность | Формат |
|---|---|---|
| **Аналитическая документация** | Желательно | Текст: описание фичи, требования, AC, контракты API (Swagger / OpenAPI / описание) |
| **Исходный код / diff** | Желательно | Релевантные классы, контроллеры, DTO, SQL-миграции, diff из PR |
| **Архитектурный контекст проекта** | Для `tc-to-autotest` | Передаётся в `<existing_project_context>` |

> Без аналитики и кода скиллы перейдут в ручной режим и будут задавать уточняющие вопросы — это работает, но медленнее.

---

## Настройка

### Шаг 1. Скачайте скиллы

Структура папок (унифицирована в v0.5.0 — только тестовый пайплайн + Оркестратор):

```
Скиллы для работы/
├── Instruction.md                                  ← этот файл
├── PIPELINE.md                                     ← схема пайплайна + контракты
├── CONTRACTS.md                                    ← канон XML-тегов и статус-маркеров
│
├── Ручные тест-кейсы/                              ← tc-generator
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
├── Валидация тест-кейсов/                          ← tc-reviewer
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
├── Автоматизированные кейсы на основе тест-кейсов/ ← tc-to-autotest
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
├── Валидация автотестов/                           ← autotest-reviewer
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
├── Разметка контекста/                             ← context-marker
│   ├── README.md
│   ├── SKILL.md
│   └── examples.md
│
└── Оркестратор/                                    ← orchestrate
    ├── README.md
    ├── SKILL.md
    ├── examples.md
    └── CHANGELOG.md                                ← история версий скилла
```

### Шаг 2. Загрузите `SKILL.md` в контекст AI

- **Claude (Web)**: скрепка → загрузить `SKILL.md` → содержимое попадёт в system prompt
- **Claude (API)**: передать в поле `system` содержимое `SKILL.md`
- **ChatGPT**: вставить в **Custom Instructions** или начало чата
- **Cursor / Windsurf / Cline**: положить `SKILL.md` в корень проекта — AI подхватит автоматически

### Шаг 3. Подготовьте входные данные

Сформируйте XML-теги и вставьте в диалог.

**Пример: `<analytics_documentation>`**
```xml
<analytics_documentation>
## Фича: Создание заявки на перевод

### Acceptance Criteria
- AC-1: Заявка создаётся с обязательными полями: amount, currency, recipientId
- AC-2: amount ∈ [1, 1_000_000]
- AC-3: currency ∈ {RUB, USD, EUR}

### API
POST /api/v1/transfers
Request: { amount: BigDecimal, currency: String, recipientId: UUID }
Response: { transferId: UUID, status: String, createdAt: ISO8601 }
</analytics_documentation>
```

**Пример: `<source_code_and_diff>`**
```xml
<source_code_and_diff>
## TransferController.java
@PostMapping("/transfers")
public ResponseEntity<TransferResponse> createTransfer(@RequestBody TransferRequest request) { ... }

## TransferRequest.java
public class TransferRequest {
  @NotNull private BigDecimal amount;
  @NotNull private String currency;
  @NotNull private UUID recipientId;
}
</source_code_and_diff>
```

---

## Использование

### Сценарий 1: Полный цикл через Оркестратор (рекомендуется)

**Вход:**
```
Создай тест-кейсы и автотесты для TransferService
```

**Поток:**
1. `orchestrate` выполняет **Deep Scan** (структурный анализ проекта) и определяет методологию.
2. **Если проект SDD и аналитика в сыром `.md`** → `orchestrate` диспетчеризует `context-marker` для разметки (оборачивает `.md` в `<analytics_documentation>`).
3. **Contract Check** (см. `Оркестратор/SKILL.md` § «Контрактное версионирование»): проверяет имена тегов и статусов по `CONTRACTS.md`.
4. `tc-generator` → генерирует ручные тест-кейсы → `<generated_test_cases>`.
5. `tc-reviewer` → валидирует ТК → `<validation_report>` + (опц.) `<corrected_test_cases>`.
6. `tc-to-autotest` → генерирует автотесты → `<automation_matrix>` + `<automation_analysis>` + файлы.
7. `autotest-reviewer` → проверяет автотесты → `<autotest_review>` + `<review_verdict>`.
8. `orchestrate` пишет `docs/to_do/orchestration-report-<TIMESTAMP>.md`.

**Выход:**
```xml
<orchestration_result>
  <status>completed | partial | failed | retry</status>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>N</iterations>
  <steps>...</steps>
  <final_result>...</final_result>
  <warnings>...</warnings>
</orchestration_result>
```

---

### Сценарий 2: Пошагово руками (без Оркестратора)

#### Шаг 1: `tc-generator`

```
Загрузите: Ручные тест-кейсы/SKILL.md
Вход:     <analytics_documentation> + <source_code_and_diff>
Выход:    <generated_test_cases>
```

#### Шаг 2: `tc-reviewer`

```
Загрузите: Валидация тест-кейсов/SKILL.md
Вход:     <generated_test_cases> + (опц.) <analytics_documentation> + <source_code_and_diff>
Выход:    <validation_report>
          + <corrected_test_cases> (если AUTO_FIX_APPLIED или ТРЕБУЕТ ДОРАБОТКИ)
Статус:   ПРИНЯТО | AUTO_FIX_APPLIED | ТРЕБУЕТ ДОРАБОТКИ
```

> **Важно:** статус `AUTO_FIX_APPLIED` означает, что скилл сам внёс правки в ТК. Используйте `<corrected_test_cases>` на следующем шаге, а не исходные `<generated_test_cases>`.

#### Шаг 3: `tc-to-autotest`

```
Загрузите: Автоматизированные кейсы на основе тест-кейсов/SKILL.md
Вход:     <test_cases>  ← берётся <corrected_test_cases> или <generated_test_cases>
          (опц.) <automation_matrix>     ← для итеративной регенерации
          (опц.) <existing_project_context>
Выход:    Файлы автотестов (DTO + тест-класс) + <automation_matrix> + <automation_analysis>
          + <conflict_resolution> (если был <automation_matrix> на входе)
```

> **Новое поведение (идемпотентность):** если передать существующий `<automation_matrix>`, скилл сравнит его с актуальным набором `ТК-N` и:
> - сохранит существующие методы для ТК без изменений;
> - удалит методы для удалённых ТК (с записью в `<removed_test_cases>`);
> - сгенерирует методы для новых ТК;
> - переименует при расщеплении ТК (например, `ТК-15` → `ТК-15а` + `ТК-15б`).

#### Шаг 4: `autotest-reviewer`

```
Загрузите: Валидация автотестов/SKILL.md
Вход:     <test_cases> + <automation_matrix> + <autotest_code>
Выход:    <autotest_review> + <review_verdict> + (опц.) <review_comments>
          + (опц.) <corrected_autotest_code>
Статус:   ПРИНЯТО | AUTO_FIX_APPLIED | ТРЕБУЕТ ДОРАБОТКИ
```

> Если `ТРЕБУЕТ ДОРАБОТКИ` — передайте `<review_comments>` обратно в `tc-to-autotest` для повторной генерации (с теми же `<test_cases>`).

---

## Словарь XML-тегов

> **Канон:** полный реестр — в `CONTRACTS.md` §2 (выходные теги) и §3 (статус-маркеры). Этот раздел — справочное сокращение для быстрого поиска.

Скиллы общаются друг с другом через **XML-теги**. Это не просто форматирование — теги задают контракт входа/выхода. Все имена корневых блоков фиксированы в `CONTRACTS.md`.

### Входные теги

| Тег | Кто принимает | Что внутри |
|---|---|---|
| `<analytics_documentation>` | `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer` | Бизнес-требования, AC, контракты API |
| `<source_code_and_diff>` | `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer` | Исходный код, diff из PR, SQL |
| `<generated_test_cases>` | `tc-reviewer` | ТК, сгенерированные `tc-generator` |
| `<test_cases>` | `tc-to-autotest`, `autotest-reviewer` | Утверждённые ТК (от reviewer или напрямую) |
| `<corrected_test_cases>` | `tc-to-autotest` (через Оркестратор) | ТК, исправленные `tc-reviewer` |
| `<automation_matrix>` (вход) | `tc-to-autotest` | Матрица `ТК-N → java-метод` от предыдущего запуска |
| `<autotest_code>` | `autotest-reviewer` | Код автотестов (или Markdown-блоки) |
| `<existing_project_context>` | `tc-to-autotest` | Архитектурный шаблон существующего проекта |
| `<raw_content>` | `context-marker` | Сырой контент для разметки (`.md`-файл без XML-тегов) |
| `<content_type>` | `context-marker` | Явное указание типа контента (опц.): `analytics`, `source_code`, `test_cases` |
| `<file_path>` | `context-marker` | Путь к исходному файлу (опц., для логирования) |
| `<goal>` | `orchestrate` | Цель в свободной форме (например, «создай тест-кейсы для X») |
| `<pipeline>` | `orchestrate` | Имя пайплайна (опц., по умолчанию — `test-pipeline`) |
| `<max_iterations>` | `orchestrate` | Лимит итераций (по умолчанию `3`) |
| `<strict_mode>` | `orchestrate` | `true` / `false` (по умолчанию `false`) |
| `<context>` | `orchestrate` | Дополнительный контекст проекта |

### Выходные корневые блоки (канон)

| Тег | Кто выдаёт | Кто принимает |
|---|---|---|
| `<generated_test_cases>` | `tc-generator` | `tc-reviewer` |
| `<validation_report>` | `tc-reviewer` | (логирование) |
| `<corrected_test_cases>` | `tc-reviewer` | `tc-to-autotest` |
| `<automation_analysis>` | `tc-to-autotest` | (отчёт) |
| `<automation_matrix>` (выход) | `tc-to-autotest` | следующий запуск `tc-to-autotest` или `autotest-reviewer` |
| `<conflict_resolution>` | `tc-to-autotest` | (отчёт о diff'е) |
| `<autotest_review>` | `autotest-reviewer` | (логирование) |
| `<review_verdict>` | `tc-reviewer`, `autotest-reviewer` | (логирование) |
| `<review_comments>` | `tc-reviewer`, `autotest-reviewer` | (логирование) |
| `<corrected_autotest_code>` | `autotest-reviewer` | (при `AUTO_FIX_APPLIED`) |
| `<orchestration_result>` | `orchestrate` | пользователь |

### Статус-маркеры (канон)

| Маркер | Источник | Означает | Реакция |
|---|---|---|---|
| `ПРИНЯТО` | `tc-reviewer`, `autotest-reviewer` | OK | Продолжить |
| `AUTO_FIX_APPLIED` | `tc-reviewer`, `autotest-reviewer` | Скилл сам внёс правки | Использовать `<corrected_*>` |
| `ТРЕБУЕТ ДОРАБОТКИ` | `tc-reviewer`, `autotest-reviewer` | FAIL без автофикса | Повторить с `<review_comments>` |
| `failed` | любой | Критическая ошибка | Стоп + эскалация |

---

## Советы по эффективному использованию

### 1. Готовьте входные данные заранее
Чем полнее `<analytics_documentation>` и `<source_code_and_diff>`, тем точнее результат.

### 2. Не пропускайте валидацию
`tc-reviewer` и `autotest-reviewer` ловят дефекты, которые дорого исправлять потом.

### 3. Используйте `<automation_matrix>` для итеративной регенерации
Если нужно **обновить** уже существующие автотесты (изменились ТК), передавайте старую `<automation_matrix>` в `tc-to-autotest` — скилл сделает **diff** (сохранит / удалит / добавит / расщепит), а не перепишет всё с нуля.

### 4. Уважайте статусы ревьюеров
- `ПРИНЯТО` — берите исходный артефакт.
- `AUTO_FIX_APPLIED` — берите `<corrected_*>` (скилл уже всё поправил).
- `ТРЕБУЕТ ДОРАБОТКИ` — не правьте руками, передайте `<review_comments>` в генератор.

### 5. Используйте Оркестратор для сложных цепочек
Вместо ручного запуска 4 шагов скажите: «создай тест-кейсы и автотесты для X». Оркестратор сам выберет пайплайн, проконтролирует итерации, выполнит **Contract Check** (см. `Оркестратор/SKILL.md` § «Контрактное версионирование») и сгенерирует отчёт.

### 6. Следите за отчётами
`docs/to_do/orchestration-report-*.md` — это ваш журнал. При `status = failed` ищите рекомендации в секции «Рекомендации» внизу файла. Секция «Contract Check» в начале отчёта — фиксация проверки контрактов.

### 7. Версионируйте скиллы
В корне каждого скилла должен быть `CHANGELOG.md` (образец — `Оркестратор/CHANGELOG.md`). Перед обновлением `SKILL.md` обязательно добавьте запись в `[Unreleased]`.

### 8. Сверяйтесь с `CONTRACTS.md`
Если вы не уверены в имени тега или статуса — откройте `CONTRACTS.md`. Это **единственный источник истины**.

---

## Частые вопросы

**Можно ли использовать скиллы по одному?**
Да, каждый скилл автономен. Но максимальная польза — при последовательном использовании.

**Что если у меня нет аналитики, только код?**
Скилл перейдёт в ручной режим и задаст уточняющие вопросы. Результат будет, но процесс займёт больше времени.

**Что если у меня есть аналитика, но она в сыром `.md` без XML-тегов?**
Используйте скилл `context-marker`: скажите «Разметь контекст для пайплайна» и передайте `.md`-файл. Скилл автоопределит тип (analytics / source_code / test_cases) и обернёт в правильный XML-тег. Или просто запустите пайплайн через Оркестратор — он сам вызовет `context-marker` для SDD-проектов.

**Что если у меня нет кода, только аналитика?**
Аналогично — скилл соберёт контекст через вопросы. Но техническая точность может пострадать (имена таблиц, коды ошибок будут примерными).

**Можно ли пропустить `tc-reviewer` и сразу идти в `tc-to-autotest`?**
Можно, но не рекомендуется. Невалидированные ТК могут содержать ошибки, которые перейдут в автотесты.

**Что делать, если `autotest-reviewer` отклонил код?**
Передайте `<review_comments>` обратно в `tc-to-autotest`. Если вы уже передавали `<automation_matrix>` — скилл сделает точечный diff, а не полную перегенерацию.

**Как посмотреть, какие теги принимает скилл?**
В каждом `SKILL.md` есть раздел **«Контракт входа»** (таблица с тегами и обязательностью). Дублируется в `examples.md` (живые примеры). Полный реестр — в `CONTRACTS.md`.

**Где смотреть, что делает скилл в нештатной ситуации?**
В `Оркестратор/SKILL.md` → раздел **«FALLBACK-СТРАТЕГИИ»** (типовые ситуации A–J с реакциями).

---

## Интеграция в CI/CD

Скиллы можно встроить в автоматический пайплайн CI для проверки каждого Pull Request. Ниже — примеры для GitHub Actions.

### GitHub Actions: тест-пайплайн для PR

```yaml
# .github/workflows/skills-test-pipeline.yml
name: Skills — Test Pipeline

on:
  pull_request:
    types: [opened, synchronize, reopened]
    paths:
      - 'src/**'
      - 'docs/analytics/**'

jobs:
  test-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # нужен полный git history для diff

      # Шаг 1: собрать входные данные
      - name: Prepare Input Context
        id: context
        run: |
          # Аналитика: все .md файлы из docs/analytics/
          find docs/analytics -name '*.md' -exec cat {} + > /tmp/analytics.md

          # Код и diff: изменения относительно target-ветки
          git diff origin/${{ github.base_ref }}...HEAD -- src/ > /tmp/diff.patch

          echo "analytics_file=/tmp/analytics.md" >> $GITHUB_OUTPUT
          echo "diff_file=/tmp/diff.patch" >> $GITHUB_OUTPUT

      # Шаг 2: передать в AI-ассистент (через API / CLI)
      # Замените {{YOUR_AI_CLI}} на реальный инструмент (Claude CLI, OpenAI CLI и т.д.)
      - name: Run Test Pipeline
        env:
          AI_API_KEY: ${{ secrets.AI_API_KEY }}
        run: |
          # Пример вызова через гипотетический CLI
          {{YOUR_AI_CLI}} run \
            --skill-file "Оркестратор/SKILL.md" \
            --input-tag analytics_documentation "$(cat ${{ steps.context.outputs.analytics_file }})" \
            --input-tag source_code_and_diff "$(cat ${{ steps.context.outputs.diff_file }})" \
            --pipeline test-pipeline \
            --max-iterations 3 \
            --output-dir "outputs/${{ github.run_id }}"

      # Шаг 3: сохранить артефакты
      - name: Upload Orchestration Report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: orchestration-report-${{ github.run_id }}
          path: outputs/${{ github.run_id }}/
          retention-days: 30

      # Шаг 4: валидация JSON Schema (опционально)
      - name: Validate Output Schemas
        if: always()
        run: |
          npm install -g ajv-cli
          npx ajv validate -s schemas/tc-generator-output.schema.json   -d outputs/${{ github.run_id }}/generated_test_cases.json || true
          npx ajv validate -s schemas/tc-reviewer-output.schema.json    -d outputs/${{ github.run_id }}/validation_report.json || true
          npx ajv validate -s schemas/tc-to-autotest-output.schema.json -d outputs/${{ github.run_id }}/automation_matrix.json || true
          npx ajv validate -s schemas/autotest-reviewer-output.schema.json -d outputs/${{ github.run_id }}/autotest_review.json || true
```

### Формирование входных тегов из PR

| Тег | Источник в CI |
|---|---|
| `<analytics_documentation>` | `.md`-файлы из `docs/analytics/` (собрать `find ... -exec cat`) |
| `<source_code_and_diff>` | `git diff origin/main...HEAD -- src/` |
| `<automation_matrix>` | Скачать артефакт предыдущего запуска (`download-artifact@v4`) |
| `<existing_project_context>` | Структура проекта: `tree src/test/java` (опционально) |

### Размещение отчётов в CI-артефактах

Стандартный путь для выхода Оркестратора:

```
outputs/<run_id>/
├── orchestration-report-<TIMESTAMP>.md   ← основной отчёт
├── generated_test_cases.json             ← выход tc-generator
├── validation_report.json                ← выход tc-reviewer
├── automation_matrix.json                ← выход tc-to-autotest
├── autotest_review.json                  ← выход autotest-reviewer
└── corrected_*                           ← артефакты автофиксов (если были)
```

Артефакты хранятся 30 дней (retention). Для постоянного хранения — настроить `actions/upload-artifact` на S3-совместимое хранилище.

### Проверка статуса пайплайна в PR

Парсите `<orchestration_result>` из отчёта:

```bash
# Извлечь статус оркестратора
STATUS=$(grep -oP '<status>\K[^<]+' outputs/${{ github.run_id }}/orchestration-report-*.md)

case "$STATUS" in
  "completed")
    echo "✅ Все шаги пайплайна выполнены успешно"
    ;;
  "partial")
    echo "⚠️  Часть шагов выполнена с предупреждениями — проверьте отчёт"
    ;;
  "failed"|"retry")
    echo "❌ Пайплайн завершился с ошибкой — проверьте отчёт"
    exit 1
    ;;
esac
```

### Рекомендации по настройке

- **Кеширование:** кешируйте `node_modules` (для `ajv-cli`) между запусками — `actions/cache@v4`.
- **Лимит контекста:** для больших PR (>100K токенов) разбейте на несколько вызовов, передавая по одной фиче за раз.
- **Ручной триггер:** добавьте `workflow_dispatch` для возможности ручного запуска пайплайна:
  ```yaml
  on:
    pull_request:
      ...
    workflow_dispatch:
      inputs:
        feature_path:
          description: 'Путь к аналитике фичи'
          required: true
  ```
- **Шаблон PR:** рекомендуйте авторам PR добавлять аналитику в `docs/analytics/feature-*.md` — это автоматически подхватится CI-пайплайном.

---

## Changelog самой инструкции

- **v0.6.0 (2026-07-07):** добавлен скилл `context-marker` (разметка сырых `.md`-файлов аналитики в XML-теги). Добавлены входные теги `<raw_content>`, `<content_type>`, `<file_path>`. Обновлён поток в Сценарии 1 (Оркестратор теперь диспетчеризует `context-marker` для SDD-проектов).
- **v0.5.0 (2026-07-03):** удалены documentation-pipeline скиллы (concept-analysis, docs-review, doc-fix). Оставлены 5 скиллов: tc-generator, tc-reviewer, tc-to-autotest, autotest-reviewer, orchestrate. Упрощены пайплайны до единственного test-pipeline. Удалены соответствующие входные/выходные теги из словарей.
- **v0.4.0 (2026-07-03):** унификация с `CONTRACTS.md` v1.0 (новый канон по результатам аудита). Удалён маркер `partial-ready` (заменён на `partial`). Обновлён словарь XML-тегов — все выходные блоки приведены к `CONTRACTS.md` §2. Упомянут `audit-report.md`. Унифицирована структура каталогов.
- **v0.3.0 (июль 2026):** переписана под актуальные контракты: добавлены `<automation_matrix>` (вход/выход), `<conflict_resolution>`, `<analysis_result>` / `<review_result>` / `<fix_result>`, статусы `partial` / `partial-ready` / `partial-fixed`, упоминание `CHANGELOG.md` в корне скиллов, раздел «Словарь XML-тегов», ссылка на `FALLBACK-СТРАТЕГИИ` в Оркестраторе.
- **v0.2.0:** расширена секция «Пайплайны» (5 шт.), добавлены `existing_project_context` и `max_iterations`.
- **v0.1.0:** первая версия.

---

*См. также: `CONTRACTS.md` (канон тегов и статусов), `PIPELINE.md` (схема пайплайна), `Оркестратор/SKILL.md` (Contract Check, FALLBACK-СТРАТЕГИИ).*