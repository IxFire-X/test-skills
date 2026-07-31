# ROADMAP — Пошаговый план для GLM

> **Единственный плановый документ.** Читай сверху вниз, выполняй по порядку.
> **Текущая позиция:** Шаг 5 завершён частично: Java PASS; InvenTree доведён до Execution Gate и заблокирован окружением.
> **Последняя проверка:** 2026-07-31. Полный Java pipeline подтверждён на JDK 24; Python/InvenTree pipeline дошёл до gate, но не стал выдавать ложный `PASS`.

---

## Шаг 0: Понять контекст (5 минут, уже сделано)

Проект: `test-orchestration-skills` — мультиагентная система для генерации тестов.
Цель: пользователь кладёт папку со скиллами в любой проект → скиллы сами его сканируют → выдают рабочие автотесты.

**Проблема, которую решаем:** раньше система выдавала `AUTO_FIX_APPLIED` на код, который физически не запускался (InvenTree 0/25). Теперь есть детерминированный `run_tests.py`, который честно говорит `NOT_RUNNABLE`.

---

## Шаг 1: Опора 1 — run_tests.py ✅ ГОТОВО

**Что сделано:**
- `tools/run_tests.py` — запускает pytest/maven, возвращает JSON-вердикт `PASS|FAIL|NOT_RUNNABLE`
- `schemas/run-tests-output.schema.json` — контракт выхода
- `Оркестратор/SKILL.md` строка 651 — Execution Gate: `autotest-reviewer` не выдаёт `ПРИНЯТО` без `PASS` от `run_tests.py`

**Проверка:** `python tools/run_tests.py --project . --language python` → должен вернуть JSON с `verdict`.

---

## Шаг 2: Опора 3 — Шаблоны ✅ ГОТОВО

**Что сделано:**
- Все 4 шаблона v2.0: `java-junit5.md`, `python-pytest.md`, `typescript-jest.md`, `go-testing.md`
- В каждом есть STEP 0 (проверка базовых фикстур) + traceability (ТК-N) + анти-паттерны

---

## Шаг 3: Опора 4 — Контрактная гигиена ✅ ГОТОВО

**Что сделано:**
- `trace_map` инлайнен в `schemas/tc-to-autotest-output.schema.json` (строки 149-173)
- Битые ссылки убраны, файл `nul` удалён

---

## Шаг 4: Опора 2 — scan_project.py ✅ ГОТОВО

**Файл:** `tools/scan_project.py`

**Что сделано:**
- `tools/scan_project.py` — детерминированный сканер проекта: определение стека по манифестам (pyproject.toml, pom.xml, package.json, go.mod), извлечение релевантного кода (target + models/serializers/urls/conftest), генерация `<source_code_and_diff>` + `<analytics_documentation>`, обновление/создание `.skillsrc`
- `schemas/scan-project-output.schema.json` — контракт выхода (status, stack, files_extracted, output_file, skillsrc_updated, warnings, errors)
- Проверка на InvenTree (`--target part/api.py`): `status: success`, стек `python/django/pytest`, извлечены `api.py`, `models.py`, `serializers.py`, создан файл аналитики, `.skillsrc` обновлён

**Проверка:** `python tools/scan_project.py --project InvenTree-master --target part/api.py --output docs/to_do/analytics-check.md` → JSON с `status: success` + файл с XML-блоками.

**Что должен делать (по порядку):**

### 4.1. Парсер аргументов
```python
# Пример вызова:
# python tools/scan_project.py --project /path/to/inventree --target part/api.py --output docs/to_do/analytics-part-api.md
import argparse
parser = argparse.ArgumentParser()
parser.add_argument("--project", required=True, help="Путь к корню проекта")
parser.add_argument("--target", required=True, help="Целевой модуль/файл (например, part/api.py)")
parser.add_argument("--output", help="Куда записать результат (по умолчанию stdout)")
```

### 4.2. Определение стека по манифестам
```python
# Логика:
# 1. Ищем pyproject.toml / requirements.txt → Python
# 2. Ищем pom.xml → Java/Maven
# 3. Ищем package.json → TypeScript/JavaScript
# 4. Ищем go.mod → Go
# 5. Для Python: ищем django в dependencies → framework=django
# 6. Для Python: ищем pytest в dependencies → test_framework=pytest

# Выход: dict {"language": "python", "framework": "django", "test_framework": "pytest", "build_tool": "pip"}
```

