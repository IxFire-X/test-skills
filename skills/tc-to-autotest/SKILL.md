---
name: tc-to-autotest
description: Use when the selected effective canonical test-case document must be translated into one project-native V5 automation version with explicit operation and assertion coverage.
---

# Project-native automation generation

Use only the selected effective canonical document and its digest. Read the [automation contract](references/automation-output-contract.md), `tools.canonical_document`, `tools.automation_validation`, and `tools.execution_preflight`.

Emit a complete proposed generated file set; the controller alone materializes it in the
selected module's active test root after accepted static review. This role does not use an
attempt-local/isolated project copy and never authorizes execution.

## Procedure

1. Validate the selected document and discover the project only within authorized paths. Preserve project-native layout, framework, setup, and runtime-only secret handles. The generated framework must equal the selected `.skillsrc` module framework; a Java file path must be the class FQN under that module's selected native test root, never an attempt-local substitute. Не добавляй зависимости.
2. For direct canonical/provider-backed execution, run global provider/adapter preflight before a test process or symbol. An exact accepted generated-source chain is instead gated by its static binding review and then the project-native test process; do not fabricate its runtime bindings.
3. Emit `GENERATED` only when every automated operation and assertion has an atomic operation/assertion relation to a generated `(file_id, symbol_id)` pair. Preserve every canonical literal and binding exactly in source. Multiple required pairs for one target mean AND, not an alternative. `automation_revision=1` has null predecessor fields; the sole permitted `automation_revision=2` is a complete fresh artifact bound to the exact revision-1 automation digest and the exact `AUTO_FIX_APPLIED` review digest.
   Automation must not recover request values from `action`, `test_data`, Markdown, reviewer prose, helper defaults, or an old revision. If a request-affecting value has no selected canonical structured input, stop for rework.
4. Emit exactly one manual disposition for every manual step. Emit `BLOCKED` only for a canonical blocker with empty generated/relation/disposition arrays; a project-generation problem without a canonical blocker stops without fabricating a BLOCKED artifact.
5. Validate via `tools.automation_validation`. Each `generated_files` row carries exact UTF-8 `content` and its matching SHA-256 `content_digest`; the complete artifact has one deterministic digest for independent review. Never write project files or claim execution. Never create a third version, a partial/destructive correction, or any regeneration after runtime `FAIL`.

Each generated test must reach the real behavior named by its operation relation and
assert the corresponding observed value or state. Do not replace the subject under test
with its expected answer, assert a constant against itself, swallow an unexpected
exception, or add skip/xfail/disabled markers to hide an unimplemented scenario. Mock only
an external boundary when the selected canonical scenario and project conventions permit
it; never mock the behavior the test claims to cover. Use deterministic data, explicit
setup and teardown through existing fixtures, order-independent tests, and bounded waits
for observed conditions instead of arbitrary sleeps. Missing fixtures/setup are rework.
Keep generated paths and process assumptions valid on Linux and Windows; use the selected
project runtime and native framework APIs, never a hardcoded host Python path.

Markdown/CSV and their human prose are never automation inputs.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Also stop when discovery, preflight, runtime setup, or a required project change is unresolved.
