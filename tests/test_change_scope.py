"""Behavior contracts for reviewed change-scope promotion."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from tools.flow_artifacts import artifact_sha256, canonical_bytes


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def source(path: str, label: str) -> dict[str, str]:
    return {"source_id": "SOURCE-" + hashlib.sha256(b"product_file\0" + path.encode("utf-8")).hexdigest(), "kind": "product_file", "path": path, "content_digest": digest(label)}


def side(path: str, label: str) -> dict[str, Any]:
    return {"source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": path, "content_sha256": digest(label)})).hexdigest(), "content_sha256": digest(label), "size_bytes": 4, "text": True}


def change(kind: str, path: str, before: str | None, after: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"kind": kind, "path": path}
    if before is not None:
        row["before"] = side(path, before)
    if after is not None:
        row["after"] = side(path, after)
    row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
    return row


def input_for(rows: list[dict[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {"schema_version": "1.0.0", "artifact": "change-input", "input_kind": "patch_manifest", "repository_id": digest("repo"), "base": {"snapshot_sha256": artifact_sha256({"repository_id": digest("repo"), "tree": "b" * 40})}, "target": {"snapshot_sha256": digest("head")}, "changes": rows}
    value["change_input_sha256"] = artifact_sha256(value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple) or isinstance(value, list):
        return [thaw(item) for item in value]
    return value


def inventory(sources: list[dict[str, str]]) -> dict[str, Any]:
    return {"module_id": "root", "sources": sources}


def tests_inventory() -> dict[str, Any]:
    file_one, file_two = "FILE-" + "1" * 64, "FILE-" + "2" * 64
    return {"module_id": "root", "test_roots": ["tests"], "files": [{"file_id": file_one, "path": "tests/test_one.py", "language": "python", "framework": "unittest", "content_digest": digest("t1")}, {"file_id": file_two, "path": "tests/test_two.py", "language": "python", "framework": "unittest", "content_digest": digest("t2")}], "symbols": [{"file_id": file_two, "symbol_id": "SYMBOL-" + "2" * 64, "locator": {"kind": "python_module_function", "function_name": "test_two"}}, {"file_id": file_one, "symbol_id": "SYMBOL-" + "1" * 64, "locator": {"kind": "python_module_function", "function_name": "test_one"}}]}


def source_envelope_for(sources: list[dict[str, str]]) -> dict[str, Any]:
    bare = inventory(sources)
    return {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {"authorized_behavior_sources": bare, "authorized_behavior_sources_sha256": artifact_sha256(bare), "technical_test_inventory": tests_inventory(), "technical_test_inventory_sha256": artifact_sha256(tests_inventory())}, "warnings": []}


def context_for(sources: list[dict[str, str]]) -> dict[str, Any]:
    bare = inventory(sources); source_ids = sorted(row["source_id"] for row in sources)
    managed = {"authorized_behavior_sources_sha256": artifact_sha256(bare), "requirements": [{"requirement_id": "REQ-1", "display_order": 1, "text": "Requirement one.", "provenance": ["inventory"]}], "product_sources": [{"source_id": row["source_id"], "kind": "product_file", "path": row["path"], "content_digest": row["content_digest"], "summary": "Reviewed product source."} for row in sources], "requirement_sources": [{"requirement_id": "REQ-1", "source_ids": source_ids}]}
    accounting = {"authorized_behavior_sources_sha256": artifact_sha256(bare), "context_receipt_sha256": digest("context"), "source_dispositions": [{"source_id": row["source_id"], "disposition": "represented", "requirement_ids": ["REQ-1"]} for row in sources], "behavior_fragment_groups": []}
    return {"schema_version": "5.0.0", "stage": "context-marker", "artifacts": {"managed_behavior_context": managed, "behavior_source_accounting": accounting}, "warnings": []}


def predecessor_for(project: Path, sources: list[dict[str, str]], context_override: dict[str, Any] | None = None) -> Any:
    """Mint the smallest real Task-2 predecessor capability for scope tests."""
    from tools.baseline_lifecycle import bind_scope_predecessor, validate_baseline_receipt
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=project, capture_output=True, check=True).stdout.decode().strip()
    source_envelope, context = source_envelope_for(sources), context_override or context_for(sources)
    authorized = source_envelope["artifacts"]["authorized_behavior_sources"]
    source_ids = [row["source_id"] for row in sources]
    fragments = [{"fragment_id": "FRAGMENT-" + hashlib.sha256(row["source_id"].encode()).hexdigest(), "source_id": row["source_id"], "item_id": f"ITEM-{index:06d}"} for index, row in enumerate(sources, 1)]
    receipt = {"schema_version": "1.0.0", "selected_module": "root", "authorized_behavior_sources_sha256": artifact_sha256(authorized), "context_plan_sha256": digest("plan"), "batch_result_sha256s": [digest("batch")], "fragment_registry": fragments, "source_outcomes": [{"source_id": row["source_id"], "domain_key": "core", "outcome": "behavior_fragments"} for row in sources]}
    context["artifacts"]["behavior_source_accounting"]["context_receipt_sha256"] = artifact_sha256(receipt)
    context["artifacts"]["behavior_source_accounting"]["behavior_fragment_groups"] = [{"group_id": "GROUP-1", "fragment_ids": [row["fragment_id"] for row in fragments], "requirement_ids": ["REQ-1"]}]
    repository = digest(str(Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()))
    commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    artifacts = {name: digest(name) for name in ("technical_test_inventory_sha256", "authorized_behavior_sources_sha256", "managed_behavior_context_sha256", "changed_behavior_context_sha256", "behavior_source_accounting_sha256", "behavior_context_receipt_sha256", "effective_technical_evidence_sha256", "effective_document_sha256", "effective_bundle_receipt_sha256", "trace_document_sha256", "trace_audit_sha256", "orchestrator_output_sha256", "terminal_run_receipt_sha256")}
    artifacts.update({"technical_test_inventory_sha256": artifact_sha256(source_envelope), "authorized_behavior_sources_sha256": artifact_sha256(source_envelope), "managed_behavior_context_sha256": artifact_sha256(context), "behavior_source_accounting_sha256": artifact_sha256(context), "behavior_context_receipt_sha256": artifact_sha256(receipt)})
    fingerprints = {name: digest(name) for name in ("pipeline_contract_sha256", "policy_bundle_sha256", "tool_bundle_sha256", "schema_bundle_sha256")}
    baseline_receipt = {"schema_version": "1.0.0", "artifact": "feature-baseline-receipt", "predecessor_baseline_sha256": None, "run_mode": "FULL", "repository_id": repository, "target_commit": commit, "target_tree": tree, "selected_module": "root", "analytics_sha256": digest("analytics"), "artifacts": artifacts, "fingerprints": fingerprints}
    baseline = validate_baseline_receipt(baseline_receipt, project, "root", fingerprints)
    return bind_scope_predecessor(baseline, source_envelope, context, receipt)


def bound_change(project: Path, value: dict[str, Any]) -> dict[str, Any]:
    """Bind synthetic semantic rows to the real repository identity used by the test capability."""
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=project, capture_output=True, check=True).stdout.decode().strip()
    value = copy.deepcopy(value); bad_base = value.get("base", {}).get("snapshot_sha256") == "x"; repository = digest(str(Path(git("rev-parse", "--path-format=absolute", "--git-common-dir")).resolve())); commit, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    value["repository_id"] = repository; value["base"] = {"commit": commit, "tree": tree, "snapshot_sha256": artifact_sha256({"repository_id": repository, "tree": tree})}
    if value["input_kind"] == "git_range": value["target"] = {"commit": commit, "tree": tree, "snapshot_sha256": artifact_sha256({"repository_id": repository, "tree": tree})}
    elif value["input_kind"] == "patch_manifest": value["base"] = {"snapshot_sha256": "x" if bad_base else artifact_sha256({"repository_id": repository, "tree": tree})}; value["target"] = {"snapshot_sha256": digest("patch-target")}
    value["change_input_sha256"] = artifact_sha256({key: item for key, item in value.items() if key != "change_input_sha256"})
    return value


class ChangeScopeTests(unittest.TestCase):
    """A production change that breaks each assertion violates scope authority."""

    def setUp(self) -> None:
        from tools.change_scope import ScopeInputs
        self.ScopeInputs = ScopeInputs
        self.module = {"id": "root", "paths": {"source": ["app"]}}
        self.project = Path.cwd()
        self.current = [source("app/core/a.py", "new-a"), source("app/core/b.py", "b"), source("app/other/c.py", "c")]
        self.old = [source("app/core/a.py", "old-a"), source("app/core/b.py", "b"), source("app/other/c.py", "c")]

    def start_change(self, relations: tuple[dict[str, Any], ...] = (), *, change_input: dict[str, Any] | None = None, current: list[dict[str, str]] | None = None, old: list[dict[str, str]] | None = None, source_envelope: dict[str, Any] | None = None, context_envelope: dict[str, Any] | None = None) -> Any:
        from tools.change_scope import start_scope
        current, old = current or self.current, old or self.old
        if source_envelope is not None or context_envelope is not None:
            raise AssertionError("Capability fixture owns predecessor envelopes.")
        return start_scope(self.ScopeInputs(self.project, "CHANGE_SET", self.module, digest("analytics"), bound_change(self.project, change_input or input_for([change("modified", "app/core/a.py", "old-a", "new-a")])), inventory(current), tests_inventory(), predecessor_for(self.project, old), relations))

    def candidate(self, snapshot: Any) -> dict[str, Any]:
        from tools.change_scope import advance_scope
        if snapshot.candidate is not None:
            return thaw(snapshot.candidate)
        action = advance_scope(snapshot)
        self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind)
        return thaw(action.artifact)

    def accept(self, snapshot: Any, candidate: dict[str, Any]) -> Any:
        from tools.change_scope import advance_scope, record_scope
        first = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []}
        snapshot = record_scope(snapshot, candidate)
        self.assertEqual("RUN_SCOPE_FALSE_INCLUSION_AUDIT", advance_scope(snapshot).kind)
        snapshot = record_scope(snapshot, first)
        self.assertEqual("RUN_SCOPE_OMISSION_AUDIT", advance_scope(snapshot).kind)
        second = {**first, "audit_kind": "omission"}
        return record_scope(snapshot, second)

    def test_diff_seed_expands_relation_closure_and_ordered_test_pairs(self) -> None:
        """Removing relation reachability would omit indirect product/test evidence."""
        relations = (
            {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[1]["source_id"], "to_source_id": self.current[2]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}},
            {"relation_kind": "SYMBOL_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}},
            {"relation_kind": "TEST_EVIDENCE_RELATION", "from_source_id": self.current[2]["source_id"], "to_source_id": tests_inventory()["files"][1]["file_id"], "evidence_locator": {"content_sha256": self.current[2]["content_digest"], "start_byte": 0, "end_byte": 1}},
        )
        candidate = self.candidate(self.start_change(relations))
        self.assertEqual([row["source_id"] for row in self.current], [row["source_id"] for row in candidate["included_sources"]])
        self.assertEqual([(tests_inventory()["symbols"][0]["file_id"], tests_inventory()["symbols"][0]["symbol_id"])], [(row["file_id"], row["symbol_id"]) for row in candidate["included_test_symbols"]])

    def test_reverse_import_relation_includes_dependent_importer(self) -> None:
        """Traversing only dependency direction would omit a changed dependency's importer."""
        relation = {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[1]["source_id"], "to_source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}}
        candidate = self.candidate(self.start_change((relation,)))
        self.assertEqual([self.current[0]["source_id"], self.current[1]["source_id"]], [row["source_id"] for row in candidate["included_sources"]])

    def test_ambiguity_advances_fixed_widening_levels_once_then_blocks(self) -> None:
        """Skipping/repeating widening levels would make uncertainty user-tunable."""
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
            self.start_change(({"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": "foreign", "evidence_locator": {"content_sha256": digest("foreign"), "start_byte": 0, "end_byte": 1}},))

    def test_full_projects_every_current_source_with_full_refresh(self) -> None:
        """Narrowing a FULL scope would violate mandatory baseline coverage."""
        from tools.change_scope import start_scope
        snapshot = start_scope(self.ScopeInputs(Path.cwd(), "FULL", {"id": "root"}, digest("analytics"), None, inventory(self.current), tests_inventory(), None))
        candidate = self.candidate(snapshot)
        self.assertIsNone(candidate["baseline_receipt_sha256"])
        self.assertIsNone(candidate["change_input_sha256"])
        self.assertEqual("full", candidate["widening_level"])
        self.assertEqual(["FULL_REFRESH"] * 3, [row["reason"] for row in candidate["included_sources"]])

    def test_change_sides_join_authoritative_inventory_by_path_and_digest(self) -> None:
        """Using Task-1 side IDs instead of inventory identities would select foreign rows."""
        candidate = self.candidate(self.start_change())
        self.assertEqual(self.current[0]["source_id"], candidate["included_sources"][0]["source_id"])

    def test_missing_required_change_side_join_rejects_before_candidate(self) -> None:
        """A changed side absent from its authoritative inventory must never promote empty scope."""
        from tools.change_scope import start_scope
        current = inventory(self.current[1:])
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_COVERAGE"):
            start_scope(self.ScopeInputs(self.project, "CHANGE_SET", self.module, digest("analytics"), bound_change(self.project, input_for([change("modified", "app/core/a.py", "old-a", "new-a")])), current, tests_inventory(), predecessor_for(self.project, self.old)))

    def test_change_input_rejects_bad_snapshot_duplicate_row_and_side_identity(self) -> None:
        """Accepting malformed Task-1 identities would disconnect scope from the reviewed diff."""
        valid = input_for([change("modified", "app/core/a.py", "old-a", "new-a")])
        cases: list[tuple[str, dict[str, Any]]] = []
        bad_snapshot = copy.deepcopy(valid); bad_snapshot["base"]["snapshot_sha256"] = "x"; bad_snapshot["change_input_sha256"] = artifact_sha256({key: value for key, value in bad_snapshot.items() if key != "change_input_sha256"}); cases.append(("snapshot", bad_snapshot))
        duplicate = copy.deepcopy(valid); duplicate["changes"].append(copy.deepcopy(duplicate["changes"][0])); duplicate["change_input_sha256"] = artifact_sha256({key: value for key, value in duplicate.items() if key != "change_input_sha256"}); cases.append(("duplicate", duplicate))
        forged_side = copy.deepcopy(valid); forged_side["changes"][0]["after"]["source_id"] = "SOURCE-" + "0" * 64; row = dict(forged_side["changes"][0]); row.pop("change_id"); forged_side["changes"][0]["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest(); forged_side["change_input_sha256"] = artifact_sha256({key: value for key, value in forged_side.items() if key != "change_input_sha256"}); cases.append(("side", forged_side))
        for name, value in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "CHANGE_SCOPE"):
                self.start_change(change_input=value)

    def test_worktree_target_snapshot_binds_canonical_change_rows(self) -> None:
        """A valid-looking worktree target digest cannot replace its frozen public change projection."""
        value = input_for([change("modified", "app/core/a.py", "old-a", "new-a")])
        value["input_kind"] = "git_worktree"; value["base"] = {"commit": "a" * 40, "tree": "b" * 40, "snapshot_sha256": artifact_sha256({"repository_id": digest("repo"), "tree": "b" * 40})}; value["target"] = {"snapshot_sha256": digest("forged-worktree")}; value["change_input_sha256"] = artifact_sha256({key: item for key, item in value.items() if key != "change_input_sha256"})
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_BINDING"):
            self.start_change(change_input=value)

    def test_every_change_variant_requires_authoritative_side_inventory_join(self) -> None:
        """Missing any before/after authority must reject every public Task-1 change variant."""
        def closed(row: dict[str, Any]) -> dict[str, Any]:
            row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
            return row
        binary_before, binary_after = side("app/core/a.py", "old-a"), side("app/core/a.py", "new-a")
        binary_before["text"] = binary_after["text"] = False
        renamed_after = source("app/core/renamed.py", "new-a")
        cases = (
            ("added", closed({"kind": "added", "path": "app/added.py", "after": side("app/added.py", "added")}), self.current, self.old),
            ("deleted", closed({"kind": "deleted", "path": "app/core/a.py", "before": side("app/core/a.py", "old-a")}), self.current, self.old[1:]),
            ("modified", change("modified", "app/core/a.py", "old-a", "new-a"), self.current[1:], self.old),
            ("renamed", closed({"kind": "renamed", "old_path": "app/core/a.py", "new_path": "app/core/renamed.py", "similarity_basis": "git-raw-rename-100", "before": side("app/core/a.py", "old-a"), "after": side("app/core/renamed.py", "new-a")}), [renamed_after, *self.current[1:]], self.old[1:]),
            ("binary", closed({"kind": "binary", "path": "app/core/a.py", "binary_change": "modified", "before": binary_before, "after": binary_after}), self.current[1:], self.old),
        )
        for name, row, current, old in cases:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_COVERAGE"):
                self.start_change(change_input=input_for([row]), current=current, old=old)

    def test_real_worktree_carrier_preserves_private_drift_guard(self) -> None:
        """Flattening a frozen worktree carrier would lose drift authority between review transitions."""
        from tools.baseline_lifecycle import ValidatedBaseline
        from tools.change_scope import advance_scope, record_scope, start_scope
        from tools.git_change_adapter import ChangeInputSpec, acquire_change_input
        def git(project: Path, *args: str) -> str:
            completed = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr.decode("utf-8", "replace"))
            return completed.stdout.decode("utf-8").strip()
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"; project.mkdir(); git(project, "init"); git(project, "config", "user.email", "scope@example.invalid"); git(project, "config", "user.name", "Scope")
            (project / "app.py").write_text("before\n", encoding="utf-8"); git(project, "add", "."); git(project, "commit", "-m", "base"); base_commit = git(project, "rev-parse", "HEAD")
            (project / "app.py").write_text("frozen\n", encoding="utf-8")
            frozen = acquire_change_input(project, ChangeInputSpec(base_commit, None, True, None))
            row = frozen["changes"][0]; before = {**source("app.py", "before"), "content_digest": row["before"]["content_sha256"]}; after = {**source("app.py", "frozen"), "content_digest": row["after"]["content_sha256"]}
            base_inventory, current_inventory = inventory([before]), inventory([after])
            source_envelope, context = source_envelope_for([before]), context_for([before])
            snapshot = start_scope(self.ScopeInputs(project, "CHANGE_SET", self.module, digest("analytics"), frozen, current_inventory, tests_inventory(), predecessor_for(project, [before]))); candidate = self.candidate(snapshot)
            self.assertFalse(any(isinstance(value, (bytes, Path)) for value in snapshot.inputs.values()))
            (project / "app.py").write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "CHANGE_SOURCE_DRIFT"):
                start_scope(self.ScopeInputs(project, "CHANGE_SET", self.module, digest("analytics"), frozen, {"broken": True}, tests_inventory(), predecessor_for(project, [before])))
            with self.assertRaisesRegex(ValueError, "CHANGE_SOURCE_DRIFT"):
                advance_scope(snapshot)
            with self.assertRaisesRegex(ValueError, "CHANGE_SOURCE_DRIFT"):
                record_scope(snapshot, candidate)

    def test_candidate_reason_relation_conjunction_rejects_direct_and_foreign_ids(self) -> None:
        """A reason without its exact relation binding could fabricate indirect scope evidence."""
        from tools.change_scope import record_scope
        from tools.schema_validation import schema_diagnostics
        relation = {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}}
        snapshot = self.start_change((relation,)); candidate = self.candidate(snapshot); root = Path(__file__).resolve().parents[1]
        direct = thaw(candidate); direct["included_sources"][0]["relation_ids"] = ["RELATION-" + "0" * 64]
        indirect = thaw(candidate); indirect["included_sources"][1]["relation_ids"] = ["RELATION-" + "f" * 64]
        self.assertNotEqual([], schema_diagnostics(direct, root / "schemas/change-scope-candidate.schema.json", root))
        for name, malformed in (("direct", direct), ("foreign", indirect)):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
                record_scope(snapshot, malformed)

    def test_predecessor_context_inner_authority_must_bind_baseline_sources(self) -> None:
        """Rebinding a self-consistent foreign context digest would misstate predecessor requirements."""
        context = context_for(self.old); foreign = digest("foreign-authorized")
        context["artifacts"]["managed_behavior_context"]["authorized_behavior_sources_sha256"] = foreign
        context["artifacts"]["behavior_source_accounting"]["authorized_behavior_sources_sha256"] = foreign
        with self.assertRaisesRegex(ValueError, "BASELINE_BINDING"):
            predecessor_for(self.project, self.old, context)

    def test_nested_private_values_cannot_enter_public_snapshot(self) -> None:
        """Freezing a nested path/bytes value would persist controller-private state in scope artifacts."""
        from tools.change_scope import start_scope
        technical = tests_inventory(); technical["symbols"][0]["locator"]["private"] = Path("secret")
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_SHAPE"):
            start_scope(self.ScopeInputs(Path.cwd(), "FULL", {"id": "root", "paths": {"source": ["app"]}}, digest("analytics"), None, inventory(self.current), technical, None))

    def test_false_inclusion_finding_pointer_must_bind_cited_candidate_source(self) -> None:
        """A free-form pointer could remove an indirect source without identifying the reviewed row."""
        from tools.change_scope import record_scope
        relation = {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}}
        snapshot = self.start_change((relation,)); candidate = self.candidate(snapshot); snapshot = record_scope(snapshot, candidate)
        finding = {"code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": "/does-not-exist", "source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Pointer does not bind the candidate row."}
        audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
            record_scope(snapshot, audit)

    def test_first_omission_successor_is_material_not_file_label_replay(self) -> None:
        """A symbol-to-file successor that changes only its label cannot repair an omission."""
        from tools.change_scope import advance_scope, record_scope
        snapshot = self.start_change(); first = self.candidate(snapshot); snapshot = record_scope(snapshot, first)
        snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(first), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []})
        finding = {"code": "IMPACT_CLOSURE_INCOMPLETE", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Need material widening."}
        snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(first), "audit_kind": "omission", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]})
        second = thaw(advance_scope(snapshot).artifact)
        projection = lambda value: {key: value[key] for key in value if key not in {"generation", "parent_candidate_sha256", "triggering_audit_sha256s", "widening_level"}}
        self.assertNotEqual(artifact_sha256(projection(first)), artifact_sha256(projection(second)))

    def test_audits_require_same_generation_and_rework_seals_predecessor(self) -> None:
        """Promoting after one/mixed/late audit would bypass dual review."""
        from tools.change_scope import advance_scope, record_scope
        snapshot = self.start_change(); candidate = self.candidate(snapshot); snapshot = record_scope(snapshot, candidate)
        bad = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "omission", "verdict": "ACCEPT", "findings": []}
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_ORDER"):
            record_scope(snapshot, bad)
        finding = {"code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Unrelated impact."}
        rework = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        self.assertEqual("BLOCKED", advance_scope(record_scope(snapshot, rework)).kind)

    def test_receipt_projects_candidate_after_two_accepts_and_is_immutable(self) -> None:
        """Changing receipt projection or mutable accepted state would corrupt promotion authority."""
        from tools.change_scope import advance_scope
        snapshot = self.start_change(); candidate = self.candidate(snapshot); snapshot = self.accept(snapshot, candidate)
        action = advance_scope(snapshot)
        self.assertEqual("COMPLETE", action.kind)
        self.assertEqual([self.current[0]["source_id"]], list(action.artifact["included_source_ids"]))
        self.assertEqual("SEQUENTIAL", action.artifact["review_mode"])
        self.assertIsInstance(action.artifact, MappingProxyType)
        with self.assertRaises(TypeError):
            action.artifact["review_mode"] = "INDEPENDENT"

    def test_schema_rejects_extra_and_relations_fixture_is_not_receipt(self) -> None:
        """Permitting hybrid/extra fields would weaken V1 closed contracts."""
        from tools.schema_validation import schema_diagnostics
        root = Path(__file__).resolve().parents[1]
        candidate = self.candidate(self.start_change()); candidate["extra"] = True
        self.assertNotEqual([], schema_diagnostics(candidate, root / "schemas/change-scope-candidate.schema.json", root))
        relations = json.loads((root / "tests/fixtures/change-scope/relations.json").read_text(encoding="utf-8"))
        self.assertNotEqual([], schema_diagnostics(relations, root / "schemas/change-scope-receipt.schema.json", root))

    def test_rejects_wrong_inventory_module_and_unsafe_relation_evidence(self) -> None:
        """Accepting foreign module identity or unsafe relation locator would mix authority."""
        from tools.change_scope import start_scope
        bad_inventory = inventory(self.current); bad_inventory["module_id"] = "foreign"
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_SHAPE"):
            start_scope(self.ScopeInputs(Path.cwd(), "FULL", {"id": "root"}, digest("analytics"), None, bad_inventory, tests_inventory(), None))
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
            self.start_change(({"relation_kind": "FOREIGN", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": -1, "end_byte": 0}},))

    def test_audit_conjunction_finding_identity_and_duplicate_order_reject(self) -> None:
        """Accept/findings mismatch, wrong code, noncanonical ID, or duplicate audit must reject."""
        from tools.change_scope import record_scope
        snapshot = self.start_change(); candidate = self.candidate(snapshot); snapshot = record_scope(snapshot, candidate)
        malformed = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": [{"finding_id": "FINDING-" + "a" * 64, "code": "IMPACT_SOURCE_OMITTED", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Bad finding."}]}
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
            record_scope(snapshot, malformed)
        accepted = {**malformed, "verdict": "ACCEPT", "findings": []}
        snapshot = record_scope(snapshot, accepted)
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_ORDER"):
            record_scope(snapshot, accepted)

    def test_actions_and_diagnostics_are_recursively_immutable(self) -> None:
        """Mutating returned action evidence or safe diagnostics would alter controller state."""
        from tools.change_scope import advance_scope
        action = advance_scope(self.start_change())
        self.assertIsInstance(action.artifact["included_sources"], tuple)
        with self.assertRaises(TypeError):
            action.artifact["included_sources"][0]["reason"] = "FULL_REFRESH"
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_BINDING"):
            advance_scope(type(self.start_change())(self.start_change().inputs, blocked=True))

    def test_schemas_reject_nested_extras_and_fabricated_independence(self) -> None:
        """Nested extra keys and JSON-only independent claims must not become receipt authority."""
        from tools.schema_validation import schema_diagnostics
        root = Path(__file__).resolve().parents[1]
        snapshot = self.start_change(); candidate = self.candidate(snapshot); snapshot = self.accept(snapshot, candidate)
        from tools.change_scope import advance_scope
        receipt = thaw(advance_scope(snapshot).artifact); receipt["included_test_symbol_pairs"].append({"file_id": "x", "symbol_id": "y", "extra": True})
        self.assertNotEqual([], schema_diagnostics(receipt, root / "schemas/change-scope-receipt.schema.json", root))
        receipt = advance_scope(snapshot, controller={"review_mode": "INDEPENDENT"})
        self.assertEqual("SEQUENTIAL", receipt.artifact["review_mode"])
        self.assertIsNone(receipt.artifact["independence_attestation_sha256"])

    def test_schema_conditionals_reject_mode_audit_and_assurance_hybrids(self) -> None:
        """Closed schemas must reject invalid lineage, verdict, mode, and assurance conjunctions."""
        from tools.schema_validation import schema_diagnostics
        from tools.change_scope import advance_scope
        root = Path(__file__).resolve().parents[1]
        snapshot = self.start_change(); candidate = self.candidate(snapshot)
        bad_candidate = thaw(candidate); bad_candidate["parent_candidate_sha256"] = digest("parent")
        self.assertNotEqual([], schema_diagnostics(bad_candidate, root / "schemas/change-scope-candidate.schema.json", root))
        audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": [{"finding_id": "FINDING-" + "a" * 64, "code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Bad."}]}
        self.assertNotEqual([], schema_diagnostics(audit, root / "schemas/change-scope-audit.schema.json", root))
        snapshot = self.accept(snapshot, candidate); receipt = thaw(advance_scope(snapshot).artifact); receipt["independence_attestation_sha256"] = digest("fabricated")
        self.assertNotEqual([], schema_diagnostics(receipt, root / "schemas/change-scope-receipt.schema.json", root))

    def test_rework_successor_changes_digest_and_old_audit_cannot_replay(self) -> None:
        """A byte-identical successor would let a rejected generation be rehabilitated."""
        from tools.change_scope import advance_scope, record_scope
        relation = {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}}
        snapshot = self.start_change((relation,)); first = self.candidate(snapshot); snapshot = record_scope(snapshot, first)
        finding = {"code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": "/included_sources/1", "source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Unrelated impact."}
        rejected = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(first), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        successor_action = advance_scope(record_scope(snapshot, rejected)); second = thaw(successor_action.artifact)
        self.assertNotEqual(artifact_sha256(first), artifact_sha256(second))
        self.assertEqual("symbol", second["widening_level"])
        successor = record_scope(record_scope(snapshot, rejected), second)
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
            record_scope(successor, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(first), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []})

    def test_false_inclusion_rework_removes_indirect_row_not_only_lineage(self) -> None:
        """A false-inclusion successor must remove cited indirect evidence, not merely rehash lineage."""
        from tools.change_scope import advance_scope, record_scope
        relation = {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}}
        snapshot = self.start_change((relation,)); first = self.candidate(snapshot); snapshot = record_scope(snapshot, first)
        finding = {"code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": "/included_sources/1", "source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Indirect source is unrelated."}
        audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(first), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        second = thaw(advance_scope(record_scope(snapshot, audit)).artifact)
        projection = lambda value: {key: value[key] for key in value if key not in {"generation", "parent_candidate_sha256", "triggering_audit_sha256s"}}
        self.assertNotEqual(artifact_sha256(projection(first)), artifact_sha256(projection(second)))
        self.assertNotIn(self.current[1]["source_id"], [row["source_id"] for row in second["included_sources"]])

    def test_false_correction_recomputes_transitive_closure_and_test_pairs(self) -> None:
        """Removing B must also remove relation-derived C and its technical evidence."""
        from tools.change_scope import advance_scope, record_scope
        relations = (
            {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}},
            {"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[1]["source_id"], "to_source_id": self.current[2]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}},
            {"relation_kind": "TEST_EVIDENCE_RELATION", "from_source_id": self.current[2]["source_id"], "to_source_id": tests_inventory()["files"][1]["file_id"], "evidence_locator": {"content_sha256": self.current[2]["content_digest"], "start_byte": 0, "end_byte": 1}},
        )
        snapshot = self.start_change(tuple(sorted(relations, key=lambda row: (row["relation_kind"], row["from_source_id"], row["to_source_id"], canonical_bytes(row["evidence_locator"]))))); first = self.candidate(snapshot); snapshot = record_scope(snapshot, first)
        finding = {"code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": "/included_sources/1", "source_id": self.current[1]["source_id"], "evidence_locator": {"content_sha256": self.current[1]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Indirect source is unrelated."}
        audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(first), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        successor = thaw(advance_scope(record_scope(snapshot, audit)).artifact)
        self.assertEqual([self.current[0]["source_id"]], [row["source_id"] for row in successor["included_sources"]])
        self.assertEqual([], successor["included_test_symbols"])

    def test_repeated_rework_walks_fixed_widening_without_skips(self) -> None:
        """Changing the fixed widening ladder would permit unsafe user-selected scope."""
        from tools.change_scope import advance_scope, record_scope
        snapshot = self.start_change(); seen: list[str] = []
        for expected in ("symbol", "file", "domain", "module", "full"):
            candidate = self.candidate(snapshot); seen.append(candidate["widening_level"])
            if snapshot.candidate is None:
                snapshot = record_scope(snapshot, candidate)
            accepted = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []}
            snapshot = record_scope(snapshot, accepted)
            finding = {"code": "IMPACT_CLOSURE_INCOMPLETE", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Closure is incomplete."}
            audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "omission", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
            snapshot = record_scope(snapshot, audit)
            if expected != "full":
                snapshot = record_scope(snapshot, thaw(advance_scope(snapshot).artifact))
        self.assertEqual(["symbol", "file", "domain", "module", "full"], seen)
        self.assertEqual("BLOCKED", advance_scope(snapshot).kind)

    def test_symbol_widening_rejection_blocks_without_lineage_replay(self) -> None:
        """The narrowest substantive scope cannot be "corrected" by cloning itself."""
        from tools.change_scope import advance_scope, record_scope
        snapshot = self.start_change(); candidate = self.candidate(snapshot); snapshot = record_scope(snapshot, candidate)
        finding = {"code": "IMPACT_WIDENING_UNNECESSARY", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "No narrower scope exists."}
        audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        self.assertEqual("BLOCKED", advance_scope(record_scope(snapshot, audit)).kind)

    def test_widening_materially_adds_domain_module_and_full_refresh(self) -> None:
        """Relabelling a scope without its domain/module evidence would leave omissions unresolved."""
        from tools.change_scope import advance_scope, record_scope
        snapshot = self.start_change(); candidates: list[dict[str, Any]] = []
        for level in ("symbol", "file", "domain", "module", "full"):
            candidate = self.candidate(snapshot); candidates.append(candidate)
            if snapshot.candidate is None: snapshot = record_scope(snapshot, candidate)
            snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []})
            finding = {"code": "IMPACT_CLOSURE_INCOMPLETE", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Closure is incomplete."}
            snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "omission", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]})
            if level != "full": snapshot = record_scope(snapshot, thaw(advance_scope(snapshot).artifact))
        self.assertEqual(["symbol", "file", "domain", "module", "full"], [row["widening_level"] for row in candidates])
        self.assertEqual({row["symbol_id"] for row in tests_inventory()["symbols"]}, {row["symbol_id"] for row in candidates[1]["included_test_symbols"]})
        self.assertEqual({self.current[0]["source_id"], self.current[1]["source_id"]}, {row["source_id"] for row in candidates[2]["included_sources"]})
        self.assertEqual({row["source_id"] for row in self.current}, {row["source_id"] for row in candidates[3]["included_sources"]})
        self.assertEqual(["FULL_REFRESH"] * 3, [row["reason"] for row in candidates[4]["included_sources"]])

    def test_full_omission_rework_blocks_lineage_only_replay(self) -> None:
        """A full omission REWORK cannot emit a successor with only lineage changes."""
        # The material widening test reaches a correctable full candidate; this
        # focused assertion exercises the action before it is recorded.
        from tools.change_scope import advance_scope, record_scope
        snapshot = self.start_change()
        for _ in range(4):
            candidate = self.candidate(snapshot)
            if snapshot.candidate is None: snapshot = record_scope(snapshot, candidate)
            snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []})
            finding = {"code": "IMPACT_CLOSURE_INCOMPLETE", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Closure is incomplete."}
            snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "omission", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]})
            snapshot = record_scope(snapshot, thaw(advance_scope(snapshot).artifact))
        candidate = self.candidate(snapshot); snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []})
        finding = {"code": "IMPACT_CLOSURE_INCOMPLETE", "candidate_pointer": "/included_sources/0", "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": "Closure is incomplete."}
        snapshot = record_scope(snapshot, {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "omission", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]})
        self.assertEqual("BLOCKED", advance_scope(snapshot).kind)

    def test_hash_matching_invalid_predecessor_envelope_is_not_baseline_authority(self) -> None:
        """A digest alone must not authenticate a schema-invalid predecessor envelope."""
        from tools.baseline_lifecycle import ValidatedBaseline, bind_scope_predecessor
        source_envelope = {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {"authorized_behavior_sources": inventory(self.old), "technical_test_inventory": tests_inventory(), "extra": True}, "warnings": []}
        context_envelope = {"schema_version": "5.0.0", "stage": "context-marker", "artifacts": {"managed_behavior_context": {"authorized_behavior_sources_sha256": artifact_sha256(inventory(self.old)), "requirements": [{"requirement_id": "REQ-1", "display_order": 1}], "product_sources": [], "requirement_sources": []}, "behavior_source_accounting": {"authorized_behavior_sources_sha256": artifact_sha256(inventory(self.old)), "context_receipt_sha256": digest("context"), "source_dispositions": [], "behavior_fragment_groups": []}}, "warnings": []}
        baseline = ValidatedBaseline(digest("repo"), "a" * 40, "b" * 40, "root", artifact_sha256(source_envelope), artifact_sha256(context_envelope), digest("document"), digest("bundle"), MappingProxyType({}), digest("receipt"), MappingProxyType({}))
        with self.assertRaisesRegex(ValueError, "BASELINE_BINDING"):
            bind_scope_predecessor(baseline, source_envelope, context_envelope, {"schema_version": "1.0.0"})

    def test_scope_predecessor_is_opaque_issued_capability(self) -> None:
        """Copying or allocating a carrier must not reproduce its authority or expose a receipt."""
        from tools.baseline_lifecycle import scope_predecessor_projection
        predecessor = predecessor_for(self.project, self.old)
        self.assertFalse(hasattr(predecessor, "receipt"))
        for forged in (copy.copy(predecessor), object.__new__(type(predecessor)), {"sources": self.old}):
            with self.subTest(kind=type(forged).__name__), self.assertRaisesRegex(ValueError, "BASELINE_BINDING"):
                scope_predecessor_projection(forged)

    def test_audit_schema_and_runtime_share_closed_pointer_and_control_rules(self) -> None:
        """Schema must not bless pointer/text variants that runtime rejects."""
        from tools.change_scope import record_scope
        from tools.schema_validation import schema_diagnostics
        snapshot = self.start_change(); candidate = self.candidate(snapshot); snapshot = record_scope(snapshot, candidate)
        root = Path(__file__).resolve().parents[1]
        def audit(pointer: str, summary: str) -> dict[str, Any]:
            finding = {"code": "IMPACT_SOURCE_UNRELATED", "candidate_pointer": pointer, "source_id": self.current[0]["source_id"], "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}, "summary": summary}
            return {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "REWORK", "findings": [{"finding_id": "FINDING-" + hashlib.sha256(canonical_bytes(finding)).hexdigest(), **finding}]}
        for pointer, summary in (("/arbitrary", "Bad."), ("/included_sources/00", "Bad."), ("/included_sources/0", "Bad.\x7f")):
            value = audit(pointer, summary)
            with self.subTest(pointer=pointer, summary=summary), self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
                record_scope(snapshot, value)
            self.assertNotEqual([], schema_diagnostics(value, root / "schemas/change-scope-audit.schema.json", root))

    def test_forged_change_row_and_invalid_relation_reject_not_widen(self) -> None:
        """Missing change sides and foreign relation endpoints cannot silently become ambiguity."""
        with self.assertRaisesRegex(ValueError, "CHANGE_SCOPE_AUDIT"):
            self.start_change(({"relation_kind": "IMPORT_RELATION", "from_source_id": self.current[0]["source_id"], "to_source_id": "foreign", "evidence_locator": {"content_sha256": self.current[0]["content_digest"], "start_byte": 0, "end_byte": 1}},))


if __name__ == "__main__":
    unittest.main()
