# План исправлений по ревью 2026-10-05

Источник: `docs/review/2026-10-05-pipeline-review.md`. Инструкция: `docs/review/2026-10-05-fix-prompt.md`.

Статусы: `[ ]` открыт · `[x] fixed` · `[x] false-positive` · `[~] deferred`. После статуса — одна строка о сделанном, тест и файл.

## Исходное состояние

- HEAD: `455ef01` (ветка `main`), поверх него незакоммиченный WIP bounded-review (Task 6).
- SHA-256 от `git diff --binary`: `bd8ae7f5a235781c2378bfa439973f603ebd94eca8ca530c78f8f5f15db34dc5`
  (посчитан в Linux-копии рабочего дерева; git нормализует CRLF→LF по `.gitattributes`, поэтому на Windows значение должно совпасть).
- `git status --short` на начало работы:

```text
 M CONTRACTS.md
 M PIPELINE.md
 M QUICKSTART.md
 M README.md
 M USAGE.md
 M contracts/pipeline.json
 M docs/superpowers/plans/2026-10-04-portable-pipeline-reliability.md
 M docs/superpowers/specs/2026-09-01-portable-testing-skills-pilot-contract-erratum.md
 M release/manifest.json
 M schemas/autotest-reviewer-output.schema.json
 M schemas/compatibility-evidence.schema.json
 M schemas/event.schema.json
 M schemas/model-request.schema.json
 M schemas/pipeline.schema.json
 M schemas/release-manifest.schema.json
 M schemas/reviewer-session.schema.json
 M schemas/tc-reviewer-output.schema.json
 M skills/autotest-reviewer/SKILL.md
 M skills/autotest-reviewer/references/autotest-review-contract.md
 M skills/orchestrate/SKILL.md
 M skills/orchestrate/references/orchestration-contract.md
 M skills/tc-reviewer/SKILL.md
 M skills/tc-reviewer/references/review-verdicts.md
 M tests/helpers.py
 M tests/test_automation_revision_budget.py
 M tests/test_compatibility_contract.py
 M tests/test_contract_registry.py
 M tests/test_generated_delta.py
 M tests/test_pilot_state.py
 M tests/test_preexecution_finalization.py
 M tests/test_project_native_pytest.py
 M tests/test_resume_and_lineage.py
 M tests/test_reviewer_protocol.py
 M tools/automation_validation.py
 M tools/compatibility_contract.py
 M tools/contract_check.py
 M tools/orchestrate_test_case_revision.py
 M tools/pilot_state.py
 M tools/render_contract_docs.py
 M tools/run_pipeline.py
?? docs/review/
?? docs/superpowers/plans/2026-10-05-bounded-review-prompt.md
?? docs/superpowers/plans/2026-10-05-bounded-review.md
?? schemas/review-part-output.schema.json
?? schemas/review-plan.schema.json
?? tools/review_parts.py
```

## Условия работы этой сессии (отклонения от инструкции)

- Сессия идёт в облачной Linux-среде без командной строки на Windows-машине. Репозиторий (вместе с `.git`) скопирован в облако, правки и адресные тесты выполняются там на Python 3.13.16, pytest 9.1.1, jsonschema 4.26.0. Изменённые файлы записываются обратно в `D:\AI-Projects\test-skills` в конце фазы.
- Полный прогон на Windows запускается через управление компьютером (PowerShell, лог в файл) — решение пользователя от 2026-10-05.
- PyPI в облаке закрыт: Python 3.11 и 3.12 в облаке не проверяются (нет `rpds-py`). 3.12 покрывается Windows-venv, 3.11 — только CI. Решение пользователя от 2026-10-05.
- Сессия 2026-10-06 (F7) идёт прямо на Windows-машине из Claude Code: PowerShell, venv `D:\AI-Projects\.runtime\test-skills-venv` (Python 3.12.14, pytest 9.1.1, jsonschema 4.26.0), логи полных прогонов — вне репозитория (`D:\AI-Projects\windows-full-20261006.log`, `D:\AI-Projects\windows-ci-gate-20261006.log`).
- В шести файлах рабочего дерева были окончания CRLF (`schemas/event.schema.json`, `schemas/model-request.schema.json`, `tests/test_reviewer_protocol.py`, `tools/orchestrate_test_case_revision.py`, `tools/pilot_state.py`, `tools/run_pipeline.py`). `.gitattributes` требует LF, поэтому затронутые файлы записываются с LF.

## Замеры

Linux: облачная машина, 2 ядра, Python 3.13.16. «До» — код на начало работы, «после» — текущее дерево.

