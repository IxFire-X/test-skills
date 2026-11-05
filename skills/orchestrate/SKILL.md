---
name: orchestrate
description: Использовать, когда пользователь просит тест-кейсы приложения по согласованным требованиям, всей спецификации OpenSpec либо выбранному change со связанной регрессией.
permissions:
  read: "файлы проекта по inventory с исключениями; --docs, --reference и --cases только внутри проекта"
  write: "<project>/.pilot-runs/<run_id>/; <project>/.skillsrc при создании; каталог публикации --output-dir внутри проекта (не внутри .git)"
  network: "нет"
  external_delivery: "нет"
  dependencies: "пакет ничего не устанавливает; pip агент не запускает"
  rollback: "удалить <project>/.pilot-runs/<run_id>/ и созданный <project>/.skillsrc"
---

# Оркестрация frozen pilot Pipeline 4.0 — тест-кейсы

Ты — controller в совместимой model-enabled CLI. Не запускай из Python модель и не передавай model credentials. Python tools только
детерминированно проверяют и публикуют/readback-проверяют артефакты; сборку и тесты проекта они не запускают. Профиль один —
`cases-only-v1`. Автотесты (`local-pilot-v1`, режим Б) — только в сборке разработки, в эту поставку они не входят.

Это карта стадий. Аргументы команды — `python -m tools.api <команда> --help`; что делать дальше — `next_step` в `status`.
Прочитай [контракт оркестрации](references/orchestration-contract.md). До работы — `python -m tools.doctor --root <pack-root>`.
Используй только exact registered Skills и stages из `contracts/pipeline.json`. Роль читает свои инструкции, контракт, схему ответа и полные
входы; validators выполняются как код, их чтение не заменяет выполнения.

Пути `contracts/`, `schemas/`, `skills/`, `tools/` — от корня пакета; tools вызывай с cwd пакета и абсолютным `--project`. Функции пакета не
импортируй: шаг — `python -m tools.api <команда> --run-root <run> --attempt <attempt_id> …`; одна строка JSON: `status: ok` и `result`,
`invalid` (код 1) или `error` (код 2, `code`, `message`, `fix`). Большой результат — `--out <run>/tmp/<имя>.json` вместе с `--run-root <run>`
(только в `tmp/` прогона, не в исходники проекта). Ход работы — `run_pipeline status` и поля JSON последних событий; `head`/`tail` по строкам
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

Ревьюер должен быть в свежем контексте. Строка `review:isolation` в `ready` (уровень OK — ничего исправлять) подсказывает, открывает ли
твоя CLI отдельный subagent. Если нет — спроси человека до scan, дословно: «Ревьюер будет работать в той же сессии, без свежего
контекста. Принять такой прогон с пометкой SELF?». Только после явного «да» повтори scan с теми же `--project`, `--docs`, `--profile`,
`--module` и `--accept-self-review` (готовая команда — `details.self_review_scan`). Без флага `finish` закрывает попытку с
`INDEPENDENCE_UNVERIFIED`. Если в `.skillsrc` стоит `review: {"self": true}` — ключ ставит человек, scan принимает SELF сам.

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
- **Повтор разметки.** Поле `reuse` вывода scan называет прошлый прогон с теми же входами и даёт команды `model-request --reuse-from
  <run_id>` и `model-response --artifact <файл>`: повтори их или размечай заново; без `--reuse-from` ответы прошлых прогонов не копируй.

Анализ неполон, пока обязательная зависимость не прочитана: верни её контроллеру, область не расширяй самовольно; исключение файла по
политике не доказывает его нерелевантность. Вход, зависимости и снимок после scan не меняются до закрытия попытки.

## 3. Построй canonical из deterministic batches

Каждый запрос стадии (`model-request`) и ответ (`model-response`) — разные команды: `model-request --stage <stage> --model-id <id>
--invocation-id <id>` (входы из журнала; у разметки — `--input-digest` по порядку входов), затем `model-response --stage <stage> --artifact
<ответ.json>` — ответ как есть. `model-response` печатает квитанцию (`result.path`, `content_digest`, `output_bytes`, `warning`), не тело; тело —
`read-model-artifact`. `--out` ему не нужен: с `--out` в stdout только `out`, `bytes`, `sha256`, квитанция — в файле. Перед шагом модели —
`step_size` (до `model-request` у частей разметки, после него у генератора и ревью). Сводка входов — `files_summary` с `list_path`: один
вызов размера на пачку; `tools.api step-inputs` — только если нужен список без замера. `--input-file` плана — абсолютный путь.

