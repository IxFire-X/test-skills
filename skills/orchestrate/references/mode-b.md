# Режим Б — `local-pilot-v1` (только сборка разработки)


Этот файл — прежний текст `SKILL.md` и контракта оркестрации, который описывает автотесты и запуск: роли `tc-to-autotest` и
`autotest-reviewer`, `tools.run_pipeline exec`, сборку проекта, стенд, карантин. В поставку «только кейсы» он не входит. Агент,
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
имя которой стоит в `.skillsrc` у модуля (`service_url_env`). Без него прогон понижается до кейсов. exec до любой записи
проверяет, что стенд отвечает; `SERVICE_URL_MISSING` / `SERVICE_UNREACHABLE` — не итог: попытка открыта, передай человеку
`fix` и после его «стенд поднят» запусти exec снова.

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

## К §3 «Построй canonical»: шаги автоматизации

одной строкой). Для `tc-to-autotest:rN` и `autotest-reviewer:rN` там же `java_level` — уровень Java модуля по его сборке — и `junit_params` (есть ли junit-jupiter-params для групп `param_group`);
передай его генератору и ревьюеру как ограничение API. Для `tc-generator:<batch>` там же может быть файл
`existing-tests.md` (basis «карта», FaT-EC) — карта существующих тестов проекта; дай его генератору вместе со входами. Остальные шаги — `--run <run_id> --files <файлы шага> --role <роль> --step <стадия>`

## К §4 «Ревью»

При отказе или terminal pre-verdict abort не вызывай automation даже в `local-pilot-v1`.
Закрой раннюю отрицательную ветку через trace/finalization с
`materialization=execution=NOT_APPLICABLE`, исходной причиной и `accepted=false`.

## §5 для `local-pilot-v1`

### `local-pilot-v1`

Для model request `tc-to-autotest:rN` передай `input_digests` ровно как
`[effective.document_digest, effective.effective_bundle_receipt_digest]` из
`python -m tools.api read-effective`. Перед вызовом построй вид документа для генератора (FaT-AV):
`python -m tools.review_view --project <abs> --run <run_id> --effective` — `step_size`/`step-inputs` отдают этот вид
вместо JSON документа, квитанцию — в `input_bindings` (носитель `effective-canonicals` не читай). Для `autotest-reviewer:rN` — ровно
`[boundary.automation_digest, boundary.digest]` из read-back
`automation-review-boundary-rN` (`tools.api read-receipt --kind automation-review-boundary-rN --event ARTIFACT_READ_BACK`). Порядок значим. Это привязки артефактов этапа,
а не hashes всех прочитанных skill/schema/source файлов или полного prompt.
Exact transport input/output bytes сохраняются отдельно; их digest не заменяет
эти поля. Не переписывай опубликованный request после вызова модели.

1. Вызови `tc-to-autotest` для effective canonical. `content_digest`, `source.*` и digest предшественника не считай:
   пропущенные ставит `model-response`.
2. Открой attempt-owned automation review boundary (`tools.api open-automation-boundary --facts <f>`); файл facts строит
   код из журнала после `model-response` стадии `tc-to-autotest:rN`, руками его не собирай:
   `tools.api automation-boundary-facts --revision N --reviewer-invocation-id <id> --fresh-context yes|no
   --out <run>/tmp/auto-facts-rN.json` (заявления — как для `session-start` в §4). Затем запиши
   `tools.api review-requested --stage autotest-reviewer:rN --digest <boundary.automation_digest>` и
   вызови `autotest-reviewer` в fresh invocation. Разрешены initial + максимум одна complete correction/review.
3. После accepted static review сразу вызывай `exec` (ниже). exec сам публикует
   execution-inputs и материализует принятую автоматизацию (generated delta) из ответов
   журнала, если этого ещё не было; partial materialization по-прежнему запрещает execution.
   Нет принятого static review — exec отвечает `EXECUTION_NOT_PREPARED` с подсказкой.
4. exec строит closed execution request сам: adapter ID, exact frozen
   interpreter/wrapper/executable, build profile и typed params; user shell strings и
   `argv_template` запрещены.
5. exec запускает exact reviewed targets один раз через module-selected pytest, `mvnw`,
   системный `mvn` или `gradlew`. Системный Maven использует закрытый адаптер
   `maven:selected-symbols-v1` и поле `executable`; абсолютный launcher и его bytes
   связаны с baseline. Hash launcher не доказывает идентичность всех библиотек Maven/JDK.
   Maven `test` читает Surefire: если проект требует Failsafe/integration-test lifecycle,
   этот маршрут недостаточен — сохрани блокер. Не исправляй тест после runtime `FAIL`.

