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
- Для текущего context-marker: сначала diagnosis skill injection/read path, затем один diagnostic run; после verified fix — до 3 total; до 5 только через stable final gate.

## Принятая история

Plan 1/core и Plan 2 scaffold/ASCII migrations приняты исторически. Важные архивные commits: `0334bcd` (failed context final batch), `1b0ac0c` (unstable context final batch) и `c41896c` (stopped failed context final v1 batch, archive/r5). Локальные инструменты этой машины: worktree `D:\AI-Projects\.worktrees\portable-testing-skills`, Python `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`, quick validator `C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py`, Sol skill `C:\Users\User\.codex\plugins\cache\sol-advisor\sol-advisor\0.4.0\skills\orchestration\SKILL.md`.

## Точный checkpoint

Перед commit этого файла `HEAD` был `c41896c`. `archive/r5` уже закоммичен: stopped v1 batch содержит 3 завершённых rep, `rep-04` только с prompt и прерван, `rep-05` отсутствует; в активном FINAL остались только placeholders. `rep-03` schema-valid, но семантически провалился: `REQ-ORDER` ids, нет provenance identity, нет inline locator у sources/warnings, endpoint потерян. Повторно не запускать и не заменять/чинить r5. Context-marker **не принят**.

Незакоммичены семантика skill и protocol v1; parent checks: **410 passed, 2 skipped**. Sol ранее дал `ship` для pre-run wording/protocol bypass fix, но это не acceptance batch. Официальные metadata и три scorecard ещё pending; активные RED/initial/pressure evidence тоже незакоммичены.

## Следующий маршрут

1. Новый чат сначала диагностирует, почему fresh evaluator не последовал текущему `SKILL.md`/reference: подтвердить injection/read path skill и evaluator brief, исследовать r5 и evaluator harness. Не добавлять текст и не запускать новые reps вслепую.
2. Создать новый RED/causal plan; только после root cause — исправление, review и новый отдельный FINAL по обязательному gate 1→3→5 (с `protocol_contract_version: 1`, literal `argv`/`cwd`, без replacement/reuse). r5 не переиспользовать и не ремонтировать.
3. Получить семантический Sol scoring; собрать metadata для 16 runs и 3 scorecards; прогнать полную verification; сделать scoped commit; получить final Sol acceptance.
4. Затем кампании оставшихся skill по порядку: `tc-generator` → `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` → `orchestrate`.
5. Затем Plan 3 E2E: `step5-java-demo`, `InvenTree-master`, `subscription-renewal-service`; в конце полный audit и verdict.

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
