# План: исправления по независимому ревью и живые прогоны внутри Claude Code (2026-10-08)

Задание: `docs/review/2026-10-08-live-runs-prompt.md`. Вход — независимое ревью волн 2–3
(`docs/review/2026-10-08-waves-2-3-independent-review.md`, не коммитится: в нём личные адреса почты; ссылки — по
номерам пунктов). Опора: план волн 2–3 `2026-10-07-waves-2-3.md`, отчёты волн 2 и 3, инструкция живого прогона
`docs/review/2026-10-07-petclinic-live-run.md`.

Статусы: `[ ]` открыт · `[x]` сделано · `[~]` отложено · `[!]` не прошло (с причиной). После статуса — одна строка:
что сделано, тест, файл. Если сессия прервётся, работа продолжается с первого пункта без `[x]`.

## Исходное состояние

- HEAD `c37e4d0` (`main` = `origin/main`); неотслеживаемые — только задание и ревью в `docs/review/`.
- Окружение: Windows 11, Claude Code (desktop), Opus 5.5; venv `D:\AI-Projects\.runtime\test-skills-venv`, JDK 17
  `D:\AI-Projects\.tools\jdk-17`, Maven `D:\AI-Projects\.tools\maven`; git с `-c safe.directory=D:/AI-Projects/test-skills`.
- Набор тестов на `c37e4d0`: 995 passed, 5 skipped (51 мин 37 с, `windows-full-20261008-wave-3.log`).

## Шаги

### 1. Волна исправлений

#### 1.1. Подтверждённые дефекты (ревью, раздел 2.1)

- [x] R1 `TEST_GAP`: одно место — `pipeline_driver_strength.proposals_path` (пишет разбор, читает `suite_update`); сквозной тест `test_suite_update.py::test_a_survivor_triage_proposal_reaches_the_update_brief` — `eea109d`.
- [x] R2 `quarantine_modes` без упавших методов — None (очистка §17 п. 2); `test_quarantine_disposition.py::test_a_fail_without_failed_methods_falls_back_to_cleanup` — `191088f`.
- [x] R3 `suite_run.not_run_reason`: `SUITE_NOT_RUN`/`SUITE_RUN_NONZERO_EXIT` — остановка, exit 1, раздел «Остановка» в описании PR, `NOT_RUN` не PASS; сквозной тест без JDK `test_a_suite_run_that_ran_nothing_stops_instead_of_passing` — `57223bc`.
- [x] R4 ошибка вне методов — `SUITE_BUILD_BROKEN`, без ремонта и карантина; внутри метода — только этот метод; строки javac/Gradle; `test_suite_failures.py`, сквозной `test_a_build_broken_outside_the_methods_stops_without_repair_or_quarantine` — `fb490aa`.
- [x] R5 `suite_impact.own_heading_changed`: правка своего заголовка раздела без ID — `changed`, переименование родителя — `relinked`; `test_suite_impact.py` (rename_student переписан) — `767537f`.
- [x] R6 `--suite` после FAIL: кейсы `QUARANTINED` (причина, ссылка = текст `@Disabled`, вопрос), `quarantine.md` с черновиками баг-репортов; манифест — по байтам из квитанций диспозиций; `test_quarantine_disposition.py` — `9f5ac8d`, `162ad65`.
- [x] R7 лимит частоты/подписки — пауза без траты попытки (60 с ×2 до 30 мин, ≤ 8 пауз), регулярка подписки Claude Code; фальшивый CLI `sublimit`; `test_model_runner.py`, `test_driver_run.py` — `448f803`.
- [x] R8 выбор: привязка `result.json` к запуску (одноразовый токен только в окружении оболочки, в каталоге — дайджест; плюс дайджест stdout) и честное сужение A1.6; тест подделки `test_forged_part_results_never_reach_driver_process` — `a5839dc`.
- [x] R9 `measure` ловит любую ошибку этапа → `NOT_RUNNABLE`/`MUTATION_STAGE_ERROR` (и сбой сохранения отчёта); `test_mutation.py` (PermissionError, KeyError), сквозной `test_mutation_java.py::test_a_stage_error_still_reaches_a_terminal_result` — `e6b7337`.
- [x] R10 `suite_merge.expectation_parts`: цепочка после метки `ASSERT-…` (Python — правая часть сравнения) не меняется; с `--mutation` ремонт с упавшей долей — карантин `REPAIR_FAILED`; `test_suite_merge.py`, сквозной `test_a_repair_that_lowers_the_kill_ratio_is_not_accepted` — `81669cf`.
- [x] R11 `suite_run.error_origin`: кадр продукта в стеке или `Request processing failed` → падение поведения (вопрос, баг-репорт); `test_suite_failures.py`, сквозной `test_a_product_exception_is_quarantined_with_a_question_not_repaired` — `c08da5a`.
- [x] R12 пост-терминальная задача без принятого ответа закрывается `submit --failed` (группы остаются pending, терминал не меняется); `run --run <id>`; `test_driver_run.py` — `ab858c1`.
- [x] R13 SHA-256 jar семейства launcher уже в квитанции; `junit-platform-*` — только версии Platform проекта; отчёт волны 2 и A2 исправлены; `test_mutation.py` — `a2a373e`.
- [x] R14 разные отрицания или числа — сходство 0; `test_analyst_report.py` — `e4bc7e1`.
- [x] R15 `requirement_identity.identify_project` (лимиты `.skillsrc`, `RequirementConflict` → `SuiteError`) во всех путях волны 3; `test_suite_impact.py` — `b0282bd`.
- [x] R16 сравнение по методу и пути, новые параметры — вопрос (`changed_parameters`), пути из констант разрешаются, неразрешённые — `~выражение~`; `test_suite_impact.py` — `ddfb88e`.
- [x] R17 `import pytest` при `from pytest import …`/алиасе, после комментариев, docstring и `from __future__`; сквозной карантин pytest на синтетическом проекте — `feed45a`.

