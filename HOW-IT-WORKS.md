# Как работает frozen pilot Pipeline 4.0

## Архитектурная граница

```text
пользователь
  -> совместимая model-enabled CLI
       -> skills/orchestrate/SKILL.md
       -> драйвер tools.pipeline_driver: next -> задача -> submit
       -> model stages в отдельных declared roles
       -> deterministic Python controller/tools
            -> durable evidence в <project>/.pilot-runs/<run_id>
            -> project-native pytest/Maven/Gradle для exact reviewed targets
```

CLI управляет моделями. Python не вызывает LLM и не хранит credentials. Project-native
executor запускается только в `local-pilot-v1` после run-scoped authorization. Pipeline
не создаёт isolated workspace: он работает в обычном проекте, где environment уже
настроен пользователем.

## Что делает код и что делает модель

Ход запуска ведёт драйвер `tools.pipeline_driver`. Это обычная программа без модели
внутри. У неё три команды: `next`, `submit` и `status`.

Простыми словами:

1. Оркестратор вызывает `next`. Драйвер выполняет подряд все шаги, где думать не надо:
   создаёт run, читает проект, фиксирует baseline, делит требования на батчи,
   собирает и публикует документы, готовит части ревью, записывает файлы тестов,
   запускает их и закрывает попытку.
2. Когда нужен содержательный ответ, драйвер останавливается и печатает одну задачу
   (`action: "llm"`): какую инструкцию роли прочитать (`skill_path`), какие файлы
   прочитать (`inputs`), куда записать ответ (`output_path`) и по какой схеме
   (`schema_path`).
3. Модель читает входы, пишет ответ в файл и вызывает `submit`.
4. `submit` проверяет ответ. Если он годится, драйвер дописывает служебные поля,
   публикует результат и сразу выдаёт следующую задачу. Если нет — возвращает ту же
   задачу со `"status": "rejected"` и списком ошибок; ничего не публикуется.
5. Так продолжается, пока драйвер не вернёт `done` (итог) или `ask_user` (вопрос
   человеку).

Что остаётся модели: разметить контекст и пробелы требований, написать кейсы для
батча, оценить часть ревью, написать автотесты. Схема задачи содержит только
содержательные поля.

Что модель больше не делает:

- не считает SHA-256 и другие digests;
- не сортирует массивы и не нумерует `display_order`;
- не заполняет служебные поля (`batch_id`, версии схем, ссылки на квитанции, lineage);
- не переносит пути и digests от одного шага к другому: все пути есть в задаче;
- не решает, какой шаг следующий.

Вопросов к человеку два. `reviewer-isolation`: может ли хост выполнить каждую часть
ревью в свежем изолированном контексте; при ответе `none` драйвер останавливается с
`REVIEWER_ISOLATION_UNAVAILABLE`, а кандидат остаётся `UNREVIEWED`.
`regenerate-after-gate`: после `NOT_RUNNABLE/GENERATED_TEST_INVALID` создать одну
дочернюю попытку или завершить.

Свои рабочие файлы (задачи, входы, ответы, копию опубликованного набора кейсов)
драйвер хранит в `<project>/.pilot-runs/<run_id>.driver/`, там же служебный лог
`driver-log.jsonl` (каждая команда, выдача задач, сбои с traceback). Их можно построить заново;
источник истины — журнал и квитанции в `.pilot-runs/<run_id>/`. После перерыва
`next --run <run_id>` возвращает ту же незакрытую задачу или продолжает с последнего
подтверждённого шага.

Ограничения драйвера:

- он не вызывает модель и не может сам доказать изоляцию ревьюера: это сообщает хост;
- один модуль на run;
- код проекта модель получает целиком одним входом; если он не помещается в окно
  модели, драйвер ничего не обрезает — размер входа виден в поле `input_bytes` задачи,
  и решение (сузить контекст через `--target` или взять другую модель) принимает человек;
- он продолжает только run, созданный его же `next`;
- он не проверен на Windows и на живой модели; состояние релиза —
  `implemented_unverified`, ready tuple — `null`.

