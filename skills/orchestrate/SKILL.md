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

# Оркестрация frozen pilot Pipeline 4.0 — тест-кейсы

Ты — controller в совместимой model-enabled CLI. Не запускай из Python модель и не передавай model credentials. Python tools только
проверяют и публикуют артефакты (с readback); сборку и тесты не запускают. Кейсы идут профилем `cases-only-v1`; автотесты
(`local-pilot-v1`, режим Б) входят в поставку профиля `mode-b` (его называет `doctor`): веди их по `references/mode-b.md`; в `cases-only` их нет.

Это карта стадий. Аргументы команды — `python -m tools.api <команда> --help`; что делать дальше — `state` и `next` в `status`.
Прочитай [контракт оркестрации](references/orchestration-contract.md). До работы — `python -m tools.doctor --root <pack-root>`.
Используй только exact registered Skills и stages из `contracts/pipeline.json`. Роль читает свои инструкции, контракт, схему ответа и полные
входы; validators выполняются как код, их чтение не заменяет выполнения.

Пути `contracts/`, `schemas/`, `skills/`, `tools/` — от корня пакета; tools вызывай с cwd пакета и абсолютным `--project`. Функции пакета не
импортируй: шаг — `python -m tools.api <команда> --run-root <run> …` (`prepare` и `submit` — ещё `--stage <id>`; флага `--attempt` у них нет); одна строка JSON: `status: ok` и `result`,
`invalid` (код 1) или `error` (код 2, `code`, `message`, `fix`). Ход работы — `run_pipeline status` и поля JSON последних событий; `head`/`tail` по строкам
объём не ограничивают. В `model_id` пиши фактический ID модели твоей CLI.

Размер входа шага меряет `tools.step_size`; вход не урезай и модель ради вместимости не меняй. `STEP_OVER_BUDGET`, `STEP_OUTPUT_LARGE`,
`STEP_INPUT_UNRESOLVED` — не стоп: одна строка человеку (с `hint`), продолжай; ответ не дели и не переписывай. `MODEL_OUTPUT_TRUNCATED`
(JSON оборван) — отказ: покажи человеку байты и подсказку, повтори стадию единственный раз с `retry_prompt` из `validate_artifact`.

## 0. Проверь готовность

`python -m tools.ready --project <abs> --docs <путь> [--docs …] --profile cases-only-v1 [--module <ID>]` — только читает: Python, пакет,
документы, `.skillsrc`, модуль, скан с исключениями. Покажи человеку таблицу (stderr); каждый `СТОП` исправь по его `fix` (команду установки
зависимостей покажи человеку, сам не запускай); повтори; код выхода 0 — можно начинать. Проект только с документами: нет манифестов сборки и исходников — один модуль `root` с корнем «.» и
`stack.language: documents`, `.skillsrc` создаётся сам; режим только `cases-only-v1`, все шаги `manual_only`, итог `coverage: MANUAL_ONLY`,
`accepted: false` — норма режима, не ошибка. Контекст модели — все документы папки, не только `--docs`.

## 1. Создай run

Объём для OpenSpec и дельты — [openspec.md](references/openspec.md). Если вся спецификация не помещается в маршрут одного модуля,
сохрани блокер, а не подмени запрос удобным модулем. Каждый пробел — в `warnings` с четырьмя частями: требование/сценарий и источник;
недостающее условие; заблокированные проверки; конкретный вопрос. Неопределённое требование остаётся в наборе; существенный пробел
запрещает заявлять полный объём. `cases-only-v1`: код проекта не выполняется, `accepted=false`.

Ревью: `INDEPENDENT`: свежий контекст обязателен (субагент или вызов без истории генерации). `SELF`: та же сессия, новую не требуем, независимым не называем. `review:isolation` в `ready` подсказывает, есть ли у CLI субагент. Если нет — перед первым ревью спроси человека дословно: «Ревьюер будет работать в той же сессии, без свежего контекста. Принять такой
прогон с пометкой SELF?». После явного «да» — `prepare` ревью с `--fresh-context no --accept-self-review` (повторный scan не нужен; согласие одно на прогон,
остальные ревью идут с `no` без флага). Без согласия отказ `INDEPENDENCE_UNVERIFIED`. Заранее — `scan --accept-self-review` (`details.self_review_scan`) или `review: {"self": true}`
в `.skillsrc` (ставит человек).