### 4.3. Извлечение релевантного кода
```python
# Для Python/Django:
# 1. Найти models.py в том же пакете, что и target
# 2. Найти serializers.py (если есть)
# 3. Найти urls.py (если есть)
# 4. Прочитать target-файл
# 5. Извлечь только те классы/функции, которые импортируются в target

# НЕ читать весь проект! Только:
# - target-файл
# - models.py того же модуля
# - serializers.py того же модуля
# - conftest.py (если есть) — для фикстур
```

### 4.4. Генерация `<source_code_and_diff>`
```xml
<source_code_and_diff>
  <file path="part/models.py">
    <!-- содержимое models.py -->
  </file>
  <file path="part/api.py">
    <!-- содержимое target-файла -->
  </file>
  <file path="part/serializers.py">
    <!-- содержимое serializers.py -->
  </file>
</source_code_and_diff>
```

### 4.5. Генерация заготовки `<analytics_documentation>`
```xml
<analytics_documentation>
  <module>part</module>
  <endpoints>
    <!-- Извлечь из urls.py или api.py: GET /api/part/, POST /api/part/, etc. -->
  </endpoints>
  <models>
    <!-- Извлечь из models.py: Part, PartCategory, BomItem -->
  </models>
  <stack>
    <language>python</language>
    <framework>django</framework>
    <test_framework>pytest</test_framework>
  </stack>
</analytics_documentation>
```

### 4.6. Обновление `.skillsrc`
```python
# Если .skillsrc существует в --project:
#   - Обновить project.language, project.framework, test.framework
# Если нет:
#   - Создать минимальный .skillsrc с определённым стеком
```

### 4.7. Выходной JSON (по аналогии с run_tests.py)
```json
{
  "status": "success",
  "stack": {"language": "python", "framework": "django", "test_framework": "pytest"},
  "files_extracted": ["part/models.py", "part/api.py", "part/serializers.py"],
  "output_file": "docs/to_do/analytics-part-api.md",
  "skillsrc_updated": true
}
```

**Критерии готовности Шага 4:**
- [x] `tools/scan_project.py` создан и запускается
- [x] `python tools/scan_project.py --project InvenTree-master --target part/api.py` → создаёт файл с `<source_code_and_diff>` + `<analytics_documentation>`
- [x] `.skillsrc` в InvenTree-master обновлён (или создан) с `language: python`, `framework: django`

---

## Шаг 5: Точка доказательства ⚠️ ЧАСТИЧНО ГОТОВО

**Цель:** Полный прогон на ЛЮБОМ проекте от сканирования до вердикта. InvenTree — это тестовый полигон, но система должна работать с любым стеком.

**Фактический результат проверки 2026-07-31:**
- ✅ **Java/Spring Boot — полный PASS.** `step5-java-demo`, target `StudentController`, run2 выполнен на JDK 24: analytics → 10 ТК → review `ПРИНЯТО` → 10 автотестов с traceability 100% → autotest-review `ПРИНЯТО` → `run_tests.py PASS`; Maven подтвердил `24/24`, `0` failures/errors.
- ℹ️ Отдельная повторная команда из текущей оболочки увидела JDK 8 и не является доказательным прогоном: проект был после этого успешно проверен в окружении с JDK 24, что зафиксировано в `run-report-student-controller.md` и `orchestration-report-student-controller.md`.
- ⚠️ **Python/Django/InvenTree — pipeline до Execution Gate.** run3: 28 ТК, `tc-reviewer ПРИНЯТО (warn 3)`, автотест создан, но запуск заблокирован окружением: `0` тестов и 74 collection errors. Это корректный `FAIL/BLOCKED`, а не ложный `PASS`.
- ✅ Снято замечание run2 по URL: актуальные URL сверены с `part/api.py`.
- ⏳ Шаг 5 считается **не полностью закрытым**, пока InvenTree не даст исполняемый `PASS` в поддерживаемом окружении.

### Вариант A: Java-проект (твой основной сценарий)
```bash
# 1. Сканирование Java-проекта (Spring Boot)
python tools/scan_project.py --project /path/to/your-java-project --target src/main/java/com/example/billing/TransferService.java

# 2. Запуск сгенерированных тестов (Maven)
python tools/run_tests.py --project /path/to/your-java-project --language java

# Ожидаемый stack-вывод scan_project.py:
# {"language": "java", "framework": "spring-boot", "test_framework": "junit5", "build_tool": "maven"}
```

**Подтверждено на `step5-java-demo` (run2):**
- артефакты: `step5-java-demo/docs/to_do/run2/`;
- 10 тест-кейсов ТК-01…ТК-10, ревью `ПРИНЯТО`;
- 10 сгенерированных методов, traceability ТК-01…ТК-10 — 100%;
- `mvn clean test`: 24/24 PASS на JDK 24, `run_tests.py`: `PASS`, exit code 0;
- для повторения результата требуется выбрать JDK 17+ (в сегодняшнем доказательном прогоне использован JDK 24);
- исправлен найденный дефект ТК-10: `@WebMvcTest` включил `HelloWorldController`.

