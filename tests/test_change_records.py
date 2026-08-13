from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.flow_artifacts import FlowError, canonical_bytes, write_create_only  # noqa: E402
from tools.git_change_adapter import (  # noqa: E402
    ChangeInputSpec,
    acquire_change_input,
    verify_change_input,
)


SECRET = "pipeline6-seeded-secret-6b20a7a"


def _git(project: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, check=False,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _init_project(root: Path) -> tuple[Path, str]:
    project = root / "project"
    project.mkdir(parents=True)
    _git(project, "init")
    _git(project, "config", "user.email", "pipeline6@example.invalid")
    _git(project, "config", "user.name", "Pipeline Six")
    (project / "modified.txt").write_text("before\n", encoding="utf-8")
    (project / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (project / "renamed.txt").write_text("rename me\n", encoding="utf-8")
    (project / "binary.bin").write_bytes(b"\x00before\xff")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "base")
    return project, _git(project, "rev-parse", "HEAD").decode("ascii").strip()


class ChangeRecordTests(unittest.TestCase):
    def test_patch_rejects_non_string_paths_and_empty_rename_basis(self) -> None:
        """Coercing closed path fields or allowing empty rename evidence accepts malformed manifests."""
        before, after = b"before\n", b"after\n"

        def side(path: object, content: bytes) -> dict[str, object]:
            digest = _digest(content)
            return {"source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": path, "content_sha256": digest})).hexdigest(), "content_sha256": digest, "size_bytes": len(content), "text": True}

        def manifest(row: dict[str, object]) -> dict[str, object]:
            row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
            return {"schema_version": "1.0.0", "artifact": "patch-manifest", "repository_id": _digest(b"repo"), "base_snapshot_sha256": _digest(b"base"), "target_snapshot_sha256": _digest(b"target"), "changes": [row], "content_blobs": [{"content_sha256": _digest(before), "size_bytes": len(before), "controller_blob_id": "BLOB-before"}, {"content_sha256": _digest(after), "size_bytes": len(after), "controller_blob_id": "BLOB-after"}]}

        candidates = [
            manifest({"kind": "modified", "path": 7, "before": side(7, before), "after": side(7, after)}),
            manifest({"kind": "renamed", "old_path": "before.py", "new_path": "after.py", "similarity_basis": "", "before": side("before.py", before), "after": side("after.py", after)}),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "patch.json"
            for candidate in candidates:
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(FlowError):
                    acquire_change_input(Path(temporary), ChangeInputSpec(None, None, False, path), {"BLOB-before": before, "BLOB-after": after}.get)

    def test_worktree_rename_read_failure_is_a_safe_flow_error(self) -> None:
        """A vanished rename destination must not leak a raw filesystem error during acquisition."""
        with tempfile.TemporaryDirectory() as temporary:
            project, base = _init_project(Path(temporary))
            _git(project, "mv", "renamed.txt", "worktree-name.txt")
            with mock.patch.object(Path, "read_bytes", side_effect=FileNotFoundError(SECRET)):
                with self.assertRaises(FlowError) as caught:
                    acquire_change_input(project, ChangeInputSpec(base, None, True, None))
            self.assertEqual("CHANGE_INPUT", caught.exception.code)
            self.assertIsNone(caught.exception.__cause__)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_worktree_snapshot_hides_immutable_private_bytes(self) -> None:
        """Exposing a mutable bytes map would let callers bypass later drift detection."""
        with tempfile.TemporaryDirectory() as temporary:
            project, base = _init_project(Path(temporary))
            (project / "untracked.txt").write_text("frozen\n", encoding="utf-8")
            frozen = acquire_change_input(project, ChangeInputSpec(base, None, True, None))
            self.assertFalse(hasattr(frozen, "files"))
            self.assertFalse(any(isinstance(value, bytes) for value in frozen.values()))
            (project / "untracked.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(FlowError, "CHANGE_SOURCE_DRIFT"):
                verify_change_input(project, frozen)

    def test_repository_identity_binds_distinct_git_directories(self) -> None:
        """Hashing the relative `.git` label would falsely bind unrelated repositories together."""
        with tempfile.TemporaryDirectory() as temporary:
            first, first_base = _init_project(Path(temporary) / "first")
            second, second_base = _init_project(Path(temporary) / "second")
            (first / "added.txt").write_text("one\n", encoding="utf-8")
            (second / "added.txt").write_text("two\n", encoding="utf-8")
            for project in (first, second):
                _git(project, "add", ".")
                _git(project, "commit", "-m", "head")
            first_record = acquire_change_input(first, ChangeInputSpec(first_base, _git(first, "rev-parse", "HEAD").decode().strip(), False, None))
            second_record = acquire_change_input(second, ChangeInputSpec(second_base, _git(second, "rev-parse", "HEAD").decode().strip(), False, None))
            self.assertNotEqual(first_record["repository_id"], second_record["repository_id"])

    def test_atomic_write_failure_never_publishes_partial_target(self) -> None:
        """Writing the final path before publication could leave an authoritative partial JSON file."""
        with tempfile.TemporaryDirectory() as temporary, mock.patch("tools.flow_artifacts.os.link", side_effect=OSError("blocked")):
            target = Path(temporary) / "artifact.json"
            with self.assertRaises(FlowError) as caught:
                write_create_only(Path(temporary), PurePosixPath("artifact.json"), {"artifact": "safe"})
            self.assertEqual("FLOW_ATOMIC_WRITE", caught.exception.code)
            self.assertFalse(target.exists())

    def test_binary_schema_rejects_invalid_side_cardinality(self) -> None:
        """A binary-added row with a before side would violate the closed union."""
        from tools.schema_validation import schema_diagnostics  # noqa: PLC0415

        side = {"source_id": "SOURCE-" + "a" * 64, "content_sha256": _digest(b"x"), "size_bytes": 1, "text": False}
        row = {"change_id": "CHANGE-" + "b" * 64, "kind": "binary", "path": "asset.bin", "binary_change": "added", "before": side, "after": side}
        self.assertTrue(schema_diagnostics(row, ROOT / "schemas" / "change-record.schema.json", ROOT))

    def test_patch_recomputes_closed_side_identity_and_path_text_bindings(self) -> None:
        """Trusting manifest source IDs or text flags would accept foreign or malformed evidence."""
        before, after = b"before\n", b"after\n"
        row = {"kind": "modified", "path": "../unsafe.py", "before": {"source_id": "SOURCE-" + "a" * 64, "content_sha256": _digest(before), "size_bytes": len(before), "text": True}, "after": {"source_id": "SOURCE-" + "b" * 64, "content_sha256": _digest(after), "size_bytes": len(after), "text": True}}
        row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
        manifest = {"schema_version": "1.0.0", "artifact": "patch-manifest", "repository_id": _digest(b"repo"), "base_snapshot_sha256": _digest(b"base"), "target_snapshot_sha256": _digest(b"target"), "changes": [row], "content_blobs": [{"content_sha256": _digest(before), "size_bytes": len(before), "controller_blob_id": "BLOB-before"}, {"content_sha256": _digest(after), "size_bytes": len(after), "controller_blob_id": "BLOB-after"}]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "patch.json"; path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(FlowError):
                acquire_change_input(Path(temporary), ChangeInputSpec(None, None, False, path), {"BLOB-before": before, "BLOB-after": after}.get)

    def test_worktree_rename_is_preserved_as_rename(self) -> None:
        """Classifying renamed worktree bytes as add/delete loses the exact before-after relation."""
        with tempfile.TemporaryDirectory() as temporary:
            project, base = _init_project(Path(temporary))
            _git(project, "mv", "renamed.txt", "worktree-name.txt")
            record = acquire_change_input(project, ChangeInputSpec(base, None, True, None))
            self.assertTrue(any(row["kind"] == "renamed" and row["old_path"] == "renamed.txt" for row in record["changes"]))

    def test_safe_errors_do_not_chain_operating_system_detail(self) -> None:
        """An exception cause could disclose raw filesystem or Git details to callers."""
        with tempfile.TemporaryDirectory() as temporary, mock.patch("tools.flow_artifacts.os.link", side_effect=OSError(SECRET)):
            with self.assertRaises(FlowError) as caught:
                write_create_only(Path(temporary), PurePosixPath("safe.json"), {"artifact": "safe"})
            self.assertIsNone(caught.exception.__cause__)
            self.assertNotIn(SECRET, str(caught.exception))

    def test_change_record_variants_and_canonical_order(self) -> None:
        """Wrong side cardinalities or unordered rows would make closed change IDs unstable."""
        with tempfile.TemporaryDirectory() as temporary:
            project, base = _init_project(Path(temporary))
            (project / "added.txt").write_text("added\n", encoding="utf-8")
            (project / "modified.txt").write_text("after\n", encoding="utf-8")
            (project / "deleted.txt").unlink()
            _git(project, "mv", "renamed.txt", "new-name.txt")
            (project / "binary.bin").write_bytes(b"\x00after\xfe")
            _git(project, "add", "-A")
            _git(project, "commit", "-m", "changes")
            head = _git(project, "rev-parse", "HEAD").decode("ascii").strip()

            record = acquire_change_input(project, ChangeInputSpec(base=base, head=head, worktree=False, patch_manifest=None))

        changes = record["changes"]
        self.assertEqual(["added", "binary", "deleted", "modified", "renamed"], [row["kind"] for row in changes])
        for row in changes:
            expected = "CHANGE-" + hashlib.sha256(canonical_bytes({key: value for key, value in row.items() if key != "change_id"})).hexdigest()
            self.assertEqual(expected, row["change_id"])
        by_kind = {row["kind"]: row for row in changes}
        self.assertEqual({"change_id", "kind", "path", "after"}, set(by_kind["added"]))
        self.assertEqual({"change_id", "kind", "path", "before"}, set(by_kind["deleted"]))
        self.assertEqual({"change_id", "kind", "path", "before", "after"}, set(by_kind["modified"]))
        self.assertEqual({"change_id", "kind", "old_path", "new_path", "similarity_basis", "before", "after"}, set(by_kind["renamed"]))
        self.assertEqual(False, by_kind["binary"]["before"]["text"])
        self.assertEqual(False, by_kind["binary"]["after"]["text"])

    def test_patch_manifest_closed_before_after_union(self) -> None:
        """Permissive manifests could bind a source digest to unreviewed controller bytes."""
        before = b"before\n"
        after = b"after\n"
        base = _digest(b"base snapshot")
        target = _digest(b"target snapshot")
        repository = _digest(b"repository")
        row = {
            "kind": "modified", "path": "src/example.py",
            "before": {"source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": "src/example.py", "content_sha256": _digest(before)})).hexdigest(), "content_sha256": _digest(before), "size_bytes": len(before), "text": True},
            "after": {"source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": "src/example.py", "content_sha256": _digest(after)})).hexdigest(), "content_sha256": _digest(after), "size_bytes": len(after), "text": True},
        }
        row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
        manifest = {
            "schema_version": "1.0.0", "artifact": "patch-manifest", "repository_id": repository,
            "base_snapshot_sha256": base, "target_snapshot_sha256": target, "changes": [row],
            "content_blobs": [
                {"content_sha256": _digest(before), "size_bytes": len(before), "controller_blob_id": "BLOB-before"},
                {"content_sha256": _digest(after), "size_bytes": len(after), "controller_blob_id": "BLOB-after"},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "patch.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = acquire_change_input(Path(temporary), ChangeInputSpec(None, None, False, path), {"BLOB-before": before, "BLOB-after": after}.get)
            self.assertEqual("patch_manifest", result["input_kind"])
            for mutate in (
                lambda item: item.__setitem__("unexpected", True),
                lambda item: item["content_blobs"].append(dict(item["content_blobs"][0])),
                lambda item: item["content_blobs"].pop(),
                lambda item: item["content_blobs"][0].__setitem__("size_bytes", 99),
                lambda item: item["changes"][0].__setitem__("old_path", "wrong.py"),
            ):
                candidate = json.loads(json.dumps(manifest))
                mutate(candidate)
                path.write_text(json.dumps(candidate), encoding="utf-8")
                with self.assertRaises(FlowError):
                    acquire_change_input(Path(temporary), ChangeInputSpec(None, None, False, path), {"BLOB-before": before, "BLOB-after": after}.get)

    def test_worktree_snapshot_freezes_staged_unstaged_and_untracked(self) -> None:
        """Reading a dirty worktree later must not silently replace the frozen byte snapshot."""
        with tempfile.TemporaryDirectory() as temporary:
            project, base = _init_project(Path(temporary))
            (project / "modified.txt").write_text("staged\n", encoding="utf-8")
            _git(project, "add", "modified.txt")
            (project / "modified.txt").write_text("unstaged\n", encoding="utf-8")
            (project / "untracked.txt").write_text("untracked\n", encoding="utf-8")

            frozen = acquire_change_input(project, ChangeInputSpec(base=base, head=None, worktree=True, patch_manifest=None))
            self.assertEqual("git_worktree", frozen["input_kind"])
            self.assertEqual({"modified.txt", "untracked.txt"}, {row.get("path") for row in frozen["changes"]})
            (project / "untracked.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(FlowError, "CHANGE_SOURCE_DRIFT"):
                verify_change_input(project, frozen)

    def test_safe_artifacts_and_diagnostics_do_not_echo_seeded_secret(self) -> None:
        """Persisting source or diagnostic echo would disclose controller-only change evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(FlowError) as caught:
                write_create_only(root, PurePosixPath("artifacts/safe.json"), {"summary": SECRET})
            self.assertNotIn(SECRET, str(caught.exception))
            stored = write_create_only(root, PurePosixPath("artifacts/safe.json"), {"artifact": "safe", "digest": _digest(b"x")})
            self.assertNotIn(SECRET, stored.path.read_text(encoding="utf-8"))
            bound = write_create_only(root, PurePosixPath("artifacts/bound.json"), {"source_id": "SOURCE-" + "a" * 64, "content_sha256": _digest(b"x")})
            self.assertEqual({"content_sha256": _digest(b"x"), "source_id": "SOURCE-" + "a" * 64}, json.loads(bound.payload))
            same = write_create_only(root, PurePosixPath("artifacts/safe.json"), {"digest": _digest(b"x"), "artifact": "safe"})
            self.assertEqual(stored.payload, same.payload)
            with self.assertRaises(FlowError):
                write_create_only(root, PurePosixPath("artifacts/safe.json"), {"artifact": "other"})
            self.assertNotIn(SECRET, stored.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
