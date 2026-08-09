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
- `context-marker` завершён по этому gate в commit `686908c`; `tc-generator` завершает уже принятую сопоставимую RED-v2 последовательность 1 → 3 → 5. Начиная с `tc-reviewer`, RED по умолчанию ограничить одним корректным контрольным запуском и расширять до трёх только при неоднозначном результате или неожиданном прохождении rubric; высвободившийся бюджет переносить на разнообразные GREEN/pressure inputs. До `tc-reviewer` явно внести эту методическую поправку в master-план.
- Для skill work основная методическая опора — `superpowers:writing-skills` и `skill-creator`: во время итераций выполнять узкие deterministic checks, а исчерпывающие проверки оставлять для final acceptance. Это не отменяет baseline-before-skill принцип и adaptive gate 1 → 3 → 5.

## Принятая история

Plan 1/core и Plan 2 scaffold/ASCII migrations приняты исторически. Важные архивные commits: `0334bcd` (failed context final batch), `1b0ac0c` (unstable context final batch) и `c41896c` (stopped failed context final v1 batch, archive/r5). Принятый context-marker checkpoint: `686908c`; принятый остановленный checkpoint `tc-generator` RED-v1: `fdd16aa`; принятый amendment для отдельного RED-v2 recovery: `9ddafed`. Локальные инструменты этой машины: worktree `D:\AI-Projects\.worktrees\portable-testing-skills`, Python `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`, quick validator `C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py`, Sol skill `C:\Users\User\.codex\plugins\cache\sol-advisor\sol-advisor\0.5.0\skills\orchestration\SKILL.md`.

## Точный checkpoint

Исторический checkpoint `context-marker` принят и закоммичен как `686908c`. Root cause r5: evaluator получил byte-identical старый canonical prompt, который не доставлял `skills/context-marker/SKILL.md` и `references/context-artifact-contract.md`; audit snapshots сами по себе не доказывали injection/read. Исправление явно связывает FINAL prompt с двумя canonical skill inputs и их SHA-256, задаёт repository-root resolution, усиливает deterministic schema/semantic guards и сохраняет immutable r5 без rerun/repair/replacement.

Его новый FINAL прошёл adaptive gate 1 → 3 → 5. Fresh Sol acceptance: `ship`, все 20 rubric booleans true. Официальные три scorecard и metadata всех 16 runs complete; исторические RED/initial prompts честно phase-bound к hash `227751…`, FINAL — к `2b750…`. Parent checks после завершения: **418 passed, 2 skipped**; contract check, render check, quick_validate и diff-check прошли. Эти факты остаются историческим context-marker evidence, а не доказательством для `tc-generator`.

Остановленный `tc-generator` RED-v1 закоммичен как `fdd16aa`. RED-v1 rep-01 и rep-02 успешны и scorable, но фаза не завершена и не comparable. RED-v1 rep-03 immutable protocol-invalid и unscored: schema validator завершился с exit 1, потому что все шесть значений `test_data` являются objects, тогда как schema требует arrays. После этого не выполнялись semantic command, rep-04/05, retry, repair или replacement. Архивированный/reserved output byte-identical: SHA-256 `08a3b3beb4a9b257e717cd40ced6f7d492ae1d606b6a2a8ff4c237c34735db55`. Эти RED-v1 runs перенесены в `historical_runs` только как integrity evidence; invalidated rep-03 сохранён без изменений и никогда не входит в scorecard.

Текущий checkpoint — принятый amendment `9ddafed`: активная фаза `01-red-control-v2`, отдельные output/protocol/report paths, пустой активный ledger, прежние evaluator tuple/prompt hash/input/rubric и строгая изоляция от RED-v1. Recorder проверяет immutable historical/invalidated protocol, SHA/byte provenance `checked_output`, filesystem identity collisions, contiguous active prefix и не допускает schema-invalid шестнадцатый `pending` append. Parent verification: **46 passed** для recorder+campaign, **2 passed** focused scaffold/versioned, metadata schema valid, RED-v1 immutability diff и diff-check прошли. Ранее полный authoritative evidence suite: **113 passed** и одна intentional failure для отсутствующего `skills/tc-generator/references/case-generation-contract.md`. Последний fresh Sol acceptance amendment: `ship`; проверок достаточно.

## Следующий маршрут

1. Начать `01-red-control-v2/rep-01` с точным immutable RED prompt hash `c63cbae7a51489338780d2b7944fd079d3d457f6794c034940707202e5edd1a5`, тем же canonical input/rubric и evaluator tuple `sol_advisor_terra_implementer` / `native-subagent-v2` / `gpt-5.6-terra/high (role-pinned)`. Skill/reference остаются withheld.
2. Выполнить adaptive gate 1 → 3 → 5 последовательно. Каждый запуск: prompt snapshot, evaluator output, ровно один schema validator, затем ровно один semantic checker; literal `argv`, абсолютный campaign cwd и immutable protocol evidence. При первом nonzero остановиться, не ремонтировать, не заменять и не повторять repetition.
3. Никогда не объединять RED-v1 rep-01/02 с RED-v2 scorecard и никогда не трогать RED-v1 rep-03. Implementation `skills/tc-generator/**` запрещена до пяти успешных RED-v2 repetitions и complete RED-v2 scorecard.
4. После полного `tc-generator` evidence/metadata/scorecards, verification, fresh Sol acceptance и scoped commit обновить master-план для сокращённого RED-контроля следующих skills; затем продолжить: `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer` → `orchestrate`.
5. Затем Plan 3 E2E: `step5-java-demo`, `InvenTree-master`, `subscription-renewal-service`; в конце полный audit и verdict.

## Безопасное возобновление

Из корня worktree сначала подтвердить scope, не предполагая clean worktree или новый `tc-generator` campaign «с нуля»:

```powershell
git status --short
git log --oneline -12
```

Amendment уже принят в `9ddafed`; следующий допустимый evaluator — только fresh Terra для `01-red-control-v2/rep-01`. До пяти успешных RED-v2 repetitions не создавать `skills/tc-generator/references/case-generation-contract.md` и не менять delivery skill. В итерациях применять узкие deterministic checks, а exhaustive checks — только перед final acceptance. Перед любым commit снова сверять `git status` и diff только owned paths. Никогда не reset и не выметать unrelated files.
