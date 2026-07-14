# CHANGELOG — Генератор автотестов (tc-to-autotest)

> **Назначение файла:** история версий скилла `tc-to-autotest`.
> Формат вдохновлён [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версионирование — [Semantic Versioning](https://semver.org/lang/ru/).

---

## [3.2] — 2026-07-03

> Gate Check — пре-валидация вердикта tc-reviewer перед генерацией. Блокировка генерации при `ТРЕБУЕТ ДОРАБОТКИ`.

### Добавлено
- **Gate Check:** секция «⛔ GATE CHECK: ПРЕ-ВАЛИДАЦИЯ ВЕРДИКТА tc-reviewer» — перед генерацией кода проверяется `VERDICT` из `<validation_report>`. При `ТРЕБУЕТ ДОРАБОТКИ` генерация блокируется, выводится `<review_comments>`.
- **Связь с shared-утилитами:** добавлены ссылки на `shared/stub-helper.md` (WireMock-мокирование) и `shared/trace-mapper.md` (traceability ТК-N → метод).

---

## [3.1] — 2026-07-03

> Мультиязычная поддержка: Python/pytest, Go/testing, TypeScript/Jest, Kotlin/JUnit 5.

### Добавлено
- **Мультиязычные шаблоны:** Python (pytest + httpx), Go (testing + testify), TypeScript (Jest + Supertest), Kotlin (JUnit 5 + RestAssured).
- **Автовыбор шаблона:** логика выбора языка из `project_context` или `.skillsrc`. Маппинг язык→стек.
- **JSON Schema (v3.0+):** поддержка `<output_format>json</output_format>` — вывод структурированного JSON по схеме `tc-to-autotest-output.schema.json`.

### Изменено
- **Fallback по умолчанию:** при отсутствии шаблона — Java/JUnit 5 (не ошибка, а предупреждение).
- **Унифицирована структура каталогов:** папка приведена к единому стандарту (SKILL.md, README.md, examples.md, CHANGELOG.md).

---

## [3.0] — 2026-07-02

> Идемпотентность: diff-генерация при повторном запуске с `<automation_matrix>`.

### Добавлено
- **Conflict Resolution:** при передаче `<automation_matrix>` на вход сравнивается с актуальным набором `ТК-N` — сохраняет существующие методы, удаляет устаревшие, генерирует новые, переименовывает при расщеплении.
- **`<conflict_resolution>`:** блок в `<automation_analysis>` с полями `removed`, `added`, `renamed`, `merged`.

### Изменено
- **Anti-patterns:** обновлён список запрещённых практик (добавлены: хардкод JSON в теле метода, пустой `catch`, псевдокод в критических блоках).

---

## [2.0] — 2026-06-28

> Первая стабильная версия с поддержкой Java/JUnit 5.

### Добавлено
- Генерация Java-автотестов (JUnit 5, RestAssured, AssertJ, WireMock, Allure).
- Парсинг Zephyr Markdown (`ТК-N` → Java-код).
- Группировка в `@ParameterizedTest` для валидационных ТК.
- Observability-паттерны (логи, метрики, аудит).
- Object Mother (Lombok Builder + `defaultRequest()`).
- Чеклист финальной проверки перед выводом.

---

*Связанные файлы: `SKILL.md` (инструкция скилла), `examples.md` (примеры), `shared/stub-helper.md` (WireMock), `shared/trace-mapper.md` (traceability).*