| Что | До | После |
| --- | --- | --- |
| Полный сьют, Linux (два шарда параллельно) | 1 ч 18 мин одним процессом (из ревью); первый шард старого кода — 20 мин 45 с, 3 падения | 700 passed, 1 skipped; шарды 2 мин 39 с и 7 мин 40 с, по часам 7 мин 40 с. Одним процессом через `tools.ci_gate`: 9 мин 30 с, код 0 |
| Полный сьют, Windows, Python 3.12 | 1 ч 44 мин (лог пользователя `reliability-full-20261005-r2`) | 26 мин 44 с одним процессом на `a5c735a` (4 падения тестов под POSIX, см. F7); после правок через `tools.ci_gate`: 697 passed, 5 skipped, 26 мин 23 с, код 0 |
| 400 событий подряд в один журнал | 62,3 с | 6,3 с |
| Запись одного события при 400 событиях в журнале | 279 мс | 19 мс |
| `derive_state` при 400 событиях | 353 мс | 11 мс |
| Сквозной тест с `exec` на `tests/fixtures/projects/python-pytest` | 28 с | около 7 с |
| `tests/test_review_fixes_execution.py` целиком | 607 с | 54 с |
| Ревью Petclinic, 16 кейсов, бюджет 200 КБ | 50 частей, 7,9 МБ | 12 частей, 2,1 МБ |
| Ревью Petclinic, 32 кейса, бюджет 200 КБ | 241 часть, 36,4 МБ | 75 частей, 12,8 МБ |
| Запуск через драйвер, `local-pilot-v1`, pytest-фикстура, 5 ответов модели | — | около 6 с кода между ответами |

## Фаза 1. Зелёный тест-сьют

- [x] F1 `test_generated_delta.py` — 4 теста — **fixed**: незавершённая миграция bounded-review в тестах: вердикт автоматизации теперь берётся из агрегата завершённых частей, ожидаемые сообщения отказа приведены к новой цепочке (отказ раньше — на снимке ревью и на durable-границе).
- [x] F2 `test_project_native_pytest.py` — 3 теста — **fixed**: на текущем дереве проходят все тесты файла (в базовом прогоне на Linux было 3 UNKNOWN вместо PASS); причина ушла вместе с исправлением дрейфа B3 и классификации запуска.
- [x] F3 `test_pipeline_acceptance.py::test_release_loader_uses_controller_owned_terminal_retry_receipt` — **fixed**: фикстура `complete_review_parts` давала ревьюеру автотестов model_id ревьюера кейсов; исправлена фикстура.
- [x] F4 `test_release_eval_policy.py::test_retained_native_rerun_observation_is_produced_by_public_controller` — **fixed**: проходит на текущем дереве (тот же корень, что F2).
- [x] F5 `test_schema_closed_world.py` — 2 теста (ждут 1.0.0, схемы 2.0.0) — **fixed**: ожидаемая версия схемы берётся из `contracts/pipeline.json` (`target_version`), а не из литерала 1.0.0.
- [x] F6 `test_run_and_module_reparse_components_never_escape` — вывод `cmd.exe mklink` читается как UTF-8 — **fixed**: вывод `mklink` читается байтами (`tests/helpers.py::make_junction`). Тест: `tests/test_pilot_state.py::test_junction_helper_survives_console_output_in_the_oem_code_page`. На Windows прошёл вместе с настоящими junction-тестами `test_pilot_state.py` (F7).
- [x] F7 Полный прогон на Windows и классификация падений — **fixed**: прогон 2026-10-06 на коммите `a5c735a` (Python 3.12.14): 4 падения, 691 прошёл, 6 пропущено за 26 мин 44 с. Все четыре — тесты под POSIX, не код пакета: `test_execution_adapters.py` (3 теста: фикстура создавала `mvnw`/`gradlew`, а на Windows ищется `mvnw.cmd`/`gradlew.bat`) и `test_m15_output_tail_*` (48 КБ через `python -c` → `WinError 206`, лимит командной строки; плюс CRLF текстового stdout). Исправлены фикстуры; добавлен Windows-тест Job Object с отсоединённым внуком, тест дочерней попытки после UNKNOWN снят со skip на Windows. После правок `tools.ci_gate`: 697 прошли, 5 пропущено (POSIX-only и symlink-тесты), 26 мин 23 с, код 0. Тесты: `tests/test_execution_adapters.py`, `tests/test_review_fixes_execution.py::test_m15_*`, `::test_r13_windows_job_stop_covers_a_detached_grandchild`, `::test_r13_unknown_with_valid_proof_*`.

## Фаза 2. Баги высокой критичности

