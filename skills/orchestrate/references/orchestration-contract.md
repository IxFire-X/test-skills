# Исполнимый контракт frozen pilot Pipeline 4.0

## Источники истины

- `contracts/pipeline.json`: stages, artifacts, schemas, profiles, adapters, lifecycle,
  acceptance и exit projection;
- `release/manifest.json`: package/runtime identity и текущая qualification;
- stage schemas и deterministic tools: проверка/публикация фактов;
- durable bytes/events под `<project>/.pilot-runs/<run_id>/`: evidence конкретного run.

Human docs не переопределяют machine truth. Каждый artifact controller публикует
атомарно и читает обратно до следующего перехода.

## Обязательная последовательность

1. Получи exact project, один requirements document, profile и run-scoped authorization.
2. До scan/model call создай/readback-проверь authorization, run manifest и
   `RUN_CREATED`.
3. Read-only inventory -> exact module -> authoritative `.skillsrc` -> complete frozen
   execution baseline. Все три boundary возникают до `ATTEMPT_CREATED`.
4. Создай единственный nonterminal attempt. Persist/readback-проверяй event/artifact
   identity и digest на каждом переходе.
5. Один раз вызови `context-marker` для complete normalized source requirements, затем
   создай deterministic batch plan. Для каждого batch: context receipt -> `tc-generator`
   -> immutable fragment bytes. Каждая logical stage invocation имеет exact stage-bound
   persisted/read-back `model-request` со всеми input digests, model и invocation;
   `MODEL_REQUESTED` ссылается на этот envelope до вызова, затем следует
   `MODEL_RESPONSE_RECEIVED`. Каждый generator fragment немедленно получает
   `CANDIDATE_PUBLISHED`.
6. Собери/audit bare canonical JSON; опубликуй revision 1 как `UNREVIEWED` с exact V4
   bundle receipt и `CANDIDATE_PUBLISHED` stage `assembly`.
7. Открой одну fresh canonical reviewer session: `(REQUESTED -> PROVIDED)*`, затем один
   authoritative verdict и completion; либо explicit terminal abort до verdict. Exact
   candidate digest получает `REVIEW_REQUESTED` до reviewer `MODEL_REQUESTED`.
8. Выбери candidate или единственный complete successor. Передай downstream только
   effective JSON/digest.
9. Для `cases-only-v1` зафиксируй materialization/execution/dispositions как
   `NOT_APPLICABLE` и перейди к trace/finalization.
10. Для `local-pilot-v1`: automation -> static review -> optional one complete
    correction/review -> generated delta -> per-file materialization -> one exact
    project-native execution.
11. Построй execution trace, определи disposition всего generated set, затем выполни
    pre-finalization trace -> finalization receipt/readback -> terminal result -> derived
    terminal trace -> terminal event.

## Project и execution boundary

Pipeline работает в обычном рабочем проекте и не создаёт isolated copy. Разрешены
read-only inventory и новые pipeline-owned generated-test files в selected active test
root. Запрещено менять application source, existing tests, config, lock files,
dependencies, permissions или behavior ради PASS.

Project code запускается только при `local-pilot-v1`, explicit run authorization,
complete materialization и accepted static review. Closed adapter получает:

```text
adapter_id + exact interpreter/wrapper + build_profile + typed adapter_parameters
```

Shell strings, `argv_template`, automatic fallback и cross-module orchestration
запрещены. Environment receipt хранит только allowlisted safe key IDs/labels без values.

## Reviewer cardinality

Canonical branch имеет максимум одну reviewer session и максимум один authoritative
verdict. Successful/effective canonical требует ровно один verdict. Zero разрешён только
для explicit terminal pre-verdict abort, включая `REVIEW_CONTEXT_LIMIT`.

Automation имеет одну static reviewer invocation на revision и не более двух полных
versions/reviews: initial + одна correction. Model self-attestation не доказывает fresh
context; это делает только host/controller evidence.

## Generated set и disposition

Каждый generated file имеет path, exact bytes/digest, materialization receipt и конечный
disposition. Execution не начинается при partial materialization.

| Branch | Disposition |
|---|---|
| authoritative `PASS` + valid trace | unchanged file `RETAINED` |
| `FAIL` / `NOT_RUNNABLE` | byte-identical pipeline-owned file `CLEANED` |
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
доказанной остановки прежнего process scope.

## Release claim

Manifest state `implemented_unverified` не является readiness. Допустимый claim после
adaptive release eval имеет только вид `core-pilot-ready for <exact verified tuple>`.
Adaptive `1 -> 3 -> 5` запускается только отдельной явной release qualification;
пакет не создаёт и не изменяет CI. Обычный production run выполняет одну logical
invocation каждой нужной model stage. Generator response записывает
`transport_attempts=1|2`; остальные responses — `1`. Bounded reviewer C-lite
кардинальность выводится из exact evidence pairs ledger, а не из повторов stage.
Instability или protocol violation навсегда дисквалифицирует текущую campaign; после
устранения причины новая clean campaign связывается с её eval receipt и требует пять
fresh runs каждого critical scenario.
Company runner, production rollback и Zephyr tenant round-trip остаются отдельными `N/A`
для core pilot.