Scan создаёт run: `run-authorization-receipt.json`, `run-manifest.json`, `RUN_CREATED`; `run_id` — последняя строка вывода. Руками эти
файлы и события не пиши. `SCAN_RUN_IN_PROGRESS` — незавершённый прогон этого запроса: продолжи по `fix` (`status`); `--new-run` — только
когда человек просит ещё один прогон или `fix`/`next_run` сами называют команду с ним. Clone, worktree, sandbox, dependency, CI/cron,
commit, push и PR не создавай.

## 2. Модуль, baseline и контекст

Документацию внешних зависимостей сам не ищи и в дерево проекта не сохраняй: бери то, что дал человек (`--docs`, `--reference`, `--cases`); не хватает — одна строка человеку.
Scan сам делает read-only inventory, исключает `.git`, зависимости, build outputs, binaries и файлы с возможными secrets, замораживает
baseline (requirements, `.skillsrc`, source/config/fixture inputs, parent dependencies, module), публикует/readback-проверяет модуль,
inventory, baseline и создаёт один nonterminal attempt (`ATTEMPT_CREATED`). Тебе — выбрать exact module и передать `--docs`, `--module`,
`--profile`.

- **Модуль.** Один exact module: автоматически — единственный или доказанно содержащий путь пользователя; иначе спроси человека и жди
  (scan без точного `--module` попытку не создаёт). `--module` — ID из inventory/`.skillsrc`, не путь: корню соответствует `root`, остальные —
  путь папки (`api`, `services/core`); имена с точкой или не из латиницы — `root--<hex>`. Документ требований может лежать вне модуля.
- **`.skillsrc`.** Валидный существующий — authoritative; отсутствующий scan создаёт сам; drift значимых полей — одно подтверждение.
- **Исключения.** После scan покажи человеку `exclusion_summary` (то же — в `status`): причины и `suspicious`. Это информация, не стоп;
  лишнее исключение возвращает `.skillsrc` `paths.include` (`hint`).
- **`--docs путь#Раздел`** (Markdown): модель читает только вид `<run>/doc-sections/<имя>.md`; нет заголовка — `TASK_DOC_SECTION_MISSING`
  с перечнем; один раздел на файл. Документ задачи всегда в контексте; `TASK_DOC_OUTSIDE_CONTEXT` — передай человеку `fix`.
- **Повтор разметки.** `reuse` в выводе scan называет прошлый прогон с теми же входами и `prepare … --reuse-from <run_id>` на стадию: он сам
  отвечает её ответом того прогона, `submit` не нужен; ответы прошлых прогонов не копируй.

Анализ неполон, пока обязательная зависимость не прочитана: верни её контроллеру, область не расширяй самовольно; исключение файла по
политике не доказывает его нерелевантность. Вход, зависимости и снимок после scan не меняются до закрытия попытки.

## 3. Построй canonical из deterministic batches

Цикл каждой стадии модели (разметка, пачки генератора, ревью, автотесты): `prepare --run-root <run> --stage <id>` печатает `read` — сводку файлов модели `files_summary` с `list_path` (один вызов размера
на пачку; замер — `step_size`), `answer_slot` и `budget`; ответ модели — как есть в `answer_slot`; `submit` с теми же флагами принимает его (`--dry-run` — проверка без записи; `--transport-attempts 2` —
единственный повтор) и печатает квитанцию (`result.path`, `content_digest`, `output_bytes`, `warning`), не тело. `<id>` с номером ревизии (`…:r1`) называет `status`.

1. **Разметка.** `status`: `next` — `prepare` или `submit`, `stage` (`context-marker:baseline`; частями — `:requirements`, затем `:batch-NNN`).
   `tools.api prepare --run-root <run> --stage <id>` печатает `read` (файлы модели), `answer_slot`, `budget`; ответ модели — как есть в
   `answer_slot`; `submit` с теми же флагами принимает его (`--dry-run` — без записи; `--transport-attempts 2` — один повтор;
   последний `submit` сливает части). Импорт — [import.md](references/import.md).
