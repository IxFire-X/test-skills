# ARCHITECTURE — карта кода «Камертона»

Для разработки и для сессий ИИ, которые правят пакет. В поставки не входит (нет в `pack_registry.ROOT_FILES`). Состояние: v0.8
после разреза ядра. Файл правят в том же коммите, что добавляет, переносит или удаляет модуль `tools/`: §4 сверяет
`tests/test_architecture_map.py`. Человеку — `HOW-TO-USE.md`.

## 1. Возможность → команда → где описано

| что нужно | команда (`<python пакета> -m tools.…`) | раздел |
|---|---|---|
| проверить копию пакета | `doctor` | HOW-TO §2 |
| готовность проекта | `ready --project … --docs …` | HOW-TO §3 |
| требования из OpenSpec, объём изменения | `ready` / `scan --docs openspec/…` | HOW-TO §3 «Требования в OpenSpec», «Когда запускать» |
| прогон: кейсы по требованиям | агент по `skills/orchestrate/SKILL.md`: `run_pipeline scan`, затем `api prepare/submit <стадия>` | HOW-TO §4, `skills/orchestrate/references/` |
| где прогон и что дальше | `run_pipeline status` (следующий шаг — `stages`) | HOW-TO §7 |
| продолжить или взять кейсы прошлого прогона | `scan --reuse-from`, `status` | HOW-TO §7 |
| ревью в свежей сессии | `api prepare --stage tc-reviewer:canonical --fresh-context yes`; вид — `review_view`, правки — `review_edits` | HOW-TO §7 «Ревью в свежей сессии» |
| результаты и разделы документа | `assemble_sections` | HOW-TO §5 |
| кейсы из Zephyr, эталон, стиль команды | `scan --cases` / `--reference`, `.skillsrc style.samples`, `zephyr_template_export` | HOW-TO §6 |
| закрыть попытку, бросить прогон | `run_pipeline finish`, `finish --abandon <причина>` | HOW-TO §8 |
| отчёт прогона | `run_pipeline report` | HOW-TO §9 |
| автотесты (режим Б) | `api prepare --stage tc-to-autotest`, `run_pipeline materialize` → человек запускает → `run_pipeline ingest` | `HOW-TO-USE-MODE-B.md` |
| контракт и его документы | `contract_check --full`, `render_contract_docs` (только разработка) | `CONTRACTS.md`, `PIPELINE.md` |

## 2. Слои
Наверху модуля импортируют только тех, кто ниже; вверх — только внутри функции, с причиной. Цикл при импорте слоёв журнала
ловит `tests/test_journal_facade.py` (свежий интерпретатор).
```
команды        api (prepare/submit)  run_pipeline (scan → run_scan; status, finish → run_facts; report → run_report)  ready  doctor
шаги агента    stage_steps → stages (следующий шаг по фактам), scope_pause, step_size (бюджет входа)
предметные     canonical_document  batch_assembly  orchestrate_test_case_revision  revision_selection  review_view  review_canaries
               test_case_projections  publish_test_case_bundle  cases_import  build_context  project_inventory  existing_tests …
журнал прогона run_store < run_journal < run_receipts < review_ledger < pilot_state (фасад) < model_records < effective_canonical
  (ядро)       < run_closure < model_inputs < model_steps < scenario_observations < execution_journal (только mode-b)
основа         confined_output  output_guard  schema_validation → schema_engine  digests  clock  run_lock  json_cli  fixes
режим Б        exec_*, run_tests, generated_delta, … — ядро берёт их только через execution_gate (закрытые списки)
```

## 3. Правила, на которые опирается код
- **Процессов пакет не запускает.** Сборку и тесты проекта запускает человек: `materialize` печатает команду, `ingest` читает
  отчёты. `subprocess` есть только в `ci_gate` и `release_manifest` (разработка, `git`).
- **Режим Б отделён.** Модули режима Б — в `execution_gate.MODE_B_MODULES` и `EXEC_MODULES`; ядро получает их через
  `load_mode_b` / `from_mode_b`, никогда прямым импортом. Состав поставок — `pack_registry` (`CASES_ONLY_EXCLUDED_*`).
- **Запись — только через `confined_output`** (атомарно, сквозь ссылку не пишет): прогон — `<проект>/.pilot-runs/<run_id>/`, `--output` —
  под `docs/to_do`, в корень проекта — только `.skillsrc` (`_PROJECT_ROOT_FILES`). Пути, которые называет агент, — через `output_guard`.
  В код проекта пишет только режим Б (`generated_delta`, `materialize`) — по описи прогона.
- **Журнал — только дописывается.** Событие — `run_journal.append_event`, состояние — `derive_state`; квитанция — запечатанный
  файл (`run_store`: поле `digest` — sha256 канонических байт без него).
