# Behavior Source Accounting Implementation Plan

## Global constraints

- Work only in `D:\AI-Projects\test-skills\.worktrees\adaptive-test-case-granularity` except final create-only InvenTree `attempt-02` artifacts.
- Preserve `.skillsrc.example`, `schemas/skillsrc.schema.json`, `tools/scan_project.py`, InvenTree source/tests/config/dependencies, and the rejected attempt.
- Follow RED → GREEN. One implementer at a time; fresh review after each coherent task.
- Do not run full unittest discovery until Task 2.
- No target requirement/case/batch count. Batch byte budget is a fixed internal implementation detail.

## Task 1: Coherent V5/Pipeline 5 vertical slice

This task must land as one coherent commit; do not commit a V5 producer against Pipeline 4 or a Pipeline 5 route against V4 fixtures.

**Core files:**

- Create `schemas/behavior-context-plan.schema.json`
- Create `schemas/behavior-context-batch-result.schema.json`
- Create `schemas/behavior-context-receipt.schema.json`
- Create `tools/behavior_context_planning.py`
- Modify `schemas/context-marker-output.schema.json`
- Modify `tools/test_classification.py`
- Modify `tools/audit_test_portfolio.py`
- Migrate Context Marker fixtures/evals/tests to V5 and add focused planner/result/receipt tests
- Modify Context Marker and generator skills/references/evals/static contracts
- Modify `contracts/pipeline.json`, `schemas/pipeline.schema.json`, `tools/contract_check.py`, `tools/doctor.py`, orchestration skill/reference, public docs, generated docs, and focused pipeline/orchestration/documentation tests

### RED matrix

1. V5 closed sibling accounting variants, receipt SHA, exact full source coverage/order, inverse links, exclusion semantics, supplied-source rejection, immutability, V4 rejection.
2. `select` and portfolio audit reject the formerly accepted requirements-only context and consume `ValidatedBehaviorContext`; no raw public requirements selector.
3. Plan exact source/accounted-range coverage, bounded read-range overlap, anchor ownership, stable IDs/exact domain-key derivation/order, fixed byte packing, zero-byte files, UTF-8-safe byte splitting of an over-budget one-line file, deterministic bytes, drift/symlink/unsafe paths.
4. Supplied inputs: exact repeated ID/path coverage, digest and nonempty checks, missing/duplicate/foreign rejection, paths absent from artifacts.
5. Batch results: exact plan/batch/item binding, one result per batch, anchor in accounted range, evidence in read range, boundary-spanning ownership, duplicate/missing/foreign results, stable safe closed fragments.
6. Receipt/accounting: selected module/domain-key recomputation, exact ordered result digests, current-byte revalidation, one outcome plus domain key per authorized source, exact fragment registry, every fragment in exactly one merge group and at least one requirement; represented sources require exact equality across fragment-group requirements, disposition requirements, and inverse source links, while excluded sources require all three sets empty; create-only/safe direct CLI.
7. Pipeline 5 mutations: version/artifacts/schema/tool integrity; exact five prefix carrier arrays from design; generator exact rejects; no accounting/receipt in generator or V3 tail; automatic `.skillsrc` behavior preserved; generated-doc drift detected.
8. Skill contract: every batch exactly once, terminal disposition, domain/global reduction, category-neutral exclusions, no project-specific materializer, generator receives only managed context.

### GREEN and verification

- Implement the smallest generic planner/receipt deep module and shared validated-context seam.
- Keep V1 source inventory unchanged. Require repeatable `SOURCE_ID=PATH` only when supplied rows exist.
- Update every public/programmatic caller in the same change.
- Run focused context/classification/audit/planner/pipeline/skill/orchestration/docs suites, schema CLI smokes, direct CLI safe-error probes, doctor, contract full, renderer check, py_compile, and diff/protected scope checks.
- Do not run full discovery.
- Fresh task review must check spec and quality before commit acceptance.
- Commit `feat: require complete behavior source accounting`.

## Task 2: InvenTree attempt-02 and final acceptance

**Create-only paths:**

- `02-context-marker/attempt-02/plan.json`, `batch-results/`, `domain-reductions/`, `receipt.json`, `context-marker-output.json`, `context-validation.json`, `completeness-review.json`
- `03-test-classifier/attempt-02/test-classifier-output.json`, `classifier-validation.json`
- `04-classifier-reviewer/attempt-02/reviewer-output.json`, `effective-technical-evidence.json`
- `05-tc-generator/attempt-02/tc-generator-output.json`, `canonical-document.json`, `canonical-validation.json`, `candidate-bundle/`, `candidate-bundle-receipt.json`
- `05-tc-reviewer/attempt-02/tc-reviewer-output.json`, and conditional `successor-document.json`
- `05-revision-orchestrator/attempt-02/revision-selection.json`, `effective-document.json`, `effective-bundle-receipt.json`, and conditional `successor-bundle/` plus `successor-bundle-receipt.json`
- `06-audit/attempt-02/classification-summary.json`, `case-inventory-review.json`, `case-inventory-summary.json`, `execution-notes.md`

### Execution

1. Reuse bootstrap and the 1,685-row inventory only after exact digest/current-byte verification. Run `context-plan`; InvenTree has no supplied inputs.
2. Process every batch once into closed batch results. Build the receipt mechanically; block on missing/duplicate/invalid results.
3. Reduce fragments by deterministic domain key and globally. Emit V5 context/accounting, then validate it with inventory + receipt.
4. Independent completeness review: every exact domain key; exclusions all when ≤25, otherwise first 10 + last 10 in inventory order and five rows selected by ascending SHA-256 of `<receipt-sha256>:<source-id>` from the remaining set. Category alone is never evidence. Verdict must be `ACCEPT`; otherwise stop.
5. Fresh classifier over all 233 pairs, full independent reviewer, and effective selection through the validated context type.
6. Generator consumes only `managed_behavior_context`; persist arbitrary adaptive case count. Run one tc-reviewer: `ПРИНЯТО` keeps revision 1; `AUTO_FIX_APPLIED` validates and selects exactly one successor; `ТРЕБУЕТ ДОРАБОТКИ` stops. Persist every named reviewer/selection/receipt path.
7. Publish/verify the immutable effective bundle. Run portfolio audit and case-inventory audit across every domain key/scope, duplicate meaning, assertion boundaries, and every unknown.
8. Require 1,685 terminal source dispositions, exact receipt-fragment-to-requirement coverage, 233 equal inventory/classification/review pairs, zero pending/error/conflict, zero uncovered requirements, classifier verdict `ПРИНЯТО`, context/case review `ACCEPT`, and tc-reviewer verdict in the two allowed continuation branches.
9. Run exactly one full unittest discovery plus contract full, doctor, renderer check, compileall, diff check, protected-file audit, and original/linked InvenTree status proof.
10. Fresh whole-change review. No InvenTree commit/push/PR.

Persist evidence in `.superpowers/sdd/2026-08-13-behavior-source-accounting/` and the exact attempt paths above. No stale Task 7 report alias is used.
