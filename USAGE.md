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
credentials и не предоставляет standalone `pipeline run`. Ход запуска ведёт драйвер
`tools.pipeline_driver` (раздел 4): он выполняет детерминированные шаги и выдаёт модели
по одной задаче, но сам модель не вызывает.

Пример запроса CLI:

```text
Выполни .tools/test-skills/skills/orchestrate/SKILL.md. Подготовь и независимо проверь тест-кейсы
для <область требований> в этом проекте. Создай функциональные автотесты, проведи их ревью и
запусти прошедшие ревью тесты средствами проекта. Разрешаю такое выполнение
только в рамках этого запуска.
```

В примере пакет находится в `.tools/test-skills`; при другом расположении замените
путь. Для тест-кейсов достаточно попросить подготовить и проверить кейсы: такой запрос
не разрешает запуск кода. Профили — внутренняя деталь; controller выводит применимый
профиль из запроса и состояния проекта.

## 2. Что указать

Обязательно:

- область требований: один или несколько документов, все актуальные OpenSpec specs
  либо выбранный change со связанной регрессией;
- открытый рабочий проект в совместимой model-enabled CLI.

Controller определяет project root, требования, модуль, Python для инструментов и
проектные настройки по доступным данным. При реальной неоднозначности или недостающем
обязательном входе он задаёт уточняющий вопрос и переходит в `WAITING_FOR_INPUT`.
Факты, которые можно прочитать из проекта, controller устанавливает сам.

`--target` или allowlist могут сузить read-only inventory. `.git`, dependencies,
build outputs, binaries, generated artifacts и потенциальные secrets не передаются
модели. Inventory учитывает `.gitignore` (`git ls-files -co --exclude-standard`; если
git недоступен — обычный обход файлов). По умолчанию также исключены `.gradle`,
`.kotlin`, `.idea`, `.vscode`, отчёты `coverage*` и `*.log`; каталоги `build`,
`generated` и `coverage` с исходным кодом остаются.

По имени как секреты исключаются только `.env*`, `*.pem`, `*.key`, `*.p12`, `*.pfx`,
`*.kdbx`, `id_*` и каталоги `secrets/`, `credentials/`. По содержимому — сигнатуры
токенов, PEM-блок приватного ключа и присваивание строкового литерала от 16 символов.
Файл кода или настроек с совпадением исключается целиком; если он необходим, дайте
sanitized source, safe fixture или opaque runtime handle. В документах и спецификациях
совпавшие строки заменяются на `[REDACTED:<rule>]`, файл остаётся входом, а номера
замаскированных строк записываются в inventory в поле `redactions`.

Файл, который не помещается в бюджет контекста, пропускается явно: он попадает в `gaps`
квитанции выбора контекста и в `context_gaps` ответа `scan`.

## 3. `.skillsrc`

Linux и Windows используют один пайплайн и project-native адаптеры:

| Runtime | Имя в `.skillsrc` | Linux | Windows |
|---|---|---|---|
| Python / pytest | `python` | `.venv/bin/python` | `.venv/Scripts/python.exe` |
| Maven Wrapper / JUnit 5 | `mvnw` | `mvnw` | `mvnw.cmd` |
| Системный Maven / JUnit 5 | `mvn` | `mvn` в PATH | `mvn.cmd` в PATH |
| Gradle / JUnit 5 | `gradlew` | `gradlew` | `gradlew.bat` |

`.skillsrc` хранит логическое имя, а путь под текущую ОС разрешается при запуске; для
`python` сначала проверяется `.venv`, затем `venv` модуля. Старые файлы с путями
конкретной ОС читаются как раньше. Необязательные поля раздела `test`:
`timeout_seconds` — таймаут тестового процесса от 1 до 86400 секунд (по умолчанию 600);
`build_root` — корень сборки многомодульного Maven/Gradle-проекта относительно проекта
(по умолчанию module root). `build_profile: "default"` означает, что профиль не объявлен.
Необязательный раздел `limits` задаёт лимиты в байтах: `context_batch_bytes`,
`docs_file_bytes`, `docs_total_bytes`. Linux venv может содержать
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

