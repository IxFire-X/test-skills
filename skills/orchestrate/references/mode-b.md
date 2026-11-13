# Режим Б — `local-pilot-v1` (в поставках с режимом Б)

Маршрут: готовность → кейсы и ревью → автотесты → `materialize` → запуск человеком → `ingest` → закрытие. Читай вместе с `SKILL.md`; роли — `tc-to-autotest` и `autotest-reviewer`.
Профиль `local-pilot-v1` разрешает только явный запрос человека на полный pipeline в этом прогоне; папка, scan или запрос тест-кейсов его не разрешают. `accepted=true` — только после live PASS всех кейсов.

## 1. Готовность

- Предупреждения блока «Нужно только для исполнения» не останавливают: скажи человеку один раз «сборка не готова: <message>; можно исправить сейчас (<fix>) до первой задачи модели, иначе этот прогон — только кейсы» и иди дальше.
- Wrapper (`mvnw`, `gradlew`) не создавай: он скачивает сборщик из сети. Maven и wrapper автоматически не устанавливай. Переключение `.skillsrc` на системный `mvn` (`execution:skillsrc-switch`) — только командой из `fix`, с подтверждения человека.
- `scan` вернул `requested_policy_profile: local-pilot-v1` и `policy_profile: cases-only-v1` — прогон понижен: передай человеку `execution_skipped.reason` и `fix`; после исправления сборки нужен новый прогон. Проект только с документами: `NO_BUILD_MODULE`.
- Модуль только с тестами, приложение рядом: `ready` спросит `module:<id>:paths.context`; ответ — каталог приложения из `options` (пакет его только читает). Приложение пакет не поднимает: стенд поднимает человек до запуска команды из `materialize`,
  адрес — в переменной окружения, имя которой стоит в `.skillsrc` у модуля (`service_url_env`); пакет стенд не опрашивает. Без неё прогон понижается до кейсов.
- Поздно найденный undeclared execution input в baseline не добавляй: закрой ветку как `NOT_RUNNABLE/BASELINE_INCOMPLETE`; продолжение — новый scan того же запроса.

## 2. Кейсы и ревью

Те же стадии, что в `SKILL.md`. Отказ ревью или abort до вердикта — автоматизацию не вызывай: закрой ветку через `finish` (`materialization=execution=NOT_APPLICABLE`, `accepted=false`). `existing-tests.md` в `read` пачки генератора — дай его генератору вместе со входами.

## 3. Автотесты

Тем же циклом, что кейсы: `status` → `prepare --run-root <run> --stage <id>` → ответ в `answer_slot` → `submit` → `status`; `<id>` с номером ревизии (`tc-to-autotest:r1`) называет `status`.

1. `prepare --stage tc-to-autotest:rN` печатает `read`, `answer_slot`, `budget`; в `read` — `java_level` (уровень Java модуля) и `junit_params` (есть ли junit-jupiter-params для `param_group`): передай их как ограничение API. В слот — полный артефакт;
   `source.*`, `content_digest` и digest предшественника ставит `submit`. `submit --dry-run` проверяет ответ и совместимость с раннером модуля до записи; отказ ничего не пишет — исправь слот и повтори (`--transport-attempts 2` — единственный повтор).
2. `prepare --stage autotest-reviewer:rN --fresh-context yes|no [--invocation-id <метка>]`: граница, `REVIEW_REQUESTED` и запрос — один вызов (повтор довершает оборванное). `yes` — ревьюер отдельной сессией, `--invocation-id` — его метка: не метка генератора и,
   для r2, не метка ревью r1. `no` — та же сессия, только после согласия человека на SELF (§1 `SKILL.md`), метку не называй. В выводе `read`, `answer_slot`, `for_human`, `generator_checks`. Digest-поля ответа ревьюера ставит `submit`, ревьюер их не заполняет.
   Разрешены initial + одна complete correction.
3. После ревью `status` называет `materialize` (принято), `prepare --stage tc-to-autotest:r2` (`AUTO_FIX_APPLIED`; после `materialize` r2 запрещена) или `finish` (отказ).

## 4. `materialize`

