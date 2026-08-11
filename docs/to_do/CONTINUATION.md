# Продолжение portable testing skills

Current checkpoint is `PILOT_READY / FORMAL_ACCEPTANCE_INCOMPLETE`. Plan 3 Java/Python/NOT_RUNNABLE baseline, semantic artifact review, six forward reports and final completion audit are committed through `fa7bcdd`. Не запускать новые дорогие project/evaluator batches автоматически: следующая практическая работа — пилотный перенос по запросу пользователя; формальное завершение требует отдельного blind/fresh review маршрута, перечисленного ниже.

## Актуальный real-chain checkpoint (2026-08-11)

### Click — complete PASS

- Project: `D:\AI-Projects\real-chain-projects\click`, source HEAD `9c4dfdaebe0e6b2aabc566eb81f6f10eb5cd6ea1`; generated untracked file: `tests/test_int_range_real_chain.py`; external test venv: `D:\AI-Projects\.tools\click-real-chain-venv`.
- Evidence: `docs/to_do/real-chains/click/chain-01/attempt-01/`.
- All five artifacts are schema-valid. The chain preserved 8 IntRange requirements/cases across interior input, closed bounds, rejection, closed clamping and open-bound inward clamping into exactly 8 CliRunner tests.
- Targeted execution: 8/8. Complete project execution: 1890 passed, 95 skipped, 31000 deselected, 1 xfailed, 0 failed; baseline passed count was 1882 and all non-pass counts were unchanged.
- Strict project boundary held: Click implementation, existing tests, configuration, `pyproject.toml`, `uv.lock` and repository dependencies remain unchanged; only the isolated generated test file is untracked.

### Flask — PASS with bounded reviewer correction

- Project: `D:\AI-Projects\real-chain-projects\flask`, source HEAD `8b4fd5d18611e63080b826ecfefe8659dba7d2e4`; generated untracked file: `tests/test_json_real_chain.py`; test venv is outside the project at `D:\AI-Projects\.tools\flask-real-chain-venv`.
- Evidence: `docs/to_do/real-chains/flask/chain-01/attempt-01/`.
- Context and generator produced 8 source-backed JSON request/response contracts/cases. Parent pre-run semantic review caught one tc-reviewer false-pass: direct return of a parsed scalar could not produce the stated JSON response. Only the harness precondition was corrected to use `jsonify`; exact original SHA and before/after are recorded in `03-parent-semantic-correction.json`.
- All five final artifacts are schema-valid. Generated execution: 8/8. Complete project execution: 499/499; baseline was 491/491, so the delta is exactly the 8 generated tests.
- Strict project boundary held: Flask implementation, existing tests, configuration, `pyproject.toml`, `uv.lock` and repository dependencies remain unchanged. Follow-up after the real-project batch: make tc-reviewer reject expected response oracles that the stated harness cannot produce.

### Hono — complete PASS

- Project: `D:\AI-Projects\real-chain-projects\hono`, source HEAD `26de73133b8552f56ba72e025ecd82b08900d796`; generated untracked file: `src/middleware/bearer-auth/real-chain.test.ts`.
- Evidence: `docs/to_do/real-chains/hono/chain-01/attempt-01/`.
- All five pipeline artifacts are schema-valid. The chain preserved exact traceability from 8 source-backed bearer-auth requirements to 8 reviewed cases and 8 generated Vitest methods.
- Targeted project-script execution: 8 passed, 0 failed/skipped. Complete project execution: 4806 test items, 4771 passed, 35 skipped, 0 failed; baseline before generation was 4798, so the delta is exactly the 8 generated tests.
- Strict project boundary held: no application source, existing test, configuration, dependency or behavior was modified; only the isolated generated test file exists as untracked project state.
- The official runner reports an existing `vitest 4.1.9` / `@vitest/coverage-v8 4.1.7` mixed-version warning in targeted and full runs. It does not fail execution and was preserved rather than changing project dependencies.

