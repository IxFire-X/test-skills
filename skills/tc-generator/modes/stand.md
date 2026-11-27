# Stand mode — the module's autotests run against a raised stand

Read only when the `read` of the batch has `stand`: `service_url_env` names the environment variable that holds the address of the
stand's API (the address the paths of the requirements start from).
- Every HTTP step: `base_url_source: {"kind": "environment", "name": <service_url_env>}` — never another name (`STAND_URL_NAME`).

The stand is shared: other people's and demo records live there.
- A record the case creates and whose name must be unique carries `<метка прогона>` in that name (the automation puts one suffix of
  letters per run there).
- Every id comes from a response (`step_output`); a missing id is a value the list response shows absent, never a constant.
- Never change, delete or count a record the case did not create; no check of a total before and after (a parallel client changes it).
- The last step «Уборка: …» deletes what the case created, by the ids from the responses. When the API cannot delete it, that step says so
  in words — the explicit refusal of the cleanup; the tester decides.
