# Продолжение portable testing skills

## Цель и источник истины

Довести пакет до **host-neutral drop-in advanced testing skills**, включая SDD; это не Codex-only решение. Канонический источник — исходный `test-orchestration-skills`. `Grok`/`Grokv2` — только материал для сравнения, не источник требований. Этот worktree/branch и fixture-проекты имеют лишь более позднюю, проверочную роль.

Планы выполняются строго так: [master](../superpowers/plans/2026-08-06-portable-testing-skills-master.md) → [Plan 1: core/runtime](../superpowers/plans/2026-08-06-portable-core-runtime.md) → [Plan 2: skills/adapters](../superpowers/plans/2026-08-06-portable-skills-adapters.md) → [Plan 3: E2E acceptance](../superpowers/plans/2026-08-06-e2e-acceptance.md).

## Обязательные правила

- Постоянные артефакты — только в `docs/to_do/`; грязное worktree сохранять. Не делать `reset`, очистку или широкий `git add` чужих файлов.
- Одна TDD-кампания одного skill за раз. Terra реализует; свежий Sol только просматривает/выносит вердикт. Перед каждым native delegation: `agent_type: sol_advisor_terra_implementer`, затем для review — `agent_type: sol_advisor_sol_reviewer`, оба с `fork_turns: none`.
- Обязательная role-preflight команда (из каталога `SKILL.md`): `skill_dir=<directory-containing-this-SKILL.md>; installer="$skill_dir/../../scripts/install-agents.sh"; sh "$installer" --check`. Она должна завершиться 0, а доступные `agent_type` должны буквально содержать оба имени выше; иначе остановить lane, не подменять роль/модель.
- Неудачные прогоны архивировать, а не чинить/перезаписывать. Forward contract: каждый фактически выполненный command записан буквально как `argv`-массив и абсолютный `cwd`, в порядке запуска, в metadata и `run-protocol`; не shell-строкой.

## ОБЯЗАТЕЛЬНО: адаптивный бюджет evaluator-прогонов

- После узкой правки wording/fix никогда автоматически не запускать пять полных evaluator repetitions. Последовательный gate: **1 → 3 → 5**. После каждого edit — deterministic/static/schema tests; затем ровно один fresh targeted diagnostic/smoke evaluator; только при его успехе — ещё две независимые rep (всего 3).
- Пять независимых rep — только финальный acceptance стабильного кандидата либо при явно подтверждённых высокой вариативности/риске с записанной причиной. Эта политика заменяет прежнюю blanket-интерпретацию «5+ на каждый wording variant», но сохраняет финальный deployment gate.
- При первом protocol или semantic failure сразу остановиться: не завершать batch, не заменять, не ремонтировать и не перезапускать; сначала root-cause diagnosis. Causally-valid RED/initial/pressure evidence переиспользовать, не повторять лишь из-за wording.
- Механические invariants (`ID`, форма provenance, paths, capture commands) прежде переводить в deterministic validators, затем тратить новые evaluator runs.
- `context-marker` завершён по этому gate в commit `686908c`; для следующего skill снова начинать с deterministic/static checks и одной fresh diagnostic rep, расширяя только 1 → 3 → 5.

## Принятая история

Plan 1/core и Plan 2 scaffold/ASCII migrations приняты исторически. Важные архивные commits: `0334bcd` (failed context final batch), `1b0ac0c` (unstable context final batch) и `c41896c` (stopped failed context final v1 batch, archive/r5). Принятый context-marker checkpoint: `686908c`. Локальные инструменты этой машины: worktree `D:\AI-Projects\.worktrees\portable-testing-skills`, Python `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`, quick validator `C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py`, Sol skill `C:\Users\User\.codex\plugins\cache\sol-advisor\sol-advisor\0.5.0\skills\orchestration\SKILL.md`.

## Точный checkpoint

`context-marker` принят и закоммичен как `686908c`. Root cause r5: evaluator получил byte-identical старый canonical prompt, который не доставлял `skills/context-marker/SKILL.md` и `references/context-artifact-contract.md`; audit snapshots сами по себе не доказывали injection/read. Исправление явно связывает FINAL prompt с двумя canonical skill inputs и их SHA-256, задаёт repository-root resolution, усиливает deterministic schema/semantic guards и сохраняет immutable r5 без rerun/repair/replacement.

Новый FINAL прошёл адаптивный gate 1 → 3 → 5. Fresh Sol acceptance: `ship`, все 20 rubric booleans true. Официальные три scorecard и metadata всех 16 runs complete; исторические RED/initial prompts честно phase-bound к hash `227751…`, FINAL — к `2b750…`. Parent checks после завершения: **418 passed, 2 skipped**; contract check, render check, quick_validate и diff-check прошли. После commit worktree был clean.

## Следующий маршрут

1. Начать следующую кампанию Plan 2 с `tc-generator`: проверить scenario/protocol, существующий causal RED и актуальность skill-input delivery; не переносить context-marker fixture semantics на другой skill.
2. Выполнить TDD и adaptive evaluator gate 1 → 3 → 5 с literal `argv`/absolute `cwd`, fresh Terra evaluator и fresh Sol reviews; при первом failure остановиться и диагностировать, не заменять rep.
3. После полного evidence/metadata/scorecards и scoped commit повторить по порядку: `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` → `orchestrate`.
4. Затем Plan 3 E2E: `step5-java-demo`, `InvenTree-master`, `subscription-renewal-service`; в конце полный audit и verdict.

## Безопасное возобновление

Из корня worktree:

```powershell
git status --short
git log --oneline -12
$py = 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe'
$pack = 'test-orchestration-skills'
$validator = 'C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py'
& $py -m pytest "$pack/tests"
& $py "$pack/tools/contract_check.py" --root $pack --full
& $py "$pack/tools/render_contract_docs.py" --root $pack --check
& $py $validator "$pack/skills/context-marker"
```

Перед любым commit снова сверять `git status` и diff только owned paths. Никогда не reset и не выметать unrelated files.
