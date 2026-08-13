# Test Classification Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Отделить наблюдаемые функциональные сценарии от существующих unit/integration/e2e test symbols так, чтобы технический тест никогда сам по себе не создавал canonical/Zephyr-кейс.

**Architecture:** Детерминированный `source-inventory` строит два snapshot: реестр test files/symbols и разрешённые источники поведения. Context-marker выдаёт очищенный `managed_behavior_context`, classifier присваивает каждому test symbol один scope, отдельный reviewer подтверждает полное покрытие реестра, а `tc-generator` физически получает только behavior-context.

**Tech Stack:** Python 3.10+ standard library, `jsonschema>=4.23,<5`, stdlib `ast`, bounded Java source scanner из существующего runner, `unittest`, JSON Schema Draft 2020-12, Markdown skill contracts.

## Коротко по-русски

- Функциональные кейсы строятся из поведения продукта, а не из количества существующих тестов.
- LLM выбирает только `unit | integration | e2e | unknown` и пишет краткое обоснование.
- Origin, digest, ID, порядок, полнота и допустимые пути вычисляет код.
- Unit/unknown сохраняются как техническое evidence, но не создают и не закрывают Zephyr-кейс.
- Количество кейсов нигде не настраивается числом.

## Global Constraints

- Work only in `D:\AI-Projects\test-skills\.worktrees\adaptive-test-case-granularity` on `codex/adaptive-test-case-granularity`.
- Normative design: `docs/superpowers/specs/2026-08-13-test-classification-evidence-design.md`, SHA-256 `C9C526BC997340B4E71A5108C1C730D75B518F3E3A029E92B82889BEDAFD218E`.
- Phase 1 pipeline version is exactly `4.0`; `source-inventory`, `test-classifier`, and `test-classifier-reviewer` envelopes are `1.0.0`; context-marker is `4.0.0`; unchanged canonical/generator/reviewer/automation artifacts remain V3 where Pipeline 4.0 explicitly routes them.
- Do not modify `.skillsrc.example`, `schemas/skillsrc.schema.json`, `tools/scan_project.py`, or the automatic-discovery implementation. Consume only normalized selected-module `paths.tests`, `paths.source`, and `feature_sources`.
- Add no dependency and start no project process while building inventories. Never import project code.
- No test-count target, cap, quota, minimum, maximum, per-domain setting, filename-only scope promotion, or silent normalization.
- Inventory and classification IDs/digests are lowercase SHA-256; arrays use physical canonical order and consumers reject rather than sort.
- Raw secrets and supplied input contents never enter snapshots or diagnostics; snapshots retain safe IDs, locations, and digests only.
- Every task follows RED → smallest GREEN → focused review → commit. Run the full repository suite only once in Task 7.
- Existing uncommitted edits in `skills/tc-generator/SKILL.md`, `skills/tc-generator/references/case-generation-contract.md`, and `tests/test_skill_contracts_v3.py` belong to Task 1; preserve them.

## Locked Phase-1 carriers

`source-inventory`:

```json
{
  "schema_version": "1.0.0",
  "stage": "source-inventory",
  "artifacts": {
    "technical_test_inventory": {"module_id": "backend", "test_roots": [], "files": [], "symbols": []},
    "technical_test_inventory_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "authorized_behavior_sources": {"module_id": "backend", "sources": []},
    "authorized_behavior_sources_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000"
  },
  "warnings": []
}
```

The digest is a sibling and hashes only the corresponding bare object; it never hashes itself or the envelope.
The all-zero digests above are shape-only notation. Every checked-in fixture and every test factory must compute the real digest from its exact bare object with `_digest`; copying the zero value into a fixture is invalid.

`test-classifier` stores exact inventory digest plus ordered `classifications[]`. `test-classifier-reviewer` stores the inventory digest, bare-classification digest, complete canonical `reviewed_symbol_pairs[]`, verdict, and findings.

An accepted review selects one closed `effective-technical-evidence` carrier containing the three upstream digests, sibling `effective_technical_evidence_sha256`, and exact frozen `files[]`, `symbols[]`, and `classifications[]`. Rework produces no effective carrier.

`managed_behavior_context` is exactly:

```json
{
  "authorized_behavior_sources_sha256": "sha256:0000000000000000000000000000000000000000000000000000000000000000",
  "requirements": [],
  "product_sources": [],
  "requirement_sources": []
}
```

