# Режим Б — `local-pilot-v1` (в поставках с режимом Б)


Этот файл — прежний текст `SKILL.md` и контракта оркестрации, который описывает автотесты и запуск: роли `tc-to-autotest` и
`autotest-reviewer`, `tools.run_pipeline materialize` и `ingest` (сборку запускает человек), стенд, карантин. В поставку «только кейсы» он не входит. Агент,
который идёт по `local-pilot-v1`, читает его вместе с `SKILL.md`: места ниже называют раздел `SKILL.md`, к которому относятся.
Профиль `local-pilot-v1` отдельно разрешает человек (запрос полного pipeline в этом прогоне).


## К §0 «Проверь готовность»

Предупреждения блока «Нужно только для исполнения» не останавливают: скажи
человеку один раз «сборка не готова: <message>; можно исправить сейчас (<fix>) до первой
задачи модели, иначе этот прогон — только кейсы» и иди дальше, не дожидаясь ответа.
Wrapper (`mvnw`, `gradlew`) не создавай: он скачивает сборщик из сети. Переключение
`.skillsrc` на системный `mvn` (`execution:skillsrc-switch`) — только командой из `fix`, с
подтверждением человека.

Если `scan` вернул `requested_policy_profile: local-pilot-v1` и `policy_profile: cases-only-v1`,
прогон понижен: передай человеку `execution_skipped.reason` и `fix` — после исправления сборки
нужен новый прогон `local-pilot-v1`.

Модуль только с тестами, приложение — в соседнем каталоге того же корня (FaT-2R): ready спросит
`module:<id>:paths.context`; ответ — каталог приложения из `options` (его пакет только читает для контекста модели, ничего
там не пишет и не собирает). Приложение пакет не поднимает: стенд поднимает человек, адрес — в переменной окружения,
имя которой стоит в `.skillsrc` у модуля (`service_url_env`). Без него прогон понижается до кейсов. Стенд человек поднимает
до запуска команды из `materialize`; пакет стенд не опрашивает.

## К §0: проект только с документами

(режим `local-pilot-v1` честно отказывает `NO_BUILD_MODULE`, без понижения)


## К §1 «Выбери профиль»

Допустимы только:

- `cases-only-v1`: draft canonical cases/review без выполнения project code; `accepted=false`, не full-pipeline exit 0;
- `local-pilot-v1`: полный pipeline с run-scoped project-native execution; `accepted=true` только после live PASS всех
  кейсов — с ручными кейсами (блокеры канона) PASS даёт `PARTIAL`/`AUTOMATION_PARTIAL`, `accepted=false`.

Для `local-pilot-v1` execution разрешён только явным пользовательским запросом полного
pipeline в этом run. Наличие папки, scan или запрос test cases не разрешают execution.

## К §2 «Выбери exact module»

   Для Maven предпочти пригодный wrapper, затем доступный через PATH системный `mvn`.
   Явную конфигурацию молча не переключай; Maven/Wrapper автоматически не устанавливай.
   source/config/build/fixture inputs, доказанные parent dependencies и module.
   Для `local-pilot-v1` также обязательны test root, interpreter/wrapper/executable,
   adapter ID, build profile и typed params. Для `cases-only-v1` эти execution facts
   неприменимы: отсутствие Maven не препятствует подготовке ручных кейсов.

Поздно найденный undeclared execution input не добавляй в baseline. Закрой branch как
`NOT_RUNNABLE/BASELINE_INCOMPLETE`; продолжение — новый scan того же запроса.
Run остаётся связан с исходной project/module identity.

## К §3 «Построй canonical»: стадии автоматизации

`status` называет `prepare` со стадией и номером ревизии (`next.stage`, например `tc-to-autotest:r1`; номер выбирает код). В `read` для `tc-to-autotest:rN` и `autotest-reviewer:rN` —
`java_level` (уровень Java модуля) и `junit_params` (есть ли junit-jupiter-params для `param_group`): передай их как ограничение API. В `read` пачки генератора может быть `existing-tests.md`
(basis «карта») — дай его генератору вместе со входами.

## К §4 «Ревью»

При отказе или terminal pre-verdict abort не вызывай automation даже в `local-pilot-v1`.
Закрой раннюю отрицательную ветку через trace/finalization с
`materialization=execution=NOT_APPLICABLE`, исходной причиной и `accepted=false`.

## §5 для `local-pilot-v1`

### `local-pilot-v1`

Автотесты идут тем же циклом, что кейсы: `status` → `prepare --run-root <run> --stage <id>` → ответ в `answer_slot` → `submit` → `status`. Digest-ы входов, вид документа и границу ревью строит код;
файлы, digest-ы и пути руками не передаются. Не переписывай опубликованный request после вызова модели.

1. `prepare --stage tc-to-autotest:rN` печатает `read`, `answer_slot`, `budget`. В слот — полный V5-артефакт; `source.*`, `content_digest` и digest предшественника ставит `submit`.
   `submit --dry-run` проверяет смысл ответа и совместимость с раннером модуля до записи; отказ ничего не пишет — исправь слот и повтори (`--transport-attempts 2` — единственный повтор).
