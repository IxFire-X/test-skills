# V5 static automation-review contract

Validate canonical JSON with `tools.canonical_document` and artifact relations with `tools.automation_validation`. Verify the automation source against the selected effective document's exact digest and its exact attempt-owned `effective_bundle_receipt_digest`, then inspect every declared file's inline UTF-8 `content` and every generated locator. Use the runner-compatibility check with `materialized=False`; no generated file is written or required on disk during this review. On-disk digest/confinement checks remain mandatory after controller materialization.

Review atomic operation/assertion relation ownership, canonical physical order, and complete coverage. `reviewed_files` contains every declared `(file_id, content_digest)` in generated-file physical order; `reviewed_symbol_pairs` contains every distinct required pair exactly once in generated-symbol physical order; `reviewed_relations_sha256` identifies the complete physical relation array. Multiple pairs for a target are AND requirements. Verify exactly one manual disposition per manual step, canonical-blocker branches, no extra relation, and no missing assertion.

Inspect each generated symbol for exact canonical input literals and input/output/assertion bindings. A literal or binding drift is rework even if declared relations, locators, and digests are internally consistent.

For shared helpers, follow calls from each declared test method to the actual operation
and assertion; a helper call or relation alone is not coverage. Apply the readability
guidance in the [automation contract](../../tc-to-autotest/references/automation-output-contract.md).
Lost assertions or changed semantics require rework; accurate repetition or naming
preferences alone are advisory and do not justify a regeneration cycle.

Also inspect testing-work constraints in the selected canonical `requirements` linked
by each case's `requirement_ids`; they need not appear in human case fields. Verify
applicable restrictions against proposed source and authorized project configuration,
including subject substitution, framework, dependencies and file edits. A requirement
link alone is not compliance evidence. Leave actual execution/file preservation to the
controller's existing checks; do not claim a static review proves runtime compliance.

For each case retain the declared application boundary, the actual test/helper call path,
absence of target substitution and the assertion observing Expected, with file and
class/method references. A confirmed unit substitute is a blocking finding and requires
`ТРЕБУЕТ ДОРАБОТКИ`; heuristic signals alone require inspection, not automatic rejection.
At `ПРИНЯТО`, findings and corrections remain empty under the existing schema. Store
positive evidence in top-level `warnings`, for example the following shape, using actual
reviewed identifiers and code (never copy the placeholders as evidence):

```text
REVIEW_EVIDENCE <case/relation IDs>: boundary=<declared interface>;
path=<file:class.method → helper → boundary>; real behavior=<code evidence>;
oracle=<assertion location and observed Expected>.
```

The string is an existing-contract evidence channel, not a new schema or acceptance
predicate. Its syntax does not prove that the reviewer traced the code. The required
Deepseek functional-vs-unit control calls must separately assess both verdict correctness
and preservation of these answers before claiming the contract sufficient in practice.

Findings are static evidence only. A semantic issue, stale digest, missing declared content, invalid locator, absent pair, secret, or unconfirmed setup is rework. The review binds `automation_revision`, reviewer session/invocation IDs, the role policy, and the digest of the exact attempt-owned controller readback boundary (`automation-review-boundary-r1` or `-r2`). The controller validates that boundary against the active attempt; self-attested isolation is not accepted. There is one static reviewer invocation per version and at most two reviews: `AUTO_FIX_APPLIED` returns only to one complete, digest-bound revision 2; never run an obsolete artifact or regenerate after runtime `FAIL`.

Only the controller may turn an accepted review into a generated delta, materialize files,
claim execution, decide dispositions, or compute terminal acceptance.
