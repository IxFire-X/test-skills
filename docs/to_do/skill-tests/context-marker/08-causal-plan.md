# Context-marker Evaluator Injection Repair Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a context-marker FINAL evaluator brief provably deliver the canonical skill and local contract, and reject r5's mechanical output defects before semantic acceptance.

**Architecture:** Keep `protocol_contract_version: 1` and the existing scenario as the brief source of truth. Add a context-marker-v1-only manifest of canonical skill inputs, bind the canonical prompt snapshot to those paths and current digests, and strengthen deterministic schema/tests only where the rules are input-independent. Preserve input-dependent endpoint and paired fact-locator checks as fixture-aware semantic gates.

**Tech Stack:** Python 3.10+, pytest, Draft 2020-12 JSON Schema, Markdown campaign protocol.

## Global Constraints

- Work only in `D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills` and preserve every pre-existing dirty change.
- Treat `docs/to_do/skill-tests/context-marker/archive/r5/**` as immutable; never repair, replace, revalidate, or reuse it.
- Permanent artifacts belong only under `docs/to_do/`.
- Run evaluator acceptance sequentially through `1 → 3 → 5`; stop on the first protocol or semantic failure.
- Keep `protocol_contract_version: 1`, literal command `argv` arrays, absolute `cwd`, and the sole final `validate_artifact.py` command contract unchanged.
- Do not claim OS-observed reads. Delivery evidence is the exact manifest plus immutable prompt snapshot; application evidence is the targeted output plus deterministic and Sol semantic scoring.
- Preserve the existing pressure prompt/hash/evidence byte-for-byte; do not relabel it as evidence for the rewritten canonical prompt.

---

### Task 1: RED — capture the missing delivery contract and r5 mechanical defects

**Files:**
- Modify: `tests/test_skill_test_evidence.py`
- Modify: `tests/test_artifact_contracts.py`

**Interfaces:**
- Consumes: current `00-scenario.json`, canonical skill/reference files, and immutable `archive/r5/e/out/03/result.json`.
- Produces: failing tests for `required_skill_inputs`, canonical prompt binding, canonical IDs, inline provenance, and the fixture-specific ordered `/id` then `/quote` rule.

- [ ] **Step 1: Add the failing scenario delivery test**

Add a context-marker-v1 assertion equivalent to:

```python
expected = [
    "skills/context-marker/SKILL.md",
    "skills/context-marker/references/context-artifact-contract.md",
]
assert [item["path"] for item in scenario["required_skill_inputs"]] == expected
for item in scenario["required_skill_inputs"]:
    source = root / item["path"]
    assert item["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert item["path"] in scenario["canonical_prompt"]
assert "Read only artifacts/inputs/raw-content.json" not in scenario["canonical_prompt"]
```

- [ ] **Step 2: Add deterministic r5 regression tests**

Require `archive/r5/e/out/03/result.json` to fail the updated context-marker schema for noncanonical `REQ-ORDER-*` IDs and missing inline provenance. Separately parse `artifacts/inputs/raw-content.json` and assert that each requirement generated for this fixture carries, in order, `/order_change/facts/<n>/id` and `/order_change/facts/<n>/quote`, while `/order_change/endpoint — POST /orders` remains a semantic fixture assertion.

- [ ] **Step 3: Run RED**

Run:

```powershell
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' -m pytest tests/test_skill_test_evidence.py tests/test_artifact_contracts.py -q
```

Expected: FAIL because the scenario has no `required_skill_inputs`, its canonical prompt is raw-only, and r5 remains schema-valid.

---

### Task 2: GREEN — bind the canonical brief and strengthen input-independent validation

**Files:**
- Modify: `docs/to_do/skill-tests/context-marker/00-scenario.json`
- Modify: `docs/to_do/skill-tests/context-marker/PROTOCOL.md`
- Modify: `schemas/skill-test-evidence.schema.json`
- Modify: `schemas/context-marker-output.schema.json`
- Modify: `tests/test_skill_test_evidence.py`
- Modify: `tests/test_artifact_contracts.py`