### Spring PetClinic REST — complete PASS

- Project: `D:\AI-Projects\real-chain-projects\spring-petclinic-rest`, source HEAD `698bd832eac0da48e72deb63ce7ab275d98a88fb`; generated untracked file: `src/test/java/org/springframework/samples/petclinic/rest/controller/OwnerRestControllerRealChainTest.java`.
- Evidence: `docs/to_do/real-chains/spring-petclinic-rest/chain-01/`.
- `attempt-01` stopped before Java generation because the tc-generator output schema rejected the unsupported category `validation`; project state remained unchanged.
- `attempt-02` passed context-marker, tc-generator, tc-reviewer, tc-to-autotest and autotest-reviewer schemas. A semantic pre-run review corrected only the generated test so `TC-0008` truly omits `firstName` instead of serializing it as `null`.
- Generated execution: 8 tests, 8 passed, 0 failures/errors/skipped. Complete project execution: 245 test items, 245 passed; baseline before generation was 237.
- Strict project boundary held: no application source, configuration, dependency or behavior was modified; only the isolated generated test file exists as untracked project state.

### InvenTree — skill/feature PASS, full-suite environment-limited

- Project: clean clone `D:\AI-Projects\real-chain-projects\inventree-clean`, source HEAD `ad8cb604e998a49e517ee4e4a1e3f4df8ebc5d8a`; generated untracked file: `src/backend/InvenTree/part/test_real_chain.py`.
- Evidence: `docs/to_do/real-chains/inventree/chain-01/`.
- `attempt-01` stopped honestly because context warnings violated the schema-required `topic — explanation` form; no downstream stage ran.
- `attempt-02` passed all five skill schemas/reviews but runtime returned HTTP 403 for all 6 generated tests. Root cause: `tc-to-autotest` copied base class and fixtures but omitted required endpoint-specific permission setup; `autotest-reviewer` false-passed it.
- Bounded portable fix: generator now preserves source-confirmed roles/permissions/fixtures/setup hooks/client initialization; reviewer has a project-native runtime setup gate and cannot accept a base class name alone. Focused regression: 5 passed; both skills pass `skill-creator` quick validation.
- `attempt-03`: only source-confirmed `roles = ['part_category.view']` was added. Generated class passed 6/6; existing `PartCategoryAPITest` plus generated class passed 17/17.
- Full official app-list run was actually executed: 1471 total, 1433 passed, 11 skipped, 27 failed, 0 errors. Remaining failures are Windows/CI setup related (Unix path assumptions, missing built static/media assets, Windows Python app alias, preserved DB state), not generated feature failures. Do not claim full-project green.
- Ruff is absent from the existing InvenTree venv; no dependency install was attempted.

### step5-java-demo — complete PASS

- Project: `D:\AI-Projects\step5-java-demo`, source HEAD `ed41efd74c7138dceab2708a4f23c9d025e36e12`; generated untracked file: `src/test/java/net/javaguides/springboot/controller/StudentControllerRealChainTest.java`.
- Evidence: `docs/to_do/real-chains/step5-java-demo/chain-01/`.
- `attempt-01`: context-marker 7 requirements, tc-generator 7 cases, tc-reviewer accepted; tc-to-autotest then truthfully stopped because the old Java template required `BaseApiTest` despite two working `@WebMvcTest + MockMvc` examples.
- Bounded fix: confirmed existing-project test architecture now overrides template defaults; `BaseApiTest` is reused only when it is the actual project pattern. Focused regression: 2 passed; quick_validate and scoped diff-check passed.
- `attempt-02`: generated one project-native JUnit/MockMvc class with 7 TC-linked methods; tc-to-autotest and autotest-reviewer artifacts are schema-valid, reviewer verdict `ПРИНЯТО`.
- Offline execution: targeted generated class `7 tests / 0 failures / 0 errors / 0 skipped`; full project `31 / 0 / 0 / 0` on Maven 3.9.16 + JDK 24.0.1.

