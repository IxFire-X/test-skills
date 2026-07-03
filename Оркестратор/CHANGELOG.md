# CHANGELOG — Оркестратор

> **Назначение файла:** история версий скилла `Оркестратор`.
> Формат вдохновлён [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

---

## [1.3.0] — 2026-07-03

> Deep Scan (v1.3): полное сканирование проекта — код, документация, методология (SDD/TDD/BDD).

### Добавлено
- **Deep Scan** — первичное сканирование проекта перед инициализацией контекста:
  - КОД: build-файлы, конфигурации, точки входа, тесты, observability.
  - ДОКУМЕНТАЦИЯ: README, ADR, OpenAPI, AsyncAPI, Gherkin-спецификации.
  - МЕТОДОЛОГИЯ: автоопределение SDD / TDD / BDD по структуре проекта.
- **Слияние Deep Scan + `.skillsrc`** — 4 стратегии: `scan_wins`, `skillsrc_wins`, `ask_user`, `merge`.
- **`<project_context>` v1.2** — новый формат с `<methodology>`, `<observability>`, `<scan_metadata>`.
- **Шаг 0 Алгоритма** обновлён: сначала Deep Scan → затем слияние с `.skillsrc` → затем Zero-Config как fallback.

## [1.2.0] — 2026-07-03

> Zero-Config, Quickstart, `.skillsrc`, единый `<project_context>`.

### Добавлено
- **Zero-Config Mode** — автообнаружение стека (Java/Maven, TypeScript/npm, Python/pip, Go).
- **Quickstart-режим** — триггеры: `"инициализируй проект"`, `"quickstart"`, `"настрой проект"`.
- **Поддержка `.skillsrc`** — единый манифест проекта.
- **Единый `<project_context>`** — блок, передаваемый всем скиллам пайплайна.
- **Triggers** в frontmatter: `"инициализируй проект"`, `"quickstart"`, `"настрой проект"`.

## [1.1.0] — 2026-07-03

> Унификация контрактов с `CONTRACTS.md` v1.0 по итогам аудита (`audit-report.md`).

### Изменено
- **Таблица «Обязательные выходные теги по скиллам»** приведена к канону `CONTRACTS.md` §2:
  - `autotest-reviewer`: `<review>` → `<autotest_review>` (корневой блок)
  - `concept-analysis`: `<documentation>` + `<status>` → `<analysis_result>` (содержит `<status>`, `<concept_document>`, `<completeness_score>`)
  - `docs-review`: `<review>` + `<review_verdict>` + `<review_comments>` + `<correction_plan>` → `<review_result>` (содержит `<status>`, `<correction_plan_path>`, `<summary>`, `<verdict>`)
  - `doc-fix`: `<documentation>` (обновлённая) + `<status>` → `<fix_result>` (содержит `<status>`, `<document_path>`, `<report_path>`, `<summary>`, `<verification_status>`)
- **Таблица решений по статус-маркерам:** удалён маркер `partial-ready` (дубль `partial`); `partial` теперь используется единообразно для `concept-analysis` и `docs-review`.
- **Контрактное версионирование:** неисполнимый SemVer для тегов **заменён** на исполнимый **Contract Check** (чеклист по `CONTRACTS.md` §5).
- **Retry / timeout / idempotency:** помечены как **декларативная спецификация** (аудит, рекомендация №6). Реальное исполнение зависит от LLM-провайдера / хоста.

### Добавлено
- **Contract Check** (Шаг 2 алгоритма + секция «Контрактное версионирование»): проверка имён корневых блоков и статус-маркеров перед стартом пайплайна. Результат фиксируется в начале `orchestration-report-<TS>.md`.
- **Секция «Ожидаемые пары»** в «Контрактном версионировании» — таблица допустимых пар (skill_N → skill_N+1).
- **Версионирование самого Оркестратора** (отдельно от контрактов): правила major/minor для SKILL.md Оркестратора.
- Ссылка на `CONTRACTS.md` в шапке файла.
- Примеры в §"Примеры использования" дополнены явной нотацией `Contract Check: PASS`.

### Устарело
- **SemVer для тегов** (`MAJOR.MINOR` каждого тега) — заменён на Contract Check. У тегов нет реальных версий, проверка была неисполнимой.

---

## [1.0.0] — 2026-07-01

### Добавлено
- Базовая маршрутизация по `status`-маркерам между скиллами
- Связки пайплайнов: документация и тестирование
- README.md с верхнеуровневой архитектурой
- examples.md с базовыми сценариями

### Известные ограничения (исправлены в 1.1.0)
- Не описаны timeout / retry / fallback → ✅ добавлены в 1.1.0 (FALLBACK-СТРАТЕГИИ A–J)
- Формат передачи контекста между скиллами — неявный → ✅ унифицирован в 1.1.0 (CONTRACTS.md)
- SemVer для тегов неисполним → ✅ заменён на Contract Check

---

## Как использовать этот CHANGELOG

**Правила ведения:**
1. Новая версия — новая секция сверху.
2. Категории изменений: `Добавлено` / `Изменено` / `Устарело` / `Удалено` / `Исправлено` / `Безопасность`.
3. Версия в `CHANGELOG.md` должна совпадать с `version` в frontmatter `SKILL.md`.
4. Дата — ISO 8601 (`YYYY-MM-DD`).