Команды, которые упоминаются в разделах ниже (`run_pipeline scan`, `exec`,
`prepare-review`, `open-part`, `submit-part`, `fail-part`, `finish-review`), — это
внутренние шаги. При обычном запуске их выполняет драйвер; отдельно они остаются для
диагностики и ручного хода
([manual-sequence.md](skills/orchestrate/references/manual-sequence.md)).

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
создать не удалось, это controller error, а не фиктивный attempt result. Эту boundary
создаёт сама команда `run_pipeline scan`: она не принимает `--run` и каждый раз
начинает новый run, после чего выполняет inventory. Первый `next` драйвера вызывает
именно её.

Писатели и читатели одного run сериализуются межпроцессной блокировкой
`.pilot-runs/<run_id>/.lock`. Читатели (`status`, `derive_state`) не удаляют
pending-маркеры незавершённой записи; восстановление после сбоя писателя делает
следующий писатель под блокировкой. Ожидание блокировки ограничено 120 секундами,
затем команда завершается ошибкой `run is locked by another process`.

Run создаётся до module selection, затем навсегда связывается с одной exact
project/module identity. Child attempts образуют последовательную append-only lineage;
два nonterminal attempts одновременно запрещены.

## 3. Inventory и frozen baseline

Scanner делает локальный read-only inventory eligible tree. В model context попадают
только deterministic batches из разрешённых source roots. Исключаются `.git`,
dependencies, build outputs, binaries, generated artifacts и потенциальные secrets.
Inventory учитывает `.gitignore` через `git ls-files -co --exclude-standard`; при сбое
git используется обход файловой системы. По умолчанию исключены также `.gradle`,
`.kotlin`, `.idea`, `.vscode`, отчёты `coverage*` и `*.log`; каталоги `build`,
`generated` и `coverage`, в которых лежит исходный код, остаются.

Секреты по имени — только `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `*.kdbx`, `id_*`
и каталоги `secrets/`, `credentials/`. По содержимому — сигнатуры токенов, PEM-блок
приватного ключа и присваивание строкового литерала от 16 символов. Файл кода или
настроек с совпадением исключается целиком. В документах и спецификациях совпавшие
строки заменяются на `[REDACTED:<rule>]`, в inventory у файла появляется поле
`redactions`, а сам файл остаётся входом; `select_context_batches` отдаёт модели уже
замаскированные байты. Файл больше бюджета контекста пропускается явно: квитанция выбора
контекста получает `gaps` и `byte_budget`, ответ `scan` — `context_gaps`.

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
Ответ `tc-generator` — исключение: это не envelope, а сам фрагмент по
`schemas/candidate-fragment.schema.json` с `batch_id` и собственным digest.
Модель возвращает драйверу только содержательную часть фрагмента; `batch_id`, digest,
`display_order` и порядок массивов добавляет `submit`.
HTML, Markdown и CSV строятся только из canonical JSON и никогда не становятся
downstream model input. CSV по умолчанию имеет профиль `zephyr-scale-step-row-24-v5`
(только человеческие поля шага); V4 доступен по явному `--csv-profile`.

При сборке фрагментов controller сам сортирует capabilities. Одинаковая capability из
разных batches с разным provenance сливается; различие в её семантике даёт
`BATCH_CAPABILITY_CONFLICT` с обоими `batch_id`. Ошибки сборки и схемы содержат все
диагностики с JSON-pointer.

## 5. Одно authoritative ревью по частям

Controller сначала публикует revision 1 как `UNREVIEWED`, проверяет exact bytes и
замораживает исходные требования, контекст и candidate в одном review snapshot. По нему
строится план частей (`per_review_part`): original-source, local и cross-part scopes.
Reviewer получает candidate, exact requirements, context/source evidence и provenance,
но не generator dialogue/reasoning.

```text
REVIEW_SESSION_STARTED
  -> для каждой части: свой fresh isolated invocation
     (open-part -> ответ модели -> submit-part)
  -> finish-review: controller aggregate, ровно один AUTHORITATIVE_VERDICT
  -> REVIEW_SESSION_COMPLETED

или, если проверены не все части:

REVIEW_SESSION_STARTED
  -> части, которые удалось проверить
  -> REVIEW_SESSION_ABORTED