## 4. Ход запуска: драйвер

Основной путь — детерминированный драйвер `tools.pipeline_driver`. Он выполняет все
шаги, где не нужна модель (scan, inventory, baseline, attempt, батчи, сборку,
публикацию, ведение ревью, выбор редакции, материализацию, запуск тестов, закрытие), и
останавливается, только когда должен ответить человек или модель. Драйвер не вызывает
модель сам: задачу выполняет CLI по `skills/orchestrate/SKILL.md`.

Цикл: `next` → выполнить задачу по её Skill → `submit` → повторять до `done` или
`ask_user`. Ответ `submit` — уже следующая задача, отдельный `next` между ними не нужен.

Команды (cwd — корень пакета, `--project` — абсолютный путь проекта):

```powershell
# первый вызов: создаёт run (внутри вызывает scan) и доходит до первой задачи
python -m tools.pipeline_driver next --project "$project" --profile cases-only-v1 `
  --docs docs/feature.md --subject "Название документа"

# ответ на задачу
python -m tools.pipeline_driver submit --project "$project" --run "$runId" --task-id "$taskId"

# продолжение после перерыва и чтение состояния
python -m tools.pipeline_driver next --project "$project" --run "$runId"
python -m tools.pipeline_driver status --project "$project" --run "$runId"
```

Первый `next` требует `--profile cases-only-v1|local-pilot-v1` и хотя бы один `--docs`
(путь внутри проекта, флаг можно повторять). Необязательные флаги: `--subject`,
`--module <ID>`, `--target`, `--document-id`, `--model-id`, `--host-cli`,
`--host-cli-version`, `--host-settings`, `--reviewer-isolation fresh|none`,
`--review-input-bytes N`, `--review-reserve-bytes N`. `run_id` берётся из первого ответа.

Каждый вызов печатает один JSON-объект:

| `action` | Основные поля | Что делать |
|---|---|---|
| `llm` | `task_id`, `stage`, `skill_path`, `inputs`, `output_path`, `schema_path`, `instructions` | Прочитать `skill_path` и все файлы из `inputs`, записать ответ в `output_path` по `schema_path`, вызвать `submit` |
| `ask_user` | `task_id`, `question`, `options` (`value`, `label`) | Задать вопрос пользователю и вызвать `submit ... --answer <value>` |
| `done` | `result` | Остановиться и передать результат пользователю |
| `error` | `code`, `message` | Прочитать причину; не обходить ошибку ручными вызовами |

Что делает `submit`:

- проверяет ответ по схеме задачи и правилам роли. Сам вычисляет все digests, порядок
  массивов, `display_order` и служебные поля, публикует артефакт и возвращает следующую
  задачу. Модель не считает SHA-256, не сортирует массивы и не переносит пути между
  шагами;
- невалидный ответ не публикует: возвращает ту же задачу со `"status": "rejected"` и
  списком `errors` (`path`, `code`, `message`). Исправьте файл ответа и вызовите
  `submit` снова;
- повторный `submit` уже принятой задачи безопасен: он возвращает текущую задачу;
- `--output <path>` задаёт другой файл ответа; по умолчанию читается `output_path`;
- для задач ревью `--failed TRANSPORT|CONTENT --reason "..."` фиксирует, что пригодной
  оценки нет (вызов не дошёл или ответ непригоден). Драйвер выдаёт ту же часть ревью
  новой задачей — не более трёх вызовов на часть.

Задачи ревью (`tc-reviewer:*`, `autotest-reviewer:*`) имеют
`requires_fresh_context=true`: каждую выполняет отдельный вызов в свежем контексте без
истории генерации.

Вопросы `ask_user`:

- `reviewer-isolation` — может ли хост выполнять каждую часть ревью в свежем
  изолированном контексте. При ответе `none` драйвер останавливается с
  `REVIEWER_ISOLATION_UNAVAILABLE`: кандидат остаётся `UNREVIEWED`. Вопрос не задаётся,
  если ответ передан флагом `--reviewer-isolation` при создании run;
- `regenerate-after-gate` — после `NOT_RUNNABLE/GENERATED_TEST_INVALID`: `regenerate`
  создаёт одну дочернюю попытку, `stop` завершает с текущим результатом. Вопрос
  задаётся один раз.

Коды завершения команды: `3` — драйвер ждёт модель или человека (`llm`, `ask_user`);
при `done` — код результата (`0`, `1` или `2`, см. раздел 10); `2` — ошибка (`error`).
`status` только читает состояние и завершается с кодом `0`.

Рабочие файлы драйвера лежат рядом с run, в `<project>/.pilot-runs/<run_id>.driver/`:

```text
config.json        параметры первого вызова и ответы на вопросы
tasks/             выданные задачи и их схемы
inputs/            входы задач (уже маскированные байты)
outputs/           ответы модели
artifacts/         копии собранного кандидата, квитанций и вывода exec
carriers/          входные файлы для exec (только local-pilot-v1)
candidate-bundle/  опубликованный набор кейсов; у дочерней попытки —
                   candidate-bundle-<начало attempt_id>/
