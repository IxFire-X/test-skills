# Zephyr Test Case Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the V2.1 one-step/manual-copy test-case model with one V3 canonical semantic document that deterministically publishes exactly JSON, human Markdown, and Zephyr Scale CSV and remains the sole source for automation and execution traceability.

**Architecture:** A shared closed JSON Schema and one semantic validator own the canonical document. A projection facade consumes only a validated bare document and builds immutable JSON/Markdown/CSV revision bundles; reviewer selection, automation relations, runner evidence, and trace all carry the digest of those exact JSON bytes. Stage envelopes transport documents but are never revision identity.

**Tech Stack:** Python 3.10+ standard library, `jsonschema>=4.23,<5` Draft 2020-12, `referencing` bundled with `jsonschema`, stdlib `unittest`, JSON/Markdown/CSV, JDK-only Java reference fixtures when a compiler is available.

## Global Constraints

- Work only in `D:\AI-Projects\test-skills\.worktrees\zephyr-test-case-projection` on `codex/zephyr-test-case-projection`.
- The normative design is `docs/superpowers/specs/2026-08-12-zephyr-test-case-projection-design.md`, SHA-256 `9BAA45DC69E088FBC909E85BB97DFB975C57F20A5EF9672A4FD9969D3F45047D`.
- Stage schema version is exactly `3.0.0`; pipeline contract version is exactly `2.0`.
- V2.1 input is rejected with a breaking-change diagnostic. No mixed V2.1/V3 pipeline and no inferred semantic migration are implemented.
- Do not modify `.skillsrc.example`, `schemas/skillsrc.schema.json`, `tools/scan_project.py`, the worktree `D:\AI-Projects\test-skills\.worktrees\automatic-skillsrc-discovery`, or commits belonging to `codex/automatic-skillsrc-discovery`.
- Preserve discovery-related wording in shared docs/skills; edit only test-case artifact lifecycle sections unless the finished discovery branch is explicitly integrated later.
- Add no runtime or test dependency. Repository self-tests use `python -m unittest`; Java reference code uses only the JDK.
- The production skills and tools never require Java, Docker, an Internet connection, or permission to bind a socket. Their mandatory transport tests use an injected in-memory fake transport and golden bytes.
- The `127.0.0.1` HTTP server and Java adapter are test-only integration evidence. They bind only loopback on an OS-assigned port, use synthetic data, are never imported by production code, and may report `SKIPPED/UNVERIFIED` without failing the mandatory local suite when policy or tooling forbids them. Full cross-language transport parity may be claimed only after this optional gate has passed at least once in an allowed environment.
- Do not commit, push, merge, or open a PR without a separate user instruction.
- Use synthetic fixtures only. Do not copy workplace test text, identifiers, URLs, secrets, or workbook row contents into the repository.
- Raw secrets never appear in canonical JSON, projections, stdout, trace, or evidence; only opaque handles and safe labels are allowed.
- Consumers reject invalid canonical physical order; they never repair, normalize, sort, or deduplicate a document.
- Every task follows RED -> smallest GREEN -> focused review. Do not begin its production step until its named RED command fails for the intended missing behavior.

## Locked V3 Transport Names

These names are fixed before implementation and must be used unchanged across schemas, tools, skills, fixtures, and docs.

```json
{
  "generator": {
    "schema_version": "3.0.0",
    "stage": "tc-generator",
    "artifacts": {"canonical_document": "<bare canonical document>"},
    "warnings": []
  },
  "reviewer": {
    "schema_version": "3.0.0",
    "stage": "tc-reviewer",
    "artifacts": {
      "validation_report": {
        "verdict": "ПРИНЯТО | AUTO_FIX_APPLIED | ТРЕБУЕТ ДОРАБОТКИ",
        "candidate": {
          "document_id": "TCDOC-*",
          "revision": 1,
          "document_sha256": "sha256:<64 lowercase hex>"
        },
        "reviewed_case_ids": ["TC-*"],
        "findings": [],
        "corrections": []
      },
      "successor_document": "present only for AUTO_FIX_APPLIED"
    },
    "warnings": []
  },
  "automation": {
    "schema_version": "3.0.0",
    "stage": "tc-to-autotest",
    "artifacts": {
      "automation_status": "GENERATED | BLOCKED",
      "source": {
        "document_id": "TCDOC-*",
        "revision": 1,
        "source_digest": "sha256:<64 lowercase hex>"
      },
      "generated_files": [],
      "generated_symbols": [],
      "implementation_relations": [],
      "manual_dispositions": [],
      "diagnostics": []
    },
    "warnings": []
  }
}
```

Additional locked names:

- Orchestration carries `candidate_document`, `candidate_bundle_receipt`, optional `successor_document`, optional `successor_bundle_receipt`, `effective_document`, and `effective_bundle_receipt`.
- A bundle receipt contains `document_id`, `revision`, `csv_profile`, `json_path`, `markdown_path`, `csv_path`, `document_sha256`, `markdown_sha256`, and `csv_sha256`.
- Automation runtime identity is `(file_id, symbol_id)`, never a global method ID.
- Trace and run artifacts use `source` with `document_id`, `revision`, and `source_digest` and use `file_digest` for the verified generated-file bytes.
- A blocked automation artifact has `automation_status: "BLOCKED"`, nonempty `diagnostics`, and empty generated/relation/disposition arrays; it is diagnostic output, not successful automation coverage.
- A generated artifact has `automation_status: "GENERATED"`, empty `diagnostics`, and satisfies all coverage/manual-disposition rules. A fully manual document may have empty files/symbols/relations and complete manual dispositions.

## File Structure and Ownership

```text
schemas/canonical-test-document.schema.json  sole canonical shape
tools/schema_validation.py                  strict JSON loading and local $ref registry
tools/canonical_document.py                 exact bytes, digest, semantic validation facade
tools/assertion_dsl.py                      portable-regex-v1 and assertion evaluator
tools/http_binding_v1.py                    language-neutral HTTP reference semantics
tools/revision_selection.py                 reviewer lineage and identity-graph checks
tools/test_case_projections.py              sole Markdown/CSV projection facade
tools/publish_test_case_bundle.py           immutable three-file publisher CLI
tools/automation_validation.py              atomic relation/manual-disposition validator
tests/                                      stdlib unittest suite and synthetic fixtures
evals/zephyr-test-case-projection/          synthetic skill behavior scenarios and rubrics
```

The existing `skills/tc-generator/scripts/export_test_cases_csv.py` becomes only a thin V3 CLI forwarder to `tools/test_case_projections.py` / `tools/publish_test_case_bundle.py`; it contains no independent CSV mapping.

---

### Task 1: Strict Schema Registry and Common Canonical Shape

**Files:**

- Create: `schemas/canonical-test-document.schema.json`
- Create: `tools/schema_validation.py`
- Create: `tests/__init__.py`
- Create: `tests/test_canonical_schema.py`
- Create: `tests/fixtures/canonical/valid/minimal-manual.json`
- Create: `tests/fixtures/canonical/valid/full-http.json`
- Create: `tests/fixtures/canonical/valid/project-action.json`
- Create: `tests/fixtures/canonical/invalid/legacy-v2.1.json`
- Modify: `tools/validate_artifact.py`

**Interfaces:**

