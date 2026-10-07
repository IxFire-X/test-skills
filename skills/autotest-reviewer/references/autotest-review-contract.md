# Bounded static automation-review contract

Validate canonical JSON with `tools.canonical_document` and artifact relations with `tools.automation_validation`. Verify the automation source against the selected effective document's exact digest and its exact attempt-owned `effective_bundle_receipt_digest`, then inspect every declared file's inline UTF-8 `content` and every generated locator. Use the runner-compatibility check with `materialized=False`; no generated file is written or required on disk during this review. On-disk digest/confinement checks remain mandatory after controller materialization.

Review atomic operation/assertion relation ownership, canonical physical order, and complete coverage. `reviewed_files` contains every declared `(file_id, content_digest)` in generated-file physical order; `reviewed_symbol_pairs` contains every distinct required pair exactly once in generated-symbol physical order; `reviewed_relations_sha256` identifies the complete physical relation array. Multiple pairs for a target are AND requirements. Verify exactly one manual disposition per manual step, canonical-blocker branches, no extra relation, and no missing assertion. A canonical blocker blocks only its own case. When at least one case can be automated the artifact must be `GENERATED`: it automates the unblocked cases, has one manual disposition for every step of each blocked case and nonempty `diagnostics`. `BLOCKED` is valid only when nothing can be automated. `tools.automation_validation` reports violations as `AUTOMATION_BLOCKED_HAS_AUTOMATABLE_CASE`, `AUTOMATION_BLOCKED_CASE_DIAGNOSTICS` and `AUTOMATION_MISSING_BLOCKED_DISPOSITION`.

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
positive evidence in the assigned scope coverage `assessment`, for example the following shape, using actual
reviewed identifiers and code (never copy the placeholders as evidence):

```text
REVIEW_EVIDENCE <case/relation IDs>: boundary=<declared interface>;
path=<file:class.method → helper → boundary>; real behavior=<code evidence>;
oracle=<assertion location and observed Expected>.
```

The string documents substantive scope review; it is not a separate acceptance predicate. Its syntax does not prove that the reviewer traced the code. The required
functional-vs-unit control calls, using the model actually selected for evaluation, must separately assess both verdict correctness
and preservation of these answers before claiming the contract sufficient in practice.

Findings are static evidence only. A semantic issue, stale digest, missing declared
content, invalid locator, absent pair or unconfirmed setup requires rework or an explicit
unchecked gap. Review all remaining trustworthy parts after a content finding.
Global bindings (source and effective bundle receipt digests, `automation_sha256`, the
relation-array digest, runner compatibility) are verified by the controller, not per
part: a part without them is complete, so do not report
`REVIEW_PART_GLOBAL_BINDINGS_NOT_IN_ENVELOPE` or mark scopes unchecked for that reason.

In `compact-v1` a part carries the automation view of its cases (every anchor and binding of the
canonical projection; human Test Data only for steps without inputs), exact method slices (original line numbers), the SUPPORT code of
their file once, a shared-state table and code-check suspicions; there are no pair scopes, and the
SUPPORT and cross areas cover test interactions. Missing slices never shorten the evidence: a file
that cannot be sliced is sent whole or its part is blocked.

There is one logical review per immutable automation revision, with sequential fresh
isolated invocations for original-source, local and cross-part scopes. Actual stage IDs
are `autotest-reviewer:r1:part-000001` (current revision/ordinal); role policy is
`autotest-static-reviewer-v2`. Each attempt-owned part boundary binds exact input,
invocation, model, CLI/settings and host isolation. The logical boundary binds session,
automation, effective canonical/bundle and plan digests. Controller readback validates
these bindings; caller copies and model assertions do not prove isolation.

Scopes of one part overlap, so the part envelope carries each distinct input once: the
first occurrence has `content`, and an exact repeat carries
`content_ref: {scope_id, input}` (the scope and zero-based input index of the first
occurrence in the same part) instead of `content`. Resolve the reference and treat the
entry as fully supplied evidence. A part whose invocation produced no valid assessment
may be reopened with a new invocation, at most three per part; the controller records
the failure with `fail-part --failure-class TRANSPORT|CONTENT`, and retried invocations
use stage IDs ending in `-try2` and `-try3`.

The model assessment contains only coverage/findings/corrections/required_checks.
Controller `submit-part` binds the declared part, and `finish-review` recomputes complete
coverage and projects one v6 output with `artifacts.review_aggregate` plan/aggregate
digests. The output retains logical `reviewer_session_id`; singular invocation/isolation
fields no longer belong in this aggregate. Never present the aggregate as a model
response or accept incomplete scope. Initial r1 plus one complete digest-bound r2 and
full repeated review remain the limit; parts spend no correction budget. Never execute
an obsolete artifact or regenerate after runtime `FAIL`. The one exception the
controller allows is a corrected revision after `NOT_RUNNABLE/GENERATED_TEST_INVALID`
(the generated test did not compile, import or collect): it is generated once, in a
child attempt with `retry_reason=GENERATED_TEST_INVALID`, and receives this same full
static review.

Only the controller may turn an accepted review into a generated delta, materialize files,
claim execution, decide dispositions, or compute terminal acceptance.