2. `prepare --stage autotest-reviewer:rN --fresh-context yes|no [--invocation-id <метка>]`: граница, `REVIEW_REQUESTED` и запрос — один вызов (повтор довершает оборванное). `yes` — ревьюер отдельной
   сессией, `--invocation-id` — его метка: не метка генератора и, для r2, не метка ревью r1. `no` — ревью в той же сессии, только после согласия человека на SELF (§1), метку не называй:
   независимость не засчитывается, отчёт это скажет. В выводе `read`, `answer_slot`, `for_human`, `generator_checks` (что код уже проверил). Digest-поля ответа ревьюера (`automation_sha256`,
   `reviewed_relations_sha256`, `host_isolation_sha256`, `reviewed_files[].content_digest`) ставит `submit`, ревьюер их не заполняет. Разрешены initial + одна complete correction.
3. После ревью `status` называет `materialize` (принято), `prepare --stage tc-to-autotest:r2` (`AUTO_FIX_APPLIED`; после `materialize` r2 запрещена) или `finish` (отказ).
4. После accepted static review сразу вызывай `materialize` (ниже). Он сам публикует
   execution-inputs и материализует принятую автоматизацию (generated delta) из ответов
   журнала, если этого ещё не было; partial materialization по-прежнему запрещает запуск.
   Нет принятого static review — `EXECUTION_NOT_PREPARED` с подсказкой.
5. `materialize` строит closed execution request сам: adapter ID, exact frozen
   interpreter/wrapper/executable, build profile и typed params; user shell strings и
   `argv_template` запрещены.
6. Команда из `materialize` запускает exact reviewed targets один раз через module-selected pytest, `mvnw`,
   системный `mvn` или `gradlew`; запускает её человек. Системный Maven использует закрытый адаптер
   `maven:selected-symbols-v1` и поле `executable`; абсолютный launcher и его bytes
   связаны с baseline. Hash launcher не доказывает идентичность всех библиотек Maven/JDK.
   Maven `test` читает Surefire: если проект требует Failsafe/integration-test lifecycle,
   этот маршрут недостаточен — сохрани блокер. Не исправляй тест после runtime `FAIL`.

Команду из `materialize` сам не выполняй ни в какой форме (`command`, `command_text`, `command_text_powershell`). Покажи человеку `files_to_review`, `note`,
`service_note` и обе формы команды как есть; дождись, пока он скажет, что просмотрел файлы, запустил команду и где отчёты, и только тогда вызывай `ingest`.
Сборку запускает человек: `materialize` кладёт тесты в проект и печатает команду для **человека** (со словом запуска) и файлы на просмотр;
тесты потом не правь (`INGEST_TESTS_CHANGED`, вернуть — `materialize --restore`), стенд поднимает человек. `ingest --reports <каталог в модуле или <run>/incoming/>`
сверяет отчёты с попыткой и закрывает её; сборка не дошла до тестов — `ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED|RESULT_UNAVAILABLE`.
Результат помечен: «результат запуска предоставлен человеком, пакет его не воспроизводил». Прерванный `ingest` повторяют теми же отчётами.
Команда `exec` заменена: она ничего не запускает и отвечает `EXEC_REPLACED`.
Команда печатается в двух формах: `command_text` — для cmd и sh, `command_text_powershell` (с `& ` и всеми токенами в кавычках) — для PowerShell; путь с `% ^ " ! $` и подобными символами в ней не печатается (`COMMAND_NOT_PRINTABLE`, до записи тестов):
проект переносят в каталог без них и начинают прогон заново (`scan --new-run`).

```powershell
python -m tools.run_pipeline materialize --project "$project" --module "$moduleId" --run "$runId"
python -m tools.run_pipeline ingest --project "$project" --module "$moduleId" --run "$runId" --reports "$reports"
```

Без authoritative framework result (отчёта нет) результат `UNKNOWN`.
Framework-reported exact-test timeout с authoritative evidence даёт `FAIL`. После
`EXECUTION_STARTED` interruption не перезапускай автоматически.

## §6 закрытие режима Б

## 6. Закрытие

`ingest` (local-pilot) и `finish` (cases-only, ранний отрицательный исход ревью, окончательный отказ автоматизации) сами проходят
линейное закрытие в фиксированном порядке: materialization → execution → execution trace →
dispositions → pre-finalization trace → finalization → terminal result и event. Повторный вызов
возвращает тот же итог. `finish`, вызванный не вовремя, отвечает `FINISH_FACT_UNDETERMINED` и
называет, чего не хватает, — сделай этот шаг, а не собирай факты вручную. Цепочка автоматизации не может
продолжиться — `finish --abandon <код>` из fix; список кодов закрытый, и `finish` сам откажет, если выход другой.
Частичная автоматизация (кейсы с блокерами канона ручные): `ingest` печатает `automation_split` — передай человеку его
`summary` как есть («автоматизировано N из M, вручную K: …»); итог `PARTIAL`/`AUTOMATION_PARTIAL`, `accepted=false`.