### PocketBase — skill PASS, environment-blocked execution

- Project: `D:\AI-Projects\real-chain-projects\pocketbase-v0.38.2`, immutable source commit `3616b9d66769dcda95841c52b00c166246323333` plus one generated uncommitted test file `apis/real_chain04_record_rules_test.go`.
- Evidence: `docs/to_do/real-chains/pocketbase-v0.38.2/chain-04/`.
- `attempt-01` correctly stopped at tc-reviewer because context-marker had dropped a supported HTTP 200/response oracle.
- `attempt-02` passed context-marker, tc-generator, tc-reviewer and tc-to-autotest, then exposed an autotest-reviewer false negative: Java `@DisplayName` was demanded from valid Go `t.Run` anchors.
- `attempt-03` SHA-binds the unchanged successful attempt-02 stages, uses portable `autotest-reviewer`, and records schema-valid `ПРИНЯТО`. Execution result is `BLOCKED/GO_TOOLCHAIN_UNAVAILABLE`; no install, network, gofmt, compilation or Go test was attempted.
- Real-chain-driven skill fixes currently dirty: context-marker preserves allowlisted observables, tc-to-autotest forbids fixture-secret/token persistence and requires runtime auth, autotest-reviewer applies language-native traceability/stack rules.
- Next exact action when Go 1.25 is available: `gofmt -w apis/real_chain04_record_rules_test.go`, recompute artifact digest if bytes change, rebind reviewer evidence if needed, then `go test ./apis -run ^TestRealChain04RecordRules$ -count=1` from the PocketBase root.
- Retry policy: preserve failed attempts; fixes continue as a new `attempt-N` inside the same chain, not a new chain directory.

## Цель и источник истины

Довести пакет до **host-neutral drop-in advanced testing skills**, включая SDD; это не Codex-only решение. Канонический источник — исходный `test-orchestration-skills`. `Grok`/`Grokv2` — только материал для сравнения, не источник требований. Этот worktree/branch и fixture-проекты имеют лишь более позднюю, проверочную роль.

Планы выполняются строго так: [master](../superpowers/plans/2026-08-06-portable-testing-skills-master.md) → [Plan 1: core/runtime](../superpowers/plans/2026-08-06-portable-core-runtime.md) → [Plan 2: skills/adapters](../superpowers/plans/2026-08-06-portable-skills-adapters.md) → [Plan 3: E2E acceptance](../superpowers/plans/2026-08-06-e2e-acceptance.md).

## Обязательные правила

- Постоянные артефакты — только в `docs/to_do/`; грязное worktree сохранять. Не делать `reset`, очистку или широкий `git add` чужих файлов.
- Обязательный post-generation CSV export реализован: канонический `tc-generator` JSON сохраняется, а рядом всегда создаётся CSV-представление всех тест-кейсов для последующего импорта в Jira Zephyr. Экспорт сохраняет ID, requirement links, title, priority, categories, preconditions, test data, ordered steps и expected outcomes; deterministic проверка доказывает эквивалентность JSON↔CSV по числу, порядку и содержимому без потерь. CSV не является входом последующих стадий и не заменяет JSON.
- Одна `writing-skills`-guided кампания одного skill за раз. Terra реализует; свежий Sol только просматривает/выносит вердикт. Перед каждым native delegation: `agent_type: sol_advisor_terra_implementer`, затем для review — `agent_type: sol_advisor_sol_reviewer`, оба с `fork_turns: none`.
- Обязательная role-preflight команда (из каталога `SKILL.md`): `skill_dir=<directory-containing-this-SKILL.md>; installer="$skill_dir/../../scripts/install-agents.sh"; sh "$installer" --check`. Она должна завершиться 0, а доступные `agent_type` должны буквально содержать оба имени выше; иначе остановить lane, не подменять роль/модель.
- Неудачные прогоны архивировать, а не чинить/перезаписывать. Forward contract: каждый фактически выполненный command записан буквально как `argv`-массив и абсолютный `cwd`, в порядке запуска, в metadata и `run-protocol`; не shell-строкой.