- Produces `load_json_strict(path: Path) -> Any`, rejecting BOM, duplicate object keys, NaN, Infinity, and trailing data.
- Produces `loads_json_strict(text: str) -> Any`, `classify_version(instance: Any) -> dict[str, str]`, `StrictJsonError`, and `SchemaRegistryError` for deterministic callers/tests.
- Produces `validator_for(schema_path: Path, root: Path) -> Draft202012Validator` with an explicit repository-local registry.
- Produces `schema_diagnostics(instance: Any, schema_path: Path, root: Path) -> list[dict[str, str]]`, each item exactly `{"path": <RFC6901>, "code": <stable code>, "message": <text>}`.
- `schemas/canonical-test-document.schema.json` is the only owner of canonical document/test-case definitions and uses closed objects/unions from design sections 5–8.

- [ ] **Step 1: Add RED tests for strict loading and local references**

```python
class StrictSchemaTests(unittest.TestCase):
    def test_common_schema_accepts_all_three_valid_variants(self):
        for name in ("minimal-manual.json", "full-http.json", "project-action.json"):
            document = load_json_strict(FIXTURES / "valid" / name)
            self.assertEqual([], schema_diagnostics(document, SCHEMA, ROOT), name)

    def test_duplicate_json_key_is_rejected(self):
        with self.assertRaisesRegex(StrictJsonError, "duplicate object key: document_id"):
            loads_json_strict('{"document_id":"a","document_id":"b"}')

    def test_nan_and_infinity_are_rejected(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token), self.assertRaises(StrictJsonError):
                loads_json_strict('{"value":' + token + '}')

    def test_legacy_v21_gets_breaking_change_diagnostic(self):
        legacy = load_json_strict(FIXTURES / "invalid" / "legacy-v2.1.json")
        self.assertEqual("V2_1_BREAKING_CHANGE", classify_version(legacy)["code"])

    def test_registry_rejects_ref_outside_repository_schemas(self):
        with self.assertRaisesRegex(SchemaRegistryError, "repository-local schema"):
            validator_for(Path("../README.md"), ROOT)
```

- [ ] **Step 2: Run the focused RED command**

Run: `python -m unittest tests.test_canonical_schema -v`

Expected: FAIL because the common schema and registry do not exist.

- [ ] **Step 3: Implement strict JSON loading and repository-local `$ref` resolution**

Use `json.loads(text, parse_constant=reject_constant, object_pairs_hook=reject_duplicates)` and a `referencing.Registry` populated only from `root/schemas/*.json`. Never fetch HTTP `$id` values or arbitrary file paths.

- [ ] **Step 4: Implement the full closed canonical schema**

Encode exact top-level fields, IDs, metadata subjects, capabilities, requirements, cases/management, unlimited steps, operations, inputs/outputs, type descriptors, expectations/assertions, blockers, conditional `manual_reason`, and conditional `operation: null`. Use `additionalProperties: false` on every object/variant and lowercase digest lexemes.

- [ ] **Step 5: Run GREEN and schema self-checks**

Run:

```powershell
python -m unittest tests.test_canonical_schema -v
python tools\validate_artifact.py schemas\canonical-test-document.schema.json tests\fixtures\canonical\valid\full-http.json
```

Expected: all tests PASS and CLI returns `status: valid`.

- [ ] **Step 6: Review task scope**

Run: `git diff --check -- schemas tools/schema_validation.py tools/validate_artifact.py tests`

Confirm no canonical definitions were copied into any stage schema yet.

### Task 2: Canonical Bytes and Digest Identity

**Files:**

- Create: `tools/canonical_document.py`
- Create: `tests/test_canonical_bytes.py`
- Create: `tests/fixtures/canonical/golden/full-http.canonical-json.bin`

**Interfaces:**

- Consumes `schema_validation.load_json_strict` and `schema_diagnostics`.
- Produces `canonical_bytes(document: dict[str, Any]) -> bytes`.
- Produces `document_sha256(document: dict[str, Any]) -> str` with `sha256:` prefix.
- Produces `load_canonical_document(path: Path) -> dict[str, Any]`.
- No function in this file writes to disk, sorts arrays, normalizes Unicode, or hashes an envelope.

- [ ] **Step 1: Write byte-level RED tests**

```python
class CanonicalBytesTests(unittest.TestCase):
    def setUp(self):
        self.document = load_json_strict(FIXTURES / "valid" / "full-http.json")

    def test_matches_exact_golden_bytes_and_digest(self):
        payload = canonical_bytes(self.document)
        golden = (GOLDEN / "full-http.canonical-json.bin").read_bytes()
        self.assertEqual(golden, payload)
        self.assertEqual("sha256:" + hashlib.sha256(golden).hexdigest(), document_sha256(self.document))

    def test_object_key_order_does_not_change_bytes(self):
        reversed_keys = dict(reversed(list(self.document.items())))
        self.assertEqual(canonical_bytes(self.document), canonical_bytes(reversed_keys))

    def test_array_order_changes_bytes(self):
        changed = copy.deepcopy(self.document)
        changed["test_cases"] = list(reversed(changed["test_cases"]))
        self.assertNotEqual(canonical_bytes(self.document), canonical_bytes(changed))

    def test_unicode_is_not_normalized(self):
        composed = {"text": "é"}
        decomposed = {"text": "e\u0301"}
        self.assertNotEqual(canonical_bytes(composed), canonical_bytes(decomposed))

    def test_no_bom_or_terminal_lf(self):
        payload = canonical_bytes(self.document)
        self.assertFalse(payload.startswith(b"\xef\xbb\xbf"))
        self.assertFalse(payload.endswith(b"\n"))

    def test_non_finite_number_is_rejected(self):
        with self.assertRaises(ValueError):
            canonical_bytes({"value": float("nan")})

    def test_envelope_digest_is_not_document_digest(self):
        envelope = {"schema_version": "3.0.0", "artifacts": {"canonical_document": self.document}}
        envelope_digest = "sha256:" + hashlib.sha256(canonical_bytes(envelope)).hexdigest()
        self.assertNotEqual(document_sha256(self.document), envelope_digest)
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_canonical_bytes -v`

Expected: FAIL because `canonical_bytes` and `document_sha256` are absent.

- [ ] **Step 3: Implement the exact serializer**

```python
def canonical_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

def document_sha256(document: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(document)).hexdigest()
```

- [ ] **Step 4: Generate the synthetic golden once and lock its explicit digest in the test**

The test reads `.bin` as bytes; do not regenerate it during normal test execution. Ensure `.bin` preserves BOM/newline bytes independently of Git text conversion.

- [ ] **Step 5: Run GREEN**

Run: `python -m unittest tests.test_canonical_bytes -v`

Expected: all tests PASS.

### Task 3: Canonical Semantic Validator

**Files:**

- Modify: `tools/canonical_document.py`
- Create: `tests/test_canonical_semantics.py`
- Create: `tests/fixture_factory.py`
- Create: `tests/fixtures/canonical/valid/eleven-steps.json`
- Create: `tests/fixtures/canonical/valid/hundred-steps.json`

**Interfaces:**

