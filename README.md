# Test Skills Portable Pilot

**[Знакомство с пайплайном и быстрый старт](QUICKSTART.md).** Что делает пакет, как его подключить, какой запрос дать в рабочей CLI и где открыть результаты. Техническую последовательность выполняет оркестратор; человеку достаточно задать область требований и желаемый результат.

## Презентация и обзор

- [Настоящие тест-кейсы Spring Petclinic: 64 кейса, 290 шагов](docs/examples/petclinic-owner-lifecycle/cases.md) — [HTML, исходный JSON и пояснение к результатам](docs/examples/petclinic-owner-lifecycle/README.md).
- [Подробный справочник всех файлов и схемы взаимодействия модулей](docs/pipeline-file-guide-ru.md).
- [Презентация на русском: 16 слайдов, около 20 минут](docs/portable-testing-pipeline-ru.pptx).
- [Заметки докладчика](docs/presentation-notes-ru.md).
- [Что сделано, структура пакета и причины попробовать](docs/portable-pipeline-overview-ru.md).
- [Результаты большого пилота и границы подтверждённого](docs/pilot-evidence.md).

Оговорка к примеру Petclinic. Прогон 64 автотестов (63 PASS, 1 FAIL) выполнен на более
старом manifest пакета, до перехода на ревью по частям; опубликованная редакция кейсов
после этого автотестами не исполнялась. Нынешняя схема ревью по частям на живой модели,
по данным [ревью от 2026-10-05](docs/review/2026-10-05-pipeline-review.md), проверялась
только на синтетических контролях из 3–4 частей. Пример показывает формат результата и
не доказывает работу текущей версии пакета на большом наборе.

В этом репозитории опубликован переносимый пакет тестирования. Старые версии
сохранены в истории Git.

Портативный skill-pack для проектирования тест-кейсов, независимого ревью,
генерации автотестов, project-native выполнения и проверяемой трассировки.
Пакет не является самостоятельным LLM runner: моделью управляет совместимая CLI,
а Python tools только читают, проверяют, публикуют и исполняют закрытые операции.
Ход запуска ведёт детерминированный драйвер `tools.pipeline_driver`: он выполняет все
шаги, где не нужна модель, и выдаёт модели по одной задаче. Сам драйвер модель не
вызывает.

## Точка входа

Скопируйте пакет целиком в обычный рабочий проект и явно попросите model-enabled
CLI выполнить `skills/orchestrate/SKILL.md` по фактическому пути к пакету, например
`.tools/test-skills/skills/orchestrate/SKILL.md`. Совместимая CLI обязана прочитать exact
Skills, references, schemas и `contracts/pipeline.json`, сохранять и читать обратно
артефакты, создавать отдельный fresh reviewer invocation и уметь продолжить
nonterminal attempt из durable state.

Оркестратор ведёт run циклом драйвера: `next` → выполнить выданную задачу по её
Skill → `submit` → повторять, пока драйвер не вернёт `done` или вопрос пользователю
(`ask_user`). Команды запускаются из корня пакета, `--project` — абсолютный путь
проекта. Первый вызов создаёт run и доходит до первой задачи:

```powershell
python -m tools.pipeline_driver next --project "$project" --profile cases-only-v1 `
  --docs docs/feature.md --subject "Название документа"
```

Обычно эти команды выполняет сама CLI по `skills/orchestrate/SKILL.md`; вручную их
вводить не нужно. Подробности — в [USAGE.md](USAGE.md), раздел 4.

Укажите один или несколько документов требований, все актуальные OpenSpec specs либо
выбранный change со связанной регрессией. Контроллер определит проект и прочитает
доступные входы сам; project, module, path или `--target` помогают определить или
сузить область. Сам путь к проекту не заменяет требований. Модели выбирает пользователь
или его CLI; пакет не требует конкретного провайдера или модели.

Инструментам пакета нужен Python 3.11, 3.12 или 3.13 с установленными зависимостями:
используйте `.tools/test-skills/.venv`, если она есть, иначе доступный настроенный
интерпретатор. Проверка документов не зависит от версии Python: названия HTTP-статусов
берутся из собственной таблицы пакета (`tools/http_reason.py`), а не из стандартной
библиотеки.
Project-native выполнение поддерживает Java/JUnit 5 (Maven Wrapper, системный Maven
или Gradle Wrapper) и Python/pytest; пакет ничего не устанавливает.

## Два профиля

- `cases-only-v1` создаёт и независимо проверяет canonical test cases. Код проекта не
  запускается; materialization и execution имеют `NOT_APPLICABLE`.
- `local-pilot-v1` после accepted canonical и automation review материализует exact
  generated file set и запускает только reviewed targets через project-native
  pytest, Maven Wrapper, системный Maven или Gradle Wrapper.

Явный запрос полного pipeline даёт run-scoped разрешение на такое выполнение только
для текущего run. Простое наличие папки или запрос тест-кейсов код не запускают.
Пайплайн не создаёт clone/worktree/sandbox, не ставит JDK, Python, зависимости или
plugins и не меняет CI, cron, Git history или remote.

## Как идёт run

```text
explicit Skill invocation
  -> run manifest + authorization + RUN_CREATED
  -> read-only inventory -> exact module -> frozen execution baseline
  -> append-only attempt
  -> deterministic requirement batches
  -> canonical candidate -> one authoritative review (fresh invocation на каждую часть)
  -> cases-only finalization
     или
     automation -> static review -> generated delta -> materialization
       -> project-native execution -> trace -> file dispositions
       -> finalization -> terminal result