```

Шаги `open-part`, `submit-part` и `finish-review` выполняет драйвер: каждая часть
приходит модели задачей `tc-reviewer:*` с `requires_fresh_context=true`.

Каждая часть — отдельный вызов модели со своим boundary и host evidence; событий
`EVIDENCE_REQUESTED`/`EVIDENCE_PROVIDED` нет. Логическое ревью одно, и authoritative
verdict один: его вычисляет controller в `finish-review` из сохранённых оценок частей.
Модель возвращает только оценку своей части. Содержимое, повторяющееся внутри одной
части, приходит один раз; у повторов вместо `content` стоит
`content_ref: {scope_id, input}` — ссылка на первое вхождение в этой же части.

Если вызов части не дал пригодной оценки, сбой фиксируется через
`submit --failed TRANSPORT|CONTENT --reason "..."` драйвера (при ручном ходе — командой
`fail-part` с `--failure-class TRANSPORT|CONTENT`), и часть открывается новым
invocation — всего не
более трёх вызовов на часть (stages `…:part-NNNNNN-try2`, `-try3`). Незавершённое
ревью закрывается как `REVIEW_SESSION_ABORTED`: `REVIEW_CONTEXT_LIMIT`, если часть не
поместилась в бюджет; `REVIEW_TRANSPORT_FAILED`, если все заблокированные части не
удалось доставить reviewer; иначе `REVIEW_INCOMPLETE` (остались непроверенные области).
Прерванное ревью никогда не даёт `REWORK`: эта причина требует одного вердикта REJECTED.

Host/controller доказывает isolation каждой части. Если доказательства нет, фиксируется
`independence_unverified`, и effective canonical не может быть accepted.
При `AUTO_FIX_APPLIED` revision 2 строит controller
(`review_parts.apply_review_corrections`) из механических исправлений, которые
reviewer предложил в частях; сам reviewer документ не пишет. Controller проверяет
schema, semantics, provenance, lineage и readback до выбора effective canonical;
отдельного generator invocation или второго reviewer verdict нет.

## 6. Два branch-профиля

### `cases-only-v1`

После canonical review automation, materialization, execution и dispositions
имеют `NOT_APPLICABLE`. Это draft/artifact-only: `accepted=false`, полный pipeline
не даёт exit 0: валидное завершение — exit 1; FATAL, `FINALIZATION_INVALID`, невалидный
trace или ненадёжное закрытие — exit 2. Controller всё равно создаёт branch-valid pre-finalization
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
Следующее отклонение закрывает branch как `PARTIAL`; все revisions и reviews
остаются append-only evidence. Драйвер записывает для статического ревью автотестов
собственные причины завершения: `AUTOMATION_REVIEW_REJECTED` (ревью отклонило),
`AUTOMATION_REVISION_BUDGET` (исправления нужны и после revision 2),
`AUTOMATION_REVIEW_CONTEXT_LIMIT`, `AUTOMATION_REVIEW_TRANSPORT_FAILED` и
`AUTOMATION_REVIEW_INCOMPLETE` (ревью прервано). Файлы тестов в проект при этом не пишутся.

Canonical blocker блокирует только свой кейс. Для остальных кейсов автоматизация
генерируется (`GENERATED`), каждый шаг заблокированного кейса получает строку в
`manual_dispositions`, а `diagnostics` обязательны. Статус `BLOCKED` допустим, только
когда автоматизировать нечего. При наличии blockers `accepted=false`.

Generated source сначала существует как pipeline-owned delta. Controller проверяет
path, digest, accepted static review и ownership до записи каждого файла в active test
root. Partial materialization запрещает execution.

## 7. Project-native execution

Closed adapter строит argv из typed fields; user shell strings отсутствуют.

- Python: выбранный interpreter/venv, обычные pytest config, conftest, fixtures и
  plugins, module cwd.
- Java: `mvnw`, системный `mvn` из PATH (адаптер `maven:selected-symbols-v1`) или
  `gradlew`; declared profile и actual project JDK. Maven и Gradle запускаются из
  корня сборки (`test.build_root`, по умолчанию module root) с указанием модуля.

`.skillsrc` хранит логические имена `python`, `mvnw`, `gradlew`, `mvn`; путь под
текущую ОС разрешается при запуске. Таймаут процесса — `test.timeout_seconds`
(по умолчанию 600 секунд).

Плагины и lifecycle hooks могут выполнить код: это доверенный обычный проект, не
sandbox. Execution запускает exact reviewed symbols только один раз в attempt.

Перед основным запуском controller выполняет шлюз компиляции/сбора: pytest
`--collect-only` по выбранным nodeid, Maven `test-compile`, Gradle `testClasses`.
Провал шлюза и ошибка компиляции/импорта в основном запуске дают
`NOT_RUNNABLE/GENERATED_TEST_INVALID`: сгенерированные файлы убираются, вывод
сохраняется в run root. После этого исправленная ревизия автоматизации допускается
один раз, в child attempt с `retry_reason=GENERATED_TEST_INVALID`, если родитель не
потратил revision 2 в статическом ревью и сам не является таким child attempt.
Драйвер перед этим задаёт вопрос `regenerate-after-gate` и создаёт child attempt
только при ответе `regenerate`.
Падение продуктовой проверки — `FAIL`, без регенерации. Процесс, который не
запустился, даёт `NOT_RUNNABLE/LAUNCH_FAILED`; pytest, собравший 0 явно выбранных
тестов из-за `addopts`, `-m` или `PYTEST_ADDOPTS`, — `NOT_RUNNABLE/TESTS_DESELECTED`.

Timeout имеет два разных смысла:

- controller/process timeout без authoritative framework result -> `UNKNOWN`;
- framework-reported exact-test timeout с authoritative evidence -> `FAIL`.

Для Maven/Gradle valid process-bound report с нулём собранных tests — authoritative
`FAIL/NO_TESTS_COLLECTED`; missing, corrupt или unbound report остаётся `UNKNOWN`
(`JUNIT_MISSING`, `JUNIT_INVALID`), как и ненулевой exit при зелёном отчёте
(`NONZERO_EXIT_GREEN_REPORT`) и сбой сохранения артефактов
(`ARTIFACT_PERSISTENCE_FAILED`).
До disposition execution receipt сохраняет native report, exact generated bytes и
ограниченный scrubbed runner output (хвост до 64 КиБ) под run root и проверяет их
readback/digests. В `execution.environment_inputs` записываются имена и SHA-256
значений `PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `MAVEN_OPTS`, `MAVEN_ARGS`,
`JAVA_TOOL_OPTIONS`, `_JAVA_OPTIONS`, `GRADLE_OPTS`, `JAVA_HOME`; сами значения не
пишутся. После запуска drift определяется только по входам baseline: изменённый или
удалённый вход — drift, новые файлы (coverage, `.gradle`, логи) — нет.