- Produces `validate_canonical_document(document: dict[str, Any]) -> list[dict[str, str]]`.
- Produces `require_valid_canonical_document(document: dict[str, Any]) -> None`, raising `CanonicalDocumentError(diagnostics: Sequence[dict[str, str]])`. The exception exposes immutable `.diagnostics`, and `str(error)` is the compact deterministic JSON serialization of that list.
- Diagnostics use RFC 6901 paths and stable codes; projections and downstream validators call this facade instead of reimplementing canonical rules.
- `tests/fixture_factory.py` produces `accepted_report_for`, `rework_report_for`, `valid_successor_of`, `delete_input`, `rename_capability_argument`, `reparent_assertion`, `edit_text_and_order_without_changing_ids`, and `diagnostic_codes` for later revision/automation tests.

- [ ] **Step 1: Add RED matrices for identity, order, ownership, type, and readiness**

Tests must mutate a known-valid synthetic fixture and cover every design item in section 18.1: duplicate/foreign IDs, every physical-order collection, set-like sort order, forward/cyclic output refs, target overlap, capability ownership, exact type compatibility, operator pairs, manual-only/blocker states, blocker code/path pairs, partial technical objects, secrets, and 1/11/100 steps.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_canonical_semantics -v`

Expected: FAIL because only schema validation exists.

- [ ] **Step 3: Implement deterministic indexing and order checks**

Build one document index containing requirements, capabilities/arguments/results, cases, parented steps/inputs/outputs/blockers/expectations/assertions. Preserve physical position and reject duplicate/local-parent collisions before resolving references.

- [ ] **Step 4: Implement data-flow and type checks**

Resolve only earlier `(step_id, output_id)` pairs; reject missing/forward/cyclic refs, incompatible stored/resolved descriptors, undeclared capability arguments/results, missing required arguments, duplicate effective targets, and HTTP body pointer overlap.

- [ ] **Step 5: Implement assertion/blocker/manual state checks**

Validate the closed operator matrix, expected/actual compatibility, exact blocker enum/path mapping, nonempty `provenance`, no partial union objects, ready assertions, and blocked/manual state transitions.

- [ ] **Step 6: Run GREEN and mutation spot checks**

Run: `python -m unittest tests.test_canonical_semantics -v`

Expected: all tests PASS with deterministic diagnostic order.

### Task 4: Portable Assertion DSL and `http-binding-v1`

**Files:**

- Create: `tools/assertion_dsl.py`
- Create: `tools/http_binding_v1.py`
- Create: `tests/test_assertion_dsl.py`
- Create: `tests/test_http_binding_v1.py`
- Create: `tests/http_binding_integration.py`
- Create: `tests/fixtures/portable-regex-v1/cases.json`
- Create: `tests/fixtures/http-binding-v1/request-cases.json`
- Create: `tests/fixtures/http-binding-v1/response-cases.json`
- Create: `tests/fixtures/http-binding-v1/phase-cases.json`
- Create: `tests/fixtures/http-binding-v1/local_server.py`
- Create: `tests/reference_adapters/python/http_binding_v1_reference.py`
- Create: `tests/reference_adapters/java/SharedContractReference.java`
- Create: `tests/run_java_contract.py`
- Create: `tests/run_http_binding_integration.py`

**Interfaces:**

- Produces tagged `ValueState.value(value)` and `ValueState.missing()`; the sentinel is never JSON-serializable.
- Produces `portable_fullmatch(pattern: str, value: str) -> bool` using the exact V1 grammar, not host regex.
- Produces immutable `EvaluationContext(resolve_schema: Callable[[str], bytes])`; the callback resolves only already-authorized in-memory/local context and never performs network I/O.
- Produces immutable `AssertionResult(status: Literal["PASSED", "FAILED", "ERROR"], code: str | None, message: str | None)` and `evaluate_assertion(assertion, resolve_actual, resolve_expected, context: EvaluationContext) -> AssertionResult`.
- Produces immutable `AbstractRequest(method, absolute_url, ordered_headers, body_bytes)` and `build_request(operation, inputs, providers, step_outputs) -> AbstractRequest`.
- Produces a minimal injectable `Transport.send_once(request, timeout_seconds) -> RawResponse`; mandatory tests use a recording `FakeTransport` and require exactly zero or one call without opening a socket.
- Produces immutable `ExecutionError(code: str, path: str, message: str)`; `build_request`, `execute_once`, and `observe_response` raise it for transport/profile/JSON errors, never return `MISSING` for those errors.
- Produces `execute_once(request: AbstractRequest, transport: Transport, timeout_seconds: float) -> RawResponse`. Mandatory fake-transport tests call this exact production seam. The Python and Java real-transport adapters implement the same `Transport.send_once` contract and make exactly one request with redirects, retries, cookies, auth, defaults, and decompression disabled.
- Produces `observe_response(response, source) -> ValueState` for deterministic response observation.

- [ ] **Step 1: Add shared corpus RED tests**

Cover every regex production/forbidden construct, Unicode scalars, equality/length rules, the full MISSING operator matrix, base origin grammar, placeholder encoding, query scalar lexemes/order, reserved/control headers, body root/non-root `/0`, pointer escapes, exact canonical body bytes, response pointers, duplicate headers/JSON keys, redirects/retries, and content-coding. All mandatory HTTP cases run through a recording fake transport and prove the exact zero/one-call contract without binding a socket. Add `schema_matches` cases for exact `uri` resolution, lowercase digest, Draft 2020-12, missing authorized bytes, invalid schema, digest mismatch, and a resolver spy proving zero network access.

- [ ] **Step 2: Run Python RED**

Run: `python -m unittest tests.test_assertion_dsl tests.test_http_binding_v1 -v`

Expected: FAIL because both reference modules are absent.

- [ ] **Step 3: Implement the Python portable-regex parser/NFA and assertion evaluator**

Do not call `re.fullmatch` for the V1 dialect. Parse to a small AST/NFA, match Unicode scalar values, and return only deterministic boolean/result states. For `schema_matches`, resolve bytes only through `EvaluationContext`, verify the declared digest before parsing, require Draft 2020-12, and evaluate with the shared local schema registry.

- [ ] **Step 4: Implement deterministic request construction and response observation**

Implement one-attempt semantics, preflight-vs-step-output failure classification, exact injected headers, no hidden cookies/auth/default headers, no redirect/retry/decompression, and zero-client-call failures behind the injectable transport seam. The mandatory Python unit/golden suite uses only `FakeTransport`. The optional Python integration adapter uses only `http.client`; the optional Java reference uses `java.net.http.HttpClient` configured with `Redirect.NEVER` and no authenticator/cookie handler.

- [ ] **Step 5: Implement a JDK-only shared-corpus runner**

`SharedContractReference.java` must consume the same fixture semantics without Maven/JUnit or any non-loopback network dependency. Keep all helper classes, including a strict duplicate-rejecting `MiniJson` reader, nested in this single source so one `javac` command is sufficient. `tests/fixtures/http-binding-v1/local_server.py` listens only on `127.0.0.1` with port `0`, exposes deterministic endpoints and atomic request counters for redirect, retryable status, raw gzip, duplicate headers, exact headers/body, and forced transport close, and rejects non-loopback use. `tests/http_binding_integration.py` contains the non-discovered Python adapter checks; its filename deliberately does not match `test_*.py`. `tests/run_http_binding_integration.py` is the single public integration runner: it probes loopback and `javac`, invokes both Python and Java adapters, and by default emits one deterministic `PASS` or `SKIPPED` JSON report with exit 0. With `--require-integration`, it exits nonzero unless both adapters report `PASS`. `tests/run_java_contract.py` is an internal helper, not the user-facing gate. None of these files is imported by a production module or skill.

- [ ] **Step 6: Run Python GREEN**

Run: `python -m unittest tests.test_assertion_dsl tests.test_http_binding_v1 -v`

Expected: all mandatory socket-free Python tests PASS on a restricted workstation.

- [ ] **Step 7: Run optional loopback and Java parity gates**

Run:

```powershell
python tests\run_http_binding_integration.py
```

Expected: exit 0 with `PASS` or an explicit deterministic `SKIPPED` reason. A policy-denied loopback bind or missing `javac` does not fail the mandatory suite and does not affect production use. In an environment that permits both, run `python tests\run_http_binding_integration.py --require-integration`; only its combined Python+Java `PASS` permits the claim `full cross-language transport parity verified`. Otherwise record the exact gate as unverified and do not weaken/remove the fixtures.

### Task 5: Reviewer Successor and Effective-Revision Selection

**Files:**

- Create: `tools/revision_selection.py`
- Create: `tests/test_revision_selection.py`

**Interfaces:**

- Consumes only schema+semantic-valid bare documents and `document_sha256`.
- Produces `validate_successor(candidate, successor) -> list[dict[str, str]]`.
- Produces `select_effective_document(candidate, validation_report, successor=None) -> dict[str, Any]`.
- Produces `SelectionError(diagnostics: Sequence[dict[str, str]])` with immutable `.diagnostics` and deterministic compact-JSON `str(error)`.
- Identity keys are explicit: document children by stable ID; capability arguments/results by `(capability_id, role, name)`; all other local entities by parent stable ID plus local ID.
- For every verdict, `validation_report.reviewed_case_ids` must equal all candidate `case_id` values exactly once and in canonical physical order; missing, duplicate, foreign, partial, or permuted review coverage is rejected.

- [ ] **Step 1: Write lineage and identity-graph RED tests**

```python
class RevisionSelectionTests(unittest.TestCase):
    def test_accepted_selects_candidate(self):
        report = accepted_report_for(self.candidate)
        self.assertIs(self.candidate, select_effective_document(self.candidate, report))

    def test_auto_fix_requires_revision_plus_one_and_parent_digest(self):
        successor = valid_successor_of(self.candidate)
        self.assertEqual([], validate_successor(self.candidate, successor))

    def test_auto_fix_rejects_changed_document_id(self):
        successor = valid_successor_of(self.candidate)
        successor["document_id"] = "TCDOC-different"
        self.assertIn("SUCCESSOR_DOCUMENT_ID", diagnostic_codes(validate_successor(self.candidate, successor)))

    def test_auto_fix_rejects_partial_successor(self):
        successor = valid_successor_of(self.candidate)
        successor["test_cases"].pop()
        self.assertIn("IDENTITY_REMOVED", diagnostic_codes(validate_successor(self.candidate, successor)))

    def test_auto_fix_rejects_deleted_renamed_or_reparented_identity(self):
        for mutation in (delete_input, rename_capability_argument, reparent_assertion):
            with self.subTest(mutation=mutation.__name__):
                successor = valid_successor_of(self.candidate)
                mutation(successor)
                self.assertIn("IDENTITY_GRAPH_CHANGED", diagnostic_codes(validate_successor(self.candidate, successor)))

    def test_text_and_display_order_edits_preserve_identity(self):
        successor = valid_successor_of(self.candidate)
        edit_text_and_order_without_changing_ids(successor)
        self.assertEqual([], validate_successor(self.candidate, successor))

    def test_reviewed_case_ids_must_exactly_cover_candidate_in_order(self):
        expected = [case["case_id"] for case in self.candidate["test_cases"]]
        for invalid in (expected[:-1], expected + ["TC-foreign"], list(reversed(expected))):
            with self.subTest(reviewed_case_ids=invalid):
                report = accepted_report_for(self.candidate)
                report["reviewed_case_ids"] = invalid
                with self.assertRaises(SelectionError):
                    select_effective_document(self.candidate, report)

    def test_rework_forbids_successor(self):
        report = rework_report_for(self.candidate)
        with self.assertRaisesRegex(SelectionError, "successor is forbidden"):
            select_effective_document(self.candidate, report, valid_successor_of(self.candidate))