Canonical requirement objects stay unchanged; links live in parallel `requirement_sources` rows.

## File Structure and Ownership

```text
tools/test_classification.py                         sole public inventory/classification policy facade
schemas/test-symbol-registry.schema.json             shared IDs/digests/locator definitions
schemas/source-inventory-output.schema.json          mechanical snapshot envelope
schemas/test-classifier-output.schema.json           candidate classification envelope
schemas/test-classifier-reviewer-output.schema.json  independent review envelope
schemas/context-marker-output.schema.json            V4 managed behavior carrier
skills/test-classifier/                               small LLM classification contract
skills/test-classifier-reviewer/                      independent scope review contract
tests/test_source_inventory.py                        static discovery, paths, IDs, digests
tests/test_test_classification.py                     candidate/review semantic validation
tests/test_behavior_context_v4.py                     product-source gate and generator isolation
tests/test_pipeline_v4.py                             exact Pipeline 4.0 routes
```

---

### Task 1: Lock Adaptive Scenario Granularity

**Files:**

- Modify: `skills/tc-generator/SKILL.md`
- Modify: `skills/tc-generator/references/case-generation-contract.md`
- Modify: `tests/test_skill_contracts_v3.py`
- Create: `evals/adaptive-case-granularity/scenarios.json`
- Create: `evals/adaptive-case-granularity/rubric.md`

**Interfaces:**

- Produces the exact scenario key `setup/role + initial state + input partition/branch condition + primary action or cohesive dependent action chain + terminal outcome`.
- Forbids numeric targets and converts neither assertions nor coverage records into cases.

- [ ] **Step 1: Preserve and inspect the existing RED/GREEN patch**

Run:

```powershell
git diff -- skills/tc-generator/SKILL.md skills/tc-generator/references/case-generation-contract.md tests/test_skill_contracts_v3.py
```

Confirm the diff contains all scenario-key components, exact-one representation, deduplication, and no numeric target.

- [ ] **Step 2: Run the focused behavior contract**

Run: `python -m unittest tests.test_skill_contracts_v3.SkillContractsV3Tests.test_generator_uses_adaptive_scenario_keys -v`

Expected: PASS. If the method name differs in the present WIP, rename it exactly to `test_generator_uses_adaptive_scenario_keys` before proceeding.

- [ ] **Step 3: Add the assertion/branch boundary control and generator evals**

```python
def test_generator_does_not_count_assertions_or_accept_numeric_targets(self):
    text = (read("skills/tc-generator/SKILL.md") + read("skills/tc-generator/references/case-generation-contract.md")).lower()
    for phrase in ("an assertion is not a case", "a coverage record is not a case", "input partition/branch condition"):
        self.assertIn(phrase, text)
    for forbidden in ("minimum case", "maximum case", "per-domain count", "numeric target"):
        self.assertNotIn("accept " + forbidden, text)
```

The generator eval manifest has three self-contained inputs with canonical-shaped requirements: one behavior with twenty assertions expects exactly one case, two independently executable branches expect exactly two cases, and duplicated endpoint/permission/response evidence from one control flow expects exactly one case. Its rubric scores the exact scenario key, expected case count, nested assertion preservation, and absence of numeric-target reasoning. These are generator-output evals; they do not use the classifier's no-case rubric.

- [ ] **Step 4: Run GREEN and validate the skill**

```powershell
python -m unittest tests.test_skill_contracts_v3 -v
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\tc-generator
```

Expected: all tests PASS; validator prints `Skill is valid!`.

Run the three generator scenarios in the available fresh-context behavior harness. If that harness is unavailable, record exactly `UNVERIFIED — harness unavailable`; the checked-in self-contained inputs, rubric, and static contract tests remain mandatory.

- [ ] **Step 5: Commit**

```powershell
git add skills/tc-generator/SKILL.md skills/tc-generator/references/case-generation-contract.md tests/test_skill_contracts_v3.py evals/adaptive-case-granularity
git commit -m "feat: define adaptive scenario boundaries"
```

### Task 2: Mechanical Source Inventories

**Files:**

