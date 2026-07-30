# ROADMAP — Пошаговый план для GLM

> **Единственный плановый документ.** Читай сверху вниз, выполняй по порядку.
> **Текущая позиция:** Шаг 4 (Опора 2 — scan_project.py)

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

## Шаг 4: Опора 2 — scan_project.py ❌ ТЕКУЩАЯ ЗАДАЧА

**Файл для создания:** `tools/scan_project.py`

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
- [ ] `tools/scan_project.py` создан и запускается
- [ ] `python tools/scan_project.py --project InvenTree-master --target part/api.py` → создаёт файл с `<source_code_and_diff>` + `<analytics_documentation>`
- [ ] `.skillsrc` в InvenTree-master обновлён (или создан) с `language: python`, `framework: django`

---

## Шаг 5: Точка доказательства ❌ ПОСЛЕ ШАГА 4

**Цель:** Полный прогон на ЛЮБОМ проекте от сканирования до вердикта. InvenTree — это тестовый полигон, но система должна работать с любым стеком.

### Вариант A: Java-проект (твой основной сценарий)
```bash
# 1. Сканирование Java-проекта (Spring Boot)
python tools/scan_project.py --project /path/to/your-java-project --target src/main/java/com/example/billing/TransferService.java

# 2. Запуск сгенерированных тестов (Maven)
python tools/run_tests.py --project /path/to/your-java-project --language java

# Ожидаемый stack-вывод scan_project.py:
# {"language": "java", "framework": "spring-boot", "test_framework": "junit5", "build_tool": "maven"}
```

### Вариант B: Python-проект (InvenTree — тестовый полигон)
```bash
# 1. Сканирование
python tools/scan_project.py --project InvenTree-master --target part/api.py --output InvenTree-master/docs/to_do/analytics-part-api.md

# 2. Запуск тестов (после генерации)
python tools/run_tests.py --project InvenTree-master --language python --pytest-target src/backend/InvenTree/part/test_generated_api.py
```

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

---

## Шаг 6+: Фаза 2 (заморожено до Шага 5)

После точки доказательства — реализовать по порядку:
1. `semantic-code-validator` — ловит галлюцинации полей ORM
2. `orm-fixture-builder` — изоляция данных для Django
3. `trace-map-enforcer` — обязательный trace_map

---

## Правила для GLM

1. **Не создавай новые LLM-скиллы** до Шага 5. Только детерминированные инструменты (Python-скрипты).
2. **Каждый инструмент** должен выводить JSON по контракту (как `run_tests.py`).
3. **Честность > удобство.** Если окружения нет — `NOT_RUNNABLE`, не фейковый `PASS`.
4. **Минимум зависимостей.** Только стандартная библиотека Python для tools.
