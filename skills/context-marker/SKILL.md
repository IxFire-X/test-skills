---
name: context-marker
description: Use when authorized requirements and product observations must become a complete-accounting V5 managed behavior context for the test pipeline.
---

# Context marking

Produce a V5 `context-marker` envelope plus its immutable receipt. Read the executable [context contract](references/context-artifact-contract.md) and `schemas/context-marker-output.schema.json`; `tools.canonical_document` owns shared canonical definitions.

## Procedure

1. Read only authorized behavior sources. Не сканируй посторонние файлы. A technical test can supplement an observation but cannot originate a requirement.
2. Run `context-plan`, process every planned batch exactly once, persist closed batch results, and run `context-receipt`. Emit unchanged canonical requirements in `artifacts.managed_behavior_context.requirements`, exact `authorized_behavior_sources_sha256`, closed `product_sources`, ordered `requirement_sources`, and the V5 `behavior_source_accounting` sibling bound to the receipt. Preserve every supported fact without copying secrets; reduction consumes structured fragments by domain and then globally, never raw source text. Apply the contract's three-gate framework-provenance, registration-sink, and bound-target rule before treating an HTTP/API path as an observable operation.
3. Every product source must copy its authorized ID, path, and digest exactly; every requirement has nonempty ordered source IDs. Keep an unsupported claim out of the artifact rather than inventing behavior.
4. Validate the V5 envelope with `tools/validate_artifact.py` and validate its exact source graph, receipt, and current bytes with `tools/test_classification.py validate-context --receipt <receipt>` before return. When the authorized inventory has `supplied_requirement` rows, repeat the exact controller-only `--supplied-input SOURCE_ID=PATH` arguments for `context-plan`, `context-receipt`, `validate-context`, `select`, and portfolio audit; otherwise omit them. Never persist those paths or place them in Pipeline carriers.

## Stop conditions

Stop instead of guessing on invalid or V2.1 input, required invention, an unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. A missing observable result is a data-gap warning, not permission to invent a result.
