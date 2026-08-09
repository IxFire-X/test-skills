# Fresh Sol acceptance

Verdict: **diagnostic-ship**.

This was a genuinely blind attempt: the evaluator used `fork_turns: none`, and neither the expected case count, distribution, identifiers, hidden oracle, nor checker was disclosed. The attempt remains formally `semantic-failed`, immutable, and excluded from formal acceptance; it must not be repaired or retried.

The evaluator independently produced exactly 36 cases with the hidden `6 happy / 12 positive boundary / 12 negative boundary / 6 authorization` distribution. Requirements, provenance, order, endpoints, statuses, response codes, roles, categories, exact bidirectional coverage, warning preservation, and absence of a `claims_adjuster` case all matched the hidden oracle.

The only two checker errors concerned `TC-0001` and `TC-0031`: the output used one supported representative datum (`seat_count: 2`), while the hidden oracle incorrectly required two. The canonical contract permits one deterministic `<field>: <lower+1>` representative value for happy and role-denial cases. Independent review therefore classified both errors as checker false negatives, not tc-generator defects.

Supported conclusion: the portable tc-generator independently determined and correctly generated the complete 36-case suite for a previously unseen feature of comparable scale.
