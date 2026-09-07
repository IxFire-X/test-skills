---
name: orchestrate
description: Использовать, когда пользователь просит создать тест-кейсы, сгенерировать автотесты или выполнить полный portable testing pipeline для одного документа требований и одного project module.
---

# Оркестрация frozen pilot Pipeline 4.0

Ты — controller в совместимой model-enabled CLI. Не запускай из Python модель и не
передавай model credentials. Python tools здесь только детерминированно проверяют,
публикуют/readback-проверяют артефакты и, при явном разрешении, запускают exact
project-native test targets.

Полностью прочитай `contracts/pipeline.json`, persisted `release/manifest.json` и
[контракт оркестрации](references/orchestration-contract.md). Используй только exact
Skills из `skill_files` и model stages из `stage_registry`; копии и псевдонимы не
являются registered input.

Пути `contracts/`, `schemas/`, `skills/`, `tools/` и `release/` разрешай от корня
этого пакета. Python tools вызывай с cwd пакета и абсолютным `--project` целевого
проекта, чтобы не импортировать чужой модуль `tools`. Cwd project-native executor
при этом остаётся выбранным module root, как требует frozen request.

При формировании reviewer prompt явно передай `run_root` и `attempt_id` и разреши
read-only вызовы штатных `tools.pilot_state` readers для exact boundary, effective
canonical и связанных receipts текущего attempt. Эти API также читают необходимые
run manifest и journal bindings. Не запрещай их общим запретом на run-файлы:
проверка происхождения обязательна. За пределами reviewer context остаются диалог
и reasoning генератора, transport logs и несвязанные runs; проверенные артефакты
и записи идентичности не являются диалогом генератора.

Для проверки хода работы используй `run_pipeline status` и компактные результаты
штатных API. Transport JSONL разбирай как JSON и выводи только нужные поля последних
завершённых событий: одна строка может содержать весь input или исходник, поэтому
`head`/`tail` по числу строк не ограничивают объём. Повторно нужную функцию читай
адресно, не выводи весь Python-модуль. Это правило вывода статуса: полное чтение
обязательных входов роли, validators, digest и readback остаётся обязательным.

## 1. Выбери профиль и создай run

Допустимы только:

- `cases-only-v1`: draft canonical cases/review без выполнения project code; `accepted=false`, не full-pipeline exit 0;
- `local-pilot-v1`: полный pipeline с run-scoped project-native execution; `accepted=true` только после live PASS.

Для `local-pilot-v1` execution разрешён только явным пользовательским запросом полного
pipeline в этом run. Наличие папки, scan или запрос test cases не разрешают execution.

До scan и первого model call вызови durable Phase 1 boundary: создай
`run-authorization-receipt.json`, `run-manifest.json`, readback-проверь оба и запиши
`RUN_CREATED`. State хранится под `<project>/.pilot-runs/<run_id>/`. Не создавай clone,
worktree, sandbox, environment, dependency, CI/cron, commit, push или PR.

## 2. Выбери exact module и заморозь baseline

1. Выполни локальный read-only inventory eligible project tree.
2. Исключи `.git`, dependencies, build outputs, binaries, generated artifacts и файлы
   с потенциальными secrets из model context.
3. Выбери ровно один exact module. Автоматически выбирай только единственный module
   или module, доказанно содержащий exact user path. При неоднозначности покажи один
   вопрос и перейди в `WAITING_FOR_INPUT`.
   `--module` принимает ID из inventory/`.skillsrc`, а не путь: например,
   корню `.` может соответствовать ID `root`. Сначала сопоставь user path с module,
   затем передавай его exact ID; при единственном module не подставляй `.` как ID.
4. Прими valid существующий `.skillsrc` как authoritative. Отсутствующий создай
   автоматически и атомарно; execution-significant drift требует одного подтверждения.
5. Сформируй complete execution baseline: requirements, `.skillsrc`, eligible
   source/config/build/fixture inputs, доказанные parent/wrapper dependencies, module
   cwd/test root, interpreter или wrapper, adapter ID, build profile и typed params.
6. Опубликуй/readback-проверь module selection, inventory и baseline до
   `ATTEMPT_CREATED`, затем создай один nonterminal attempt.

Поздно найденный undeclared execution input не добавляй в baseline. Закрой branch как
`NOT_RUNNABLE/BASELINE_INCOMPLETE`; продолжение возможно только через child attempt.
Run остаётся связан с исходной project/module identity.

