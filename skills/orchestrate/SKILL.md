---
name: orchestrate
description: Использовать, когда пользователь просит тест-кейсы приложения по согласованным требованиям, всей спецификации OpenSpec либо выбранному change со связанной регрессией.
permissions:
  read: "файлы проекта по inventory с исключениями; --docs, --reference и --cases только внутри проекта"
  write: "<project>/.pilot-runs/<run_id>/; <project>/.skillsrc при создании; каталог публикации: <run>/published"
  network: "нет"
  external_delivery: "нет"
  dependencies: "пакет ничего не устанавливает; pip агент не запускает"
  rollback: "удалить <project>/.pilot-runs/<run_id>/ и созданный <project>/.skillsrc"
---

# Оркестрация Pipeline 4.0 — тест-кейсы

Ты — controller в model-enabled CLI. Не запускай из Python модель и не передавай model credentials. Python tools проверяют и публикуют
артефакты; сборку и тесты не запускают. Кейсы идут профилем `cases-only-v1`; автотесты (`local-pilot-v1`, режим Б) входят в поставку профиля `mode-b` (его называет `doctor`): веди их по `references/mode-b.md`; в `cases-only` их нет.

Это карта стадий. Аргументы команды — `python -m tools.api <команда> --help`; что делать дальше — `state` и `next` в `status`.
До работы — `python -m tools.doctor --root <pack-root>`. tools вызывай с cwd пакета и абсолютным `--project`; функции пакета не импортируй.
Шаг — `python -m tools.api <команда> --run-root <run> …` (`prepare`, `submit` — ещё `--stage <id>`): одна строка JSON, `status: ok` и `result`,
`invalid` (код 1) или `error` (код 2: `code`, `message`, `fix`). Писать можно только в `<project>/.pilot-runs/`, `.skillsrc` и `<run>/published`.
В `model_id` пиши фактический ID модели твоей CLI. Clone, worktree, sandbox, dependency, CI/cron, commit, push и PR не создавай.

Вход шага не урезай и модель ради вместимости не меняй. `STEP_OVER_BUDGET`, `STEP_OUTPUT_LARGE`, `STEP_INPUT_UNRESOLVED` — не стоп: одна
строка человеку (с `hint`), продолжай; ответ не дели и не переписывай. `MODEL_OUTPUT_TRUNCATED` — отказ: покажи человеку байты и подсказку, повтори стадию единственный раз по `fix`.

## 0. Готовность

`python -m tools.ready --project <abs> --docs <путь> [--docs …] --profile cases-only-v1 [--module <ID>]` только читает: покажи человеку таблицу, каждый `СТОП`
исправь по его `fix` (команду установки зависимостей покажи человеку, сам не запускай); код 0 — можно начинать. Проект только с документами:
один модуль `root` с корнем «.» и `stack.language: documents`, `.skillsrc` создаётся сам; режим только `cases-only-v1`, все шаги `manual_only`, итог `coverage: MANUAL_ONLY`, `accepted: false` — норма, не ошибка.

## 1. Run и режим ревью

Объём для OpenSpec — [openspec.md](references/openspec.md). Каждый пробел — в `warnings` с четырьмя частями: требование и источник; недостающее условие;
заблокированные проверки; вопрос. Неопределённое требование остаётся в наборе; существенный пробел запрещает заявлять полный объём. `scan` создаёт run (`run_id` — последняя строка вывода; файлы и события run руками не пиши).
`SCAN_RUN_IN_PROGRESS` — продолжи по `fix`; `--new-run` — только по просьбе человека или если `fix` его называет. Незакрытый прогон, у которого файлы проекта изменились (дрейф), `scan` не продолжает: он начинает новый и называет прошлый в `abandoned_runs`.
Документацию внешних зависимостей сам не ищи и в дерево проекта не сохраняй: бери то, что дал человек; не хватает — одна строка человеку.

Ревью `INDEPENDENT`: свежий контекст обязателен (субагент или вызов без истории генерации). `SELF`: та же сессия, новую не требуем, независимым не называем. `review:isolation` в `ready` подсказывает, есть ли у CLI субагент.
Если нет — перед первым ревью спроси человека дословно: «Ревьюер будет работать в той же сессии, без свежего контекста. Принять такой прогон с пометкой SELF?». После явного «да» — `prepare` ревью с `--fresh-context no --accept-self-review`
(повторный scan не нужен; согласие одно на прогон, остальные ревью идут с `no` без флага). Без согласия отказ `INDEPENDENCE_UNVERIFIED`.

## 2. Модуль и контекст

Модуль, `.skillsrc`, исключения, `--docs путь#Раздел`, повтор разметки — [scan.md](references/scan.md). После scan покажи человеку `exclusion_summary`. Анализ неполон, пока обязательная зависимость не прочитана: область не расширяй самовольно, исключение файла по политике не доказывает его нерелевантность. Вход, зависимости и снимок после scan не меняются до закрытия попытки.

## 3. Canonical из пачек

Цикл каждой стадии модели: `python -m tools.api prepare --run-root <run> --stage <id>` печатает `read` — сводку файлов модели `files_summary` с `list_path` (один вызов размера
на пачку; замер — `step_size`), `answer_slot` и `budget`; ответ модели — как есть в `answer_slot`; `python -m tools.api submit --run-root <run> --stage <id>` принимает его (`--dry-run` — только проверки; `--transport-attempts 2` —
единственный повтор) и печатает квитанцию (`result.path`, `content_digest`, `output_bytes`, `warning`), не тело. `<id>` с номером ревизии (`…:r1`) называет `status`.

