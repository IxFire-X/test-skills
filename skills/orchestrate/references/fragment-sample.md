# Фрагмент генератора: структура и как собрать большой

Ответ `tc-generator` — один JSON-файл, **голый фрагмент** (без конверта): объект по `schemas/candidate-fragment.schema.json`; кейсы в нём
— по `schemas/canonical-test-document.schema.json`. Ниже — только форма. Значения в `<…>` берёшь из входов пачки (план, header, ответ
разметки); механику код ставит сам (список под образцом кейса). Пиши JSON без отступов и лишних пробелов; перенос строки — только между кейсами.

```json
{
  "schema_version": "1.0.0",
  "status": "COMPLETE",
  "batch_id": "<BATCH-… из плана>",
  "namespace": "<B1, B2, … из плана>",
  "owned_source_requirement_ids": ["<SREQ-… этой пачки>"],
  "requirements": [
    { "requirement_id": "CREQ-<ns>-001", "text": "<условие, выведенное из источника>", "provenance": ["<путь:строка-строка>"] }
  ],
  "source_to_canonical_mappings": [
    { "source_requirement_id": "<SREQ-…>", "canonical_requirement_ids": ["CREQ-<ns>-001"] }
  ],
  "operation_capabilities": [
    { "capability_id": "CAP-<ns>-<имя>", "action": "<имя операции>", "adapter": "<адаптер из входов>", "arguments": [],
      "results": [ { "name": "count", "semantic_type": { "kind": "json", "type": "integer" } } ], "provenance": ["<фраза источника>"] }
  ],
  "test_cases": [ "… кейсы, форма ниже …" ]
}
```

Кейс (поля человека — по-русски, технические токены — как в схеме):

```json
{
  "case_id": "TC-<ns>-001", "priority": "HIGH", "categories": ["positive", "functional"],
  "title": "<название>", "objective": "<цель>", "preconditions": ["<условие данных, не условие работы над тестами>"],
  "requirement_ids": ["CREQ-<ns>-001"],
  "management": { "folder": "<папка>", "components": [], "labels": [], "owner": null, "status": null, "estimated_time": null,
                  "custom_fields": {}, "external_keys": {}, "external_links": { "issues": [], "pages": [] } },
  "steps": [
    {
      "action": "<что сделать>", "test_data": "<данные>",
      "operation": { "kind": "project_action", "capability_id": "CAP-<ns>-<имя>" },
      "inputs": [ { "target": { "location": "arg", "name": "owner_id" }, "source": { "kind": "literal", "value": 7 } } ],
      "outputs": [ { "source": { "kind": "project_result", "name": "count" } } ],
      "expectations": [
        { "text": "<наблюдаемый результат>",
          "assertions": [ { "operator": "equals", "actual": { "kind": "project_result", "name": "count" }, "expected": { "kind": "literal", "value": 0 } } ] }
      ]
    }
  ]
}
```

Второй кейс — тот же каркас с другим `case_id` и своими `requirement_ids`; у каждого шага свой `operation.capability_id` из `operation_capabilities`.

**Код ставит сам (не пиши):** `display_order` (позиция, с 1); `step_id` = `STEP-<хвост case_id после TC->-<NN>` (`TC-B1-003`, шаг 2 → `STEP-B1-003-02`),
`input_id` = `INPUT-<хвост>-<NN>-<k>`, `expectation_id` = `EXP-…-<NN>-<k>`, `assertion_id` = `ASSERT-…-<NN>-<k>-<j>`; `output_id` = `name` источника;
`context_receipt`, digest-поля, `diagnostics`; `manual_only: false`, `manual_reason: null`, `automation_blockers: []`, `sensitive`; `semantic_type`, когда тип
выводится: у входа — тип литерала, выхода `step_output` или аргумента capability; у выхода — тип результата capability (`http_status` — integer, `http_header` — string).
Не выводится (`fixture`, `environment`, `secret_handle`, `http_body`) — пиши сам. Свой тип код не меняет: отличающийся от типа литерала отказывает проверка.
За тобой: `case_id`, `requirement_ids`, `capability_id`, тексты, значения, `output_id` у `http_body`/`http_status`; ссылка `step_output` — полным id шага
по этой схеме (`"step_id": "STEP-B1-003-02"`).

## Три проверки, которые слабеют первыми

