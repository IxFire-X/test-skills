# Руководство по эксплуатации (USER-GUIDE)

> **Как ИСПОЛЬЗОВАТЬ скиллы промптами, без чтения SKILL.md.** Настройка, словарь тегов и CI/CD — в `Instruction.md`. Канон тегов — `CONTRACTS.md`.

---

## 1. Система за 30 секунд

```
context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer
(разметка .md)   (ручные ТК)    (валидация ТК)  (автотесты)      (валидация автотестов)
```

- Каждый скилл — отдельным промптом. **Оркестратор** — вся цепочка одним промптом.
- **context-marker** — размечает сырой `.md` в XML (нужен, если аналитика без тегов).
- **Оркестратор Lite** (`SKILL-LITE.md`) — для моделей 7B–13B (<32K контекста).

## 2. Быстрый старт

**Полный пайплайн (одной фразой):**
```
Создай тест-кейсы и автотесты для <фича/сервис>
```
Оркестратор: Deep Scan → (если SDD) context-marker → Contract Check → 4 скилла → отчёт `docs/to_do/orchestration-report-<TS>.md`.

**Только ТК:** `Сгенерируй тест-кейсы для X` (+ `<analytics_documentation>` + `<source_code_and_diff>`).
**Только автотесты:** `Сгенерируй автотесты` (+ `<test_cases>`).

## 3. Каталог промптов

### 3.1 Оркестратор (полный пайплайн)

| Промпт | Результат |
|---|---|
| `Создай тест-кейсы и автотесты для X` / `Запусти тестовый пайплайн` / `Полный цикл тестирования` | test-pipeline целиком |
| `Инициализируй проект` / `Quickstart` | Deep Scan + создание `.skillsrc` + чек-лист стека |

Опциональные параметры:
```xml
<goal>Создай тест-кейсы и автотесты для X</goal>
<max_iterations>5</max_iterations>
<strict_mode>true</strict_mode>
```

### 3.2 Отдельные скиллы (без Оркестратора)

| Скилл | Промпт | Вход | Выход |
|---|---|---|---|
| context-marker | `Разметь контекст для пайплайна` | `<raw_content>` | `<analytics_documentation>` / `<source_code_and_diff>` / `<test_cases>` |
| tc-generator | `Сгенерируй тест-кейсы` | `<analytics_documentation>` + `<source_code_and_diff>` | `<generated_test_cases>` |
| tc-reviewer | `Проверь тест-кейсы` | `<generated_test_cases>` | `<validation_report>` + вердикт |
| tc-to-autotest | `Сгенерируй автотесты` | `<test_cases>` / `<corrected_test_cases>` (+ опц. `<automation_matrix>`) | файлы + `<automation_matrix>` |
| autotest-reviewer | `Проверь автотесты` | `<test_cases>` + `<automation_matrix>` + `<autotest_code>` | `<autotest_review>` + вердикт |

### 3.3 Итеративная регенерация (diff-режим)

ТК изменились → передайте в `tc-to-autotest` обновлённые `<test_cases>` + **старую** `<automation_matrix>`. Скилл сделает diff (keep/remove/add/rename) вместо переписывания с нуля, вернёт `<conflict_resolution>`.

## 4. Правило переименования тегов (критично)

На границе `tc-reviewer → tc-to-autotest`:

| Вердикт | Что передавать |
|---|---|
| `ПРИНЯТО` | `<generated_test_cases>` → **переименовать** в `<test_cases>` |
| `AUTO_FIX_APPLIED` | `<corrected_test_cases>` как есть |
| `ТРЕБУЕТ ДОРАБОТКИ` | `<corrected_test_cases>` как есть (после доработки) |

**Запрещено** передавать `<generated_test_cases>` напрямую в `tc-to-autotest` — ошибка контракта. Оркестратор делает это автоматически; вручную — вы сами.

## 5. Как читать результаты

**Вердикты ревьюеров:** `ПРИНЯТО` (продолжить) / `AUTO_FIX_APPLIED` (брать `<corrected_*>`) / `ТРЕБУЕТ ДОРАБОТКИ` (читать `<review_comments>`, вернуть в генератор).
**Статус оркестратора:** `completed` / `partial` / `failed` / `retry` (исчерпан max_iterations).

## 6. Обработка ошибок

| Симптом | Реакция |
|---|---|
| `status = failed` | Открыть отчёт, секция «Рекомендации» |
| `status = retry` | `Увеличь max_iterations до N и перезапусти` |
| Ревьюер `ТРЕБУЕТ ДОРАБОТКИ` (ТК) | Вернуть `<review_comments>` + `<analytics_documentation>` в tc-generator |
| Ревьюер `ТРЕБУЕТ ДОРАБОТКИ` (автотесты) | Вернуть `<review_comments>` + `<test_cases>` + `<automation_matrix>` в tc-to-autotest |
| Аналитика в сыром `.md` | `Разметь контекст для пайплайна` (context-marker) |

## 7. Какой файл загружать

| Скилл | Файл |
|---|---|
| context-marker | `Разметка контекста/SKILL.md` |
| tc-generator | `Ручные тест-кейсы/SKILL.md` |
| tc-reviewer | `Валидация тест-кейсов/SKILL.md` |
| tc-to-autotest | `Автоматизированные кейсы на основе тест-кейсов/SKILL.md` |
| autotest-reviewer | `Валидация автотестов/SKILL.md` |
| Оркестратор | `Оркестратор/SKILL.md` (полный) / `SKILL-LITE.md` (7B–13B) |

Живые примеры с реальными входными — в `examples.md` каждого скилла.

---

*См. также: `Instruction.md` (настройка + CI/CD), `CONTRACTS.md` (канон тегов), `PIPELINE.md` (схема), `BACKLOG.md` (план развития).*