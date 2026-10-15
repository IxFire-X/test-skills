# Отчёт: волна исправлений по независимому ревью волн 2–3 (2026-10-08)

Задание — `docs/review/2026-10-08-live-runs-prompt.md`, раздел 1. План и журнал решений —
`docs/superpowers/plans/2026-10-08-review-fixes.md`. Вход — независимое ревью
`docs/review/2026-10-08-waves-2-3-independent-review.md` (в репозиторий не коммитится: в нём личные адреса
почты; ниже ссылки по номерам пунктов). Исходное состояние — `c37e4d0` (995 passed, 5 skipped).

Каждая находка проверена тестом, который падал до исправления (запущен на старом коде перед правкой),
затем исправлена отдельным коммитом и запушена в `main`. Ни одна из 17 подтверждённых находок раздела 2.1
не опровергнута.

## 1. Подтверждённые дефекты (ревью, 2.1)

| № | Что было | Что сделано | Тест (падал до исправления) | Коммит |
| --- | --- | --- | --- | --- |
| 1 | разбор пишет `strength/<attempt8>/proposals.json`, обновление читает `strength/proposals.json` | одно место — `pipeline_driver_strength.proposals_path`, читатель берёт run и попытку из манифеста | `test_suite_update.py::test_a_survivor_triage_proposal_reaches_the_update_brief` (сквозной: предложение → бриф `update`) | `eea109d` |
| 2 | `FAIL` без исходов по методам под карантином → все файлы `RETAINED` | `quarantine_modes` без упавших методов — `None`: очистка §17 п. 2 | `test_quarantine_disposition.py::test_a_fail_without_failed_methods_falls_back_to_cleanup` | `191088f` |
| 3 | `NOT_RUN` = PASS, код возврата прогона набора игнорируется | ничего не выполнилось → `SUITE_NOT_RUN`, зелёный отчёт при ненулевом коде → `SUITE_RUN_NONZERO_EXIT`: `STOPPED`, exit 1, раздел «Остановка» в описании PR, манифест не трогается; `NOT_RUN` даёт `UNKNOWN` | сквозной без JDK (настоящий `mvnw`) `test_a_suite_run_that_ran_nothing_stops_instead_of_passing`, `test_not_run_methods_are_never_a_pass` | `57223bc` |
| 4 | ошибка компиляции вне методов → ремонт всех → карантин всего файла | ошибка вне методов (импорт, SUPPORT, продукт, без строки) → `SUITE_BUILD_BROKEN`, без ремонта и карантина; внутри метода — только этот метод; строки ошибок javac/Gradle | `test_suite_failures.py` (импорт, продукт, без строки, Gradle), сквозной `test_a_build_broken_outside_the_methods_stops_without_repair_or_quarantine` (настоящий Maven) | `fb490aa` |
| 5 | правка заголовка без ID → `relinked` | собственный заголовок раздела без ID — часть контракта: его правка — `changed`; переименование родительского раздела — по-прежнему `relinked`; тест `rename_student` переписан | `test_suite_impact.py::test_requirement_changes_are_found_by_key`, `…heading_edit_updates_its_cases…` | `767537f` |
| 6 | `--suite` после `FAIL` пишет кейсы `ACTIVE`; файл берётся с диска | кейс упавшего метода — `QUARANTINED` (причина `ASSERTION_FAILED`/`TEST_ERROR`, ссылка = текст `@Disabled`, вопрос аналитику), `quarantine.md` с черновиками баг-репортов; манифест строится из байтов квитанций диспозиций (для `QUARANTINED` — сверка с дайджестом плана), правка человека остаётся его правкой | сквозные `test_the_suite_after_a_quarantined_fail_keeps_the_quarantine…`, `test_a_person_edit_before_the_suite_is_written_stays_a_person_edit`, схема сводки | `9f5ac8d`, `162ad65` |
| 7 | 429 тратит попытку, пауза 60 с; лимиты подписки не узнаются | лимит частоты или подписки — пауза без траты попытки: 60 с, вдвое дольше, до 30 мин, не больше 8 пауз; узнаются «You've hit your limit», «usage limit reached», недельный и 5-часовой лимиты, «out of usage credits»; части ревью и `run --runner process` | фальшивый CLI `sublimit`/`sublimit-text`: `test_model_runner.py` (4 лимита подряд при 3 попытках), `test_driver_run.py`, распознавание сообщений | `448f803` |
| 8 | `DRIVER_PROCESS` из файла, который может написать любой | выбор: привязка к запуску — одноразовый токен только в окружении оболочки (в каталоге — его SHA-256) и SHA-256 stdout; несвязанный результат — `RUNNER_RESULT_UNBOUND`. A1.6 честно сужен: против пользователя машины уровень не сильнее `HOST_DECLARED` (обоснование — журнал решений плана) | `test_forged_part_results_never_reach_driver_process` (сценарий ревью с `--require-driver-isolation`), `test_a_result_the_launched_process_did_not_write_is_refused` | `a5839dc` |
| 9 | `measure` ловит только `MutationStop` | любая ошибка этапа — `NOT_RUNNABLE`/`MUTATION_STAGE_ERROR` в квитанции (и сбой сохранения отчёта), финализация доходит до терминала | `PermissionError` при `rmtree`, `KeyError` разбора; сквозной на настоящем PIT `test_a_stage_error_still_reaches_a_terminal_result` | `e6b7337` |
| 10 | проверка ремонта видит только строковые литералы | сравнение утверждений целиком: цепочка после метки `ASSERT-…` (матчер, аргументы, следующие матчеры; Python — ожидаемая сторона сравнения); с `--mutation` ремонт, понизивший долю убитых мутантов кейса, не принимается (карантин `REPAIR_FAILED`, запись в описании PR) | `test_suite_merge.py` (`hasSize(4)→(3)`, `isEqualTo→isNotNull`, `hasSizeGreaterThan`), сквозной на PIT `test_a_repair_that_lowers_the_kill_ratio_is_not_accepted` | `81669cf` |
| 11 | JUnit `<error>` всегда `REPAIR` | `error_origin`: кадр класса продукта в стеке (с `Caused by`) или `Request processing failed` MockMvc → падение поведения (вопрос, черновик баг-репорта); `REPAIR` — только ошибки кода теста | `test_suite_failures.py` (стек продукта и стек теста), сквозной `test_a_product_exception_is_quarantined_with_a_question_not_repaired` | `c08da5a` |
| 12 | три отказа пост-терминальной задачи → exit 2, нет возобновления | задача разбора без принятого ответа закрывается `submit --failed` (группы остаются pending, терминал стоит); `run --run <id>` продолжает прерванный прогон и собирает уже запущенные процессы | `test_driver_run.py` (`triage-prose` на настоящем PIT: done, exit 0; прерывание и продолжение) | `ab858c1` |
| 13 | jar launcher по префиксу | SHA-256 jar семейства уже писался в квитанцию; теперь `junit-platform-*` обязан быть версии Platform проекта; отчёт волны 2 и A2 больше не говорят «каждый jar с пином» | `test_launcher_family_jars_are_recorded_and_bound_to_the_project_platform` | `a2a373e` |
| 14 | «возвращать»/«не возвращать», «40»/«20» склеиваются | разные отрицания или числа — сходство 0 | `test_analyst_report.py` (3 пары) | `e4bc7e1` |
| 15 | `identify()` в путях волны 3 с лимитами по умолчанию | `identify_project`: лимиты `.skillsrc`; `RequirementConflict` → `SuiteError` с кодом причины | `test_the_suite_paths_use_the_document_limits_of_the_skillsrc` (документ 300 КиБ) | `b0282bd` |
| 16 | новый метод на названном пути не вопрос; параметры не сравниваются | сравнение по методу и пути (`GET /x/{id}` в требовании не покрывает `DELETE /x/{id}`), новые query/body/form-параметры — вопрос аналитику, пути из констант разрешаются, неразрешимые — сегмент `~выражение~` | `test_suite_impact.py` (константы, метод на названном пути, новый параметр, переименование переменной пути — не шум) | `ddfb88e` |
| 17 | `from pytest import mark` → `NameError`; импорт до `from __future__` | `import pytest` добавляется, если имя не привязано, после комментариев, docstring и всех `from __future__` | `test_quarantine.py` (4 варианта, компиляция и импорт модуля), сквозной карантин pytest на синтетическом проекте драйвера (`xfailed`, с `--runxfail` — `failed`) | `feed45a` |

