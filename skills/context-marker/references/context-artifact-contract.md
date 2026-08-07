# Context artifact contract

The authoritative machine schema is [context-marker-output.schema.json](../../../schemas/context-marker-output.schema.json).

Required rules:

- `schema_version` is `2.1.0`; `stage` is `context-marker`.
- `artifacts.analytics_documentation.requirements` is a nonempty array of objects with `id`, `text`, and a nonempty `provenance` string array. Requirement ids begin with `REQ-`.
- `artifacts.source_code_and_diff.sources` is a nonempty string array.
- `warnings` is a string array. State an unsupported claim as a warning, not as a requirement.
- No additional top-level or nested fields are permitted by the schema.

Use local provenance anchors such as `input.md#AC-1` or `change.patch#function-name`; preserve the supplied wording where possible.
