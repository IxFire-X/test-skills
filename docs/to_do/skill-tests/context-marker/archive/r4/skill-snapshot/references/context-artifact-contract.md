# Context artifact contract

The authoritative machine schema is [context-marker-output.schema.json](../../../schemas/context-marker-output.schema.json).

Use the schema for field names and validation; this reference owns the authoring recipe.

- Set `schema_version` to `2.1.0` and `stage` to `context-marker`; populate both required artifact branches with supported, nonempty content.
- For every supported requirement, retain faithful text and an exact locator in `provenance`: a JSON Pointer for JSON, or `path#anchor` / `path:line` for text. Каждый элемент `requirements[].provenance` равен только locator: без цитаты, описания, разделителя ` — ` и любого суффикса. Preserve source observations and warnings as strings with inline provenance: `<locator> — <faithful observation or gap>`; этот формат не применяется к `requirements[].provenance`.
- Assign canonical IDs deterministically. For sorting only, normalize each locator and faithful claim text by Unicode NFC normalization, trimming outer whitespace, replacing `\` with `/`, and collapsing each whitespace run to one ASCII space. Represent every locator as `(normalized_locator, raw_locator)`; ordinal-sort those pairs by normalized then raw locator, and emit provenance in that order. Sort requirements by the ordered normalized locator tuple, ordered raw locator tuple, normalized faithful text, then raw faithful text, all in ordinal code-point order. Assign `REQ-0001`, `REQ-0002`, and so on in that total order.
- If evidence supplies an identifier, retain that source identity in provenance but still map the requirement to the canonical `REQ-####` ID; do not blindly preserve an invalid or non-`REQ-*` identifier.
- Preserve supplied wording where practical. Any unsupported or requester-asserted claim absent from allowlisted evidence is a warning/gap only, never a requirement or source fact.
