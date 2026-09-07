---
name: context-marker
description: Use when authorized requirements, code observations, or change notes must become a provenance-preserving 5.0.0 source-requirement context artifact for the test pipeline.
---

# Context marking

Produce a 5.0.0 `context-marker` envelope. Read the executable [context contract](references/context-artifact-contract.md) and `schemas/context-marker-output.schema.json`; `tools.canonical_document` owns shared canonical definitions.

Run once inside an existing nonterminal attempt after the controller has published and
read back the exact authorized inventory/context receipts. Produce the complete normalized
source-requirement set from those inputs; the controller creates batches afterwards. Do
not rescan the project, create a run/attempt, or widen feature/module scope.

## Procedure

1. Read only authorized inputs. Не сканируй посторонние файлы. Classify explicit behavior and acceptance criteria as requirements; retain route, symbol, and module observations as provenance, not new behavior.
2. Emit `artifacts.analytics_documentation.requirements` with a deterministic `source_requirement_id`, `display_order`, `text`, exact source digest, and provenance. A source ID is never a canonical requirement ID: the controller later assigns canonical IDs and batch ownership.
3. Explicit authorized requirements define expected behavior; code and runtime reports describe the current implementation. Preserve both when they disagree, with a source-bound warning. Never replace a required outcome with the observed defect. When requirements leave an outcome undefined, trace its end-to-end control flow through reachable throws, exception translation, and HTTP mapping; label the observation and preserve an unresolved oracle as a warning.
4. Emit `source_code_and_diff.sources` as safe inline provenance observations. Keep an unsupported claim in `warnings`, never as a requirement.
5. Validate the 5.0.0 envelope with `tools/validate_artifact.py` and its schema before return.

Before returning, reconcile every behavior and acceptance criterion in the original
authorized request with the normalized source set. Preserve each condition, role, boundary,
failure outcome, and state change, even when several share one source paragraph. Do not
drop an unsupported or ambiguous requirement: retain it and its precise gap warning.
Schema validity and requirement counts alone do not prove this semantic completeness.

## Stop conditions

Stop instead of guessing on pre-5.0 input, required invention, an unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. A missing observable result is a data-gap warning, not permission to invent a result.
