# Tc-reviewer initial-GREEN acceptance

Fresh Sol verdict: `ship`.

Accepted initial-GREEN map, in declared repetition order:

1. `02-green-initial-v2/rep-01`
2. `02-green-initial-v2/rep-02`
3. `02-green-initial-v4/rep-03`
4. `02-green-initial-v4/rep-04`
5. `02-green-initial-v5/rep-05`

`05-scorecards/green-initial.json` is complete: all five repetitions are `true` for `deterministic-outcome`, `traceable-review`, and `blocking-gap-preserved`; its aggregate is `all_passed: true` and `no_gap: false` as required by the scorecard schema. Every accepted run has schema-valid output evidence and a passing recorded semantic result; focused lifecycle and command-contract checks cover the declared mixed route and v5 bootstrap/v2-checker contract.

The unversioned initial v1 attempt, v2 `rep-03`, v3 `rep-03`, and v4 `rep-05` invalidated attempts remain immutable and unscored. They are not evidence for the accepted scorecard and are never repaired, rerun, replaced, or reactivated.

The next step is a read-only pressure readiness audit. A pressure evaluator is not authorized by this checkpoint.