```

Источник истины остаётся в `.pilot-runs/<run_id>/` (журнал и квитанции). Не запускайте
параллельно две команды драйвера для одного run и не удаляйте `.lock` вручную.

Ограничения:

- драйвер продолжает только run, созданный его же `next`; run, начатый ручными
  командами, он не подхватывает;
- один модуль на run;
- context-marker, генератор кейсов и генератор автотестов получают весь выбранный
  контекст проекта одним входом, даже если `scan` разбил его на несколько частей.
  Поле `input_bytes` задачи показывает общий размер входов: если он больше возможностей
  модели, вход не обрезается — запуск нужно остановить и сузить контекст (`--target`)
  или взять модель с большим окном;
- драйвер не вызывает модель и не проверяет, что ревью действительно выполнено в
  свежем контексте: это сообщает хост;
- драйвер покрыт тестами пакета, но не проверен на Windows и на живой модели.
  Состояние релиза остаётся `implemented_unverified`, ready tuple — `null`.

Прежние команды остаются рабочими и нужны для диагностики и ручного хода:
`tools.run_pipeline` (`scan`, `status`, `exec`, `rerun-retained`),
`tools.orchestrate_test_case_revision` (`prepare-review`, `open-part`, `submit-part`,
`fail-part`, `finish-review` и другие), `tools.review_budget`. Ручная
последовательность описана в
[skills/orchestrate/references/manual-sequence.md](skills/orchestrate/references/manual-sequence.md).
В разделах ниже эти команды упоминаются как внутренние шаги, которые при обычном
запуске вызывает драйвер.

## 5. Durable run

Первый `next` драйвера вызывает прежнюю команду `run_pipeline scan`. Она сама создаёт
новый run (флага `--run` у неё нет) до inventory и первого model call:

```text
<project>/.pilot-runs/<run_id>/
  run-authorization-receipt.json
  run-manifest.json
  events/
  attempts/
  .lock
