# Portable testing skills roadmap

Актуально на 2026-08-11. Это operational roadmap; подробная история исходного аудита сохранена в [docs/audit-report-2026-07-31.md](docs/audit-report-2026-07-31.md).

## Цель

Host-neutral drop-in пакет из шести skills, который:

- превращает source-backed требования в полные ручные test cases;
- всегда создаёт JSON и lossless CSV для Jira Zephyr-ориентированного переноса;
- независимо review-ит manual и automated tests;
- генерирует project-native Java/Python tests без изменения проекта;
- принимает результат только после runner evidence и полной trace;
- работает через прямые portable paths без обязательного plugin/adapter.

## Завершено

### Core runtime

- contracts/pipeline.json — единственный registry routes, artifacts и skill paths.
- Draft 2020-12 schemas для stage outputs, runner, trace и orchestrator.
- validate_artifact.py, contract_check.py и generated contract docs.
- run_tests.py с честными PASS, FAIL и NOT_RUNNABLE.
- build_trace_document.py и trace_check.py с REQ → TC → FILE → METHOD → RUN topology.

### Portable skills

- context-marker сохраняет provenance и source observations без secrets.
- tc-generator создаёт traceable cases и обязательный lossless CSV.
- tc-reviewer проверяет полноту, лишние/неверные cases и полный harness/oracle.
- tc-to-autotest следует project-native setup, roles, permissions, fixtures и auth.
- autotest-reviewer независимо проверяет exact source semantics и coverage.
- orchestrate связывает schemas, reviewers, runner и trace и fail-closed останавливает цепочку.

### Adapters

- Generic Python installer: byte/timestamp preserving, dry-run, idempotent.
- Windows PowerShell installer: byte/timestamp preserving, WhatIf, idempotent.
- Прямое использование skills не требует host adapter.

### Практические цепочки

Проверены Click, Flask, Hono, Spring PetClinic REST, step5-java-demo, InvenTree и PocketBase. Подробные артефакты находятся в docs/to_do/real-chains/.

- Полные PASS не смешиваются с generated-slice PASS.
- Environment/toolchain blockers сохраняются как blockers.
- Production/config/dependencies проектов не менялись ради тестов.
- Найденные переносимые дефекты превращены в skill regressions.

## Текущий gate

Plan 2 Task 9: package/evidence acceptance audit.

Требуется:

1. шесть и только шесть canonical skill packages;
2. никаких package README, SKILL-LITE или templates;
3. все local Markdown links существуют;
4. нет obsolete artifact aliases в authority declarations;
5. все campaign scenario/metadata/scorecards schema-valid;
6. adapters повторно копируют финальные current bytes;
7. full pytest, Ruff, contract/render checks и quick validation проходят.

Formal tc-reviewer FINAL и orchestrate evaluator scorecards остаются отдельным evidence debt; это нельзя скрывать как complete. Дорогие fresh-model проверки выполняются один раз в final acceptance, а не после каждой wording-правки.

## Следом

### Plan 3 E2E acceptance

- одна Java и одна Python execution-required chain в изолированных workspaces;
- exact orchestrator cross-check;
- проверка установленной adapter-копии, а не только source package;
- понятный пошаговый human report рядом с JSON authorities.

### Пользовательская проверка

После package acceptance:

- запустить полную цепочку на проектах из AI SKILLS;
- отдельно повторить InvenTree и step5-java-demo;
- показать пользователю JSON, CSV, reviewer verdict, generated source, runner output и trace на каждом шаге;
- вносить изменения в skills только по воспроизводимым chain failures, не подстраивая проекты.

## Будущие улучшения

1. Отдельный Zephyr import adapter под выбранную Jira/Zephyr версию и field mapping.
2. SDD/OpenAPI validation: operation, request/response schema и spec/code drift.
3. Deterministic boundary generation из JSON Schema.
4. TypeScript/Go generation и execution только после project-native capability gate.
5. AsyncAPI/event-driven trace.
6. Optional UI для human approval; JSON receipts остаются authority.

## Неизменные правила

- Честность выше удобства: non-PASS не преобразуется в PASS.
- Project source не меняется ради generated tests.
- Secrets не попадают в artifacts.
- Failed attempts immutable.
- Один source of truth на каждый contract.
- Не заявлять универсальность для любой LLM до независимого финального acceptance.

Implementation plans: [master](docs/superpowers/plans/2026-08-06-portable-testing-skills-master.md), [Plan 1](docs/superpowers/plans/2026-08-06-portable-core-runtime.md), [Plan 2](docs/superpowers/plans/2026-08-06-portable-skills-adapters.md), [Plan 3](docs/superpowers/plans/2026-08-06-e2e-acceptance.md).
