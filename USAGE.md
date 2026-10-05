# Использование portable pilot

Для первого запуска откройте **[QUICKSTART.md](QUICKSTART.md)**: там есть подготовка окружения, готовые запросы и список результатов. Ниже — подробные правила настройки и выполнения.

## 1. Подключите пакет

Поместите папку пакета в обычный рабочий проект, сохранив `contracts/`, `schemas/`,
`skills/`, `tools/`, `evals/` и `release/`. Проект уже должен иметь рабочие JDK/Maven,
Gradle или Python/pytest environment. Пайплайн ничего не устанавливает.

Удобное место — `<project>/.tools/test-skills/`. Все пути пакета разрешаются от этой
папки. Команды `python -m tools...` выполняйте с cwd пакета и передавайте абсолютный
`--project`; так модуль `tools` целевого проекта не подменит инструменты пакета.
Сами автотесты запускаются из выбранного module root.

Нормативная точка входа — явный запрос model-enabled CLI выполнить
`skills/orchestrate/SKILL.md`. Python controller не выбирает модель, не хранит model
credentials и не предоставляет standalone `pipeline run`.

Пример запроса CLI:

```text
Выполни skills/orchestrate/SKILL.md для требований docs/feature.md в этом проекте.
Профиль: local-pilot-v1. Разрешаю project-native выполнение exact reviewed targets
только в рамках этого run.
```

Для тест-кейсов без запуска кода выберите `cases-only-v1` и не давайте execution
authorization.

## 2. Что указать

Обязательно:

- один документ требований новой или изменяемой фичи;
- exact project root;
- профиль `cases-only-v1` или `local-pilot-v1`.

Если проект многомодульный, укажите module ID или дайте pipeline выбрать его по
доказанному exact path. При неоднозначности pipeline задаёт только один вопрос и
переходит в `WAITING_FOR_INPUT`. Текстовый guess запрещён.

`--target` или allowlist могут сузить read-only inventory. `.git`, dependencies,
build outputs, binaries, generated artifacts и потенциальные secrets не передаются
модели. Файл с потенциальным secret исключается целиком; если он необходим, дайте
sanitized source, safe fixture или opaque runtime handle.

## 3. `.skillsrc`

Linux и Windows используют один пайплайн и project-native адаптеры:

| Runtime | Linux | Windows |
|---|---|---|
| Python / pytest | `.venv/bin/python` | `.venv/Scripts/python.exe` |
| Maven / JUnit 5 | `mvnw` | `mvnw.cmd` |
| Gradle / JUnit 5 | `gradlew` | `gradlew.bat` |

Автоматическое обнаружение выбирает runtime текущей ОС. Linux venv может содержать
стандартный interpreter symlink: baseline связывает `pyvenv.cfg`, целевой interpreter
и его bytes. Произвольные symlink wrappers и выходы пути за module запрещены. На Linux
interpreter/wrapper должен уже иметь executable permission; пайплайн её не изменяет.
После переноса проекта на другую ОС создайте её venv и подтвердите изменение runtime
в существующем `.skillsrc`; старый attempt с прежним baseline не переиспользуется.

Пакет переносится в проект целиком, но автоматическое выполнение ограничено этими
адаптерами и имеющимся project setup. Для другого языка/framework сохраняйте явный
manual/blocker gap; наличие Python tools в пакете само по себе не делает такой проект
исполняемым. `coverage=FULL` означает покрытие автоматизацией выбранного canonical
документа, а не доказательство исчерпывающего покрытия произвольной функциональности.
Для полноты reviewer сравнивает исходные требования, сценарии и наблюдаемые проверки;
нерешённые пробелы и manual-проверки должны оставаться видны в результате.

Если `.skillsrc` отсутствует, scanner предлагает и атомарно создаёт его. Существующий
valid файл — authoritative user configuration и молча не перезаписывается. Значимый
drift требует `WAITING_FOR_INPUT`; подтверждённая замена сохраняет прежние bytes как
evidence.

