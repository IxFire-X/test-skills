# Как работает frozen pilot Pipeline 4.0

## Архитектурная граница

```text
пользователь
  -> совместимая model-enabled CLI
       -> skills/orchestrate/SKILL.md
       -> model stages в отдельных declared roles
       -> deterministic Python controller/tools
            -> durable evidence в <project>/.pilot-runs/<run_id>
            -> project-native pytest/Maven/Gradle для exact reviewed targets
```

CLI управляет моделями. Python не вызывает LLM и не хранит credentials. Project-native
executor запускается только в `local-pilot-v1` после run-scoped authorization. Pipeline
не создаёт isolated workspace: он работает в обычном проекте, где environment уже
настроен пользователем.

## 1. Release identity

`release/manifest.json` связывает package `0.5.0-pilot` с exact digest каждого runtime
файла под `contracts/`, `schemas/`, `skills/`, `tools/` и `evals/`. Сам manifest не
входит в registry, поэтому digest graph ацикличен:

```text
runtime bytes -> sorted path/file digests -> skill_pack_digest
-> manifest body -> manifest digest
```

Manifest также связывает Pipeline `4.0`, compatibility `portable-cli-v1`, execution
profile `v1`, stage/profile/adapter registries и `pilot-critical-v1` eval suite.
`implemented_unverified` означает, что bytes и contract реализованы, но exact host/model
tuple ещё не получил release-eval evidence.

## 2. Run раньше scan

Явный invocation сначала вызывает durable Phase 1 boundary:

```text
run authorization receipt
  -> run-manifest.json
  -> RUN_CREATED
```

Каждая публикация атомарна и немедленно читается обратно. Если достоверную boundary
создать не удалось, это controller error, а не фиктивный attempt result.

Run создаётся до module selection, затем навсегда связывается с одной exact
project/module identity. Child attempts образуют последовательную append-only lineage;
два nonterminal attempts одновременно запрещены.

## 3. Inventory и frozen baseline

Scanner делает локальный read-only inventory eligible tree. В model context попадают
только deterministic batches из разрешённых source roots. Исключаются `.git`,
dependencies, build outputs, binaries, generated artifacts и потенциальные secrets.

После exact module selection controller фиксирует:

- project/module identity;
- requirements bytes/provenance;
- authoritative `.skillsrc`;
- eligible source/config/build/fixture inputs;
- parent/wrapper/build inputs, которые входят в declared execution baseline;
- module cwd, test root, interpreter/wrapper, adapter ID, build profile и typed params.

Baseline публикуется и читается обратно до `ATTEMPT_CREATED`. Поздно найденный
undeclared execution input не добавляется в текущий attempt: branch закрывается как
`NOT_RUNNABLE/BASELINE_INCOMPLETE`, продолжение возможно только child attempt.

## 4. Requirement batches и canonical

```text
source requirement + provenance/digest
  -> normalized requirement
  -> deterministic batch plan
  -> candidate fragments
  -> assembled canonical revision 1
  -> schema + semantic + provenance audit
```

Mapping many-to-many связывает source requirements с canonical requirements, cases,
steps, expectations, assertions и последующим execution evidence. `out-of-scope`
разрешён только относительно заранее выбранного feature/module scope.

Canonical JSON `1.0.0` — semantic source. Stage envelopes имеют version `5.0.0`.
HTML и CSV V4 строятся только из canonical JSON и никогда не становятся downstream
model input.

## 5. Одна authoritative reviewer session

Controller сначала публикует revision 1 как `UNREVIEWED`, проверяет exact bytes и
формирует immutable reviewer package. Reviewer получает candidate, exact requirements,
context/source evidence и provenance, но не generator dialogue/reasoning.

```text
REVIEW_SESSION_STARTED
  -> zero or more bounded EVIDENCE_REQUESTED/EVIDENCE_PROVIDED pairs
  -> exactly one AUTHORITATIVE_VERDICT
  -> REVIEW_SESSION_COMPLETED

or, before any verdict:

REVIEW_SESSION_STARTED
  -> zero or more bounded EVIDENCE_REQUESTED/EVIDENCE_PROVIDED pairs
  -> REVIEW_SESSION_ABORTED
```

Разрешена одна fresh role-isolated session с несколькими bounded C-lite retrieval
calls. Per-batch, hierarchical, вторая session и несколько authoritative reviewers
запрещены. Zero verdict допустим только для explicit terminal pre-verdict abort,
например `REVIEW_CONTEXT_LIMIT`.

Host/controller доказывает isolation. Если доказательства нет, фиксируется
`independence_unverified`, и effective canonical не может быть accepted.
При `AUTO_FIX_APPLIED` тот же reviewer output содержит один complete
reviewer-produced `successor_document` revision 2. Controller проверяет schema,
semantics, provenance, lineage и readback до выбора effective canonical; отдельного
generator invocation или второго reviewer verdict нет.

## 6. Два branch-профиля

### `cases-only-v1`

После canonical review automation, materialization, execution и dispositions
имеют `NOT_APPLICABLE`. Это draft/artifact-only: `accepted=false`, полный pipeline
не даёт exit 0. Controller всё равно создаёт branch-valid pre-finalization
trace, finalization receipt, terminal result и derived terminal trace.