## 2. Контракт, документация, тесты (ревью, 2.2)

- **Карантин по умолчанию.** Строка поправки волны 3 в `contracts/pipeline.json`: `opt_in: false`, `default_on`
  (A4, `local-pilot-v1`, `--disposition-policy quarantine`, отказ `cleanup`, с 2026-10-08); `contract_check`
  требует согласия `default_on` с умолчаниями политики и запрещает умолчание замороженного профиля без
  объявления в поправке. Тексты приведены: документ поправок (введение, A4, «что остаётся в силе»),
  `CONTRACTS.md`/`PIPELINE.md` (рендер), `USAGE.md`, `HOW-IT-WORKS.md`, `README.md`, SKILL оркестратора и
  `orchestration-contract.md` — `7df7acd`.
- **Что изменило замороженное** — новый раздел документа поправок «What changed in the frozen registries»:
  четыре версии в `schema_registry`, `acceptance_reason_codes`, расширенные без смены версии схемы, умолчание
  `local-pilot-v1`, команда Gradle. Фраза отчёта волны 2 «замороженные реестры не тронуты» исправлена — `7df7acd`.
- **`contract_check`** сверяет `tools/mutation_tools.json` с контрактом (версии, корни, jar инструмента и
  плагина, SHA-256); `mutation-triage-output.schema.json` — в `optional_schema_registry` — `7df7acd`.