Что делает закрытие (это проверяет код): `PASS` с валидной трассой — `RETAINED`; `FAIL` —
карантин: упавшие методы помечаются `@Disabled`/`xfail`, прошедшие остаются (`QUARANTINED`); если
сопоставить упавшие методы нельзя — очистка с `QUARANTINE_UNRESOLVED`; `NOT_RUNNABLE` — очистка
только byte-identical pipeline-owned файлов; `UNKNOWN` — очистка
запрещена (`PRESERVED_EXECUTION_UNKNOWN`); частичная материализация — исполнения нет.
`valid=false` финализации — terminal с `FINALIZATION_INVALID` и `accepted=false`.

После `finish`/`ingest` с `FAIL` покажи человеку список `quarantine` (файл, метод, ref) и строку
`quarantine_unresolved` как есть. `@Disabled`/`xfail` в этом прогоне не снимай и тесты не чини.
`scan --disposition-policy cleanup` (FAIL = очистка) — только по просьбе человека.
Строки `style_checks_skipped` и `format_hint` из вывода `ingest`/`finish` передай человеку как есть; файлы сам не форматируй.

## Что пишет режим Б и как это откатить

Сверх `<project>/.pilot-runs/` режим Б пишет: сгенерированные тесты в тестовый корень модуля (`materialize`), строки карантина (`@Disabled`/`xfail`) в этих файлах
после `FAIL` и `.skillsrc` — только переключением на системный `mvn` (`execution:skillsrc-switch`) с подтверждения человека. Откат: `finish`/`ingest` по квитанциям
убирают материализованные тесты и аннотации (`NOT_RUNNABLE` — очистка byte-identical файлов пакета; `scan --disposition-policy cleanup` — очистка и после `FAIL`); файлы,
изменённые после `materialize`, возвращает `materialize --restore`; каталог `<run>` удаляется целиком. Для режима Б это заменяет «Project boundary» контракта
оркестрации («код проекта не запускается», запись только в `.pilot-runs/`, `.skillsrc` и `--output-dir`): тесты и сборку запускает человек.

## Контракт оркестрации — места режима Б

| Автоматизация | `tools.api prepare` / `tools.api submit` для `tc-to-autotest:rN` и `autotest-reviewer:rN` | одна версия и независимое static review; максимум одна correction |
| Материализация | делает `tools.run_pipeline materialize` сам | accepted exact generated set, ownership/readback |
| Выполнение | команду печатает `materialize`, запускает человек, отчёты принимает `ingest` | reviewed selectors, отчёт привязан к попытке |

   baseline выбранного профиля. Для cases-only нужны точные source/config/requirements,
   для local-pilot дополнительно все execution facts. Существующее событие
   `EXECUTION_BASELINE_FROZEN` подтверждает applicable receipt, не разрешение запуска.

10. Для `local-pilot-v1`: automation -> static review -> optional one complete
    correction/review -> generated delta -> per-file materialization -> one exact
    project-native execution (запускает человек).

Project code запускает человек, и только при `local-pilot-v1`, explicit run authorization,
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

Automation имеет одну static reviewer invocation на revision и не более двух полных
versions/reviews: initial + одна correction. Model self-attestation не доказывает fresh
context; это делает только host/controller evidence.

## Generated set и disposition

Каждый generated file имеет path, exact bytes/digest, materialization receipt и конечный
disposition. Execution не начинается при partial materialization.

| Branch | Disposition |
|---|---|
| authoritative `PASS` + valid trace | unchanged file `RETAINED` |
| `FAIL` (policy `quarantine`, default) | failed methods marked, file `QUARANTINED`; none failed in file → `RETAINED`; unresolved → `CLEANED` + `QUARANTINE_UNRESOLVED` |
| `FAIL` (policy `cleanup`) / `NOT_RUNNABLE` | byte-identical pipeline-owned file `CLEANED` |
| `UNKNOWN`, unchanged | `PRESERVED_EXECUTION_UNKNOWN` |
| `UNKNOWN`, drifted | `PRESERVED_CONTENT_CONFLICT` + unknown evidence |
| partial materialization | safe cleanup либо `NOT_MATERIALIZED` |

Если cleanup нельзя доказать безопасным, сохрани file с exact reason и
`accepted=false`. Cleanup при `UNKNOWN` запрещён.

execution input закрывает текущий attempt как `NOT_RUNNABLE/BASELINE_INCOMPLETE`.
Execution interruption даёт `UNKNOWN`; продолжение — только новый scan.

## К итогу прогона

В `report.md` стоит строка «Уровень автотестов: …»: `in-process` (тесты идут против кода модуля) или `stand` (против поднятого стенда по 
`service_url_env`). Передай её человеку как есть. Если уровень выведен, а не задан (`TEST_LEVEL_DERIVED` в `ready` и в `warnings` scan), 
скажи, что строка `test.level: <уровень>` в блоке `test` модуля `.skillsrc` делает его явным; файл правит человек. Отчёт не называет 
уровень, если `.skillsrc` изменён после scan. В `.skillsrc` `test.executable: mvn` — имя из PATH, а не путь этой машины; прежний 
абсолютный путь продолжает работать.
