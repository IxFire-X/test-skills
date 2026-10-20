---
name: test-design
description: Use when the source requirements of a run must become a test design — parameters, equivalence partitions, boundary values and decision, state and error rules — before any test case is written.
---

# Test design

Produce the `artifacts` and `warnings` of a `test-design` artifact valid against
`schemas/test-design.schema.json`; the controller adds `schema_version` and `stage`, and
`tools.test_design.validate_design` checks every reference and rule below.

The controller invokes this role once per attempt, after the context marker and before
the case generator. Its only input is the brief: the source requirements (`SREQ-*`) with
their text and the ID formats. Do not read project code, invent requirements, write test
cases, choose priorities or widen scope. The generator later covers every element of the
design with cases, and code counts that coverage.

## Procedure

1. Read every requirement. Find the inputs and conditions it depends on (a field value,
   a role, an object state, a quantity, a format) — each is a **parameter** `P-NNN` with
   the requirements it comes from and its `kind`: `ordered` (numbers, dates, lengths,
   counts — anything with a "less/more"), `enum`, `boolean`, `text` (free text without a
   rule on its size), `structure` (an object or a list as a whole).
2. Split each parameter into **equivalence partitions** `EQ-NNN`: classes of values the
   requirement treats the same way. Give each its `condition` and `valid` (`true` — the
   requirement accepts it, `false` — it must be rejected or handled as an error). Every
   parameter needs a valid and an invalid partition; when an invalid one truly cannot
   exist, say why in `single_class_reason`.
3. For an `ordered` parameter give **boundary values** `BV-NNN` of its partitions: `min`
   and `max` of a range, `just_inside` / `just_outside` around a limit. A length limit of a
   text is its own `ordered` parameter (the length), not a boundary of the text.
4. Write **rules** `DR-NNN` for behavior that depends on conditions: `decision` (a
   combination of conditions → an outcome), `state` (a transition from a state by an
   event), `error` (an invalid input or forbidden action → the error the requirement
   names). `conditions` reference partitions or boundaries (`{"ref": "EQ-003"}`) or state
   a condition in words (`{"text": "..."}`); `expected` is the observable outcome the
   requirement gives. Do not invent an outcome a requirement does not state: name the gap
   in `warnings` instead.
5. A requirement that has neither a parameter nor a rule — a non-functional statement, a
   definition, context without behavior — goes to `not_designed` with the reason. Every
   source requirement is designed or declared not designed, never silently skipped; a
   requirement in `not_designed` is named by no design element.

IDs are unique across the whole artifact, use the formats of the brief and are never
reused for another element. Keep texts short and concrete; Russian, like the requirements.

## Stop conditions

Stop instead of guessing when the brief has no requirements, a requirement is unreadable,
or a design would need behavior no requirement states. A missing outcome is a warning, not
permission to invent one.