- **CI.** `CLAUDE.md` репозитория правку workflow не запрещает: `actions/setup-java` по SHA (v6.0.1, Temurin 17),
  `TEST_SKILLS_JAVA_HOME` для `ci_gate`, таймаут 180 мин; тест проводки — `a645f94`. Прогон в GitHub Actions после
  push — проверить по вкладке Actions (сессия CI не опрашивает).
- **Слабые тесты** переписаны так, чтобы падать при поломке — `2faf81c`.
- **Мелкие расхождения** — `427b6a8`: тест argv Gradle этапа мутаций появился и **нашёл дефект**: путь вложенного
  модуля Gradle не извлекался из `:a:b:cleanTest` (мутации шли бы из корня) — исправлено; `USAGE.md` знает профиль
  `suite-update-v1`, флаги раннера, `run --run`, `migrate`; `.skillsrc.example` — 5.2.0 с тестом валидности.
  `live-step5/analyst-report.json.gz` (16 пунктов) — первая сборка живого прогона волны 2, записанная до склейки
  похожих вопросов; «13 пунктов» отчёта волны 2 — пересборка кодом после `21cd552`. Архив не пересобирался:
  исходных ответов ролей в нём нет, а после п. 14 (отрицания и числа) число пунктов при пересборке может снова
  измениться; это пояснение — ответ на расхождение.

## 3. Непроверенные замечания (ревью, 2.3)

