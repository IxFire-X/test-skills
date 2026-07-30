# Инструкция по настройке (Instruction)

> **Как НАСТРОИТЬ скиллы в проекте + словарь тегов + CI/CD.** Как использовать (промпты, сценарии) — в `USER-GUIDE.md`. Канон тегов и статусов — `CONTRACTS.md` (единственный источник истины). План развития — `BACKLOG.md`.

---

## 1. Что это за скиллы

Набор из **6 AI-скиллов**: 4 тестовых (tc-generator, tc-reviewer, tc-to-autotest, autotest-reviewer) + context-marker (разметка контекста) + orchestrate (Оркестратор). Работают как **1 пайплайн**:

```
context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer
```

Каждый скилл автономен, но максимум пользы — через `orchestrate`.

## 2. Предварительные требования

- AI-ассистент с загрузкой файлов и context window ≥ 64K токенов (для больших фич).
- Входные данные (чем полнее, тем точнее):

| Данные | Обязательность |
|---|---|
| Аналитика (описание фичи, AC, контракты API) | Желательно |
| Исходный код / diff (контроллеры, DTO, миграции) | Желательно |
| `.skillsrc` (манифест проекта) | Опционально (Оркестратор создаст через Quickstart) |

Без данных скиллы перейдут в ручной режим с уточняющими вопросами.

## 3. Настройка

### Шаг 1. Структура папок

```
test-orchestration-skills/
├── Instruction.md, USER-GUIDE.md, PIPELINE.md, CONTRACTS.md, BACKLOG.md, .skillsrc
├── Ручные тест-кейсы/            (tc-generator: SKILL.md, examples.md, README.md)
├── Валидация тест-кейсов/        (tc-reviewer)
├── Автоматизированные кейсы.../  (tc-to-autotest + templates/)
├── Валидация автотестов/         (autotest-reviewer)
├── Разметка контекста/           (context-marker)
├── Оркестратор/                  (SKILL.md + SKILL-LITE.md)
├── schemas/                      (JSON Schema выходов)
└── shared/                       (общие утилиты)
```

### Шаг 2. Загрузите `SKILL.md` в контекст AI

- **Claude (Web):** скрепка → загрузить `SKILL.md`.
- **Claude (API):** передать в поле `system`.
- **ChatGPT:** Custom Instructions / начало чата.
- **Cursor / Windsurf / Cline:** положить `SKILL.md` в корень проекта.

### Шаг 3. Манифест `.skillsrc`

Создайте в корне проекта (образец — `./.skillsrc`), или скажите `Инициализируй проект` — Оркестратор заполнит автоматически. Минимум: `project.name`, `project.language`, `paths.source`, `paths.tests`, `test.framework`, `methodology`.

## 4. Словарь XML-тегов (краткая справка)

> Полный канон — `CONTRACTS.md` §2 (выходные), §3 (статусы). Здесь — только самое нужное для понимания входа/выхода.

**Основные входные:** `<analytics_documentation>`, `<source_code_and_diff>`, `<test_cases>`, `<corrected_test_cases>`, `<generated_test_cases>`, `<automation_matrix>`, `<autotest_code>`, `<raw_content>`, `<goal>`.
**Основные выходные:** `<generated_test_cases>`, `<validation_report>`, `<corrected_test_cases>`, `<automation_matrix>`, `<automation_analysis>`, `<autotest_review>`, `<review_verdict>`, `<orchestration_result>`.

**Статус-маркеры:** `ПРИНЯТО` / `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ` (ревьюеры); `completed` / `partial` / `failed` / `retry` (оркестратор).

**Правило переименования:** после `ПРИНЯТО` — `<generated_test_cases>` переименовывается в `<test_cases>` перед передачей в `tc-to-autotest` (см. `CONTRACTS.md` §2.3).

## 5. Интеграция в CI/CD (GitHub Actions)

```yaml
name: Skills — Test Pipeline
on:
  pull_request:
    paths: ['src/**', 'docs/analytics/**']
jobs:
  test-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - name: Prepare Context
        run: |
          find docs/analytics -name '*.md' -exec cat {} + > /tmp/analytics.md
          git diff origin/${{ github.base_ref }}...HEAD -- src/ > /tmp/diff.patch
      - name: Run Test Pipeline
        env: { AI_API_KEY: ${{ secrets.AI_API_KEY }} }
        run: |
          {{YOUR_AI_CLI}} run \
            --skill-file "Оркестратор/SKILL.md" \
            --input-tag analytics_documentation "$(cat /tmp/analytics.md)" \
            --input-tag source_code_and_diff "$(cat /tmp/diff.patch)" \
            --pipeline test-pipeline \
            --output-dir "outputs/${{ github.run_id }}"
      - name: Upload Report
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: orchestration-report-${{ github.run_id }}
          path: outputs/${{ github.run_id }}/
      - name: Validate Schemas
        if: always()
        run: |
          npm install -g ajv-cli
          npx ajv validate -s schemas/tc-generator-output.schema.json -d outputs/${{ github.run_id }}/generated_test_cases.json || true
          npx ajv validate -s schemas/autotest-reviewer-output.schema.json -d outputs/${{ github.run_id }}/autotest_review.json || true
```

**Источники тегов в CI:** `<analytics_documentation>` — `find docs/analytics -name '*.md'`; `<source_code_and_diff>` — `git diff origin/main...HEAD -- src/`; `<automation_matrix>` — артефакт предыдущего запуска.

**Проверка статуса:**
```bash
STATUS=$(grep -oP '<status>\K[^<]+' outputs/<run_id>/orchestration-report-*.md)
[ "$STATUS" = "completed" ] && echo "OK" || { echo "FAIL"; exit 1; }
```

---

*См. также: `USER-GUIDE.md` (как использовать), `CONTRACTS.md` (канон тегов), `PIPELINE.md` (схема), `BACKLOG.md` (план развития).*