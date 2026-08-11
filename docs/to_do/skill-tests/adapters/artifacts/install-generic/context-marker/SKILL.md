---
name: context-marker
description: Use when converting supplied requirements and source changes into a provenance-preserving context artifact for the testing pipeline.
---

# Context Marker

Produce one JSON artifact named `context-marker-output.json`. Read only caller-allowlisted inputs; never scan unrelated files. The machine authority is the repository [schema](../../schemas/context-marker-output.schema.json); read the local [contract](references/context-artifact-contract.md) for concise usage rules.

## Workflow

1. Classify only claims supported by caller-allowlisted evidence before assigning canonical IDs: explicit behavior or acceptance assertions are requirements; standalone contextual metadata are source observations.
2. Follow the local contract's deterministic recipe for requirement IDs, exact requirement locators, inline provenance for string fields, and nonempty branches.
3. Write the schema's canonical envelope with `schema_version` `2.1.0`, `stage` `context-marker`, and the two `artifacts` branches. Do not add wrapper or alternate keys.
4. Put any unsupported or requester-asserted claim absent from the allowlisted evidence only in a warning/gap, never as a requirement or source fact.
5. Report a blocker rather than fabricate content needed for the envelope.
6. When the validator, schema, and output path are reachable, validate once with `tools/validate_artifact.py`; otherwise report the validation failure or unavailability honestly.

## Observable requirement gate

Before assigning canonical IDs, apply this gate to every behavioral or acceptance
requirement. When allowlisted evidence explicitly supplies an action/condition plus
externally observable result (status, response field, error code, state/event),
retain enough of that supported observable in the requirement text to make it
deterministic and testable; do not reduce the requirement to route or method metadata.

Evidence may span multiple allowlisted sources: join only exact supported facts and
include provenance for every contributing fact in deterministic order. Do not copy
secret values or credentials while preserving non-secret observables such as HTTP
status, response-field presence or absence, and event/state. If no observable exists
in allowlisted evidence, preserve the partial behavior but emit a warning/gap rather
than inventing an oracle.

## Проверка перед записью

До назначения canonical ID проверьте eligibility и классификацию: самостоятельные metadata контекста сохраняются как source observation, а требованием становится только явное поведенческое или acceptance-утверждение; ни один поддержанный факт нельзя молча удалить. Затем для каждой структурированной claim/fact-записи с отдельными полями identity и claim text проверьте два точных locator в `requirements[].provenance` в порядке identity, затем claim text; identity не является `REQ-*` ID. Каждый locator содержит только JSON Pointer, `path#anchor` или `path:line`: без цитаты, описания, поясняющей метки, разделителя ` — ` или суффикса. Формат `<locator> — <faithful observation or gap>` допустим только для строковых `sources` и `warnings`.

## Boundaries

- Do not output XML, batch results, wrappers, empty-array skeletons, or alternate envelope keys.
- Never drop, invent, or reclassify supported or unsupported claims solely to satisfy the schema.
