# V3 automation-output contract

`schemas/tc-to-autotest-output.schema.json`, `tools.canonical_document`, and `tools.automation_validation` are executable truth. Input is the selected effective canonical JSON and its exact digest, never Markdown or Zephyr CSV.

For `GENERATED`, declare `source`, generated files with byte digests, generated symbols with the exact locator variant, implementation relations, manual dispositions, and no diagnostics. Runtime identity is `(file_id, symbol_id)`. An operation relation identifies case/step/pair; an assertion relation additionally identifies expectation/assertion/pair. Relations are atomic. Required pairs are complete and AND-combined.

For each manual step declare exactly one manual disposition. `BLOCKED` is permitted only for a canonical blocker and has nonempty diagnostics with empty files, symbols, relations, and dispositions. Do not report a project-discovery or dependency failure as canonical blocking.

Use project-native discovery and isolated output. Do not edit the application, existing tests, configuration, lock files, or dependencies. Keep secrets as runtime handles only. Run global provider/adapter preflight through `tools.execution_preflight` before test processes or symbols, then use language conventions only when they match the confirmed project.