1. **Разметка.** `python -m tools.context_marker_parts plan --run-root <run>` показывает шаги и `input_digests`. `mode: single` — одна
   стадия `context-marker`; `mode: parts` — `context-marker:requirements` (только документы), затем по части `context-marker:batch-NNN`,
   затем `merge` (слияние кодом, не вызов модели); между частями можно `/clear`. Импорт готовых кейсов (`scan --cases`) — [import.md](references/import.md).
2. **Пауза после разметки.** Пробелы без наблюдаемого канала, ожидаемого результата или доступа — `next_step.code: SCOPE_PENDING` и
   черновик `questions-for-analyst.md`. Остановись: покажи человеку список и спроси, исключать ли требования (кейсы для них не пишутся) или
   продолжать как есть. Ответ — в `plan-batches --exclude <SREQ-id> --reason "<текст человека>"` или `--continue`: код пишет
   `scope-decision.json`; без ответа `plan-batches` и `publish-generator-input --kind plan` отказывают `SCOPE_PENDING`. Решение за
   человеком; другое — новый прогон. `scan --no-pause` просит человек, ты его не добавляешь.
3. **План пачек.** Header — `tools.api publish-generator-input --kind header --value <f>`; `tools.api plan --header <f> --requirements <f>
   --grouping <f> --project <abs> --input-file <ответ разметки> --context-receipt <f> --run-root <run> --attempt <id> --out-plan
   <run>/tmp/plan.json --out-published <run>/tmp/published.json` строит и публикует план (формы входов — [fragment-sample.md](references/fragment-sample.md#входы-plan)).
   Пачки делит код по оценке ответа и окну `limits.step_window_bytes`; `notes` и `context` вывода покажи человеку как есть.
4. **Генератор.** Пачки строго по очереди (`ordinal`), пачка k после принятого ответа k−1. `model-request --stage tc-generator:<batch>`
   берёт входы из журнала; с пачки 2 — capabilities прошлых пачек (`prior-capabilities --batch-id <id>`). В начало задачи генератору вставь
   «Контрольное слово: <nonce>» из вывода `model-request` (в ответ и файлы слово не копируй); `existing-tests.md` из `step-inputs` дай
   генератору вместе со входами. **Фрагмент** — голый объект без конверта `artifacts`, образец — [fragment-sample.md](references/fragment-sample.md),
   схема — `schemas/candidate-fragment.schema.json`; `context_receipt` — квитанция контекста пачки **целиком** (файл receipt как есть); digest-поля
   ставит код (`model-response` заполняет пропущенные, неверные отказывает `MODEL_RESPONSE_DIGEST_MISMATCH` — единственный повтор). Ответ — файл данных: собирай его по частям, кейс за кейсом; скрипты, которые импортируют или вызывают код пакета, не пиши.
   До `model-response` пачки — только `tools.api validate-fragment --fragment <f> --plan <f> --header <f> --context-receipt <f> --run-root <run>` (с пачки 2 — `--prior-fragment <f>`
   на каждую прошлую пачку: **записанный ответ**, `path` из вывода её `model-response`); `assemble` на части пачек всегда отказывает
   (`BATCH_PARTITION_CONFLICT`, фрагментов ещё нет). Отказ `validate-fragment` — ошибка ответа: единственный повтор с `retry_prompt`; лимит повторов
   (`--transport-attempts` ≤ 2) держишь ты, записанный ответ не заменить.
5. **Сборка.** `assemble` и `validate-canonical` — после ответа последней пачки, на всех фрагментах: `tools.api assemble --header <f> --plan <f> --fragment <f> … --run-root <run> --out-candidate
   <run>/tmp/candidate.json --out-receipt <run>/tmp/assembly-receipt.json`, затем `validate-canonical --document <run>/tmp/candidate.json` —
   bare canonical JSON `1.1.0` (`schema_version` в header). `--fragment` — файл ответа пачки как есть или записанный ответ (`read-model-artifact`). Проверь полноту относительно исходного документа; недоказанную полноту оставь
   gap, не `FULL`-обещание. Human-facing поля — на русском, technical tokens exact; условия работы над тестами (заглушки, режим запуска)
   в предусловия, шаги и ожидания не пишутся — ревьюер вернёт весь набор. Потолка на число требований и кейсов нет.
6. Revision 1 (`UNREVIEWED`, V4 derived bundle) публикует `review-open` из §4 сам. HTML/CSV никогда не входят в model input.

## 4. Выполни одно authoritative canonical review

Ревью — две команды и ревьюер между ними:

1. `tools.api review-open --run-root <run> --attempt <id> --output-dir <dir> --model-id <метка ревьюера> --invocation-id <id ревьюера>
   --fresh-context yes|no` — до вызова модели: публикует кандидата (если ещё нет), строит пакет и границу, пишет `REVIEW_REQUESTED` и запрос
   ревьюера. Повтор ничего не меняет; другой ревьюер — `REVIEW_ALREADY_OPEN`. `next_step` вывода — следующая команда.
2. `tc-reviewer` в fresh invocation; ответ — файл `<run>/tmp/reviewer-answer.json`. Кандидата он читает видом `tools.review_view`
   (`review-views/<sha>.md`), не полным JSON; размер шага — `step_size --role tc-reviewer --step tc-reviewer:canonical`.
3. `tools.api review-select --run-root <run> --attempt <id> --output-dir <dir> --verdict <файл>` — записывает ответ, закрывает сессию,
   выбирает ревизию. Отказ называет ошибку и ничего не пишет: единственный повтор стадии (`--transport-attempts 2`). Без `--verdict`
   берётся последняя ревизия журнала. `review-select --dry-run` — только при сомнении в ответе.

**`--output-dir`** — один каталог на всё ревью, внутри проекта и вне `.git`: `<run>/published` (там кандидат и HTML/Markdown/CSV для человека). Тот же
путь — в `review-open`, `review-select --run-root <run> --attempt <id> --output-dir <dir>` и `review-publish --run-root <run> --attempt <id> --output-dir <dir>`; вне проекта — `OUTPUT_DIR_OUTSIDE_PROJECT`. `--fresh-context yes` — честное
заявление: ревьюер отдельный субагент или вызов без истории генерации; `no` допустим только в прогоне, принявшем SELF (§1) — там флаг можно
опустить; иначе `FRESH_CONTEXT_REQUIRED` или `INDEPENDENCE_UNVERIFIED`. Бюджет сессии, `distinct_invocations` и `evidence_digest` считает
код; бюджет меньше пакета — `REVIEW_CONTEXT_LIMIT` сразу.

**Ревьюер.** Свежая сессия (`for_human`), ревьюер-субагент и его команды, канарейки, кардинальность сессии, бюджет и `review-abort` —
[review.md](references/review.md). Карту канареек не читай и ревьюеру не показывай.

`ПРИНЯТО` выбирает exact candidate. `AUTO_FIX_APPLIED` допускает один complete successor revision 2 (`successor_edits` — точные правки
человеческих полей, revision 2 строит код). Любая смысловая, частичная или третья правка — `REWORK/PARTIAL`. Без host isolation evidence
записывай `independence_unverified` и ветку не принимай. При отказе или terminal pre-verdict abort закрой отрицательную ветку через `finish`
с исходной причиной и `accepted=false`.

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
<run_id>». Затем `python -m tools.run_pipeline status --project <abs> --run <run_id>` и продолжай с `next_step` (стадия, действие, раздел
этого SKILL; при `pending_request_path` — ответ на этот запрос). `next_step.command` — шаблон с `<…>` вместо значений: шаг выбираешь по
SKILL, значения берёшь из результатов tools. Подтверждённое журналом не повторяй. `RUN_LEFT` — прогон оставлен до ревью (`finish --abandon LEFT_BEFORE_REVIEW`): выполни
`next_step.command` (простой scan того же запроса).

`status: drift` — передай человеку `drift.changed` и `drift.fix` как есть: вернуть файлы или начать новый прогон командой `drift.command`;
файлы не правь, прогон сам не начинай. После `finish` с `next_run` (ТРЕБУЕТ ДОРАБОТКИ) — команда `next_run.command` как есть.

## Stop conditions

Остановись без guess при invalid/unsupported version, ambiguous module, unsafe path, required invention, unavailable tool/model,
schema/semantic/provenance failure, secret exposure, baseline drift, stale digest/readback, unproved reviewer isolation или выходе за
authorized scope. Сохрани factual partial/waiting evidence и не выдавай `implemented_unverified` за verified readiness.