## 3. Построй canonical из deterministic batches

1. Один раз вызови `context-marker` для authorized requirements/context и
   persist/readback-проверь полный normalized source-requirement artifact. До вызова
   persist/readback-проверь `model-request` с exact stage/role/policy, model,
   invocation и ordered artifact `input_digests`; `MODEL_REQUESTED` ссылается на digest
   этого envelope. После schema-valid ответа запиши `MODEL_RESPONSE_RECEIVED` с exact
   output digest.
2. Детерминированно разбей normalized requirements на batches и persist/readback-проверь
   каждый context-selection receipt.
3. Для каждого batch вызови `tc-generator` с declared immutable inputs.
   Persist/readback-проверь каждый complete fragment и сразу запиши
   `CANDIDATE_PUBLISHED` для его exact digest.
4. Собери один bare canonical JSON `1.0.0` с explicit many-to-many traceability и
   выполни schema/semantic/provenance audit.
   Проверь полноту относительно исходного документа, а не только normalized context.
   Передай reviewer исходные authorized requirement bytes через существующие immutable
   context receipts; недоступное доказательство полноты оставь gap, не `FULL`-обещание.
5. Немедленно опубликуй revision 1 как `UNREVIEWED` с V4 derived bundle и прочитай exact
   bytes обратно; journal связывает его digest с `CANDIDATE_PUBLISHED` stage `assembly`.
   HTML/CSV никогда не становятся downstream model input.

Human-facing canonical поля обязательны на русском; technical tokens сохраняются
exact. Не ограничивай число requirements/cases искусственным потолком.

## 4. Выполни одно authoritative canonical review

Controller формирует immutable package из candidate, exact requirements/context/source
evidence и provenance. Открой одну fresh role-isolated reviewer session и вызови
`tc-reviewer`. До reviewer model call запиши `REVIEW_REQUESTED` для exact опубликованного
candidate; сам вызов окружи stage-bound `MODEL_REQUESTED` и
`MODEL_RESPONSE_RECEIVED`. Тот же порядок применяется к automation review.

Эти события ограничивают одну logical stage invocation. Поле
`MODEL_RESPONSE_RECEIVED.transport_attempts` равно `1` без retry или `2` после
единственного transport/schema retry генератора; для остальных stages оно всегда `1`.
Кардинальность C-lite фиксируется самими
`EVIDENCE_REQUESTED/EVIDENCE_PROVIDED` pairs reviewer ledger. Эти внутренние действия
не являются повторениями release campaign. Adaptive `1 -> 3 -> 5` запускается только
отдельной явной release qualification, никогда обычным production run.

Внутри session допустимы zero or more bounded C-lite
`EVIDENCE_REQUESTED/EVIDENCE_PROVIDED` pairs. Запрещены per-batch/hierarchical reviewers,
вторая session, generator dialogue/reasoning и больше одного authoritative verdict.
Successful session содержит ровно один verdict; explicit terminal pre-verdict abort,
включая `REVIEW_CONTEXT_LIMIT`, содержит ноль.

`ПРИНЯТО` выбирает exact candidate. `AUTO_FIX_APPLIED` допускает только один complete
successor revision 2 и тот же authoritative verdict. Любая смысловая/частичная/третья
correction — `REWORK/PARTIAL`. Без host isolation evidence записывай
`independence_unverified` и не принимай branch.

При отказе или terminal pre-verdict abort не вызывай automation даже в `local-pilot-v1`.
Закрой раннюю отрицательную ветку через trace/finalization с
`materialization=execution=NOT_APPLICABLE`, исходной причиной и `accepted=false`.

## 5. Закрой выбранный профиль

### `cases-only-v1`

Не вызывай automation, materialization или executor. Зафиксируй их как
`NOT_APPLICABLE`, затем создай branch-valid pre-finalization trace и выполни общий
finalization/terminal порядок. Результат draft/artifact-only: `verification=NOT_APPLICABLE`,
`accepted=false`; green full-pipeline exit 0 запрещён.

### `local-pilot-v1`

