# Portable package acceptance checkpoint

Дата: 2026-08-11.

## Scope

Task 9 проверяет authority declarations, package shape, local Markdown links, evidence JSON и финальные adapter copies.

## RED и исправления

Первый focused audit: 3 failed.

1. ROADMAP содержал obsolete trace_map declaration.
2. Link parser ошибочно считал PowerShell code в immutable archive Markdown-ссылкой и сканировал archived skill snapshots.
3. tc-generator всё ещё содержал legacy README.md и examples.md.

Root cause fixes:

- link audit исключает fenced/inline code и archive/skill-snapshot fixture trees;
- tc-generator legacy docs удалены: полезный актуальный материал уже находится в references/case-generation-contract.md и deterministic CSV script;
- Instruction.md, USER-GUIDE.md и ROADMAP.md заменены текущими portable JSON/CSV/runner/trace инструкциями;
- authority declarations используют canonical artifact names;
- evidence discovery строго допускает шесть skill campaigns и один auxiliary adapters/artifacts root.

Focused package audit: 3 passed. Combined adapters/package audit: 9 passed.

## Финальная проверка пакета

- Полный набор: **730 passed, 2 skipped**, exit 0.
- Полный Ruff-аудит `tools`, `tests`, `skills/tc-generator/scripts` и `adapters/generic`: clean.
- `contract_check.py --full`, проверка сгенерированной документации и `doctor.py`: PASS.
- Все шесть canonical skill-пакетов прошли `skill-creator` quick validation.
- `git diff --check`: exit 0.

Первый запуск quick validator для `tc-reviewer` остановился до проверки содержимого: Windows выбрал `cp1251` и Python не смог декодировать UTF-8. Повтор с явным `-X utf8` успешно проверил все шесть пакетов; файлы скилла из-за этого не менялись.

## Acceptance boundaries

- Ровно шесть skill packages.
- Package entries ограничены SKILL.md, references, scripts и assets.
- JSON — downstream authority; CSV — обязательный lossless transport companion.
- Java/Python acceptance требует runner PASS и trace PASS.
- Project production/config/dependencies не меняются ради tests.
- Historical docs/evaluator archives сохраняются как evidence, но не являются current authority.
- Formal tc-reviewer FINAL и orchestrate model-evaluator scorecards остаются pending evidence debt и не объявлены complete.

## Material verification commands

Абсолютный cwd:

D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills

~~~json
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_portable_package.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_portable_package.py","tests\\test_adapters.py","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","adapters\\generic","tests\\test_adapters.py","tests\\test_portable_package.py"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_skill_test_evidence.py::test_skill_test_scaffolds_are_complete_schema_valid_and_confined","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q","--tb=short"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tools","tests","skills\\tc-generator\\scripts","adapters\\generic"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\contract_check.py","--root",".","--full"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\render_contract_docs.py","--root",".","--check"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\doctor.py"]
["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-X","utf8","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","<absolute-skill-directory>"]
["git","diff","--check"]
~~~