### Вариант B: Python-проект (InvenTree — тестовый полигон)
```bash
# 1. Сканирование
python tools/scan_project.py --project InvenTree-master --target part/api.py --output InvenTree-master/docs/to_do/analytics-part-api.md

# 2. Запуск тестов (после генерации)
python tools/run_tests.py --project InvenTree-master --language python --pytest-target src/backend/InvenTree/part/test_generated_api.py
```

**Результат run3 (2026-07-31):**
- аналитика, генерация ТК, review и генерация автотестов завершены;
- создан `InvenTree-master/src/backend/InvenTree/part/test_generated_run3_api.py`;
- 28 ТК доведены до Execution Gate;
- запуск заблокирован: проект требует Python 3.12+, а также GTK3 runtime для WeasyPrint; в текущем окружении получены 74 collection errors и 0 запущенных тестов;
- следующий прогон выполнять после устранения окружения: `pytest src/backend/InvenTree/part/test_generated_run3_api.py -v --tb=short -rA`.

### Вариант C: TypeScript-проект
```bash
python tools/scan_project.py --project /path/to/ts-project --target src/controllers/user.controller.ts
python tools/run_tests.py --project /path/to/ts-project --language typescript
```

### Вариант D: Go-проект
```bash
python tools/scan_project.py --project /path/to/go-project --target internal/handler/user.go
python tools/run_tests.py --project /path/to/go-project --language go
```

**Ожидаемый результат (для любого варианта):**
- `scan_project.py` → создал аналитику + обновил `.skillsrc` под стек проекта
- **Пайплайн сгенерировал артефакты:**
  - `tc-generator` → **25 ручных тест-кейсов** в формате Zephyr Markdown (`docs/to_do/test-cases-<module>.md`)
  - `tc-reviewer` → провалидировал ТК, вердикт `ПРИНЯТО` / `AUTO_FIX_APPLIED` (`docs/to_do/test-cases-review-<module>.md`)
  - `tc-to-autotest` → **автотесты по шаблону под стек** (java-junit5.md / python-pytest.md / typescript-jest.md / go-testing.md) + `<automation_matrix>` (traceability ТК-N → метод)
  - `autotest-reviewer` → проверил автотесты, вердикт + отчёт (`docs/to_do/autotest-review-<module>.md`)
- `run_tests.py` → `PASS` (не `NOT_RUNNABLE`) через нативный раннер стека (mvn test / pytest / npm test / go test)
- `autotest-reviewer` → `ПРИНЯТО` (после `PASS`)
- **Итоговые файлы:** тест-кейсы (Zephyr Markdown) + автотесты (код) + отчёты ревью + отчёт оркестратора

**Важно:** Шаблон выбирается автоматически по `project.language` из `.skillsrc`. Мультиязычность уже встроена в шаблоны (Опора 3) и run_tests.py (Опора 1) — scan_project.py должен лишь корректно определить стек.

### Артефакты доказательства

- Java: `step5-java-demo/docs/to_do/run2/orchestration-report-student-controller.md` и `run-report-student-controller.md` — полный `PASS`.
- InvenTree: `InvenTree-master/docs/to_do/run3/orchestration-report-part-api.md` — `BLOCKED (окружение)`, без подмены результата.
- Временные диагностические логи (`diag*.txt`, `*probe*`, `pytest_run3_out.txt`) не являются артефактами roadmap и не должны коммититься.

---

## Шаг 6+: Фаза 2 (частично разблокирована; InvenTree PASS остаётся отдельным долгом)

После подтверждённого Java-прогона можно планировать реализацию по порядку, но до завершения InvenTree-прогона не считать общую точку доказательства полностью закрытой:
1. `semantic-code-validator` — ловит галлюцинации полей ORM
2. `orm-fixture-builder` — изоляция данных для Django
3. `trace-map-enforcer` — обязательный trace_map

---

## Правила для GLM

1. **Не создавай новые LLM-скиллы** до Шага 5. Только детерминированные инструменты (Python-скрипты).
2. **Каждый инструмент** должен выводить JSON по контракту (как `run_tests.py`).
3. **Честность > удобство.** Если окружения нет — `NOT_RUNNABLE`, не фейковый `PASS`.
4. **Минимум зависимостей.** Только стандартная библиотека Python для tools.
