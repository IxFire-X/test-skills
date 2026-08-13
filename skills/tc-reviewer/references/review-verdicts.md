# V3 review verdict contract

`tools.revision_selection` validates candidate digest, full successor lineage, and identity preservation; `tools.canonical_document` validates canonical semantics.

| Verdict | Required output |
|---|---|
| `ПРИНЯТО` | candidate is selected; no successor |
| `AUTO_FIX_APPLIED` | one complete successor document, incremented lineage, preserved identity graph |
| `ТРЕБУЕТ ДОРАБОТКИ` | blocking findings; no successor |

`reviewed_case_ids` lists all candidate cases exactly once in physical order. A safe correction has a single evidence-backed mechanical meaning. It retains every unrelated entity and all IDs, including nested capability, step, output, expectation, and assertion identities. Removing a case, changing behavior, filling a missing technical fact, or returning a fragment is rework.

Review all human Action/Expected Result pairs against their machine operation/assertion ownership, prior-step data flow, manual branch, and blocker path. The report cites the exact bare candidate digest; a successor is validated as a whole, never merged with its candidate.
