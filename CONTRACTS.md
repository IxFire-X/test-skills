# Контракты передачи данных (Единый канон)

> **Назначение:** этот документ — **единственный источник истины** для XML-тегов и статус-маркеров в мультиагентной системе «Скиллы для работы».
> Все SKILL.md, PIPELINE.md, Instruction.md и Оркестратор **обязаны** ссылаться на этот реестр и **не должны** объявлять собственные альтернативные имена тегов.
>
> **Версия канона:** 1.0
> **Дата введения:** 2026-07-03
> **Основание:** результаты аудита (`audit-report.md`, 2026-07-03), раздел 2.2 «Рассинхрон контрактов» — рекомендация №1 (КРИТИЧНО) и №2 (КРИТИЧНО).
> **Машиночитаемая схема:** [`contracts-schema.json`](contracts-schema.json) — JSON Schema (Draft 2020-12), формализующая все контракты из §2–§3 для автоматической валидации.

---

## 1. Принципы именования

1. **Каждый скилл выдаёт ОДИН именованный XML-блок-результат** (например, `<analysis_result>`, `<review_result>`). Имя блока фиксировано и не переименовывается между этапами.
2. **Внутри блока** — обязательные и опциональные дочерние теги. Структура блока — часть контракта.
3. **Статус-маркеры** — отдельный канон (§3), скиллы обязаны использовать только их.
4. **Запрещено:**
   - Переименовывать корневой блок (`<fix_result>` → `<documentation>` и т.п.);
   - Вводить параллельные «плоские» версии (`<documentation>` как альтернатива `<concept_document>`);
   - Использовать строковые статусы, не входящие в реестр.

---

## 2. Реестр выходных тегов (канон)

> **Каждый скилл выдаёт ОДИН корневой блок.** В таблице ниже `→` означает «передаётся на вход следующего скилла как есть».

| # | Корневой блок | Внутри (обязательные поля) | Производитель | Потребитель |
|---|---|---|---|---|
| 1 | `<generated_test_cases>` | Zephyr Markdown в CDATA | `tc-generator` | `tc-reviewer` |
| 2 | `<validation_report>` | `VERDICT`, проверки, AUTO_FIX-список, WARN | `tc-reviewer` | Оркестратор / лог |
| 3 | `<corrected_test_cases>` | Zephyr Markdown в CDATA | `tc-reviewer` (только при `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ`) | `tc-to-autotest` |
| 4 | `<automation_analysis>` | блок `1.–5.` + `<conflict_resolution>` (опц.) | `tc-to-autotest` | `autotest-reviewer` / лог |
| 5 | `<automation_matrix>` | `ТК-N -> javaMethod(...)` | `tc-to-autotest` | `autotest-reviewer` + следующий запуск `tc-to-autotest` |
| 6 | `<autotest_review>` | блоки 1–5 (Traceability / Анти-паттерны / TODO / WireMock / Стек) | `autotest-reviewer` | Оркестратор / лог |
| 7 | `<review_verdict>` | `ПРИНЯТО` / `AUTO_FIX_APPLIED` / `ТРЕБУЕТ ДОРАБОТКИ` | `tc-reviewer`, `autotest-reviewer` | Оркестратор |
| 8 | `<review_comments>` | список дефектов по severity | `tc-reviewer`, `autotest-reviewer` | Оркестратор / пользователь |
| 9 | `<corrected_autotest_code>` | исправленный Java-код (только при `AUTO_FIX_APPLIED`) | `autotest-reviewer` | CI/CD |
| 10 | `<orchestration_result>` | `<status>`, `<pipeline_name>`, `<iterations>`, `<steps>`, `<final_result>`, `<warnings>` | `orchestrate` | пользователь |

### 2.1 Внутренние теги внутри блоков

| Тег | Содержимое | Где встречается |
|---|---|---|
| `<conflict_resolution>` | блок `removed/added/renamed/merged` | `<automation_analysis>` |
| `<warnings>` | список строк | `<orchestration_result>` |
| `<summary>` | вложенный блок с подсчётом severity/counts | `<validation_report>`, `<autotest_review>` |
| `<verdict>` | человекочитаемое резюме | `<validation_report>`, `<autotest_review>` |

### 2.3 Правило переименования: `<generated_test_cases>` → `<test_cases>`

> **Критически важное правило трансформации тегов между этапами пайплайна.** Это не техническое переименование, а семантический переход права собственности.

| Этап | Тег на выходе | Тег на входе следующего | Условие перехода |
|---|---|---|---|
| `tc-generator` → `tc-reviewer` | `<generated_test_cases>` | `<generated_test_cases>` | Прямая передача (без переименования) |
| `tc-reviewer` → `tc-to-autotest` | `<corrected_test_cases>` | `<corrected_test_cases>` | При вердикте `AUTO_FIX_APPLIED` или `ТРЕБУЕТ ДОРАБОТКИ` |
| `tc-reviewer` → `tc-to-autotest` | `<generated_test_cases>` (исходный) | `<test_cases>` | При вердикте `ПРИНЯТО` — исходный `<generated_test_cases>` **переименовывается** в `<test_cases>` |