- [x] B1 Чтение статуса удаляет чужое событие (`pilot_state._events`) — **fixed**: межпроцессная блокировка `.pilot-runs/<run>/.lock` (fcntl.flock / msvcrt.locking, реентерабельная, ожидание до 120 с); читатели не удаляют pending-маркеры, восстановление — только у писателя под блокировкой. Windows-ветка (`msvcrt.locking`) исполнена на Windows: три межпроцессных теста прошли (F7). Тест: `tests/test_review_fixes_state.py::test_b1_*` (5 тестов, 3 межпроцессных).
- [x] B2 Второй писатель оставляет pending-маркер (`_PublicationUnknown`) — **fixed**: писатель, обнаруживший чужое изменение журнала, снимает свой маркер перед `_PublicationUnknown`; маркер после сбоя уже совершённого коммита сохраняется как доказательство (как и раньше). Тест: `test_review_fixes_state.py::test_b2_*`.
- [x] B3 Побочные файлы сборки превращают PASS в UNKNOWN (дрейф, `.gitignore`) — **fixed**: инвентарь через `git ls-files -co --exclude-standard` с откатом на обход ФС, новые исключения по умолчанию; дрейф после старта — только изменённые/удалённые входы baseline. Тесты: `test_review_fixes_specs.py::test_b3_*`, `test_review_fixes_execution.py::test_b3_*`.
- [x] B4 Некомпилируемый тест остаётся в проекте (шлюз компиляции/сбора) — **fixed**: шлюз (pytest --collect-only / test-compile / testClasses) после `EXECUTION_STARTED`; провал → `NOT_RUNNABLE/GENERATED_TEST_INVALID`, очистка, вывод в run root; ошибка компиляции в основном запуске → `NOT_RUNNABLE`. Ревизия r2: попытка запускает проект один раз, поэтому исправленная ревизия генерируется в дочерней попытке с `retry_reason=GENERATED_TEST_INVALID`; `create_attempt` разрешает её один раз, если родитель не потратил r2 в статическом ревью и сам не является такой дочерней (решение вне зафиксированных: r2 реализована дочерней попыткой, а не внутри той же). Maven/Gradle-шлюз проверен только на argv. Тесты: `tests/test_review_fixes_execution.py::test_b4_*`, `tests/test_review_fixes_state.py::test_b4_*`.
- [x] B5 Многомодульные сборки Gradle/Maven запускаются неверно — **fixed**: запуск из корня сборки (`test.build_root`): Gradle `:<path>:cleanTest :<path>:test --tests … --no-daemon`, Maven `-pl <m> -am … -B -ntp`. Gradle проверен на реальном 8.14.3 офлайн; Maven — только argv (Central закрыт). Тест: `tests/test_review_fixes_adapters.py`.
- [x] B6 Два валидных фрагмента не собираются (порядок capabilities, слияние provenance, все диагностики) — **fixed**: capabilities сортируются по code point, provenance одинаковой capability объединяется, различие семантики → `BATCH_CAPABILITY_CONFLICT` с обоими batch_id; `BatchAssemblyError.diagnostics` несёт все находки с JSON-pointer. Тест: `tests/test_review_fixes_assembly.py`.
- [x] B7 Один blocker убивает всю автоматизацию — **fixed**: blocker блокирует только свой кейс; `BLOCKED` — если автоматизировать нечего; acceptance не менялся. Сквозной durable-прогон частично заблокированного документа не выполнялся. Тест: `test_review_fixes_projections.py::test_b7_*`.
- [x] B8 Zephyr CSV выгружает машинную модель (профиль v5) — **fixed**: профиль по умолчанию `zephyr-scale-step-row-24-v5` (только человеческие поля), v4 — opt-in. Тест: `tests/test_review_fixes_projections.py::test_b8_*`.
- [x] B9 Эвристика секретов скрывает обычный код и спеки — **fixed**: по имени — только явные секреты; по содержимому — сигнатуры и литерал ≥16; документы маскируются `[REDACTED:<rule>]` с `redactions` в инвентаре. Тест: `tests/test_review_fixes_specs.py::test_b9_*`.
- [x] B10 Сбой одного вызова ревьюера обрывает ревью — **fixed**: часть без валидной оценки переоткрывается новым invocation (`fail_review_part`, до 3 попыток, стадии `…:part-N`, `-try2`, `-try3`, каждая с запросом и ответом в журнале); `transport_attempts` до 3; блокировка различает `TRANSPORT`/`CONTENT`, транспортный сбой даёт `REVIEW_TRANSPORT_FAILED`, а не `REWORK`. CLI: `fail-part`, `block-part --failure-class`. Тест: `tests/test_review_fixes_review.py` (11 тестов).
- [x] B11 Автообнаружение модулей (Python без `src/`, POM-only, `requirements.txt`, Go, `init`) — **fixed**: Python без `src/`, пропуск `packaging=pom`, `requirements.txt` без исходников, Go без вопроса, `init` принимает ответ-путь. Тест: `test_review_fixes_specs.py::test_b11_*`.
- [x] B12 Reason phrase зависит от версии Python — **fixed**: своя таблица `tools/http_reason.py`, для 413/414/416/422 принимаются формулировки RFC 9110 и старые (414 тоже различался между 3.12 и 3.13, в ревью не упомянут). Тест: `tests/test_review_fixes_model.py::test_b12_*`.

Сопутствующие зафиксированные решения (закрываются вместе с пунктами M):

