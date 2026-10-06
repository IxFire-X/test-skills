# Исполнимый контракт frozen pilot Pipeline 4.0

## Источники истины

- `contracts/pipeline.json`: stages, artifacts, schemas, profiles, adapters, lifecycle,
  acceptance и exit projection;
- `release/manifest.json`: package/runtime identity и текущая qualification;
- stage schemas и deterministic tools: проверка/публикация фактов;
- durable bytes/events под `<project>/.pilot-runs/<run_id>/`: evidence конкретного run.

Human docs не переопределяют machine truth. Каждый artifact controller публикует
атомарно и читает обратно до следующего перехода.

## Короткая карта вызовов

Команды выполняются из корня пакета; `--project` — абсолютный путь. В таблице API
относятся к существующим Python modules, не к model tools произвольного хоста.
Командная строка есть у строк «Целостность», «Подготовка», «Canonical review»
(те же команды с `--review-key r1|r2` и `--automation` служат ревью автоматизации),
«Выполнение» и «Ход/resume», а также у публикации candidate (`publish`) и выбора
(`select`) в `tools.orchestrate_test_case_revision`. Остальное — запрос и ответ model
stage, батчи, сборка, automation review boundary, child attempt, материализация и
закрытие `cases-only-v1` — вызовы функций Python без CLI.

| Этап | Команда/API | Результат и проверка |
| --- | --- | --- |
| Весь ход | `tools.pipeline_driver next/submit/status --project <path> [--run <id>]` | детерминированные шаги до задачи `llm`, вопроса `ask_user` или `done`; `submit` проверяет ответ, считает digests и порядок, публикует; рабочие файлы в `<run_id>.driver/` |
| Целостность | `tools.doctor --root <pack-root>` | manifest и registered bytes; не readiness |
| Подготовка | `tools.run_pipeline scan --project <path> --profile <profile> --docs <path>` (повтори `--docs` для всех входов) | новый run (флага `--run` нет), inventory, baseline, attempt, context receipts; `context_gaps` — файлы сверх бюджета контекста; при нескольких modules нужен exact `--module <ID>` |
| Контекст | `pilot_state.publish_model_request` → model call → `publish_model_stage_artifact` | schema, exact authorized context; marker OpenSpec reconciliation |
| Батчи | `batch_assembly.plan_batches`, `project_inventory.select_context_batches`, `pilot_state.publish_context_selection` | independent plan и current receipt без truncation; модель получает маскированные байты (`[REDACTED:<rule>]`), пропущенные файлы — в `gaps` |
| Генерация | те же request/artifact API для `tc-generator:<batch>` | fragment связан с marker/context/plan/header; code-owned digests |
| Сборка | `batch_assembly.assemble_candidate`, `orchestrate_test_case_revision.publish_unreviewed_candidate` | semantic/provenance audit, UNREVIEWED bundle/readback |
| Canonical review | `orchestrate_test_case_revision prepare-review/next-part/open-part/submit-part/fail-part/block-part/finish-review`; `tools.review_budget`; `pilot_state` readers | frozen originals/context/candidate, exact parts, fresh isolation, до трёх вызовов на часть, controller aggregate |
| Выбор | `orchestrate_test_case_revision.orchestrate_revision`, `pilot_state.read_effective_canonical` | immutable selected document и receipt |
| Автоматизация | request/artifact API; `pilot_state.open_automation_review_boundary` | одна версия и независимое static review; максимум одна correction |
| Материализация | `generated_delta.materialize_delta` | accepted exact generated set, ownership/readback |
| Выполнение | `tools.run_pipeline exec` с шестью carriers из инструкции | шлюз компиляции/сбора, reviewed selectors, process-bound native report; при `NOT_RUNNABLE` — `reason` и `regeneration` |
| Закрытие | существующий `finalize_attempt.finalize_attempt` / `finalize_durable_execution_attempt` | trace/dispositions/finalization/terminal по выбранной ветке |
| Ход/resume | `tools.run_pipeline status --project <path> --run <id>` | last confirmed stage, next artifact, stop reason, evidence paths и сохранённые warnings; чтение не запускает recovery |

