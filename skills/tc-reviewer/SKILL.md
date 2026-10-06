---
name: tc-reviewer
description: Use when a canonical test-case branch needs complete original-source, local and cross-part review through fresh bounded invocations.
---

# Canonical test-case review

Read the [review contract](references/review-verdicts.md), the supplied exact part input
and `schemas/review-part-output.schema.json`. Execute relevant canonical/revision
validators; inspect their source only to diagnose a failure. The controller prepares
one immutable snapshot and plan for this logical review. One or many parts use the
same protocol; each invocation has a fresh isolated context and `canonical-reviewer-v2`.
Never consume generator dialogue or reasoning.

## Procedure

1. Check the supplied part identity, scope and exact original evidence. Use the supplied
   `run_root` and `attempt_id` for read-only controller readers when provenance needs
   verification. Do not expand scope or silently truncate a required input.
   Inside one part envelope each distinct piece of evidence is carried once. The first
   occurrence of an input has `content`; an exact repeat in a later scope of the same
   part has no `content` and instead carries `content_ref: {scope_id, input}`, naming
   the scope and the zero-based input index of that first occurrence. Read the content
   through the reference; a `content_ref` entry is complete evidence, not a missing or
   truncated input.
   A `[REDACTED:<rule>]` line in a document is a masked secret, not a requirement gap;
   do not reconstruct it or report it as missing evidence.
2. Review every assigned original-source, local or cross-part scope substantively.
   Compare original requirements with normalized conditions and actual case actions,
   data flow and observable assertions in both directions. Apply the mandatory
   [human scenario rules](../tc-generator/references/case-generation-contract.md#human-scenario-rules).
   Mapping IDs, correct digests and summaries alone do not establish coverage.
3. Return an assessment JSON with exactly `coverage`, `findings`, `corrections` and
   `required_checks`. Coverage has each supplied scope once in physical order, an
   explicit status, exact evidence and substantive assessment. Findings identify the
   violated rule, source and affected case/step. Request a bounded cross check through
   `required_checks` when the assigned evidence reveals an unresolved relationship.
   Address a check by what it must see: `case_ids` (every case ID is in the envelope's
   `document_index.case_ids`) and/or `requirement_ids` (`CREQ-…`/`SREQ-…`); `scope_ids` may name
   only scopes you were given. The controller adds the scopes holding those cases and
   requirements. Never name your own scopes to reach evidence outside this part.
4. Mark missing or untrusted evidence as unchecked and explain the gap. If the answer is
   elsewhere in the document, also add a `required_checks` entry addressed to the cases
   or requirements that hold it: when every check your answer requested comes back
   CHECKED, the controller closes your UNCHECKED scope and records the link. Such a gap
   in your envelope is not a finding against the cases. A substantive
   defect does not stop later trustworthy parts. Damaged shared snapshot evidence
   makes further review untrustworthy; report it to the controller.

## compact-v1 parts

When the task says `review_mode: compact-v1`, the only input is the part text (`.input.md`):
a deterministic projection of the canonical document with ID anchors. A line that starts
with `[ID]` defines that ID; a continuation of a text value starts with `| `. Original
requirement documents are printed verbatim with line numbers (`[SRC-n]`, lines `L12| …`).
The part lists its answer areas under «Области ответа»:

- `source-NNNNNN` — compare every condition of the original text with SREQ and CREQ
  (including prose without an SREQ, dropped acceptance criteria, requirements no case
  covers — the case index lists every case with its CREQ) and the capabilities with
  their sources;
- `local-<case>` — this case alone: actions, data, calls and inputs, every expectation and
  assertion against its CREQ/SREQ text and the capability contract (human scenario rules
  apply as above);
- `cross-…` — every pair of cases in this part: the same call with different expectations,
  shared state, absolute counts or full lists of a resource other cases change, order.

Answer with exactly `coverage`, `findings`, `corrections`, `lint_dispositions` and
`required_checks`:

- `coverage`: one row per listed area, in order — `area_id`, `status` (`CHECKED` or
  `UNCHECKED`), `refs` (1–3 anchors of this part; a case area cites at least one anchor of
  its own case — the case, a step, an expectation or an assertion; a source line is
  `SRC-n:Lk`) and `note` (at most 200 characters). Areas listed as carried are not answered.
- `findings`: `severity`, `code` (`UPPER_SNAKE`), `related_ids` (canonical IDs) and `message`
  (at most 600 characters, the violated rule and the exact location). At most 5 INFO.
- `corrections`: mechanical text fixes only — `target_id`, `field` from the dictionary
  (case: `title`, `objective`, `preconditions[N]`, `management.folder|status|owner|estimated_time`,
  `management.components[N]`, `management.labels[N]`; step: `action`, `test_data`,
  `manual_reason`; expectation: `text`; requirement: `text`), `before` (the exact current
  text), `after` and `why`. Behavior, literals, IDs and structure are never corrected.
- `lint_dispositions`: one per `[LINT-…]` suspicion of the part — `confirmed` or `rejected`
  with a short reason. A suspicion is a hint, not a finding: confirm it only after checking,
  and write the finding yourself.
- `required_checks`: `case_ids`/`requirement_ids` and `reason` for evidence outside the part.

The controller rejects an answer whose refs are not anchors of the part, whose case area
cites no anchor of its own case, that leaves a suspicion unanswered, has more than 5 INFO
findings or names unknown IDs. Fix the answer and submit again.

The controller binds the assessment using `submit-part`, validates all required parts,
then `finish-review` produces the v6 output and one authoritative aggregate. That output
is a controller projection, not a model response. The model never writes a session
ledger, service digests, full-document verdict or selected successor by hand.

Canonical r1 permits at most one complete mechanical r2 with preserved identities and
lineage. The controller builds that r2 from the parts' `corrections`; the reviewer
proposes corrections and never writes the successor document. Destructive, partial or semantic-choice corrections require rework. Parts do
not spend correction budgets. Incomplete coverage cannot be accepted. The aggregate
selects canonical content; execution and terminal acceptance remain later policy gates.