#### 1.2. Контракт, документация, тесты (ревью, раздел 2.2)

- [x] C1 строка поправки волны 3: `opt_in: false`, `default_on` (A4, `local-pilot-v1`, `quarantine`, отказ `cleanup`); `contract_check` сверяет с умолчаниями политики; тексты (документ поправок, USAGE, HOW-IT-WORKS, README, SKILL и справочник оркестратора); `test_wave3_contract.py` — `7df7acd`.
- [x] C2 раздел «What changed in the frozen registries» документа поправок (четыре версии, `acceptance_reason_codes`, расширенные схемы); отчёт волны 2 исправлен — `7df7acd`.
- [x] C3 `contract_check._validate_mutation_pins` (версии, корни, jar инструмента и плагина, SHA-256); `mutation-triage-output` в `optional_schema_registry` — `7df7acd`.
- [x] C4 `CLAUDE.md` правку workflow не запрещает: `actions/setup-java` по SHA (v6.0.1, Temurin 17), `TEST_SKILLS_JAVA_HOME` для `ci_gate`, таймаут 180 мин; `test_ci_gate.py` — `a645f94`.
- [x] C5 таймер — вокруг вызова, запускающего части (фальшивый CLI `slow`); две части с одной сессией реально строятся; вопрос без ответа обязателен; пропущенная и переставленная группа разбора отклоняются — `2faf81c`.
- [x] C6 тест argv Gradle (и найден дефект: путь вложенного модуля Gradle не извлекался — исправлено); `USAGE.md` (профиль `suite-update-v1`, флаги раннера, `run --run`, `migrate`); `.skillsrc.example` 5.2.0 с тестом; 16/13 пунктов `live-step5/analyst-report.json.gz` — пояснено в отчёте (архив — первая сборка живого прогона) — `427b6a8`.

#### 1.3. Непроверенные замечания (ревью, раздел 2.3)

- [x] N1 подтвердилось: `suite-update-v1` работал с одним файлом. Теперь несколько файлов (метод остаётся в своём, новый — в названный ответом или первый); тест с набором из двух классов — `1d0dfbf`.
- [x] N2 подтвердилось (`read_text` перед записью): файлы читаются байтами, CRLF сохраняется при обновлении и карантине; тест — `7f9dc17`.
- [x] N3 подтвердилось: `suite migrate` без `--write` только показывает; запись — согласие человека или шаг `suite-update-v1`, квитанция в `.pilot-runs/suite-migrations/`; A5.1 уточнён — `4dc1bed`.
- [x] N4 подтвердилось: новый файл набора — `open(..., "xb")`, заменяемый — повторная сверка дайджеста; тест гонки — `127be37`.
- [x] N5 подтвердилось: строка `#` в блоке кода не сбрасывает цепочку заголовков (разбиение SREQ заморожено и не меняется — отложено: такой «раздел» по-прежнему отдельный SREQ) — `8e9cf30`.
- [x] N6 подтвердилось: вызовы параметризованного метода в одном прогоне — один исход (худший); прогон считает только запрошенные методы — `4a0d279`.
- [x] N7 подтвердилось: нет мутантов — `NOT_APPLICABLE`/`MUTATION_NO_MUTANTS`, ни один не сопоставлен — `NOT_RUNNABLE`/`MUTATION_NOT_ATTRIBUTED` — `0f0c2f4`.
- [x] N8 исправлено: перенос/переименование документа — переименование ключей; смена `id_pattern` — `SUITE_ID_PATTERN_CHANGED`; снимок проекта видит каталоги и ссылки (`82fb11f`). Отложено с причиной — см. журнал.

#### 1.4. Гейт исправлений

- [x] G1 Весь набор тестов (1070 passed, 5 skipped) и `contract_check --full` зелёные на `963febc`.
- [x] G2 Финальные прогоны на Windows (оба — 1070 passed, 5 skipped, код 0): `windows-full-20261008-review-fixes.log` (59:44), `windows-ci-gate-20261008-review-fixes.log` (1:00:13).
- [x] G3 Отчёт `docs/review/2026-10-08-review-fixes-report.md`.