- [x] R12 Exit-коды cases-only (FATAL / `FINALIZATION_INVALID` / невалидный trace / `operational_reliable=false` → 2), параметр `controller_error` — **fixed**: cases-only: FATAL / `FINALIZATION_INVALID` / невалидный trace / `operational_reliable=false` → exit 2, валидный терминал → exit 1; в контракт добавлено правило `cases_only_fatal_invalid_closure_or_unreliable_evidence`; параметр `controller_error` работает и соответствует контракту (ревью назвало его неиспользуемым: его не передаёт ни один вызывающий, но сигнатура закреплена в `runtime_signatures`). Тест: `tests/test_review_fixes_state.py::test_r12_*`.
- [x] R13 Остановка процессов: Windows Job Object, POSIX-проверка группы, дочерняя попытка после `UNKNOWN` с proof — **fixed**: Windows Job Object (`WINDOWS_JOB_TERMINATED`), POSIX — проверка группы и ушедших через setsid; дочерняя попытка после UNKNOWN с proof. ctypes-ветка исполнена на Windows (F7): настоящий таймаут даёт `WINDOWS_JOB_TERMINATED`, отсоединённый внук (`DETACHED_PROCESS`) умирает вместе с Job Object — новый тест `test_r13_windows_job_stop_covers_a_detached_grandchild`; тест дочерней попытки после UNKNOWN больше не пропускается на Windows (`HOST_STOP_PROOF`). Тест: `test_review_fixes_execution.py::test_r13_*`.

## Фаза 3. Средние и низкие баги

Ядро состояния (`pilot_state.py`)

- [x] M01 Попытка не возвращается в `ACTIVE` после `WAITING_FOR_MODEL` — **fixed**: состояние возвращается в `ACTIVE` после события прогресса (модельные события, `CONTEXT_SELECTED`, `EXECUTION_*`). Тест: `tests/test_review_fixes_state.py::test_m01_*`.
- [x] M02 Прерванный `create_attempt` оставляет незафиксированный файл попытки — **fixed**: откат файла попытки и при `KeyboardInterrupt`/`SystemExit`; читатель пропускает ровно одну запечатанную незафиксированную попытку, не удаляя её. Тест: `tests/test_review_fixes_state.py::test_m02_*`.
- [x] M03 Дедупликация событий игнорирует позицию — **fixed**: повтор `WAITING_*` идемпотентен только пока попытка в этом состоянии; после прогресса записывается новое событие (решение: правка ограничена событиями ожидания, остальная дедупликация по содержимому сохранена). Тест: `tests/test_review_fixes_state.py::test_m03_*`.
- [x] M04 Нет fsync каталогов — **fixed**: fsync родительского каталога после create/replace/unlink на POSIX (`confined_output._sync_parent_directory`). Тест: `test_review_fixes_state.py::test_m04_*`.
- [x] M05 Windows: замена журнала без повтора; длинный временный путь (MAX_PATH) — **fixed**: повтор замены при `PermissionError` (5 попыток, 20–400 мс); временные имена `.<12hex>.tmp` не длиннее целевых. На Windows исполнено (F7): тесты M05 прошли с настоящей политикой повтора. Тест: `test_review_fixes_state.py::test_m05_*`.
- [x] M06 `_validate_authorization`: `KeyError` вместо `ValueError`; конфликт содержимого как «unsafe path» — **fixed**: неполная авторизация → `ValueError`; конфликт содержимого → «already exists with different content» (`OutputConflictError`). Тест: `tests/test_review_fixes_state.py::test_m06_*`.
- [x] M07 `git` отсутствует в PATH → `FileNotFoundError`; вызов без таймаута и с `safe.directory` — **fixed**: `git status` с таймаутом 120 с, без `safe.directory`; отсутствие git → `ValueError` «project Git state is unavailable». Тест: `tests/test_review_fixes_state.py::test_m07_*`.
- [x] M08 terminal-result, closure и disposition не проверяются схемой при повторном чтении — **fixed**: `_read_artifact` проверяет схемой тот же набор, что и публикация (terminal result, closure, disposition). Тест: `tests/test_review_fixes_state.py::test_m08_*`.

Исполнение (`run_tests.py`, `execution_adapters.py`)

- [x] M09 Вложенный pytest-модуль с конфигом в родительском `pyproject.toml` → другой classname — **fixed**: сопоставление nodeid с префиксом rootdir. Тест: `tests/test_review_fixes_execution.py::test_m09_*`.
- [x] M10 `addopts = -m smoke` / `PYTEST_ADDOPTS` → `FAIL/NO_TESTS_COLLECTED` и удаление файла — **fixed**: pytest 0 собранных по явному nodeid → `NOT_RUNNABLE/TESTS_DESELECTED`, файл остаётся. Тест: `tests/test_review_fixes_execution.py::test_m10_*`.
- [x] M11 Ложное доказательство остановки на POSIX, отсутствие на Windows — **fixed**: см. R13.
- [x] M12 `OSError` при запуске → `UNKNOWN` вместо `NOT_RUNNABLE` — **fixed**: `OSError` → `NOT_RUNNABLE/LAUNCH_FAILED`. Тест: `tests/test_review_fixes_execution.py::test_m12_*`.
- [x] M13 Исключения при сохранении артефактов после запуска не перехватываются — **fixed**: сбой сохранения артефактов → `UNKNOWN/ARTIFACT_PERSISTENCE_FAILED`. Тест: `tests/test_review_fixes_execution.py::test_m13_*`.
- [x] M14 Ненулевой exit при зелёном отчёте → `JUNIT_INVALID`; `JUNIT_MISSING` недостижим — **fixed**: `NONZERO_EXIT_GREEN_REPORT`, достижимый `JUNIT_MISSING`. Тест: `tests/test_review_fixes_execution.py::test_m14_*`.
- [x] M15 Хвост вывода 2 000 байт, только UTF-8 — **fixed**: хвост 64 КиБ, UTF-8 затем кодировка консоли. Тест: `tests/test_review_fixes_execution.py::test_m15_*`.
- [x] M16 Тестовый процесс наследует весь `os.environ` без фиксации — **fixed**: `execution.environment_inputs`: имена и дайджесты 8 переменных. Тест: `tests/test_review_fixes_execution.py::test_m16_*`.
- [x] M17 Maven: всегда `-Pdefault`, нет `-B -ntp` — **fixed**: `-P` только для объявленного профиля, всегда `-B -ntp`. Тест: `test_review_fixes_adapters.py::test_maven_*`.
- [x] M18 `python -m tools.run_tests` расходует запуск попытки без receipt — **fixed**: CLI `tools.run_tests` публикует receipt и проходит шлюз. Тест: `tests/test_review_fixes_execution.py::test_m18_*`.
- [x] M19 `company_runner.py`: расхождение статусов, мёртвый код — **fixed**: статусы и имена профилей приведены к валидатору; модуль остаётся неподключённым — вопрос пользователю, удалять ли. Тест: `tests/test_review_fixes_execution.py::test_m19_*`.