**Interfaces:**
- Produces scenario field: `required_skill_inputs: [{"path": <repo-relative canonical path>, "sha256": <64 lowercase hex>}, ...]`.
- Preserves: pressure prompt/hash/evidence; `protocol_contract_version: 1`; final literal command capture.

- [ ] **Step 1: Implement the context-marker-v1-only manifest schema**

Allow `required_skill_inputs` on scenario documents and conditionally require it only when `skill_id == "context-marker"` and `protocol_contract_version == 1`. Keep other skill scenarios valid and unchanged. Constrain each entry to a safe repository-relative `skills/` path and lowercase SHA-256.

- [ ] **Step 2: Rewrite only the canonical prompt**

Make it explicitly direct the evaluator to read, in order, `skills/context-marker/SKILL.md`, `skills/context-marker/references/context-artifact-contract.md`, and then `artifacts/inputs/raw-content.json`. State that the raw allowlist limits task data, not the two canonical instruction files. Recompute only `prompt_sha256.canonical`; preserve `pressure_prompt` and `prompt_sha256.pressure` exactly.

- [ ] **Step 3: Strengthen only universal output-schema mechanics**

Require context-marker requirement IDs to match `^REQ-[0-9]{4}$`. Require source/warning strings that carry evidence to use the contract's `locator — faithful observation or gap` form. Do not universally require exactly two provenance entries or a specific endpoint; enforce those against this fixture in tests/scoring.

- [ ] **Step 4: Update protocol wording**

Replace the contradictory statement that every evaluator receives only `raw-content.json` with the exact canonical-input manifest rule for active FINAL runs. Explicitly mark existing RED/initial/pressure evidence as historical and unchanged.

- [ ] **Step 5: Run GREEN and static gates**

Run:

```powershell
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' -m pytest tests/test_skill_test_evidence.py tests/test_artifact_contracts.py -q
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' tools/validate_artifact.py schemas/skill-test-evidence.schema.json docs/to_do/skill-tests/context-marker/00-scenario.json
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' -m pytest tests -q
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' tools/contract_check.py --root . --full
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' tools/render_contract_docs.py --root . --check
& 'D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe' 'C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py' skills/context-marker
```

Expected: targeted and full tests pass; scenario/schema/contract/render/skill validation exits 0; pressure bytes are unchanged.

---

### Task 3: Review and adaptive evaluator acceptance

**Files:**
- Create only the next active FINAL evidence paths reserved by the scenario/protocol.
- Do not modify: `archive/r5/**` or historical RED/initial/pressure artifacts.

**Interfaces:**
- Consumes: reviewed Task 2 diff and the compiled canonical prompt bytes from `00-scenario.json`.
- Produces: one diagnostic FINAL result first, then at most three total results unless the stable final five-run gate is explicitly reached.

- [ ] **Step 1: Obtain fresh read-only Sol review of Task 2**

Require `ship`, `fix-first`, or `rethink`. Any fix invalidates the verdict and requires a new review.

- [ ] **Step 2: Run exactly one fresh diagnostic evaluator**

Pass the exact canonical prompt, verify both manifest paths/digests before spawn, and record every actual command as literal `argv` plus absolute `cwd`. Validate schema and fixture-aware semantics. Stop immediately on any failure; archive the failed run without repair or replacement.

- [ ] **Step 3: Expand only after success**

After the diagnostic run succeeds, run two more independent repetitions for three total. Run repetitions four and five only as the explicit stable final acceptance gate. Never reuse or replace a repetition.

- [ ] **Step 4: Complete evidence and acceptance**

Obtain semantic Sol scoring, complete 16-run metadata and all three scorecards, run the full verification suite, inspect the scoped diff/status, commit only owned paths, and obtain a final fresh Sol `ship` verdict.