## ОБЯЗАТЕЛЬНО: адаптивный бюджет evaluator-прогонов

- После узкой правки wording/fix никогда автоматически не запускать пять полных evaluator repetitions. Последовательный gate: **1 → 3 → 5**. После каждого edit — deterministic/static/schema tests; затем ровно один fresh targeted diagnostic/smoke evaluator; только при его успехе — ещё две независимые rep (всего 3).
- Пять независимых rep — только финальный acceptance стабильного кандидата либо при явно подтверждённых высокой вариативности/риске с записанной причиной. Эта политика заменяет прежнюю blanket-интерпретацию «5+ на каждый wording variant», но сохраняет финальный deployment gate.
- При первом protocol или semantic failure сразу остановиться: не завершать batch, не заменять, не ремонтировать и не перезапускать; сначала root-cause diagnosis. Causally-valid RED/initial/pressure evidence переиспользовать, не повторять лишь из-за wording.
- Механические invariants (`ID`, форма provenance, paths, capture commands) прежде переводить в deterministic validators, затем тратить новые evaluator runs.
- `context-marker` завершён в commit `686908c`; `tc-generator` завершён в implementation/evidence commit `c9d9aa6` по отдельной принятой change-and-GREEN поправке. Начиная с `tc-reviewer`, RED по умолчанию ограничить одним корректным контрольным запуском и расширять до трёх только при неоднозначном результате или неожиданном прохождении rubric; высвободившийся бюджет переносить на разнообразные GREEN/pressure inputs.
- Для skill work основная методическая опора — `superpowers:writing-skills` и `skill-creator`: во время итераций выполнять узкие deterministic checks, а исчерпывающие проверки оставлять для final acceptance. Это не отменяет baseline-before-skill принцип и adaptive gate 1 → 3 → 5.

## Принятая история

Plan 1/core и Plan 2 scaffold/ASCII migrations приняты исторически. Важные архивные commits: `0334bcd` (failed context final batch), `1b0ac0c` (unstable context final batch), `c41896c` (stopped failed context final v1 batch, archive/r5), `fdd16aa` (остановленный tc-generator RED-v1), `9ddafed` (отдельный RED-v2 recovery) и `b359415` (RED-v2 checkpoint). Принятый context-marker checkpoint: `686908c`; принятый tc-generator implementation/evidence checkpoint: `c9d9aa6`. Локальные инструменты этой машины: worktree `D:\AI-Projects\.worktrees\portable-testing-skills`, Python `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`, quick validator `C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py`, Sol skill `C:\Users\User\.codex\plugins\cache\sol-advisor\sol-advisor\0.5.0\skills\orchestration\SKILL.md`.

## Точный checkpoint

Исторический checkpoint `context-marker` принят и закоммичен как `686908c`. Root cause r5: evaluator получил byte-identical старый canonical prompt, который не доставлял `skills/context-marker/SKILL.md` и `references/context-artifact-contract.md`; audit snapshots сами по себе не доказывали injection/read. Исправление явно связывает FINAL prompt с двумя canonical skill inputs и их SHA-256, задаёт repository-root resolution, усиливает deterministic schema/semantic guards и сохраняет immutable r5 без rerun/repair/replacement.

Его новый FINAL прошёл adaptive gate 1 → 3 → 5. Fresh Sol acceptance: `ship`, все 20 rubric booleans true. Официальные три scorecard и metadata всех 16 runs complete; исторические RED/initial prompts честно phase-bound к hash `227751…`, FINAL — к `2b750…`. Parent checks после завершения: **418 passed, 2 skipped**; contract check, render check, quick_validate и diff-check прошли. Эти факты остаются историческим context-marker evidence, а не доказательством для `tc-generator`.