```

Рядом драйвер создаёт каталог рабочих файлов `<project>/.pilot-runs/<run_id>.driver/`.

`.lock` — межпроцессная блокировка run: писатели и читатели одного run выполняются по
очереди. Чтение (`status`) ничего не восстанавливает и не удаляет; незавершённую запись
после сбоя доводит следующий писатель. Если блокировка занята дольше 120 секунд,
команда завершается ошибкой `run is locked by another process`.

Все JSON artifacts публикуются атомарно, читаются обратно и связываются digest.
Один run получает одну project/module identity и последовательную append-only lineage
child attempts. Одновременно разрешён максимум один nonterminal attempt.

`WAITING_FOR_INPUT`, `WAITING_FOR_MODEL` и interruption между model stages можно
продолжить из новой CLI session после snapshot/config validation: вызовите
`python -m tools.pipeline_driver next --project <abs> --run <run_id>`. Драйвер вернёт
ту же незакрытую задачу или продолжит с последнего подтверждённого шага. Изменение
requirements, module или policy создаёт child attempt. Terminal attempt не меняется.
После `WAITING_FOR_INPUT`/`WAITING_FOR_MODEL` attempt снова становится `ACTIVE`, как
только записан следующий шаг работы.

Если процесс был запущен, но authoritative framework result отсутствует, результат —
`UNKNOWN`; автоматический повтор запрещён. Новый execution retry возможен только в
explicit child attempt после доказанной остановки прежнего process scope. Доказательство
выдаётся при остановке по таймауту: на Windows процесс запускается в Job Object, и
proof `WINDOWS_JOB_TERMINATED` означает завершение всего Job; на Linux proof
`POSIX_PROCESS_GROUP` выдаётся, только когда живых процессов нет ни в группе, ни среди
ушедших из неё через `setsid`. Если остановку доказать не удалось, retry запрещён.

Для чувствительных параметров pytest задавайте безопасные явные `ids`: значения
параметров могут попасть в имена тестов в JUnit. Отчёт с обнаруженной сигнатурой
токена в имени, классе или пути не сохраняется; runner завершает такой execution
как `UNKNOWN` с причиной `JUNIT_INVALID` и очищает чувствительный вывод.

## 6. Canonical и reviewer

Requirements детерминированно разбиваются на batches из разрешённых source roots.
Каждый source requirement получает provenance и digest, затем many-to-many mapping к
canonical requirements, cases, steps, assertions, symbols и execution evidence.

Candidate revision 1 немедленно публикуется как `UNREVIEWED` и читается обратно.
После этого выполняется одно логическое ревью по частям: controller замораживает
исходные требования, контекст и candidate в одном snapshot и делит проверку на части.
Каждую часть проверяет отдельный fresh role-isolated вызов reviewer со своим host
evidence; событий `EVIDENCE_REQUESTED`/`EVIDENCE_PROVIDED` нет. Правила:

- итог собирает controller: одно ревью даёт ровно один authoritative verdict;
- terminal pre-verdict abort, включая `REVIEW_CONTEXT_LIMIT`,
  `REVIEW_TRANSPORT_FAILED` и `REVIEW_INCOMPLETE`, содержит ноль;
- часть без пригодной оценки можно открыть заново новым вызовом — не более трёх
  вызовов на часть; сбой вызова фиксирует `submit --failed TRANSPORT|CONTENT --reason`
  драйвера (при ручном ходе — команда `fail-part` с
  `--failure-class TRANSPORT|CONTENT`);
- содержимое, которое повторяется внутри одной части, передаётся один раз; у повтора
  вместо `content` стоит ссылка `content_ref` на первое вхождение;
- generator dialogue/reasoning reviewer не получает;
- без host isolation evidence каждой части записывается `independence_unverified`,
  acceptance запрещён. Драйвер при ответе `none` на вопрос `reviewer-isolation` ревью
  не начинает и останавливается с `REVIEWER_ISOLATION_UNAVAILABLE`.

При `AUTO_FIX_APPLIED` revision 2 строит controller из механических исправлений,
предложенных в частях ревью; reviewer её не пишет.

Лимит части ревью задаётся при создании run флагами драйвера `--review-input-bytes`
и `--review-reserve-bytes` (по умолчанию 200000 и 20000 байт). Значения можно получить
из лимита модели в токенах:
`python -m tools.review_budget --context-tokens N --response-tokens M --sample <файл>`
печатает `input_byte_budget` и `response_reserve_bytes`. При ручном ходе те же значения
передаются в `prepare-review`.

Canonical human fields пишутся по-русски. JSON остаётся единственным semantic source;
HTML, Markdown и CSV — derived exports. Профиль CSV по умолчанию —
`zephyr-scale-step-row-24-v5` (только человеческие поля шага); прежний v4 включается
явным `--csv-profile zephyr-scale-step-row-24-v4`.

## 7. `cases-only-v1`

Профиль даёт draft/artifact-only canonical cases: verification=NOT_APPLICABLE, accepted=false, полный pipeline exit не 0: валидное завершение даёт exit 1, а FATAL, `FINALIZATION_INVALID`, невалидный trace или ненадёжное закрытие — exit 2. Live PASS и accepted=true только у local-pilot-v1. Для него:

```text
materialization = NOT_APPLICABLE
execution       = NOT_APPLICABLE
dispositions    = NOT_APPLICABLE
verification    = NOT_APPLICABLE
```

Запрос этого профиля не запускает project code, plugins или lifecycle hooks.

## 8. `local-pilot-v1`

После accepted canonical pipeline:

1. создаёт automation revision 1;
2. выполняет отдельный static review;
3. при correction допускает ровно одну полную revision 2 и второй review;
4. формирует complete generated file set;
5. материализует каждый pipeline-owned file и пишет receipt;
6. выполняет шлюз компиляции/сбора и запускает exact reviewed targets закрытым adapter;
7. строит execution trace и disposition всего generated delta;
8. выполняет finalization и terminal transition.

Все эти шаги ведёт драйвер. Модель получает задачи `tc-to-autotest:r1` (и `r2` при
`AUTO_FIX_APPLIED`) и части статического ревью `autotest-reviewer:*`. Если ревью не
приняло автотесты, попытка завершается без записи файлов в проект, с причиной:

- `AUTOMATION_REVIEW_REJECTED` — ревью отклонило автотесты;
- `AUTOMATION_REVISION_BUDGET` — исправления потребовались и после revision 2;
- `AUTOMATION_REVIEW_CONTEXT_LIMIT` — часть ревью не поместилась в лимит;
- `AUTOMATION_REVIEW_TRANSPORT_FAILED` — части ревью не удалось доставить reviewer;
- `AUTOMATION_REVIEW_INCOMPLETE` — часть областей осталась непроверенной.

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

pytest запускается из module root. Maven запускается из корня реактора с
`-Dtest=… -B -ntp test`; для модуля внутри реактора добавляются `-pl <module> -am` и
`-Dsurefire.failIfNoSpecifiedTests=false`, а `-P<profile>` — только для объявленного
профиля. Gradle запускается из корня сборки:
`:<path>:cleanTest :<path>:test --tests <FQN> --no-daemon`.

Перед основным запуском выполняется шлюз компиляции/сбора: pytest `--collect-only` по
выбранным nodeid, Maven `test-compile`, Gradle `:<path>:testClasses`. Провал шлюза, а
также ошибка компиляции или импорта в основном запуске дают `NOT_RUNNABLE` с причиной
`GENERATED_TEST_INVALID`: сгенерированные файлы убираются, вывод сохраняется в run root.
Исправленная ревизия автоматизации допускается один раз, в child attempt с
`retry_reason=GENERATED_TEST_INVALID`, если прежний attempt не потратил revision 2 в
статическом ревью и сам не был таким child attempt. Вывод `exec` в этом случае содержит
`reason`, `automation_revision`, `automation_revision_allowed` и `regeneration`.
Когда регенерация разрешена, драйвер задаёт вопрос `regenerate-after-gate`; при ответе
`regenerate` он сам создаёт child attempt, в котором кейсы, ревью и автоматизация
проходят заново.
Падение продуктовой проверки — `FAIL`; регенерации после него нет.

Другие причины `NOT_RUNNABLE` после старта: `LAUNCH_FAILED` — процесс не запустился;
`TESTS_DESELECTED` — pytest собрал 0 тестов по явно выбранным nodeid из-за `addopts`,
`-m` или `PYTEST_ADDOPTS` (файлы тестов остаются `RETAINED`).

После запуска с замороженным baseline сравниваются только его входы: изменённый или
удалённый файл — drift, новые файлы (coverage, `.gradle`, логи) drift не считаются.
Execution receipt записывает в `execution.environment_inputs` имена и SHA-256 значений
`PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `MAVEN_OPTS`, `MAVEN_ARGS`, `JAVA_TOOL_OPTIONS`,
`_JAVA_OPTIONS`, `GRADLE_OPTS` и `JAVA_HOME`; сами значения не сохраняются.

