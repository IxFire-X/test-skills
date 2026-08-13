# Test-classifier reviewer contract

Read `schemas/test-classifier-reviewer-output.schema.json` as the executable carrier contract. The review binds the exact `technical_test_inventory_sha256` and `classification_sha256` to every inventory pair in physical order.

`reviewed_symbol_pairs` lists each `(file_id, symbol_id)` exactly once. The schema-defined accepted verdict means the unchanged complete classification is supported. Its rework verdict carries concrete findings with a path and, when applicable, the affected pair.

The reviewer never auto-fix rows, never creates a classification, and never creates cases. It reports defects; the classifier owns any later replacement artifact. The reviewer does not originate requirements or infer evidence beyond the supplied authorized sources.
