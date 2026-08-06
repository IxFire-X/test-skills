# SDD trace document

`schemas/trace-document.schema.json` defines the host-neutral JSON record that proves the chain
from a source requirement to a test case, generated source, generated method, and (when required)
execution evidence. Validate it with `tools/trace_check.py`; this document is explanatory and does
not create an alternate contract.

## Model

The document has `schema_version: "2.1.0"` and closed objects throughout. Stable IDs use these
prefixes: `REQ-`, `TC-`, `FILE-`, `METHOD-`, and `RUN-`. Requirements, test cases, generated
files, methods, and mappings are all non-empty: a trace cannot claim success through an empty
topology.

```json
{
  "schema_version": "2.1.0",
  "requirements": [{"id": "REQ-login", "provenance": ["spec §3"]}],
  "test_cases": [{"id": "TC-login", "requirement_ids": ["REQ-login"]}],
  "generated_files": [{"id": "FILE-login", "path": "tests/test_login.py"}],
  "methods": [{
    "id": "METHOD-login",
    "file_id": "FILE-login",
    "name": "test_login",
    "test_case_ids": ["TC-login"],
    "requirement_ids": ["REQ-login"]
  }],
  "trace_map": [{
    "requirement_id": "REQ-login",
    "test_case_id": "TC-login",
    "file_id": "FILE-login",
    "method_id": "METHOD-login"
  }],
  "execution_required": true,
  "execution": {
    "verdict": "PASS",
    "evidence": [{"run_id": "RUN-01", "method_id": "METHOD-login", "status": "passed"}],
    "allowed_skips": []
  },
  "final_verdict": "PASS"
}
```

`generated_files.path` is a normalized portable relative POSIX path: no drive, absolute path,
backslash, `.`/`..` segment, repeated slash, or trailing slash. Dotfiles and normal extensions are
allowed. Physical path identity is compared case-insensitively as a deliberate portability
restriction, so `tests/ApiTest.py` and `tests/apitest.py` cannot be separate generated files. A
file may contain several methods, and a method may implement several requirements or test cases. A
mapping is the explicit four-ID link; distinct mappings may share a method.

## Invariants

Every requirement and test case needs a mapping. Every generated method needs a mapping, and every
generated file must own at least one method. References must resolve, a mapped case and method must
both declare its requirement and test case, and the method's `file_id` must equal the mapped file.
Duplicate entity IDs, mappings, and execution run IDs are rejected even when surrounding objects
differ.

Execution is required when either `execution_required` or the command-line
`--require-execution` flag is true. Then the execution verdict must be `PASS` and each mapped
method needs evidence. When execution is supplied at all, `FAIL` and `NOT_RUNNABLE` still make the
trace invalid; optional topology-only success therefore requires omitting `execution` and setting
`execution_required` to `false`. `NOT_RUNNABLE` has empty evidence and skip rules and is never an
acceptance result. A `PASS` verdict may not contain failed/error evidence, and a `FAIL` verdict
must have failure evidence. A `skipped` method is acceptable only with exactly one matching
allowed-skip rule, which has a non-empty `reason` and `policy_ref`:

```json
{
  "verdict": "PASS",
  "evidence": [{"run_id": "RUN-02", "method_id": "METHOD-login", "status": "skipped"}],
  "allowed_skips": [{
    "method_id": "METHOD-login",
    "reason": "isolated environment lacks the external identity service",
    "policy_ref": "test-policy §5.2"
  }]
}
```

The skip rule does not replace evidence; the skipped evidence still supplies the `RUN-` ID. Unused,
duplicate, unknown, or unqualified rules are rejected. `NOT_RUNNABLE` is never an acceptance
result. `final_verdict` must honestly match the derived state: `PASS` only for a trace without
semantic errors, `FAIL` for an invalid trace, and `NOT_RUNNABLE` only when execution is unavailable.

## Command and output

```text
python tools/trace_check.py DOCUMENT [--require-execution] [--schema PATH]
```

The command writes one UTF-8 JSON object to stdout. Exit `0` means the trace is valid, `1` means a
semantic trace failure, and `2` means unreadable JSON, invalid input shape, unavailable/invalid
schema, missing runtime dependency, or invalid arguments. Schema diagnostics use
`invalid_input_schema` and RFC-6901 paths; semantic diagnostics use codes such as
`MISSING_MAPPING`, `MAPPING_MISMATCH`, and `EXECUTION_GATE`.

For final pipeline acceptance, also supply the structural orchestrator artifact:

```text
python tools/trace_check.py TRACE_DOCUMENT --orchestrator-artifact ORCHESTRATOR_ARTIFACT
```

This compares the artifact's trace audit, run verdict, and per-method execution evidence against
the authoritative trace document. It preserves `SKIPPED` evidence only when the exact allowed-skip
reason and policy reference match; standalone artifact-schema validation cannot establish those
cross-document relationships.

The resulting `trace_audit` has `{verdict, mappings, errors}` and is the `trace_audit` artifact
produced by the `trace-check` step in `contracts/pipeline.json`. Each mapping contains
`requirement_id`, `test_case_id`, `file_id`, `method_id`, and `evidence_ids`, so it can be embedded
directly in the orchestrator artifact. Execution evidence intentionally records stable method and
run IDs rather than outdated path strings; the complete file identity is reconstructed through the
mapping's `file_id` and the generated method/file records. A topology-only check can have empty
`evidence_ids`; it is not execution acceptance.