`tc-generator` принят и закоммичен как `c9d9aa6`. Каноническая поставка — `skills/tc-generator/SKILL.md` и `references/case-generation-contract.md`; она работает от входных требований, provenance, ролей, границ и API-оракулов, а не от конкретного проекта или роли `sales_manager`.

Активный ledger завершён: 3 RED-v2 diagnostic runs, 5 initial-GREEN-v3, 1 pressure-v4 и 5 FINAL-GREEN, всего 14 успешных active runs. Metadata — `complete`; initial/final GREEN scorecards — `complete`, все 30 rubric booleans true. RED scorecard намеренно `pending`/empty/non-comparable по принятой bounded change-and-GREEN поправке. Два RED-v1 historical runs и семь invalidated attempts сохранены отдельно и не входят в scorecards. Не повторять ни один tc-generator evaluator run.

Финальный контроллер v1 честно отказал до записи из-за byte-only newline guard. V2 один раз завершил уже валидный `04-green-final/rep-05`; controller attestation, protocol, scorecard и metadata согласованы. Дополнение `12-stale-lock-recovery-limit.md` сужает обещание recovery после uncatchable termination: автоматическое снятие stale lock не заявляется; ручная очистка разрешена только после точной проверки, и representative test покрывает no-write refusal → exact lock removal → truthful recovered attestation.

Supplemental scale evidence не подменяет основной acceptance. `08-scale-acceptance` дал 36 кейсов из 18 требований и получил `diagnostic-ship` после false negatives exact-string checker. Слепой `09-blind-scale-acceptance` использовал новую subscription-invoicing feature без раскрытия числа/распределения/hidden oracle; skill самостоятельно дал 36 кейсов. Попытка остаётся schema-valid, semantic-failed по двум checker false negatives, immutable и формально исключённой; fresh Sol verdict — `diagnostic-ship`.

Финальная verification после последнего recovery-теста: **596 passed, 2 skipped**; focused v2 — **9 passed**; Ruff, `skill-creator` quick validation, full contract check, render check и пять evidence schema validations прошли. Exact manifest и staged implementation scope совпали: 216 путей, missing/extra 0. Fresh Sol acceptance после двух bounded corrections: `ship`; проверок достаточно.

### Real-chain-driven skill amendment checkpoint (2026-08-11)

После практических цепочек внесён bounded portable слой: context-marker сохраняет подтверждённые observables без копирования секретов; tc-generator всегда создаёт lossless deterministic CSV рядом с canonical JSON; tc-reviewer блокирует невозможный harness/oracle; tc-to-autotest следует project-native архитектуре и setup; autotest-reviewer независимо проверяет полноту и смысл автотестов; trace builder строит честную TC→METHOD→runner трассу. Подробности и literal verification ledger: `docs/to_do/skill-tests/18-real-chain-driven-automation-checkpoint.md`.

Focused combined verification после правок: **124 passed**. Package/evidence/campaign regression: **239 passed**. Все четыре изменённые skill-папки проходят `skill-creator` quick validation; новые Python tools проходят `py_compile` и Ruff; `contract_check.py --full` и `render_contract_docs.py --check` проходят. Первый полный прогон выявил 9 stale/current-binding failures при 709 passed/2 skipped; все 9 исправлены и отдельно подтверждены `9 passed`. Свежий финальный полный прогон: **718 passed, 2 skipped**. Исторические evaluator hashes не изменялись: `portable-skill-input-amendments.json` фиксирует предыдущий и текущий SHA-256 и реальные/focused evidence paths. Формальный tc-reviewer FINAL scorecard остаётся pending; новых evaluator repetitions в этом checkpoint не было.

### Portable orchestrate checkpoint (2026-08-11)

