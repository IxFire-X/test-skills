# Продолжение portable testing skills

## Цель и источник истины

Довести пакет до **host-neutral drop-in advanced testing skills**, включая SDD; это не Codex-only решение. Канонический источник — исходный `test-orchestration-skills`. `Grok`/`Grokv2` — только материал для сравнения, не источник требований. Этот worktree/branch и fixture-проекты имеют лишь более позднюю, проверочную роль.

Планы выполняются строго так: [master](../superpowers/plans/2026-08-06-portable-testing-skills-master.md) → [Plan 1: core/runtime](../superpowers/plans/2026-08-06-portable-core-runtime.md) → [Plan 2: skills/adapters](../superpowers/plans/2026-08-06-portable-skills-adapters.md) → [Plan 3: E2E acceptance](../superpowers/plans/2026-08-06-e2e-acceptance.md).

## Обязательные правила

- Постоянные артефакты — только в `docs/to_do/`; грязное worktree сохранять. Не делать `reset`, очистку или широкий `git add` чужих файлов.
- Одна TDD-кампания одного skill за раз. Terra реализует; свежий Sol только просматривает/выносит вердикт. Перед каждым native delegation: `agent_type: sol_advisor_terra_implementer`, затем для review — `agent_type: sol_advisor_sol_reviewer`, оба с `fork_turns: none`.
- Обязательная role-preflight команда (из каталога `SKILL.md`): `skill_dir=<directory-containing-this-SKILL.md>; installer="$skill_dir/../../scripts/install-agents.sh"; sh "$installer" --check`. Она должна завершиться 0, а доступные `agent_type` должны буквально содержать оба имени выше; иначе остановить lane, не подменять роль/модель.
- Неудачные прогоны архивировать, а не чинить/перезаписывать. Forward contract: каждый фактически выполненный command записан буквально как `argv`-массив и абсолютный `cwd`, в порядке запуска, в metadata и `run-protocol`; не shell-строкой.

## Принятая история

Plan 1/core и Plan 2 scaffold/ASCII migrations приняты исторически. Важные архивные commits: `0334bcd` (failed context final batch) и `1b0ac0c` (unstable context final batch). Локальные инструменты этой машины: worktree `D:\AI-Projects\.worktrees\portable-testing-skills`, Python `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`, quick validator `C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py`, Sol skill `C:\Users\User\.codex\plugins\cache\sol-advisor\sol-advisor\0.4.0\skills\orchestration\SKILL.md`.

## Точный checkpoint

Перед commit этого файла `HEAD` был `1b0ac0c`. Незакоммичены context-marker RED/initial/pressure и hardening skill/protocol. Финалы r3/r4 отклонены и архивированы; официальные metadata и три scorecard ещё не собраны; в активном FINAL остались только placeholders. Combined checks сейчас: **410 passed, 2 skipped**. Нужен свежий bypass re-review: пока он не дал `ship`, context-marker **не принят**.

## Следующий маршрут

1. Получить/восстановить вердикт свежего Sol. Если `ship`, выполнить ровно пять свежих последовательных FINAL-rep текущим skill и `protocol_contract_version: 1`: literal `argv`/`cwd`, без replacement/reuse.
2. Получить семантический Sol scoring; собрать metadata для 16 runs и 3 scorecards; прогнать полную verification; сделать scoped commit; получить final Sol acceptance.
3. Затем кампании оставшихся skill по порядку: `tc-generator` → `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` → `orchestrate`.
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
