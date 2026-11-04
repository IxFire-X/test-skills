# Фрагмент генератора: структура и как собрать большой

Ответ `tc-generator` — один JSON-файл, **голый фрагмент** (без конверта): объект по `schemas/candidate-fragment.schema.json`; кейсы в нём
— по `schemas/canonical-test-document.schema.json`. Ниже — только форма. Значения в `<…>` берёшь из входов пачки (план, header, квитанция
контекста, ответ разметки); digest-поля (`digest`, а у кейсов и ответов — что назовёт `model-response`) не считай: пропущенные ставит
`model-response`, неверные он отказывает с верным значением.

```json
{
  "schema_version": "1.0.0",
  "status": "COMPLETE",
  "batch_id": "<BATCH-… из плана>",
  "namespace": "<B1, B2, … из плана>",
  "plan_digest": "<result.digest публикации плана>",
  "header_digest": "<result.digest публикации header>",
  "context_receipt_digest": "<digest квитанции контекста пачки>",
  "context_receipt": { "…": "квитанция контекста пачки ЦЕЛИКОМ — файл receipt как есть, а не её digest" },
  "owned_source_requirement_ids": ["<SREQ-… этой пачки>"],
  "requirements": [
    { "requirement_id": "CREQ-<ns>-001", "display_order": 1, "text": "<условие, выведенное из источника>", "provenance": ["<файл#Lстрока-Lстрока>"] }
  ],
  "source_to_canonical_mappings": [
    { "source_requirement_id": "<SREQ-…>", "canonical_requirement_ids": ["CREQ-<ns>-001"] }
  ],
  "operation_capabilities": [
    { "capability_id": "CAP-<ns>-<имя>", "action": "<имя операции>", "adapter": "<адаптер из входов>", "arguments": [],
      "results": [ { "name": "count", "semantic_type": { "kind": "json", "type": "integer" } } ], "provenance": ["<фраза источника>"] }
  ],
  "test_cases": [ "… кейсы, форма ниже …" ],
  "diagnostics": [],
  "digest": "<можно опустить: поставит model-response>"
}
```

Кейс (поля человека — по-русски, технические токены — как в схеме):

```json
{
  "case_id": "TC-<ns>-001", "display_order": 1, "priority": "HIGH", "categories": ["positive", "functional"],
  "title": "<название>", "objective": "<цель>", "preconditions": ["<условие данных, не условие работы над тестами>"],
  "requirement_ids": ["CREQ-<ns>-001"],
  "management": { "folder": "<папка>", "components": [], "labels": [], "owner": null, "status": null, "estimated_time": null,
                  "custom_fields": {}, "external_keys": {}, "external_links": { "issues": [], "pages": [] } },
  "steps": [
    {
      "step_id": "STEP-<ns>-001-01", "display_order": 1, "action": "<что сделать>", "test_data": "<данные>",
      "manual_only": false, "manual_reason": null, "automation_blockers": [],
      "operation": { "kind": "project_action", "capability_id": "CAP-<ns>-<имя>" },
      "inputs": [], "outputs": [ { "output_id": "count", "display_order": 1, "semantic_type": { "kind": "json", "type": "integer" },
                                   "source": { "kind": "project_result", "name": "count" } } ],
      "expectations": [
        { "expectation_id": "EXP-<ns>-001-01-1", "display_order": 1, "text": "<наблюдаемый результат>",
          "assertions": [ { "assertion_id": "ASSERT-<ns>-001-01-1-1", "display_order": 1, "operator": "equals",
                            "actual": { "kind": "project_result", "name": "count" }, "expected": { "kind": "literal", "value": 0 } } ] }
      ]
    }
  ]
}
```

Второй кейс — тот же каркас с другими `case_id` / `step_id` / `expectation_id` / `assertion_id` (счётчики внутри `<ns>`) и своими `requirement_ids`; у
каждого шага свой `operation.capability_id` из `operation_capabilities`.

## Как собрать фрагмент на десятки кейсов

1. Составь список кейсов (по требованиям пачки) и для каждого — шаги, ожидания, проверки; id нумеруй по шаблонам выше, они уникальны в пачке.
2. Файл ответа пиши по частям — кейс за кейсом (дописывай в один файл), а не одной строкой на сотни килобайт. Допустим собственный скрипт,
   который только собирает JSON из твоих данных и **не импортирует `tools`**; код пакета не вызывай и исходник `tools` ради формата не читай —
   форма выше и схемы достаточны.
3. Проверь файл: `python -m tools.api validate-fragment --fragment <f> --plan <f> --header <f> --context-receipt <f> --run-root <run>`;
   отказ называет, что исправить. Исправленный файл — единственный повтор стадии; после `model-response` ответ не меняется.
4. Передай ответ как есть: `python -m tools.api model-response --stage tc-generator:<batch> --artifact <файл> --run-root <run> --attempt <id> --out <run>/tmp/<имя>.json`;
   `result.path` — записанный ответ, он же `--prior-fragment` для следующей пачки.