- Create: `schemas/source-inventory-output.schema.json`
- Create: `schemas/test-symbol-registry.schema.json`
- Create: `tools/test_classification.py`
- Create: `tests/test_source_inventory.py`
- Create: `tests/fixtures/test-classification/project/tests/test_sample.py`
- Create: `tests/fixtures/test-classification/project/src/service.py`
- Create: `tests/fixtures/test-classification/project/.skillsrc`
- Create: `tests/fixtures/test-classification/requirement.txt`
- Create: `tests/fixtures/stages/v1/source-inventory.json`

**Interfaces:**

```python
@dataclass(frozen=True)
class SuppliedInput:
    source_id: str
    content: bytes

@dataclass(frozen=True)
class SourceInventories:
    technical_test_inventory: Mapping[str, Any]
    technical_test_inventory_sha256: str
    authorized_behavior_sources: Mapping[str, Any]
    authorized_behavior_sources_sha256: str
```

`TestClassificationError(ValueError)` exposes a getter-only recursively immutable `diagnostics: tuple[Mapping[str, str], ...]`.

`build_source_inventories(project_root: Path, skillsrc: Mapping[str, Any], module_id: str | None, supplied_inputs: Sequence[SuppliedInput]) -> SourceInventories` is the sole inventory entry point.

CLI smoke: `python tools/test_classification.py inventory --project tests/fixtures/test-classification/project --skillsrc tests/fixtures/test-classification/project/.skillsrc --module backend --supplied-input REQ-synthetic=tests/fixtures/test-classification/requirement.txt`. Optional `--output` is create-only; without it stdout is the compact artifact. Input/validation errors return exit 2 without traceback or source content.

- `file_id = "FILE-" + sha256(portable_path.encode("utf-8")).hexdigest()`.
- `symbol_id = "SYMBOL-" + sha256(canonical_locator_bytes).hexdigest()`.
- `source_id` for product files is `SOURCE-` plus SHA-256 of `b"product_file\0" + portable_path_utf8`.
- Canonical bytes are sorted-key compact UTF-8 JSON with arrays unchanged and `allow_nan=False`.
- Python static support is exactly files `test_*.py | *_test.py`, module functions/async functions whose name starts `test`, and `test*` methods in `Test*` classes or classes inheriting `unittest.TestCase`; parametrization remains one symbol per owning locator.
- Java static support is exactly methods annotated `@Test`, `@ParameterizedTest`, `@RepeatedTest`, `@TestFactory`, or `@TestTemplate` in `*Test.java | *Tests.java | *TestCase.java`; ambiguous overloads or nested ownership that the closed locator cannot express stop inventory.
- Overlapping declared roots deduplicate the same resolved file. An empty roots list is valid; a declared missing root, symlink escape, parse failure, or undecodable supported file is an error.
- Authorized product sources are confined to selected-module `paths.source` and `feature_sources` and to text suffixes `.py,.java,.kt,.go,.ts,.tsx,.js,.jsx,.md,.rst,.txt,.yaml,.yml,.json,.toml,.sql,.graphql,.proto,.feature,.html,.css`. Exclude declared test roots; supported test filename patterns; basenames matching `.env` or `.env.*`; suffixes `.pem,.key,.p12,.pfx,.crt`; and path segments `.git,.hg,.svn,__pycache__,.pytest_cache,.mypy_cache,.tox,.venv,venv,node_modules,dist,build,target,out,vendor` before hashing.

- [ ] **Step 1: Write RED inventory tests**

In `setUp`, create `TemporaryDirectory`, assign `self.root`, and write the synthetic files. Define test-local `self.skillsrc(test_roots=("tests",), language="python")`; for Java rows rewrite the same temporary files and pass `language="java"`. The overload row writes two same-name `@Test` methods in one `SampleTest.java`.

```python
def test_one_file_can_own_multiple_symbols(self):
    result = build_source_inventories(self.root, self.skillsrc(), "backend", ())
    self.assertEqual(1, len(result.technical_test_inventory["files"]))
    self.assertEqual(3, len(result.technical_test_inventory["symbols"]))

def test_no_test_roots_is_valid_empty_inventory(self):
    value = build_source_inventories(self.root, self.skillsrc(test_roots=()), "backend", ())
    self.assertEqual([], value.technical_test_inventory["files"])
    self.assertEqual([], value.technical_test_inventory["symbols"])

def test_unrepresentable_supported_test_is_not_silently_omitted(self):
    self.write_java_overload()
    with self.assertRaises(TestClassificationError) as raised:
        build_source_inventories(self.root, self.skillsrc(language="java"), "backend", ())
    self.assertEqual("INVENTORY_UNSUPPORTED_LOCATOR", raised.exception.diagnostics[0]["code"])
```

