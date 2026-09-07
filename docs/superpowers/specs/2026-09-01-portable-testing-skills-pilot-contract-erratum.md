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
