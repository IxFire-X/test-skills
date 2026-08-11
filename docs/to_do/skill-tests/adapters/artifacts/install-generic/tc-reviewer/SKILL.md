---
name: tc-reviewer
description: Review generated manual test cases for traceability, executable expected results, and unsupported assumptions; apply only safe mechanical corrections and return the canonical tc-reviewer JSON envelope. Use after tc-generator or whenever schema-valid generated test cases need deterministic acceptance, correction, or blocking review.
---

# Test-case reviewer

Review supplied `tc-generator` output without adding product behavior that the input does not state.

## Required inputs

Read, in order:

1. the supplied `tc-generator` JSON envelope;
2. [review-verdicts.md](references/review-verdicts.md);
3. `schemas/tc-reviewer-output.schema.json` from the skill-pack repository root.

The input envelope is the only authority for requirements, provenance, roles, endpoints, data, and expected behavior. Do not use repository knowledge or plausible defaults to fill gaps unless the task explicitly supplies them as an additional input.

## Review workflow

1. Collect the ordered requirement IDs, test-case IDs, and coverage links.
2. Review every test case named in the input. Preserve that input order in `reviewed_test_case_ids`.
3. Check each case for:
   - requirement IDs that exist and are covered bidirectionally;
   - actions, roles, endpoints, and data grounded in the supplied requirements;
   - steps with a concrete, observable expected result;
   - an `expected_outcome` consistent with the step-level oracle;
   - the complete `setup → action → observable result` chain is internally executable: the declared harness can actually produce the claimed response media type, body shape, status, and state;
   - no placeholder such as `TBD`, `TODO`, `unknown`, or `not specified` where an executable result is required.
4. Classify every defect before changing anything:
   - `mechanical`: spelling, punctuation, or formatting whose single replacement is explicit in the same input;
   - `blocking`: missing behavior, missing oracle, unsupported authorization, dangling traceability, contradiction, or any correction that requires choosing product semantics.
5. Apply verdict precedence from the reference. One blocking finding makes the whole review `ТРЕБУЕТ ДОРАБОТКИ` and prohibits corrections.
6. Emit one JSON envelope that validates against `schemas/tc-reviewer-output.schema.json`.

## Closed-world rule

Never invent or substitute:

- HTTP statuses or response codes;
- roles, permissions, or authentication policy;
- endpoints, methods, fields, records, or state transitions;
- retries, persistence, notifications, audit behavior, or other side effects;
- expected results for placeholders or missing outcomes.

Treat a harness/oracle contradiction as blocking even when every individual token appears in the input. Do not accept, for example, a JSON response oracle when the stated action returns plain text, or an authenticated outcome when the stated setup never establishes that role.

State the unsupported claim as a `BLOCKING` finding with field-level evidence. Do not rewrite the case into a behavior that merely seems reasonable.

## Safe corrections

Use `AUTO_FIX_APPLIED` only when every defect is mechanical and each replacement has exactly one interpretation supported by the input. For each changed case:

- copy the complete test-case object;
- change only the evidenced mechanical defect;
- add one correction record linked to the case;
- keep all unrelated fields byte-for-byte equivalent as JSON values.

A missing or placeholder expected result is never a mechanical correction. An unsupported role is never replaced with a supported role automatically.

## Output rules

- `ПРИНЯТО`: no findings, no corrections, and no corrected cases.
- `AUTO_FIX_APPLIED`: at least one non-blocking finding, at least one correction, and only the changed complete cases in `corrected_test_cases`.
- `ТРЕБУЕТ ДОРАБОТКИ`: at least one `BLOCKING` finding, no corrections, and no corrected cases.
- Every finding and correction uses only supplied requirement or test-case IDs in `related_ids`.
- Evidence names the exact input field and observed value; it does not claim unseen execution.
- `warnings` is for stage-level limitations only, not a substitute for findings.
- Return JSON only when the caller requests the pipeline artifact.

Before returning, validate the artifact with the repository validator and the canonical output schema.
