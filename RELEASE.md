# Portable pilot release

- **Package version:** `0.5.0-pilot`
- **Machine authority:** `release/manifest.json`
- **Qualification:** `implemented_unverified`
- **Ready tuple:** `null`
**Previous known-good:** none recorded

`release/manifest.json` binds the exact runtime registry/digest, Pipeline `4.0`,
compatibility contract `portable-cli-v1`, execution profile `v1`, policies, adapters,
model-stage registry and `pilot-critical-v1` release-eval suite. The manifest is
persisted but excluded from its own runtime registry to avoid a self-digest cycle.

`implemented_unverified` is deliberate: a green local implementation gate does not
prove a CLI/host/model/project tuple. This release must not claim broad Python, Java,
Maven, Gradle or model compatibility.

## Local implementation gate

Use one Python 3.11, 3.12 or 3.13 environment with `requirements-dev.txt` already installed:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m tools.ci_gate --root .
python -m tools.doctor --root .
git diff --check
git status --short
```

The pack does not create the environment or install dependencies. Exact observed
dependency versions are recorded in `release/dependencies.lock.txt`; an approved
company mirror/wheelhouse remains external deployment evidence.

Linux uses the same gate:

```bash
export PYTHONDONTWRITEBYTECODE=1
python -m tools.ci_gate --root .
python -m tools.doctor --root .
git diff --check
```

The repository workflow `.github/workflows/portable.yml` checks Ubuntu 24.04 with
Python 3.11/3.12 and Windows with Python 3.12. `ci_gate` runs contract validation,
projection checks and pytest once; do not prepend a second full test run. It exits `2`
when pytest is not installed or a step times out (`--check-timeout`, default 900 seconds
per contract check; `--pytest-timeout`, default 4 hours). A configured
workflow is not a successful Linux run: record the actual OS/runtime and gate result
before making a platform verification claim.

`.gitattributes` keeps text checkout bytes as LF on both platforms. Do not normalize
runtime bytes while hashing: the manifest and receipts continue to bind exact bytes.

## Exact-tuple release gate

The release environment supplies one authorized real project and immutable compatible
CLI campaign:

```powershell
python -m evals.release_eval `
  --campaign-dir $env:PILOT_EVAL_CAMPAIGN `
  --project $env:PILOT_E2E_PROJECT `
  --module $env:PILOT_E2E_MODULE `
  --policy local-pilot-v1 `
  --require-real-execution `
  --require-independent-review
```

The gate fails closed on absent/tampered evidence, tuple drift, missing real execution,
unverified reviewer isolation or a protocol violation. Policy `adaptive-1-3-5-v1`
requires one smoke plus three fresh runs for every critical scenario. Instability or a
protocol violation permanently disqualifies that campaign. After the cause is fixed,
start a new clean campaign. The evaluator keeps its append-only controller-owned
history under `.pilot-runs/release-eval-ledger`, automatically carries the active
escalation forward, and requires five fresh runs for every critical scenario.
`--predecessor-evaluation` only imports a valid external predecessor into that same
history; omitting the option cannot clear an already recorded escalation.

The first previous-known-good promotion requires a separate explicit human approval
receipt. Do not invent a rollback SHA or infer it from local Git state.

## N/A for core pilot

- live company execution adapter;
- production deployment and rollback;
- real Zephyr tenant import/re-export;

These may receive independent evidence later. Their absence does not change the honest
current state: package implementation exists, but no exact tuple is yet verified.