Модель кейсов и проекции

- [x] M20 Blocker на `/inputs` только при несвязанном path-плейсхолдере — **fixed**: blocker на `/inputs` для неизвестного поля тела. Тест: `tests/test_review_fixes_projections.py::test_m20_*`.
- [x] M21 Литерал несовместим с именованным типом — **fixed**: литерал совместим с именованным типом того же представления. Тест: `tests/test_review_fixes_projections.py::test_m21_*`.
- [x] M22 Сборка возвращает только первый код ошибки без пути — **fixed**: закрыто вместе с B6: все диагностики схемы фрагмента и аудита сборки с путями. Тест: `tests/test_review_fixes_assembly.py::test_m22_*`.
- [x] M23 Регулярки в assertions не разбираются при валидации — **fixed**: regex разбирается `portable-regex-v1` при валидации (`SEMANTIC_PORTABLE_REGEX`). Тест: `tests/test_review_fixes_projections.py::test_m23_*`.
- [x] M24 HTTP-шаг без тела требует буквальную фразу; path-параметры в HTML как `{ownerId}` — **fixed**: шаг без тела принимает любой текст; path/query-плейсхолдеры человекочитаемы. Тест: `tests/test_review_fixes_projections.py::test_m24_*`.
- [x] M25 Markdown-проекция: JSON только последнего ожидания, `1.`/`---` не экранируются, U+2028 — **fixed**: fence на каждое ожидание, экранирование `1.`/`---`, U+2028. Тест: `tests/test_review_fixes_projections.py::test_m25_*`.
- [x] M26 XML-экспорт: `sha256` без префикса; управляющие символы падают на экспорте — **fixed**: `sha256:` в XML-квитанции; управляющие символы ловит валидация. Тест: `tests/test_review_fixes_projections.py::test_m26_*`.

Спецификации и контекст

- [x] M27 OpenSpec только в корневом `openspec/` — **fixed**: OpenSpec в любом `**/openspec/`. Тест: `tests/test_review_fixes_specs.py::test_m27_*`.
- [x] M28 `###Requirement:` без пробела; `## Added Requirements`; BOM — **fixed**: заголовки без учёта регистра/пробелов, BOM. Тест: `tests/test_review_fixes_specs.py::test_m28_*`.
- [x] M29 `SREQ`-ID по сортировке текста, а не по порядку документа — **fixed**: SREQ-ID в порядке документа. Тест: `tests/test_review_fixes_specs.py::test_m29_*`.
- [x] M30 Каждый заголовок Markdown становится требованием — **fixed**: заголовок без текста, глоссарий, оглавление — не требования. Тест: `tests/test_review_fixes_specs.py::test_m30_*`.
- [x] M31 Файл больше 256 КБ → `CONTEXT_SELECTION_INVALID` для всего скана — **fixed**: большой файл → gap в квитанции, лимиты в `.skillsrc` (`limits`). Тест: `tests/test_review_fixes_specs.py::test_m31_*`.
- [x] M32 Фиксированные имена `build`/`generated`/`coverage` выбрасывают настоящие пакеты — **fixed**: исходные пакеты build/generated/coverage остаются. Тест: `tests/test_review_fixes_specs.py::test_m32_*`.

Конфигурация и инструменты