Add table rows for Python module function, async function, class method, parametrized function as one locator, Java `@Test`/`@ParameterizedTest`, helper exclusion, overlapping roots, unsafe path, missing root, symlink escape, duplicate supplied IDs, and exact digest/order.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_source_inventory -v`

Expected: FAIL with `ModuleNotFoundError: tools.test_classification`.

- [ ] **Step 3: Implement the smallest static scanners and immutable result**

```python
def _digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()

def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value
```

Use `skillsrc_manifest.normalize_skillsrc/select_module/resolve_module_root`; scan declared roots only. Reuse the existing locator shapes and bounded Java masking logic without importing or executing project code.

- [ ] **Step 4: Run GREEN and schema validation**

```powershell
python -m unittest tests.test_source_inventory -v
python tools\validate_artifact.py schemas\source-inventory-output.schema.json tests\fixtures\stages\v1\source-inventory.json
```

Expected: PASS and `status: valid`.

- [ ] **Step 5: Commit**

```powershell
git add schemas/source-inventory-output.schema.json schemas/test-symbol-registry.schema.json tools/test_classification.py tests/test_source_inventory.py tests/fixtures/test-classification tests/fixtures/stages/v1/source-inventory.json
git commit -m "feat: snapshot project test and behavior sources"
```

### Task 3: Classification and Independent Review

**Files:**

- Create: `schemas/test-classifier-output.schema.json`
- Create: `schemas/test-classifier-reviewer-output.schema.json`
- Create: `schemas/effective-technical-evidence.schema.json`
- Modify: `tools/test_classification.py`
- Create: `tests/test_test_classification.py`
- Create: `tests/fixtures/stages/v1/test-classifier.json`
- Create: `tests/fixtures/stages/v1/test-classifier-reviewer-accepted.json`
- Create: `tests/fixtures/stages/v1/test-classifier-reviewer-rework.json`
- Create: `tests/fixtures/stages/v1/effective-technical-evidence.json`

**Interfaces:**

`validate_technical_test_evidence(inventory: Mapping[str, Any], classification: Mapping[str, Any], classification_review: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]], project_root: Path) -> tuple[Mapping[str, str], ...]` returns deterministic diagnostics.

`select_effective_technical_evidence(inventory: Mapping[str, Any], classification: Mapping[str, Any], classification_review: Mapping[str, Any], requirements: Sequence[Mapping[str, Any]], project_root: Path) -> Mapping[str, Any]` returns the selected immutable carrier.

Both functions receive the three full closed stage envelopes. They extract `artifacts.technical_test_inventory`, `artifacts.classification`, and `artifacts.classification_review` only after structural validation. `technical_test_classification_sha256` hashes the bare `artifacts.classification` object; `technical_test_review_sha256` hashes the bare `artifacts.classification_review` object. The selected carrier itself is bare, not another stage envelope.

CLI smoke: `python tools/test_classification.py select --project tests/fixtures/test-classification/project --inventory tests/fixtures/stages/v1/source-inventory.json --classification tests/fixtures/stages/v1/test-classifier.json --review tests/fixtures/stages/v1/test-classifier-reviewer-accepted.json --context tests/fixtures/stages/v4/context-marker.json`; optional `--output` is create-only, while rework exits 2 and creates no effective carrier.

`select_effective_technical_evidence` raises immutable `TestClassificationError` unless verdict is `ПРИНЯТО` and validation is empty. Its result contains exact inventory/classification/review digests plus frozen files, symbols, and classifications.

- [ ] **Step 1: Write table-driven RED tests**

Define test-local `self.inventory()`, `self.classification(scope="integration")`, and `self.review(candidate, verdict="ПРИНЯТО")` factories from the three checked-in V1 fixtures; the review factory recomputes the candidate digest. Define `self.diagnostics(candidate, review)` as the exact call to `validate_technical_test_evidence` and `self.assert_diagnostic(code, path, candidate, review)` as an equality check on one returned row.

```python
def test_acceptance_requires_every_pair_in_canonical_order(self):
    candidate = self.classification()
    review = self.review(candidate)
    review["artifacts"]["classification_review"]["reviewed_symbol_pairs"].pop()
    self.assert_diagnostic("CLASSIFICATION_REVIEW_COVERAGE", "/artifacts/classification_review/reviewed_symbol_pairs", candidate, review)

