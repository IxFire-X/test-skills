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

Команды выполняются из корня пакета (`python -m tools.…`); `--project` — абсолютный путь.
Каждая команда `tools.api` вызывает одну функцию пакета и печатает её результат JSON;
функции пакета не импортируй. Это не model tools произвольного хоста.

| Этап | Команда/API | Результат и проверка |
| --- | --- | --- |
| Целостность | `tools.doctor --root <pack-root>` | manifest и registered bytes; не readiness |
| Подготовка | `tools.run_pipeline scan --project <path> --profile <profile> --docs <path>` (повтори `--docs` для всех входов) | run, inventory, baseline, context receipts; при нескольких modules нужен exact `--module <ID>` |
| Контекст | `tools.api model-request` → model call → `tools.api model-response` | schema, exact authorized context; marker OpenSpec reconciliation |
| Батчи | receipts выбора контекста публикует `scan`; `tools.api prepare` / `tools.api submit --stage plan` строит и публикует header и plan из журнала | independent plan и current receipt без truncation |
| Генерация | `tools.api prepare` / `tools.api submit` для `tc-generator:<batch>`, пачки по очереди; с пачки 2 capabilities прошлых пачек публикует `prepare` | fragment связан с marker/context/plan/header, с пачки 2 — и с capabilities прошлых пачек; code-owned digests |
| Сборка | последний `tools.api submit` (до записи ответа); кандидата записывает `prepare` ревью | semantic/provenance audit, запись кандидата (файлов до вердикта нет) |
| Canonical review | `tools.api prepare --stage tc-reviewer:canonical` (пакет и запрос проверяющего записаны до его вызова); вызов `tc-reviewer`; `tools.api submit --stage tc-reviewer:canonical` (`evidence` — ключ ответа, `--abort` — отказ от ревью) | complete package, отдельный reviewer, exact boundary/ledger |
| Выбор | тот же `submit` (выбор и публикация набора в `<run>/published`) | immutable selected document и receipt |
| Закрытие | `tools.run_pipeline finish` | trace/finalization/terminal |
| Ход/resume | `tools.run_pipeline status --project <path> --run <id>` | last confirmed stage, next artifact, stop reason, evidence paths и сохранённые warnings; чтение не запускает recovery |

Аргументы команды — в `python -m tools.api <команда> --help`; modules не перечитывай. Envelope и digests
создают существующие API; модель пишет содержательную тестовую логику. Отдельного
универсального `next/submit` нет. Status различает journal-only assembly observation
и reread artifact; ранний scan может не иметь сохранённой причины. Ошибка readback
показывается как `EVIDENCE_UNVERIFIED`, а исторический terminal result не переписывается
из-за более позднего изменения исходников.

Inventory учитывает файлы выбранного модуля, parent inputs и явно заданных
зависимостей с причинами исключений. Незавершённый обход и непрочитанный
обязательный source/config/docs не дают `INVENTORY_READY`. Содержательное чтение
проверяемых точек входа, реализации и необходимых зависимостей подтверждается
отдельно по authorized context; список файлов сам по себе не означает полный анализ.

## Обязательная последовательность

1. Получи exact project, полный выбранный набор requirements documents, profile и
   run-scoped authorization. Для OpenSpec зафиксируй full-spec либо selected change
   со связанной регрессией, baseline/дельту и реальные версии/схему. Сверь итоговый
   состав требований и сценариев после дельты и отдельно все условия в их тексте.
   Ограничение одного module не разрешает сужать full-spec. Сохрани пробелы с
   источником, недостающим условием, затронутыми проверками и вопросом в `warnings`.
2. До scan/model call создай/readback-проверь authorization, run manifest и
   `RUN_CREATED`.
3. Read-only inventory -> exact module -> authoritative `.skillsrc` -> frozen
   baseline выбранного профиля. Нужны точные source/config/requirements.
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
6. Собери/audit bare canonical JSON; запиши revision 1: exact V4 bundle receipt (расчётный) и
   `CANDIDATE_PUBLISHED` stage `assembly`. Файлов в `published/` до вердикта нет.
7. Открой одну fresh canonical reviewer session: `(REQUESTED -> PROVIDED)*`, затем один
   authoritative verdict и completion; либо explicit terminal abort до verdict. Exact
   candidate digest получает `REVIEW_REQUESTED` (его пишет `prepare` ревью) до reviewer `MODEL_REQUESTED`;
8. Выбери candidate или единственный complete successor (из `successor_edits` его строит код). Передай downstream только
   effective JSON/digest.
9. Перейди к trace/finalization: pre-finalization trace -> finalization receipt/readback -> terminal result ->
   derived terminal trace -> terminal event (`finish` проходит это сам).


## Project boundary

Pipeline работает в обычном рабочем проекте и не создаёт isolated copy. Разрешены
read-only inventory и запись только в `<project>/.pilot-runs/`, `<project>/.skillsrc` и каталог
публикации `--output-dir` — `<run>/published`. Запрещено менять application source, existing tests, config,
lock files, dependencies, permissions. Код проекта не запускается.

## Reviewer cardinality

Canonical branch имеет максимум одну reviewer session и максимум один authoritative
verdict. Successful/effective canonical требует ровно один verdict. Zero разрешён только
для explicit terminal pre-verdict abort, включая `REVIEW_CONTEXT_LIMIT`.

Model self-attestation не доказывает fresh context; это делает только host/controller evidence.

## Finalization и result

Finalization verifier читает pre-finalization trace.

Receipt `valid=false` не препятствует terminal transition: terminal reason становится
`FINALIZATION_INVALID`, а прежняя stage cause остаётся immutable evidence. Verification
и coverage не переписываются.

Читай результат по всем полям:

```text
attempt_state + completion + verification + coverage + reason_code + accepted
```

Нельзя преобразовывать manual coverage или invalid finalization в PASS-подобный плоский status.

## Resume

Waiting/model interruption продолжает тот же attempt только после validation frozen
snapshot/config. Requirements/module/policy drift останавливает attempt: status называет изменённые пути, выход — вернуть файлы или новый scan.

## Release claim

Manifest state `implemented_unverified` не является readiness. Допустимый claim после
adaptive release eval имеет только вид `core-pilot-ready for <exact verified tuple>`.
Adaptive `1 -> 3 -> 5` запускается только отдельной явной release qualification;
пакет не создаёт и не изменяет CI. Обычный production run выполняет одну logical
invocation каждой нужной model stage. Каждая model response записывает
`transport_attempts=1|2`; `2` — единственный повтор после ошибки схемы или валидации ответа. Bounded reviewer C-lite
кардинальность выводится из exact evidence pairs ledger, а не из повторов stage.
Instability или protocol violation навсегда дисквалифицирует текущую campaign; после
устранения причины новая clean campaign связывается с её eval receipt и требует пять
fresh runs каждого critical scenario.
Company runner, production rollback и Zephyr tenant round-trip остаются отдельными `N/A`
для core pilot.

## Проекции входа модели (V7)

`step-inputs` и `step_size` дают модели не квитанции, а проекции, которые строит код по журналу и пишет в `<run>/review-views/`: опись — `inventory-view-v1` (путь, вид, размер файлов вместо квитанции описи), план пачек генератора — `plan-view-v1` (без текстов требований: тексты — в ответе разметки, `requirements[].text` по `source_requirement_id`). Читай файл проекции из списка входа, а не `inventories/` и `batch-plans/`; `input_digests` запроса остаются digest-ами квитанций.