- [x] M33 `.skillsrc` привязан к ОС — **fixed**: `.skillsrc` хранит логические имена `python`/`mvnw`/`gradlew`/`mvn`, путь разрешается при запуске; старые пути читаются. Windows-раскладка проверена на Windows через адаптеры: `tests/test_execution_adapters.py` создаёт `mvnw.cmd`/`gradlew.bat` по `runtime_candidates` (F7). Тест: `test_review_fixes_adapters.py::test_logical_*`, `test_legacy_*`.
- [x] M34 Создание `.skillsrc` без эксклюзивного создания; слияние/миграция v2 недостижимы — **fixed**: эксклюзивное создание `.skillsrc`; мёртвый код слияния/миграции v2 удалён. Тест: `tests/test_review_fixes_specs.py::test_m34_*`.
- [x] M35 `orchestrate_test_case_revision` скрывает сообщения argparse — **fixed**: сообщение argparse в `diagnostics[0].message` и stderr, exit 2. Тест: `test_review_fixes_adapters.py::test_orchestration_cli_*`.
- [x] M36 `ci_gate` без pytest возвращает 1 вместо 2; дочерние процессы без таймаута — **fixed**: без pytest и при таймауте exit 2; таймауты дочерних процессов. Тест: `test_review_fixes_adapters.py::test_ci_gate_*`.
- [x] M37 `scan_project._java_imported_sources` обходит весь репозиторий и минует фильтр секретов — **fixed**: один индекс .java, фильтр секретов. Тест: `tests/test_review_fixes_specs.py::test_m37_*`.
- [x] M38 Тест `test_run_and_module_reparse_components_never_escape` (см. F6) — **fixed**: см. F6.

## Фаза 4. Производительность и токены

- [x] P01 Каждое событие перечитывает и перезаписывает весь `events.jsonl` — **fixed**: кэш проверенного префикса журнала на процесс (сверка байтов журнала и подписей файлов событий), разбирается и валидируется только новый хвост; 400 событий: 62,3 с → 6,3 с, одно событие при 400: 279 мс → 19 мс. Журнал по-прежнему заменяется атомарно (решение: O_APPEND не вводился — на протоколе replace держатся тесты восстановления, а стоимость определяла валидация). Тест: `tests/test_review_fixes_perf.py::test_p01_*`.
- [x] P02 Публичные функции повторно вызывают `derive_state` — **fixed**: `derive_state` и проверенные чтения квитанций мемоизируются в пределах одной операции (внешнее удержание блокировки run) и сбрасываются при любой записи; многошаговые команды помечены `run_operation`. Тест: `tests/test_review_fixes_perf.py::test_p02_*`.
- [x] P03 Кэш схем не работает — **fixed**: валидатор схемы компилируется один раз на процесс (ключ — подпись файлов схем), результаты кэшируются по дайджесту документа. Тест: `tests/test_review_fixes_perf.py::test_p03_*`.
- [x] P04 Неотслеживаемые файлы в `run-manifest.json`, попарный `uniqueItems` — **fixed**: убран попарный `uniqueItems` в `project_state` (уникальность путей обеспечивает код); структура манифеста не менялась. Тест: `tests/test_review_fixes_perf.py::test_p04_*`.
- [x] P05 Инвентарь строится 3 раза за `exec` — **fixed**: факты о содержимом файла (размер, дайджест, секреты) кэшируются по size/mtime/inode, перечитываются только изменившиеся файлы. Тест: `tests/test_review_fixes_perf.py::test_p05_*`.
- [x] P06 План ревью пересобирается на каждую часть — **fixed**: доказательство плана (повторная деривация из снимка) делается один раз на процесс для пары (снимок, план), валидация плана кэшируется по дайджесту. Тест: `tests/test_review_fixes_perf.py::test_p06_*`.
- [x] P07 Gradle `--rerun-tasks`, зашитый таймаут 600 с — **fixed**: `cleanTest` вместо `--rerun-tasks`; таймаут `test.timeout_seconds` (по умолчанию 600). Тест: `test_review_fixes_adapters.py::test_gradle_single_project_reruns_only_the_test_task`, `test_timeout_comes_from_skillsrc_and_defaults_to_600`.
- [x] P08 Повторная валидация большого canonical-документа — **fixed**: результат `validate_canonical_document` кэшируется по дайджесту документа. Тест: `test_review_fixes_perf.py::test_p08_*`.
- [x] P09 Время тест-сьюта — **fixed**: полный сьют на Linux (2 ядра, 2 параллельных шарда): было 1 ч 18 мин, стало около 8–10 мин; точные числа — в таблице замеров.
- [~] T01 Машинная модель шага в 10 раз больше человеческого текста — **deferred**: размер машинной модели шага — следствие слияния кейса и автоматизации в одном canonical (A3), вне объёма этой работы; нужен отдельный запрос пользователя на разделение слоёв.
- [x] T02 Текст требования повторяется в provenance — **fixed**: provenance-метка больше не повторяет текст требования (`<файл> — <раздел>`), текст связан полем `digest`. Тест: `test_review_fixes_perf.py::test_t02_*`.
- [x] T03 Входы ревьюеров дублируются — **fixed**: повторяющееся содержимое внутри одной части ревью передаётся один раз (`content_ref` на первое вхождение). На примере Petclinic (бюджет 200 КБ): 16 кейсов — 50 частей / 7,9 МБ → 12 / 2,1 МБ; 32 кейса — 241 / 36,4 МБ → 75 / 12,8 МБ. Квадратичный перебор пар остаётся (A5 вне объёма). Формат конверта части изменился: незавершённые ревью старого формата надо начинать новой попыткой. Тест: `test_review_fixes_perf.py::test_t03_*`.
- [x] T04 Бюджет ревью в байтах, нет инструмента пересчёта в токены — **fixed**: новый `python -m tools.review_budget`: консервативный пересчёт лимита в токенах в `--input-byte-budget` / `--response-reserve-bytes` без токенизатора. Тест: `test_review_fixes_perf.py::test_t04_*`.

