# Context Marker route-evidence rubric

## Pilot matrix

| Authorized evidence | Route fragment |
|---|---:|
| Handler plus explicit router registration connecting HTTP action and path | Yes |
| URL/path helper with no registration sink or binding | No |

Use hard binary gates. A sample passes only when every applicable gate passes:

- The registered route produces a route fragment because explicit framework registration connects its HTTP action and path to the handler.
- The helper-only path produces no route fragment and no endpoint requirement. An API-shaped name or URL-shaped string does not prove registration, reachability, or an HTTP action.
- The model does not invent a route table, decorator, router registration, setup/include binding, handler connection, method, response, actor, or outcome absent from authorized evidence.
- Rejecting an unsupported endpoint claim does not erase a separately supported non-route fact; such a fact remains eligible under the ordinary observable-fact contract.

Score control and guidance variants independently. Record aggregate failure categories only; never persist raw outputs. A repeated bypass fails the scenario: successful samples do not average it away. Behavior evaluation is `UNVERIFIED — harness unavailable` until a real fresh-context harness produces the policy's samples.