Для model request `tc-to-autotest:rN` передай `input_digests` ровно как
`[effective.document_digest, effective.effective_bundle_receipt_digest]` из
`read_effective_canonical`. Для `autotest-reviewer:rN` — ровно
`[boundary.automation_digest, boundary.digest]` из read-back
`automation-review-boundary-rN`. Порядок значим. Это привязки артефактов этапа,
а не hashes всех прочитанных skill/schema/source файлов или полного prompt.
Exact transport input/output bytes сохраняются отдельно; их digest не заменяет
эти поля. Не переписывай опубликованный request после вызова модели.

1. Вызови `tc-to-autotest` для effective canonical.
2. Открой attempt-owned automation review boundary и вызови `autotest-reviewer` в fresh
   invocation. Разрешены initial + максимум одна complete correction/review.
3. После accepted static review сформируй complete generated delta. Controller
   материализует каждый новый pipeline-owned file в active test root и пишет receipt;
   partial materialization запрещает execution.
4. Построй closed execution request. Используй только adapter ID, exact frozen
   interpreter/wrapper, build profile и typed params; user shell strings и
   `argv_template` запрещены.
5. Запусти exact reviewed targets один раз через module-selected pytest, `mvnw` или
   `gradlew`. Не исправляй тест после runtime `FAIL`.

Для первого запуска используй существующий `tools.run_pipeline exec` из cwd пакета.
Передай все шесть carrier paths к уже опубликованным/read-back артефактам этого
attempt; они сверяются с durable evidence и не заменяют authorization или review:

```powershell
python -m tools.run_pipeline exec --project "$project" --module "$moduleId" --run "$runId" `
  --canonical-document "$effectiveCanonicalPath" `
  --automation-artifact "$automationPath" --autotest-review "$automationReviewPath" `
  --authorization-receipt "$authorizationPath" --host-isolation-receipt "$isolationPath" `
  --generated-delta-receipt "$generatedDeltaPath"
```

Здесь `$moduleId` — выбранный ID, а все `*Path` — абсолютные пути: effective canonical,
принятые automation/review, run authorization, `automation-review-boundary-rN` и
generated-delta соответственно. Получи их из результатов штатной публикации и
readback; не создавай заменяющие receipts. Перед первым `exec` проверь наличие всех
шести значений. Пропущенный carrier даёт `RUNNER_INPUT` до старта; это не результат
тестов. Resume после `EXECUTION_STARTED` следует штатной ветке восстановления,
а не повторному запуску по этому примеру.

Controller/process timeout без authoritative framework result даёт `UNKNOWN`.
Framework-reported exact-test timeout с authoritative evidence даёт `FAIL`. После
`EXECUTION_STARTED` interruption не перезапускай автоматически.

## 6. Выполни линейное закрытие

Строго соблюдай порядок:

```text
materialization -> execution -> execution trace -> retain/cleanup decision
-> disposition receipts -> pre-finalization trace -> finalization verification
-> finalization receipt readback -> terminal result -> derived terminal trace
-> terminal event
```

Disposition обязан покрыть весь generated file set:

- `PASS` + valid trace/path/digests: `RETAINED` до finalization;
- `FAIL`/`NOT_RUNNABLE`: очищай только byte-identical pipeline-owned files;
- `UNKNOWN`: cleanup запрещён; используй `PRESERVED_EXECUTION_UNKNOWN` или
  `PRESERVED_CONTENT_CONFLICT`;
- partial materialization: execution не начинается; созданные неизменённые files
  безопасно очищаются, остальные получают `NOT_MATERIALIZED`.

Terminal transition требует completed/read-back finalization receipt. `valid=false`
всё равно terminal, но даёт `FINALIZATION_INVALID` и `accepted=false`, не переписывая
verification/coverage. `RETAINED` сам по себе не означает acceptance.

## Resume и child attempts

`WAITING_FOR_INPUT`, `WAITING_FOR_MODEL` и interruption между model stages остаются
nonterminal. Продолжай их в новой CLI session только после snapshot/config validation.
Requirements/module/policy drift создаёт child attempt. Terminal attempt immutable.
Execution retry требует доказанной остановки прежнего process scope и explicit child
attempt; одновременно active может быть только один.

## Stop conditions

Остановись без guess при invalid/unsupported version, ambiguous module, unsafe path,
required invention, unavailable tool/model, schema/semantic/provenance failure, secret
exposure, baseline drift, stale digest/readback, unproved reviewer isolation, partial
materialization или выходе за authorized scope. Сохрани factual partial/waiting evidence
и не выдавай `implemented_unverified` за verified readiness.
