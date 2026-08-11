# PocketBase real chain 03

Status: pending.

This is a fresh functional recovery chain against PocketBase v0.38.2 at commit
`3616b9d66769dcda95841c52b00c166246323333`.

Route: `context-marker` → `tc-generator` → `tc-reviewer` → `tc-to-autotest`
→ `autotest-reviewer` → narrow Go execution gate.

The attempt stops at the first protocol, schema, semantic, review, or execution
failure. A failed stage is preserved and is not repaired or retried in this chain.
