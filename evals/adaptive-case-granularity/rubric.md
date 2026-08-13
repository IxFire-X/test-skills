# Adaptive case-granularity generator rubric

These are generator-output evaluations. They do not use the classifier no-case rubric.

Use hard binary gates. A sample passes only when every applicable gate passes:

- The output is a valid canonical generator document grounded only in the inline V3 context artifact.
- Every generated case has exactly one scenario key: `setup/role + initial state + input partition/branch condition + primary action or cohesive dependent action chain + terminal outcome`.
- The output has the manifest's expected case count: twenty assertions for one behavior yield one case; two independently executable branches yield two cases; duplicated endpoint, permission, and response evidence from one control flow yield one case.
- Assertions, endpoint evidence, permission checks, response fields, and other same-flow observations remain nested checks or evidence. They do not become extra cases.
- The model derives boundaries from distinct scenario keys and gives no minimum, maximum, quota, per-domain count, or other numeric-target reasoning.

Score control and guidance variants independently. Record aggregate failure categories only; never persist raw outputs. A repeated bypass fails the scenario: successful samples do not average it away. Behavior evaluation is `UNVERIFIED — harness unavailable` until a real fresh-context harness produces the policy's samples.