## Фаза 5. Документация

- [x] D01 Одна сессия ревьюера и `EVIDENCE_*` (HOW-IT-WORKS §5, USAGE §5, orchestration-contract, PIPELINE) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D02 r2 «возвращает ревьюер» (HOW-IT-WORKS §5) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D03 Envelope `tc-generator-output` 5.0.0 (tc-generator/SKILL.md) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D04 «Run создаётся до scan» (orchestrate/SKILL.md) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D05 «Всё делается штатными командами» — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D06 Retry после доказанной остановки (README, HOW-IT-WORKS §7) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D07 «Создаёт только свои файлы тестов и evidence» (README) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D08 Java только через `mvnw`/`gradlew` (java-junit5.md, HOW-IT-WORKS §7) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D09 `SREQ`-ID сортируются по ссылке provenance (context-artifact-contract) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D10 «Работает на Python 3.11+» (README, QUICKSTART, CI) — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).
- [x] D11 Пример Petclinic в README без оговорки о старом манифесте — **fixed**: документы приведены к коду (см. раздел «Фаза 5» ниже в журнале).

## Фаза 6. Драйвер

- [x] A1.1 `tools/pipeline_driver.py`: `next`, `submit`, `status` — **fixed**: `tools/pipeline_driver.py` + `tools/pipeline_driver_automation.py`: `next`/`submit`/`status`, задачи `llm`/`ask_user`/`done`, отклонение невалидного ответа без публикации, идемпотентный `submit`, `--failed` для ревью, продолжение после обрыва. Тест: `tests/test_review_fixes_driver.py` (17 тестов).
- [x] A1.2 Профиль `cases-only-v1` целиком — **fixed**: context-marker → генерация → сборка → ревью по частям → выбор → закрытие. Тест: `test_driver_cli_drives_cases_only_run_to_done`.
- [x] A1.3 Профиль `local-pilot-v1` — **fixed**: автоматизация r1, статическое ревью, одна исправленная ревизия r2, материализация, `exec`, вопрос о дочерней попытке после `GENERATED_TEST_INVALID`. Тесты: `test_driver_drives_local_pilot_run_to_passing_execution`, `..._one_regeneration_after_broken_generated_test`, `..._corrected_automation_revision_after_auto_fix`, `..._rejected_static_review_without_materialization`. Проверено только на pytest-фикстуре, Linux.
- [x] A1.4 Новый `skills/orchestrate/SKILL.md` — **fixed**: SKILL сокращён с 430 до ~190 строк: цикл next → задача → submit; прежняя последовательность перенесена в `references/manual-sequence.md` для диагностики.
- [x] A1.5 Сквозной тест драйвера для обоих профилей — **fixed**: ответы модели — сохранённые JSON в `tests/fixtures/driver/`; оба профиля доходят до `done`.

## Журнал решений

Решения, принятые вне раздела «Зафиксированные решения» инструкции:

