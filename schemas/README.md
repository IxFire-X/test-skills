# /schemas — JSON Schema для контрактной валидации

> **Назначение:** машиночитаемые схемы для автоматической валидации выходных данных скиллов. Каждая схема формализует структуру выходного JSON, описанного в соответствующем SKILL.md.
>
> **Стандарт:** JSON Schema Draft 2020-12
> **Дата создания:** 2026-07-03

---

## Состав

| Файл | Описывает выход скилла | Версия |
|---|---|---|
| `tc-generator-output.schema.json` | `tc-generator` → `<generated_test_cases_json>` | 1.0 |
| `tc-reviewer-output.schema.json` | `tc-reviewer` → `<validation_report_json>` | 1.0 |
| `tc-to-autotest-output.schema.json` | `tc-to-autotest` → `<automation_matrix_json>` | 1.0 |
| `autotest-reviewer-output.schema.json` | `autotest-reviewer` → `<autotest_review_json>` | 1.0 |
| `context-marker-output.schema.json` | `context-marker` → `<batch_marking_result>` (batch-режим) | 1.0 |
| `orchestrator-output.schema.json` | `orchestrate` → `<orchestration_result>` | 1.0 |
| `skillsrc.schema.json` | Манифест проекта `.skillsrc` | 1.0 |
| `run-tests-output.schema.json` | `tools/run_tests.py` → JSON-вердикт оракула исполнения (Опора 1) | 1.0 |

---

## Быстрый старт: валидация

### Способ 1: AJV CLI (рекомендуемый)

Установите `ajv-cli` глобально:

```bash
npm install -g ajv-cli
```

Проверьте выход скилла на соответствие схеме:

```bash
# Пример: валидация выхода tc-generator
npx ajv validate -s schemas/tc-generator-output.schema.json -d output.json

# Пример: валидация выхода tc-reviewer
npx ajv validate -s schemas/tc-reviewer-output.schema.json -d output.json

# Пример: валидация выхода tc-to-autotest
npx ajv validate -s schemas/tc-to-autotest-output.schema.json -d output.json

# Пример: валидация выхода autotest-reviewer
npx ajv validate -s schemas/autotest-reviewer-output.schema.json -d output.json
```

**Интерпретация результата:**
- `output.json valid` — PASS. Выход соответствует контракту, можно передавать следующему скиллу.
- Ошибки с указанием полей — FAIL. Выход не соответствует контракту. **НЕ передавать** следующему скиллу.

### Способ 2: Python (jsonschema)

```bash
pip install jsonschema
```

```python
import json
from jsonschema import validate, ValidationError

with open('schemas/tc-reviewer-output.schema.json') as f:
    schema = json.load(f)
with open('output.json') as f:
    data = json.load(f)

try:
    validate(instance=data, schema=schema)
    print("✅ PASS: output.json соответствует схеме")
except ValidationError as e:
    print(f"❌ FAIL: {e.message}")
```

### Способ 3: Интеграция в CI (скрипт validate.sh)

Создайте скрипт `schemas/validate.sh`:

```bash
#!/bin/bash
# Валидация всех JSON-выходов пайплайна по схемам
# Использование: ./schemas/validate.sh <output_dir>

set -e

SCHEMAS_DIR="$(dirname "$0")"
OUTPUT_DIR="${1:-outputs}"

validate_one() {
    local schema="$1"
    local data="$2"
    local name="$3"
    echo -n "  [$name] ... "
    if npx ajv validate -s "$schema" -d "$data" 2>/dev/null; then
        echo "✅ PASS"
        return 0
    else
        echo "❌ FAIL"
        return 1
    fi
}

echo "=== Schema Validation ==="
FAILURES=0

validate_one "$SCHEMAS_DIR/tc-generator-output.schema.json"   "$OUTPUT_DIR/generated_test_cases.json"   "tc-generator"    || ((FAILURES++))
validate_one "$SCHEMAS_DIR/tc-reviewer-output.schema.json"    "$OUTPUT_DIR/validation_report.json"      "tc-reviewer"     || ((FAILURES++))
validate_one "$SCHEMAS_DIR/tc-to-autotest-output.schema.json" "$OUTPUT_DIR/automation_matrix.json"      "tc-to-autotest"  || ((FAILURES++))
validate_one "$SCHEMAS_DIR/autotest-reviewer-output.schema.json" "$OUTPUT_DIR/autotest_review.json"    "autotest-reviewer" || ((FAILURES++))

echo "=== Result: $FAILURES failure(s) ==="
exit $FAILURES
```

