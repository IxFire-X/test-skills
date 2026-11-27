# Stand mode — the module's autotests run against a raised stand

Read only when the `read` of the batch has `stand`: `service_url_env` names the environment variable that holds the address of the
stand's API (the address the paths of the requirements start from).
- Every HTTP step: `base_url_source: {"kind": "environment", "name": <service_url_env>}` — never another name (`STAND_URL_NAME`).
- The stand is shared: its data rule is the insert `shared-data.md`, read with this one.
