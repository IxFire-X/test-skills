# 5.0.0 context artifact contract

`schemas/context-marker-output.schema.json` and `tools.canonical_document` are executable truth. Emit `schema_version: "5.0.0"`, `stage: "context-marker"`, the two closed artifact branches, and `warnings`.

The controller supplies the immutable authorized inventory/context identities from an
existing attempt. Context marking runs once and produces the normalized source-requirement
set; deterministic candidate batches are a later controller concern. The model neither
widens those inputs nor writes project/run state.

Each source requirement has a stable `source_requirement_id`, physical `display_order`, supported `text`, exact source `digest`, and ordered `provenance`. Derive ordering and IDs only from authorized evidence. Source requirements are not canonical requirements: the controller assigns batch ownership and namespaces; the generator later emits `CREQ-*` conditions and explicit source-to-canonical mappings within its assigned namespace. Preserve safe observable facts (status, response field, event, state), but never a credential or raw secret.

For reproducible IDs, normalize provenance references and text only for sorting: apply Unicode NFC; trim outer whitespace; convert CRLF/CR to LF; then collapse every remaining whitespace run to one ASCII space. For references also replace `\` with `/`. Keep each reference as `(normalized_reference, original_reference)` and sort by that pair. Sort requirements by their ordered normalized-reference tuple, original-reference tuple, normalized text, then original text, each in code-point order. Assign physical `display_order` from that order: the first ordered distinct requirement gets `SREQ-0001`, the second gets `SREQ-0002`, continuing zero-padded four digits. If a source has separate identifier and text references, keep that identifier/text provenance pair in its physical order.

Use `source_code_and_diff.sources` for safe source observations in the schema's inline-provenance form. A claim without sufficient evidence is a warning. Validate the envelope; downstream stages consume its 5.0.0 source requirements, not a prose reconstruction.

Reconcile the complete original authorized text before normalization is accepted.
Requirement labels and Markdown headings are delimiters, not substitutes for their
multiline bodies; unlabelled prose, repeated headings and constraints must not disappear.
Keep literal whitespace, line breaks and quoted values in requirement text; use
whitespace normalization only for ordering, never to rewrite expected data.
Copy opaque technical tokens such as commit IDs, identifiers and exact byte literals
from the authorized source bytes with the available deterministic file/JSON tools;
do not retype or reconstruct them. Before publishing, compare those copied values
with their source spans. A transcription error is not an ambiguity in the user's
requirement and must not be converted into a manual gap.
Explicit requirements define intended behavior. Keep conflicting implementation/runtime
observations as source-bound warnings, never as replacements for that intended behavior.
A known implementation defect with a clear oracle remains testable; the warning does
not itself require manual/blocker treatment. Identify the actual missing oracle,
input, access or setup when a warning prevents a reproducible test.

The caller may supply a detailed specification or a short feature request with
authorized code. Preserve the actual request in both cases; a manually prepared
coverage checklist is not a prerequisite. For a short request, collect the scoped
entry points, reachable branches, validation rules and state changes as source-bound
observations for downstream test design. Distinguish observed implementation behavior
from explicitly required behavior; do not invent business policy or silently treat
the implementation as proof of correctness. If the intended scope or expected outcome
cannot be established, retain the exact uncertainty for controller clarification.
Downstream generation decomposes this evidence into canonical coverage conditions,
and review independently reconciles those conditions with the original request.

For standard OpenSpec, the authorized `openspec/specs/<capability>/spec.md` documents
are baseline; `openspec/changes/<selected-change>/specs/<capability>/spec.md` documents
are one explicitly selected live delta. Compose the final state: RENAMED changes identity
and keeps baseline behavior, REMOVED deletes it, MODIFIED replaces the complete block,
ADDED introduces a block. A rename followed by MODIFIED uses the TO name. Reject an
unmatched operation or ambiguous duplicate; do not silently match similar names or apply
archive twice. Retain every scenario and condition of each surviving block.

`tools.build_context` retains source IDs and adds inline identity provenance, for example
`openspec/specs/catalog/spec.md:8 — capability=catalog; ### Requirement: Search; #### Scenario: Found`.
Keep those exact requirement/scenario references, `path — sha256:...`, and any RENAMED
references in normalized rows. The identity union across split rows must equal the final
selected source set, with matching source digests; IDs are never replaced by titles.
`openspec_diagnostics` checks the actual normalized envelope at controller publication
and readback, reporting missing/extra identities and origin mismatches with source
locations. Archived documents are historical context and are not reapplied. This is a composition check;
independent review still checks all semantic conditions in the bodies.

The marker request binds exactly `[baseline.requirements.digest, baseline.inventory_digest,
*context_receipt_digests]`, with at least one current authorized context receipt from the
same attempt/inventory. Publication and readback verify the bound OpenSpec files and
normalized identities. No additional source discovery or whole-project claim follows
from those receipts: full-spec scope needs all agreed specifications authorized; change
scope needs the selected delta and the justified related regression inputs.

Keep gaps in existing `warnings`, with four fields: source requirement/scenario and link;
what is missing; which checks it blocks; the concrete question requiring resolution.
Requirement-without-scenario warnings preserve the requirement. Unsupported schema,
unmatched names or missing baseline require clarification; they never authorize invented
requirements. Substantive unresolved gaps block full-coverage claims, while independently
confirmed behavior may proceed under the existing protocol.