1. **Разметка.** `context-marker:baseline`, либо частями `:requirements`, затем `:batch-NNN`. Импорт — [import.md](references/import.md).
2. **Пауза.** Пробелы без канала наблюдения, ожидаемого результата или доступа — `state: scope_pause` (`scope_gaps`). `prepare --stage scope` печатает пробелы и формы ответа.
   Остановись: покажи человеку список, спроси «исключить или продолжать»; его слова — в `answer_slot`, затем `submit --stage scope`. Без ответа `submit --stage plan`
   отказывает `SCOPE_PENDING`. Решение за человеком (исключить можно и без пробелов, до плана), другое — новый прогон; `scan --no-pause` просит человек, ты его не добавляешь.
3. **План** (`--stage plan`). В `answer_slot` — `{"subject": "<название предмета проверки по-русски>"}` и ничего больше. `notes` и `context` вывода покажи человеку как есть.
4. **Генератор.** Пачки по очереди. `prepare` получает `--model-id` и `--invocation-id` сессии, пишущей кейсы (от метки зависит независимость ревью); `prepare` один раз
   печатает `nonce_note` — выполни как написано. **Фрагмент** — голый объект без конверта `artifacts`; образец —
   [fragment-sample.md](references/fragment-sample.md), схема — `schemas/candidate-fragment.schema.json`; `context_receipt` и digest-поля ставит код, не пиши их. Пачка 2 и дальше: в `operation_capabilities` фрагмента — копии capabilities
   прошлых пачек, которые он использует (из входа `prior_capabilities`, слово в слово). Ответ — файл данных: собирай его по частям, кейс за кейсом; скрипты, которые импортируют или вызывают код пакета, не пиши. Отказ — единственный повтор с `retry_prompt`.
5. **Сборка.** Её делает `submit` последней пачки до записи ответа. `MANUAL_SHARE_HIGH` — скажи человеку: большинство шагов ручные, автотестов по ним не будет, контрольных ошибок ревью может не быть — смотри отчёт. Проверь
   полноту по исходному документу; недоказанное — gap. Поля для человека — по-русски; условия работы над тестами (заглушки, режим запуска) в предусловия, шаги и ожидания не пишутся. Потолка на число требований и кейсов нет.

## 4. Ревью

1. `prepare --stage tc-reviewer:canonical --invocation-id <метка ревьюера> --fresh-context yes|no` печатает `answer_slot`, `reviewer_view`, `for_human`. `yes` — метка обязательна
   и не равна метке генератора; `no` — после согласия человека на SELF (§1).
2. `tc-reviewer` в fresh invocation читает `reviewer_view` и пишет в `answer_slot`; файлы проекта сверх пакета — ключ `evidence: ["путь", …]` верхнего уровня (ключ вне схемы ответа: код вынимает его до проверки).
   Карту канареек не читай и ревьюеру не показывай; роль, канарейки, бюджет — [review.md](references/review.md).
3. `submit --stage tc-reviewer:canonical` кладёт набор в `<run>/published`. Ревью не довести до вердикта — `submit --abort REVIEW_CONTEXT_LIMIT` или `REVIEW_UNUSABLE`, затем `finish`.

По `outcome` ответа `submit` скажи человеку: `ACCEPTED_WHOLE` — набор принят, `finish`. `PARTIAL` — принят частично: годные кейсы в наборе, отклонённые отдельно (`*.REJECTED-CASES.*`, в Zephyr не импортировать);
если `status` называет `prepare tc-generator:rework-1` — один круг доработки (`tc-generator:rework-1`, затем `tc-reviewer:rework-1`, метка и `--fresh-context` как у первого ревью), `finish` до него не зови; не получается —
`submit --stage tc-generator:rework-1 --abort REWORK_UNUSABLE`: набор выйдет без доработки, прогон идёт дальше. `REWORK` — отклонён целиком, `finish` закроет прогон без CSV. Без host isolation evidence ветку не принимай (`independence_unverified`).

## 5. Закрытие и отчёт

`python -m tools.run_pipeline finish --project <abs> --run <run_id>` проходит закрытие сам; `FINISH_FACT_UNDETERMINED` называет недостающее — сделай этот шаг. Цепочка
не продолжается — `finish --abandon <код>` из `fix`. Результат — `verification=NOT_APPLICABLE`, `accepted=false`; читай все поля, «PASS» из manual coverage не делай. Итог с `FINALIZATION_INVALID` — прогон закрыт невалидным, `accepted=false`: передай человеку как есть, `finish` не повторяй и файлы не правь.
Задан `projection.zephyr.template` — экспорт для Zephyr после закрытия: [import.md](references/import.md).
В конце один раз `python -m tools.run_pipeline report --project <abs> --run <id>` — `report.md`, `report.json`, `questions-for-analyst.md`; назови пути. `review_notes` — число и путь одной строкой; кейсы не меняй, вердикт не пересматривай.

## Resume и дрейф

Контекст занят больше чем наполовину — после завершённого шага попроси человека `/clear` и «продолжай test-skills <run_id>»; затем `python -m tools.run_pipeline status --project <abs> --run <run_id>`:
продолжай с `state` и `next` (`waiting_for: model` — ответ на запрос из `pending_request_path`; `human` — жди); `recovery` — скажи человеку. Подтверждённое журналом не повторяй. `state: left` (`RUN_LEFT`) —
`finish --abandon LEFT_BEFORE_REVIEW`, затем `scan_command`. `status: drift` — передай человеку `drift.changed` и `drift.fix`, файлы не правь, прогон сам не начинай. После `finish` с `next_run` — команда `next_run.command` как есть.

## Stop conditions

Остановись без guess: invalid/unsupported version, ambiguous module, unsafe path, required invention, unavailable tool/model, schema/semantic/provenance failure, secret exposure,
baseline drift, stale digest/readback, unproved reviewer isolation, выход за authorized scope. Сохрани factual partial evidence; `implemented_unverified` за verified readiness не выдавай.
