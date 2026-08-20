"""Small public checks for the semantic-prefix facade."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from tools.feature_flow import _plain, advance_feature_flow
from tools.flow_artifacts import canonical_bytes
from tools.flow_artifacts import artifact_sha256
from tools.test_classification import build_source_inventories
from tools.skillsrc_manifest import load_skillsrc
from tools.git_change_adapter import ChangeInputSpec


ROOT = Path(__file__).resolve().parents[1]


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)


def _git_value(project: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=project, check=True, capture_output=True, text=True).stdout.strip()


def _project(root: Path, *, modules: int = 1) -> tuple[Path, Path]:
    project = root / "project"; project.mkdir()
    entries = []
    for index in range(modules):
        module_root = "." if modules == 1 else f"m{index + 1}"
        if module_root != ".":
            (project / module_root / "src").mkdir(parents=True)
        entries.append({"id": "root" if modules == 1 else f"m{index + 1}", "root": module_root, "stack": {"language": "python"}, "paths": {"source": ["src"]}, "detected_from": ["pyproject.toml"]})
    manifest = {"version": "3.0", "project": {"name": "fixture"}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": entries}
    (project / ".skillsrc").write_text(json.dumps(manifest), encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'fixture'\nversion = '0.0.0'\n", encoding="utf-8")
    if modules == 1:
        (project / "src").mkdir(); (project / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    else:
        (project / "m1" / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "m2" / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    analytics = root / "analytics.json"; analytics.write_text("{}", encoding="utf-8")
    _git(project, "init"); _git(project, "config", "user.email", "test@example.invalid"); _git(project, "config", "user.name", "Test")
    _git(project, "add", "."); _git(project, "commit", "-m", "initial")
    return project, analytics


def _advanced_baseline(project: Path, root: Path, *, include_app_source: bool = False) -> Path:
    from tests import test_baseline_lifecycle as lifecycle
    from tools.baseline_lifecycle import advance_baseline
    from tools.contract_check import materialize_fingerprint_registries
    from tools.init_skillsrc import ensure_skillsrc

    bootstrap = ensure_skillsrc(project, {}, write=True)
    if bootstrap["status"] != "unchanged":
        _git(project, "add", ".skillsrc"); _git(project, "commit", "-m", "normalize skillsrc")
    head, tree = _git_value(project, "rev-parse", "HEAD"), _git_value(project, "rev-parse", "HEAD^{tree}")
    run, baseline_root = root / "baseline-run", root / "baselines"
    registries = materialize_fingerprint_registries(ROOT, json.loads((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8")))
    fixture = lifecycle.build_run(run, lifecycle.repository_id(project), head, tree, ledger_mutator=lambda ledger: ledger.update({"fingerprints": registries}))
    if include_app_source:
        content = subprocess.run(["git", "show", f"{head}:src/app.py"], cwd=project, check=True, capture_output=True).stdout
        source_id = "SOURCE-" + hashlib.sha256(b"product_file\0src/app.py").hexdigest()
        source = {"source_id": source_id, "kind": "product_file", "path": "src/app.py", "content_digest": "sha256:" + hashlib.sha256(content).hexdigest()}
        def mutate(values: dict[str, dict]) -> None:
            values["technical_test_inventory"]["artifacts"]["authorized_behavior_sources"]["sources"].append(source)
            context = values["managed_behavior_context"]["artifacts"]
            context["behavior_source_accounting"]["source_dispositions"].append({"source_id": source_id, "disposition": "no_supported_observable_fact", "reason": "no_supported_actor_operation_or_outcome_after_full_review"})
            values["behavior_context_receipt"]["source_outcomes"].append({"source_id": source_id, "domain_key": "_product", "outcome": "no_supported_observable_fact"})
        lifecycle.BaselineLifecycleTests().rewrite_prefix_graph(run, fixture, mutate)
    advanced = advance_baseline({"project": project, "baseline_root": baseline_root}, lifecycle.persist_terminal(run, fixture))
    assert advanced["status"] == "ADVANCED"
    for binding in fixture["ledger_value"]["artifacts"].values():
        value = json.loads((run / binding["path"]).read_text(encoding="utf-8"))
        path = baseline_root / "payloads" / "sha256" / f"{artifact_sha256(value)[7:]}.json"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(value))
    for name in ("effective_document", "effective_bundle_receipt"):
        value = json.loads(fixture["tails"][name].path.read_text(encoding="utf-8"))
        path = baseline_root / "payloads" / "sha256" / f"{artifact_sha256(value)[7:]}.json"
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(canonical_bytes(value))
    return baseline_root / "receipts" / f"{advanced['successor_baseline_sha256'][7:]}.json"


def _modified_patch(project: Path, root: Path) -> tuple[ChangeInputSpec, dict[str, bytes], bytes]:
    repository = _git_value(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    repository_id = "sha256:" + hashlib.sha256(str(Path(repository).resolve()).encode("utf-8")).hexdigest()
    tree = _git_value(project, "rev-parse", "HEAD^{tree}")
    before = subprocess.run(["git", "show", "HEAD:src/app.py"], cwd=project, check=True, capture_output=True).stdout
    after = b"VALUE = 2\n"
    def side(value: bytes) -> dict[str, object]:
        digest = "sha256:" + hashlib.sha256(value).hexdigest()
        return {"source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": "src/app.py", "content_sha256": digest})).hexdigest(), "content_sha256": digest, "size_bytes": len(value), "text": True}
    row: dict[str, object] = {"kind": "modified", "path": "src/app.py", "before": side(before), "after": side(after)}
    row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
    manifest = root / "modified-patch.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0.0", "artifact": "patch-manifest", "repository_id": repository_id,
        "base_snapshot_sha256": artifact_sha256({"repository_id": repository_id, "tree": tree}),
        "target_snapshot_sha256": "sha256:" + "a" * 64, "changes": [row],
        "content_blobs": [{"content_sha256": side(before)["content_sha256"], "size_bytes": len(before), "controller_blob_id": "BLOB-before"}, {"content_sha256": side(after)["content_sha256"], "size_bytes": len(after), "controller_blob_id": "BLOB-after"}],
    }), encoding="utf-8")
    return ChangeInputSpec(None, None, False, manifest), {"BLOB-before": before, "BLOB-after": after}, after


class FeatureFlowTests(unittest.TestCase):
    def test_initial_full_requires_exact_committed_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            action = advance_feature_flow(project, analytics, Path(temp) / "run")
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind)
            self.assertIsNotNone(action.artifact); self.assertIsNotNone(action.record_path)

    def test_missing_skillsrc_bootstraps_before_the_prefix_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            (project / ".skillsrc").unlink()
            _git(project, "add", "-u"); _git(project, "commit", "-m", "remove manifest")
            action = advance_feature_flow(project, analytics, Path(temp) / "run")
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind)
            self.assertTrue((project / ".skillsrc").is_file())
            self.assertNotIn("bootstrap", json.dumps(_plain(action.artifact)))

    def test_missing_stale_or_incompatible_baseline_forces_full(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            action = advance_feature_flow(project, analytics, Path(temp) / "run", change_input=ChangeInputSpec("HEAD", "HEAD", False, None))
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind)
            self.assertEqual("FULL", action.artifact["run_mode"])

    def test_stale_live_fingerprint_expectation_forces_full_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); receipt = _advanced_baseline(project, root)
            stale = {name: "sha256:" + "a" * 64 for name in ("pipeline_contract_sha256", "policy_bundle_sha256", "tool_bundle_sha256", "schema_bundle_sha256")}
            with patch("tools.feature_flow._current_fingerprint_projection", return_value=stale):
                action = advance_feature_flow(project, analytics, root / "run", receipt, ChangeInputSpec("HEAD", "HEAD", False, None))
            self.assertEqual("RUN_FULL_BASELINE", action.kind, action.diagnostics)

    def test_git_range_requires_predecessor_target_as_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            action = advance_feature_flow(project, analytics, Path(temp) / "run", change_input=ChangeInputSpec("HEAD", "HEAD", False, None))
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind)
            self.assertEqual("FULL", action.artifact["run_mode"])

    def test_worktree_snapshot_freezes_staged_unstaged_and_untracked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            (project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            run = Path(temp) / "run"
            action = advance_feature_flow(project, analytics, run, change_input=ChangeInputSpec("HEAD", None, True, None))
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind)
            (project / "src" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
            drift = advance_feature_flow(project, analytics, run, change_input=ChangeInputSpec("HEAD", None, True, None))
            self.assertEqual("BLOCKED", drift.kind)
            self.assertEqual("CHANGE_SOURCE_DRIFT", drift.diagnostics[0]["code"])

    def test_empty_patch_manifest_enters_change_set_and_preserves_resolver_on_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); receipt = _advanced_baseline(project, root)
            repository = _git_value(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
            tree = _git_value(project, "rev-parse", "HEAD^{tree}")
            repository_id = "sha256:" + hashlib.sha256(str(Path(repository).resolve()).encode("utf-8")).hexdigest()
            manifest = root / "empty-patch.json"
            manifest.write_text(json.dumps({
                "schema_version": "1.0.0", "artifact": "patch-manifest", "repository_id": repository_id,
                "base_snapshot_sha256": artifact_sha256({"repository_id": repository_id, "tree": tree}),
                "target_snapshot_sha256": "sha256:" + "a" * 64, "changes": [], "content_blobs": [],
            }), encoding="utf-8")
            spec = ChangeInputSpec(None, None, False, manifest)
            first = advance_feature_flow(project, analytics, root / "run", receipt, spec, blob_resolver={})
            self.assertEqual("PRODUCE_CHANGE_SCOPE", first.kind, first.diagnostics)
            self.assertEqual("CHANGE_SET", first.artifact["run_mode"])
            first.record_path.write_bytes(canonical_bytes(_plain(first.artifact["prompt"])))
            replay = advance_feature_flow(project, analytics, root / "run", receipt, spec, first.record_path, blob_resolver={})
            self.assertEqual("RUN_SCOPE_FALSE_INCLUSION_AUDIT", replay.kind, replay.diagnostics)

    def test_initial_patch_manifest_requires_the_materialized_checkout_to_match(self) -> None:
        from tools.init_skillsrc import ensure_skillsrc

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root)
            if ensure_skillsrc(project, {}, write=True)["status"] != "unchanged":
                _git(project, "add", ".skillsrc"); _git(project, "commit", "-m", "normalize skillsrc")
            spec, resolver, after = _modified_patch(project, root)
            (project / "src" / "app.py").write_bytes(after)
            action = advance_feature_flow(project, analytics, root / "run", change_input=spec, blob_resolver=resolver)
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind, action.diagnostics)
            self.assertEqual("FULL", action.artifact["run_mode"])
            flow = json.loads((root / "run" / "feature-flow" / "prefix" / "000000-flow-input.json").read_bytes())
            self.assertEqual("PROVISIONAL", flow["source_revision"]["durability"])
            self.assertFalse(flow["source_revision"]["baseline_eligible"])
            self.assertIsNotNone(flow["change_input_sha256"])
            (project / "unrelated.py").write_text("foreign = True\n", encoding="utf-8")
            blocked = advance_feature_flow(project, analytics, root / "foreign", change_input=spec, blob_resolver=resolver)
            self.assertEqual("BLOCKED", blocked.kind)
            self.assertEqual("CHANGE_SOURCE_DRIFT", blocked.diagnostics[0]["code"])

    def test_patch_manifest_requires_the_materialized_checkout_to_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); receipt = _advanced_baseline(project, root, include_app_source=True)
            spec, resolver, after = _modified_patch(project, root)
            (project / "src" / "app.py").write_bytes(after)
            action = advance_feature_flow(project, analytics, root / "run", receipt, spec, blob_resolver=resolver)
            self.assertEqual("PRODUCE_CHANGE_SCOPE", action.kind, action.diagnostics)
            self.assertEqual("CHANGE_SET", action.artifact["run_mode"])
            (project / "unrelated.py").write_text("foreign = True\n", encoding="utf-8")
            blocked = advance_feature_flow(project, analytics, root / "foreign", receipt, spec, blob_resolver=resolver)
            self.assertEqual("BLOCKED", blocked.kind)
            self.assertEqual("CHANGE_SOURCE_DRIFT", blocked.diagnostics[0]["code"])

    def test_patch_manifest_rejects_a_committed_descendant_of_its_baseline_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); receipt = _advanced_baseline(project, root)
            spec, resolver, _ = _modified_patch(project, root)
            (project / "foreign.py").write_text("foreign = True\n", encoding="utf-8")
            _git(project, "add", "foreign.py"); _git(project, "commit", "-m", "foreign descendant")
            blocked = advance_feature_flow(project, analytics, root / "run", receipt, spec, blob_resolver=resolver)
            self.assertEqual("BLOCKED", blocked.kind)
            self.assertEqual("CHANGE_SOURCE_DRIFT", blocked.diagnostics[0]["code"])

    def test_feature_flow_selects_exactly_one_module_or_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp), modules=2)
            action = advance_feature_flow(project, analytics, Path(temp) / "run")
            self.assertEqual("BLOCKED", action.kind)
            self.assertEqual("FEATURE_FLOW_INPUT", action.diagnostics[0]["code"])

    def test_resume_ignores_temporary_files_and_returns_exact_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); run = root / "run"
            first = advance_feature_flow(project, analytics, run)
            (run / "feature-flow" / "prefix" / ".stale.tmp").write_text("ignored", encoding="utf-8")
            second = advance_feature_flow(project, analytics, run)
            self.assertEqual(first.kind, second.kind)
            self.assertEqual(first.record_path, second.record_path)
            first.record_path.write_bytes(canonical_bytes(first.artifact["prompt"]))
            resumed = advance_feature_flow(project, analytics, run)
            self.assertEqual("RUN_SCOPE_FALSE_INCLUSION_AUDIT", resumed.kind)

    def test_record_rejects_wrong_label_and_stale_prior_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); run = root / "run"
            first = advance_feature_flow(project, analytics, run)
            wrong = first.record_path.with_name("000001-run-scope-false-inclusion-audit.json")
            wrong.parent.mkdir(parents=True, exist_ok=True)
            wrong.write_bytes(canonical_bytes(_plain(first.artifact["prompt"])))
            self.assertEqual("BLOCKED", advance_feature_flow(project, analytics, run, recorded_artifact=wrong).kind)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); run = root / "run"
            first = advance_feature_flow(project, analytics, run)
            first.record_path.write_bytes(canonical_bytes(_plain(first.artifact["prompt"])))
            second = advance_feature_flow(project, analytics, run, recorded_artifact=first.record_path)
            audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(_plain(first.artifact["prompt"])), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []}
            second.record_path.write_bytes(canonical_bytes(audit))
            stale = advance_feature_flow(project, analytics, run, recorded_artifact=first.record_path)
            self.assertEqual("BLOCKED", stale.kind)
            self.assertEqual("FEATURE_FLOW_INPUT", stale.diagnostics[0]["code"])

    def test_full_provenance_freezes_revision_and_durability(self) -> None:
        from tools.init_skillsrc import ensure_skillsrc

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root)
            ensure_skillsrc(project, {}, write=True); _git(project, "add", ".skillsrc"); _git(project, "commit", "-m", "current skillsrc")
            run = root / "run"; self.assertEqual("PRODUCE_CHANGE_SCOPE", advance_feature_flow(project, analytics, run).kind)
            manifest = json.loads((run / "feature-flow" / "prefix" / "000000-flow-input.json").read_text(encoding="utf-8"))
            self.assertEqual("DURABLE", manifest["source_revision"]["durability"])
            self.assertTrue(manifest["source_revision"]["baseline_eligible"])
            self.assertEqual(manifest["skillsrc_sha256"], manifest["source_revision"]["skillsrc_sha256"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root)
            (project / "src" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
            self.assertEqual("BLOCKED", advance_feature_flow(project, analytics, root / "blocked").kind)
            run = root / "worktree"; self.assertEqual("PRODUCE_CHANGE_SCOPE", advance_feature_flow(project, analytics, run, change_input=ChangeInputSpec("HEAD", None, True, None)).kind)
            manifest = json.loads((run / "feature-flow" / "prefix" / "000000-flow-input.json").read_text(encoding="utf-8"))
            self.assertEqual("PROVISIONAL", manifest["source_revision"]["durability"])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); run = root / "range"
            self.assertEqual("PRODUCE_CHANGE_SCOPE", advance_feature_flow(project, analytics, run, change_input=ChangeInputSpec("HEAD", "HEAD", False, None)).kind)
            manifest = json.loads((run / "feature-flow" / "prefix" / "000000-flow-input.json").read_text(encoding="utf-8"))
            self.assertEqual(_git_value(project, "rev-parse", "HEAD"), manifest["source_revision"]["commit"])
            self.assertEqual(_git_value(project, "rev-parse", "HEAD^{tree}"), manifest["source_revision"]["tree"])

    def test_genuine_advanced_baseline_exact_range_enters_change_set(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root)
            (project / ".gitattributes").write_text("*.py text eol=crlf\n", encoding="utf-8")
            _git(project, "add", ".gitattributes"); _git(project, "commit", "-m", "declare checkout eol")
            from tools.init_skillsrc import ensure_skillsrc
            ensure_skillsrc(project, {}, write=True)
            _git(project, "add", ".skillsrc"); _git(project, "commit", "-m", "normalize skillsrc")
            receipt = _advanced_baseline(project, root)
            base = _git_value(project, "rev-parse", "HEAD")
            (project / "src" / "new.py").write_text("VALUE = 2\n", encoding="utf-8")
            _git(project, "add", "src/new.py"); _git(project, "commit", "-m", "add changed source")
            head = _git_value(project, "rev-parse", "HEAD")
            _git(project, "checkout", "--", "src/new.py")
            raw = subprocess.run(["git", "show", f"{head}:src/new.py"], cwd=project, check=True, capture_output=True).stdout
            self.assertNotEqual(raw, (project / "src" / "new.py").read_bytes())
            self.assertEqual("", _git_value(project, "status", "--porcelain=v1", "--untracked-files=all"))
            snapshot = build_source_inventories(project, load_skillsrc(project / ".skillsrc"), "root", (), snapshot_reader=lambda path: subprocess.run(["git", "show", f"{head}:{path}"], cwd=project, check=True, capture_output=True).stdout)
            source = next(row for row in snapshot.authorized_behavior_sources["sources"] if row.get("path") == "src/new.py")
            self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), source["content_digest"])
            exact = advance_feature_flow(project, analytics, root / "change", receipt, ChangeInputSpec(base, head, False, None))
            self.assertEqual("PRODUCE_CHANGE_SCOPE", exact.kind, exact.diagnostics)
            self.assertEqual("CHANGE_SET", exact.artifact["run_mode"])
            candidate = _plain(exact.artifact["prompt"]); exact.record_path.write_bytes(canonical_bytes(candidate))
            false = advance_feature_flow(project, analytics, root / "change", receipt, ChangeInputSpec(base, head, False, None), exact.record_path)
            audit = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(candidate), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []}
            false.record_path.write_bytes(canonical_bytes(audit))
            omission = advance_feature_flow(project, analytics, root / "change", receipt, ChangeInputSpec(base, head, False, None), false.record_path)
            audit["audit_kind"] = "omission"; omission.record_path.write_bytes(canonical_bytes(audit))
            batch = advance_feature_flow(project, analytics, root / "change", receipt, ChangeInputSpec(base, head, False, None), omission.record_path)
            self.assertEqual("PRODUCE_BATCH_CANDIDATE", batch.kind, batch.diagnostics)
            foreign = advance_feature_flow(project, analytics, root / "foreign", receipt, ChangeInputSpec(head, head, False, None))
            self.assertEqual("RUN_FULL_BASELINE", foreign.kind)
            (project / "src" / "new.py").unlink()
            dirty = advance_feature_flow(project, analytics, root / "dirty", receipt, ChangeInputSpec(base, head, False, None))
            self.assertEqual("BLOCKED", dirty.kind)
            self.assertEqual("FEATURE_FLOW_INPUT", dirty.diagnostics[0]["code"])

    def test_safe_artifacts_and_diagnostics_do_not_echo_seeded_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            action = advance_feature_flow(project, analytics, Path(temp) / "run", change_input=ChangeInputSpec(None, None, False, Path(temp) / "credential-secret.json"))
            rendered = json.dumps({"diagnostics": [dict(row) for row in action.diagnostics]})
            self.assertEqual("BLOCKED", action.kind)
            self.assertNotIn("credential-secret", rendered)

    def test_cli_emits_blocked_diagnostics_only_to_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project, analytics = _project(Path(temp))
            result = subprocess.run(
                [sys.executable, "-m", "tools.test_classification", "feature-flow", "--project", str(project), "--analytics", str(analytics), "--run-root", str(Path(temp) / "run"), "--patch-manifest", str(Path(temp) / "secret.patch")],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertEqual("", result.stdout)
            payload = json.loads(result.stderr)
            self.assertEqual("error", payload["status"])
            self.assertNotIn("secret.patch", result.stderr)

    def test_handoff_remains_outside_tail(self) -> None:
        from dataclasses import fields
        from tools.feature_flow import FeatureFlowAction
        self.assertEqual(["kind", "artifact", "record_path", "diagnostics"], [field.name for field in fields(FeatureFlowAction)])

    def test_full_replay_reaches_closed_prefix_handoff(self) -> None:
        """A caller can write each exact requested record and reach only the tail handoff."""
        from tests.fixture_factory import canonical_document
        from tools.behavior_context_planning import build_context_plan

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); project, analytics = _project(root); run = root / "run"
            action = advance_feature_flow(project, analytics, run)
            inventories = build_source_inventories(project, load_skillsrc(project / ".skillsrc"), "root", ())
            plan = _plain(build_context_plan(project, {"id": "root", "root": ".", "paths": {"source": ["src"]}}, _plain(inventories.authorized_behavior_sources)))
            latest_batch: dict[str, object] | None = None
            for _ in range(32):
                if action.kind == "COMPLETE":
                    ledger = dict(action.artifact or {})
                    self.assertEqual("READY_FOR_PIPELINE_TAIL", ledger["status"])
                    self.assertIn("records", ledger)
                    self.assertIn("flow_input", ledger["artifacts"])
                    self.assertIn("delta_handoff", ledger["artifacts"])
                    self.assertNotIn("terminal_run_receipt", json.dumps(_plain(ledger)))
                    self.assertEqual("COMPLETE", advance_feature_flow(project, analytics, run).kind)
                    break
                self.assertNotEqual("BLOCKED", action.kind, action.diagnostics)
                prompt = dict(_plain((action.artifact or {})["prompt"]))
                if action.kind == "RUN_SCOPE_FALSE_INCLUSION_AUDIT":
                    prompt = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(latest_batch or {}), "audit_kind": "false_inclusion", "verdict": "ACCEPT", "findings": []}
                elif action.kind == "RUN_SCOPE_OMISSION_AUDIT":
                    prompt = {"schema_version": "1.0.0", "artifact": "change-scope-audit", "candidate_sha256": artifact_sha256(latest_batch or {}), "audit_kind": "omission", "verdict": "ACCEPT", "findings": []}
                elif action.kind == "PRODUCE_BATCH_CANDIDATE":
                    batch = next(row for row in plan["batches"] if row["batch_id"] == prompt["batch_id"])
                    items = [{"item_id": item["item_id"], "outcome": "no_supported_observable_fact"} for item in batch["items"]]
                    prompt = {"schema_version": "1.0.0", "artifact": "semantic-batch-candidate", "run_mode": prompt["run_mode"], "scope_receipt_sha256": prompt["scope_receipt_sha256"], "plan_sha256": prompt["plan_sha256"], "batch_id": prompt["batch_id"], "generation": prompt["generation"], "parent_candidate_sha256": prompt["parent_candidate_sha256"], "triggering_audit_sha256s": prompt["triggering_audit_sha256s"], "result_schema_version": prompt["result_schema_version"], "result": {"schema_version": prompt["result_schema_version"], "plan_sha256": prompt["plan_sha256"], "batch_id": prompt["batch_id"], "items": items}}
                elif action.kind in {"RUN_BATCH_FALSE_CLAIM_AUDIT", "RUN_BATCH_OMISSION_AUDIT"}:
                    prompt = {"schema_version": "1.0.0", "artifact": "semantic-batch-audit", "audit_kind": "false_claim" if action.kind.endswith("FALSE_CLAIM_AUDIT") else "omission", "candidate_sha256": artifact_sha256(latest_batch or {}), "batch_id": (latest_batch or {})["batch_id"], "generation": (latest_batch or {})["generation"], "verdict": "ACCEPT", "findings": []}
                elif action.kind == "GENERATE_CHANGED_BEHAVIOR":
                    prompt = {"schema_version": "4.0.0", "stage": "tc-generator", "run_mode": "FULL", "artifacts": {"canonical_document": canonical_document()}, "warnings": []}
                if action.kind in {"PRODUCE_CHANGE_SCOPE", "PRODUCE_BATCH_CANDIDATE"}:
                    latest_batch = prompt
                if action.record_path is not None:
                    action.record_path.write_bytes(canonical_bytes(prompt))
                action = advance_feature_flow(project, analytics, run, recorded_artifact=action.record_path)
            else:
                self.fail("semantic-prefix replay did not reach COMPLETE")



if __name__ == "__main__":
    unittest.main()