| Замечание | Итог | Тест | Коммит |
| --- | --- | --- | --- |
| `manifest["files"][0]` | подтвердилось: обновление работало с одним файлом — теперь несколько (метод остаётся в своём файле, новый — в названный ответом или первый) | набор из двух классов | `1d0dfbf` |
| CRLF → LF | подтвердилось (`read_text` перед записью) — файлы читаются байтами | обновление и карантин CRLF-файла | `7f9dc17` |
| `migrate` без run-scoped authorization | подтвердилось: CLI без `--write` только показывает; запись — согласие человека или шаг `suite-update-v1`, квитанция в `.pilot-runs/suite-migrations/`; A5.1 уточнён | предпросмотр и запись | `4dc1bed` |
| `write_suite` через `exists()` | подтвердилось: новый файл — `O_EXCL`, заменяемый — повторная сверка | гонка создания | `127be37` |
| `#` в блоке кода | подтвердилось: цепочка заголовков больше не сбрасывается; разбиение SREQ заморожено и не менялось (такой «раздел» по-прежнему отдельный SREQ — отложено) | `test_a_hash_line_inside_a_code_block…` | `8e9cf30` |
| параметризованные тесты как `FLAKY` | подтвердилось: вызовы в одном прогоне — один исход (худший) | `m(String)[1..3]` | `4a0d279` |
| `MEASURED` при нуле сопоставленных | подтвердилось: `MUTATION_NO_MUTANTS` / `MUTATION_NOT_ATTRIBUTED` | нет мутантов, не сопоставлены | `0f0c2f4` |
| перенос файла спецификации → `to_retire` | подтвердилось: переименование ключей между путями | перенос документа | `82fb11f` |
| смена `id_pattern` → массовый `to_retire` | подтвердилось: `SUITE_ID_PATTERN_CHANGED`, остановка | сквозной | `82fb11f` |
| `project_snapshot` не видит каталоги и ссылки | подтвердилось: снимок их учитывает | пустой каталог | `82fb11f` |
| якорь «своя строка» ±4, ответы разбора только в `.driver/`, обрыв BLOCKED, `--review-runner-command` в `config.json`, `~2`, мутаторы фикстуры Petclinic, `codex --sandbox read-only` | отложено, причины — журнал решений плана | — | — |

## 4. Найдено при подготовке живых прогонов

- **Gradle и проверка покрытия.** У RealWorld `test` финализируется `jacocoTestCoverageVerification` с правилом
  «100 % методов каждого класса»: запуск выбранных тестов (`--tests`) её всегда роняет (exit 1 при зелёном
  отчёте), и пайплайн получил бы `UNKNOWN`. Исправлено: команда `gradle-wrapper:selected-symbols-v1`
  подключает init-скрипт пакета `tools/gradle/selected-symbols.init.gradle`, отключающий задачи
  `JacocoCoverageVerification` для этой сборки; квитанции с прежней командой проверяются; файлы `.gradle` — в
  release manifest; запись — п. 5 раздела «What changed» документа поправок. Проверено руками на RealWorld
  (`jacocoTestCoverageVerification SKIPPED`, сборка зелёная) и тестом — `963febc`.
- Обнаружение `.skillsrc` у Gradle-проекта не выводит JUnit 5 (спрашивает фреймворк) — `.skillsrc` RealWorld
  написан по образцу `.skillsrc.example`; отличие от Maven записано для отчёта живых прогонов.

## 5. Гейт

Весь набор тестов и `contract_check --full` зелёные; финальные прогоны на Windows — `D:\AI-Projects\windows-full-20261008-review-fixes.log`
и `D:\AI-Projects\windows-ci-gate-20261008-review-fixes.log` (итоги — раздел 6 после прогона).

## 6. Итог гейта

Код гейта — `963febc`. Оба финальных прогона на Windows 11 (Python 3.12, JDK 17 в `TEST_SKILLS_JAVA_HOME`,
Maven в `PATH`): **1070 passed, 5 skipped, код 0** — полный набор за 59 мин 44 с
(`windows-full-20261008-review-fixes.log`), `ci_gate` (`contract_check --full`, проверка рендера, весь набор) —
1 ч 0 мин 13 с (`windows-ci-gate-20261008-review-fixes.log`). Было на `c37e4d0`: 995 passed, 5 skipped —
прирост 75 тестов волны исправлений. Гейт пройден: дальше — живые прогоны (отчёт `2026-10-08-live-runs-report.md`).
