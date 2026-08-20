---
name: orchestrate
description: Использовать, когда запрос содержит «создай тест-кейсы», «сгенерируй автотесты», «запусти тестовый пайплайн», «создай тесты», «инициализируй проект», «быстрый старт» или «настрой проект» и требуется координация Pipeline 6.0.
---

# Оркестрация тестового пайплайна Pipeline 6.0

Следуй Pipeline 6.0 из `contracts/pipeline.json` и [контракту оркестрации](references/orchestration-contract.md). Сначала заверши semantic-prefix только через `feature_flow`; Context Marker создаёт полный `managed_behavior_context`, generator-safe `changed_behavior_context`, accounting и receipt, после чего prefix получает generator result только из `changed_behavior_context`. После `READY_FOR_PIPELINE_TAIL` classifier/reviewer образуют изолированный technical-evidence gate и не меняют уже созданный candidate/delta. Определяй пути скиллов только через `skill_files`; не используй копии или псевдонимы.

## Обязательная подготовка

1. Полностью прочитай `contracts/pipeline.json` и контракт оркестрации.
2. Выбери exact корень проекта и новый каталог запуска под `<project>/docs/to_do/<run>/`.
3. До чтения первого stage skill выполни bootstrap с output `<project>/docs/to_do/<run>/00-project-bootstrap/attempt-01/skillsrc-init.json` и проверь receipt командами из контракта.
4. При `needs_input` или `conflict` останови пайплайн до `context-marker`. Покажи только первый неразрешённый вопрос вместе с options, evidence и impact; не задавай следующий вопрос одновременно.
5. После ответа сохрани только выбранные option IDs в `<project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-answers.json` и повтори bootstrap с output `<project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-init.json`. Не изменяй прежнюю attempt-папку или receipt.
6. Только при `created`, `updated` или `unchanged` загрузи `<project>/.skillsrc` и выбери exact module ID.
7. При неоднозначном feature-to-module выборе запроси один выбор; не выбирай module по вероятности. Только после exact module selection переходи к `context-marker`.
8. Осмотри выбранный module root без изменений и выбери изолированное место для новых сгенерированных тестов.

Не включай discovery questions, answers и bootstrap-квитанции во входы evaluator-скиллов. Это controller evidence, а не содержимое фичи.

## Выбор модуля фичи

Выбирай module ID только в следующем порядке:

1. Выбери единственный module автоматически.
2. Для exact user-supplied relative feature path выбери module, чей root содержит этот path.
3. Для текстового названия проверь только объявленные `feature_sources` и source paths.
4. При одном прямом совпадении requirement, route, symbol или path выбери module и запиши ID с evidence в controller receipt.
5. При нуле совпадений попроси path или module ID.
6. При нескольких совпадениях покажи module IDs и evidence, затем запроси один выбор.

Передай выбранные requirements и source files как `raw_content` существующему `context-marker`. Не создавай второй feature catalog, не добавляй business content в `.skillsrc` и не сохраняй его в bootstrap receipt.

## Lifecycle

1. Повторяй `tools.feature_flow.advance_feature_flow`: выполни только возвращённый producer/audit/generator action, сохрани его только в returned exact `record_path`, передай record и вызови facade снова. Portable controller — `SEQUENTIAL`; не спрашивай и не принимай user count/mode/scope/shard tuning. Initial run — immutable `FULL`; compatible later run may be `CHANGE_SET`. Обе scope audit и обе batch audit обязательны.
2. При `COMPLETE` semantic-prefix имеет только `READY_FOR_PIPELINE_TAIL`. Вызови `tools.contract_check.materialize_fingerprint_registries(root, contract)`, затем повторяй `tools.pipeline6_tail.advance_pipeline6_tail(..., fingerprint_registries=...)`: запиши только returned tail record и вызови tail снова до `COMPLETE` или `BLOCKED`.
3. Tail сначала independently классифицирует и проверяет technical tests; это chronology после READY_FOR_PIPELINE_TAIL, но не меняет уже созданный prefix generator result. Затем tail использует FULL candidate или CHANGE_SET delta: nonzero delta идёт в publish/review/revision, zero-op выбирает verified predecessor document без candidate review.
4. Tail uses the existing V3 `finalize_orchestration` signature/schema unchanged, builds the terminal receipt with `tools/baseline_lifecycle.build_terminal_run_receipt`, and advances only eligible baselines. Не создавай CLI для tail здесь: Task 10/11 нужен public invocation.

Markdown/CSV — immutable human projections. Не парси их для automation, не объединяй revisions и не создавай receipts вручную. Do not hand-build the terminal carrier. Не выдавай manual/blocker states за PASS и не меняй project code, existing tests, dependencies, configuration или secrets. Raw source/diff, accounting, receipt и technical evidence никогда не входят в generation.

## Stop conditions

Остановись при invalid/V2.1 input, `needs_input`, `conflict`, неизвестном или неоднозначном module, unsafe module root, language conflict, required invention, недоступном validator/tool, schema/semantic failure, secret exposure risk или выходе за authorized scope. Также остановись при failed receipt, невыбранной effective revision, invalid trace или status/branch mismatch.
