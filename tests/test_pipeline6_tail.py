from __future__ import annotations

import tempfile
import unittest
import subprocess
import json
import sys
from dataclasses import fields
from pathlib import Path

from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes
from tools.contract_check import materialize_fingerprint_registries
from tools.pipeline6_tail import PipelineTailAction, _terminal_change_input, advance_pipeline6_tail
from tests.test_baseline_lifecycle import build_run, make_project, persist_terminal, bundle_for
from tools.baseline_lifecycle import advance_baseline, bind_effective_baseline, validate_baseline_receipt
from tools.document_delta import apply_document_delta, delta_application_receipt, unchanged_document_selection
from tests.test_document_delta import _delta, _receipt_and_context


_ROOT = Path(__file__).resolve().parents[1]


def _current_registries() -> dict:
    contract = json.loads((_ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
    return materialize_fingerprint_registries(_ROOT, contract)


def _projected_fingerprints(registries: dict) -> dict:
    return {name + "_sha256": registries[name]["sha256"] for name in registries}


class Pipeline6TailTests(unittest.TestCase):
    class _ReadyRegistry:
        def require(self, adapter: str, action: str) -> None:
            return None

    class _ReadyResolver:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, kind: str, name: str) -> str:
            self.calls += 1
            if kind == "environment":
                return "https://example.invalid"
            raise LookupError()

    def test_cli_direct_and_module_emit_the_same_safe_blocked_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            arguments = ["--project", str(_ROOT), "--run-root", str(Path(temp) / "missing-run"), "--baseline-root", str(Path(temp) / "missing-baseline")]
            commands = ([sys.executable, str(_ROOT / "tools" / "pipeline6_tail.py"), *arguments], [sys.executable, "-m", "tools.pipeline6_tail", *arguments])
            results = [subprocess.run(command, cwd=_ROOT, text=True, capture_output=True, check=False) for command in commands]
            self.assertTrue(all(result.returncode == 2 and result.stdout == "" for result in results))
            payloads = [json.loads(result.stderr) for result in results]
            self.assertEqual(payloads[0], payloads[1])
            self.assertEqual({"kind", "artifact", "record_path", "diagnostics"}, set(payloads[0]))
            self.assertEqual("BLOCKED", payloads[0]["kind"])
            self.assertIsNone(payloads[0]["artifact"])
            self.assertIsNone(payloads[0]["record_path"])

    @staticmethod
    def _install_pytest_shim(project: Path) -> None:
        """Exercise the runner without adding an undeclared pytest dependency."""
        (project / "pytest.py").write_text(
            "import pathlib, runpy, sys\n"
            "if __name__ == '__main__':\n"
            "    target = next(arg for arg in sys.argv if '::' in arg)\n"
            "    path, name = target.split('::', 1)\n"
            "    runpy.run_path(path)[name]()\n"
            "    path = path.replace('\\\\', '/')\n"
            "    xml = pathlib.Path(sys.argv[sys.argv.index('--junitxml') + 1])\n"
            "    xml.write_text('<testsuite><testcase file=\"%s\" classname=\"tests.test_item\" name=\"%s\"/></testsuite>' % (path, name), encoding='utf-8')\n",
            encoding="utf-8",
        )

    def _feature_prefix(self, root: Path, fixture: dict, repository: str, head: str, tree: str, full_change: dict | None = None) -> None:
        run = root / "run"; artifact = fixture["ledger_value"]["artifacts"]
        # The fixture's legacy lifecycle manifest is deliberately replaced by
        # the Task 9 tail-enriched manifest under the same canonical location.
        (run / "manifest" / "prefix-ledger.json").unlink()
        def put(name: str, value: dict) -> dict:
            payload = canonical_bytes(value); path = run / "artifacts" / ("sha256:" + __import__("hashlib").sha256(payload).hexdigest())[7:]
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(payload)
            return {"path": path.relative_to(run).as_posix(), "sha256": "sha256:" + __import__("hashlib").sha256(payload).hexdigest()}
        change = None if full_change is None else put("change", full_change)
        flow = put("flow", {"schema_version": "1.0.0", "artifact": "feature-flow-input", "repository_id": repository, "selected_module": "root", "run_mode": "FULL", "analytics_sha256": fixture["ledger_value"]["analytics_sha256"], "baseline_receipt_sha256": None, "change_input_sha256": None if full_change is None else artifact_sha256(full_change), "skillsrc_sha256": fixture["ledger_value"]["analytics_sha256"], "source_revision": {"commit": head, "tree": tree}})
        document = fixture["document"]
        handoff = put("handoff", {"schema_version": "1.0.0", "artifact": "feature-flow-ready-handoff", "status": "READY_FOR_PIPELINE_TAIL", "run_mode": "FULL", "generator_output": flow, "changed_behavior_context": artifact["changed_behavior_context"], "behavior_context_receipt": artifact["behavior_context_receipt"], "candidate_document": put("candidate", document)})
        bindings = {"flow_input": flow, "source_inventory": artifact["technical_test_inventory"], "change_scope_receipt": artifact["change_scope_receipt"], "context_envelope": artifact["managed_behavior_context"], "changed_behavior_context": artifact["changed_behavior_context"], "behavior_context_receipt": artifact["behavior_context_receipt"], "delta_handoff": handoff}
        if change is not None: bindings["change_input"] = change
        ledger = {"schema_version": "1.0.0", "artifact": "feature-flow-prefix-ledger", "status": "READY_FOR_PIPELINE_TAIL", "run_mode": "FULL", "selected_module": "root", "analytics_sha256": fixture["ledger_value"]["analytics_sha256"], "artifacts": bindings, "records": []}
        path = run / "feature-flow" / "prefix-ledger.json"; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(ledger))

    def test_full_reviewed_tail_replays_to_terminal_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, _, _ = make_project(root)
            (project / "tests").mkdir(); (project / "tests" / "test_item.py").write_text("def test_item(): pass", encoding="utf-8")
            self._install_pytest_shim(project)
            subprocess.run(["git", "add", "."], cwd=project, check=True); subprocess.run(["git", "commit", "-m", "test"], cwd=project, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            fixture = build_run(root / "run", identity, head, tree)
            self._feature_prefix(root, fixture, identity, head, tree)
            records = [
                fixture["ledger_value"] and __import__("json").loads((root / "run" / fixture["ledger_value"]["artifacts"]["technical_test_classification"]["path"]).read_bytes()),
                __import__("json").loads((root / "run" / fixture["ledger_value"]["artifacts"]["classification_review"]["path"]).read_bytes()),
                __import__("json").loads((root / "run" / fixture["ledger_value"]["artifacts"]["validation_report"]["path"]).read_bytes()),
                __import__("json").loads(fixture["tails"]["automation_artifact"].payload),
                __import__("json").loads(fixture["tails"]["autotest_review"].payload),
            ]
            registries = _current_registries()
            adapters = self._ReadyRegistry()
            resolver = self._ReadyResolver()
            action = advance_pipeline6_tail(project, root / "run", root / "baselines", provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            for value in records:
                self.assertNotEqual("BLOCKED", action.kind, action.diagnostics)
                action.record_path.parent.mkdir(parents=True, exist_ok=True); action.record_path.write_bytes(canonical_bytes(value))
                action = advance_pipeline6_tail(project, root / "run", root / "baselines", provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            self.assertEqual("COMPLETE", action.kind, action.diagnostics)
            self.assertIn("terminal_run_receipt", action.artifact)
            self.assertIn(action.artifact["baseline_advancement"]["status"], {"ADVANCED", "IDEMPOTENT"})

    def test_full_worktree_tail_is_provisional_and_replays_without_successor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, _, _ = make_project(root)
            (project / "tests").mkdir(); (project / "tests" / "test_item.py").write_text("def test_item(): pass", encoding="utf-8")
            self._install_pytest_shim(project)
            subprocess.run(["git", "add", "."], cwd=project, check=True); subprocess.run(["git", "commit", "-m", "test"], cwd=project, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            fixture = build_run(root / "run", identity, head, tree)
            self._feature_prefix(root, fixture, identity, head, tree, {"input_kind": "git_worktree", "repository_id": identity, "base": {"commit": head, "tree": tree, "snapshot_sha256": "sha256:" + "a" * 64}, "target": {"snapshot_sha256": "sha256:" + "b" * 64}, "changes": []})
            records = [
                json.loads((root / "run" / fixture["ledger_value"]["artifacts"]["technical_test_classification"]["path"]).read_bytes()),
                json.loads((root / "run" / fixture["ledger_value"]["artifacts"]["classification_review"]["path"]).read_bytes()),
                json.loads((root / "run" / fixture["ledger_value"]["artifacts"]["validation_report"]["path"]).read_bytes()),
                json.loads(fixture["tails"]["automation_artifact"].payload), json.loads(fixture["tails"]["autotest_review"].payload),
            ]
            registries = _current_registries(); adapters = self._ReadyRegistry(); resolver = self._ReadyResolver()
            action = advance_pipeline6_tail(project, root / "run", root / "baselines", provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            for value in records:
                action.record_path.parent.mkdir(parents=True, exist_ok=True); action.record_path.write_bytes(canonical_bytes(value))
                action = advance_pipeline6_tail(project, root / "run", root / "baselines", provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            self.assertEqual("PROVISIONAL", action.kind, action.diagnostics)
            self.assertIsNone(action.artifact["baseline_advancement"]["successor_baseline_sha256"])
            completion_path = root / "run" / "pipeline6-tail" / "completion.json"
            completion = json.loads(completion_path.read_bytes())
            forged = json.loads(json.dumps(completion)); forged["baseline_advancement"]["predecessor_baseline_sha256"] = "sha256:" + "a" * 64
            completion_path.write_bytes(canonical_bytes(forged))
            self.assertEqual("BLOCKED", advance_pipeline6_tail(project, root / "run", root / "baselines", provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries).kind)
            completion_path.write_bytes(canonical_bytes(completion))
            self.assertEqual("PROVISIONAL", advance_pipeline6_tail(project, root / "run", root / "baselines", provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries).kind)

    def test_change_set_zero_op_bypasses_candidate_review_and_validation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, base, base_tree = make_project(root)
            registries = _current_registries()
            baseline_run = build_run(root / "baseline-run", identity, base, base_tree, ledger_mutator=lambda ledger: ledger.update({"fingerprints": registries}))
            baseline = advance_baseline({"project": project, "baseline_root": root / "baselines"}, persist_terminal(root / "baseline-run", baseline_run))
            issued = validate_baseline_receipt(baseline["successor_baseline_receipt"], project, "root", _projected_fingerprints(registries))
            effective = bind_effective_baseline(issued, baseline_run["document"], bundle_for(baseline_run["document"]))
            (project / "feature.txt").write_text("next\n", encoding="utf-8")
            (project / "tests").mkdir(); (project / "tests" / "test_item.py").write_text("def test_item(): pass", encoding="utf-8")
            self._install_pytest_shim(project)
            subprocess.run(["git", "add", "."], cwd=project, check=True); subprocess.run(["git", "commit", "-m", "next"], cwd=project, check=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=project, check=True, capture_output=True, text=True).stdout.strip()
            fixture = build_run(root / "run", identity, head, tree, mode="CHANGE_SET", base=(base, base_tree))
            receipt, context = _receipt_and_context(); applied = apply_document_delta(fixture["document"], _delta(fixture["document"], receipt, context), receipt, context)
            self.assertEqual("UNCHANGED", applied.status)
            selection = unchanged_document_selection(applied, effective)
            run = root / "run"; artifact = fixture["ledger_value"]["artifacts"]
            (run / "manifest" / "prefix-ledger.json").unlink()
            def put(value: dict) -> dict:
                payload = canonical_bytes(value); digest = "sha256:" + __import__("hashlib").sha256(payload).hexdigest(); path = run / "artifacts" / f"{digest[7:]}.json"; path.write_bytes(payload)
                return {"path": path.relative_to(run).as_posix(), "sha256": digest}
            flow = put({"schema_version": "1.0.0", "artifact": "feature-flow-input", "repository_id": identity, "selected_module": "root", "run_mode": "CHANGE_SET", "analytics_sha256": fixture["ledger_value"]["analytics_sha256"], "baseline_receipt_sha256": baseline["successor_baseline_sha256"], "change_input_sha256": artifact_sha256(fixture["ledger_value"]["change_input"]), "skillsrc_sha256": fixture["ledger_value"]["analytics_sha256"], "source_revision": None})
            change = put(fixture["ledger_value"]["change_input"])
            handoff = put({"schema_version": "1.0.0", "artifact": "feature-flow-ready-handoff", "status": "READY_FOR_PIPELINE_TAIL", "run_mode": "CHANGE_SET", "generator_output": flow, "changed_behavior_context": artifact["changed_behavior_context"], "behavior_context_receipt": artifact["behavior_context_receipt"], "delta_application_receipt": put(delta_application_receipt(applied)), "unchanged_document_selection": put(selection), "effective_baseline_document": put(fixture["document"]), "effective_baseline_bundle_receipt": put(__import__("dataclasses").asdict(bundle_for(fixture["document"])))})
            ledger = {"schema_version": "1.0.0", "artifact": "feature-flow-prefix-ledger", "status": "READY_FOR_PIPELINE_TAIL", "run_mode": "CHANGE_SET", "selected_module": "root", "analytics_sha256": fixture["ledger_value"]["analytics_sha256"], "artifacts": {"flow_input": flow, "change_input": change, "source_inventory": artifact["technical_test_inventory"], "change_scope_receipt": artifact["change_scope_receipt"], "context_envelope": artifact["managed_behavior_context"], "changed_behavior_context": artifact["changed_behavior_context"], "behavior_context_receipt": artifact["behavior_context_receipt"], "delta_handoff": handoff}, "records": []}
            path = run / "feature-flow" / "prefix-ledger.json"; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(ledger))
            records = [__import__("json").loads((run / artifact["technical_test_classification"]["path"]).read_bytes()), __import__("json").loads((run / artifact["classification_review"]["path"]).read_bytes()), __import__("json").loads(fixture["tails"]["automation_artifact"].payload), __import__("json").loads(fixture["tails"]["autotest_review"].payload)]
            adapters = self._ReadyRegistry()
            resolver = self._ReadyResolver()
            baseline_path = root / "baselines" / "receipts" / f"{baseline['successor_baseline_sha256'][7:]}.json"
            action = advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            for index, value in enumerate(records):
                self.assertNotEqual("REVIEW_CANDIDATE_DOCUMENT", action.kind)
                action.record_path.parent.mkdir(parents=True, exist_ok=True); action.record_path.write_bytes(canonical_bytes(value))
                if index == len(records) - 1:
                    injected = advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry={"run_result": {"verdict": "PASS"}, "effective_technical_evidence": {}}, fingerprint_registries=registries)
                    self.assertEqual("BLOCKED", injected.kind)
                    self.assertEqual("/adapter_registry", injected.diagnostics[0]["path"])
                action = advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            self.assertEqual("COMPLETE", action.kind, action.diagnostics)
            terminal = __import__("json").loads((run / action.artifact["terminal_run_receipt"]["path"]).read_bytes())
            self.assertEqual("UNCHANGED_BASELINE", terminal["acceptance"]["test_case_review_verdict"])
            self.assertIsNone(terminal["artifacts"]["validation_report_sha256"])
            self.assertIsNotNone(terminal["artifacts"]["run_result_sha256"])
            self.assertIn(action.artifact["baseline_advancement"]["status"], {"ADVANCED", "IDEMPOTENT"})
            self.assertEqual(baseline["successor_baseline_sha256"], action.artifact["baseline_advancement"]["predecessor_baseline_sha256"])
            run_result = __import__("json").loads((run / "artifacts" / f"{terminal['artifacts']['run_result_sha256'][7:]}.json").read_bytes())
            self.assertEqual({"status": "ready", "interpreter": None, "interpreter_path": None, "working_dir": ".", "missing": None}, run_result["environment"])
            self.assertIsNone(run_result["raw_output_excerpt"])
            resolved = resolver.calls
            replay = advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            self.assertEqual("COMPLETE", replay.kind, replay.diagnostics)
            self.assertEqual(action.artifact, replay.artifact)
            self.assertEqual(resolved, resolver.calls)
            completion_path = run / "pipeline6-tail" / "completion.json"
            completion = json.loads(completion_path.read_bytes())
            forged = json.loads(json.dumps(completion)); forged["status"] = "PROVISIONAL"; forged["baseline_advancement"].update({"status": "PROVISIONAL", "predecessor_baseline_sha256": None, "successor_baseline_sha256": None, "successor_baseline_receipt": None})
            completion_path.write_bytes(canonical_bytes(forged))
            self.assertEqual("BLOCKED", advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries).kind)
            completion_path.write_bytes(canonical_bytes(completion))
            self.assertEqual("BLOCKED", advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters).kind)
            stale = json.loads(json.dumps(registries))
            stale["tool_bundle"]["files"][0]["sha256"] = "sha256:" + "a" * 64
            stale["tool_bundle"]["sha256"] = artifact_sha256({"files": stale["tool_bundle"]["files"]})
            self.assertEqual("BLOCKED", advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=stale).kind)
            completion = __import__("json").loads(completion_path.read_bytes())
            forged = __import__("copy").deepcopy(completion)
            forged["baseline_advancement"]["extra"] = True
            completion_path.write_bytes(canonical_bytes(forged))
            self.assertEqual("BLOCKED", advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries).kind)
            forged = __import__("copy").deepcopy(completion)
            forged["baseline_advancement"]["successor_baseline_sha256"] = "sha256:" + "a" * 63 + "/"
            completion_path.write_bytes(canonical_bytes(forged))
            self.assertEqual("BLOCKED", advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries).kind)
            completion_path.write_bytes(canonical_bytes(completion))
            (run / "manifest" / "prefix-ledger.json").write_bytes((root / "baseline-run" / "manifest" / "prefix-ledger.json").read_bytes())
            foreign = advance_pipeline6_tail(project, run, root / "baselines", baseline_path, provider_resolver=resolver, adapter_registry=adapters, fingerprint_registries=registries)
            self.assertEqual("BLOCKED", foreign.kind)
            self.assertEqual("/completion/manifest", foreign.diagnostics[0]["path"])

    def test_missing_receipt_named_payload_blocks_before_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree)
            terminal = persist_terminal(root / "run", fixture)
            # Remove one baseline-required receipt-named projection after terminal construction.
            inventory_digest = __import__("json").loads(terminal.payload)["artifacts"]["technical_test_inventory_sha256"]
            (root / "run" / "artifacts" / f"{inventory_digest[7:]}.json").unlink()
            with self.assertRaises(FlowError):
                advance_baseline({"project": project, "baseline_root": root / "baselines"}, terminal)
            self.assertFalse((root / "baselines" / "links").exists())

    def test_stale_or_forged_tail_record_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, identity, head, tree = make_project(root); fixture = build_run(root / "run", identity, head, tree); self._feature_prefix(root, fixture, identity, head, tree)
            registries = _current_registries()
            action = advance_pipeline6_tail(project, root / "run", root / "baselines", fingerprint_registries=registries)
            forged = action.record_path.with_name("000001-review-automation.json"); forged.parent.mkdir(parents=True, exist_ok=True); forged.write_bytes(canonical_bytes({"artifact": "forged"}))
            blocked = advance_pipeline6_tail(project, root / "run", root / "baselines", recorded_artifact=forged, fingerprint_registries=registries)
            self.assertEqual("BLOCKED", blocked.kind)
            self.assertEqual("FEATURE_FLOW_INPUT", blocked.diagnostics[0]["code"])

    def test_public_action_is_closed_and_recursively_immutable(self) -> None:
        action = PipelineTailAction("CLASSIFY_TECHNICAL_TESTS", {"nested": {"row": ["value"]}}, Path("record.json"))
        self.assertEqual(["kind", "artifact", "record_path", "diagnostics"], [field.name for field in fields(PipelineTailAction)])
        with self.assertRaises(TypeError):
            action.artifact["nested"] = {}  # type: ignore[index]
        with self.assertRaises(TypeError):
            action.artifact["nested"]["row"] = ()  # type: ignore[index]

    def test_missing_or_forged_prefix_never_becomes_tail_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            action = advance_pipeline6_tail(root, root / "run", root / "baselines")
            self.assertEqual("BLOCKED", action.kind)
            (root / "run" / "feature-flow").mkdir(parents=True)
            (root / "run" / "feature-flow" / "prefix-ledger.json").write_bytes(canonical_bytes({"artifact": "forged"}))
            action = advance_pipeline6_tail(root, root / "run", root / "baselines")
            self.assertEqual("BLOCKED", action.kind)

    def test_change_inputs_reduce_to_closed_terminal_identity_and_stale_registries_block(self) -> None:
        digest = "sha256:" + "a" * 64
        repository = "sha256:" + "b" * 64
        commit, tree = "c" * 40, "d" * 40
        variants = {
            "git_range": (
                {"input_kind": "git_range", "repository_id": repository,
                 "base": {"commit": commit, "tree": tree, "snapshot_sha256": digest},
                 "target": {"commit": commit, "tree": tree, "snapshot_sha256": digest},
                 "changes": []},
                {"input_kind": "git_range", "repository_id": repository,
                 "base": {"commit": commit, "tree": tree},
                 "target": {"commit": commit, "tree": tree}},
            ),
            "git_worktree": (
                {"input_kind": "git_worktree", "repository_id": repository,
                 "base": {"commit": commit, "tree": tree, "snapshot_sha256": digest},
                 "target": {"snapshot_sha256": digest}, "changes": []},
                {"input_kind": "git_worktree", "repository_id": repository,
                 "base": {"commit": commit, "tree": tree},
                 "target_snapshot_sha256": digest},
            ),
            "patch_manifest": (
                {"input_kind": "patch_manifest", "repository_id": repository,
                 "base": {"snapshot_sha256": digest}, "target": {"snapshot_sha256": digest},
                 "changes": []},
                {"input_kind": "patch_manifest", "repository_id": repository,
                 "base_snapshot_sha256": digest, "target_snapshot_sha256": digest},
            ),
        }
        for kind, (raw, expected) in variants.items():
            with self.subTest(kind=kind):
                self.assertEqual(expected, _terminal_change_input(raw))

        registries = _current_registries()
        stale = json.loads(json.dumps(registries))
        stale["tool_bundle"]["files"][0]["sha256"] = digest
        stale["tool_bundle"]["sha256"] = artifact_sha256({"files": stale["tool_bundle"]["files"]})
        blocked = advance_pipeline6_tail(Path("missing"), Path("missing-run"), Path("missing-baselines"), fingerprint_registries=stale)
        self.assertEqual("BLOCKED", blocked.kind)
        self.assertEqual("BASELINE_FINGERPRINT", blocked.diagnostics[0]["code"])


if __name__ == "__main__":
    unittest.main()