---

## Приоритеты ошибок валидации

| Приоритет | Уровень | Действие Оркестратора |
|---|---|---|
| **P0** | Обязательные поля отсутствуют | БЛОКИРОВАТЬ передачу, вернуть ошибку |
| **P1** | Формат данных не соответствует (enum, type) | БЛОКИРОВАТЬ передачу, запросить перегенерацию |
| **P2** | Опциональные поля отсутствуют | WARN, продолжить (graceful degradation) |
| **P3** | Дополнительные поля (не из схемы) | IGNORE (forward compatibility) |

---

## Интеграция с Оркестратором

Оркестратор использует схему на каждом шаге пайплайна:

```
ПОСЛЕ выполнения скилла N, ПЕРЕД передачей выхода скиллу N+1:

1. ЗАГРУЗИТЬ schema.json из schemas/{skill_name}-output.schema.json
2. ПРОВЕРИТЬ выход JSON на соответствие JSON Schema:
   - ЕСЛИ PASS → передать выход скиллу N+1
   - ЕСЛИ FAIL → зафиксировать ошибки, вернуть скиллу N, НЕ передавать дальше
   3. ЗАФИКСИРОВАТЬ результат в отчёте выполнения (orchestration-report)
```

См. также: [`../shared/schema-validator.md`](../shared/schema-validator.md) — концептуальное описание контрактной валидации.

---

## 🚪 Жёсткий гейт Оракула исполнения (Опора 1)

> **Назначение:** `tools/run_tests.py` — единственный детерминированный источник правды о том, запустились ли автотесты. Его вердикт **не интерпретируется LLM**: `PASS` означает реальный запуск и успех. Это закрывает корневую причину провала InvenTree (0/25), где `autotest-reviewer` выдавал `AUTO_FIX_APPLIED` на код, который физически ни разу не запускался.

**Контракт выхода:** [`run-tests-output.schema.json`](run-tests-output.schema.json) — три вердикта:

| `verdict` | Что значит | Реакция Оркестратора |
|---|---|---|
| `PASS` | Тесты РЕАЛЬНО запустились и прошли | Разрешить `autotest-reviewer` выдать `ПРИНЯТО` |
| `FAIL` | Запустились, но есть падения; `failed_methods[]` + `root_cause[]` заполнены | `ТРЕБУЕТ ДОРАБОТКИ`; `root_cause[]` передать дорогой модели (Kimi K3) для диагностики причины |
| `NOT_RUNNABLE` | Окружение недоступно (нет интерпретатора/фреймворка/проекта) | Честный ответ «не могу проверить» — **НЕ ПРИНЯТО** и **НЕ фейковый PASS** |

**Жёсткое правило (BACKLOG, Опора 1, п.4):**
```
autotest-reviewer НЕ выдаёт ПРИНЯТО, пока run_tests.py не вернул PASS.
NOT_RUNNABLE — это ЧЕСТНЫЙ «не могу проверить», а не основание доверять коду.
```

**Использование:**
```bash
# из корня целевого проекта
python ../test-orchestration-skills/tools/run_tests.py --skillsrc .skillsrc
python ../test-orchestration-skills/tools/run_tests.py --project . --language python --pytest-target tests/test_part_api.py
```

**Exit codes для CI:** `0` — PASS или NOT_RUNNABLE (честный недоступ), `1` — FAIL (тесты упали), `2` — внутренняя ошибка раннера.

---

## Как добавить новую схему

1. Создать файл `schemas/{skill_name}-output.schema.json`
2. Использовать `"$schema": "https://json-schema.org/draft/2020-12/schema"`
3. Добавить `"$id"`, `"title"`, `"type": "object"`
4. Определить `"required"` и `"properties"`
5. Использовать `"enum"` для ограниченных наборов значений (например, вердикты)
6. Добавить файл в таблицу выше
7. Обновить `validate.sh` для включения новой проверки

---

*См. также: [`CONTRACTS.md`](../CONTRACTS.md) (текстовый канон тегов), [`../shared/schema-validator.md`](../shared/schema-validator.md) (концепция валидации).*