### `local-pilot-v1`

```text
effective canonical
  -> automation revision 1
  -> static reviewer invocation
  -> optional one complete correction/review (revision 2)
  -> complete generated delta
  -> per-file materialization receipts
  -> exact project-native execution
```

Automation initial + максимум одна correction означает не более двух полных revisions.
Следующее отклонение закрывает branch как `REWORK/PARTIAL`; все revisions и reviews
остаются append-only evidence.

Generated source сначала существует как pipeline-owned delta. Controller проверяет
path, digest, accepted static review и ownership до записи каждого файла в active test
root. Partial materialization запрещает execution.

## 7. Project-native execution

Closed adapter строит argv из typed fields; user shell strings отсутствуют.

- Python: выбранный interpreter/venv, обычные pytest config, conftest, fixtures и
  plugins, module cwd.
- Java: module-local `mvnw`/`gradlew`, declared profile и actual project JDK.

Плагины и lifecycle hooks могут выполнить код: это доверенный обычный проект, не
sandbox. Execution запускает exact reviewed symbols только один раз в attempt.

Timeout имеет два разных смысла:

- controller/process timeout без authoritative framework result -> `UNKNOWN`;
- framework-reported exact-test timeout с authoritative evidence -> `FAIL`.

Valid process-bound report с нулём собранных tests — authoritative
`FAIL/NO_TESTS_COLLECTED`; missing, corrupt или unbound report остаётся `UNKNOWN`.
До disposition execution receipt сохраняет native report, exact generated bytes и
ограниченный scrubbed runner output под run root и проверяет их readback/digests.

После `EXECUTION_STARTED` interruption не перезапускается автоматически. Для retry
нужны доказанная остановка process scope и explicit child execution attempt.

## 8. Trace, disposition и finalization

Нормативный физический порядок:

```text
MATERIALIZATION
-> EXECUTION
-> EXECUTION_TRACE
-> RETAIN_OR_CLEANUP_DECISION
-> DISPOSITION_RECEIPTS
-> PRE_FINALIZATION_TRACE
-> FINALIZATION_VERIFICATION
-> FINALIZATION_RECEIPT_READ_BACK
-> TERMINAL_RESULT
-> DERIVED_TERMINAL_TRACE
-> TERMINAL_EVENT
```

Pre-finalization trace заканчивается execution/dispositions и не ссылается на ещё не
существующий finalization receipt. После verification публикуется derived terminal
trace со ссылкой на receipt/result — цикла нет.

Disposition определяется для каждого materialized file и всего generated delta:

| Verification | Неизменённый pipeline-owned file | Drift/conflict |
|---|---|---|
| `PASS` + valid trace | `RETAINED` | unaccepted conflict |
| `FAIL` / `NOT_RUNNABLE` | `CLEANED` | preserved с точной причиной |
| `UNKNOWN` | `PRESERVED_EXECUTION_UNKNOWN` | `PRESERVED_CONTENT_CONFLICT` |

Cleanup при `UNKNOWN` запрещён. `RETAINED` — только физический pre-finalization факт;
сам по себе он не означает `accepted=true`.

После `FAIL` live generated bytes очищаются только после проверки уже связанного
execution receipt; durable копия, native report и structured execution result остаются.

Terminal transition требует completed/read-back receipt, но не `valid=true`. Invalid
receipt даёт `FINALIZATION_INVALID`, сохраняет фактические verification/coverage и
делает `accepted=false`. Terminal `reason_code` устанавливается один раз; stage causes
остаются в append-only events/trace.

Повторный finalize не создаёт второй execution или terminal result. После readback
scenario observation controller добавляет единственный attempt-bound
`TERMINAL_RETRY_OBSERVED`; loader проверяет неизменные terminal/closure digests и counts.

## 9. Result projection

```text
attempt_state: ACTIVE | WAITING_FOR_INPUT | WAITING_FOR_MODEL | TERMINAL
completion:    COMPLETE | PARTIAL | FATAL | null
verification:  PASS | FAIL | UNKNOWN | NOT_RUNNABLE | NOT_APPLICABLE | null
coverage:      FULL | MIXED | MANUAL_ONLY | null
accepted:      boolean только terminal
reason_code:   один раз только terminal
```

Оси независимы: `COMPLETE + FAIL` корректен. Early FATAL может иметь `coverage=null`.
`REVIEW_CONTEXT_LIMIT` до execution имеет `verification=NOT_APPLICABLE`.

## 10. Release eval

Readiness всегда выглядит как `core-pilot-ready for <exact verified tuple>` и включает
pack version/digest, CLI host/runtime, role policy, generator/reviewer models, OS,
language runtime, framework, build tool, adapter/profile и immutable project snapshot.

Policy `adaptive-1-3-5-v1` требует:

1. один smoke;
2. три fresh repetitions каждого critical scenario;
3. пять после любой instability или protocol violation.

Protocol violation всегда блокирует readiness. Company runner, production rollback и
Zephyr tenant round-trip не нужны для core pilot и остаются `N/A` без отдельного
evidence.
