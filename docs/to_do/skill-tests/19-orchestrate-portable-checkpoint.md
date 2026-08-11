# Portable orchestrate checkpoint

Дата: 2026-08-11.

## Итог

`skills/orchestrate` переведён с legacy XML/quickstart/retry-документации на компактный portable JSON workflow.

- Канонический порядок и skill paths берутся только из `contracts/pipeline.json`.
- Каждый stage JSON валидируется до следующего этапа.
- После tc-generator всегда создаётся и lossless-проверяется CSV companion; downstream authority остаётся JSON.
- Reviewer rework, runner `FAIL|NOT_RUNNABLE` и trace mismatch останавливают цепочку.
- `run_tests.py` остаётся единственным execution authority.
- Финальный PASS требует execution-required trace и exact orchestrator cross-check.
- Production, existing tests, configuration, dependencies и behavior проекта не меняются ради generated tests.
- Logical failure сохраняется immutable; новая попытка возможна только после реального изменения входа/skill/controller contract.

Legacy `README.md`, `SKILL-LITE.md` и `examples.md` удалены после переноса полезных правил в `references/orchestration-contract.md`. Добавлены project-neutral PASS/NOT_RUNNABLE fixtures в `assets/orchestration-fixtures/`.

## Практический RED

Старый orchestrate:

- описывал XML envelopes вместо schema-valid JSON;
- заканчивался до `run_tests.py` и execution trace;
- разрешал автоматические повторы и project initialization;
- не знал обязательный JSON→CSV transport;
- смешивал project discovery с разрешением менять проект.

Flask real chain дополнительно доказал, что schema-valid и reviewer-accepted промежуточных артефактов недостаточно: до исполнения был найден невозможный harness/oracle. Новый orchestrate требует полный reviewer → runner → trace gate и не считает человеческий отчёт execution evidence.

## Детерминированная проверка

Первый focused test run был ожидаемо RED: `3 failed` из-за трёх legacy-файлов и отсутствующих portable reference/assets. После минимальной реализации тот же run: `3 passed`.

Combined package/schema/trace regression: `264 passed`. Полный suite: `721 passed, 2 skipped`. `quick_validate.py`, full Ruff, `contract_check.py --full`, render check и `git diff --check` прошли. Первый full Ruff выявил только лишнюю пустую строку после import в текущем untracked `test_context_marker_portability.py`; root cause подтверждён отдельным Ruff-прогоном, удалена одна пустая строка, targeted test и полный Ruff стали GREEN. Новых evaluator repetitions, campaign metadata/scorecard записей и project mutations не было; formal orchestrate campaign остаётся pending.

## Literal command ledger

Абсолютный cwd для всех команд:

`D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`

```json
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_orchestrate_portability.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\trace_check.py","skills\\orchestrate\\assets\\orchestration-fixtures\\accepted-trace-document.json","--require-execution"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_orchestrate_portability.py","tests\\test_skill_packages.py","tests\\test_contract_check.py","tests\\test_contract_docs.py","tests\\test_artifact_contracts.py","tests\\test_trace_check.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-X","utf8","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","skills\\orchestrate"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\contract_check.py","--root",".","--full"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tests\\test_orchestrate_portability.py"]
["git","diff","--check","--","skills/orchestrate","tests/test_orchestrate_portability.py"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tools","tests","skills\\tc-generator\\scripts"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\render_contract_docs.py","--root",".","--check"]
["git","diff","--check"]
```
