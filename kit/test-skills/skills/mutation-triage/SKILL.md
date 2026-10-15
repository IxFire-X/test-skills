---
name: mutation-triage
description: Use when the driver issues a mutation-triage task: decide for each group of surviving PIT mutants whether a test check is missing, the requirement is silent, the mutant is equivalent, or the code is out of scope.
---

# Survivor triage (`mutation-triage-v1`)

The opt-in MUTATION stage ran PIT on the passing generated tests. A mutant that survived
changed the product code and no generated test noticed. Each task carries a few groups of
survivors (one group = one product method and line) and everything needed to judge them:

- the mutated product line with its neighbours (`[MUT-0001:L59]`, the mutated line marked `>`);
- every mutation of the group (`[MUT-0001-1]` with the PIT mutator and description);
- the linked cases as the automation reviewer sees them (`[TC-…]`, `[STEP-…]`, `[EXP-…]`, `[ASSERT-…]`);
- the linked canonical and source requirements with their text (`[CREQ-…]`, `[SREQ-…]`);
- the exact slices of the generated test methods that ran on the line (`[T:L0120]`);
- the mutated class whole (`[F1:L0048]`) and, as the budget allows, the product methods that call
  the mutated method (`[F2:L…]`, one key per file).

The task fits the run's review part budget: the class and at least one covering test method with
its cases always come in; other methods (each with its cases) and the callers only while they fit.
What did not fit is listed on a line `… не вошли по бюджету: …` — those case IDs are still valid for
`TEST_GAP` and `SPEC_GAP`. The group's requirements whose cases did not fit are listed one line each
(`[CREQ-…]`, `[SREQ-…]`): they are anchors, a `SPEC_GAP` may name them. Judge `EQUIVALENT` only from the code in the input, never from what
you know about the project otherwise.

The task input is the only input. Do not read other files, the project or earlier answers.

## Decide one of four, per group

1. `TEST_GAP` — a requirement **does** specify the behaviour the mutant changes, and a linked
   test does not check it. Give `proposal`: the `case_id` (one of the group's cases), optionally
   the `step_id`, and in `text` which step or expectation to add or strengthen and what it must
   observe. Cite the requirement and the product line.
2. `SPEC_GAP` — no requirement specifies this behaviour, so no test may expect it yet. Give a
   question for the analyst (`question`, up to 300 characters) and the `requirement_id` of the
   nearest linked requirement. **Never turn the code into the expectation:** if the requirement
   is silent, it is a question, not a new check.
3. `EQUIVALENT` — the mutant does not change observable behaviour (for example it removes a
   debug print, or the changed boundary cannot be reached). Explain why in `rationale`.
4. `OUT_OF_SCOPE` — the code is outside the feature under test (unrelated method, framework glue).
   Explain in `rationale`.

Every group answer cites 1–6 anchors of this input in `refs`, at least one of them a line of
the group itself (`MUT-000N:L…`). `rationale` (up to 600 characters) explains the decision in
Russian; `proposal.text` and `question` are in Russian too.

## Answer

Return exactly `{"groups": [...]}` with one object per group of the task, in task order:
`group_id`, `decision`, `refs`, `rationale`, and `proposal` (only `TEST_GAP`) or `question` with
`requirement_id` (only `SPEC_GAP`). The controller checks every reference and rejects an answer
that cites an anchor outside the task, a case or requirement not linked to the group, or misses
a group. Triage changes no test case and no test: `TEST_GAP` proposals wait for the suite update
(wave 3), `SPEC_GAP` questions go to the analyst report.
