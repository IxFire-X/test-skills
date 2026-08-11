# PocketBase real chain 04

Status: attempt-03 reached all five skill gates; execution is environment-blocked because Go 1.25 tooling is unavailable locally.

Fresh full-chain working container after `chain-03` stopped before any skill stage
because of a controller command quoting error. This chain uses simple commands
without PowerShell pipelines or embedded regular-expression quoting.

Route: `context-marker` → `tc-generator` → `tc-reviewer` → `tc-to-autotest`
→ `autotest-reviewer` → narrow Go execution gate.

At the first stage protocol, schema, semantic, review, or execution failure,
preserve the raw attempt under a distinct `attempt-N` identity. Diagnose and fix
the bounded root cause, then continue with a new attempt inside this same chain.
Never overwrite or silently repair a failed attempt.

Attempt summary:

- `attempt-01`: tc-reviewer correctly blocked a nondeterministic password-auth oracle;
- `attempt-02`: upstream stages and generated Go test succeeded, but the old autotest-reviewer produced a Java-only false negative;
- `attempt-03`: upstream artifacts are SHA-bound, the portable autotest-reviewer returned `ПРИНЯТО`, and the execution gate truthfully stopped before formatting/compilation because no local `go.exe`/`gofmt.exe` or checked alternative runtime exists.