Каждый attempt выбирает ровно один exact module, включая nested module. Module cwd,
test root, wrapper/interpreter, adapter ID, build profile и typed adapter parameters
принадлежат этому attempt. Cross-module orchestration в pilot отсутствует.

## 4. Durable run

До scan или model call controller создаёт:

```text
<project>/.pilot-runs/<run_id>/
  run-authorization-receipt.json
  run-manifest.json
  events/
  attempts/
```

Все JSON artifacts публикуются атомарно, читаются обратно и связываются digest.
Один run получает одну project/module identity и последовательную append-only lineage
child attempts. Одновременно разрешён максимум один nonterminal attempt.

`WAITING_FOR_INPUT`, `WAITING_FOR_MODEL` и interruption между model stages можно
продолжить из новой CLI session после snapshot/config validation. Изменение
requirements, module или policy создаёт child attempt. Terminal attempt не меняется.

Если процесс был запущен, но authoritative framework result отсутствует, результат —
`UNKNOWN`; автоматический повтор запрещён. Новый execution retry возможен только в
explicit child attempt после доказанной остановки прежнего process scope.

Для чувствительных параметров pytest задавайте безопасные явные `ids`: значения
параметров могут попасть в имена тестов в JUnit. Отчёт с обнаруженной сигнатурой
токена в имени, классе или пути не сохраняется; runner завершает такой execution
как `UNKNOWN` с причиной `JUNIT_INVALID` и очищает чувствительный вывод.

## 5. Canonical и reviewer

Requirements детерминированно разбиваются на batches из разрешённых source roots.
Каждый source requirement получает provenance и digest, затем many-to-many mapping к
canonical requirements, cases, steps, assertions, symbols и execution evidence.

Candidate revision 1 немедленно публикуется как `UNREVIEWED` и читается обратно.
После этого открывается одна fresh role-isolated reviewer session для полного canonical
branch. Внутри неё допустимы bounded C-lite evidence requests, но:

- per-batch/hierarchical/multiple-authoritative reviewers запрещены;
- successful session содержит ровно один authoritative verdict;
- terminal pre-verdict abort, включая `REVIEW_CONTEXT_LIMIT`, содержит ноль;
- generator dialogue/reasoning reviewer не получает;
- без host isolation evidence записывается `independence_unverified`, acceptance
  запрещён.

Canonical human fields пишутся по-русски. JSON остаётся единственным semantic source;
HTML и `zephyr-scale-step-row-24-v4` CSV — derived exports.

## 6. `cases-only-v1`

Профиль даёт draft/artifact-only canonical cases: verification=NOT_APPLICABLE, accepted=false, полный pipeline exit не 0. Live PASS и accepted=true только у local-pilot-v1. Для него:

```text
materialization = NOT_APPLICABLE
execution       = NOT_APPLICABLE
dispositions    = NOT_APPLICABLE
verification    = NOT_APPLICABLE
```

Запрос этого профиля не запускает project code, plugins или lifecycle hooks.

## 7. `local-pilot-v1`

После accepted canonical pipeline:

1. создаёт automation revision 1;
2. выполняет отдельный static review;
3. при correction допускает ровно одну полную revision 2 и второй review;
4. формирует complete generated file set;
5. материализует каждый pipeline-owned file и пишет receipt;
6. запускает exact reviewed targets закрытым adapter;
7. строит execution trace и disposition всего generated delta;
8. выполняет finalization и terminal transition.

Execution работает в доверенном обычном проекте: pytest plugins, conftest, Maven/Gradle
plugins и lifecycle hooks могут выполнять код. Run-scoped authorization относится
только к текущему явному запросу. Никакого постоянного trust store, clone или sandbox
нет.

Pipeline не принимает shell strings или `argv_template`. Команду строит только один из
closed adapters:

- `pytest:selected-symbols-v1`;
- `maven-wrapper:selected-symbols-v1`;
- `gradle-wrapper:selected-symbols-v1`.

## 8. Disposition и finalization

Физический порядок неизменяем:

```text
materialization -> execution -> execution trace -> retain/cleanup decision
-> disposition receipts -> pre-finalization trace -> finalization verification
-> finalization receipt readback -> terminal result -> derived terminal trace
-> terminal event
```

Для всего generated file set:

- `PASS` + valid trace/path/digests: `RETAINED` до finalization;
- `FAIL`/`NOT_RUNNABLE`: byte-identical pipeline-owned files получают `CLEANED`;
- `UNKNOWN`: unchanged file получает `PRESERVED_EXECUTION_UNKNOWN`, изменённый —
  `PRESERVED_CONTENT_CONFLICT`; cleanup запрещён;
- partial materialization: execution не начинается, неизменённые созданные файлы
  очищаются, остальные получают `NOT_MATERIALIZED`.

Для `FAIL` cleanup следует только после readback execution receipt, где остаются
exact generated bytes, native report, bounded scrubbed output, exit и structured result.
Valid process-bound report с `tests=0` даёт `NO_TESTS_COLLECTED`, `FAIL`, exit `1`;
missing/corrupt/unbound report, timeout и OS error дают `UNKNOWN`, exit `2`.

Если безопасный cleanup невозможен, файл сохраняется с точной причиной и
`accepted=false`. Terminal transition требует completed/read-back finalization receipt,
но receipt может иметь `valid=false`; тогда terminal reason — `FINALIZATION_INVALID`.

## 9. Как читать результат

Не сводите результат к одному слову. Проверяйте вместе:

```text
attempt_state  completion  verification  coverage  reason_code  accepted
```

Поля completion/verification/coverage могут отсутствовать, пока факт не установлен.
`COMPLETE + FAIL` допустим. `EXECUTION_UNKNOWN` означает
`TERMINAL + PARTIAL + UNKNOWN + accepted=false`.

Exit projection:

- `0` — accepted terminal;
- `1` — trustworthy terminal, но unaccepted;
- `2` — controller error, unreliable closure, UNKNOWN, NOT_RUNNABLE или FATAL;
- `3` — waiting for input/model.

## 10. Проверка и release identity

```powershell
python -m tools.contract_check --root . --full
python -m tools.render_contract_docs --root . --check
python -m pytest -q
python -m tools.doctor --root .
python -m tools.ci_gate --root .
```

Это maintainer/release-команды, а не шаги обычного production run. Runtime не запускает
полный repository test suite, `ci_gate` или release eval автоматически.

`release/manifest.json` — persisted machine authority для package version, exact runtime
registry/digest, pipeline contract, profiles, adapters и release-eval suite. Он исключён
из собственного runtime digest, чтобы не создавать self-cycle.

Текущий package state — `implemented_unverified`. Claim
`core-pilot-ready for <exact verified tuple>` разрешён только после одного smoke и
трёх fresh runs каждого critical scenario. Instability или protocol violation навсегда
блокирует readiness текущей campaign; evaluator сохраняет append-only receipt в
`.pilot-runs/release-eval-ledger`, и следующая clean campaign автоматически требует
пять fresh runs каждого critical scenario. `--predecessor-evaluation` нужен только для
импорта валидного внешнего predecessor; отсутствие флага не сбрасывает уже записанную
эскалацию.
Эта adaptive campaign запускается отдельно для release qualification; production run
не вызывает её и не повторяет model stages ради квалификации.
В обычном run generator записывает `MODEL_RESPONSE_RECEIVED.transport_attempts=1|2`;
`2` означает единственный внутренний transport/schema retry. У остальных stages это
поле равно `1`, а C-lite cardinality читается из evidence pairs reviewer ledger.

Company runner, production rollback и реальный Zephyr tenant round-trip для core pilot
остаются `N/A`.
