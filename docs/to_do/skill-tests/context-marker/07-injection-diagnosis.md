# Context-marker r5 injection/read-path diagnosis

## Scope and conclusion

This is a diagnosis only. `archive/r5` is immutable; no evaluator was spawned and
no skill, harness, test, schema, protocol, or archive file was changed.

**Root cause:** the final-v1 controller assembled the evaluator brief from the
scenario's short `canonical_prompt`, rather than from an explicit skill-and-
reference read contract. The delivered brief said `Read only
artifacts/inputs/raw-content.json` and named neither
`skills/context-marker/SKILL.md` nor its local contract. It therefore excluded
the two files whose recipe requires canonical `REQ-0001` IDs, identity-then-
claim locators, inline provenance for strings, and preservation of standalone
endpoint metadata. The post-run archive snapshot proves which skill/reference
bytes were current; it is not evidence that those bytes were injected into, or
read by, the evaluator.

This is not a stale-snapshot issue. The current and r5-pinned SHA-256 values are
identical:

```text
SKILL.md                         af57640c881f222dc98ab2157d8ebac20fa31515523684fa34dfde15766c0aa4
context-artifact-contract.md     8168791a511103a16ec2a2b37e26eba50df64ad0c680b7240e47b8708b78a1be
```

## Exact r5 symptom

`archive/r5/e/out/03/result.json` is schema-valid but semantically invalid:

- It emits `REQ-ORDER-001` through `REQ-ORDER-003`, not the contract's
  `REQ-0001`, `REQ-0002`, ... sequence.
- Each requirement retains only `/quote`, omitting the required preceding
  `/id` locator.
- It drops the standalone `/order_change/endpoint` fact (`POST /orders`).
- Its two `sources` and four `warnings` have no required
  `<locator> — <faithful observation or gap>` inline provenance.

Direct evidence: [r5 rep-03 output](archive/r5/e/out/03/result.json) lines
8--41; [raw input](artifacts/inputs/raw-content.json) line 1;
[canonical skill](../../../../skills/context-marker/SKILL.md) lines 8, 12--17,
21; and [canonical contract](../../../../skills/context-marker/references/context-artifact-contract.md)
lines 7--12. The archive's own rejection records the same conclusion in
[REJECTED.md](archive/r5/REJECTED.md) lines 3--11.

## Tight feedback loop (executed, intentionally red)

This one unattended, read-only command parses immutable r5 rep-03, compares it
with the current/pinned skill and reference bytes, and exits nonzero *only when
the exact missing skill/reference-application symptoms are present*. It does
not treat schema validity as success and writes nothing.

```powershell
$py='D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe'; & $py -c @'
from pathlib import Path
import hashlib, json, re, sys
root = Path(r"D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills")
r5 = root / "docs/to_do/skill-tests/context-marker/archive/r5"
out = json.loads((r5 / "e/out/03/result.json").read_text(encoding="utf-8"))
prompt = (r5 / "e/pro/03/prompt.txt").read_text(encoding="utf-8")
current_skill = root / "skills/context-marker/SKILL.md"
snapshot_skill = r5 / "skill-snapshot/SKILL.md"
current_contract = root / "skills/context-marker/references/context-artifact-contract.md"
snapshot_contract = r5 / "skill-snapshot/references/context-artifact-contract.md"
requirements = out["artifacts"]["analytics_documentation"]["requirements"]
sources = out["artifacts"]["source_code_and_diff"]["sources"]
warnings = out["warnings"]
failures = []
if not all(re.fullmatch(r"REQ-\d{4}", item["id"]) for item in requirements): failures.append("noncanonical_requirement_ids")
for index, item in enumerate(requirements):
    if item["provenance"] != [f"/order_change/facts/{index}/id", f"/order_change/facts/{index}/quote"]: failures.append(f"requirement_{index}_identity_then_quote_locator_missing")
if "/order_change/endpoint — POST /orders" not in sources: failures.append("endpoint_source_observation_dropped")
if not all(" — " in item for item in sources + warnings): failures.append("inline_source_or_warning_provenance_missing")
if "skills/context-marker/SKILL.md" not in prompt: failures.append("brief_omits_skill_read_path")
if "references/context-artifact-contract.md" not in prompt: failures.append("brief_omits_contract_read_path")
if current_skill.read_bytes() != snapshot_skill.read_bytes(): failures.append("r5_skill_snapshot_differs_from_current")
if current_contract.read_bytes() != snapshot_contract.read_bytes(): failures.append("r5_contract_snapshot_differs_from_current")
print(json.dumps({"archive":"r5/e/out/03/result.json","semantic_failures":failures,"prompt_sha256":hashlib.sha256(prompt.encode()).hexdigest()}, sort_keys=True))
sys.exit(1 if failures else 0)
'@; exit $LASTEXITCODE
```

