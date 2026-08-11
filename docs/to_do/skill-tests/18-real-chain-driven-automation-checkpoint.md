# Real-chain-driven automation checkpoint — 2026-08-11

## Результат

Практические дефекты, найденные на `step5-java-demo`, InvenTree, PocketBase и Flask, перенесены в переносимые контракты навыков без подстройки проектов под тест-кейсы.

- `tc-generator`: канонический JSON остаётся машинным источником истины; после его валидации всегда создаётся соседний детерминированный CSV. Экспорт сохраняет все поля тест-кейса и сам доказывает JSON↔CSV эквивалентность.
- `tc-reviewer`: проверяет не только отдельные токены, но и исполнимость полной цепочки `setup → action → observable result`; несовместимый harness/oracle блокируется кодом `HARNESS_ORACLE_MISMATCH`.
- `tc-to-autotest`: следует фактической архитектуре проекта, не изобретает `BaseApiTest`, сохраняет roles/permissions/fixtures/setup/client/auth, не переносит секреты, не меняет production/config/dependencies и выпускает только изолированные companion tests.
- `autotest-reviewer`: независимо проверяет полноту, отсутствие лишних executable tests, точные data/oracles, project-native setup и language-native traceability; генератор не является авторитетом смысла.
- `build_trace_document.py`: строит трассу только из schema-valid артефактов и runner evidence; один реальный `run_id` допустимо связывает несколько методов, уникальность сохраняется по `(run_id, method_id)`.

Исторические evaluator-хэши не переписаны. Их связь с текущими real-chain amendments закреплена в `docs/to_do/skill-tests/portable-skill-input-amendments.json`. Формальный synthetic FINAL scorecard `tc-reviewer` остаётся `pending`; этот checkpoint не выдаёт его за завершённый.

## Исходные практические сигналы

- `step5-java-demo`: старый генератор требовал отсутствующий `BaseApiTest`, хотя проект уже имел рабочий `@WebMvcTest + MockMvc` pattern.
- InvenTree: generated tests дошли до endpoint без обязательного permission setup и получили 403; прежний reviewer это принял.
- PocketBase: потребовалось запретить перенос credential/secret values и Java-only правила review для Go `t.Run`.
- Flask: reviewer принял JSON oracle, который заявленный harness не мог породить без дополнительного `jsonify` setup.

## Literal command ledger

Абсолютный `cwd` для всех команд ниже:

`D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`

```json
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_tc_to_autotest_portability.py","tests\\test_autotest_reviewer_portability.py","tests\\test_build_trace_document.py","tests\\test_trace_check.py","tests\\test_tc_generator_csv_export.py","tests\\test_tc_reviewer_campaign.py::test_reviewer_rejects_an_oracle_that_the_declared_harness_cannot_produce","-q","--tb=short"],"exit_code":0,"result":"124 passed in 7.18s"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","py_compile","tools\\build_trace_document.py","skills\\tc-generator\\scripts\\export_test_cases_csv.py"],"exit_code":0}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tools\\build_trace_document.py","tools\\trace_check.py","skills\\tc-generator\\scripts\\export_test_cases_csv.py","tests\\test_build_trace_document.py","tests\\test_trace_check.py","tests\\test_tc_generator_csv_export.py","tests\\test_tc_to_autotest_portability.py","tests\\test_autotest_reviewer_portability.py"],"exit_code":0,"result":"All checks passed!"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-X","utf8","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","skills\\tc-generator"],"exit_code":0,"result":"Skill is valid!"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-X","utf8","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","skills\\tc-reviewer"],"exit_code":0,"result":"Skill is valid!"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-X","utf8","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","skills\\tc-to-autotest"],"exit_code":0,"result":"Skill is valid!"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-X","utf8","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","skills\\autotest-reviewer"],"exit_code":0,"result":"Skill is valid!"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\contract_check.py","--root",".","--full"],"exit_code":0,"result":"passed; errors=[]"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_skill_packages.py","tests\\test_skill_test_evidence.py","tests\\test_tc_reviewer_campaign.py","-q","--tb=short"],"exit_code":0,"result":"239 passed in 25.44s"}
{"argv":["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q","--tb=short"],"exit_code":0,"result":"718 passed, 2 skipped in 90.81s"}
{"argv":["git","diff","--check"],"exit_code":0,"result":"no whitespace errors; line-ending warnings only"}
```

Полный пакетный прогон до bounded corrections завершился `709 passed, 2 skipped, 9 failed`; все девять failures были воспроизведены отдельно и после исправлений дали `9 passed`. Свежий финальный полный результат — `718 passed, 2 skipped`.

Ни один evaluator, output validator или semantic checker campaign repetition в этом checkpoint не запускался. Ни один production-проект, его конфигурация, manifest, lockfile или dependency не менялся.
