# Tc-generator 36-case Scale Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce independent evidence that the portable `tc-generator` generates exactly 36 complete, ordered, traceable cases for an 18-requirement bulk-shipment feature.

**Architecture:** A self-contained context-marker envelope and expected atom matrix drive a campaign-local semantic checker. One fresh Terra evaluator reads the unchanged canonical skill delivery, writes one reserved output, and the parent performs one schema validation and one scale semantic check before fresh Sol acceptance.

**Tech Stack:** JSON Schema, Python 3 standard library, pytest, Markdown evidence, native Terra/Sol delegation.

## Global Constraints

- All permanent artifacts remain under `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/`.
- Main tc-generator campaign metadata, phases, scorecards, and evaluator counts remain unchanged.
- The input has exactly 18 requirements and expects exactly 36 cases: 6 happy, 12 positive-boundary, 12 negative-boundary, and 6 authorization-denial.
- Requirements, provenance, warnings, case IDs/order, one-step actions, executable oracles, and bidirectional coverage are exact.
- `claims_adjuster` has no supplied policy, remains only an exact warning, and produces no case.
- No evaluator repetition is repaired or retried after a protocol, schema, or semantic failure.
- Iteration uses focused checks; broad verification is deferred to the final tc-generator checkpoint.
- No intermediate commit; changes join the user-requested scoped checkpoint commit.

---

### Task 1: Scale input and expected matrix

**Files:**
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/input/context-marker-output.json`
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/expected-matrix.json`
- Test: `tests/test_tc_generator_scale_acceptance.py`

**Interfaces:**
- Consumes: context-marker output schema v2.1.0 and the approved `DESIGN.md`.
- Produces: `expected-matrix.json` with ordered `happy`, `positive_boundary`, `negative_boundary`, and `authorization` atoms and exact expected total `36`.

- [ ] Create a schema-valid envelope with requirements `REQ-1001..REQ-1006`, `REQ-1011..REQ-1016`, and `REQ-1021..REQ-1026`, each with exact text and provenance.
- [ ] Encode the six operation endpoints/status codes, six inclusive ranges/outside error codes, six role denials, and exact `claims_adjuster` warning in the matrix.
- [ ] Add a focused fixture test that asserts 18 unique requirements, 36 unique ordered expected case IDs, category totals `6/12/12/6`, and exact warning preservation.
- [ ] Validate the input once with `schemas/context-marker-output.schema.json` and run only the fixture test.

### Task 2: Scale semantic checker

**Files:**
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/check_output.py`
- Modify: `tests/test_tc_generator_scale_acceptance.py`

**Interfaces:**
- Consumes: `--input`, `--matrix`, and `--output` absolute paths.
- Produces: stdout JSON `{status, errors, artifact_sha256, requirement_count, test_case_count}` and exit `0` only for exact scale acceptance.

- [ ] Implement stdlib-only JSON loading and SHA-256 reporting; invocation/read/shape errors exit `2`, semantic mismatch exits `1`.
- [ ] Verify exact requirements/provenance/warnings, `TC-0001..TC-0036` order, one requirement link and one step per case, exact atom fields/categories/actions/oracles, and exact reverse coverage.
- [ ] Reject missing/duplicate/dangling/extra links, unsupported `claims_adjuster` cases, added warnings, and appended behavior in any checked field.
- [ ] Add focused mutations for one missing boundary, duplicate ID, wrong coverage, wrong oracle, invented role case, and warning drift; run this test file once.

### Task 3: Readiness review and one evaluator run

**Files:**
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/protocol/prompt.txt`
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/output/tc-generator-output.json`
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/output/validation-result.json`
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/output/semantic-result.json`
- Create: `docs/to_do/skill-tests/tc-generator/08-scale-acceptance/REPORT.md`

**Interfaces:**
- Consumes: canonical `SKILL.md`, reference, output schema, approved scale input, and reserved output path.
- Produces: one immutable Terra output and literal-command evidence reviewed by fresh Sol.

- [ ] Obtain fresh Sol readiness approval for input/matrix/checker and exact command protocol.
- [ ] Run mandatory Sol Advisor preflight, materialize/hash the prompt, and verify all canonical input hashes.
- [ ] Delegate one fresh Terra evaluator to read prompt, skill, reference, output schema, and scale input in order and write the reserved output once.
- [ ] Run `tools/validate_artifact.py` once, then `check_output.py` once only if schema validation succeeds; stop on the first failure.
- [ ] Record literal argv arrays, absolute cwd, timestamps, hashes, results, observation, and report without modifying main campaign metadata.
- [ ] Obtain fresh Sol acceptance; pass requires schema success, semantic `pass`, exactly 18 requirements, exactly 36 cases, exact coverage/warning behavior, and no filesystem aliases.
