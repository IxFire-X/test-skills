---
name: context-marker
description: Use when converting supplied requirements and source changes into a provenance-preserving context artifact for the testing pipeline.
---

# Context Marker

Produce one JSON artifact named `context-marker-output.json`. Read only the inputs named by the caller; never scan unrelated files.

## Required output

The artifact must validate against [the context artifact contract](references/context-artifact-contract.md). Its top-level shape is:

```json
{
  "schema_version": "2.1.0",
  "stage": "context-marker",
  "artifacts": {
    "analytics_documentation": {"requirements": []},
    "source_code_and_diff": {"sources": []}
  },
  "warnings": []
}
```

For every supported requirement, emit a `REQ-*` id, faithful text, and an array of precise source anchors. `sources` and `warnings` are arrays of strings. Quote unsupported, missing, or ambiguous policy only as a warning; never promote it to a requirement.

## Method

1. Extract only explicit behavior from the supplied analytics input.
2. Record code/diff observations as source strings, without inventing behavior.
3. Preserve an input path plus a stable local anchor for each requirement.
4. Add a warning when a requested policy is absent or unsupported.
5. Validate the single JSON envelope before returning it.

## Boundaries

- Do not output XML, batch results, wrappers, or alternate envelope keys.
- Do not create authorization, approval, retention, privacy, or execution claims absent from the inputs.
- Do not read a schema or fixture unless this skill links it or the caller permits it.