```

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_revision_selection -v`

Expected: FAIL because selection code does not exist.

- [ ] **Step 3: Implement graph extraction and exact verdict branches**

Return a full bare document, never a merged list/patch. Reject candidate digest mismatches and incomplete/duplicate/foreign/permuted `reviewed_case_ids` before considering a successor.

- [ ] **Step 4: Run GREEN**

Run: `python -m unittest tests.test_revision_selection -v`

Expected: all branches PASS.

### Task 6: V3 Stage Envelopes and Breaking-Change Diagnostics

**Files:**

- Modify: `schemas/context-marker-output.schema.json`
- Modify: `schemas/tc-generator-output.schema.json`
- Modify: `schemas/tc-reviewer-output.schema.json`
- Modify: `schemas/tc-to-autotest-output.schema.json`
- Modify: `schemas/autotest-reviewer-output.schema.json`
- Modify: `schemas/run-tests-output.schema.json`
- Modify: `schemas/trace-document.schema.json`
- Modify: `schemas/orchestrator-output.schema.json`
- Modify: `tools/validate_artifact.py`
- Create: `tests/test_v3_stage_schemas.py`
- Create: `tests/fixtures/stages/v3/context-marker.json`
- Create: `tests/fixtures/stages/v3/tc-generator.json`
- Create: `tests/fixtures/stages/v3/tc-reviewer-accepted.json`
- Create: `tests/fixtures/stages/v3/tc-reviewer-auto-fix.json`
- Create: `tests/fixtures/stages/v3/tc-reviewer-rework.json`

**Interfaces:**

- Generator `artifacts.canonical_document` and reviewer `artifacts.successor_document` use repository-local `$ref` to `canonical-test-document.schema.json`; no copied `$defs.test_case` remain.
- Reviewer conditional shape follows the locked names: successor required only for `AUTO_FIX_APPLIED`, forbidden otherwise.
- Context-marker requirement items become `requirement_id`, `display_order`, `text`, and `provenance` so generator can preserve them directly.
- Every affected V3 entry point recognizes `schema_version: "2.1.0"` before generic validation and returns stable code `V2_1_BREAKING_CHANGE`.

- [ ] **Step 1: Write RED schema/envelope matrices**

