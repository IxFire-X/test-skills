---
name: context-marker
description: Use when converting supplied requirements and source changes into a provenance-preserving context artifact for the testing pipeline.
---

# Context Marker

Produce one JSON artifact named `context-marker-output.json`. Read only caller-allowlisted inputs; never scan unrelated files. The machine authority is the repository [schema](../../schemas/context-marker-output.schema.json); read the local [contract](references/context-artifact-contract.md) for concise usage rules.

## Workflow

1. Classify only claims supported by caller-allowlisted evidence.
2. Follow the local contract's deterministic recipe for requirement IDs, locators, inline string provenance, and nonempty branches.
3. Write the schema's canonical envelope with `schema_version` `2.1.0`, `stage` `context-marker`, and the two `artifacts` branches. Do not add wrapper or alternate keys.
4. Put any unsupported or requester-asserted claim absent from the allowlisted evidence only in a warning/gap, never as a requirement or source fact.
5. Report a blocker rather than fabricate content needed for the envelope.
6. When the validator, schema, and output path are reachable, validate once with `tools/validate_artifact.py`; otherwise report the validation failure or unavailability honestly.

## Boundaries

- Do not output XML, batch results, wrappers, empty-array skeletons, or alternate envelope keys.
- Never drop, invent, or reclassify supported or unsupported claims solely to satisfy the schema.