- **Фасад `pilot_state`** (PEP 562) отдаёт имя модуля-владельца и пишет подмену из теста во все модули журнала. Живёт до v0.9;
  новый код берёт имена у владельца.
- **Чужие скрытые имена не импортируются** (`tests/test_no_private_imports.py`, `EXCEPTIONS` только убывает). Соседу нужен
  помощник — у владельца появляется публичное имя (`run_root = _run_root`).
- **Хеши — `tools.digests`** (`digest_bytes`, `json_digest`), формат `sha256:<hex>`. Канонический JSON — три байтовых вида, не сводить:
  `digests.json_bytes` (ключи по порядку, без пробелов, без `\n`); `canonical_document.canonical_bytes` (то же, NaN — отказ;
  документ кейсов); `run_store.canonical_bytes` (с `\n`; файлы прогона, печать). Время — `clock`.
- **Отказ** — код и сообщение (`ValueError`, `HostStop`); что сделать — `fixes.fix_for(code)`, слова человеку — `fixes.HUMAN`.
- **Импорт — наверху модуля**, если не замыкает цикл, не идёт в режим Б, тест не подменяет имя у владельца и цель уже загружена
  на пути `api`/`run_pipeline`; иначе — внутри функции, и у каждого такого импорта своя причина.
- **Тексты для человека и модели** (отказы, подсказки) — по-русски; код, комментарии и сообщения коммитов — по-английски.

## 4. Модули `tools/`
Поставка: «обе» — universal (mode-b) и cases-only; «mode-b» — только поставка режима Б; «dev» — ни в одной.

