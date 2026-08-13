# V3 behavior-evaluation rubric

Use hard binary gates. A sample passes only when every applicable gate passes:

- schema validity;
- canonical semantic validity;
- scenario topology/identity;
- exact human format for title, objective, preconditions, and numbered Action/Expected Result steps;
- no invented facts/secrets;
- exact stop behavior.

Score control and guidance variants independently. Record aggregate failure categories only; never persist raw outputs. A repeated bypass fails the scenario: successful samples do not average it away. Behavior evaluation is `UNVERIFIED — harness unavailable` until a real fresh-context harness produces the policy's samples.