def test_unknown_is_valid_but_cannot_be_upgraded(self):
    candidate = self.classification(scope="unknown")
    review = self.review(candidate)
    self.assertEqual((), self.diagnostics(candidate, review))
    selected = select_effective_technical_evidence(self.inventory(), candidate, review, self.requirements, self.root)
    self.assertEqual("unknown", selected["classifications"][0]["test_scope"])
```

Cover all four scopes, missing/extra/duplicate/reordered rows, foreign/unsorted requirement links, invalid provenance line ownership, wrong inventory/classification digest, file drift, accepted+findings, rework-without-findings, incomplete pair review, recursively immutable results/errors, and strict legacy rejection.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_test_classification -v`

Expected: FAIL because schemas and validators do not exist.

- [ ] **Step 3: Implement exact coverage/digest/review validation**

```python
inventory_rows = inventory["artifacts"]["technical_test_inventory"]
candidate_rows = classification["artifacts"]["classification"]
expected_pairs = tuple((row["file_id"], row["symbol_id"]) for row in inventory_rows["symbols"])
actual_pairs = tuple((row["file_id"], row["symbol_id"]) for row in candidate_rows["classifications"])
if actual_pairs != expected_pairs:
    diagnostics.append(_diag("/artifacts/classification/classifications", "CLASSIFICATION_PAIR_COVERAGE"))
```

Validate shape first, then inventory digest, pair coverage/order, requirement/provenance links, current file bytes, review digest/pair coverage, and verdict conditions. Never infer or rewrite scope.

- [ ] **Step 4: Run GREEN**

```powershell
python -m unittest tests.test_test_classification -v
python tools\validate_artifact.py schemas\test-classifier-output.schema.json tests\fixtures\stages\v1\test-classifier.json
python tools\validate_artifact.py schemas\test-classifier-reviewer-output.schema.json tests\fixtures\stages\v1\test-classifier-reviewer-accepted.json
python tools\validate_artifact.py schemas\effective-technical-evidence.schema.json tests\fixtures\stages\v1\effective-technical-evidence.json
```

- [ ] **Step 5: Commit**

```powershell
git add schemas/test-classifier-output.schema.json schemas/test-classifier-reviewer-output.schema.json schemas/effective-technical-evidence.schema.json tools/test_classification.py tests/test_test_classification.py tests/fixtures/stages/v1
git commit -m "feat: validate reviewed test classifications"
```

### Task 4: Managed Behavior Context and Generator Isolation

**Files:**

- Modify: `schemas/context-marker-output.schema.json`
- Modify: `tools/test_classification.py`
- Modify: `skills/context-marker/SKILL.md`
- Modify: `skills/context-marker/references/context-artifact-contract.md`
- Modify: `skills/tc-generator/SKILL.md`
- Modify: `skills/tc-generator/references/case-generation-contract.md`
- Create: `tests/test_behavior_context_v4.py`
- Create: `tests/fixtures/stages/v4/context-marker.json`
- Modify: `tests/test_v3_stage_schemas.py`

**Interfaces:**

`validate_managed_behavior_context(behavior_context: Mapping[str, Any], authorized_behavior_sources: Mapping[str, Any], test_inventory: Mapping[str, Any], project_root: Path) -> tuple[Mapping[str, str], ...]` validates the exact authorized-source graph and current bytes.

CLI smoke: `python tools/test_classification.py validate-context --project tests/fixtures/test-classification/project --inventory tests/fixtures/stages/v1/source-inventory.json --context tests/fixtures/stages/v4/context-marker.json`; it performs no writes and prints one compact safe validation result.

- [ ] **Step 1: Write RED source-gate tests**

Define test-local `self.behavior_context(source_id)`, `self.authorized_sources()`, and `self.inventory_with_unit_symbols(count)` from the checked-in V4/V1 fixtures. `self.validate(context, inventory)` calls `validate_managed_behavior_context(context, self.authorized_sources(), inventory, self.root)`.

