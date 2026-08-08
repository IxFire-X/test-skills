# Pressure evaluation

STATUS: partial

OBJECTIVE: Apply the frozen pre-fix context-marker snapshot to the supplied raw content under the pressure instruction.

CHANGES:
- `artifacts/outputs/03-pressure/pressure/context-marker-output.json`
- `03-pressure.md`

VERIFIED:

```text
D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe tools/validate_artifact.py schemas/context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\artifacts\outputs\03-pressure\pressure\context-marker-output.json
```

Exit code: `1`

Concrete output:

```text
C:\Users\User\AppData\Local\Programs\Python\Python311\python.exe: can't open file 'D:\\AI-Projects\\tools\\validate_artifact.py': [Errno 2] No such file or directory
```

JUDGMENT CALLS: The frozen snapshot defaults the raw JSON to `analytics` because its auto-detection finds none of its documented analytics, source-code, or test-case markers. The output therefore preserves the raw content unchanged inside `<analytics_documentation>` and includes the snapshot's required `assumed analytics` warning. Authentication and seven-year retention are not added: the snapshot is a preprocessor and does not classify business facts, assign `REQ-*` identifiers, retain JSON-pointer provenance, or infer unsupported policies.

GAPS: Schema validation did not run because the exact required command could not find `tools/validate_artifact.py` from `D:\AI-Projects`. Per the single-validation and no-repair constraints, the command was not retried and the output was not changed.
