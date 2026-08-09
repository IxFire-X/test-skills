# Blind scale acceptance

This isolated acceptance scenario uses a new subscription-invoicing domain. The evaluator receives only the blind prompt, canonical skill inputs, and the schema-valid context-marker artifact; it does not receive the oracle, expected cardinality, group distribution, or checker.

The hidden oracle defines the complete supported semantic atoms. The checker verifies copied requirements/provenance/warnings, exact atom coverage, structural endpoint and response behavior, no duplicates or dangling coverage, and known-token contamination in free text. It deliberately permits paraphrased titles and preconditions. A fresh Sol semantic review is mandatory after a passing deterministic check to assess claims that cannot be reliably parsed.

Exit codes: `0` pass, `1` semantic failure, `2` output shape failure.
