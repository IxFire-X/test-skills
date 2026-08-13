---
name: tc-to-autotest
description: Use when the selected effective V3 canonical test-case document must be translated into project-native automation artifacts with explicit operation and assertion coverage.
---

# Project-native automation generation

Use only the selected effective canonical document and its digest. Read the [automation contract](references/automation-output-contract.md), `tools.canonical_document`, `tools.automation_validation`, and `tools.execution_preflight`.

## Procedure

1. Validate the selected document and discover the project only within authorized paths. Preserve project-native layout, framework, setup, and runtime-only secret handles. Не добавляй зависимости.
2. Run global provider/adapter preflight before any generated test process or symbol. If no confirmed project-native route exists, stop rather than choosing a language or architecture.
3. Emit `GENERATED` only when every automated operation and assertion has an atomic operation/assertion relation to a generated `(file_id, symbol_id)` pair. Multiple required pairs for one target mean AND, not an alternative.
4. Emit exactly one manual disposition for every manual step. Emit `BLOCKED` only for a canonical blocker with empty generated/relation/disposition arrays; a project-generation problem without a canonical blocker stops without fabricating a BLOCKED artifact.
5. Validate via `tools.automation_validation`. Source files are declared by digest and locator; never claim execution.

Markdown/CSV and their human prose are never automation inputs.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Also stop when discovery, preflight, runtime setup, or a required project change is unresolved.