Каждое ожидание — только то, что выражают операторы схемы (`equals`, `contains` — строка в строке, `greater_*`, `length_equals`, …) над
выходами шага. Слабая замена (`greater_than`, «число дат») ревьюер возвращает как BLOCKING. Если проверку нельзя выразить так, не подменяй
её слабой: оставь шаг `manual_only` с `manual_reason` или блокером (`automation_blockers`).

1. **«Ровно на N»** — capability сама считает разность и отдаёт её отдельным результатом `delta` (тип integer):

```jsonl
{"name": "delta", "semantic_type": {"kind": "json", "type": "integer"}}
{"operator": "equals", "actual": {"kind": "project_result", "name": "delta"}, "expected": {"kind": "literal", "value": 1}}
```

2. **Перенаправление** — шаг создания отдаёт `owner_id`, шаг отправки формы отдаёт `redirectId`; ожидание сверяет их, а не только «302»:

```jsonl
{"operator": "equals", "actual": {"kind": "project_result", "name": "redirectId"}, "expected": {"kind": "step_output", "step_id": "STEP-<хвост case_id>-01", "output_id": "owner_id"}}
```

3. **Ошибка поля** — результат `telephoneError` (string) собирает пару «поле:код»; ожидание называет обе части:

```jsonl
{"operator": "equals", "actual": {"kind": "project_result", "name": "telephoneError"}, "expected": {"kind": "literal", "value": "telephone:typeMismatch"}}
```

Типы обеих сторон `equals` совместимы (integer с integer, string со string); `contains` — только строка в строке.

## Три отказа, которые встречаются чаще всего

1. **Пачка 2 и дальше: capabilities прошлых пачек.** Шаг, который вызывает capability из пачки 1, требует её описания в `operation_capabilities` этой же пачки: код берёт тип результата только оттуда.
   Без копии отказ — ошибка схемы `semantic_type`. Скопируй capability из входа `prior_capabilities` слово в слово (другой байт — `BATCH_CAPABILITY_CONFLICT`): в `operation_capabilities` пачки 2 стоит
   `{"capability_id": "CAP-B1-count", "action": "<имя операции>", "adapter": "<адаптер из входов>", "arguments": [], "results": [{"name": "count", "semantic_type": {"kind": "json", "type": "integer"}}], "provenance": ["<фраза источника>"]}` — как в пачке 1.

2. **Ссылка на выход шага — только на шаг выше** (`SEMANTIC_FORWARD_STEP_OUTPUT`). Ожидание шага 2 может сослаться (в `expected`) на выход шага 1 — `{"kind": "step_output", "step_id": "STEP-B1-003-01", "output_id": "owner_id"}`; утверждение о выходе собственного шага — это `actual` вида `http_status`, `http_header`, `http_body` или `project_result`, не `step_output`.

3. **Группа вариантов `param_group`** (`SEMANTIC_PARAM_GROUP_MISMATCH`). Кейсы одной группы различаются только значениями литералов, `case_id`, названием, целью и текстами шагов; `requirement_ids`, `priority`, `categories`,
   предусловия, `operation`, операторы, число шагов и ссылки между ними — одни и те же.

Проекции входа: список `read` называет файлы `<run>/review-views/` (опись — `inventory-view-v1`, план пачек — `plan-view-v1` без текстов требований: они в ответе разметки, `requirements[].text` по `source_requirement_id`). Читай их, а не `inventories/` и `batch-plans/`.

## Как собрать фрагмент на десятки кейсов

1. Составь список кейсов (по требованиям пачки) и для каждого — шаги, ожидания, проверки; id шагов и проверок ставит код по схеме выше, `case_id` у тебя уникальны в пачке.
2. Ответ — файл данных: собирай его по частям, кейс за кейсом; скрипты, которые импортируют или вызывают код пакета, не пиши. Дописывай в один файл, JSON без отступов; исходник `tools` ради формата не читай — форма выше и схемы достаточны.
3. Положи файл в `answer_slot` стадии и проверь: `python -m tools.api submit --run-root <run> --stage tc-generator:<batch> --dry-run`;
   отказ называет, что исправить. Исправленный файл — единственный повтор стадии; после `submit` ответ не меняется.
4. Прими ответ тем же вызовом без `--dry-run`; последняя пачка сама проверяет сборку набора целиком, файлов кандидата не нужно.
