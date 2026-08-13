---
name: context-marker
description: Use when authorized requirements and product observations must become a provenance-preserving V4 managed behavior context for the test pipeline.
---

# Context marking

Produce a V4 `context-marker` envelope. Read the executable [context contract](references/context-artifact-contract.md) and `schemas/context-marker-output.schema.json`; `tools.canonical_document` owns shared canonical definitions.

## Procedure

1. Read only authorized behavior sources. Не сканируй посторонние файлы. A technical test can supplement an observation but cannot originate a requirement.
2. Emit unchanged canonical requirements in `artifacts.managed_behavior_context.requirements`, exact `authorized_behavior_sources_sha256`, closed `product_sources`, and parallel ordered `requirement_sources` links. Preserve every supported fact without copying secrets.
3. Every product source must copy its authorized ID, path, and digest exactly; every requirement has nonempty ordered source IDs. Keep an unsupported claim out of the artifact rather than inventing behavior.
4. Validate the V4 envelope with `tools/validate_artifact.py` and validate its exact source graph and current bytes with `tools/test_classification.py validate-context` before return.

## Stop conditions

Stop instead of guessing on invalid or V2.1 input, required invention, an unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. A missing observable result is a data-gap warning, not permission to invent a result.