После `EXECUTION_STARTED` interruption не перезапускается автоматически. Для retry
нужны доказанная остановка process scope и explicit child execution attempt.
Доказательство выдаётся при остановке по таймауту. На Windows тестовый процесс
запускается внутри Job Object; proof `WINDOWS_JOB_TERMINATED` означает, что завершён
весь Job. На POSIX proof `POSIX_PROCESS_GROUP` выдаётся, только когда живых процессов
нет ни в группе процесса, ни среди ушедших из неё через `setsid`. Если остановку
доказать не удалось, proof нет и child attempt после `UNKNOWN` запрещён.

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
| `FAIL` / `NOT_RUNNABLE` | `CLEANED` (`NOT_RUNNABLE/TESTS_DESELECTED` — `RETAINED`) | preserved с точной причиной |
| `UNKNOWN` | `PRESERVED_EXECUTION_UNKNOWN` | `PRESERVED_CONTENT_CONFLICT` |

Cleanup при `UNKNOWN` запрещён. `RETAINED` — только физический pre-finalization факт;
сам по себе он не означает `accepted=true`.

После `FAIL` live generated bytes очищаются только после проверки уже связанного
execution receipt; durable копия, native report и structured execution result остаются.
Очистка удаляет файлы: пустые каталоги пакетов, созданные под них, и отчёт
`test-results/pytest.xml` могут остаться в проекте.

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
`attempt_state` возвращается в `ACTIVE`, когда после `WAITING_FOR_MODEL` или
`WAITING_FOR_INPUT` записан прогресс.

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
