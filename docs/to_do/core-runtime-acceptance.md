# Core runtime acceptance — current Fix Round (2026-08-07)

## Scope and environment

Accepted subject: the deterministic portable core runtime only. This is not an
acceptance claim for the six skills, adapters, or end-to-end skill behavior;
those remain Plan 2/3 work.

- Worktree under test: `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`
- Historical base evidence: `f6c2d5e` (the measurements below marked historical
  describe that base and are not claims about current HEAD).
- Current HEAD evidence: recorded below after the Fix Round verification commands.
- Audit interpreter: `D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe`
- Runtime dependencies: `jsonschema`, `PyYAML`, `pytest` from the audit venv.
- Java baseline: `JAVA_HOME=D:\AI-Projects\.tools\jdk-17`, Maven bin
  `D:\AI-Projects\.tools\maven\bin`, OpenJDK `17.0.10`.

## Historical base evidence (not current HEAD)

Before the runner correction, the newly added POSIX-wrapper unit coverage was
run first and failed as expected:

```text
python -m pytest tests/test_run_tests.py -q
2 failed, 9 passed
observed command: ['mvnw', 'test']; expected: ['./mvnw', 'test']
```

The minimal fix invokes selected POSIX project wrappers as `./mvnw` or
`./gradlew`; the Windows `.cmd` wrapper test remains Windows-only via
`skipif(os.name != "nt")`.

## Current Fix Round obligations

Current HEAD adds scanner confinement (including symlink-resolved candidates), one
schema-valid JSON scanner result containing its artifact, pack-integrity checks,
zero-discovery FAIL policy, and method-level runner evidence from the existing
`tc-to-autotest` artifact. `trace_check --require-execution` remains the terminal
acceptance gate; unbound or ambiguous runner evidence is non-authoritative and
cannot support pipeline PASS.

## Seven CLI smoke checks (historical baseline)

`tests/test_cli_smoke.py` invokes every command below as a real subprocess via
the current interpreter, with raw byte capture, `check=False`, and a timeout.
It decodes contract-bearing stdout as strict UTF-8 before JSON parsing; stderr
is separately decoded with UTF-8 replacement only for diagnostics. Temporary
Python project, artifact/schema, and trace inputs are pytest-owned `tmp_path`
fixtures; no persistent temporary project is created.

Fix Round 1 found a Windows CP1251 framing defect: scanner and runner JSON
with Cyrillic text failed strict UTF-8 decoding in a normal inherited
environment. The smoke helper no longer sets `PYTHONUTF8` or
`PYTHONIOENCODING`. Instead, `scan_project.py`, `run_tests.py`, `doctor.py`,
and `contract_check.py` explicitly reconfigure stdout to UTF-8 at entry. The
scanner smoke snapshots the complete fixture tree before invocation—every
relative directory plus every relative file and exact bytes—and requires exact
equality afterward.

| CLI and direct command shape | Exit | Key observed output |
| --- | ---: | --- |
| `doctor.py --root ROOT` | 0 | JSON `status: PASS` |
| `contract_check.py --root ROOT --full` | 0 | JSON `status: passed` |
| `render_contract_docs.py --root ROOT --check` | 0 | clean empty stdout/stderr |
| `scan_project.py --project TMP --target src/api.py` | 0 | schema-valid JSON, `status: success`, only `src/api.py`, exact fixture tree unchanged (including directories and file bytes) |
| `run_tests.py --project TMP --language python --python-executable SYS --pytest-target TMP/test_smoke.py` | 0 | schema-valid JSON `verdict: PASS`, `stats.total/passed: 1/1` |
| `validate_artifact.py TMP/schema.json TMP/valid.json` | 0 | JSON `status: valid` |
| `validate_artifact.py TMP/schema.json TMP/invalid.json` | 1 | JSON `status: invalid` |
| `trace_check.py TMP/trace.json --require-execution` | 0 | JSON `valid: true`, `trace_audit.verdict: PASS` |

Focused Fix Round 1 smoke command:

```text
python -m pytest tests/test_cli_smoke.py -q
9 passed in 1.80s
```

## Pack verification (historical baseline)

```text
python -m pytest tests -q
284 passed in 11.45s

python -m ruff check tools tests
All checks passed!

python tools/contract_check.py --root . --full
exit 0; {"status": "passed", "errors": []}

python tools/render_contract_docs.py --root . --check
exit 0; no output (checked-in projections are deterministic and clean)
```

The full pytest suite includes schema meta-validation coverage for the existing
JSON Schemas.

## Real Java fixture baseline (historical, external fixture)

With `JAVA_HOME` and `PATH` set to the supplied JDK 17 and Maven locations:

```text
python tools/run_tests.py --project D:\AI-Projects\step5-java-demo --language java
exit 0
verdict: PASS
target.command: mvnw.cmd test
environment.interpreter: openjdk version "17.0.10" 2024-01-16
stats: total=24, passed=24, failed=0, errors=0, skipped=0
```

The project-local Windows wrapper `mvnw.cmd` was selected, and Maven’s final
Surefire aggregate confirms 24/24 tests.

The external `D:\AI-Projects\step5-java-demo` fixture is not part of this pack,
so the historical 24/24 measurement is not reproducible from the pack alone.
Current Fix Round Java verification must be reported separately with its exact
fixture source and command; a missing external fixture is a limitation, not a
replacement PASS claim.

## Current Fix Round measurements (2026-08-07)

```text
python -m pytest tests -q
297 passed, 1 skipped in 16.01s

python -m ruff check tools tests
All checks passed!

python tools/contract_check.py --root . --full
exit 0; {"status": "passed", "errors": []}

python tools/render_contract_docs.py --root . --check
exit 0; no output

pytest tests/test_run_tests.py::test_python_artifact_binds_each_selected_method_to_real_pytest_outcome -q
1 passed in 0.75s
```

The generated-method scenario invokes a real pytest subprocess and verifies one
passed, one skipped, and one failed `METHOD-*` binding. The requested external
Maven baseline was also re-run with JDK 17 and Maven: it returned `PASS`,
`mvnw.cmd test`, and `total=24, passed=24`.

## Fix Round 2 measurements

Current HEAD adds schema/digest validation of `tc-to-autotest` input, exact
physical Python/Java method identity, and fail-closed Java report freshness.
Gradle aggregate/XML parsing has fixture-level coverage; no real Gradle E2E is
claimed because this environment supplied Maven only. The exact current probes:

```text
python -m pytest tests -q
314 passed, 1 skipped in 14.35s

pytest tests/test_run_tests.py::test_python_artifact_binds_each_selected_method_to_real_pytest_outcome -q
1 passed in 0.78s

JAVA_HOME=D:\AI-Projects\.tools\jdk-17; Maven=D:\AI-Projects\.tools\maven\bin
python tools/run_tests.py --project D:\AI-Projects\step5-java-demo --language java
PASS; mvnw.cmd test; OpenJDK 17.0.10; total=24, passed=24
```

## Known warnings and deferred items

- The Java fixture emitted its application-level expected warning about a
  missing request parameter; the Maven build and all tests passed.
- `scan_project.py` may legitimately return `partial` when a project lacks
  enough manifest evidence. The smoke fixture provides both application and
  test dependency evidence and therefore verifies `success`.
- Normal Windows console code-page inheritance is no longer accepted as JSON
  framing: runtime CLI stdout is explicitly UTF-8, while stderr remains
  diagnostic-only and is safely replacement-decoded by the smoke harness.
- Skill semantics, adapter integration, and end-to-end skill acceptance are
  explicitly deferred to Plans 2/3 and are not covered by this report.
