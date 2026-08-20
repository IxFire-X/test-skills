# Change-scoped semantic evidence rubric

The fixed portable floor is exactly 24 synthetic controls: one `supported` and one `deceptive_or_no_fact` control for each of the 12 categories in `scenarios.json`. Run every control with the `SEQUENTIAL` controller. Independent comparison is optional and never replaces this floor.

Score these phases independently and in this exact order:

1. `scope_selection`
2. `scope_false_inclusion`
3. `scope_omission`
4. `candidate_extraction`
5. `batch_false_claim`
6. `batch_omission`
7. `promotion`

A phase passes only when the persisted artifact and verdict match the control oracle. A correct rejection of a deceptive claim is a pass. A supported non-route fact must survive rejection of a false route. A genuine no-fact source must remain no-fact, while README/package contracts and runtime configuration must not be discarded by path or file category.

The route controls require framework provenance, a registration sink, and a bound target together. URL/path helpers alone are not routes. The known route-helper, public README false-no-fact, and runtime/deployment-config regressions are mandatory and cannot be averaged away.

Overall status is `PASS` only when all 168 phase results pass and `mismatch_count` is zero. Persist only control IDs, verdicts, artifact digests, controller mode, clean target identity, and terminal/baseline bindings. Never persist raw source, prompts, model reasoning, secrets, or absolute artifact paths.

Any mismatch blocks `PASS`, promotion, and baseline advancement.
