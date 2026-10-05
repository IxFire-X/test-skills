# Portable Testing Skills Pilot — contract erratum

Status: `ACCEPTED`

`contracts/pipeline.json` remains the sole machine truth. This erratum replaces only
the following historical statements in the frozen pilot contract; every other frozen
boundary remains unchanged.

1. `cases-only-v1` is always draft and artifact-only:
   `verification = NOT_APPLICABLE`, `accepted = false`, and exit code `1`.
2. A fresh, valid, process-bound zero-test JUnit report with no `testcase` may produce
   authoritative `FAIL` with reason `NO_TESTS_COLLECTED` without per-symbol evidence.
   A negative process exit, missing or malformed report, unbound report, timeout, or OS
   error remains `UNKNOWN`.
3. `TERMINAL_RETRY_OBSERVED` is a narrow post-terminal observation event emitted only
   after scenario-observation readback. It does not change the terminal result.
4. `FAIL` or `NOT_RUNNABLE` disposition requires an existing execution receipt that was
   published and read back before cleanup.
5. Context normalization must be bound to the current attempt's authorized context
   selections before model invocation and on artifact acceptance. For standard OpenSpec
   sources, reconcile requirement/scenario identities and source provenance against the
   selected baseline plus one selected delta, applying ADDED/MODIFIED/REMOVED/RENAMED.
   This mechanical check does not establish semantic completeness or authorize scope
   expansion. Missing requirements remain visible gaps; no complete-coverage claim is
   permitted while a material requirement gap is unresolved.
6. The closed Maven adapter set also includes `maven:selected-symbols-v1`: `executable`
   resolves system Maven to an absolute launcher path, whose bytes and path are bound
   before execution. It uses the same exact selectors, `test` lifecycle and Surefire
   evidence as the wrapper adapter. A launcher digest does not identify all Maven/JDK
   libraries. Existing explicit configuration remains authoritative.
7. The existing frozen baseline receipt is profile-specific. `cases-only-v1` binds
   requirements, inventory, module and authoritative configuration without requiring
   runtime, adapter, build profile or executable test root. `local-pilot-v1` still
   requires all execution facts. `EXECUTION_BASELINE_FROZEN` keeps its place in the
   journal and means that this profile-applicable receipt was frozen; it never grants
   cases-only execution authorization. No fake adapter or runtime is permitted.
8. Generator requests bind ordered marker artifact, current context receipt, plan and
   header digests; publication/readback checks the fragment against these commitments
   and the stored context. Plan/header semantics remain an assembly responsibility.
   Canonical reviewer requests bind the existing boundary's candidate/package digests
   and reviewer invocation; the preceding request, boundary and ledger must belong to
   the same attempt. These checks reuse the existing events and artifact readers.