| модуль | поставка | что внутри |
|---|---|---|
| **команды и шаги агента** | | |
| `api` | обе | JSON-команды `prepare`/`submit` стадий для агента |
| `run_pipeline` | обе | CLI `scan`/`status`/`finish`/`report`; `materialize`/`ingest` передаёт режиму Б |
| `run_scan` | обе | что вычисляет `scan`: входы и документы, выбор прогона, контекст и база, `state`/`next` |
| `run_facts` | обе | факты прогона только чтением — общие для `status`, `finish`, отчёта и команд исполнения |
| `stages` | обе | стадии прогона и одно правило «следующий шаг по фактам» |
| `stage_steps` | обе | цикл агента: `status` → `prepare` → модель → `incoming/<стадия>.json` → `submit` |
| `scope_pause` | обе | пауза после маркера контекста: решение человека до плана пакетов |
| `step_size` | обе | размер входа шага модели до вызова против бюджета шага; лимиты `.skillsrc` |
| `ready` | обе | готовность проекта к первому прогону: все проверки сразу, у каждой — команда исправления |
| `doctor` | обе | целостность копии пакета: файлы, SHA-256, концы строк |
| `json_cli` | обе | общий argparse для JSON-команд, один вывод ответа |
| `fixes` | обе | одна таблица `код отказа → что сделать` |
| **журнал прогона (ядро), снизу вверх** | | |
| `run_lock` | обе | блокировка ОС на прогон и типовые отказы журнала |
| `run_store` | обе | файлы прогона: канонические байты, печать, строгий разбор, схемы, корень, публикация и чтение |
| `run_journal` | обе | события и переходы, `derive_state` и кэш в пределах команды, `append_event`, попытки, замороженные входы |
| `run_receipts` | обе | квитанции попытки: `RECEIPT_DIRECTORIES` (вид → папка), публикация, чтение, диспетчер проверок видов |
| `review_ledger` | обе | журнал ревьюера, согласие на самопроверку, свежий контекст |
| `pilot_state` | обе | фасад старых имён журнала (до v0.9) |
| `model_records` | обе | где лежат запрос, ответ и выбор контекста шага модели; проверка источников разметки |
| `effective_canonical` | обе | принятый набор кейсов: публикация, чтение, проверка вида |
| `run_closure` | обе | закрытие попытки: артефакты, проверка, итог; вид `review-canaries` |
| `model_inputs` | обе | входы шага модели и привязки ответа, вход генератора, план пакетов, квитанции кейсов, `--reuse-from` |
| `model_steps` | обе | шаг модели: `publish_model_request`, `publish_model_stage_artifact`, код-шаг разметки OpenSpec |
| `scenario_observations` | обе | наблюдения сценариев и состояния проекта после прогона |
| `finalize_attempt` | обе | примитивы завершения попытки (фаза 7) |
| `execution_journal` | mode-b | журнал режима Б: проверки и публикации квитанций генерации и прогона автотестов |
| **документ кейсов, ревью, проекции** | | |
| `canonical_document` | обе | проверка канонического документа кейсов, его байты и ревизия |
| `batch_assembly` | обе | план пакетов и сборка кандидата кодом |
| `orchestrate_test_case_revision` | обе | публикация и выбор ревизии документа после ревью; круги доработки |
| `revision_selection` | обе | линия ревизий ревьюера, выбор принятого документа |
| `review_view` | обе | компактный вид кандидата, который читает ревьюер вместо полного JSON |
| `review_canaries` | обе | канарейки в виде ревьюера, кодовое слово генератора |
| `review_edits` | обе | AUTO_FIX ревьюера — точные правки полей; применяет код |
| `test_design_hints` | обе | подсказки по тест-дизайну в ответе генератора, без остановки |
| `test_case_projections` | обе | превью для человека и CSV Zephyr фиксированного профиля |
| `publish_test_case_bundle` | обе | публикация набора: JSON, HTML, CSV и Markdown |
| **проект и контекст** | | |
| `discover_project` | обе | опись запускаемых модулей проекта, только чтение |
| `init_skillsrc` | обе | `.skillsrc` из найденного в проекте |
| `skillsrc_manifest` | обе | проверка и нормализация `.skillsrc` |
| `module_ids` | обе | id модуля `.skillsrc` — путь его папки |
| `project_inventory` | обе | опись проекта, база исполнения, пакеты C-lite |
| `scan_rules` | обе | какие папки и файлы скан пропускает |
| `stack_catalog` | обе | каталог признаков стека сборки |
| `build_context` | обе | конверт маркера контекста из разрешённых документов и исходников |
| `context_marker_parts` | обе | маркер по частям: план частей |
| `context_marker_merge` | обе | слияние частей маркера кодом |
| `code_skeleton` | обе | скелет кодовых файлов, которые генератор не читает целиком: строки объявлений с номерами, без тел |
| `doc_sections` | обе | `--docs файл#раздел` — один раздел Markdown |
| `openspec_scope` | обе | объём прогона по одному изменению OpenSpec |
| `existing_tests` | обе | карта того, что уже проверяют тесты проекта (подсказка генератору и ревьюеру) |
| `java_text` | обе | лексер Java для карты тестов и проверок автотестов |
| `api_contract` | обе | контракт API проекта (OpenAPI 3, Swagger 2) для таблицы покрытия |
| **кейсы извне и отчёт** | | |
| `cases_import` | обе | готовые кейсы из CSV Zephyr (`scan --cases`) — источник прогона |
| `cases_scope` | обе | какие старые кейсы судит обновление: требования, которые трогает дельта OpenSpec |
| `reference_match` | обе | новые кейсы против эталона Zephyr (`scan --reference`) — подсказка |
| `style_samples` | обе | образцы стиля команды (`.skillsrc style.samples`) — форма, не факты |
| `zephyr_csv` | обе | один читатель CSV Zephyr Scale (обратный к проекции) |
| `zephyr_template_export` | обе | CSV импорта по шаблону экспорта Zephyr проекта |
| `assemble_sections` | обе | один документ по разделам: где прогоны и один CSV Zephyr |
| `run_report` | обе | отчёт прогона без кода и текстов кейсов, вопросы аналитику |
| **основа** | | |
| `confined_output` | обе | атомарная запись байтов только в `docs/to_do` проекта; `is_reparse` — ссылка или reparse-точка для читающих |
| `output_guard` | обе | пути, которые называет агент: публикуемая папка и входные файлы |
| `schema_engine` | обе | валидатор JSON Schema 2020-12 на stdlib |
| `schema_validation` | обе | строгая загрузка JSON и проверка по схемам `schemas/` |
| `validate_artifact` | обе | CLI: JSON-артефакт против схемы |
| `digests` | обе | sha256 байтов и канонического JSON без перевода строки |
| `clock` | обе | одна форма момента: UTC, микросекунды, `Z` |
| `pack_registry` | обе | из чего состоит пакет и поставки, SHA-256, сверка копии с `release/manifest.json` |
| `execution_gate` | обе | единственный шов ядра с режимом Б: закрытые списки, `load_mode_b` |
| **режим Б** | | |
| `execution_core` | mode-b | интерфейс ядра для файлов `exec_*` |
| `exec_commands` | mode-b | `materialize` (тесты в проект, команда человеку), `ingest` (отчёты), `exec` |
| `exec_runner` | mode-b | что берёт исполнитель из прогона человека: отчёт по байтам отчётов |
| `execution_adapters` | mode-b | закрытые построители запросов исполнения по стеку проекта, классификация итогов |
| `run_tests` | mode-b | прогон сгенерированных тестов с доказательством `(file_id, symbol_id)` |
| `generated_delta` | mode-b | транзакция дельты сгенерированных тестов в проекте |
| `automation_validation` | mode-b | проверка артефактов автоматизации и статические ревью |
| `automation_text` | mode-b | ответ `tc-to-autotest` так, как его читает ревьюер |
| `autotest_hints` | mode-b | подсказки по слабым проверкам в автотестах (Java, pytest) |
| `code_slices` | mode-b | точные срезы символов сгенерированных тестов для ревью |
| `build_trace_document` | mode-b | проекция трассы до завершения |
| `trace_check` | mode-b | квитанция аудита трассы |
| `quarantine` | mode-b | упавшие методы тестов как находки |
| **разработка** | | |
| `ci_gate` | dev | локальные ворота целостности (`git`) |
| `contract_check` | dev | проверка машинной правды `contracts/pipeline.json` и проекций |
| `render_contract_docs` | dev | `CONTRACTS.md`, `PIPELINE.md` из контракта |
| `release_manifest` | dev | `release/manifest.json` и списки файлов поставок |