- B1/B2: журнал по-прежнему переписывается атомарно целиком (без `O_APPEND`); стоимость снята кэшем проверенного префикса. Блокировку `<run>/.lock` берут и читатели: чтение короткое, зато не видит полузаписанное состояние. Читатель никогда не чинит журнал и не удаляет pending-маркеры — это делает только писатель под блокировкой.
- M03: повторное событие не записывается только для событий ожидания (`WAITING_*`); остальные события остаются как были.
- B4 (решение 4): исправленная ревизия после `GENERATED_TEST_INVALID` реализована как одна дочерняя попытка с `retry_reason=GENERATED_TEST_INVALID`; бюджет проверяет `create_attempt`. Дочерняя попытка заново проходит кейсы и ревью, потому что принятый набор кейсов принадлежит попытке.
- Версии схем при аддитивных изменениях не повышались (новые необязательные поля и значения); `target_version` в контракте не менялся.
- B12: фраза причины для кода 414 тоже различается между версиями Python — добавлена в общий список допустимых.
- T03: формат конверта части ревью изменился (`content_ref`). Незавершённые ревью старого формата надо начинать новой попыткой.
- T01 отложен: размер машинной модели шага — следствие общего canonical (A3 вне объёма).
- M19: `company_runner.py` приведён к валидатору, но остаётся неподключённым. Удалять или подключать — решение пользователя.
- `evidence_pairs` удалён из контракта и схемы: поле нигде не читалось.
- R13/M11: Job Object на Windows сделан через `ctypes`; ветка покрыта тестами с подменой и, с 2026-10-06, настоящими процессами на Windows (таймаут, отсоединённый внук, дочерняя попытка после UNKNOWN).
- Фаза 5: документы приведены к коду, а не наоборот. D01 — описаны сессии ревью по частям и состояния `EVIDENCE_*`; D02 — r2 строит controller из исправлений; D03 — envelope генератора; D04 — run создаёт `scan`; D05 — перечислено, у каких шагов есть команда; D06 — retry только при доказанной остановке процессов; D07 — перечислены все места записи; D08 — системный `mvn` разрешён закрытым адаптером; D09 — порядок `SREQ`; D10 — Python 3.11–3.13; D11 — оговорка у примера Petclinic.
- A1: драйвер хранит рабочие файлы рядом с run (`<run_id>.driver/`), а не внутри него: каталог run остаётся только журналом и квитанциями. Драйвер продолжает только run, созданный его же `next`.
- A1: порядок кейсов и требований задаёт модель (это содержание); `display_order`, порядок категорий, capabilities, блокеров и связей автоматизации расставляет код.
- A1: если `scan` разбил контекст проекта на несколько квитанций, драйвер публикует ещё одну, объединяющую все выбранные файлы, и привязывает генерацию к ней. Все три роли, читающие код, получают один и тот же полный набор файлов; размер входа виден в поле задачи `input_bytes`. Первая версия драйвера давала генератору кейсов только первую квитанцию — исправлено 2026-10-06 по замечанию пользователя. Тесты: `test_generator_reads_every_context_portion_through_one_combined_receipt`, `test_child_attempt_keeps_the_combined_context_receipt`.
- A1: для отказа статического ревью автотестов введены отдельные причины `AUTOMATION_REVIEW_REJECTED`, `AUTOMATION_REVISION_BUDGET`, `AUTOMATION_REVIEW_CONTEXT_LIMIT`, `AUTOMATION_REVIEW_TRANSPORT_FAILED`: причины `REWORK` и `REVIEW_CONTEXT_LIMIT` по правилам результата относятся к ревью кейсов, которое в этой ветке принято.
- A1: если хост не может дать ревьюеру свежий контекст, драйвер останавливается с `REVIEWER_ISOLATION_UNAVAILABLE` и не ведёт ревью, которое заведомо не будет принято как независимое.
- A1: вопрос о дочерней попытке после `GENERATED_TEST_INVALID` задаётся пользователю, а не решается автоматически: попытка повторяет все вызовы модели.
- F7 (2026-10-05): терминалы на компьютере пользователя доступны управлению только для кликов, без ввода текста. Обходить это ограничение запуском скрипта через Проводник не стал: команду полного прогона запускает пользователь, лог разбирается после.
- F7 (2026-10-06): полный прогон запущен из Claude Code на Windows. Все четыре падения — тесты, написанные под POSIX, а не код пакета: (1) `test_execution_adapters.py` создавал файл `mvnw`/`gradlew`, тогда как `runtime_candidates` на Windows по правилу M33 ищет `mvnw.cmd`/`gradlew.bat` — фикстура создаёт host-специфичный файл; (2) `test_m15` передавал 48 КБ текста через `python -c` — на Windows это `WinError 206` (лимит командной строки 32 767 символов), `run_subprocess` честно вернул `OS_ERROR`; текст теперь идёт через файл скрипта и `sys.stdout.buffer`, потому что текстовый stdout на Windows превращает LF в CRLF. Код пакета не менялся. Добавлен Windows-тест Job Object с отсоединённым внуком; тест дочерней попытки после UNKNOWN теперь идёт и на Windows.

Не проверено:

- Windows: проверено полным прогоном 2026-10-06 (блокировка через `msvcrt`, повтор `os.replace`, Job Object с настоящими процессами, junction в тестах, драйвер на pytest-фикстуре). Не проверено на Windows: Maven/Gradle с реальными сборками.
- Maven: только состав команды и разбор отчётов (Maven Central из облака закрыт). Gradle проверен офлайн на 8.14.3.
- Python 3.11: только компиляция файлов и CI. Python 3.12 проверен полным прогоном на Windows 2026-10-06, 3.13 — на Linux.
- Драйвер на живой модели и на Java-проекте не запускался.

## Предлагаемые коммиты по фазам

2026-10-06: по решению пользователя фазы 1–6 закоммичены одним коммитом `a5c735a` (`fix: close pipeline review findings and add deterministic driver`) в ветке `review-fixes-2026-10-05`; исправления по F7 — отдельным коммитом в той же ветке. Push и merge в `main` — только после разрешения пользователя. Ранее предлагавшиеся сообщения (по одному на фазу) сохранены для истории:

1. `test: make the suite green on Linux and Windows (LF, junction helper, contract-driven versions)`
2. `fix: run lock, journal recovery by writer only, compile/collect gate, drift and reason-phrase bugs (B1–B12, R12, R13)`
3. `fix: medium and low review findings in state, specs, projections, adapters and execution (M01–M38)`
4. `perf: cache validated journal prefix, schemas and inventory facts; deduplicate review inputs; add review_budget (P01–P09, T02–T04)`
5. `docs: align README, USAGE, HOW-IT-WORKS, QUICKSTART and skills with the code (D01–D11)`
6. `feat: deterministic pipeline driver next/submit/status and short orchestrate skill (A1)`
