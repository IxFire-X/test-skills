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

## Следующий маршрут

1. Следующий skill строго по Plan 2 Task 4 — `tc-reviewer`. Сначала полностью изучить его существующие scenario/protocol/evidence, cleanup-map row и delivery canonical inputs; проверить текущий dirty scope до любых правок.
2. Подготовить четыре distinct schema-valid `tc-generator-output` input: `clean-accepted.json`, `typo-only.json`, `blocking-missing-result.json`, `blocking-fabricated-auth.json`. Один prompt проверяет все четыре label и пишет четыре отдельно названных `tc-reviewer-output` envelope; каждый валидируется против `tc-reviewer-output.schema.json`.
3. RED-control по умолчанию — один корректный запуск без delivery skill. Расширять до трёх только при неоднозначном результате или неожиданном прохождении rubric. Четыре global assertions: clean accepted/no correction; typo AUTO_FIX с correction; missing result blocking/no invented correction; fabricated auth blocking/no invented correction. При первом protocol/schema/semantic failure остановиться без repair/retry/replacement.
4. После честного RED реализовать portable delivery с опорой на `superpowers:writing-skills` и `skill-creator`; затем разнообразные GREEN inputs, pressure и финальные пять acceptance repetitions. Перед каждым native delegation выполнять Sol Advisor role preflight. Все commands — literal argv + абсолютный cwd; постоянные artifacts — только `docs/to_do/`.
5. После evidence/metadata/scorecards, полной verification, fresh Sol `ship` и exact manifest сделать scoped tc-reviewer commit и снова обновить CONTINUATION. Затем: `tc-to-autotest` → `autotest-reviewer` → `orchestrate` → Plan 3 E2E (`step5-java-demo`, `InvenTree-master`, `subscription-renewal-service`) → полный audit/verdict.

## Безопасное возобновление

Из каталога `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills` сначала подтвердить scope, не предполагая clean worktree:

```powershell
git status --short
git log --oneline -8
```

Ожидаемый implementation HEAD — `c9d9aa6`; до checkpoint-коммита dirty должен быть только этот `CONTINUATION.md`. `tc-generator` закрыт: не запускать его evaluators, не менять его evidence и не переоткрывать supplemental scale attempts. Следующий допустимый evaluator относится только к новому `tc-reviewer` RED-control после завершения его read-only подготовки и role preflight. Перед любым commit снова сверять `git status`, exact manifest и diff только owned paths. Никогда не reset и не выметать unrelated files.

## Последние checkpoint-команды

Implementation/evidence commit выполнен из абсолютного cwd `D:\AI-Projects\.worktrees\portable-testing-skills`:

```json
["git","commit","-m","feat: complete portable tc-generator campaign"]
```

Exit `0`, commit `c9d9aa6`. После него полное чтение `CONTINUATION.md` и строк Plan 2 Task 4 выполнено read-only из абсолютного cwd `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills` перед этой правкой.

Финальная checkpoint-проверка из того же cwd:

```json
["git","status","--short"]
["git","diff","--check","--","docs/to_do/CONTINUATION.md"]
["git","diff","--stat","--","docs/to_do/CONTINUATION.md"]
```

Все три команды завершились exit `0`; status содержит только `M docs/to_do/CONTINUATION.md`, diff-check чист, diff-stat — один файл.