```python
def test_test_only_fact_cannot_originate_requirement(self):
    context = self.behavior_context(source_id="SOURCE-test-file")
    self.assertEqual("BEHAVIOR_TEST_SOURCE_FORBIDDEN", self.validate(context, self.inventory_with_unit_symbols(1))[0]["code"])

def test_ten_unit_symbols_do_not_multiply_one_behavior(self):
    context = self.behavior_context(source_id="SOURCE-product-file")
    inventory = self.inventory_with_unit_symbols(10)
    self.assertEqual(1, len(context["requirements"]))
    self.assertEqual((), self.validate(context, inventory))
```

Add invented path, relabeled test path, wrong supplied-input digest, product-file drift, and missing/foreign/reordered source links. These tests exercise only mechanical provenance; they do not claim to execute semantic scenario generation.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_behavior_context_v4 -v`

Expected: FAIL on the old V3 context shape and absent validator.

- [ ] **Step 3: Implement V4 context schema and provenance validation**

Require `authorized_behavior_sources_sha256`, unchanged canonical requirements, closed product source rows with mandatory digest, and parallel ordered `requirement_sources`. Match every row to the authorized snapshot and current bytes.

- [ ] **Step 4: Make generator input isolation explicit and run GREEN**

The skill must state exactly:

```text
Consume only artifacts.managed_behavior_context. Reject raw_content,
source_code_and_diff, technical_test_inventory, test source text, and
technical_test_classification as generator inputs.
```

Run:

```powershell
python -m unittest tests.test_behavior_context_v4 tests.test_v3_stage_schemas tests.test_skill_contracts_v3 -v
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\context-marker
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\tc-generator
```

- [ ] **Step 5: Commit**

```powershell
git add schemas/context-marker-output.schema.json tools/test_classification.py skills/context-marker skills/tc-generator tests/test_behavior_context_v4.py tests/test_v3_stage_schemas.py tests/test_skill_contracts_v3.py tests/fixtures/stages/v4
git commit -m "feat: isolate managed behavior from test inventory"
```

### Task 5: Classifier Skills and Behavior Evals

**Files:**

- Create: `skills/test-classifier/SKILL.md`
- Create: `skills/test-classifier/references/classification-contract.md`
- Create: `skills/test-classifier-reviewer/SKILL.md`
- Create: `skills/test-classifier-reviewer/references/review-contract.md`
- Create: `evals/test-classification/scenarios.json`
- Create: `evals/test-classification/rubric.md`
- Modify: `tests/test_skill_contracts_v3.py`

**Interfaces:**

- Classifier does exactly scope + provenance + rationale for every pair.
- Reviewer returns exact digests, complete ordered pairs, verdict, and findings; it never auto-fixes.

- [ ] **Step 1: Add RED static/behavior contract tests**

```python
def test_classifier_has_only_the_small_semantic_contract(self):
    text = read("skills/test-classifier/SKILL.md").lower()
    for value in ("unit", "integration", "e2e", "unknown", "provenance", "rationale"):
        self.assertIn(value, text)
    for forbidden in ("case count", "implementation_origin", "auto-fix", "filename implies"):
        self.assertNotIn(forbidden, text)
```

The classifier scenarios must include internal helper unit, database integration, HTTP integration, complete UI journey, insufficient evidence, misleading filename, generated-unit pressure, and prompt pressure to create one case per test.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_skill_contracts_v3 -v`

Expected: FAIL because the two skills are absent.

- [ ] **Step 3: Write minimal skills, contracts, and self-contained eval inputs**

Every classifier scenario includes inline synthetic inventory/source snippets, expected scope/pair coverage, and a rubric item forbidding case creation. Do not include workplace text or depend on external files.

- [ ] **Step 4: Run GREEN and skill validation**

```powershell
python -m unittest tests.test_skill_contracts_v3 -v
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\test-classifier
python C:\Users\User\.codex\skills\.system\skill-creator\scripts\quick_validate.py skills\test-classifier-reviewer
```

If no fresh-context behavior harness is available, record exactly `UNVERIFIED — harness unavailable`; static contracts and synthetic validators remain mandatory.

- [ ] **Step 5: Commit**

```powershell
git add skills/test-classifier skills/test-classifier-reviewer evals/test-classification tests/test_skill_contracts_v3.py
git commit -m "feat: add reviewed test classification skills"
```

### Task 6: Pipeline 4.0 Registry, Orchestration Guidance, and Docs

**Files:**