`skills/orchestrate` теперь использует только canonical JSON route из `contracts/pipeline.json`, валидирует каждый stage до forwarding, всегда создаёт lossless CSV companion после tc-generator, останавливается на reviewer/runner/trace failure и запрещает подстраивать проект под generated tests. Legacy `README.md`, `SKILL-LITE.md` и `examples.md` удалены; portable contract и исполнимые PASS/NOT_RUNNABLE fixtures добавлены. RED: 3 ожидаемых failures; GREEN: 3 passed; combined package/schema/trace regression: **264 passed**; полный suite: **721 passed, 2 skipped**; quick validation, full Ruff, contract/render checks и diff-check прошли. Evidence и literal command ledger: `docs/to_do/skill-tests/19-orchestrate-portable-checkpoint.md`. Новых evaluator runs и изменений formal orchestrate scorecards/metadata не было.

### Adapters и package audit checkpoint (2026-08-11)

Generic Python и Windows PowerShell adapters реализованы byte/timestamp-preserving и idempotent; dry-run/WhatIf не создают destination. Шесть exact adapter tests прошли. Permanent destinations содержат 20 final skill files и exact совпадают с source по path/SHA-256/mtime; второй запуск обоих: 0 copied, 20 unchanged. Task 9 удалил последние legacy tc-generator docs, исправил current root operational docs и добавил authority/link/package/evidence audit. Focused Task 8/9 gate: **9 passed**. Подробности: `docs/to_do/skill-tests/adapters/CHECKPOINT.md` и `docs/to_do/skill-tests/20-portable-package-acceptance.md`.

Свежая итоговая проверка всего текущего дерева: **730 passed, 2 skipped**; полный Ruff, contract check, render check, doctor, шесть quick validation в UTF-8 и `git diff --check` прошли. Первый quick-validation запуск `tc-reviewer` завершился до анализа скилла из-за системного `cp1251`; явный `python -X utf8` устранил только проблему среды, после чего все шесть пакетов были признаны валидными.

### Portable package commit checkpoint (2026-08-11)

Implementation/evidence commit: `6f83f8e` (`feat: complete portable testing skill package`). Манифест `docs/to_do/skill-tests/adapters/changed-files-manifest.txt` содержит 591 уникальный repository-relative путь; индекс с отключённым rename folding совпал с ним без missing/extra. Посторонних путей вне `test-orchestration-skills` и подозрительных credential/key-файлов не найдено. `CONTINUATION.md` намеренно не входил в implementation-коммит и фиксируется отдельным checkpoint-коммитом.

### Plan 3 E2E pilot-ready checkpoint (2026-08-11)

- Reproducible fixture manifest: `b20a288`.
- Honest empty-fixture `NOT_RUNNABLE`: `50d1a62`.
- Java/Python SHA-bound acceptance, JSON+CSV, generated source, runner bridge и trace: `6063075`.
- Semantic artifact review: `03370a5`; Java `ACCEPT`, Python bounded feature scope `ACCEPT`, с явным residual risk по count-only oracles и исключённым endpoint behavior.
- Six forward reports: `e8cef59`; practical gates PASS, formal debt не скрыт.
- Two tc-reviewer immutable report bytes восстановлены по authoritative hashes: `1780ddc`.
- Completion audit и ROADMAP: `fa7bcdd`; authoritative verdict — `PILOT_READY / FORMAL_ACCEPTANCE_INCOMPLETE`.

Фактические E2E результаты: Java 7 ТК → 7 методов → targeted 7/7 → full 31/31 → trace PASS; Python 6 ТК → 6 методов → targeted 6/6 → paired 17/17 → trace PASS; empty subscription fixture → `NOT_RUNNABLE`. CSV verify-only подтвердил lossless 7 и 6 case IDs. Production/config/dependencies проектов в этой приёмке не менялись.

Первый final pytest выявил единый evidence drift: 5 failures из-за двух удалённых terminal LF. После byte restoration authoritative evidence suite: **201 passed**; повторный full suite: **735 passed, 2 skipped**. Full Ruff, contract `--full`, render `--check`, doctor с обязательным absolute `--root`, Markdown links, шесть UTF-8 quick validations и шесть ключевых E2E schema checks прошли.

