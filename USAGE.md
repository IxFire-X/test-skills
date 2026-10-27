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

Что исключается по каталогу (`tools/scan_rules.py`):

- везде — VCS (`.git`), кэши (`__pycache__`, `.pytest_cache`…), зависимости
  (`node_modules`, `.venv`, `venv`, `site-packages`, `.tox`), инструменты и каталоги агентов
  CLI (`.idea`, `.vscode`, `.tools`, `.worktrees`, `.gigacode`, `.claude`, `.codex`, `.qwen`,
  `.gemini`);
- только вне дерева `src/` — имена сборки и зависимостей, которые бывают и именами пакетов
  (`build`, `target`, `dist`, `out`, `coverage`, `htmlcov`, `generated`, `generated-sources`,
  `test-results`, `vendor`), и каталоги `secrets`/`credentials`; внутри `src/…` это код, и
  секреты в нём ищут правила по содержимому.

Секретное имя файла (`token`, `secret`, `password`, `api-key`…) исключает только
не-исходники (конфиги, ключи, текст); исходник (`.java`, `.kt`, `.py`, `.ts`, `.js`, `.go`,
`.cs`…) проверяется по содержимому, и файл с ключом или паролем выпадает целиком.

Вывод `scan` и `status` несёт `exclusion_summary`: число исключений по причинам и правилам,
до 10 путей на причину и `suspicious` — то, что стоит проверить. Лишнее исключение по имени
каталога возвращает `.skillsrc` модуля:

```yaml
modules:
  - id: api
    paths:
      include: ["build/generated-api/**"]   # glob от корня проекта, без .. и абсолютных путей
```

`include` не возвращает VCS, инструменты и каталоги агентов и не обходит правила по
файлу: бинарные файлы и файлы с секретным именем или содержимым остаются исключёнными.
Каталог, в который glob только ведёт (`build/` для `build/generated-api/**`), — проход:
соседние каталоги и файлы в нём (`build/reports`, `build/tmp`) остаются исключёнными.
Путь до исключённого каталога пишется в glob буквально от корня проекта: glob, начинающийся
с `*` или `**`, внутрь исключённого каталога не ведёт.

## 3. `.skillsrc`

Linux и Windows используют один пайплайн и project-native адаптеры:

| Runtime | Linux | Windows |
|---|---|---|
| Python / pytest | `.venv/bin/python` | `.venv/Scripts/python.exe` |
| Maven Wrapper / JUnit 5 | `mvnw` | `mvnw.cmd` |
| Системный Maven / JUnit 5 | `mvn` в PATH | `mvn.cmd` в PATH |
| Gradle / JUnit 5 | `gradlew` | `gradlew.bat` |

Автоматическое обнаружение выбирает runtime текущей ОС. Linux venv может содержать
стандартный interpreter symlink: baseline связывает `pyvenv.cfg`, целевой interpreter
и его bytes. Произвольные symlink wrappers и выходы пути за module запрещены. На Linux
interpreter/wrapper должен уже иметь executable permission; пайплайн её не изменяет.
После переноса проекта на другую ОС создайте её venv и подтвердите изменение runtime
в существующем `.skillsrc`; старый attempt с прежним baseline не переиспользуется.

При создании `.skillsrc` для Maven сначала выбирается пригодный wrapper, затем
системный `mvn` из PATH. Для системного варианта используется
`maven:selected-symbols-v1` и поле `executable`; разрешённый абсолютный путь и bytes
launcher связываются с baseline. Существующая явная конфигурация не переключается
молча. Оба Maven-адаптера запускают фазу `test` и читают Surefire; маршрут
Failsafe/`verify` ими не реализован.

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
test root, wrapper/interpreter/executable, adapter ID, build profile и typed adapter parameters
принадлежат этому attempt. Cross-module orchestration в pilot отсутствует.

**Шаблон ID требований** (`.skillsrc` `requirements.id_pattern`). Строка, начинающаяся с ID
проекта, открывает отдельное требование, как встроенные `REQ-`/`AC-`: для нумерованных
спецификаций — `requirements: {id_pattern: '\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\s'}`. Без шаблона
разбиение прежнее.

**Бюджет шага модели** (`.skillsrc` `limits.step_input_bytes`, по умолчанию 500 000 Б).
`python -m tools.step_size --project <abs> --files <файлы> [--role <роль>]` или
`--run <run_id> --stage context-marker` оценивает вход шага (байты и токены при 0,24 ток/Б) до
вызова модели. Не влезает — предупреждение `STEP_OVER_BUDGET` с советом сузить контекст, не стоп
(код 0). С `--run` шаг (`--step`) и байты пишутся в `step-sizes.jsonl` прогона; `run_pipeline report`
показывает их и предупреждение. Ответ каждого вызова модели тоже меряется: `output_bytes` в результате
публикации и в отчёте; больше `limits.step_output_warn_bytes` (по умолчанию 128 000 Б) — предупреждение
`STEP_OUTPUT_LARGE`, только замер, деление на пачки выключено.

**Вид для ревью.** Ревьюер читает не полный канон, а компактный вид:
`python -m tools.review_view --project <abs> --run <run_id> --candidate <r1.json>` пишет
`review-views/<sha>.md` — требования, кейсы, шаги, данные, ожидания и проверки одной строкой
(ночной канон 52 кейса: 733 КБ → 240 КБ). Машинную часть проверяет код; `CV_DATA_MISMATCH` —
подсказка: значения входов шага не названы в его действии и данных. Исправление ревьюер отдаёт
списком точных правок `successor_edits`; `python -m tools.review_edits --candidate <r1.json>
--edits <файл>` проверяет их и печатает ссылку `effective` для отчёта.