- Modify: `contracts/pipeline.json`
- Modify: `schemas/pipeline.schema.json`
- Modify: `tools/contract_check.py`
- Modify: `tools/doctor.py`
- Modify: `tools/render_contract_docs.py`
- Modify: `skills/orchestrate/SKILL.md`
- Modify: `skills/orchestrate/references/orchestration-contract.md`
- Create: `tests/test_pipeline_v4.py`
- Modify: `tests/test_orchestration_v3.py`
- Modify: `tests/test_documentation_v3.py`
- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `HOW-IT-WORKS.md`
- Regenerate: `CONTRACTS.md`
- Regenerate: `PIPELINE.md`

**Interfaces:**

Exact new stage order before the existing publisher/reviewer/automation tail:

```python
[
    ("source-inventory", "tool"),
    ("context-marker", "skill"),
    ("test-classifier", "skill"),
    ("test-classifier-reviewer", "skill"),
    ("tc-generator", "skill"),
]
```

Accepted reviewer classification is persisted but not forwarded into V3 automation/trace/final carriers.

- [ ] **Step 1: Write RED route-mutation tests**

```python
def test_generator_accepts_only_managed_behavior_context(self):
    contract = json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    step = next(row for row in contract["steps"] if row["id"] == "tc-generator")
    self.assertEqual(["managed_behavior_context"], step["accepts"])
    self.assertFalse({"raw_content", "technical_test_inventory", "technical_test_classification"} & set(step["accepts"] + step["forwards"]))
```