**Правило:**
1. Тег `<generated_test_cases>` используется **только** на границе `tc-generator → tc-reviewer`.
2. После получения вердикта `ПРИНЯТО` от `tc-reviewer`, Оркестратор (или пользователь при ручном запуске) **обязан** переименовать `<generated_test_cases>` в `<test_cases>` перед передачей в `tc-to-autotest`.
3. Если вердикт — `AUTO_FIX_APPLIED` или `ТРЕБУЕТ ДОРАБОТКИ`, приоритет имеет `<corrected_test_cases>` (передаётся как есть, без переименования).
4. **Запрещено** передавать `<generated_test_cases>` напрямую в `tc-to-autotest` — это вызовет ошибку контракта (скилл ожидает `<test_cases>` или `<corrected_test_cases>`).

**Почему это важно:** при ручном запуске скиллов пользователь может случайно передать `<generated_test_cases>` напрямую в `tc-to-autotest`, что нарушит контракт. Оркестратор автоматизирует это переименование, но ручной пользователь должен знать правило.

### 2.2 Входные теги (не меняются)

Входные теги остаются как в PIPELINE.md:

- `<analytics_documentation>`
- `<source_code_and_diff>`
- `<test_cases>`, `<corrected_test_cases>`, `<generated_test_cases>` (на вход — кто принимает)
- `<automation_matrix>` (на вход — для diff'а)
- `<existing_project_context>`
- `<concept_name>`, `<source_code>`
- `<doc_path>`, `<document_path>`, `<correction_plan>`, `<correction_plan_path>`
- `<autotest_code>`, `<autocode>`
- `<goal>`, `<pipeline>`, `<max_iterations>`, `<strict_mode>`, `<context>`
- `<raw_content>` — сырой текст/содержимое `.md`-файла без XML-разметки (вход скилла `context-marker`)
- `<content_type>` — тип контента: `analytics`, `source_code`, `test_cases`, `requirements`, `concept`, `auto`, `batch` (вход скилла `context-marker`)
- `<file_path>` — путь к файлу или директории для batch-режима (вход скилла `context-marker`, опционально)

---

## 3. Реестр статус-маркеров (канон)

> **Единый реестр строковых значений `<status>` и `VERDICT`.**
> Quality-gate Оркестратора, ревьюеры и скиллы **обязаны** использовать только эти маркеры. Любой другой статус → `unknown_status` (Fallback B).

### 3.1 Документационный пайплайн (@deprecated — удалён, оставлен для справки)

> ⚠️ **Этот раздел @deprecated.** Документационный пайплайн (`concept-analysis`, `docs-review`, `doc-fix`) удалён из проекта. Статус-маркеры ниже больше не используются. Сохранены только для обратной совместимости при чтении старых отчётов.

| Маркер | Источник | Означает | Примечание |
|---|---|---|---|
| `production-ready` | `concept-analysis`, `docs-review` (верификация) | Документ соответствует качеству production | @deprecated |
| `partial` | `concept-analysis`, `docs-review` | Частично готов (есть некритичные пробелы) | @deprecated |
| `not-ready` | `docs-review` | Есть критические/высокие дефекты | @deprecated |
| `fixed` | `doc-fix` | Все дефекты устранены | @deprecated |
| `partial-fixed` | `doc-fix` | Часть дефектов устранена | @deprecated |
| `failed` | любой | Критическая ошибка выполнения | @deprecated |
### 3.2 Тестовый пайплайн (вердикты ревьюеров)

| Маркер | Источник | Означает | Реакция оркестратора |
|---|---|---|---|
| `ПРИНЯТО` | `tc-reviewer`, `autotest-reviewer` | Все проверки пройдены | **Продолжить** к следующему шагу |
| `AUTO_FIX_APPLIED` | `tc-reviewer`, `autotest-reviewer` | Дефекты исправлены автофиксом | **Использовать** `<corrected_*>` вместо исходного |
| `ТРЕБУЕТ ДОРАБОТКИ` | `tc-reviewer`, `autotest-reviewer` | Есть FAIL без автофикса | **Приостановить**; вывести `<review_comments>`; повторный запуск — только после подтверждения пользователя |

### 3.3 Общий статус пайплайна (Оркестратор)

| Маркер | Означает |
|---|---|
| `completed` | Пайплайн выполнен полностью; финальный артефакт соответствует `production-ready` / `ПРИНЯТО` |
| `partial` | Пайплайн выполнен, но финальный артефакт в `partial` / `partial-fixed` / `AUTO_FIX_APPLIED` |
| `failed` | Критическая ошибка; пайплайн остановлен |
| `retry` | Достигнут `max_iterations`; требуется вмешательство пользователя |

---

## 4. Граф передачи контрактов

=== Тестовый пайплайн ===

┌─────────────────────┐
│   tc-generator      │ ──<generated_test_cases>──▶
└─────────────────────┘                            │
                                                   ▼
┌─────────────────────┐                  ┌──────────────────┐
│    tc-reviewer      │ ◀────────────────│ <generated_tc>   │
└─────────────────────┘                  └──────────────────┘
        │                                       
        ├── VERDICT=ПРИНЯТО          → <generated_tc> как <test_cases> ─▶
        ├── VERDICT=AUTO_FIX_APPLIED → <corrected_test_cases> ──────────▶│
        └── VERDICT=ТРЕБУЕТ ДОРАБОТКИ→ <corrected_test_cases>+review_comments (стоп)        
                                                                                │
                                                                                ▼
┌─────────────────────┐                              ┌──────────────────────────┐
│   tc-to-autotest    │ ◀─────────────────────────────│ <test_cases>/<corrected> │
└─────────────────────┘                              └──────────────────────────┘
        │                                       
        ├── <automation_analysis> + <automation_matrix> + Java-файлы ─▶
                                                                                │
                                                                                ▼
┌─────────────────────┐                              ┌──────────────────────────┐
│  autotest-reviewer  │ ◀─────────────────────────────│ <automation_matrix>+<autotest_code> │
└─────────────────────┘                              └──────────────────────────┘
        │
        ├── VERDICT=ПРИНЯТО          → END
        ├── VERDICT=AUTO_FIX_APPLIED → <corrected_autotest_code> → END
        └── VERDICT=ТРЕБУЕТ ДОРАБОТКИ→ <review_comments> (стоп)
```

---

## 5. Правила SemVer для контрактов (заменено на исполнимый чеклист)

> SemVer для тегов **заменён** на **исполнимый чеклист** (см. также `Оркестратор/SKILL.md` § «Контрактное версионирование»).
> Причина: у тегов нет реальных версий → SemVer-проверка была декларативной и неисполнимой (аудит, рекомендация №3, ВЫСОКИЙ приоритет).

**Перед стартом пайплайна Оркестратор обязан выполнить:**

```text
ДЛЯ КАЖДОЙ пары (skill_N → skill_N+1):
  1. По таблице §2 найти ожидаемый корневой блок
  2. По SKILL.md skill_N+1 найти «Контракт входа» — проверить, что имя блока совпадает
  3. По SKILL.md skill_N+1 найти «Контракт выхода» skill_N+1 — проверить, что выходной блок skill_N+1 снова есть в §2
  4. По §3 найти все статус-маркеры, которые может вернуть skill_N — проверить, что они в реестре
  5. ЕСЛИ любая проверка не прошла:
       → status = failed
       → error = "contract_mismatch: skill=<N> → <N+1>: missing <tag> | unknown status <mark>"
       → БЛОКИРОВАТЬ пайплайн
```

**Чеклист фиксируется в начале `docs/to_do/orchestration-report-<TS>.md`** отдельной секцией «Contract Check».

---

## 6. Правила retry / timeout / idempotency (пометка о спецификации)

> Эти механизмы **декларативны** в Markdown и **не исполняются кодом** (аудит, рекомендация №6, НИЗКИЙ приоритет).
> Исполнение зависит от LLM-провайдера / хоста скиллов.

- **`retry` (Fallback A, G, H, I):** рекомендация LLM перезапустить шаг; 1–2 попытки.
- **`step_timeout`:** рекомендация хосту ограничить время шага; по умолчанию не задан.
- **`sha256`-идемпотентность:** `source_hash` ключевого тега пишется в `step_results[i].input_hash`; повторный запуск **должен** давать идентичный результат.
- **Graceful stop (Fallback F):** сохранение `partial_results` в `docs/to_do/orchestration-report-partial-<TS>.md`.

Эти правила являются **спецификацией поведения**, а не гарантией исполнения.

---

## 7. Совместимость с предыдущими версиями

- **До v1.0 канона** использовались параллельные имена: `<documentation>` (выход `concept-analysis`/`doc-fix`), `<review>` (выход `autotest-reviewer`), `<fix_result>` (выход `doc-fix`). Все три скилла документационного пайплайна удалены, их теги больше не актуальны.
- **Сейчас** все SKILL.md, PIPELINE.md и Оркестратор должны использовать **только** имена из §2.
- **Миграция:** Оркестратор при старте проверяет, что выход очередного скилла соответствует ожидаемому корневому блоку. Неизвестные теги (включая устаревшие `<documentation>`, `<review>`, `<fix_result>`) считаются `contract_mismatch` и блокируют пайплайн.

---

*См. также: `PIPELINE.md` (содержит ссылки на этот канон), `Оркестратор/SKILL.md` (quality-gate и contract check), `Instruction.md` (словарь XML-тегов).*
