# Fresh Sol acceptance

Verdict: **diagnostic-ship**.

The immutable attempt remains formally `semantic-failed` and excluded from supplemental acceptance. It must not be repaired, replaced, or retried.

Independent review confirmed that the generated artifact nevertheless demonstrates the requested scale: exactly 18 copied requirements, exactly 36 ordered cases with the `6/12/12/6` distribution, exact requirement links and reverse coverage, supported methods, paths, data, roles, HTTP status/code oracles, the exact warning, and no `claims_adjuster` case.

Scope limitation: the evaluator-facing prompt disclosed the expected 18 requirements, 36 cases, and `6/12/12/6` distribution. This proves execution at that scale, but it does **not** prove that the skill can independently discover the appropriate case count. A separate blind campaign with a different feature is required for that claim.

All 41 checker errors are limited to literal `title` or `preconditions` equality. The generated wording is semantically supported; the hidden matrix contains wording and prerequisites that the input does not require. The formal failure is therefore a checker false negative, not a tc-generator coverage or oracle defect.

No retry is authorized. Any future formal scale acceptance must use a separately versioned checker/fixture and must leave this attempt immutable.
