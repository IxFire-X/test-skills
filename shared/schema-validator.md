# /shared — Schema Validator (Контрактная валидация между скиллами)

> **Назначение:** единый механизм валидации выходных данных скиллов перед передачей следующему в пайплайне.
>
> **Версия:** 1.0

## Проблема (из аудита)

Аудит (`audit-report.md`, секция 3.1.B) выявил:
- **Нет валидации выходных данных скилла перед передачей следующему** — Оркестратор не проверяет, что выход скилла N соответствует контракту.
- **Контракты текстовые, не machine-readable** (CONTRACTS.md) — невозможна автоматическая валидация.

## Решение

Вводим систему JSON Schema для каждого выходного формата скиллов:

```
schemas/
├── tc-generator-output.schema.json    # Выход ручных тест-кейсов
├── tc-reviewer-output.schema.json     # Выход валидации тест-кейсов
├── tc-to-autotest-output.schema.json  # Выход генератора автотестов
└── autotest-reviewer-output.schema.json # Выход валидации автотестов
```

## КАК ЭТО РАБОТАЕТ

### Для Оркестратора (добавить в SKILL.md)

```
ПОСЛЕ выполнения скилла N, ПЕРЕД передачей выхода скиллу N+1:

1. ЗАГРУЗИТЬ schema.json из schemas/{skill_name}-output.schema.json
2. ПРОВЕРИТЬ выход скилла N на соответствие JSON Schema:
   - ЕСЛИ валидация PASS → передать выход скиллу N+1
   - ЕСЛИ валидация FAIL:
     a. ЗАФИКСИРОВАТЬ ошибки в pipeline-notes.md (секция validation.errors)
     b. ВЕРНУТЬ ошибку скиллу N с описанием несоответствий
     c. НЕ ПЕРЕДАВАТЬ некорректные данные следующему скиллу
3. ЗАФИКСИРОВАТЬ результат валидации в pipeline-notes.md (секция validation.{step})
```

### Для каждого скилла (добавить в SKILL.md)

Каждый скилл должен:
1. **Знать свою выходную схему** — указать путь к schema.json в своём SKILL.md.
2. **Валидировать свой выход** перед финальным выводом (self-check).
3. **Принимать schema.json как опциональный вход** для самовалидации.

## ПРИМЕР: ВАЛИДАЦИЯ ВЫХОДА tc-reviewer

```json
// schemas/tc-reviewer-output.schema.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "schemas/tc-reviewer-output.schema.json",
  "title": "TC Reviewer Output",
  "type": "object",
  "required": ["verdict", "test_cases_count", "review_date", "reviewer"],
  "properties": {
    "verdict": {
      "type": "string",
      "enum": ["ПРИНЯТО", "ТРЕБУЕТ ДОРАБОТКИ", "AUTO_FIX_APPLIED"]
    },
    "test_cases_count": { "type": "integer", "minimum": 1 },
    "review_date": { "type": "string", "format": "date" },
    "reviewer": { "type": "string" },
    "statistics": {
      "type": "object",
      "required": ["pass", "warn", "fail"],
      "properties": {
        "pass": { "type": "integer", "minimum": 0 },
        "warn": { "type": "integer", "minimum": 0 },
        "fail": { "type": "integer", "minimum": 0 }
      }
    },
    "categories": {
      "type": "object",
      "properties": {
        "structure": { "$ref": "#/$defs/category_result" },
        "limits": { "$ref": "#/$defs/category_result" },
        "determinism": { "$ref": "#/$defs/category_result" },
        "technical_accuracy": { "$ref": "#/$defs/category_result" },
        "coverage": { "$ref": "#/$defs/category_result" },
        "consistency": { "$ref": "#/$defs/category_result" }
      }
    }
  },
  "$defs": {
    "category_result": {
      "type": "object",
      "required": ["status"],
      "properties": {
        "status": { "type": "string", "enum": ["PASS", "WARN", "FAIL"] },
        "details": { "type": "string" }
      }
    }
  }
}
```

## ПРИОРИТЕТЫ ВАЛИДАЦИИ

| Приоритет | Уровень | Действие Оркестратора |
|-----------|---------|----------------------|
| **P0** | Обязательные поля отсутствуют | БЛОКИРОВАТЬ передачу, вернуть ошибку |
| **P1** | Формат данных не соответствует (enum, type) | БЛОКИРОВАТЬ передачу, запросить перегенерацию |
| **P2** | Опциональные поля отсутствуют | WARN, продолжить (graceful degradation) |
| **P3** | Дополнительные поля (не из схемы) | IGNORE (forward compatibility) |

## ИНТЕГРАЦИЯ С PIPELINE

```
Шаг пайплайна N:
  1. Выполнить скилл N
  2. Валидировать выход по schema N (Оркестратор)
  3. Сохранить валидированный выход в pipeline-notes.md
  4. Передать выход скиллу N+1
```

## КОНТРАКТ С ЭТАЛОННЫМИ ПРАКТИКАМИ

- **DeepSeek/Qwen (reference:5):** формальные JSON Schema для параметров — именно то, что рекомендовано.
- **Anthropic Progressive Disclosure (reference:1):** схема загружается только при необходимости валидации, не в каждом вызове.
- **OpenAI Routines (reference:4):** валидация между шагами рутины предотвращает каскадные ошибки.

---

*См. также: `CONTRACTS.md` (текстовые контракты), `schemas/*.json` (формальные схемы).*