### 2. Живые прогоны внутри Claude Code

- [ ] L1 Petclinic с нуля (`local-pilot-v1 --suite --mutation --analyst-report`), критерии раздела 5 инструкции.
- [ ] L2 Petclinic `suite-update-v1`: O05 изменён в тексте и коде.
- [ ] L3 RealWorld: выбор реализации, подготовка, спецификация, эталон `specs/api/hurl`.
- [ ] L4 RealWorld A1 — полный `local-pilot-v1`; `coverage-oracle.json`.
- [ ] L5 RealWorld A2 — шесть сценариев `suite-update-v1`.

### 3. Итог

- [ ] F1 Отчёт `docs/review/2026-10-08-live-runs-report.md`, коммит и push.

## Находки живых прогонов

(пусто)

## Решения пользователя

Вопросы, которые сессия не решает сама (раздел 4 задания); работа, которую они не блокируют, продолжается.

- Оставить карантин политикой `local-pilot-v1` по умолчанию или вернуть очистку — после живых прогонов, по данным отчёта.
- Переписывать ли историю git ради личных адресов почты и трейлеров `Co-Authored-By` (ревью, раздел 1): force-push — только явным решением и силами пользователя.
- Менять ли CI workflow (JDK в GitHub Actions): `CLAUDE.md` репозитория этого не запрещает — см. C4.
- Снятие `implemented_unverified`; порог доли убитых мутантов для `accepted`; коммиты и PR силами пакета.

## Журнал решений

- 2026-10-08, R8: выбрана привязка `result.json` к запуску (одноразовый токен только в окружении оболочки, в каталоге — его SHA-256; плюс SHA-256 stdout) и честное сужение A1.6. Сильнее сделать нельзя: оркестратор работает от того же пользователя ОС, что и драйвер, и может переписать любой файл драйвера; ключ «вне каталога задач» лежал бы там же. Привязка ловит ошибку или небрежность хоста (запись в каталог раннера), но не недобросовестного хоста — это записано в A1.6 и `HOW-IT-WORKS.md`.
- 2026-10-08, R6: кейс, чей метод отключён карантином в `local-pilot-v1`, получает причину `ASSERTION_FAILED` (FAILED) или новую `TEST_ERROR` (ERROR — исключение): перечисление причин схемы `suite-manifest` расширено без смены версии (C2). Вопрос и черновик баг-репорта для первого прогона сформулированы иначе, чем для обновления: тест построен по требованию, «ничего не менялось» неверно. Текст падения берётся из `runner-output.txt` попытки: долговечные копии JUnit-отчётов хранят только исходы, без сообщений.
- 2026-10-08, R2: у прогонов, сделанных кодом `656b663`…`191088f` с `FAIL` без исходов по методам, квитанции диспозиций (RETAINED) больше не сходятся с перевыводом (ожидается очистка). Такие прогоны — только локальные, живых среди них нет.
- 2026-10-08, R7: при лимите каталог процесса откладывается (`<task>.rate-limited-<n>`), задача перезапускается после паузы с той же попыткой; открытая, но не запущенная часть теперь держит `wait` (иначе драйвер уходил в финализацию с непроверенной частью).
- 2026-10-08, R10: блокировка ремонта по доле мутантов не откатывает метод (у ремонта «не компилируется» откат сломал бы сборку), а отправляет его в карантин `REPAIR_FAILED` с записью в описании PR; в счётчик отремонтированных он не входит.
- 2026-10-08, N8, отложено с причиной: (1) якорь «своя строка» разбора выживших принимает соседей ±4 строки — это часть блока группы (решающее условие над мутированным `return`), строгое правило отклоняло бы верные ответы; (2) ответы разбора видны только в `.driver/`, `status` незавершённый разбор не показывает — видимость, не корректность; (3) обрыв между артефактом отказа части и событием BLOCKED в журнале ревьюера не воспроизведён без инъекции сбоя в `pilot_state`; живые прогоны этой сессии идут без раннера процессов; (4) `--review-runner-command` хранится в `.driver/config.json` открытым текстом — нужен для возобновления; секреты в шаблон класть нельзя (написано в USAGE); (5) удаление первого из двух одинаковых разделов (`~2`) неразличимо по тексту — ограничение ключей; (6) фикстура Petclinic записана с неявным набором мутаторов PIT, продакшн передаёт `DEFAULTS` — уже в журнале волн 2–3, не дефект кода; (7) пресет `codex` с `--sandbox read-only` оставляет чтение и shell — A1.3 обещает «без инструментов записи»; уточнение в документе поправок не делалось, пресет `codex` живьём не проверялся.
- 2026-10-08, C6: проверка `test_the_gradle_path_builds_its_argv…` нашла дефект: `BuildTool.from_execution` не извлекал путь вложенного модуля Gradle из `:a:b:cleanTest` (мутации шли бы из корня проекта). Исправлено тем же коммитом.

