# Продолжение portable testing skills

## Цель и источник истины

Довести пакет до **host-neutral drop-in advanced testing skills**, включая SDD; это не Codex-only решение. Канонический источник — исходный `test-orchestration-skills`. `Grok`/`Grokv2` — только материал для сравнения, не источник требований. Этот worktree/branch и fixture-проекты имеют лишь более позднюю, проверочную роль.

Планы выполняются строго так: [master](../superpowers/plans/2026-08-06-portable-testing-skills-master.md) → [Plan 1: core/runtime](../superpowers/plans/2026-08-06-portable-core-runtime.md) → [Plan 2: skills/adapters](../superpowers/plans/2026-08-06-portable-skills-adapters.md) → [Plan 3: E2E acceptance](../superpowers/plans/2026-08-06-e2e-acceptance.md).

## Обязательные правила

- Постоянные артефакты — только в `docs/to_do/`; грязное worktree сохранять. Не делать `reset`, очистку или широкий `git add` чужих файлов.
- Одна `writing-skills`-guided кампания одного skill за раз. Terra реализует; свежий Sol только просматривает/выносит вердикт. Перед каждым native delegation: `agent_type: sol_advisor_terra_implementer`, затем для review — `agent_type: sol_advisor_sol_reviewer`, оба с `fork_turns: none`.
- Обязательная role-preflight команда (из каталога `SKILL.md`): `skill_dir=<directory-containing-this-SKILL.md>; installer="$skill_dir/../../scripts/install-agents.sh"; sh "$installer" --check`. Она должна завершиться 0, а доступные `agent_type` должны буквально содержать оба имени выше; иначе остановить lane, не подменять роль/модель.
- Неудачные прогоны архивировать, а не чинить/перезаписывать. Forward contract: каждый фактически выполненный command записан буквально как `argv`-массив и абсолютный `cwd`, в порядке запуска, в metadata и `run-protocol`; не shell-строкой.

## ОБЯЗАТЕЛЬНО: адаптивный бюджет evaluator-прогонов

- После узкой правки wording/fix никогда автоматически не запускать пять полных evaluator repetitions. Последовательный gate: **1 → 3 → 5**. После каждого edit — deterministic/static/schema tests; затем ровно один fresh targeted diagnostic/smoke evaluator; только при его успехе — ещё две независимые rep (всего 3).
- Пять независимых rep — только финальный acceptance стабильного кандидата либо при явно подтверждённых высокой вариативности/риске с записанной причиной. Эта политика заменяет прежнюю blanket-интерпретацию «5+ на каждый wording variant», но сохраняет финальный deployment gate.
- При первом protocol или semantic failure сразу остановиться: не завершать batch, не заменять, не ремонтировать и не перезапускать; сначала root-cause diagnosis. Causally-valid RED/initial/pressure evidence переиспользовать, не повторять лишь из-за wording.
- Механические invariants (`ID`, форма provenance, paths, capture commands) прежде переводить в deterministic validators, затем тратить новые evaluator runs.
- `context-marker` завершён по этому gate в commit `686908c`; для следующего skill снова начинать с deterministic/static checks и одной fresh diagnostic rep, расширяя только 1 → 3 → 5.
- Для skill work основная методическая опора — `superpowers:writing-skills` и `skill-creator`: во время итераций выполнять узкие deterministic checks, а исчерпывающие проверки оставлять для final acceptance. Это не отменяет baseline-before-skill принцип и adaptive gate 1 → 3 → 5.

## Принятая история

Plan 1/core и Plan 2 scaffold/ASCII migrations приняты исторически. Важные архивные commits: `0334bcd` (failed context final batch), `1b0ac0c` (unstable context final batch) и `c41896c` (stopped failed context final v1 batch, archive/r5). Принятый context-marker checkpoint: `686908c`; принятый остановленный checkpoint `tc-generator` RED-v1: `fdd16aa`. Локальные инструменты этой машины: worktree `D:\AI-Projects\.worktrees\portable-testing-skills`, Python `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`, quick validator `C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py`, Sol skill `C:\Users\User\.codex\plugins\cache\sol-advisor\sol-advisor\0.5.0\skills\orchestration\SKILL.md`.

