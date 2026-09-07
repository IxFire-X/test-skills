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
