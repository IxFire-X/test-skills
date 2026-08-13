# Как работает полный тестовый пайплайн Pipeline 4.0

## Главная идея

Это управляемая цепочка LLM skills и deterministic Python tools, а не одна CLI
команда. `contracts/pipeline.json` — canonical registry этапов, маршрутов и
возможностей; `CONTRACTS.md` и `PIPELINE.md` генерируются из него.

```text
allowed requirements and read-only project context
  -> source-inventory: technical test inventory + authorized behavior sources
  -> context-marker: managed behavior context
  -> test-classifier -> test-classifier-reviewer: persisted technical evidence sidecar
  -> tc-generator: candidate bare canonical JSON from managed behavior context only
  -> publish immutable JSON/Markdown/CSV bundle
  -> tc-reviewer and effective revision selection
  -> tc-to-autotest -> autotest-reviewer
  -> optional execution -> trace build/check -> finalization
```

Внешний контроллер читает нужный `SKILL.md`, reference, schema, вход текущего
этапа и путь результата, затем запускает validator и проверяет exit code. Независимый
reviewer должен работать в fresh context и не видеть hidden reasoning генератора.

`source-inventory` механически создаёт snapshots test files/symbols и authorized
behavior sources. `context-marker` выделяет managed behavior context, а classifier
и independent reviewer классифицируют полный inventory. Accepted
`effective_technical_evidence` остаётся persisted sidecar attempt: в Phase 1 оно
никогда не передаётся в V3 automation, trace или finalization. Structural guard:
`tc-generator` получает только `managed_behavior_context`, без raw test source,
inventory или classification.

## Автоматическое discovery и границы проекта

Оркестратору нужны корень skill pack, корень проекта, ограниченная feature scope,
явно разрешённые requirements/code sources, новый artifact directory и isolated
workspace для новых автотестов. `.skillsrc` только описывает project language,
framework, build tool и paths; он не даёт права менять проект. Его форма описана в
`schemas/skillsrc.schema.json`.

При отсутствии `.skillsrc` оркестратор сначала read-only исследует проект и
автоматически создаёт v3 manifest через `tools/init_skillsrc.py`. Если scanner
обнаружил критическую неоднозначность, receipt содержит один вопрос; ответ с
выбранным option ID сохраняется в новом immutable attempt. Старый receipt не
перезаписывается. В монорепозитории после инициализации обязательно выбирается
exact module ID, и только его confined root, язык и paths идут в feature context и
runner.

Отдельный read-only осмотр остаётся доступен:

```bash
python <skill-pack>/tools/scan_project.py --project <project> --target <feature>
```

Артефакты каждого запуска размещаются в
`<project>/docs/to_do/test-pipeline/<feature>/<attempt>/`. Новый attempt получает
новый каталог. Рабочий код, existing tests, configuration, dependencies и lock files
не изменяются; generated tests живут только в изолированном рабочем пространстве.

Не передавайте секреты: bearer/session/API tokens, cookies, passwords и private keys.
Canonical document, projections, stdout, trace и evidence содержат только opaque
handles и safe labels, а не secret values.

## Канонический документ и проекции

`bare canonical JSON` — единственный semantic source. Он содержит stable
`document_id`/revision, requirements с provenance, cases и arbitrary sequential
steps. Step хранит human `Action`, expectations с human `Expected Result`, technical
operation/bindings/outputs/assertions либо явный manual reason/blocker. Более поздний
step может сослаться только на output раннего шага; schema и semantic validator
отклоняют отсутствующие или придуманные technical details.

Human Markdown — производная форма, а не второй model: title, goal, preconditions и
arbitrary numbered steps. Каждый step показывается в exact table:

```text
| № | Действие | Ожидаемый результат |
```

Нет отдельной Test Data column. Action и expectation text являются human fields
canonical document, а не реконструкцией из operation/assertion.

Zephyr CSV — другая immutable projection. Fixed profile
`zephyr-scale-step-row-24-v1` has exactly 24 headers, one row per canonical step,
case metadata only on the first case row, formula-safe cells and human-readable
values. Operation, bindings и assertions не копируются в неё, поэтому CSV cannot
reconstruct JSON. Workbook/export structure was observed, but a real tenant import round trip remains unverified;
universal Zephyr/tenant import compatibility не
заявляется.

## Revision lifecycle

После schema+semantic validation publisher строит три immutable files:

```text
<document-id>.r<revision>.json
<document-id>.r<revision>.md
<document-id>.r<revision>.zephyr-scale.csv
```

Candidate публикуется before review. A valid complete `AUTO_FIX_APPLIED` successor
сохраняет существующие identities, получает следующую revision и также публикуется.
`ПРИНЯТО` выбирает candidate, а `ТРЕБУЕТ ДОРАБОТКИ` не выбирает revision. Candidate и
successor остаются audit evidence; exactly one `effective revision` and receipt
проходят downstream. Markdown/CSV никогда не становятся downstream input.

## Automation, execution и trace

`tc-to-autotest` использует selected canonical document и project-native evidence.
Он не invents adapters, HTTP values, authentication или fixtures. Если technical
context недостаточен, он возвращает exact blocker instead of fictional code or
manual-only conversion.

Automation output хранит files, symbols с pair-addressed locators, atomic operation
and assertion relations и manual dispositions. Runtime identity is
`(file_id, symbol_id)`; several required pairs for one target have AND semantics.
Global provider/adapter preflight runs before every target process/symbol. Static
autotest review covers each required pair and не объявляет execution.

Runner запускается только для branch с required symbols. Manual-only/blocked
zero-pair branch skips runner only. Все terminal branches, включая FAIL и
NOT_RUNNABLE, строят и валидируют trace:

```text
requirement -> case -> step -> expectation -> assertion -> file -> symbol -> current-run evidence
```

`trace_check --require-execution` запускается для каждой terminal branch. Он
проверяет exact branch obligation; null execution допустим только для valid
BLOCKED/manual no-run semantics. При no-run omitted только `--run-result` во время
trace build.

Final statuses exactly: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`,
`BLOCKED`, `FAIL`, `NOT_RUNNABLE`. `PASS_WITH_MANUAL_REMAINDER` и `MANUAL_ONLY` не
выдают ручную работу за проверенный PASS; FAIL/NOT_RUNNABLE не отбрасываются до trace.

## Команды и финализация

Рабочие module-form commands, включая publish, selection, runner и trace, приведены
в [USAGE.md](USAGE.md). Runner получает exact selected module context:

```bash
python <root>/tools/run_tests.py --project <project> --skillsrc <project>/.skillsrc --module <module-id> --canonical-document <effective-document.json> --automation-artifact <tc-to-autotest-output.json>
```

Module execution требуется для publisher, потому что direct script form не разрешает
его package imports из repository root. Finalization remains
a Python seam: `orchestrate_revision(...)`,
`validate_trace_document(trace, document, automation, run_result=None)` и
`finalize_orchestration(...)`. Нет finalization CLI и нет trace-check
orchestrator-artifact flag.

## Что не должно происходить

- Не подгонять product под test case и не менять existing tests/dependencies.
- Не превращать неподтверждённое техническое предположение в action, assertion или code.
- Не сохранять secrets либо не считать слова LLM доказательством execution.
- Не редактировать projections independently и не извлекать из них automation.
- Не объявлять PASS без exact current-run evidence для required pairs.

## V2.1 is unsupported

V2.1 artifacts explicitly reject with a breaking-change diagnostic. There is no
automatic semantic migration, no mixed-version pipeline, and no actionable legacy
lifecycle examples in this guide.
