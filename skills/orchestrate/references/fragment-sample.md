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

## Входы `plan`

Три файла, которые агент собирает до `tools.api plan` (значения в `<…>` — из ответа разметки и вывода scan). `digest` у header код дописывает сам.

`--header` — заголовок канонического документа:

```json
{"schema_version": "1.1.0", "document_id": "TCDOC-<имя>", "revision": 1, "parent_sha256": null, "content_locale": "ru-RU",
 "metadata": {"subject": {"kind": "generic", "name": "<предмет>"}, "documentation": ["<документ требований>"], "project": "<проект>",
              "author": "controller", "date": "<ГГГГ-ММ-ДД>"}}
```

`--requirements` — **голый массив** — поле `artifacts.analytics_documentation.requirements` ответа разметки (не весь ответ, и поля `source_requirements` в нём нет);
`display_order` — 1..N подряд, `provenance` — `путь:строка` (так их читает проверка порядка):

```json
[{"source_requirement_id": "SREQ-0001", "display_order": 1, "text": "<текст>", "provenance": ["<путь:строка-строка>"], "digest": "sha256:<64 hex>"}]
```

`--grouping` — digest квитанций контекста; оба поля вместе, даже когда квитанция одна (`header_digest` код ставит сам):

```json
{"context_receipt_digest": "sha256:<квитанция с наименьшим batch_index>", "context_receipt_digests": ["sha256:<она же, первой>", "sha256:<остальные по batch_index>"]}
```

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

## Как собрать фрагмент на десятки кейсов

1. Составь список кейсов (по требованиям пачки) и для каждого — шаги, ожидания, проверки; id шагов и проверок ставит код по схеме выше, `case_id` у тебя уникальны в пачке.
2. Ответ — файл данных: собирай его по частям, кейс за кейсом; скрипты, которые импортируют или вызывают код пакета, не пиши. Дописывай в один файл, JSON без отступов; исходник `tools` ради формата не читай — форма выше и схемы достаточны.
3. Проверь файл: `python -m tools.api validate-fragment --fragment <f> --plan <f> --header <f> --context-receipt <f> --run-root <run>`;
   отказ называет, что исправить. Исправленный файл — единственный повтор стадии; после `model-response` ответ не меняется.
4. Передай ответ как есть: `python -m tools.api model-response --stage tc-generator:<batch> --artifact <файл> --run-root <run> --attempt <id>`;
   `result.path` — записанный ответ, он же `--prior-fragment` для следующей пачки.
