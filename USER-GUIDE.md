# Руководство по эксплуатации (USER-GUIDE)

> **Как ИСПОЛЬЗОВАТЬ скиллы промптами, без чтения SKILL.md.** Настройка, словарь тегов и CI/CD — в `Instruction.md`. Канон тегов — `CONTRACTS.md`.

---

## 1. Система за 30 секунд

```
context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer → Execution Gate
(разметка .md)   (ручные ТК)    (валидация ТК)  (автотесты)      (валидация автотестов)  (run_tests.py)
```

- Каждый скилл — отдельным промптом. **Оркестратор** — вся цепочка одним промптом.
- **context-marker** — размечает сырой `.md` в XML (нужен, если аналитика без тегов).
- **Execution Gate** (`tools/run_tests.py`) — детерминированный запуск автотестов; финальный `ПРИНЯТО` невозможен без `PASS`.
- **Trace audit** (`tools/trace_check.py --require-execution`) — терминальный аудит: pipeline может завершиться `PASS` только после точного сопоставления method-level evidence с trace document и orchestrator artifact.
- **Оркестратор Lite** (`SKILL-LITE.md`) — для моделей 7B–13B (<32K контекста).

## 2. Быстрый старт

### 2.1 Переносимый runtime: требования и bootstrap

Для детерминированного runtime нужен Python 3.10+ и зависимости из
`requirements-dev.txt` (в том числе `jsonschema`, `PyYAML` и `pytest`). Java
проверки дополнительно требуют JDK и Maven или Gradle; project-local wrapper
(`mvnw`/`mvnw.cmd`/`gradlew`) имеет приоритет над системным runner.

```sh
python -m pip install -r requirements-dev.txt
python tools/doctor.py --root .
```

`doctor.py` — первая проверка переносимой среды. Его JSON-статус `PASS`
означает, что Python runtime готов; `NOT_RUNNABLE` означает честно
недоступную зависимость или среду.

### 2.2 Семь CLI runtime

Все команды запускайте из корня пакета. Их stdout предназначен для JSON,
кроме `render_contract_docs.py --check`, который при чистых проекциях молчит.

```sh
python tools/doctor.py --root .
python tools/contract_check.py --root . --full
python tools/render_contract_docs.py --root . --check
python tools/scan_project.py --project /path/to/project --target src/api.py
python tools/run_tests.py --project /path/to/project --language python --automation-artifact tc-to-autotest-output.json
python tools/validate_artifact.py schemas/run-tests-output.schema.json artifact.json
python tools/trace_check.py trace-document.json --require-execution
```

Для `run_tests.py` exit code строго означает: `0` — `PASS`, `1` — `FAIL`,
`2` — `NOT_RUNNABLE` или внутренняя ошибка runner. При `--automation-artifact`
runner использует уже валидированный `tc-to-autotest` artifact, выбирает его
`generated_test_files` и выдаёт `RUN-*` + trace-compatible `execution_evidence` для
каждого `METHOD-*`; отсутствующая или неоднозначная привязка — `FAIL`, даже если
процесс тестового runner завершился с нулевым кодом. Ноль распознанных Python/Java
тестов также всегда `FAIL` (`no_tests_discovered`). Для валидаторов
`validate_artifact.py` и `trace_check.py`: `0` — валидно, `1` — невалидный
документ, `2` — ошибка аргументов, входа или схемы. `scan_project.py` отдаёт
`0` для `success`/`partial` и `1` для `error`; `doctor.py` отдаёт `0` для
`PASS` и `2` для `NOT_RUNNABLE`. Ненулевой код никогда нельзя преобразовывать
в успешный verdict в CI.

### 2.3 Артефакты и границы runtime

Постоянные диагностические и приёмочные артефакты runtime хранятся только в
`docs/to_do/`; `scan_project.py --output` также принимает только путь внутри
точного каталога `docs/to_do`. Не записывайте результаты проверки в исходные
файлы скиллов или в проект клиента.

Этот runtime принят только как детерминированное ядро: шесть скиллов,
адаптеры и сквозное поведение скиллов относятся к планам 2/3 и здесь не
заявляются завершёнными.

Материалы в локализованных каталогах и старые XML-теговые примеры — **legacy,
pre-migration (Plan 2)**. До их отдельной миграции authoritative являются
`contracts/pipeline.json`, JSON Schemas, deterministic CLI outputs и этот terminal
trace audit; legacy paths не задают runtime-контракт.

**Проверка переносимой среды:**
```
python tools/doctor.py --root .
```
Doctor выводит JSON: `PASS` означает, что обязательные Python-зависимости
доступны; `NOT_RUNNABLE` честно сообщает об их отсутствии. Поля
`languages.<name>.execution` описывают поддержку скилл-пака, а не наличие
инструментальной цепочки на текущем хосте.

**Полный пайплайн (одной фразой):**
```
Создай тест-кейсы и автотесты для <фича/сервис>
```
Оркестратор: Deep Scan → (если SDD) context-marker → Contract Check → 4 скилла → Execution Gate (`run_tests.py`) → **terminal Trace audit** (`trace_check.py --require-execution`) → отчёт `docs/to_do/orchestration-report-<TS>.md`.

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
**Execution Gate (`<run_tests_verdict>`):** `PASS` (тесты реально прошли, но ещё не финальный verdict) / `FAIL` (упали → `ТРЕБУЕТ ДОРАБОТКИ`, смотреть `root_cause[]`) / `NOT_RUNNABLE` (окружения нет → честный отказ, не фейковый `PASS`). Финальный `ПРИНЯТО` выдаётся только после terminal trace audit с полным method-level evidence.
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