Test every required/extra field, each reviewer verdict branch, common-schema `$ref`, lowercase digest, source carrier, full successor, and a representative V2.1 envelope for each affected entry point.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_v3_stage_schemas -v`

Expected: FAIL because schemas still declare `2.1.0` and duplicate V2.1 models.

- [ ] **Step 3: Replace duplicated stage shapes with V3 carriers**

Keep stage envelopes closed. Do not define automation relation internals twice: `tc-to-autotest` is their owner and later validators consume its exact schema.

- [ ] **Step 4: Add deterministic breaking-version detection to validation CLI**

Schema-invalid V3 remains exit 1. Unreadable input remains exit 2. A validly read V2.1 envelope returns exit 1 with `V2_1_BREAKING_CHANGE`, not a wall of unrelated missing-field messages.

- [ ] **Step 5: Run GREEN and validate every V3 fixture through the CLI**

Run:

```powershell
python -m unittest tests.test_v3_stage_schemas -v
python tools\validate_artifact.py schemas\tc-generator-output.schema.json tests\fixtures\stages\v3\tc-generator.json
python tools\validate_artifact.py schemas\tc-reviewer-output.schema.json tests\fixtures\stages\v3\tc-reviewer-auto-fix.json
```

Expected: all tests PASS and both fixtures are valid.

### Task 7: Exact Human Markdown and Zephyr Scale CSV Projections

**Files:**

- Create: `tools/test_case_projections.py`
- Create: `tests/test_markdown_projection.py`
- Create: `tests/test_zephyr_csv_projection.py`
- Create: `tests/fixtures/projections/full-http.markdown.bin`
- Create: `tests/fixtures/projections/full-http.zephyr-scale.csv.bin`

**Interfaces:**

- Produces immutable `Projection(payload: bytes, warnings: Sequence[str])`.
- Produces `escape_inline(value: str) -> str`.
- Produces `render_markdown(document: dict[str, Any]) -> Projection`.
- Produces `render_zephyr_csv(document, profile="zephyr-scale-step-row-24-v1") -> Projection`.
- Both functions call `require_valid_canonical_document` and consume existing physical order. Neither reconstructs or edits JSON.

- [ ] **Step 1: Add Markdown byte-golden RED tests**

Cover the exact Russian header/metadata/case/objective/preconditions/three-column step table, empty preconditions/documentation warning, generic subject, multiple cases, multiple expectations with `<br>`, special characters, UTF-8 without BOM, LF, terminal LF, and no trailing spaces/Test Data column.

- [ ] **Step 2: Add CSV byte-golden RED tests**

Assert the exact 24 headers, one row per step, first-row metadata/empty continuation columns 1–19, four priority mappings, six custom fields, all standard/custom renderers, input-source text, unknown-custom warning, formula defense, RFC 4180 quoting, UTF-8 BOM, CRLF on every record, and empty columns 23–24.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_markdown_projection tests.test_zephyr_csv_projection -v`

Expected: FAIL because projection facade is absent.

- [ ] **Step 4: Implement one validated facade and Markdown renderer**

Keep generated Markdown tokens outside `escape_inline`. Preserve precondition and expectation order; do not derive action or expected text from technical fields.

- [ ] **Step 5: Implement the single fixed CSV profile**

Use `csv.writer` with comma, `quotechar='"'`, `lineterminator='\r\n'`, encode `utf-8-sig`, and apply formula protection before quoting to every textual cell. Reject unknown profiles before rendering.

- [ ] **Step 6: Run GREEN and compare exact bytes**

Run: `python -m unittest tests.test_markdown_projection tests.test_zephyr_csv_projection -v`

Expected: all projection and warning tests PASS.

### Task 8: Immutable Recoverable Three-Artifact Publisher

**Files:**

- Create: `tools/publish_test_case_bundle.py`
- Create: `tests/test_bundle_publisher.py`
- Modify: `skills/tc-generator/scripts/export_test_cases_csv.py`

**Interfaces:**

- Produces `BundlePayloads(json_bytes, markdown_bytes, csv_bytes, warnings)`.
- Produces `Receipt` with the nine locked receipt fields.
- Produces `build_bundle(document, csv_profile) -> BundlePayloads`.
- Produces `publish_bundle(document, output_dir, csv_profile) -> Receipt`.
- Produces `verify_bundle(document, output_dir, csv_profile) -> Receipt`.
- CLI accepts `--input`, `--output-dir`, `--csv-profile`, and `--verify-only`; stdout is one deterministic JSON object with no secret values.

- [ ] **Step 1: Add `BundlePublisherTests` filesystem/concurrency RED tests**