Точные сигнатуры бери из нужной функции, не перечитывай все modules. Envelope и digests
создают существующие API; модель пишет содержательную тестовую логику. Обычный запуск
ведёт драйвер `tools.pipeline_driver` (`next`, `submit`, `status`): он вызывает эти же
API в нужном порядке, а строки таблицы остаются картой для диагностики и ручного хода.
Status различает journal-only assembly observation
и reread artifact; ранний scan может не иметь сохранённой причины. Ошибка readback
показывается как `EVIDENCE_UNVERIFIED`, а исторический terminal result не переписывается
из-за более позднего изменения исходников.

Inventory учитывает файлы выбранного модуля, parent inputs и явно заданных
зависимостей с причинами исключений. Незавершённый обход и непрочитанный
обязательный source/config/docs не дают `INVENTORY_READY`. Содержательное чтение
проверяемых точек входа, реализации и необходимых зависимостей подтверждается
отдельно по authorized context; список файлов сам по себе не означает полный анализ.

## Обязательная последовательность

1. Определи project, область требований и применимый profile из обычного запроса и
   доступного проекта; не требуй от пользователя назвать внутренний профиль или число
   документов, если входы можно установить чтением проекта. Областью могут быть один
   или несколько документов, все актуальные OpenSpec specs либо selected change со
   связанной регрессией. Зафиксируй полный выбранный набор requirements documents и
   run-scoped authorization. Для OpenSpec зафиксируй full-spec либо selected change
   со связанной регрессией, baseline/дельту и реальные версии/схему. Сверь итоговый
   состав требований и сценариев после дельты и отдельно все условия в их тексте.
   Ограничение одного module не разрешает сужать full-spec. Сохрани пробелы с
   источником, недостающим условием, затронутыми проверками и вопросом в `warnings`.
2. Authorization, run manifest и `RUN_CREATED` создаёт и readback-проверяет сама
   команда `scan` до inventory и любого model call; отдельно run перед ней не создавай.
3. Read-only inventory -> exact module -> authoritative `.skillsrc` -> frozen
   baseline выбранного профиля. Для cases-only нужны точные source/config/requirements,
   для local-pilot дополнительно все execution facts. Существующее событие
   `EXECUTION_BASELINE_FROZEN` подтверждает applicable receipt, не разрешение запуска.
   Все boundary возникают до `ATTEMPT_CREATED`.
4. Создай единственный nonterminal attempt. Persist/readback-проверяй event/artifact
   identity и digest на каждом переходе.
5. Один раз вызови `context-marker` для complete normalized source requirements, затем
   создай deterministic batch plan. Для каждого batch: context receipt -> `tc-generator`
   -> immutable fragment bytes. Каждая logical stage invocation имеет exact stage-bound
   persisted/read-back `model-request` со всеми input digests, model и invocation;
   `MODEL_REQUESTED` ссылается на этот envelope до вызова, затем следует
   `MODEL_RESPONSE_RECEIVED`. Каждый generator fragment немедленно получает
   `CANDIDATE_PUBLISHED`.
6. Собери/audit bare canonical JSON; опубликуй revision 1 как `UNREVIEWED` с exact
   bundle receipt (CSV-профиль по умолчанию `zephyr-scale-step-row-24-v5`) и
   `CANDIDATE_PUBLISHED` stage `assembly`.
7. Подготовь bounded review одного snapshot. Последовательно выполни свежие изолированные
   вызовы всех original-source/local/cross parts и необходимых дополнительных checks.
   `open-part` связывает boundary/request до model call, `submit-part` сохраняет exact
   assessment. `finish-review` recompute/readback проверяет полный состав и публикует
   один controller aggregate; это не model response. Дефект не останавливает остальные
   достоверные части; непроверенные области запрещают acceptance. Сбой вызова части
   фиксирует `fail-part --failure-class TRANSPORT|CONTENT`; после него часть открывается
   новым `open-part` с новым `reviewer_invocation_id`, всего до трёх вызовов на часть.