Запуск человеком (P4, предпочтительный путь): `materialize` кладёт тесты в проект и печатает команду для **человека** (со словом запуска) и файлы на просмотр;
тесты потом не правь (`INGEST_TESTS_CHANGED`, вернуть — `materialize --restore`), стенд поднимает человек. `ingest --reports <каталог в модуле или <run>/incoming/>`
сверяет отчёты с попыткой и закрывает её; сборка не дошла до тестов — `ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED|RESULT_UNAVAILABLE`.
Результат помечен: «результат запуска предоставлен человеком, пакет его не воспроизводил». Прежний путь (`exec`) — до следующего выпуска.

Исполнение и всё закрытие — одна команда из cwd пакета; шесть квитанций она берёт из журнала
попытки сама (явный путь `--canonical-document` и т. п. — необязательная сверка тех же байтов):

```powershell
python -m tools.run_pipeline exec --project "$project" --module "$moduleId" --run "$runId"
```

Resume после `EXECUTION_STARTED` идёт штатной веткой восстановления той же командой, а не
повторным запуском тестов.

Controller/process timeout без authoritative framework result даёт `UNKNOWN`.
Framework-reported exact-test timeout с authoritative evidence даёт `FAIL`. После
`EXECUTION_STARTED` interruption не перезапускай автоматически.

## §6 закрытие режима Б

## 6. Закрытие

`exec` (local-pilot) и `finish` (cases-only, ранний отрицательный исход ревью, окончательный отказ автоматизации) сами проходят
линейное закрытие в фиксированном порядке: materialization → execution → execution trace →
dispositions → pre-finalization trace → finalization → terminal result и event. Повторный вызов
возвращает тот же итог. `finish`, вызванный не вовремя, отвечает `FINISH_FACT_UNDETERMINED` и
называет, чего не хватает, — сделай этот шаг, а не собирай факты вручную. Цепочка автоматизации не может
продолжиться — `finish --abandon <код>` из fix; список кодов закрытый, и `finish` сам откажет, если выход другой.
Частичная автоматизация (кейсы с блокерами канона ручные): `exec` печатает `automation_split` — передай человеку его
`summary` как есть («автоматизировано N из M, вручную K: …»); итог `PARTIAL`/`AUTOMATION_PARTIAL`, `accepted=false`.

Что делает закрытие (это проверяет код): `PASS` с валидной трассой — `RETAINED`; `FAIL` —
карантин: упавшие методы помечаются `@Disabled`/`xfail`, прошедшие остаются (`QUARANTINED`); если
сопоставить упавшие методы нельзя — очистка с `QUARANTINE_UNRESOLVED`; `NOT_RUNNABLE` — очистка
только byte-identical pipeline-owned файлов; `UNKNOWN` — очистка
запрещена (`PRESERVED_EXECUTION_UNKNOWN`); частичная материализация — исполнения нет.
`valid=false` финализации — terminal с `FINALIZATION_INVALID` и `accepted=false`.

После `finish`/`exec` с `FAIL` покажи человеку список `quarantine` (файл, метод, ref) и строку
`quarantine_unresolved` как есть. `@Disabled`/`xfail` в этом прогоне не снимай и тесты не чини.
`scan --disposition-policy cleanup` (FAIL = очистка) — только по просьбе человека.
Строки `style_checks_skipped` и `format_hint` из вывода `exec`/`finish` передай человеку как есть; файлы сам не форматируй.

## Контракт оркестрации — места режима Б

| Автоматизация | `model-request`/`model-response`; `tools.api automation-boundary-facts`, `tools.api open-automation-boundary`, `tools.api read-receipt`, `tools.api review-requested` | одна версия и независимое static review; максимум одна correction |
| Материализация | делает `tools.run_pipeline exec` сам | accepted exact generated set, ownership/readback |
| Выполнение | `tools.run_pipeline exec` с шестью carriers из инструкции | reviewed selectors, process-bound native report |

   baseline выбранного профиля. Для cases-only нужны точные source/config/requirements,
   для local-pilot дополнительно все execution facts. Существующее событие
   `EXECUTION_BASELINE_FROZEN` подтверждает applicable receipt, не разрешение запуска.

10. Для `local-pilot-v1`: automation -> static review -> optional one complete
    correction/review -> generated delta -> per-file materialization -> one exact
    project-native execution.

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
Execution interruption даёт `UNKNOWN`; retry — только новый scan после
доказанной остановки прежнего process scope.

## К итогу прогона

В `report.md` стоит строка «Уровень автотестов: …»: `in-process` (тесты идут против кода модуля) или `stand` (против поднятого стенда по 
`service_url_env`). Передай её человеку как есть. Если уровень выведен, а не задан (`TEST_LEVEL_DERIVED` в `ready` и в `warnings` scan), 
скажи, что строка `test.level: <уровень>` в блоке `test` модуля `.skillsrc` делает его явным; файл правит человек. Отчёт не называет 
уровень, если `.skillsrc` изменён после scan. В `.skillsrc` `test.executable: mvn` — имя из PATH, а не путь этой машины; прежний 
абсолютный путь продолжает работать.