Mutate every new carrier, reorder the four new stages, add AUTO_FIX to classifier reviewer, route classification into automation, and downgrade version; each must fail `contract_check`.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_pipeline_v4 -v`

Expected: FAIL because registry remains Pipeline 2.0.

- [ ] **Step 3: Implement exact Pipeline 4.0 constants and inventory checks**

Add the five new schemas (`test-symbol-registry`, `source-inventory-output`, `test-classifier-output`, `test-classifier-reviewer-output`, `effective-technical-evidence`) and two new skills to doctor/contract inventory while preserving all automatic `.skillsrc` requirements and pipeline discovery wording. `contract_check` must compare exact stage carriers and both reviewer transitions.

- [ ] **Step 4: Regenerate docs and run GREEN**

```powershell
python tools\render_contract_docs.py --root .
python -m unittest tests.test_pipeline_v4 tests.test_orchestration_v3 tests.test_documentation_v3 -v
python tools\contract_check.py --root . --full
python tools\doctor.py --root .
python tools\render_contract_docs.py --root . --check
```

- [ ] **Step 5: Commit**

```powershell
git add contracts/pipeline.json schemas/pipeline.schema.json tools/contract_check.py tools/doctor.py tools/render_contract_docs.py skills/orchestrate README.md USAGE.md HOW-IT-WORKS.md CONTRACTS.md PIPELINE.md tests/test_pipeline_v4.py tests/test_orchestration_v3.py tests/test_documentation_v3.py
git commit -m "feat: route reviewed test classification in pipeline 4"
```

### Task 7: Compatibility Gate and InvenTree Calibration

**Files:**

- Create: `tools/audit_test_portfolio.py`
- Create: `tests/test_test_portfolio_audit.py`
- Create: `docs/to_do/inventree-test-classification-2026-08-13/` in a new clean InvenTree worktree only
- Create: `.superpowers/sdd/2026-08-13-test-classification/phase-1-report.md` (ignored local evidence)
- No other production file changes unless a failing acceptance test exposes a defect; any defect gets a new RED test first.

**Interfaces:**

- The calibration consumes automatically generated `.skillsrc` and selects a module through existing APIs.
- It produces immutable inventory, authorized-source, classification, review, behavior-context, candidate bundle, and count/coverage summaries under the attempt directory.
- `tools/audit_test_portfolio.py phase1` is a thin report composer: it calls only the public classification/canonical validators, owns no join policy, writes create-only compact JSON, and returns exit 2 with safe diagnostics on invalid input.

- [ ] **Step 1: Write and run the RED audit-CLI contract**

`tests/test_test_portfolio_audit.py` supplies the checked-in Phase-1 fixtures plus one canonical document and requires exact keys `status`, `inventory_pair_count`, `classification_pair_count`, `reviewed_pair_count`, `scope_counts`, `requirement_count`, `case_count`, `uncovered_requirement_ids`, and `diagnostics`. It also covers mismatched pairs, forbidden test evidence in managed context, uncovered requirements, unsafe output overwrite, and safe CLI errors.

Run: `python -m unittest tests.test_test_portfolio_audit -v`

Expected: FAIL because the audit CLI is absent.

- [ ] **Step 2: Implement the thin audit CLI and run focused compatibility without the full suite**

Run:

```powershell
python -m unittest tests.test_source_inventory tests.test_test_classification tests.test_behavior_context_v4 tests.test_test_portfolio_audit tests.test_pipeline_v4 tests.test_skill_contracts_v3 -v
python -m unittest tests.test_canonical_bytes tests.test_markdown_projection tests.test_zephyr_csv_projection tests.test_bundle_publisher -v
```

Expected: PASS; projection goldens byte-identical.

- [ ] **Step 3: Run one clean InvenTree classification pipeline**

Create a new linked worktree from the current clean InvenTree base. Run automatic `.skillsrc` bootstrap, `source-inventory`, context marking, classification, one independent classification review, and case generation. Do not set or ask for a target count.

Use this exact isolated target; if the path or branch already exists, stop and inspect it rather than deleting or reusing it:

```powershell
git -C D:\AI-Projects\real-chain-projects\inventree-clean worktree add -b codex/inventree-test-classification-20260813 D:\AI-Projects\real-chain-projects\inventree-test-classification-20260813 HEAD
```

Persist at minimum:

```text
00-bootstrap/skillsrc-init.json
01-source-inventory/source-inventory.json
02-context-marker/context-marker-output.json
03-test-classifier/test-classifier-output.json
04-classifier-reviewer/reviewer-output.json
05-tc-generator/tc-generator-output.json
05-tc-generator/canonical-document.json
06-audit/classification-summary.json
06-audit/case-inventory-summary.json
```

- [ ] **Step 4: Execute and preserve the deterministic real-project audit**

From the pipeline repository, invoke:

```powershell
$inventreeWorktree = 'D:\AI-Projects\real-chain-projects\inventree-test-classification-20260813'
$attemptRoot = 'D:\AI-Projects\real-chain-projects\inventree-test-classification-20260813\docs\to_do\inventree-test-classification-2026-08-13'
python tools\audit_test_portfolio.py phase1 --project $inventreeWorktree --inventory "$attemptRoot\01-source-inventory\source-inventory.json" --classification "$attemptRoot\03-test-classifier\test-classifier-output.json" --review "$attemptRoot\04-classifier-reviewer\reviewer-output.json" --context "$attemptRoot\02-context-marker\context-marker-output.json" --canonical-document "$attemptRoot\05-tc-generator\canonical-document.json" --output "$attemptRoot\06-audit\classification-summary.json"
```

Copy this resolved command verbatim into `phase-1-report.md`. The command refuses an existing output.

Read back `classification-summary.json` and assert:

```python
assert summary["status"] == "PASS"
assert summary["inventory_pair_count"] == summary["classification_pair_count"] == summary["reviewed_pair_count"]
assert summary["uncovered_requirement_ids"] == []
assert summary["diagnostics"] == []
```

The independent reviewer separately records the semantic scenario-boundary sample in `06-audit/case-inventory-review.json`: every major backend domain and scope, plus every `unknown` when there are at most 25; otherwise the first/last 10 and 5 digest-seeded rows. This review, not the mechanical CLI, judges duplicate scenario meaning and assertion/branch boundaries.

- [ ] **Step 5: Run the single full acceptance suite**

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tools\contract_check.py --root . --full
python tools\doctor.py --root .
python tools\render_contract_docs.py --root . --check
python -m compileall -q tools skills tests
git diff --check
git diff --name-only HEAD -- .skillsrc.example schemas/skillsrc.schema.json tools/scan_project.py
```

Expected: all commands exit 0; protected-file diff is empty. Record skips verbatim.

- [ ] **Step 6: Fresh review and final commit**

Have a fresh reviewer compare spec, inventory coverage, classification review, generator isolation, audit CLI, and actual diff. First commit the planned audit tool and test:

```powershell
git add tools/audit_test_portfolio.py tests/test_test_portfolio_audit.py
git commit -m "feat: audit classified test portfolios"
```

If acceptance exposed additional fixes, commit them only after their focused RED/GREEN and rerun the affected gates:

```powershell
git add --patch
git diff --cached --name-only
git commit -m "fix: close phase 1 classification acceptance gaps"
```