Test safe filename/revision validation, unknown profile before writes, hard-link probe failure, JSON→Markdown→CSV order, identical rerun, different-byte collision, partial recovery, verify-only mismatch list, read-back digest, same-payload concurrent publishers, different-payload concurrent publishers, and no `os.replace`/overwrite path. Name the two full smoke methods `test_cli_publish_verify_and_exactly_three_files` and `test_concurrent_identical_and_conflicting_publishers` for Task 15 reuse.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_bundle_publisher -v`

Expected: FAIL because publisher is absent and the old exporter writes only one replaceable CSV.

- [ ] **Step 3: Implement in-memory bundle construction**

Validate once, serialize bare JSON with `canonical_bytes`, render both projections, compute all digests, and reserve exactly:

```text
<document-id>.r<revision>.json
<document-id>.r<revision>.md
<document-id>.r<revision>.zephyr-scale.csv
```

- [ ] **Step 4: Implement probed create-if-absent publication**

Probe `os.link` in the target directory before bundle writes. Write temporary files in that directory, link in fixed order, handle `FileExistsError` by exact byte comparison, remove temporary links/files, then read back all three targets before emitting a receipt. Never use `os.replace` for revision targets.

- [ ] **Step 5: Convert the old exporter into a thin forwarder**

Preserve the documented script path but remove `FIELDNAMES`, reverse reconstruction, V2.1 parsing, and independent write logic. Map its V3 arguments directly to publisher/facade functions and return the shared receipt.

- [ ] **Step 6: Run GREEN and inspect the filesystem scope**

Run:

```powershell
python -m unittest tests.test_bundle_publisher -v
$forbidden = rg -n "os\.replace|FIELDNAMES|_reconstruct" skills\tc-generator\scripts\export_test_cases_csv.py tools\publish_test_case_bundle.py
if ($LASTEXITCODE -eq 0) { $forbidden; throw "forbidden duplicate/overwrite implementation remains" }
if ($LASTEXITCODE -ne 1) { throw "rg failed with exit code $LASTEXITCODE" }
```

Expected: tests PASS; no overwrite/reconstruction implementation remains.

### Task 9: Atomic Automation Relations and Manual Dispositions

**Files:**

- Modify: `schemas/tc-to-autotest-output.schema.json`
- Modify: `schemas/autotest-reviewer-output.schema.json`
- Create: `tools/automation_validation.py`
- Create: `tests/test_automation_relations.py`
- Create: `tests/fixtures/stages/v3/tc-to-autotest-generated.json`
- Create: `tests/fixtures/stages/v3/tc-to-autotest-manual-only.json`
- Create: `tests/fixtures/stages/v3/tc-to-autotest-blocked.json`

**Interfaces:**

- Produces `validate_automation_artifact(artifact, document) -> list[dict[str, str]]`.
- Produces `required_symbol_pairs(artifact, document) -> set[tuple[str, str]]`.
- Generated files are closed objects with globally unique `file_id`, portable `path`, `language`, `framework`, and lowercase `content_digest`.
- Generated symbols are closed locator unions keyed locally by `(file_id, symbol_id)` for Python module function, Python class method, and Java FQN class method.
- Relations are one semantic target per row: `operation` or `assertion`; multiple rows for one target are AND.

- [ ] **Step 1: Add relation/topology RED tests**

Cover wrong parent IDs, foreign assertion/step, exact duplicate relation, same local symbol ID in two files, duplicate symbol in one file, multiple symbols per target, incomplete ready-step/assertion coverage, orphan symbol/file, locator variants, lowercase digest, blocker output, and source digest mismatch.

- [ ] **Step 2: Add manual-disposition RED tests**

Require exactly one disposition per canonical manual-only step, no duplicate/foreign/ready/blocked disposition, no copied reason field, valid fully-manual empty generated arrays, and blocked status with diagnostics/no coverage artifacts.

- [ ] **Step 3: Run RED**

Run: `python -m unittest tests.test_automation_relations -v`

Expected: FAIL because V2.1 still uses case-level matrix/method arrays.

- [ ] **Step 4: Implement the closed V3 automation schema and semantic validator**

Validate source identity against exact `document_sha256(document)`, relation ownership against the canonical index, symbol/file ownership, deterministic relation order, required target coverage, AND required pairs, and manual/blocker branches.

- [ ] **Step 5: Run GREEN and validate all three automation fixtures**

Run: `python -m unittest tests.test_automation_relations -v`

Expected: all automation topology/status tests PASS.

### Task 10: Runner Evidence by `(file_id, symbol_id)`

**Files:**

- Create: `tools/execution_preflight.py`
- Modify: `tools/run_tests.py`
- Modify: `schemas/run-tests-output.schema.json`
- Create: `tests/test_execution_preflight.py`
- Create: `tests/test_run_tests_v3.py`

**Interfaces:**

- `load_automation_artifact` accepts only V3 and returns the locked automation structure.
- Runner CLI requires `--canonical-document <bare-document.json>` in addition to `--automation-artifact`; before any project subprocess it validates schema, semantics, and `document_sha256(document) == artifacts.source.source_digest`.
- Produces protocol `ProviderResolver.resolve(kind: Literal["fixture", "environment", "secret_handle"], name: str) -> Any` and protocol `AdapterRegistry.require(adapter: str, action: str) -> None`.
- Produces immutable `PreflightResult(status: Literal["READY", "NOT_RUNNABLE"], resolved_values: Mapping[tuple[str, str], Any], diagnostics: Sequence[dict[str, str]])`.
- Produces `preflight_execution(document, provider_resolver, adapter_registry) -> PreflightResult`; it resolves every distinct base URL/provider/assertion source and project adapter before any required symbol or operation starts.
- Produces host API `run_tests_v3(project: Path, language: Literal["python", "java"], canonical_document: Mapping[str, Any], automation_artifact: Mapping[str, Any], provider_resolver: ProviderResolver | None = None, adapter_registry: AdapterRegistry | None = None) -> dict[str, Any]`. An embedding host supplies concrete resolver/registry instances here; there is no dynamic plugin discovery.
- CLI `None` defaults are exact and closed: `EnvironmentOnlyProviderResolver(os.environ)` resolves only `kind="environment"`; fixture and secret-handle requests are unavailable, and `RejectingAdapterRegistry` rejects every project adapter. Unsupported requirements yield preflight `NOT_RUNNABLE` with zero subprocess/symbol/operation starts. CLI flags never accept secret values or import arbitrary provider code.
- `validate_artifact_runner_compatibility` verifies source digest, full generated-file digest, confinement, and unique locator before any test starts.
- `validate_execution_evidence(verdict, run_id, source, evidence, authoritative, required_pairs)` validates exact current-run terminal coverage.
- Evidence record fields are exactly `run_id`, `source_digest`, `file_id`, `symbol_id`, `file_digest`, and `status` in `PASSED|FAILED|ERROR|SKIPPED`.

- [ ] **Step 1: Write global-preflight RED phase tests**

For base URL, fixture, environment, secret handle, and project adapter, cover missing/inaccessible/setup exception/empty/type/profile failures. Assert `NOT_RUNNABLE`, zero subprocess/symbol starts, zero HTTP/project calls, no `run_id`, and empty evidence. Cover invalid literal as pre-publication canonical failure and `step_output` as runtime-only rather than preflight.

- [ ] **Step 2: Write runner RED tests around pure functions**

Cover two files sharing one `symbol_id`, duplicate/conflicting records, stale run/source/file digests, missing pair, SKIPPED, PASSED/FAILED/ERROR, locator collision, modified file bytes, same symbol implementing multiple relations, and multi-symbol AND.

- [ ] **Step 3: Add subprocess binding RED fixtures**

Use temporary synthetic Python tests to prove module-function/class-method identities and same-named methods in different classes; preserve existing Java parsing fixtures without claiming execution if no JDK toolchain is present.

- [ ] **Step 4: Run RED**

Run: `python -m unittest tests.test_execution_preflight tests.test_run_tests_v3 -v`

Expected: FAIL because current code keys evidence by global `method_id`.

- [ ] **Step 5: Implement the global preflight before runner dispatch**

Implement `run_tests_v3` as the sole production wiring seam. Its explicit host objects are passed directly to `preflight_execution`; its `None` defaults instantiate only `EnvironmentOnlyProviderResolver` and `RejectingAdapterRegistry` as defined above. Fixture/secret/project adapters therefore require host embedding, otherwise return `NOT_RUNNABLE` before starting the target test process. Never delay these canonical provider failures into generated test code. Keep resolved values in memory and redact them from reports.

- [ ] **Step 6: Replace method-ID assumptions end-to-end**

Update loading, compatibility, pytest binding, JUnit XML binding, report construction, and schema. A preflight failure produces `NOT_RUNNABLE`, no `run_id`, and empty evidence; failures after symbol start produce terminal records and `FAIL` as defined by the canonical runtime contract.

- [ ] **Step 7: Run GREEN plus current runner regression tests**

Run: `python -m unittest tests.test_execution_preflight tests.test_run_tests_v3 -v`

Expected: all evidence/binding tests PASS.

### Task 11: Exact Atomic Trace Join

**Files:**

- Modify: `tools/build_trace_document.py`
- Modify: `tools/trace_check.py`
- Modify: `schemas/trace-document.schema.json`
- Create: `tests/test_build_trace_v3.py`
- Create: `tests/test_trace_check_v3.py`
- Replace: `skills/orchestrate/assets/orchestration-fixtures/accepted-trace-document.json`

**Interfaces:**

- CLI replaces separate `--requirements` and `--test-cases` with `--canonical-document`; it still requires `--automation-artifact`, `--run-result`, and `--output`.
- Produces `build_trace(document, automation, run_result) -> dict[str, Any]`.
- Trace registries preserve requirements/cases/steps/expectations/assertions/files/symbols plus atomic relations and evidence.
- `trace_check` imports `document_sha256`; it must not define another canonicalizer or hash arrays/envelopes.

- [ ] **Step 1: Write build/join RED tests**

Cover accepted automated, mixed manual remainder, fully manual, blocked, source mismatch, foreign parent relation, multi-symbol AND, same symbol ID in different files, stale/missing/duplicate/conflicting evidence, changed file digest, and exact final verdicts.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_build_trace_v3 tests.test_trace_check_v3 -v`

Expected: FAIL because V2.1 creates a requirement×method cartesian trace and hashes a different object.

- [ ] **Step 3: Replace topology construction with validated atomic joins**

Start from canonical ownership, preserve each validated relation, join only by `(file_id, symbol_id)`, and compute coverage/status without generating inferred requirement×symbol combinations.

- [ ] **Step 4: Replace trace schema and CLI validation**

Store locked source identity, file digest, locator, relation keys, current evidence, manual dispositions, and final verdict. Reject V2.1 explicitly.

- [ ] **Step 5: Run GREEN and accepted fixture checks**

Run:

```powershell
python -m unittest tests.test_build_trace_v3 tests.test_trace_check_v3 -v
python tools\validate_artifact.py schemas\trace-document.schema.json skills\orchestrate\assets\orchestration-fixtures\accepted-trace-document.json
python tools\trace_check.py skills\orchestrate\assets\orchestration-fixtures\accepted-trace-document.json --require-execution
```

Expected: all commands PASS.

### Task 12: Orchestration Selection, Pipeline Contract, and Audit Bundles

**Files:**

- Create: `tools/orchestrate_test_case_revision.py`
- Modify: `contracts/pipeline.json`
- Modify: `schemas/pipeline.schema.json`
- Modify: `tools/contract_check.py`
- Modify: `tools/doctor.py`
- Modify: `schemas/orchestrator-output.schema.json`
- Replace: `skills/orchestrate/assets/orchestration-fixtures/accepted-orchestrator-output.json`
- Replace: `skills/orchestrate/assets/orchestration-fixtures/not-runnable-orchestrator-output.json`
- Create: `tests/test_orchestration_v3.py`

**Interfaces:**

- Produces immutable `OrchestrationResult(candidate_document, candidate_bundle_receipt, successor_document, successor_bundle_receipt, effective_document, effective_bundle_receipt, status: Literal["EFFECTIVE_SELECTED", "REWORK"], diagnostics)`. `ПРИНЯТО` and a valid `AUTO_FIX_APPLIED` map to `EFFECTIVE_SELECTED`; `ТРЕБУЕТ ДОРАБОТКИ` maps to `REWORK` and has no effective document/receipt. Blocker/manual classification is downstream and does not suppress selection of an otherwise valid reviewed revision.
- Produces `orchestrate_revision(candidate, review_artifact, output_dir, csv_profile, publisher=publish_bundle, verifier=verify_bundle) -> OrchestrationResult` and CLI `--candidate`, `--review`, `--output-dir`, `--csv-profile`.
- `orchestrate_revision` always schema+semantic-validates and publishes the candidate first, then validates reviewer coverage/base digest/verdict, publishes a valid full successor only for AUTO_FIX, and finally selects one effective document. A rework verdict returns the immutable candidate receipt plus status `REWORK` and no effective document.
- Pipeline artifact registry uses the locked candidate/successor/effective document and bundle-receipt names and the V3 automation/trace artifacts.
- `candidate_document` is always published before review. `successor_document` is schema+semantic+lineage validated and published before selection. Only `effective_document` and its bare digest flow downstream.
- Candidate and successor bundle receipts are immutable audit evidence; selection does not rewrite either bundle.

- [ ] **Step 1: Add accepted/AUTO_FIX/rework and downstream-status orchestration RED tests**

Call `orchestrate_revision` with a recording fake publisher/verifier and assert candidate-before-review/successor order, exact selected digest propagation, missing or mismatched receipt rejection, no effective output on rework, valid blocker/manual documents still selecting an effective revision, and V2.1 route rejection. Separately assert the final orchestrator schema's closed status enum and branch mapping: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`, `BLOCKED`, `FAIL`, `NOT_RUNNABLE`.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_orchestration_v3 -v`

Expected: FAIL because the orchestration facade is absent and pipeline V1 routes original/corrected case arrays rather than effective documents.

- [ ] **Step 3: Implement the production orchestration facade**

Compose only `require_valid_canonical_document`, `publish_bundle`, `validate_successor`, `select_effective_document`, and `verify_bundle`; do not duplicate their rules. Before returning an effective selection, call `verify_bundle(effective_document, output_dir, csv_profile)` against the three published files, store that returned read-back receipt as `effective_bundle_receipt`, and prove its `document_sha256` equals `document_sha256(effective_document)`. Receipts are returned evidence, not persisted manifests.

- [ ] **Step 4: Bump the machine pipeline contract to `2.0`**

Update artifact IDs, stage accepts/forwards/produces, transitions, and traceability through step/expectation/assertion/file/symbol/evidence. Update `contract_check.py` exact constants and invariants in the same change.

- [ ] **Step 5: Update orchestrator output and integrity checks**

Require all selected source and receipt digests to agree. Add the common schema, projection/publisher/validator tools, and V3 fixtures to `doctor.py` integrity checks without touching skill discovery files.

- [ ] **Step 6: Run GREEN**

Run:

```powershell
python -m unittest tests.test_orchestration_v3 -v
python tools\doctor.py --root .
python tools\contract_check.py --root .
```

Expected: all commands PASS. Full drift check waits until generated docs are regenerated in Task 13.

### Task 13: Skill Contracts and Behavior Evals

**Files:**

- Modify: `skills/context-marker/SKILL.md`
- Modify: `skills/context-marker/references/context-artifact-contract.md`
- Modify: `skills/tc-generator/SKILL.md`
- Modify: `skills/tc-generator/references/case-generation-contract.md`
- Modify: `skills/tc-reviewer/SKILL.md`
- Modify: `skills/tc-reviewer/references/review-verdicts.md`
- Modify: `skills/tc-to-autotest/SKILL.md`
- Modify: `skills/tc-to-autotest/references/automation-output-contract.md`
- Modify: `skills/tc-to-autotest/assets/java-python-conventions/python-pytest.md`
- Modify: `skills/tc-to-autotest/assets/java-python-conventions/java-junit5.md`
- Modify: `skills/autotest-reviewer/SKILL.md`
- Modify: `skills/autotest-reviewer/references/autotest-review-contract.md`
- Modify: `skills/orchestrate/SKILL.md`
- Modify: `skills/orchestrate/references/orchestration-contract.md`
- Create: `evals/zephyr-test-case-projection/scenarios.json`
- Create: `evals/zephyr-test-case-projection/rubric.md`
- Create: `tests/test_skill_contracts_v3.py`

**Interfaces:**

- Every skill emits/consumes the exact locked V3 field names and directs deterministic outputs through schemas/tools rather than prose reconstruction.
- Generator outputs JSON only and supports arbitrary sequential steps, human actions/results, stable IDs, typed step-output data flow, assertions, and blockers.
- Reviewer returns a complete successor only when safe and non-destructive.
- Autotest stages consume only the selected canonical document, never Markdown/CSV.
- Orchestrator publishes each valid candidate/successor bundle and passes one effective digest downstream.
- Both Python/Java convention assets require pair identity, global provider preflight before any test symbol, one-attempt/no-redirect/no-retry/no-decompression HTTP behavior, and explicit atomic operation/assertion relation emission.

- [ ] **Step 1: Add static contract RED tests**

Assert no changed skill/reference/Java-Python asset contains `2.1.0`, exact-one-step rules, `test_data`, `expected_outcome`, partial `corrected_test_cases`, old `automation_matrix`, or instructions to parse Markdown/CSV for automation. Assert every skill links its V3 schema/reference and names its stop conditions; assert both language assets name `(file_id, symbol_id)`, preflight, one attempt, and atomic relations.

- [ ] **Step 2: Create synthetic behavior scenarios and exact rubric**

The scenarios must include: three-step order create/reuse/confirm; 11-step data flow; mixed manual/automatic; missing technical context under pressure; reviewer correction in a multi-case document; attempted stable-identity deletion; one runtime symbol implementing multiple targets; exact human Markdown with no visible Test Data. The rubric checks schema validity, semantic validity, human format, no invented facts, and exact stop behavior.

- [ ] **Step 3: Run the baseline controls before editing skills**

