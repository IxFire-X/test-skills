# Context Marker route-evidence rubric

## Pilot matrix

| Authorized evidence | Framework provenance | Registration sink | Bound target | Route fragment |
|---|---:|---:|---:|---:|
| Known framework import, mounted table entry, resolved handler | Yes | Yes | Yes | Yes |
| Known framework import, mounted include entry, included resolver | Yes | Yes | Yes | Yes, as delegation |
| URL/path helper only | No | No | No | No |
| Local `register`-shaped function, local table, callback | No | Yes | Yes | No |
| Known framework constructor, mounted table entry, null target | Yes | Yes | No | No |

Use hard binary gates. A sample passes only when every applicable gate passes:

- The registered route rows satisfy all three gates. Each helper-only path or incomplete row produces no route fragment.
- A route fragment exists exactly when cited evidence_ranges jointly establish framework provenance, a registration sink, and a bound target.
- Framework provenance comes from an imported or qualified known framework constructor, decorator, or router identity. Names and call shapes alone do not establish it.
- A sink is a route table/config entry, returned mounted collection, decorator, or router registration. A URL/path helper is not a sink.
- A direct fragment identifies the resolved handler, view, or viewset. An include fragment identifies delegation to the included resolver and does not relabel it as direct dispatch.
- An HTTP verb appears only when authorized evidence explicitly states or binds it. The direct and include positive pilots therefore contain no inferred verb.
- Rejecting an unsupported route does not erase a separately supported non-route fact; such a fact remains eligible under the ordinary observable-fact contract.

Score control and guidance variants independently. Record aggregate failure categories only; never persist raw outputs. A repeated bypass fails the scenario: successful samples do not average it away. Behavior evaluation is `UNVERIFIED — harness unavailable` until a real fresh-context harness produces the policy's samples.
