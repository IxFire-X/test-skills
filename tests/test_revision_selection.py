from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from types import MappingProxyType


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.canonical_document import document_sha256, validate_canonical_document  # noqa: E402
from tools.revision_selection import (  # noqa: E402
    SelectionError,
    select_effective_document,
    validate_successor,
)


def _type(kind: str = "string") -> dict[str, str]:
    return {"kind": "json", "type": kind}


def _management() -> dict[str, object]:
    return {
        "status": None, "folder": None, "components": [], "labels": [],
        "owner": None, "estimated_time": None, "external_keys": {},
        "external_links": {"issues": [], "pages": []}, "custom_fields": {},
    }


def _regular_step(case_token: str, step_token: str, order: int) -> dict[str, object]:
    return {
        "step_id": f"STEP-{step_token}", "display_order": order,
        "action": f"Read {step_token}.", "manual_only": False,
        "manual_reason": None, "automation_blockers": [],
        "operation": {
            "kind": "http", "binding_profile": "http-binding-v1",
            "base_url_source": {
                "kind": "environment", "name": "API_BASE_URL",
                "provenance": ["https://example.invalid/runtime"],
            },
            "method": "GET", "path": "/items/{item_id}",
        },
        "inputs": [{
            "input_id": f"INPUT-{case_token}-{step_token}", "display_order": 1,
            "target": {"location": "path", "name": "item_id", "sensitive": False},
            "source": {"kind": "literal", "value": step_token},
            "semantic_type": _type(),
        }],
        "outputs": [{
            "output_id": f"status_{step_token}", "display_order": 1,
            "source": {"kind": "http_status"}, "semantic_type": _type("integer"),
        }],
        "expectations": [{
            "expectation_id": f"EXP-{case_token}-{step_token}", "display_order": 1,
            "text": "The service returns 200.",
            "assertions": [{
                "assertion_id": f"ASSERT-{case_token}-{step_token}", "display_order": 1,
                "actual": {"kind": "http_status"}, "operator": "equals",
                "expected": {"kind": "literal", "value": 200},
            }],
        }],
    }


def _blocked_step(case_token: str, step_token: str, order: int) -> dict[str, object]:
    return {
        "step_id": f"STEP-{step_token}", "display_order": order,
        "action": f"Confirm {step_token} when context is missing.",
        "manual_only": False, "manual_reason": None,
        "automation_blockers": [
            {
                "blocker_id": f"BLOCK-{case_token}-{step_token}-assertion",
                "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions",
                "reason": "Assertion evidence is unavailable without the operation.",
                "provenance": ["https://example.invalid/context"],
            },
            {
                "blocker_id": f"BLOCK-{case_token}-{step_token}-operation",
                "code": "UNRESOLVED_OPERATION", "field_path": "/operation",
                "reason": "Technical operation is unavailable.",
                "provenance": ["https://example.invalid/context"],
            },
        ],
        "operation": None, "inputs": [], "outputs": [],
        "expectations": [{
            "expectation_id": f"EXP-{case_token}-{step_token}", "display_order": 1,
            "text": "The blocked operation is documented.", "assertions": [],
        }],
    }


def rich_document() -> dict[str, object]:
    """A test-local valid graph with all stable-identity node kinds and siblings."""
    return {
        "document_id": "TCDOC-revision-selection", "revision": 1,
        "parent_sha256": None,
        "metadata": {
            "subject": {"kind": "http_endpoint", "method": "GET", "path": "/items/{item_id}"},
            "documentation": ["https://example.invalid/spec"],
            "project": "EXAMPLE", "author": "TEST_AUTHOR", "date": "2026-08-12",
        },
        "operation_capabilities": [
            {
                "capability_id": "CAP-alpha", "adapter": "example", "action": "alpha",
                "arguments": [{"name": "token", "semantic_type": _type(), "required": False}],
                "results": [{"name": "value", "semantic_type": _type()}],
                "provenance": ["https://example.invalid/capabilities"],
            },
            {
                "capability_id": "CAP-beta", "adapter": "example", "action": "beta",
                "arguments": [{"name": "limit", "semantic_type": _type("integer"), "required": False}],
                "results": [{"name": "count", "semantic_type": _type("integer")}],
                "provenance": ["https://example.invalid/capabilities"],
            },
        ],
        "requirements": [
            {"requirement_id": "REQ-alpha", "display_order": 1, "text": "Alpha works.", "provenance": ["https://example.invalid/spec#alpha"]},
            {"requirement_id": "REQ-beta", "display_order": 2, "text": "Beta works.", "provenance": ["https://example.invalid/spec#beta"]},
        ],
        "test_cases": [
            {
                "case_id": "TC-alpha", "display_order": 1, "requirement_ids": ["REQ-alpha"],
                "title": "Read alpha", "objective": "Verify alpha.",
                "categories": ["positive", "functional"], "priority": "HIGH",
                "preconditions": [], "management": _management(),
                "steps": [_regular_step("alpha", "alpha-one", 1), _blocked_step("alpha", "alpha-two", 2)],
            },
            {
                "case_id": "TC-beta", "display_order": 2, "requirement_ids": ["REQ-beta"],
                "title": "Read beta", "objective": "Verify beta.",
                "categories": ["positive", "functional"], "priority": "HIGH",
                "preconditions": [], "management": _management(),
                "steps": [_regular_step("beta", "beta-one", 1), _blocked_step("beta", "beta-two", 2)],
            },
        ],
    }