Actual result: exit `1` (the intended red verdict).

```json
{"archive":"r5/e/out/03/result.json","prompt_sha256":"2277516492736557a03d3b3a5f296bf4920db62b007038c8d205313c9e355cf4","semantic_failures":["noncanonical_requirement_ids","requirement_0_identity_then_quote_locator_missing","requirement_1_identity_then_quote_locator_missing","requirement_2_identity_then_quote_locator_missing","endpoint_source_observation_dropped","inline_source_or_warning_provenance_missing","brief_omits_skill_read_path","brief_omits_contract_read_path"]}
```

The smallest artifact-level reproducer is consequently just these immutable
four files: `r5/e/pro/03/prompt.txt`, `r5/e/out/03/result.json`,
`r5/skill-snapshot/SKILL.md`, and
`r5/skill-snapshot/references/context-artifact-contract.md`. Removing any one
of prompt, output, or contract removes an asserted part of the symptom; the
current files merely demonstrate that the snapshot is not stale.

## Actual injection/read and evaluator-brief path

```text
00-scenario.json.canonical_prompt
  SHA-256 2277516492736557a03d3b3a5f296bf4920db62b007038c8d205313c9e355cf4
       |
       +--> r5/e/pro/01..04/prompt.txt (identical delivered evaluator brief)
                |
                +--> /root/context_final_controller/final_green_v1_rep_0N
                       -> fresh sol_advisor_terra_implementer
                       -> read only raw-content.json (as the brief directs)
                       -> r5/e/out/0N/result.json
                       -> validate_artifact.py (schema only; exit 0)

skills/context-marker/SKILL.md + references/context-artifact-contract.md
       |
       +--> copied after/for archive audit as r5/skill-snapshot/**
            (manifest records `kind: copied_skill_snapshot`)
       X--> no path/reference/read attestation in prompt, run.json, or
            observation.json; therefore no evidenced delivery edge to evaluator
```

Evidence for each edge:

- [Scenario](00-scenario.json) line 1 supplies the canonical text, single raw
  allowlist, `fork_turns: "none"`, and the same prompt hash. It does not name
  either canonical file as evaluator-readable input.
- The archived [prompt](archive/r5/e/pro/03/prompt.txt) lines 1--3 is byte-bound
  to that hash by [run record](archive/r5/e/pro/03/run.json) line 1. It says
  `Read only artifacts/inputs/raw-content.json`; neither canonical path appears.
- The run record identifies the actual controller child task and only records
  the role check plus validator argv/cwd. The matching
  [observation](archive/r5/e/pro/03/observation.json) line 1 records only
  validator success and output hash. Neither has an opened/read skill/reference
  command or resolved path.
- The campaign [PROTOCOL.md](PROTOCOL.md) line 3 independently says every
  evaluator receives only `raw-content.json`; its v1 section (lines 7--11)
  preserves literal executed argv/cwd but has no required skill/reference
  injection or read field.
- [r5 manifest](archive/r5/manifest.json) lines 18--19 calls both files
  `copied_skill_snapshot`; [r5 REJECTED.md](archive/r5/REJECTED.md) lines
  13--14 says they are copies, not moved evidence. That is audit provenance,
  not delivery/read provenance.