После принятого static review сразу вызывай `materialize` (нет принятого ревью — `EXECUTION_NOT_PREPARED`). Он сам публикует execution-inputs и материализует принятую автоматизацию в проект, строит закрытый запрос исполнения и печатает команду для **человека**.
Команду из `materialize` сам не выполняй ни в какой форме (`command`, `command_text`, `command_text_powershell`). Покажи человеку `files_to_review`, `note`, `service_note` и обе формы команды как есть: `command_text` — для cmd и sh,
`command_text_powershell` — для PowerShell. Путь с `% ^ " ! $` в команду не попадает (`COMMAND_NOT_PRINTABLE`, до записи тестов): проект переносят в другой каталог и начинают прогон заново (`scan --new-run`).

## 5. Запуск человеком и `ingest`

Сборку и тесты запускает человек напечатанной командой — один раз, точные reviewed targets; стенд поднимает человек. Maven `test` читает Surefire: проект требует Failsafe/integration-test lifecycle — этот маршрут недостаточен, сохрани блокер.
Дождись слов человека, что он просмотрел файлы, запустил команду и где отчёты, — только тогда `ingest`. Тесты после `materialize` не правь (`INGEST_TESTS_CHANGED`; вернуть — `materialize --restore`) и после runtime `FAIL` не исправляй.

```powershell
python -m tools.run_pipeline materialize --project "$project" --module "$moduleId" --run "$runId"
python -m tools.run_pipeline ingest --project "$project" --module "$moduleId" --run "$runId" --reports "$reports"
```

`ingest --reports <каталог в модуле или <run>/incoming/>` сверяет отчёты с попыткой и закрывает её; сборка не дошла до тестов — `ingest --no-reports --reason BUILD_FAILED|TESTS_NOT_STARTED|RESULT_UNAVAILABLE`. Прерванный `ingest` повторяют теми же отчётами.
Нет отчёта — `UNKNOWN`; timeout точного теста по отчёту фреймворка — `FAIL`. После `EXECUTION_STARTED` прерывание автоматически не перезапускай; продолжение — новый scan.

## 6. Закрытие

`ingest` сам проходит закрытие; повторный вызов возвращает тот же итог. `finish` не вовремя — `FINISH_FACT_UNDETERMINED` с недостающим: сделай этот шаг. Цепочка автоматизации не продолжается — `finish --abandon <код>` из `fix`. Итог с `FINALIZATION_INVALID` — прогон закрыт невалидным, `accepted=false`: передай как есть, `finish` не повторяй, файлы не правь.
Частичная автоматизация (кейсы с блокерами канона ручные): `ingest` печатает `automation_split` — передай человеку его `summary` как есть; итог `PARTIAL`/`AUTOMATION_PARTIAL`, `accepted=false`.

Судьба файлов (проверяет код): `PASS` — `RETAINED`; `FAIL` — карантин: упавшие методы помечаются `@Disabled`/`xfail`, прошедшие остаются; упавшие не сопоставить — очистка с `QUARANTINE_UNRESOLVED`; `NOT_RUNNABLE` — очистка byte-identical файлов пакета;
`UNKNOWN` — очистка запрещена. После `FAIL` покажи человеку список `quarantine` (файл, метод, ref) и строку `quarantine_unresolved` как есть; `@Disabled`/`xfail` не снимай, тесты не чини. `scan --disposition-policy cleanup` — только по просьбе человека.
`style_checks_skipped` и `format_hint` передай человеку как есть; файлы не форматируй.

## 7. Итог прогона

В `report.md` стоит строка «Уровень автотестов: …» — `in-process` или `stand` (по `service_url_env`): передай её человеку как есть. Уровень выведен, а не задан (`TEST_LEVEL_DERIVED`) — скажи, что строка `test.level: <уровень>` в блоке `test` модуля `.skillsrc` делает его явным; файл правит человек.

## Что пишет режим Б и как это откатить

Сверх `<project>/.pilot-runs/`: сгенерированные тесты в тестовый корень модуля (`materialize`), строки карантина (`@Disabled`/`xfail`) после `FAIL` и `.skillsrc` — только переключением на системный `mvn` с подтверждения человека. Откат: `finish`/`ingest` по квитанциям убирают тесты и аннотации; изменённые после `materialize` файлы возвращает `materialize --restore`; каталог `<run>` удаляется целиком. Это заменяет для режима Б правило «код проекта не запускается»: тесты и сборку запускает человек.