def successor_of(candidate: dict[str, object]) -> dict[str, object]:
    successor = copy.deepcopy(candidate)
    successor["revision"] = candidate["revision"] + 1
    successor["parent_sha256"] = document_sha256(candidate)
    return successor


def report_for(candidate: dict[str, object], verdict: str = "ПРИНЯТО") -> dict[str, object]:
    return {
        "verdict": verdict,
        "candidate": {
            "document_id": candidate["document_id"], "revision": candidate["revision"],
            "document_sha256": document_sha256(candidate),
        },
        "reviewed_case_ids": [case["case_id"] for case in candidate["test_cases"]],
        "findings": [], "corrections": [],
    }


def _swap(container: list[object], other: list[object]) -> None:
    container[0], other[0] = other[0], container[0]


def _remove_requirement_and_reassign(document: dict[str, object]) -> None:
    document["requirements"].pop(0)
    document["test_cases"][0]["requirement_ids"] = ["REQ-beta"]


def _rename_case(document: dict[str, object]) -> None:
    document["test_cases"][0]["case_id"] = "TC-gamma"


def _reparent_step(document: dict[str, object]) -> None:
    alpha_steps = document["test_cases"][0]["steps"]
    beta_steps = document["test_cases"][1]["steps"]
    beta_steps.append(alpha_steps.pop(0))
    for index, step in enumerate(alpha_steps, 1):
        step["display_order"] = index
    for index, step in enumerate(beta_steps, 1):
        step["display_order"] = index


def _reverse_cases(document: dict[str, object]) -> None:
    document["test_cases"].reverse()
    for index, case in enumerate(document["test_cases"], 1):
        case["display_order"] = index


def _remove_alpha_step_after_case_reorder(document: dict[str, object]) -> None:
    _reverse_cases(document)
    alpha_steps = document["test_cases"][1]["steps"]
    alpha_steps.pop(0)
    for index, step in enumerate(alpha_steps, 1):
        step["display_order"] = index


def _reparent_alpha_assertion_after_case_reorder(document: dict[str, object]) -> None:
    _reverse_cases(document)
    beta_step = document["test_cases"][0]["steps"][0]
    alpha_step = document["test_cases"][1]["steps"][0]
    assertion = alpha_step["expectations"][0]["assertions"].pop()
    alpha_step["automation_blockers"] = [{
        "blocker_id": "BLOCK-alpha-alpha-one-assertion",
        "code": "UNRESOLVED_ASSERTION", "field_path": "/expectations/0/assertions",
        "reason": "Assertion evidence is unavailable after review reparenting.",
        "provenance": ["https://example.invalid/context"],
    }]
    beta_assertions = beta_step["expectations"][0]["assertions"]
    beta_assertions.append(assertion)
    for index, item in enumerate(beta_assertions, 1):
        item["display_order"] = index


def codes(diagnostics: list[dict[str, str]] | tuple[object, ...]) -> set[str]:
    return {item["code"] for item in diagnostics}


class RevisionSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidate = rich_document()

    def assert_error(self, callable_, code: str, path: str) -> SelectionError:
        with self.assertRaises(SelectionError) as raised:
            callable_()
        diagnostic = next(item for item in raised.exception.diagnostics if item["code"] == code)
        self.assertEqual(path, diagnostic["path"])
        return raised.exception

    def test_rich_document_satisfies_canonical_precondition(self) -> None:
        """Selection fixtures must begin as schema-and-semantic-valid documents."""
        self.assertEqual([], validate_canonical_document(self.candidate))

    def test_accepted_returns_exact_candidate(self) -> None:
        """Returning a copy or successor for an accepted review would select the wrong revision."""
        self.assertIs(self.candidate, select_effective_document(self.candidate, report_for(self.candidate)))

    def test_auto_fix_returns_exact_valid_successor(self) -> None:
        """Ignoring a valid reviewer successor would discard an approved correction."""
        successor = successor_of(self.candidate)
        self.assertEqual([], validate_successor(self.candidate, successor))
        self.assertIs(successor, select_effective_document(self.candidate, report_for(self.candidate, "AUTO_FIX_APPLIED"), successor))

    def test_auto_fix_lineage_guards_are_independent(self) -> None:
        """Each lineage guard prevents a successor from silently changing ancestry."""
        mutations = (
            ("document id", lambda document: document.__setitem__("document_id", "TCDOC-other"), "SUCCESSOR_DOCUMENT_ID", "/document_id"),
            ("revision", lambda document: document.__setitem__("revision", 9), "SUCCESSOR_REVISION", "/revision"),
            ("parent digest", lambda document: document.__setitem__("parent_sha256", "sha256:" + "0" * 64), "SUCCESSOR_PARENT_SHA256", "/parent_sha256"),
        )
        for name, mutate, code, path in mutations:
            with self.subTest(name=name):
                successor = successor_of(self.candidate)
                mutate(successor)
                self.assert_error(lambda: select_effective_document(self.candidate, report_for(self.candidate, "AUTO_FIX_APPLIED"), successor), code, path)

    def test_report_identity_rejects_stale_values_and_envelope_digest(self) -> None:
        """A stale or envelope-derived review report must not authorize selection."""
        cases = (
            ("document id", ("candidate", "document_id"), "TCDOC-stale", "REVIEW_CANDIDATE_DOCUMENT_ID", "/candidate/document_id"),
            ("revision", ("candidate", "revision"), 9, "REVIEW_CANDIDATE_REVISION", "/candidate/revision"),
            ("revision boolean", ("candidate", "revision"), True, "REVIEW_CANDIDATE_REVISION", "/candidate/revision"),
            ("revision float", ("candidate", "revision"), 1.0, "REVIEW_CANDIDATE_REVISION", "/candidate/revision"),
            ("digest", ("candidate", "document_sha256"), document_sha256({"artifacts": {"canonical_document": self.candidate}}), "REVIEW_CANDIDATE_DOCUMENT_SHA256", "/candidate/document_sha256"),
        )
        for name, path, value, code, expected_path in cases:
            with self.subTest(name=name):
                report = report_for(self.candidate)
                report[path[0]][path[1]] = value
                self.assert_error(lambda: select_effective_document(self.candidate, report, successor_of(self.candidate)), code, expected_path)

    def test_reviewed_case_ids_must_exactly_cover_candidate_in_physical_order(self) -> None:
        """Set-like coverage would accept incomplete, duplicate, foreign, or permuted review."""
        expected = ["TC-alpha", "TC-beta"]
        for name, reviewed in (
            ("missing", ["TC-alpha"]), ("duplicate", ["TC-alpha", "TC-alpha"]),
            ("foreign", expected + ["TC-foreign"]), ("partial", ["TC-beta"]),
            ("permuted", list(reversed(expected))),
        ):
            with self.subTest(name=name):
                report = report_for(self.candidate)
                report["reviewed_case_ids"] = reviewed
                self.assert_error(lambda: select_effective_document(self.candidate, report, successor_of(self.candidate)), "REVIEWED_CASE_IDS_MISMATCH", "/reviewed_case_ids")

    def test_verdict_branches_and_missing_successor(self) -> None:
        """Verdict-specific successor handling prevents accidental downstream selection."""
        auto = report_for(self.candidate, "AUTO_FIX_APPLIED")
        self.assert_error(lambda: select_effective_document(self.candidate, auto), "SUCCESSOR_REQUIRED", "/successor_document")
        rework = report_for(self.candidate, "ТРЕБУЕТ ДОРАБОТКИ")
        self.assert_error(lambda: select_effective_document(self.candidate, rework), "NO_EFFECTIVE_DOCUMENT", "/verdict")
        forbidden = self.assert_error(lambda: select_effective_document(self.candidate, rework, successor_of(self.candidate)), "SUCCESSOR_FORBIDDEN", "/successor_document")
        self.assertIn("successor is forbidden", next(item["message"] for item in forbidden.diagnostics if item["code"] == "SUCCESSOR_FORBIDDEN"))
        self.assert_error(lambda: select_effective_document(self.candidate, report_for(self.candidate, "UNKNOWN")), "REVIEW_VERDICT", "/verdict")
        self.assert_error(lambda: select_effective_document(self.candidate, report_for(self.candidate), successor_of(self.candidate)), "SUCCESSOR_FORBIDDEN", "/successor_document")

    def test_malformed_report_fields_emit_diagnostics_without_exception_leaks(self) -> None:
        """Malformed report fragments must remain controlled diagnostics, not Python errors."""
        for name, mutate, code, path in (
            ("candidate omitted", lambda report: report.pop("candidate"), "REVIEW_CANDIDATE_DOCUMENT_ID", "/candidate/document_id"),
            ("case ids scalar", lambda report: report.__setitem__("reviewed_case_ids", "TC-alpha"), "REVIEWED_CASE_IDS_MISMATCH", "/reviewed_case_ids"),
            ("verdict list", lambda report: report.__setitem__("verdict", []), "REVIEW_VERDICT", "/verdict"),
        ):
            with self.subTest(name=name):
                report = report_for(self.candidate)
                mutate(report)
                self.assert_error(lambda: select_effective_document(self.candidate, report), code, path)

    def test_report_failures_precede_successor_diagnostics(self) -> None:
        """Checking successor first could hide a stale review report behind unrelated errors."""
        report = report_for(self.candidate, "AUTO_FIX_APPLIED")
        report["candidate"]["revision"] = 9
        successor = successor_of(self.candidate)
        successor["document_id"] = "TCDOC-other"
        error = self.assert_error(lambda: select_effective_document(self.candidate, report, successor), "REVIEW_CANDIDATE_REVISION", "/candidate/revision")
        self.assertNotIn("SUCCESSOR_DOCUMENT_ID", codes(error.diagnostics))

    def test_removed_or_reparented_every_identity_kind_is_rejected_without_position_matching(self) -> None:
        """Stable-key loss/reparenting must fail for every kind even when array positions change."""
        mutations = {
            "capability": (lambda d: d["operation_capabilities"].pop(0), "IDENTITY_REMOVED", "/operation_capabilities"),
            "requirement": (_remove_requirement_and_reassign, "IDENTITY_REMOVED", "/requirements"),
            "case": (_rename_case, "IDENTITY_GRAPH_CHANGED", "/test_cases"),
            "argument": (lambda d: d["operation_capabilities"][1]["arguments"].append(d["operation_capabilities"][0]["arguments"].pop(0)), "IDENTITY_GRAPH_CHANGED", "/operation_capabilities/0/arguments"),
            "result": (lambda d: d["operation_capabilities"][1]["results"].append(d["operation_capabilities"][0]["results"].pop(0)), "IDENTITY_GRAPH_CHANGED", "/operation_capabilities/0/results"),
            "step": (_reparent_step, "IDENTITY_GRAPH_CHANGED", "/test_cases/0/steps"),
            "input": (lambda d: _swap(d["test_cases"][0]["steps"][0]["inputs"], d["test_cases"][1]["steps"][0]["inputs"]), "IDENTITY_GRAPH_CHANGED", "/test_cases/0/steps/0/inputs"),
            "output": (lambda d: _swap(d["test_cases"][0]["steps"][0]["outputs"], d["test_cases"][1]["steps"][0]["outputs"]), "IDENTITY_GRAPH_CHANGED", "/test_cases/0/steps/0/outputs"),
            "blocker": (lambda d: _swap(d["test_cases"][0]["steps"][1]["automation_blockers"], d["test_cases"][1]["steps"][1]["automation_blockers"]), "IDENTITY_GRAPH_CHANGED", "/test_cases/0/steps/1/automation_blockers"),
            "expectation": (lambda d: _swap(d["test_cases"][0]["steps"][0]["expectations"], d["test_cases"][1]["steps"][0]["expectations"]), "IDENTITY_GRAPH_CHANGED", "/test_cases/0/steps/0/expectations"),
            "assertion": (lambda d: _swap(d["test_cases"][0]["steps"][0]["expectations"][0]["assertions"], d["test_cases"][1]["steps"][0]["expectations"][0]["assertions"]), "IDENTITY_GRAPH_CHANGED", "/test_cases/0/steps/0/expectations/0/assertions"),
        }
        for kind, (mutate, expected_code, expected_path) in mutations.items():
            with self.subTest(kind=kind):
                successor = successor_of(self.candidate)
                mutate(successor)
                diagnostics = validate_successor(self.candidate, successor)
                self.assertIn(expected_code, codes(diagnostics))
                self.assertEqual(expected_path, next(item["path"] for item in diagnostics if item["code"] == expected_code))

    def test_child_diagnostics_use_successor_parent_positions_after_case_reorder(self) -> None:
        """Candidate indices must not label child diagnostics after a valid successor reorder."""
        mutations = (
            ("step deletion", _remove_alpha_step_after_case_reorder, "IDENTITY_REMOVED", "/test_cases/1/steps"),
            ("assertion reparent", _reparent_alpha_assertion_after_case_reorder, "IDENTITY_GRAPH_CHANGED", "/test_cases/1/steps/0/expectations/0/assertions"),
        )
        for name, mutate, code, path in mutations:
            with self.subTest(name=name):
                successor = successor_of(self.candidate)
                mutate(successor)
                self.assertEqual([], validate_canonical_document(successor))
                diagnostics = validate_successor(self.candidate, successor)
                self.assertEqual(path, next(item["path"] for item in diagnostics if item["code"] == code))

    def test_capability_argument_and_result_renames_are_graph_changes(self) -> None:
        """Renaming either role would lose a capability-local identity."""
        for role in ("arguments", "results"):
            with self.subTest(role=role):
                successor = successor_of(self.candidate)
                successor["operation_capabilities"][0][role][0]["name"] = "renamed"
                self.assertIn("IDENTITY_GRAPH_CHANGED", codes(validate_successor(self.candidate, successor)))

    def test_capability_member_role_is_part_of_its_identity(self) -> None:
        """Treating argument and result names as one namespace would permit a role change."""
        successor = successor_of(self.candidate)
        member = successor["operation_capabilities"][0]["arguments"].pop(0)
        successor["operation_capabilities"][0]["results"].append({"name": member["name"], "semantic_type": member["semantic_type"]})
        self.assertIn("IDENTITY_GRAPH_CHANGED", codes(validate_successor(self.candidate, successor)))

    def test_missing_parent_suppresses_descendant_cascades(self) -> None:
        """Deleting a parent must emit its one diagnostic rather than every descendant's."""
        successor = successor_of(self.candidate)
        successor["test_cases"].pop(0)
        diagnostics = validate_successor(self.candidate, successor)
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("/test_cases", diagnostics[0]["path"])

    def test_additions_and_text_or_order_only_edits_preserve_graph(self) -> None:
        """Graph equality would wrongly reject permitted additions and nonidentity edits."""
        successor = successor_of(self.candidate)
        successor["requirements"][0]["text"] = "Alpha text revised."
        successor["test_cases"][0]["title"] = "Reworded alpha"
        successor["test_cases"].reverse()
        for index, case in enumerate(successor["test_cases"], 1):
            case["display_order"] = index
        successor["operation_capabilities"].append({
            "capability_id": "CAP-gamma", "adapter": "example", "action": "gamma",
            "arguments": [], "results": [], "provenance": ["https://example.invalid/capabilities"],
        })
        self.assertEqual([], validate_successor(self.candidate, successor))

    def test_selection_error_is_deeply_immutable_and_deterministic(self) -> None:
        """Mutable or nondeterministic diagnostics would make caller handling unreliable."""
        successor = successor_of(self.candidate)
        successor["document_id"] = "TCDOC-other"
        successor["revision"] = 9
        error = self.assert_error(lambda: select_effective_document(self.candidate, report_for(self.candidate, "AUTO_FIX_APPLIED"), successor), "SUCCESSOR_DOCUMENT_ID", "/document_id")
        self.assertEqual(tuple(sorted(error.diagnostics, key=lambda item: (item["path"], item["code"], item["message"]))), error.diagnostics)
        self.assertTrue(all(isinstance(item, MappingProxyType) for item in error.diagnostics))
        with self.assertRaises(TypeError):
            error.diagnostics[0]["code"] = "changed"
        self.assertEqual(json.dumps([dict(item) for item in error.diagnostics], ensure_ascii=False, separators=(",", ":")), str(error))


if __name__ == "__main__":
    unittest.main()