## 9. Disposition и finalization

Физический порядок неизменяем:

```text
materialization -> execution -> execution trace -> retain/cleanup decision
-> disposition receipts -> pre-finalization trace -> finalization verification
-> finalization receipt readback -> terminal result -> derived terminal trace
-> terminal event
```

Для всего generated file set:

- `PASS` + valid trace/path/digests: `RETAINED` до finalization;
- `FAIL`/`NOT_RUNNABLE`: byte-identical pipeline-owned files получают `CLEANED`
  (кроме `NOT_RUNNABLE/TESTS_DESELECTED`: файлы остаются `RETAINED`);
- `UNKNOWN`: unchanged file получает `PRESERVED_EXECUTION_UNKNOWN`, изменённый —
  `PRESERVED_CONTENT_CONFLICT`; cleanup запрещён;
- partial materialization: execution не начинается, неизменённые созданные файлы
  очищаются, остальные получают `NOT_MATERIALIZED`.

Для `FAIL` cleanup следует только после readback execution receipt, где остаются
exact generated bytes, native report, bounded scrubbed output, exit и structured result.
Для Maven/Gradle valid process-bound report с `tests=0` даёт `NO_TESTS_COLLECTED`,
`FAIL`, exit `1`; для pytest ноль собранных выбранных тестов — `NOT_RUNNABLE`
(`TESTS_DESELECTED` или `GENERATED_TEST_INVALID`). Missing/corrupt/unbound report
(`JUNIT_MISSING`, `JUNIT_INVALID`), ненулевой exit при зелёном отчёте
(`NONZERO_EXIT_GREEN_REPORT`), timeout и сбой сохранения артефактов
(`ARTIFACT_PERSISTENCE_FAILED`) дают `UNKNOWN`, exit `2`. Процесс, который не удалось
запустить, — `NOT_RUNNABLE/LAUNCH_FAILED`.

