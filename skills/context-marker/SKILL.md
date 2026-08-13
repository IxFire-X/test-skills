---
name: context-marker
description: Use when authorized requirements, code observations, or change notes must become a provenance-preserving V3 context artifact for the test pipeline.
---

# Context marking

Produce a V3 `context-marker` envelope. Read the executable [context contract](references/context-artifact-contract.md) and `schemas/context-marker-output.schema.json`; `tools.canonical_document` owns shared canonical definitions.

## Procedure

1. Read only authorized inputs. Не сканируй посторонние файлы. Classify explicit behavior and acceptance criteria as requirements; retain route, symbol, and module observations as provenance, not new behavior.
2. Emit `artifacts.analytics_documentation.requirements` with `requirement_id`, `display_order`, `text`, and provenance. Use deterministic source ordering and IDs; preserve every supported fact without copying secrets.
3. Emit `source_code_and_diff.sources` as safe inline provenance observations. Keep an unsupported claim in `warnings`, never as a requirement.
4. Validate the V3 envelope with `tools/validate_artifact.py` and its schema before return.

## Stop conditions

Stop instead of guessing on invalid or V2.1 input, required invention, an unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. A missing observable result is a data-gap warning, not permission to invent a result.