2. **Пауза после разметки.** Пробелы без наблюдаемого канала, ожидаемого результата или доступа — `state: scope_pause` (`scope_gaps`).
   `prepare --stage scope` печатает пробелы, `answer_slot`, формы ответа. Остановись: покажи человеку
   список, спроси: исключить или продолжать; его слова — в `answer_slot`
   (`{"decision":"exclude","excluded":["SREQ-…"],"reason":"<слова>"}` или `{"decision":"continue"}`), затем `submit --stage scope` (исключить можно и без пробелов, до плана). Без ответа
   `submit --stage plan` отказывает `SCOPE_PENDING`. Решение за человеком; другое — новый прогон. `scan --no-pause` просит человек, ты его не добавляешь.
3. **План пачек.** `status`: `next` — `prepare` или `submit`, `stage: plan`. `prepare --stage plan` печатает `answer_slot`, форму и счётчики; в слот —
   `{"subject": "<название предмета проверки по-русски>"}` и ничего больше: header, план и скелеты код строит из журнала, `submit --stage plan` публикует
   их одним вызовом (`--dry-run` — без записи).
   Пачки делит код по оценке ответа и окну `limits.step_window_bytes`; `notes` и `context` вывода покажи человеку как есть.
4. **Генератор.** `status`: `next` — `prepare` или `submit`, `stage: tc-generator:<batch>`; пачки строго по очереди (`STAGE_WAITS` — предыдущая не принята). `prepare` генератору получает
   `--model-id` и `--invocation-id` той сессии, которая пишет кейсы (как у `prepare` ревью: от этой метки зависит независимость ревью); входы берёт
   из журнала (capabilities прошлых пачек публикует сам) и один раз печатает `nonce_note`: вставь «Контрольное слово: <nonce>» в начало задачи генератору (в ответ и файлы
   слово не копируй). **Фрагмент** — голый объект без конверта `artifacts`, образец — [fragment-sample.md](references/fragment-sample.md), схема — `schemas/candidate-fragment.schema.json`;
   `context_receipt` и digest-поля ставит код, не пиши их (неверные отказывает `MODEL_RESPONSE_DIGEST_MISMATCH` — единственный повтор). Ответ — файл данных: собирай его по частям, кейс за кейсом; скрипты, которые импортируют или вызывают код пакета, не пиши. Кладёшь его в `answer_slot`; `submit --dry-run` проверяет фрагмент без записи. Отказ — ошибка ответа: единственный повтор с
   `retry_prompt` (`--transport-attempts 2`); записанный ответ не заменить.
5. **Сборка.** Её делает `submit` последней пачки до записи ответа и печатает `candidate` (`document_digest`, `warnings`); файлов нет — `prepare` ревью берёт кандидата из журнала.
   `MANUAL_SHARE_HIGH` — скажи человеку: большинство шагов ручные, автотестов по ним не будет, контрольных ошибок ревью может не быть — смотри отчёт. Проверь полноту по исходному документу; недоказанное — gap,
   не `FULL`-обещание. Human-facing поля — на русском, technical tokens exact; условия работы над тестами (заглушки, режим запуска)
   в предусловия, шаги и ожидания не пишутся — ревьюер вернёт весь набор. Потолка на число требований и кейсов нет.
6. `prepare` ревью (§4) записывает кандидата сам; файлов до вердикта нет. HTML/CSV никогда не входят в model input.

## 4. Выполни одно authoritative canonical review

Ревью — те же `prepare` и `submit`, ревьюер между ними:

1. `status` называет `prepare --stage tc-reviewer:canonical`; флаги: `--invocation-id <метка сессии ревьюера>` и `--fresh-context yes|no`. `yes` — честное
   заявление: ревьюер отдельный субагент или вызов без истории генерации; метка обязательна и не равна метке генератора (`REVIEW_INVOCATION_REQUIRED`,
   `INDEPENDENCE_UNVERIFIED`). `no` — ревью в той же сессии, метка не нужна; допустимо после согласия человека на SELF (§1, `--accept-self-review`). `prepare` строит пакет, границу
   и запрос ревьюера, печатает `answer_slot`, `reviewer_view`, `for_human`; повтор ничего не меняет, другой ревьюер — `REVIEW_ALREADY_OPEN`.
2. `tc-reviewer` в fresh invocation читает `reviewer_view` (не полный JSON) и пишет ответ в `answer_slot`; файлы проекта сверх пакета он называет
   ключом `evidence: ["путь", …]` верхнего уровня файла. Карту канареек не читай и ревьюеру не показывай; роль, канарейки, бюджет — [review.md](references/review.md).