## 5. Тесты
- Что запускать после правки и какие тесты ловят какой класс правки — таблица `tests/README.md`.
  Smoke: `-m "contract and not native and not slow"`; набор `contract` — `tests/contract/members.py`.
- Сторожа устройства: `test_no_private_imports` (скрытые имена), `test_mode_b_split` и `test_exec_split` (шов режима Б),
  `test_pack_profile` (состав поставок), `test_journal_facade` (фасад и свежий импорт слоёв), `test_release_manifest` (манифест), `test_architecture_map` (§4).
- Безопасность — `test_sec_*`; тексты ролей и их бюджеты — `tests/contract/test_role_bytes.py`; снимок ключей CLI —
  `tests/contract/api-result-keys.json`.
- Манифест пересобирать только из `git archive HEAD` (LF), не из рабочей копии.

## 6. Куда идти, чтобы изменить X
| X | где | что ещё |
|---|---|---|
| новый код отказа или его подсказка | `fixes.py` (таблица) | тексты — по-русски; `grep -rn "<код>" tests/` |
| новая стадия или шаг | `stages.py`, `stage_steps.py`, `contracts/pipeline.json` (`stage_registry`) | `render_contract_docs`, `test_stages` |
| новый вид квитанции | `run_receipts.RECEIPT_DIRECTORIES` + `RECEIPT_CHECKS` модуля-владельца | строка `published_artifacts` в `pipeline.json` (`contract_check` сверяет) |
| правило OpenSpec | `build_context` (разбор), `openspec_scope` (объём), `model_steps` (код-шаг разметки) | `ready` печатает объём |
| проекция кейсов в Zephyr | `test_case_projections` (профили `zephyr-scale-step-row-24-v*`), `zephyr_csv` (чтение), `zephyr_template_export` | профиль — и в `contracts/pipeline.json`, `contract_check` |
| роль модели | `skills/<роль>/SKILL.md`, `references/` | бюджет — `tests/contract/role_bytes.py` |
| ключ `.skillsrc` | `skillsrc_manifest`, `schemas/skillsrc.schema.json` | `init_skillsrc`, `ready` |
| ключ вывода CLI | модуль команды | снимок `tests/contract/api-result-keys.json` |
| код режима Б | `exec_*` или модуль из `MODE_B_MODULES` | ядро не импортирует его напрямую; `test_mode_b_split` |
| хеш или канонический JSON | `digests` | не путать с `run_store.canonical_bytes` (с `\n`) |
| новый модуль `tools/` | слой из §2, строка в §4 | манифест; поставка — `pack_registry` |

## 7. Словарь
- **прогон** (run) — папка `<проект>/.pilot-runs/<run_id>/`: журнал, квитанции, входы, `published/`; создаёт его `scan`.
- **попытка** (attempt) — проход стадий внутри прогона; текущая — последняя в `derive_state(root)["attempts"]`.
- **журнал** — `events.jsonl` прогона, только дописывается; **состояние** — то, что `derive_state` выводит из событий.
- **квитанция** — запечатанный JSON, который код публикует о шаге; **вид квитанции** — её тип (`RECEIPT_DIRECTORIES`).
- **стадия** — шаг прогона (`stages.STAGES`); у модели — `context-marker`, `tc-generator`, `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer`.
- **маркер контекста** — что модель взяла из документов и кода; **принятый набор** (effective canonical) — кейсы после ревью.
- **журнал ревьюера** — сессии ревьюера и свежесть их контекста (`review_ledger`).
- **режим Б** — генерация и прогон автотестов; **поставка** (профиль) — `universal`, `mode-b`, `cases-only` (`pack_registry`).