Два формальных P1 acceptance gaps сохранены: (1) Python Plan 3 ещё не имеет отдельной source-backed negative/authorization-denial цепочки; (2) Task 6/7 не получили новый blind forward dispatch и новый independent Sol review. Формальные tc-reviewer FINAL и orchestrate model-evaluator scorecards также pending. Подробности: `docs/to_do/completion-audit.md` и `docs/to_do/final-sol-review.md`.

## Следующий маршрут

1. Для практического пилота запускать полную fail-closed цепочку только на новых Java/Python feature scopes, показывая пользователю JSON, CSV, reviewer verdict, generated source, runner output и trace. Проект не менять и не подстраивать под ТК.
2. Изменять skills только после воспроизводимого real-chain failure; успешные Java/InvenTree evidence не повторять без новой причины.
3. Для полного design-spec acceptance провести отдельную Python negative/auth chain, затем новый blind forward cycle по шести skills и новый independent Sol review. После этого закрыть formal tc-reviewer FINAL и orchestrate evaluator scorecards либо оставить их явно pending.
4. TypeScript/Go сохранять experimental до project-native generation + review + runtime execution; PocketBase сейчас подтверждает только static Go portability и честный toolchain blocker.

## Безопасное возобновление

Из каталога `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills` сначала подтвердить scope, не предполагая clean worktree:

```powershell
git status --short
git log --oneline -8
```

Final-audit HEAD до отдельного continuation commit — `fa7bcdd7b044d9ddd4e50c35aac9bf8bc67930d4`. Portable package, E2E evidence, semantic review, forward reports и completion audit уже закоммичены. Не запускать `tc-generator` evaluators и не переоткрывать supplemental scale attempts. Не повторять tc-reviewer RED/initial/pressure/invalidated evidence. Никогда не reset, clean или широкий `git add`.

## Последние checkpoint-команды

Все команды выполнены из абсолютного cwd `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`:

```json
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_tc_to_autotest_portability.py","tests\\test_autotest_reviewer_portability.py","tests\\test_build_trace_document.py","tests\\test_trace_check.py","tests\\test_tc_generator_csv_export.py","tests\\test_tc_reviewer_campaign.py::test_reviewer_rejects_an_oracle_that_the_declared_harness_cannot_produce","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_skill_packages.py","tests\\test_skill_test_evidence.py","tests\\test_tc_reviewer_campaign.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tools\\build_trace_document.py","tools\\trace_check.py","skills\\tc-generator\\scripts\\export_test_cases_csv.py","tests\\test_build_trace_document.py","tests\\test_trace_check.py","tests\\test_tc_generator_csv_export.py","tests\\test_tc_to_autotest_portability.py","tests\\test_autotest_reviewer_portability.py","tests\\test_skill_test_evidence.py","tests\\test_tc_reviewer_campaign.py","tests\\test_skill_packages.py"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\contract_check.py","--root",".","--full"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\render_contract_docs.py","--root",".","--check"]
["git","diff","--check"]
["git","status","--short"]
```

Исторические команды выше завершились exit `0`. Свежий package gate завершён с `730 passed, 2 skipped`; Ruff clean, contracts passed, generated docs current, doctor PASS, все шесть quick validations PASS в UTF-8. Implementation/evidence scope закоммичен как `6f83f8e`; reset/clean/широкий add не выполнялись.

## Plan 3 final material commands

Все команды ниже выполнены из того же абсолютного cwd:

```json
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_skill_test_evidence.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tools","tests","skills\\tc-generator\\scripts","adapters\\generic"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\contract_check.py","--root","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills","--full"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\render_contract_docs.py","--root","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills","--check"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\doctor.py","--root","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills"]
["git","diff","--check"]
["git","status","--short"]
```

Final outcomes: full pytest **735 passed, 2 skipped**; authoritative evidence **201 passed**; Ruff/contract/render/doctor/links/quick validators/E2E schema+trace+CSV checks PASS. Подробный literal ledger находится в `docs/to_do/completion-audit.md`.
