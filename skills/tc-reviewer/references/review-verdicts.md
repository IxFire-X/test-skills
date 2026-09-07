# Pilot reviewer-session and verdict contract

The controller publishes/reads back canonical revision 1 as `UNREVIEWED`, creates the exact reviewer package binding, and keeps the host-owned `reviewer-session` ledger separate from model-produced `tc-reviewer-output`. The allowed sequence is `STARTED -> (REQUESTED -> PROVIDED)* -> AUTHORITATIVE_VERDICT -> COMPLETED`, or `STARTED -> ... -> ABORTED` before a verdict. At most one verdict exists. A completed/effective canonical has exactly one verdict; only an explicit pre-verdict abort, including `REVIEW_CONTEXT_LIMIT`, has zero.

Each package binds candidate, normalized requirements, source/canonical/case mappings, inventory/context receipts, and exact evidence digests. Retrieval is C-lite and budgeted. Per-batch reviewers, reviewer trees, a second session, a second verdict, generator reasoning, and silent context truncation are forbidden. Host evidence proves isolation; missing proof is `independence_unverified` and makes acceptance false.

Use those authorized context receipts to compare against the original requirements,
not only their model-normalized subset. Reject dropped acceptance criteria, requirements
linked to irrelevant cases, and expected results weakened to match an implementation
defect. When original evidence is unavailable within the budget, report the gap or the
existing context-limit abort; never infer complete coverage from a valid mapping graph.

Audit coverage in two passes using the existing canonical requirements and mappings.
First derive the distinct conditions from the original authorized request yourself
and reconcile them with `requirements`: check each input variant, boundary, branch,
field rule and state transition, including several conditions in one source paragraph.
Do not use the generator's inventory as the sole list of what should be tested.
Second, trace every behavioral canonical condition to its actual case inputs,
operations and assertions (or justified manual/blocker gap). For example, linking a
length-30 case to a length-31 rejection condition does not cover that rejection.
Check all jointly required observations, such as rejection and unchanged storage.
Report a missing condition against its source/mapping and a missing check against
the affected canonical requirement and case/step. Do not claim full coverage from
the number of conditions, cases or relations. This audit belongs to the same single
authoritative review; no additional reviewer role or user-supplied checklist is required.

`tools.revision_selection` validates candidate digest, full successor lineage, source/canonical/case traceability, and identity preservation; `tools.canonical_document` validates canonical semantics. Canonical review has revision 1 and at most one complete mechanical successor revision 2; revision 3, destructive, partial, and choice-bearing changes are `ТРЕБУЕТ ДОРАБОТКИ`.

The verdict is canonical evidence, not the terminal pipeline result. Acceptance remains a
versioned policy decision after branch-valid trace and finalization; local execution also
requires generated-delta, execution, and disposition evidence.

| Verdict | Required output |
|---|---|
| `ПРИНЯТО` | candidate is selected; no successor |
| `AUTO_FIX_APPLIED` | one complete successor document, incremented lineage, preserved identity graph |
| `ТРЕБУЕТ ДОРАБОТКИ` | blocking findings; no successor |

`reviewed_case_ids` lists all candidate cases exactly once in physical order. A safe correction has a single evidence-backed mechanical meaning. It retains every unrelated entity and all IDs, including nested capability, step, output, expectation, and assertion identities. Removing a case, changing behavior, filling a missing technical fact, or returning a fragment is rework.

Review every human Action/Test Data/Expected Result triple against the mandatory
[human scenario rules](../../tc-generator/references/case-generation-contract.md#human-scenario-rules),
which are the shared format authority for generator and reviewer. Verify machine
operation/input/output/assertion ownership, previous-step data flow and manual/blocker
branches. A violation needs a precise location and a concrete ambiguity, semantic error
or broken format rule; a preference for different Russian wording is not rework.
Check that testing-work instructions remain in linked canonical requirements rather than
human case fields, while necessary setup and actual product constraints remain testable.
Do not demand extra cases or assertions to test the author of the tests. A mixed source
paragraph must retain both its product meaning and its instructions without copying the
instructions into the scenario. Cite the shared rule and exact field for a violation.
Check that a reader unfamiliar with the project can understand the purpose and result;
technical execution details must not obscure the meaningful action.
For baseline snapshots, verify that human fields identify the initial state from
Preconditions or a concrete preceding action, and the captured data used later;
“before this step” alone does not identify that state.
Check the shared rules on scenario-specific setup, helper controls in Test Data,
object-specific preservation and an objective that adds meaning to the title. Trace a
date/control dependency to the actual scenario, not just to a required helper argument.
Distinguish required behavior from source-derived exception/message characterization:
verify its provenance, selected boundary and stated scope; explicit requirement error
codes remain authoritative. Unsupported or contradictory oracles are correctness
findings. Redundant but accurate wording is a readability observation, not by itself a
blocking defect; cite concrete ambiguity when comprehension or execution is impaired.

Review native operation capabilities against the shared generation contract. A
composition of confirmed application/framework calls need not already exist as an
application helper or runtime provider; a route need not use direct HTTP binding when
the declared native test boundary exercises it faithfully. Reject unsupported calls,
fabricated observations and changed transport semantics. Also challenge a blocker
based only on the lack of a ready-made helper when the authorized evidence supports
the required composition. Check its argument/result bindings and real persistence
boundary, not merely its label. Missing evidence remains a gap, never a guessed API.

Compare candidate requirements with the input context without losing behavior classes.
Each relevant product-behavior warning needs either a testable canonical step or a concrete gap for
missing oracle, input, access or setup. A known defect with a clear oracle stays testable.
Every structured assertion must inspect the intended behavior; relations and warnings
cannot stand in for coverage. The report binds the exact bare candidate digest, and a
successor is validated as a whole, never merged with its candidate.
