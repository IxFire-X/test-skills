# V3 static automation-review contract

Validate canonical JSON with `tools.canonical_document` and artifact relations with `tools.automation_validation`. Verify the automation source against the selected effective document's exact digest, then inspect every declared file's actual bytes and every generated locator.

Review atomic operation/assertion relation ownership, canonical physical order, and complete coverage. A required runtime pair is `(file_id, symbol_id)`; `reviewed_symbol_pairs` contains every distinct pair exactly once in generated-symbol physical order. Multiple pairs for a target are AND requirements. Verify exactly one manual disposition per manual step, canonical-blocker branches, no extra relation, and no missing assertion.

Findings are static evidence only. A semantic issue, stale digest, missing file, invalid locator, absent pair, secret, or unconfirmed setup is rework. `AUTO_FIX_APPLIED` returns to regeneration; never run an obsolete automation artifact.