8. Выбери candidate или единственный complete successor; successor r2 строит
   controller из механических `corrections`, reviewer его не пишет. Передай downstream
   только effective JSON/digest.
9. Для `cases-only-v1` зафиксируй materialization/execution/dispositions как
   `NOT_APPLICABLE` и перейди к trace/finalization.
10. Для `local-pilot-v1`: automation -> static review -> optional one complete
    correction/review -> generated delta -> per-file materialization -> шлюз
    компиляции/сбора -> one exact project-native execution. После
    `NOT_RUNNABLE/GENERATED_TEST_INVALID` исправленная ревизия допускается один раз в
    child attempt с `retry_reason=GENERATED_TEST_INVALID`; после `LAUNCH_FAILED` и
    `TESTS_DESELECTED` регенерации нет.
11. Построй execution trace, определи disposition всего generated set, затем выполни
    pre-finalization trace -> finalization receipt/readback -> terminal result -> derived
    terminal trace -> terminal event.

## Ответ пользователю

В итоговом ответе отрази вердикт reviewer и фактическое выполнение: сколько тестов
прошло/упало либо почему execution не состоялся, пробелы и блокировки, пути к имеющимся
HTML/Markdown/JSON выбранной редакции, reviewer reports, automation, execution report и
папке run. По disposition receipts укажи, какие созданные тестовые файлы сохранены или
удалены; если судьба файла не подтверждена, прямо сообщи это.
Отдельно обозначь drafts, unaccepted/отсутствующие артефакты и невыполненные этапы; не
выводи успешный результат из неполных данных. Используй существующие отчёты и state,
новые форматы не вводи.

## Project и execution boundary

Pipeline работает в обычном рабочем проекте и не создаёт isolated copy. Разрешены
read-only inventory и новые pipeline-owned generated-test files в selected active test
root. Запрещено менять application source, existing tests, config, lock files,
dependencies, permissions или behavior ради PASS.

Project code запускается только при `local-pilot-v1`, explicit run authorization,
complete materialization и accepted static review. Closed adapter получает:

```text
adapter_id + exact interpreter/wrapper/executable + build_profile + typed adapter_parameters
```

Shell strings, `argv_template`, automatic fallback и cross-module orchestration
запрещены. Environment receipt хранит только allowlisted safe key IDs/labels без values.
`maven:selected-symbols-v1` разрешает системный Maven через `executable` и замораживает
абсолютный путь и digest launcher. Wrapper предпочтителен при создании отсутствующей
конфигурации; существующая конфигурация authoritative. Оба Maven-адаптера запускают
точные reviewed selectors через `test` и читают Surefire. Отдельный integration-test
lifecycle требует подтверждённого закрытого маршрута; произвольная shell-команда его
не заменяет. Cases-only не требует фиктивного runtime и не вызывает project code.

## Reviewer cardinality

Canonical branch имеет один logical review и максимум один authoritative aggregate
verdict. Automation имеет один logical review на immutable revision и максимум r1/r2.
Каждый review состоит из последовательных fresh isolated model invocations по объявленным
parts, включая original-source, local и cross checks. Одна часть проходит тот же путь.
Stage IDs: `tc-reviewer:canonical:part-000001`, `autotest-reviewer:r1:part-000001` с
реальной revision/ordinal; policies `canonical-reviewer-v2`/`autotest-static-reviewer-v2`.
Повторный вызов той же части после `fail-part` получает суффикс `-try2` или `-try3`.
В конверте части повторяющееся содержимое передаётся один раз; повтор несёт
`content_ref: {scope_id, input}` вместо `content`.

Controller связывает точные snapshot/plan/input digests, host/CLI/model/settings и
invocation каждой части, сохраняет результат, затем recompute/readback проверяет общий
aggregate. Model self-attestation не доказывает isolation. Нельзя принять неполный,
чужой или stale состав. Дополнительные cross checks остаются в том же append-only ledger;
пересказы, mapping counts и hashes не заменяют смысловой проверки originals и кода.
Содержательные defects сохраняются и не прекращают последующие trustworthy parts.
Повреждение shared snapshot блокирует достоверное продолжение.

