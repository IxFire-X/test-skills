---
name: tc-to-autotest
description: Use when the selected effective canonical test-case document must be translated into one project-native V5 automation version with explicit operation and assertion coverage.
---

# Project-native automation generation

Use only the selected effective canonical document and its digest. Read the [automation contract](references/automation-output-contract.md), output schema and required authorized project inputs. Execute canonical, automation and preflight validators; read their source only as needed to diagnose a failure.

Emit a complete proposed generated file set; the controller alone materializes it in the
selected module's active test root after accepted static review. This role does not use an
attempt-local/isolated project copy and never authorizes execution.

## Procedure

1. Validate the selected document and discover the project only within authorized paths. Preserve project-native layout, framework, setup, and runtime-only secret handles. The generated framework must equal the selected `.skillsrc` module framework; a Java file path must be the class FQN under that module's selected native test root, never an attempt-local substitute. Не добавляй зависимости.
2. For direct canonical/provider-backed execution, run global provider/adapter preflight before a test process or symbol. An exact accepted generated-source chain is instead gated by its static binding review and then the project-native test process; do not fabricate its runtime bindings.
3. Emit `GENERATED` only when every automated operation and assertion has an atomic operation/assertion relation to a generated `(file_id, symbol_id)` pair. Preserve every canonical literal and binding exactly in source. Multiple required pairs for one target mean AND, not an alternative. `automation_revision=1` has null predecessor fields; the sole permitted `automation_revision=2` is a complete fresh artifact bound to the exact revision-1 automation digest and the exact `AUTO_FIX_APPLIED` review digest.
   Automation must not recover request values from `action`, `test_data`, Markdown, reviewer prose, helper defaults, or an old revision. If a request-affecting value has no selected canonical structured input, stop for rework.
4. Emit exactly one manual disposition for every manual step. A canonical blocker blocks only its own case: generate automation for every other case (`GENERATED`), add one `manual_dispositions` row for every step of each blocked case, and fill `diagnostics` (required whenever a blocked case is left out). Emit `BLOCKED` only when no case can be automated, with nonempty diagnostics and empty generated/relation/disposition arrays; a project-generation problem without a canonical blocker stops without fabricating a BLOCKED artifact. Blockers keep the run unaccepted (`accepted=false`).
5. Validate via `tools.automation_validation`. Each `generated_files` row carries exact UTF-8 `content` and its matching SHA-256 `content_digest`; the complete artifact has one deterministic digest for independent review. Never write project files or claim execution. Never create a third version, a partial/destructive correction, or any regeneration after runtime `FAIL`.
   Before the main run the controller compiles/collects the materialized tests (pytest `--collect-only`, Maven `test-compile`, Gradle `testClasses`). A test that does not compile, import or collect ends the attempt as `NOT_RUNNABLE/GENERATED_TEST_INVALID`. Only then may the controller request one corrected revision in a child attempt with `retry_reason=GENERATED_TEST_INVALID`, unless revision 2 was already spent in static review; use the saved gate output to fix the test itself, never the expected behavior.

Generate functional application tests for the accepted cases. Use the declared application
boundary: prefer the external API when both routes are available; MockMvc with real
application components is also allowed unless this run explicitly requires the external
API. Reuse the project's client, authentication, configuration and setup/cleanup. An
isolated class or method test cannot substitute for an application case. One request is
enough when it exercises that case and observes its required outcome.

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
Missing URL, access, fixture or oracle is a concrete gap: identify its source, affected
checks and required clarification. Do not invent it or replace the case with a unit test.

Markdown/CSV and their human prose are never automation inputs.

## Update and repair modes (`suite-update-v1`)

A task with `mode: update` automates the updated and new cases of a living suite. The
brief names the target file (its current text), and for every case its canonical case and,
for an updated case, its current method (`locator`, `method_name`, `source`). Return
`methods` — one complete method per case of the task: the same `method_name` for an
updated case, a new unique name for a new case — plus `helpers` (new helper methods) and
`imports` (missing import lines) when needed. Every check keeps its `ASSERT-…` label and
the case's expected value; the controller rejects a method that drops one. The controller
keeps every other method and the file's shared code, splices the methods in by their
slices, reviews them statically and writes the file only if no person edited the methods.

A task with `mode: repair` follows a suite run where the test does not compile or its own
code fails while the case and its requirements did not change. The brief carries the case,
the current method, the error output and the target file. Return `methods` with the same
`method_name` and corrected code. A repair fixes the test's code, never the expected
behaviour: the controller rejects a method that loses any literal or `ASSERT-…` label of
the old one. There is one repair try; a method that still fails goes to quarantine.

## Stop conditions

Stop on invalid or V2.1 input, required invention, unavailable validator or tool, schema or semantic failure, secret exposure risk, or work outside the authorized scope. Also stop when discovery, preflight, runtime setup, or a required project change is unresolved.