## Точный checkpoint

Исторический checkpoint `context-marker` принят и закоммичен как `686908c`. Root cause r5: evaluator получил byte-identical старый canonical prompt, который не доставлял `skills/context-marker/SKILL.md` и `references/context-artifact-contract.md`; audit snapshots сами по себе не доказывали injection/read. Исправление явно связывает FINAL prompt с двумя canonical skill inputs и их SHA-256, задаёт repository-root resolution, усиливает deterministic schema/semantic guards и сохраняет immutable r5 без rerun/repair/replacement.

Его новый FINAL прошёл adaptive gate 1 → 3 → 5. Fresh Sol acceptance: `ship`, все 20 rubric booleans true. Официальные три scorecard и metadata всех 16 runs complete; исторические RED/initial prompts честно phase-bound к hash `227751…`, FINAL — к `2b750…`. Parent checks после завершения: **418 passed, 2 skipped**; contract check, render check, quick_validate и diff-check прошли. Эти факты остаются историческим context-marker evidence, а не доказательством для `tc-generator`.

Текущий терминальный checkpoint — остановленный `tc-generator` RED-v1, закоммиченный как `fdd16aa`. RED-v1 rep-01 и rep-02 успешны и scorable, но фаза не завершена и не comparable. RED-v1 rep-03 immutable protocol-invalid и unscored: schema validator завершился с exit 1, потому что все шесть значений `test_data` являются objects, тогда как schema требует arrays. После этого не выполнялись semantic command, rep-04/05, retry, repair или replacement. Архивированный/reserved output byte-identical: SHA-256 `08a3b3beb4a9b257e717cd40ced6f7d492ae1d606b6a2a8ff4c237c34735db55`. Metadata status и все scorecards остаются pending.

Focused harness/recorder verification: **39 passed**. Authoritative evidence suite: **112 passed** и одна intentional failure для отсутствующего `skills/tc-generator/references/case-generation-contract.md`. Fresh Sol acceptance остановленного checkpoint: `ship`; recovery commitment review: `change`, route A. Это verdict о честно остановленном evidence, не разрешение перезапускать, изменять или смешивать старые RED-v1 артефакты.

## Следующий маршрут

1. До любой implementation или нового baseline run внести поправки в Plan 2 и campaign contracts: определить отдельную versioned RED phase со своими rep-01..05, paths и snapshots, сохранив те же baseline input/prompt/rubric/evaluator и adaptive gate 1 → 3 → 5. Не изобретать здесь имя этой фазы или дизайн metadata fields.
2. Никогда не объединять старые RED-v1 rep-01/02 с новым scorecard и никогда не трогать старый rep-03. Новый baseline и evidence вести только в distinct versioned phase; не rerun и не contaminating старое evidence.
3. После amendment contracts выполнить `writing-skills`-guided работу и новый adaptive evaluator gate 1 → 3 → 5 с literal `argv`/absolute `cwd`, fresh Terra evaluator и fresh Sol reviews; при первом failure остановиться и диагностировать, не заменять rep.
4. После полного нового evidence/metadata/scorecards и scoped commit повторить по порядку: `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` → `orchestrate`.
5. Затем Plan 3 E2E: `step5-java-demo`, `InvenTree-master`, `subscription-renewal-service`; в конце полный audit и verdict.

## Безопасное возобновление

Из корня worktree сначала подтвердить scope, не предполагая clean worktree или новый `tc-generator` campaign «с нуля»:

```powershell
git status --short
git log --oneline -12
```

До amendment Plan 2 и campaign contracts не запускать implementation, validator, evaluator, semantic checker или новый baseline. После amendment применять narrow deterministic checks в итерациях, а exhaustive checks — только перед final acceptance. Перед любым commit снова сверять `git status` и diff только owned paths. Никогда не reset и не выметать unrelated files.