Canonical r1 допускает один complete mechanical r2. Automation r1 допускает одну complete
correction r2 с полным повторным review. Parts не тратят correction budgets. После
runtime FAIL регенерация запрещена. Successful logical review имеет один verdict;
explicit pre-verdict abort, включая REVIEW_CONTEXT_LIMIT и REVIEW_TRANSPORT_FAILED
(все заблокированные части имеют класс `TRANSPORT`), — ноль. Static review не
доказывает runtime PASS. `implemented_unverified`/`ready_tuple=null` сохраняются без
реальной qualification. Конкретные CLI аргументы приведены в [Skill](../SKILL.md#4-выполни-одно-authoritative-canonical-review).

## Generated set и disposition

Каждый generated file имеет path, exact bytes/digest, materialization receipt и конечный
disposition. Execution не начинается при partial materialization.

| Branch | Disposition |
|---|---|
| authoritative `PASS` + valid trace | unchanged file `RETAINED` |
| `FAIL` / `NOT_RUNNABLE` | byte-identical pipeline-owned file `CLEANED` |
| `NOT_RUNNABLE/TESTS_DESELECTED` | file остаётся `RETAINED` |
| `UNKNOWN`, unchanged | `PRESERVED_EXECUTION_UNKNOWN` |
| `UNKNOWN`, drifted | `PRESERVED_CONTENT_CONFLICT` + unknown evidence |
| partial materialization | safe cleanup либо `NOT_MATERIALIZED` |

Если cleanup нельзя доказать безопасным, сохрани file с exact reason и
`accepted=false`. Cleanup при `UNKNOWN` запрещён.

## Finalization и result

Finalization verifier читает pre-finalization trace, заканчивающийся dispositions.
Materialization/execution/dispositions проверяются только когда required текущим
profile/branch; иначе они `NOT_APPLICABLE`, не missing.

Receipt `valid=false` не препятствует terminal transition: terminal reason становится
`FINALIZATION_INVALID`, а прежняя stage cause остаётся immutable evidence. Verification
и coverage не переписываются.

Читай результат по всем полям:

```text
attempt_state + completion + verification + coverage + reason_code + accepted
```

Нельзя преобразовывать `FAIL`, `UNKNOWN`, `NOT_RUNNABLE`, manual coverage или invalid
finalization в PASS-подобный плоский status.

## Resume

Waiting/model interruption продолжает тот же attempt только после validation frozen
snapshot/config. Requirements/module/policy drift создаёт child attempt. Late undeclared
execution input закрывает текущий attempt как `NOT_RUNNABLE/BASELINE_INCOMPLETE`.
Execution interruption даёт `UNKNOWN`; retry — только explicit child attempt после
доказанной остановки прежнего process scope: proof `WINDOWS_JOB_TERMINATED` (Windows,
Job Object) или `POSIX_PROCESS_GROUP` (живых процессов нет ни в группе, ни среди
ушедших через `setsid`). Без proof повтор запрещён.

## Release claim

Manifest state `implemented_unverified` не является readiness. Допустимый claim после
adaptive release eval имеет только вид `core-pilot-ready for <exact verified tuple>`.
Adaptive `1 -> 3 -> 5` запускается только отдельной явной release qualification;
пакет не создаёт и не изменяет CI. Обычный production run выполняет одну logical
invocation каждой нужной model stage; исключение — часть ревью после `fail-part`
(до трёх вызовов, stages `-try2`/`-try3`). Каждый response записывает
`transport_attempts` от 1 до 3. Событий `EVIDENCE_REQUESTED`/`EVIDENCE_PROVIDED` нет:
число вызовов reviewer выводится из частей ревью и их boundary.
Instability или protocol violation навсегда дисквалифицирует текущую campaign; после
устранения причины новая clean campaign связывается с её eval receipt и требует пять
fresh runs каждого critical scenario.
Company runner, production rollback и Zephyr tenant round-trip остаются отдельными `N/A`
для core pilot.