```

Все шаги этой схемы, кроме ответов модели, выполняет драйвер. Модель получает задачу
(`action: "llm"`), пишет ответ в указанный файл и вызывает `submit`. Контрольные суммы,
порядок массивов, `display_order`, служебные поля и пути между шагами заполняет код.
Прежние команды `tools.run_pipeline` (`scan`, `status`, `exec`, `rerun-retained`),
`tools.orchestrate_test_case_revision` и `tools.review_budget` остаются рабочими — для
диагностики и ручного хода. Драйвер продолжает только run, созданный его же `next`.

Один run навсегда связан с одной exact project/module identity. Он может содержать
последовательную append-only lineage child attempts, но одновременно nonterminal
может быть только один. `WAITING_FOR_INPUT` и `WAITING_FOR_MODEL` продолжаются через
resume; terminal attempt неизменяем. Запись и чтение состояния одного run разными
процессами сериализуются файлом `.pilot-runs/<run_id>/.lock`; если блокировку не удалось
получить за 120 секунд, команда завершается ошибкой `run is locked by another process`.

Canonical JSON — единственный семантический источник. HTML, Markdown и
CSV — derived human projections. Профиль CSV по умолчанию —
`zephyr-scale-step-row-24-v5`; прежний `zephyr-scale-step-row-24-v4` доступен только по
явному `--csv-profile zephyr-scale-step-row-24-v4`. Опциональный
`zephyr-scale-xml-observed-v1` остаётся observed/unverified: реальный Zephyr tenant
import/re-export не доказан.

При публикации кейсов рядом с `<document_id>.r<revision>.html` автоматически
создаётся `<document_id>.r<revision>.md`: цель, предусловия и шаги с данными и
ожидаемыми результатами. Markdown строится из того же JSON и не служит входом
автоматизации. Повторная публикация сверяет его байты и не перезаписывает изменённую
вручную копию. Формат существующих receipts сохранён: `--verify-only` проверяет
JSON/HTML/CSV, а Markdown проверяется при публикации и не входит в receipt.

## Execution и generated files

Executor получает закрытый adapter ID, interpreter/wrapper/executable, build profile
и typed parameters — не shell string. `.skillsrc` хранит логические имена `python`,
`mvnw`, `gradlew`, `mvn`; путь под текущую ОС разрешается при запуске. pytest запускается
из module root, Maven и Gradle — из корня сборки (`test.build_root`, по умолчанию module
root). Таймаут тестового процесса — `test.timeout_seconds`, по умолчанию 600 секунд.

Перед основным запуском выполняется шлюз компиляции/сбора: pytest `--collect-only` по
выбранным тестам, Maven `test-compile`, Gradle `testClasses`. Если сгенерированный тест
не компилируется или не импортируется, результат — `NOT_RUNNABLE` с причиной
`GENERATED_TEST_INVALID`; исправленную ревизию автоматизации можно сгенерировать один раз
в дочерней попытке. Драйвер в этом случае задаёт пользователю вопрос
`regenerate-after-gate`: создать такую попытку или завершить. Падение продуктовой
проверки — `FAIL`; после него тест не перегенерируется и не исправляется.

Если статическое ревью не приняло автотесты, попытка завершается с причиной
`AUTOMATION_REVIEW_REJECTED`, `AUTOMATION_REVISION_BUDGET`,
`AUTOMATION_REVIEW_CONTEXT_LIMIT`, `AUTOMATION_REVIEW_TRANSPORT_FAILED` или
`AUTOMATION_REVIEW_INCOMPLETE`; файлы
тестов в проект не записываются.

Если процесс остановлен по таймауту и авторитетного результата нет, verification —
`UNKNOWN`. Повторный запуск возможен только в явной дочерней попытке и только когда
остановка всех процессов доказана: на Windows — завершением Job Object
(`WINDOWS_JOB_TERMINATED`), на Linux — когда живых процессов не осталось ни в группе
процесса, ни среди ушедших из неё через `setsid` (`POSIX_PROCESS_GROUP`). Без такого
доказательства повтор запрещён.

Generated output — множество файлов, и у каждого есть materialization и disposition
receipt. Основные правила:

- authoritative `PASS` и valid trace позволяют записать pre-finalization
  `RETAINED`; acceptance дополнительно требует valid finalization;
- `FAIL` и `NOT_RUNNABLE` очищают только byte-identical pipeline-owned files;
  исключение — `NOT_RUNNABLE/TESTS_DESELECTED` (настройки pytest проекта исключили
  выбранные тесты): файлы остаются;
- `UNKNOWN` никогда не очищает generated delta;
- partial materialization запрещает execution и безопасно закрывается как
  `PARTIAL/NOT_APPLICABLE`.

## Результат

Результат не является одним старым status. Он содержит независимые оси:

- lifecycle: `ACTIVE`, `WAITING_FOR_INPUT`, `WAITING_FOR_MODEL`, `TERMINAL`;
- completion: `COMPLETE`, `PARTIAL`, `FATAL` или ещё не установлен;
- verification: `PASS`, `FAIL`, `UNKNOWN`, `NOT_RUNNABLE`, `NOT_APPLICABLE` или ещё
  не установлен;
- coverage: `FULL`, `MIXED`, `MANUAL_ONLY` или ещё не установлено;
- terminal `reason_code` и вычисленное `accepted`.

Invalid finalization всё равно завершает attempt, но даёт `accepted=false` и
`FINALIZATION_INVALID`. Фактические verification/coverage при этом не переписываются.

## Секреты и границы

По имени исключаются только явные хранилища секретов: `.env*`, `*.pem`, `*.key`, `*.p12`,
`*.pfx`, `*.kdbx`, `id_*` и каталоги `secrets/`, `credentials/`. По содержимому ищутся
сигнатуры токенов, PEM-блок приватного ключа и присваивание строкового литерала от 16
символов. Файл кода или настроек с таким совпадением целиком исключается из model
context. В документах и спецификациях совпавшие строки заменяются на
`[REDACTED:<rule>]`, а сам файл остаётся входом; модель получает уже замаскированный
текст. Артефакты содержат
только safe labels или opaque runtime handles, никогда raw values или обычный hash
секрета. Окружение хранит только allowlisted safe key IDs/labels без values.

Пайплайн записывает в проект только свои новые generated-test files и собственные
evidence artifacts. Application source, существующие тесты, настройки, lock files и
зависимости не меняются. После очистки в проекте могут остаться побочные следы: пустые
каталоги пакетов, созданные под сгенерированные тесты, и отчёт запуска pytest
`test-results/pytest.xml`. Сборка и тесты проекта также создают свои обычные файлы
(`target`, `build`, `.gradle`, отчёты покрытия, логи).

## Проверка пакета

Linux и Windows — целевые платформы реализации. CI этого репозитория запускает один
и тот же implementation gate на Ubuntu и Windows; он проверяет пакет, а не квалификацию
всех CLI/моделей/проектов. Настройка CI целевого проекта остаётся за его владельцем.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m tools.ci_gate --root .
python -m tools.doctor --root .
```

