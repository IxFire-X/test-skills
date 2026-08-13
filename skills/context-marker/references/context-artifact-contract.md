# V3 context artifact contract

`schemas/context-marker-output.schema.json` and `tools.canonical_document` are executable truth. Emit `schema_version: "3.0.0"`, `stage: "context-marker"`, the two closed artifact branches, and `warnings`.

Each requirement has a stable `requirement_id`, physical `display_order`, supported `text`, and ordered `provenance`. Derive ordering and IDs only from authorized evidence; source-local identifiers remain provenance. Preserve safe observable facts (status, response field, event, state), but never a credential or raw secret.

For reproducible IDs, normalize provenance references and text only for sorting: apply Unicode NFC; trim outer whitespace; convert CRLF/CR to LF; then collapse every remaining whitespace run to one ASCII space. For references also replace `\` with `/`. Keep each reference as `(normalized_reference, original_reference)` and sort by that pair. Sort requirements by their ordered normalized-reference tuple, original-reference tuple, normalized text, then original text, each in code-point order. Assign physical `display_order` from that order: the first ordered distinct requirement gets `REQ-0001`, the second gets `REQ-0002`, continuing zero-padded four digits in canonical `REQ-####` form. If a source has separate identifier and text references, keep that identifier/text provenance pair in its physical order.

Use `source_code_and_diff.sources` for safe source observations in the schema's inline-provenance form. A claim without sufficient evidence is a warning. Validate the envelope; downstream stages consume its V3 requirements, not a prose reconstruction.