3. `submit --stage tc-reviewer:canonical` проверяет ответ до записи, записывает его, выбирает ревизию и кладёт набор в `<run>/published`. Отказ ничего не пишет —
   единственный повтор (`--transport-attempts 2`); `--dry-run` — только проверки. Ревью не довести до вердикта — `submit --abort REVIEW_CONTEXT_LIMIT` или
   `REVIEW_UNUSABLE`, затем `finish`.

Скажи человеку по `outcome` ответа `submit`: `ACCEPTED_WHOLE` — набор принят, `finish`. `PARTIAL` — принят частично: годные кейсы в наборе, отклонённые лежат отдельно
(`*.REJECTED-CASES.*`, в Zephyr не импортировать), пробелы покрытия — в итоге; если `status` называет `prepare tc-generator:rework-1`, отклонённые кейсы идут на доработку
один раз — `prepare`/`submit` `tc-generator:rework-1`, затем `tc-reviewer:rework-1` (метка и `--fresh-context` как у первого ревью; `no` — без метки); итог выходит после него, `finish` до этого не зови. `REWORK` — набор отклонён целиком, `finish` закроет прогон без CSV. Без host isolation evidence ветку не принимай
(`independence_unverified`).

## 5. Закрой попытку

`python -m tools.run_pipeline finish --project <abs> --run <run_id>` — одна команда проходит закрытие. Результат draft/artifact-only:
`verification=NOT_APPLICABLE`, `accepted=false`; green full-pipeline exit 0 запрещён.

## 6. Закрытие

`finish` сам проходит trace → pre-finalization trace → finalization → terminal result; повторный вызов даёт тот же итог. `FINISH_FACT_UNDETERMINED`
называет, чего не хватает: сделай этот шаг, не собирай факты вручную. Цепочка не может продолжиться — `finish --abandon <код>` из `fix`
(закрытый список). `valid=false` финализации — terminal с `FINALIZATION_INVALID`, `accepted=false`.

Если в `.skillsrc` задан `projection.zephyr.template`, после закрытия один раз: `python -m tools.zephyr_template_export --project <abs> --run
<id> --cases <bundle .zephyr-scale.csv>` — рядом ляжет `.zephyr-import.csv` для импорта в Zephyr. Ошибки `ZEPHYR_TEMPLATE_*`,
`ZEPHYR_EXPORT_*` — покажи `hint` человеку как есть; шаблон не правь.

В конце (после закрытия или остановки) один раз: `python -m tools.run_pipeline report --project <abs> --run <id>` — пишет `report.md`,
`report.json`, `questions-for-analyst.md`; назови человеку пути. `review_notes` в выводе `finish`/`status` — назови число и путь одной строкой;
кейсы не меняй, вердикт не пересматривай.

## Resume и дрейф

Если контекст занят больше чем наполовину, после завершённого шага попроси человека `/clear` (или новую сессию) и «продолжай test-skills
<run_id>». Затем `python -m tools.run_pipeline status --project <abs> --run <run_id>` и продолжай с `state` и `next` (`waiting_for: model` — ответ на запрос из
`pending_request_path`; `human` — жди человека). `next.verb` — имя команды шага; аргументы — по SKILL, `--help` и результатам
tools. Подтверждённое журналом не повторяй. `state: left` (`RUN_LEFT`) — прогон оставлен до ревью (`finish --abandon LEFT_BEFORE_REVIEW`): `scan_command`
(простой scan того же запроса). `recovery` — скажи человеку.

`status: drift` — передай человеку `drift.changed` и `drift.fix` как есть: вернуть файлы или начать новый прогон командой `drift.command`;
файлы не правь, прогон сам не начинай. После `finish` с `next_run` (ТРЕБУЕТ ДОРАБОТКИ) — команда `next_run.command` как есть.

## Stop conditions

Остановись без guess при invalid/unsupported version, ambiguous module, unsafe path, required invention, unavailable tool/model,
schema/semantic/provenance failure, secret exposure, baseline drift, stale digest/readback, unproved reviewer isolation или выходе за
authorized scope. Сохрани factual partial/waiting evidence и не выдавай `implemented_unverified` за verified readiness.
