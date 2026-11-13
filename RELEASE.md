# Portable pilot release

- **Package version:** `release_qualification.package_version` of `contracts/pipeline.json` (the one place; the manifest and `PIPELINE.md` take it from there)
- **Machine authority:** `release/manifest.json`
- **Qualification:** `implemented_unverified`
- **Ready tuple:** `null`
**Previous known-good:** none recorded

`release/manifest.json` binds the exact runtime registry/digest, Pipeline `4.0`,
execution profile `v1`, policies, adapters and the model-stage registry. The manifest is
persisted but excluded from its own runtime registry to avoid a self-digest cycle.

`implemented_unverified` is deliberate: a green local implementation gate does not
prove a CLI/host/model/project tuple. This release must not claim broad Python, Java,
Maven, Gradle or model compatibility.

## Local implementation gate

Use one Python 3.11+ environment with `requirements-dev.txt` already installed:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m tools.ci_gate --root .
python -m tools.doctor --root .
git diff --check
git status --short
```

The pack does not create the environment or install dependencies: the runtime uses
only Python and its standard library, so `requirements.txt` holds no packages. The
development dependencies (the `jsonschema` oracle of the differential test, pytest)
are pinned with hashes in `requirements-dev.txt`; an approved company
mirror/wheelhouse remains external deployment evidence for them.

Moving from v0.1.1: `.skillsrc` is now a JSON object (the file keeps its name), not YAML. A YAML one is not read:
`ready`, `scan` and `init_skillsrc` stop with the keys to carry over (`limits`, `review`, `service_url_env`,
`paths.include`, `projection`) and the steps: rename it to `.skillsrc.yaml.bak`, run `init_skillsrc --write`, move the values in.

Linux uses the same gate:

```bash
export PYTHONDONTWRITEBYTECODE=1
python -m tools.ci_gate --root .
python -m tools.doctor --root .
git diff --check
```

The repository workflow `.github/workflows/portable.yml` checks Ubuntu 24.04 with
Python 3.11/3.12 and Windows with Python 3.12. `ci_gate` runs contract validation,
projection checks and pytest once; do not prepend a second full test run. A configured
workflow is not a successful Linux run: record the actual OS/runtime and gate result
before making a platform verification claim.

`.gitattributes` keeps text checkout bytes as LF on both platforms. Do not normalize
runtime bytes while hashing: the manifest and receipts continue to bind exact bytes.

## N/A for core pilot

- live company execution adapter;
- production deployment and rollback;
- real Zephyr tenant import/re-export;

These may receive independent evidence later. Their absence does not change the honest
current state: package implementation exists, but no exact tuple is yet verified.
