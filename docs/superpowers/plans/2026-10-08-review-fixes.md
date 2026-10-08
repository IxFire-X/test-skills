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
- [ ] R9 `measure`: любая ошибка этапа — `NOT_RUNNABLE` с причиной, финализация доходит до терминала.
- [ ] R10 Проверка ремонта сравнивает утверждения целиком; падение доли мутантов по кейсу блокирует ремонт.
- [ ] R11 JUnit `<error>` из кода продукта — падение поведения (вопрос, черновик баг-репорта).
- [ ] R12 `run --runner process`: пост-терминальные задачи не отменяют терминал; `run --run <id>` продолжает.
- [ ] R13 Jar launcher: SHA-256 в квитанции; тексты без «каждый jar с пином».
- [ ] R14 Сходство вопросов: отрицания и числа различают вопросы.
- [ ] R15 `identify()` в путях волны 3 — лимиты из `.skillsrc`; `RequirementConflict` — статус с причиной.
- [ ] R16 Поверхность кода: метод и путь, новые параметры — вопрос, пути из констант не схлопываются молча.
- [ ] R17 Карантин pytest: `from pytest import mark`, `from __future__`; сквозной тест на синтетическом проекте.

#### 1.2. Контракт, документация, тесты (ревью, раздел 2.2)

- [ ] C1 Тексты — карантин по умолчанию в `local-pilot-v1`; `contracts/pipeline.json`: умолчание у поправки A4, сверка в `contract_check`.
- [ ] C2 Документ поправок: что изменило замороженное (четыре версии, `acceptance_reason_codes`, шесть схем); отчёт волны 2.
- [ ] C3 `contract_check`: `tools/mutation_tools.json` против контракта; `mutation-triage-output` в `optional_schema_registry`.
- [ ] C4 CI: JDK и `TEST_SKILLS_JAVA_HOME` в GitHub Actions (см. «Решения пользователя»).
- [ ] C5 Слабые тесты: `test_model_runner.py:58-60`, `test_driver_run.py:53-60`, `test_mutation_triage.py:92`, «одна сессия на несколько частей».
- [ ] C6 Мелкие расхождения: тест argv Gradle, 16/13 пунктов `live-step5/analyst-report.json.gz`, `USAGE.md:147`, `.skillsrc.example`.

#### 1.3. Непроверенные замечания (ревью, раздел 2.3)

- [ ] N1 `manifest["files"][0]` — набор из двух файлов тестов.
- [ ] N2 CRLF переписывается в LF.
- [ ] N3 `migrate` без run-scoped authorization (A5.1).
- [ ] N4 `write_suite`: `exists()` вместо `O_EXCL`.
- [ ] N5 `#` внутри блока кода в цепочке заголовков.
- [ ] N6 Параметризованные тесты как `FLAKY`.
- [ ] N7 `MEASURED` при нуле сопоставленных мутантов.
- [ ] N8 Остальные замечания 2.3 — проверка и запись (подтвердилось / отложено / не подтвердилось).

#### 1.4. Гейт исправлений

- [ ] G1 Весь набор тестов и `contract_check --full` зелёные.
- [ ] G2 Финальные прогоны на Windows: `windows-full-20261008-review-fixes.log`, `windows-ci-gate-20261008-review-fixes.log`.
- [ ] G3 Отчёт `docs/review/2026-10-08-review-fixes-report.md`.

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

(пусто)