На Linux используйте `export PYTHONDONTWRITEBYTECODE=1`, затем те же команды Python.
`tools.ci_gate` уже включает contract check, проверку проекций и весь pytest; отдельно
повторять их не требуется. Если pytest не установлен или проверка не уложилась в таймаут
(`--check-timeout`, по умолчанию 900 секунд на каждую проверку контрактов;
`--pytest-timeout`, по умолчанию 4 часа), код завершения — `2`. Сама команда не создаёт CI-сервис или workflow.
Весь этот раздел предназначен для maintainer/release-проверки и автоматически из
обычного production pipeline run не вызывается.

`contracts/pipeline.json` — machine truth. `CONTRACTS.md` и `PIPELINE.md` — generated
projections. `release/manifest.json` связывает package `0.5.0-pilot` с exact runtime
bytes и сейчас честно имеет `implemented_unverified`; readiness допустима только как
`core-pilot-ready for <exact verified tuple>` после внешнего adaptive release eval.
Драйвер `tools.pipeline_driver` покрыт тестами пакета, но не проверен на Windows и на
живой модели; ready tuple остаётся `null`.
Release eval запускается только отдельной явной командой квалификации и не входит в
обычный production pipeline run. Пакет не создаёт и не изменяет CI целевого проекта.

Company runner, production rollback и реальный Zephyr tenant round-trip не входят в
core pilot readiness и остаются `N/A`, пока не появится отдельное evidence.

Подробнее: [USAGE.md](USAGE.md), [HOW-IT-WORKS.md](HOW-IT-WORKS.md) и
[RELEASE.md](RELEASE.md).
