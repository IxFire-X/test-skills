---
name: orchestrate
description: Использовать, когда запрос содержит «создай тест-кейсы», «сгенерируй автотесты», «запусти тестовый пайплайн», «создай тесты», «инициализируй проект», «быстрый старт» или «настрой проект» и требуется координация Pipeline 4.0.
---

# Оркестрация тестового пайплайна Pipeline 4.0

Следуй Pipeline 4.0 из `contracts/pipeline.json` и [контракту оркестрации](references/orchestration-contract.md). Определяй пути скиллов только через `skill_files`; не используй копии или псевдонимы.

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

1. Сначала построй `technical_test_inventory` и `authorized_behavior_sources`; затем `context-marker` создаёт только `managed_behavior_context`. Независимо классифицируй и проверь каждый test symbol; accepted `effective_technical_evidence` сохрани рядом с attempt, но не передавай в V3 automation, trace или final carriers. Только `managed_behavior_context` передай в `tc-generator` для candidate bare canonical JSON.
2. Publish candidate: проверь candidate schema+semantics и опубликуй immutable JSON/Markdown/Zephyr CSV bundle до review.
3. Вызови `orchestrate_revision` из `tools.orchestrate_test_case_revision`: publish a valid full successor до выбора; downstream передавай ровно одну effective revision и effective digest.
4. Сгенерируй и статически проверь automation. При `AUTO_FIX_APPLIED` от autotest reviewer выполни regeneration и review заново.
5. Пропусти `tools.run_tests` только для BLOCKED или manual zero-pair branch. Для остальных используй selected `--skillsrc` и `--module`.
6. Для каждой terminal branch построй trace через `tools/build_trace_document.py`, вызови `validate_trace_document`, затем `tools/trace_check.py --require-execution`.
7. Вызови `finalize_orchestration` и получи ровно один status: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`, `BLOCKED`, `FAIL` или `NOT_RUNNABLE`.

Markdown/CSV — immutable human projections. Не парси их для automation, не объединяй revisions и не создавай receipts вручную. Do not hand-build the terminal carrier. Не выдавай manual/blocker states за PASS и не меняй project code, existing tests, dependencies, configuration или secrets.

## Stop conditions

Остановись при invalid/V2.1 input, `needs_input`, `conflict`, неизвестном или неоднозначном module, unsafe module root, language conflict, required invention, недоступном validator/tool, schema/semantic failure, secret exposure risk или выходе за authorized scope. Также остановись при failed receipt, невыбранной effective revision, invalid trace или status/branch mismatch.