For each changed guidance/control variant, run at least five fresh-context agent samples against the current HEAD skill text and record only aggregate failure categories in temporary working notes. Do not store model outputs that may contain supplied workplace data.

- [ ] **Step 4: Rewrite skills and references to the executable V3 contracts**

Retain project-native discovery, confinement, and secret rules. Remove duplicated schema prose where a reference to the common schema/tool is sufficient, but keep all human test-design and stop rules required for reliable generation.

- [ ] **Step 5: Run static GREEN**

Run: `python -m unittest tests.test_skill_contracts_v3 -v`

Expected: all static contract checks PASS.

- [ ] **Step 6: Run fresh-context behavior GREEN samples**

Run at least five new samples per control/guidance variant with the same synthetic scenarios. Manually score each against `rubric.md`; any repeated bypass or invented technical field reopens this task. If no fresh-agent harness is available, mark behavior validation unverified rather than substituting the parent agent's own output.

### Task 14: Documentation, Generated Contract Views, and Built-In Fixtures

**Files:**

- Modify: `README.md`
- Modify: `USAGE.md`
- Modify: `HOW-IT-WORKS.md`
- Modify: `tools/render_contract_docs.py`
- Regenerate: `CONTRACTS.md`
- Regenerate: `PIPELINE.md`
- Modify: remaining V2.1 JSON fixtures under `skills/orchestrate/assets/orchestration-fixtures/`
- Create: `tests/test_documentation_v3.py`

**Interfaces:**

- Public docs describe one bare canonical source, the exact human Markdown, the fixed 24-column Zephyr Scale CSV profile, immutable three-artifact revision bundles, effective revision selection, atomic automation relations, and exact trace evidence.
- Docs explicitly state the residual: workbook structure was observed, but a real tenant import round trip is not yet proven.
- `CONTRACTS.md` and `PIPELINE.md` remain generated projections of `contracts/pipeline.json`, never hand-maintained copies.

- [ ] **Step 1: Add stale-doc RED checks**

Search public docs and fixtures for V2.1 field names/routes and assert every command/example references real V3 paths/arguments. Exclude the migration/rejection explanation where `2.1.0` is intentionally named.

- [ ] **Step 2: Run RED**

Run: `python -m unittest tests.test_documentation_v3 -v`

Expected: FAIL because docs still describe the nine-column lossless CSV and original/corrected arrays.

- [ ] **Step 3: Update public docs and examples after executable contracts are stable**

Do not edit `.skillsrc` discovery instructions. Replace only artifact model, lifecycle, CLI, projection, automation, runner, trace, and final-status sections.

- [ ] **Step 4: Regenerate machine-derived docs**

Run: `python tools\render_contract_docs.py --root .`

Review generated `CONTRACTS.md` and `PIPELINE.md`; do not hand-adjust their output.

- [ ] **Step 5: Run GREEN and drift checks**

Run:

```powershell
python -m unittest tests.test_documentation_v3 -v
python tools\render_contract_docs.py --root . --check
python tools\contract_check.py --root . --full
```

Expected: all commands PASS with no projection drift.

### Task 15: Full Verification and Independent Final Review

**Files:**

- Verify only; fixes go back to the owning task/file.

**Interfaces:**

- Produces one evidence summary listing passed commands, exact unverified gates, changed-file scope, and independent Sol findings.

- [ ] **Step 1: Run the complete local suite**

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
python tools\doctor.py --root .
python tools\contract_check.py --root . --full
python tools\render_contract_docs.py --root . --check
python -m compileall -q tools skills tests
git diff --check
```

- [ ] **Step 2: Run exact artifact smoke tests in a temporary directory**

Run:

```powershell
python -m unittest tests.test_bundle_publisher.BundlePublisherTests.test_cli_publish_verify_and_exactly_three_files -v
python -m unittest tests.test_bundle_publisher.BundlePublisherTests.test_concurrent_identical_and_conflicting_publishers -v
```

The first test invokes the real CLI against `TemporaryDirectory`, then invokes `--verify-only`, asserts exactly three files, validates the bare `.json`, and compares Markdown/CSV golden bytes. The second runs identical and differing payload publishers concurrently and asserts no overwrite/mixed receipt.

- [ ] **Step 3: Run optional real-transport parity or record the precise blocker**

Run `python tests\run_http_binding_integration.py`. A default `SKIPPED` result is acceptable for the restricted-workstation suite but must be reported verbatim as an unverified integration gate. If loopback binding and `javac` are available, additionally run `python tests\run_http_binding_integration.py --require-integration`; only its combined Python+Java `PASS` closes real transport parity. The integration runner is outside the `test_*.py` discovery pattern, and no production command or skill invocation depends on it.

- [ ] **Step 4: Run skill behavior evals or record the precise blocker**

Confirm the required fresh-context sample counts and rubric results. If the harness is unavailable, report that gap independently from executable Python contract tests.

- [ ] **Step 5: Verify scope and branch isolation**

Run:

```powershell
git status --short
git diff --name-only
git diff --check
```

Confirm `.skillsrc.example`, `schemas/skillsrc.schema.json`, and `tools/scan_project.py` are absent from the diff and no files outside the isolated worktree changed.

- [ ] **Step 6: Request a fresh Sol code/spec review**

Give the reviewer the approved design hash, implementation plan path, exact diff, and verification output. Require `SHIP` or concrete `FIX-FIRST`; after any code change, rerun local verification and use a new fresh reviewer.

## Dependency and Parallelism Gates

- Tasks 1 -> 2 -> 3 -> 4 -> 5 -> 6 are sequential: they lock one canonical interface and its execution semantics.
- Task 7 depends on Tasks 1–4; Task 8 depends strictly on Task 7 and must not reimplement rendering.
- Task 9 depends on Tasks 1–6. Tasks 10 -> 11 are sequential after Task 9 because runtime identity changes end-to-end.
- Task 12 begins only after Tasks 8 and 11 are GREEN.
- Task 13 begins after executable contracts in Tasks 1–12 stabilize. Task 14 follows Task 13.
- Agents may independently review completed tasks, inspect distinct read-only surfaces, or author non-overlapping synthetic fixtures. Two implementers must never edit the same schema/validator/skill concurrently.

## Self-Review Checklist

- [ ] Every design section 5–18 maps to at least one task above.
- [ ] No second editable test-case model or projection mapping is introduced.
- [ ] All digest carriers use exact bare canonical bytes.
- [ ] All three artifact formats and the fixed 24-column mapping have byte-golden tests.
- [ ] V2.1 is rejected explicitly and no automatic migration is implemented.
- [ ] Reviewer identity preservation covers capability argument/result local identities as well as all nested case entities.
- [ ] Runtime evidence is keyed by `(file_id, symbol_id)` and cannot false-PASS on missing/stale/duplicate records.
- [ ] Manual-only, blockers, preflight `NOT_RUNNABLE`, runtime `ERROR`, and assertion `MISSING` remain distinct.
- [ ] No task modifies automatic-skillsrc-discovery-owned files.
- [ ] No dependency, commit, push, merge, or PR is included.

## Execution Handoff

The user already selected agent-driven execution with separate Sol planning and review. Execute this plan with `superpowers:subagent-driven-development`: one Terra implementation owner per task, parent verification/acceptance after every task, and fresh Sol review at the major gates (canonical core, projection bundle, automation/trace, final branch).