- The current evidence schema permits only `skill_present: boolean` for a run,
  not a resolved skill/reference path, digest, or read event
  ([schema](../../../../schemas/skill-test-evidence.schema.json) lines 91--93).
  The semantic test checks that boolean and raw allowlist
  ([test](../../../../tests/test_skill_test_evidence.py) lines 257--273), while
  final v1 command checks validate only literal command capture
  ([test](../../../../tests/test_skill_test_evidence.py) lines 77--110).
- No versioned controller/brief-assembler implementation is present under the
  repository's `tools/` or `tests/`; the only r5 controller identifier is the
  archived external task path above. The immutable prompt is therefore the
  reproducible assembly boundary. Historical hidden host injection cannot be
  replayed from repository artifacts, but it cannot cure the explicit
  contradictory `Read only raw-content.json` direction.

## Hypotheses, probes, and results

1. **Confirmed — controller brief omitted the canonical read contract.** If this
   is the cause, the delivered prompt will omit both paths and the output will
   violate rules unique to the local contract. The red loop found both omissions
   and all four unique semantic failures. This is the root cause.
2. **Falsified — r5 used stale or different skill/reference bytes.** Prediction:
   current and pinned file digests differ. Both SHA-256 pairs match exactly (see
   above and manifest lines 18--19).
3. **Confirmed as a detection gap, not the primary behavior cause — schema
   validation is too weak to detect the contract recipe.** The actual command
   below exits `0` although rep-03 is semantically defective:

   ```powershell
   D:\AI-Projects\.tools\skill-audit-venv\Scripts\python.exe D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\tools\validate_artifact.py D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\schemas\context-marker-output.schema.json D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills\docs\to_do\skill-tests\context-marker\archive\r5\e\out\03\result.json
   # {"errors":[],"status":"valid"}
   ```

   The schema accepts broad `^REQ-[A-Za-z0-9_.:-]+$` IDs and one-or-more
   arbitrary provenance strings ([schema](../../../../schemas/context-marker-output.schema.json)
   lines 29--37), so it cannot replace a semantic contract assertion.
4. **Unsupported/falsified to the available artifact boundary — an implicit
   skill injection caused a normal skill read which the model simply ignored.**
   Prediction: a brief/reference/read record or command trail identifies those
   paths. There is none; all three final `run.json` command lists contain role
   integrity and validator only. The archive cannot prove what an unrecorded
   host system prompt contained, but the recorded exclusive raw-input direction
   is enough to rule out compliant file reading under this brief.
5. **Falsified — the current contract is ambiguous about these fields.** Its
   lines 8--11 explicitly require both fact locators in order, inline string
   provenance, source-observation treatment of endpoint metadata, and canonical
   sequential IDs. The result violates each condition directly.

## Smallest future TDD seam (no fix made here)

Add a deterministic evaluator-brief compilation seam, not a new wording-only
test: a function that accepts the scenario and resolved canonical skill root,
emits the exact evaluator brief plus a manifest of every mandatory read path and
SHA-256. Its first red fixture should be this r5-shaped scenario/prompt and
assert that compilation fails unless it includes:

1. `skills/context-marker/SKILL.md`;
2. `skills/context-marker/references/context-artifact-contract.md`; and
3. a non-conflicting allowlist that permits those required reads in addition to
   `raw-content.json`.

The next seam-level assertion should reject a completed run without recorded
resolved path+digest/read evidence for both files. That catches the actual
injection/read failure before an evaluator is spent; it preserves protocol v1's
literal argv arrays and absolute cwd unchanged.

## Verification and preservation

- Executed the red diagnostic command above: exit `1`, eight exact symptom
  labels, no writes.
- Executed the repository validator command above: exit `0` with
  `{"errors":[],"status":"valid"}`, proving it is not the required semantic
  loop.
- Inspected the r5 prompt, outputs, observations, run records, manifest,
  canonical/current snapshot hashes, scenario, protocol, schema, and evidence
  validator tests. Read all continuation-linked plans: master, Plan 1 core,
  Plan 2 skills/adapters, and Plan 3 E2E.
- No fresh evaluator repetition, archive repair, or non-owned file modification
  occurred.