**Разметка частями.** Если выбранный контекст разбит на несколько батчей (`.skillsrc`
`limits.context_batch_bytes`, по умолчанию 256 КиБ), разметка идёт частями:
`context-marker:requirements` по документам, по вызову на батч и слияние кодом в
`context-marker:baseline` (`python -m tools.context_marker_parts plan|merge --run-root <run>`). Журнал
пересчитывает слияние при записи и чтении; генератор и ревью читают тот же baseline.

**Документ задачи.** Файл из `--docs`, лежащий в inventory модуля, всегда входит в выбор
контекста — и с `--target`; файлы других задач в каталогах `to_do` при заданном `--docs` в
контекст не идут. Документ вне inventory модуля (модуль-подкаталог, правило исключения) —
стоп `TASK_DOC_OUTSIDE_CONTEXT`; без `--docs` уходят все документы `to_do`, и scan об этом
предупреждает. `context_summary` вывода scan: файлы кода, документы задачи, пропущенные файлы
других задач. Продолжение после `/clear` — «продолжай test-skills <run_id>», дальше
`run_pipeline status` и его `next_step`.

**Закрытие.** `python -m tools.run_pipeline exec --project <abs> --module <ID> --run <id>` исполняет
тесты и закрывает попытку; квитанции берёт из журнала сам (явные пути — необязательная сверка).
`python -m tools.run_pipeline finish --project <abs> --run <id>` закрывает попытку без исполнения
(cases-only, ранний отрицательный исход ревью, окончательный отказ автоматизации — отвергнута последняя допустимая ревизия или BLOCKED при блокерах в каноне) и уже закрытую — идемпотентно.

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

- ревью без свежего контекста принимается только в прогоне, созданном с
  `scan --accept-self-review` (явное согласие человека); итог помечается
  `review_independence: "SELF"`, и local-pilot `PASS` с ним может быть `accepted=true`.
  Отсутствие поля значит «не принято через SELF», а не доказательство изоляции: факт изоляции —
  `evidence.reviewer_isolation_state`. Без флага такое ревью закрывается `INDEPENDENCE_UNVERIFIED`;
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
- `maven:selected-symbols-v1`;
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
- `FAIL` (карантин, по умолчанию): упавшие методы — те, что exec отметил FAILED/ERROR
  в своём отчёте, — получают одну строку `@org.junit.jupiter.api.Disabled("test-skills
  quarantine: <ASSERT-… или FAIL> <первая строка сообщения>")` или
  `@pytest.mark.xfail(strict=True, reason="…")`; файл с ними — `QUARANTINED` (новый digest
  в receipt), файл без упавших — `RETAINED`. Если упали все методы, файл остаётся, помечен
  каждый метод. Правятся только byte-identical pipeline-owned файлы, иначе — `PRESERVED_*`.
  Если отказ нельзя однозначно сопоставить с методом, файлы очищаются (`CLEANED`,
  `QUARANTINE_UNRESOLVED`). `finish`/`status` выводят `quarantine` (файл, метод, ref,
  сообщение). Приёмка не меняется: `FAIL` даёт `accepted=false`. Прежняя очистка —
  `scan --disposition-policy cleanup` или `"disposition_policy": "cleanup"` в `.skillsrc`;
  выбор фиксируется в authorization receipt на scan, флаг сильнее `.skillsrc`;
- `NOT_RUNNABLE` (и `FAIL` при `cleanup`): byte-identical pipeline-owned files получают `CLEANED`;
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
В обычном run каждая model stage записывает `MODEL_RESPONSE_RECEIVED.transport_attempts=1|2`;
`2` означает единственный повтор после ошибки схемы или валидации ответа (по `retry_prompt`
из `validate_artifact`). C-lite cardinality читается из evidence pairs reviewer ledger.

Отчёт прогона, который можно вынести с работы: `python -m tools.run_pipeline report --project <abs>
--run <id>` пишет в каталог прогона `report.md`, `report.json` и `questions-for-analyst.md`. В отчёте
шаги со временем (часы машины, не проверяются), длительность и размер входа каждого вызова модели
против бюджета шага, `transport_attempts`, коды остановок, числа, карантин (файл, метод, ref) и
окружение — без кода, текста спеки, кейсов и ответов модели. `questions-for-analyst.md` также
появляется рядом с опубликованным набором кейсов.

Zephyr без ручных правок (FaT-7): положите в проект CSV-экспорт Zephyr Scale с заглушками, где заполнены
«АС», «Вид тестирования», «Команда», «Приоритет теста», «Статус», и укажите путь в `.skillsrc`:
`"projection": {"zephyr": {"template": "zephyr/stubs.csv"}}`. После закрытия попытки
`python -m tools.zephyr_template_export --project <abs> --run <id> --cases <…zephyr-scale.csv>` пишет рядом
`<doc>.r<N>.zephyr-import.csv`: значения из первой заполненной заглушки, лишние колонки шаблона в конце,
разделитель как в шаблоне («,» или «;»), UTF-8 с BOM. «Автоматизирован» (если в шаблоне да/нет) — «да»
только для кейса с кодом в принятой автоматизации. Шаблон не в UTF-8 — ошибка с подсказкой до экспорта.
Сам набор кейсов и его квитанция не меняются; в прогоне остаются только путь и digest шаблона.

Company runner, production rollback и реальный Zephyr tenant round-trip для core pilot
остаются `N/A`.