Если безопасный cleanup невозможен, файл сохраняется с точной причиной и
`accepted=false`. Terminal transition требует completed/read-back finalization receipt,
но receipt может иметь `valid=false`; тогда terminal reason — `FINALIZATION_INVALID`.

## 10. Как читать результат

Не сводите результат к одному слову. Проверяйте вместе:

```text
attempt_state  completion  verification  coverage  reason_code  accepted
```

Поля completion/verification/coverage могут отсутствовать, пока факт не установлен.
`COMPLETE + FAIL` допустим. `EXECUTION_UNKNOWN` означает
`TERMINAL + PARTIAL + UNKNOWN + accepted=false`.

Exit projection:

- `0` — accepted terminal;
- `1` — trustworthy terminal, но unaccepted (включая валидное завершение
  `cases-only-v1`);
- `2` — controller error, unreliable closure, UNKNOWN, NOT_RUNNABLE, FATAL,
  `FINALIZATION_INVALID` или невалидный trace;
- `3` — waiting for input/model.

Команды драйвера используют те же значения: `3` — выдана задача `llm` или `ask_user`;
при `done` — код результата; `2` — ошибка драйвера (`action: "error"`). Остановка без
terminal result (например, `REVIEWER_ISOLATION_UNAVAILABLE`) тоже даёт `2`. Если scan
при создании run ждёт уточнения (`WAITING_FOR_INPUT`), драйвер возвращает `done` со
`status=stopped` и кодом `3`.

## 11. Проверка и release identity

```powershell
python -m tools.contract_check --root . --full
python -m tools.render_contract_docs --root . --check
python -m pytest -q
python -m tools.doctor --root .
python -m tools.ci_gate --root .
```

`tools.ci_gate` завершается с кодом `2`, если pytest не установлен или проверка не
уложилась в таймаут: `--check-timeout` (по умолчанию 900 секунд на каждую проверку
контрактов) и `--pytest-timeout` (по умолчанию 4 часа).

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
В обычном run каждое `MODEL_RESPONSE_RECEIVED` записывает `transport_attempts` от 1 до
3 — число транспортных попыток получить этот ответ; его передаёт
`submit --transport-attempts` драйвера (при ручном ходе для части ревью —
`submit-part --transport-attempts`). Число вызовов reviewer читается из частей ревью и
их boundary (повторный вызов части имеет stage с суффиксом `-try2` или `-try3`), а не
из evidence pairs: таких событий нет.

Company runner, production rollback и реальный Zephyr tenant round-trip для core pilot
остаются `N/A`.
