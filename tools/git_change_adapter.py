"""Closed Git and controller patch acquisition without durable source bytes."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Iterator, Mapping

from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes
from tools.schema_validation import StrictJsonError, load_json_strict


BlobResolver = Callable[[str], bytes | None] | Mapping[str, bytes]


@dataclass(frozen=True)
class ChangeInputSpec:
    base: str | None
    head: str | None
    worktree: bool
    patch_manifest: Path | None


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class _FrozenChangeInput(Mapping[str, Any]):
    def __init__(self, public: Mapping[str, Any], files: Mapping[str, bytes | None]):
        self.public = _freeze(public)
        self.files = dict(files)

    def __getitem__(self, key: str) -> Any:
        return self.public[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.public)

    def __len__(self) -> int:
        return len(self.public)


def _error(code: str, path: str, message: str) -> FlowError:
    return FlowError(code, path, message)


def _git(project: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
    except OSError as error:
        raise _error("CHANGE_INPUT", "/project", "Git could not be executed for the project.") from error
    if completed.returncode:
        raise _error("CHANGE_INPUT", "/git", "Git could not resolve the requested closed input.")
    return completed.stdout


def _one_line(project: Path, *args: str) -> str:
    try:
        return _git(project, *args).decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise _error("CHANGE_INPUT", "/git", "Git returned an invalid object identity.") from error


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _repository_id(project: Path) -> str:
    return _digest(_one_line(project, "rev-parse", "--git-common-dir").encode("utf-8"))


def _side(path: str, value: bytes) -> dict[str, Any]:
    digest = _digest(value)
    return {
        "source_id": "SOURCE-" + hashlib.sha256(canonical_bytes({"path": path, "content_sha256": digest})).hexdigest(),
        "content_sha256": digest,
        "size_bytes": len(value),
        "text": _is_text(value),
    }


def _is_text(value: bytes) -> bool:
    try:
        value.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return b"\x00" not in value


def _path(raw: bytes) -> str:
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise _error("CHANGE_INPUT", "/changes", "Git path is not UTF-8.") from error
    if not decoded or decoded.startswith("/") or "\\" in decoded or any(part in ("", ".", "..") for part in decoded.split("/")):
        raise _error("CHANGE_SCOPE_SHAPE", "/changes", "Change paths must be safe relative POSIX paths.")
    return decoded


def _raw_changes(project: Path, base: str, head: str) -> list[tuple[str, str | None, str, int | None]]:
    raw = _git(project, "diff", "--raw", "-z", "-M", "--no-abbrev", base, head)
    tokens = raw.split(b"\0")
    index = 0
    records: list[tuple[str, str | None, str, int | None]] = []
    while index < len(tokens) - 1:
        header = tokens[index]
        index += 1
        try:
            status = header.rsplit(b" ", 1)[1].decode("ascii")
        except (IndexError, UnicodeDecodeError) as error:
            raise _error("CHANGE_INPUT", "/git", "Git raw change metadata is malformed.") from error
        code = status[:1]
        similarity = int(status[1:]) if code in ("R", "C") and status[1:].isdigit() else None
        if code in ("R", "C"):
            if index + 1 >= len(tokens):
                raise _error("CHANGE_INPUT", "/git", "Git rename metadata is incomplete.")
            old_path, new_path = _path(tokens[index]), _path(tokens[index + 1])
            index += 2
            records.append((code, old_path, new_path, similarity))
        else:
            if index >= len(tokens):
                raise _error("CHANGE_INPUT", "/git", "Git path metadata is incomplete.")
            records.append((code, None, _path(tokens[index]), None))
            index += 1
    return records


def _tree_bytes(project: Path, treeish: str, path: str) -> bytes | None:
    completed = subprocess.run(["git", "cat-file", "-e", f"{treeish}:{path}"], cwd=project, capture_output=True, check=False)
    if completed.returncode:
        return None
    return _git(project, "cat-file", "-p", f"{treeish}:{path}")


def _row(kind: str, path: str | None, before: tuple[str, bytes] | None, after: tuple[str, bytes] | None, *, old_path: str | None = None, similarity: int | None = None) -> dict[str, Any]:
    text = all(side is None or _is_text(side[1]) for side in (before, after))
    if not text:
        binary_change = "added" if before is None else "deleted" if after is None else "modified"
        row: dict[str, Any] = {"kind": "binary", "path": path or old_path, "binary_change": binary_change,
                               "before": None if before is None else {**_side(before[0], before[1]), "text": False},
                               "after": None if after is None else {**_side(after[0], after[1]), "text": False}}
    elif kind == "added":
        row = {"kind": "added", "path": path, "after": _side(after[0], after[1])}
    elif kind == "deleted":
        row = {"kind": "deleted", "path": path, "before": _side(before[0], before[1])}
    elif kind == "renamed":
        row = {"kind": "renamed", "old_path": old_path, "new_path": path, "similarity_basis": f"git-raw-rename-{similarity or 0}",
               "before": _side(before[0], before[1]), "after": _side(after[0], after[1])}
    else:
        row = {"kind": "modified", "path": path, "before": _side(before[0], before[1]), "after": _side(after[0], after[1])}
    row["change_id"] = "CHANGE-" + hashlib.sha256(canonical_bytes(row)).hexdigest()
    return row


def _order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (str(row.get("path") or row.get("old_path")), row["kind"], str(row.get("new_path", ""))))


def _snapshot_identity(repository_id: str, tree: str) -> str:
    return artifact_sha256({"repository_id": repository_id, "tree": tree})


def _range(project: Path, base: str, head: str) -> Mapping[str, Any]:
    base_commit, head_commit = _one_line(project, "rev-parse", f"{base}^{{commit}}"), _one_line(project, "rev-parse", f"{head}^{{commit}}")
    base_tree, head_tree = _one_line(project, "rev-parse", f"{base_commit}^{{tree}}"), _one_line(project, "rev-parse", f"{head_commit}^{{tree}}")
    rows: list[dict[str, Any]] = []
    for code, old_path, path, similarity in _raw_changes(project, base_commit, head_commit):
        before_path = old_path or path
        before, after = _tree_bytes(project, base_commit, before_path), _tree_bytes(project, head_commit, path)
        if code in ("R", "C") and before is not None and after is not None:
            rows.append(_row("renamed", path, (before_path, before), (path, after), old_path=old_path, similarity=similarity))
        elif before is None and after is not None:
            rows.append(_row("added", path, None, (path, after)))
        elif before is not None and after is None:
            rows.append(_row("deleted", path, (path, before), None))
        elif before is not None and after is not None:
            rows.append(_row("modified", path, (path, before), (path, after)))
        else:
            raise _error("CHANGE_SCOPE_BINDING", "/changes", "A Git change could not bind to an object side.")
    repository_id = _repository_id(project)
    public = {"schema_version": "1.0.0", "artifact": "change-input", "input_kind": "git_range", "repository_id": repository_id,
              "base": {"commit": base_commit, "tree": base_tree, "snapshot_sha256": _snapshot_identity(repository_id, base_tree)},
              "target": {"commit": head_commit, "tree": head_tree, "snapshot_sha256": _snapshot_identity(repository_id, head_tree)}, "changes": _order(rows)}
    public["change_input_sha256"] = artifact_sha256(public)
    return _freeze(public)


def _worktree(project: Path, base: str) -> Mapping[str, Any]:
    base_commit = _one_line(project, "rev-parse", f"{base}^{{commit}}")
    base_tree = _one_line(project, "rev-parse", f"{base_commit}^{{tree}}")
    names: set[str] = set()
    rename_hints: dict[str, tuple[str, int | None]] = {}
    # The staged/unstaged union is deliberately computed independently.
    for command in (("diff", "--raw", "-z", "-M", "--no-abbrev", "--cached"), ("diff", "--raw", "-z", "-M", "--no-abbrev")):
        raw = _git(project, *command)
        tokens = raw.split(b"\0")
        index = 0
        while index < len(tokens) - 1:
            header = tokens[index]; index += 1
            status = header.rsplit(b" ", 1)[1].decode("ascii"); code = status[:1]
            if code in ("R", "C"):
                old_path, new_path = _path(tokens[index]), _path(tokens[index + 1]); index += 2
                names.update((old_path, new_path)); rename_hints[new_path] = (old_path, int(status[1:]) if status[1:].isdigit() else None)
            else:
                names.add(_path(tokens[index])); index += 1
    for raw in _git(project, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if raw:
            names.add(_path(raw))
    rows: list[dict[str, Any]] = []
    frozen: dict[str, bytes | None] = {}
    for path in sorted(names):
        before = _tree_bytes(project, base_commit, path)
        disk = project / Path(*path.split("/"))
        after = disk.read_bytes() if disk.is_file() else None
        frozen[path] = after
        if before is None and after is not None:
            rows.append(_row("added", path, None, (path, after)))
        elif before is not None and after is None:
            rows.append(_row("deleted", path, (path, before), None))
        elif before is not None and after is not None and before != after:
            hint = rename_hints.get(path)
            if hint and _tree_bytes(project, base_commit, hint[0]) is not None:
                old, similarity = hint
                frozen.setdefault(old, None)
                rows.append(_row("renamed", path, (old, _tree_bytes(project, base_commit, old)), (path, after), old_path=old, similarity=similarity))
            else:
                rows.append(_row("modified", path, (path, before), (path, after)))
    repository_id = _repository_id(project)
    public = {"schema_version": "1.0.0", "artifact": "change-input", "input_kind": "git_worktree", "repository_id": repository_id,
              "base": {"commit": base_commit, "tree": base_tree, "snapshot_sha256": _snapshot_identity(repository_id, base_tree)},
              "target": {"snapshot_sha256": artifact_sha256({"repository_id": repository_id, "changes": _order(rows)})}, "changes": _order(rows)}
    public["change_input_sha256"] = artifact_sha256(public)
    return _FrozenChangeInput(public, frozen)


_TOP = {"schema_version", "artifact", "repository_id", "base_snapshot_sha256", "target_snapshot_sha256", "changes", "content_blobs"}
_ROW_KEYS = {
    "added": {"change_id", "kind", "path", "after"}, "modified": {"change_id", "kind", "path", "before", "after"},
    "deleted": {"change_id", "kind", "path", "before"}, "renamed": {"change_id", "kind", "old_path", "new_path", "similarity_basis", "before", "after"},
    "binary": {"change_id", "kind", "path", "binary_change", "before", "after"},
}


def _resolve(resolver: BlobResolver | None, blob_id: str) -> bytes | None:
    if resolver is None:
        return None
    value = resolver.get(blob_id) if isinstance(resolver, Mapping) else resolver(blob_id)
    return value if isinstance(value, bytes) else None


def _patch(project: Path, path: Path, resolver: BlobResolver | None) -> Mapping[str, Any]:
    try:
        manifest = load_json_strict(path)
    except (OSError, StrictJsonError) as error:
        raise _error("CHANGE_INPUT", "/patch_manifest", "Patch manifest could not be read as strict JSON.") from error
    if not isinstance(manifest, Mapping) or set(manifest) != _TOP or manifest.get("schema_version") != "1.0.0" or manifest.get("artifact") != "patch-manifest":
        raise _error("CHANGE_SCOPE_SHAPE", "/patch_manifest", "Patch manifest must use the closed V1 shape.")
    changes, blobs = manifest.get("changes"), manifest.get("content_blobs")
    if not isinstance(changes, list) or not isinstance(blobs, list):
        raise _error("CHANGE_SCOPE_SHAPE", "/patch_manifest", "Patch manifest collections must be arrays.")
    blob_by_digest: dict[str, Mapping[str, Any]] = {}
    for blob in blobs:
        if not isinstance(blob, Mapping) or set(blob) != {"content_sha256", "size_bytes", "controller_blob_id"}:
            raise _error("CHANGE_SCOPE_SHAPE", "/content_blobs", "Patch blobs must use the closed shape.")
        digest = blob.get("content_sha256")
        if not isinstance(digest, str) or digest in blob_by_digest:
            raise _error("CHANGE_SCOPE_BINDING", "/content_blobs", "Patch blobs must bind each digest exactly once.")
        blob_by_digest[digest] = blob
    used: set[str] = set()
    identifiers: set[str] = set()
    for index, row in enumerate(changes):
        if not isinstance(row, Mapping) or row.get("kind") not in _ROW_KEYS or set(row) != _ROW_KEYS[row["kind"]]:
            raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Patch change row must use one closed variant.")
        row_copy = dict(row); change_id = row_copy.pop("change_id", None)
        if not isinstance(change_id, str) or change_id != "CHANGE-" + hashlib.sha256(canonical_bytes(row_copy)).hexdigest() or change_id in identifiers:
            raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}/change_id", "Patch change identifier does not bind its closed row.")
        identifiers.add(change_id)
        before, after = row.get("before"), row.get("after")
        if row["kind"] == "added" and (before is not None or not isinstance(after, Mapping)) or row["kind"] == "deleted" and (after is not None or not isinstance(before, Mapping)) or row["kind"] in ("modified", "renamed") and (not isinstance(before, Mapping) or not isinstance(after, Mapping)):
            raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Patch row side cardinality is invalid.")
        if row["kind"] == "renamed" and (row["old_path"] == row["new_path"] or not isinstance(row["similarity_basis"], str)):
            raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch rename relation is invalid.")
        for side in (before, after):
            if side is None:
                continue
            if not isinstance(side, Mapping) or set(side) != {"source_id", "content_sha256", "size_bytes", "text"}:
                raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Patch side must use the closed shape.")
            digest = side.get("content_sha256")
            blob = blob_by_digest.get(digest)
            if blob is None:
                raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch side has no controller blob binding.")
            content = _resolve(resolver, str(blob.get("controller_blob_id")))
            if content is None or _digest(content) != digest or len(content) != side.get("size_bytes") or len(content) != blob.get("size_bytes"):
                raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch blob does not match its closed digest and size binding.")
            used.add(digest)
        if row["kind"] == "binary":
            expected = "added" if before is None else "deleted" if after is None else "modified"
            if row.get("binary_change") != expected or any(side is not None and side.get("text") is not False for side in (before, after)):
                raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Binary row cardinality or text flags are invalid.")
        elif any(side is not None and side.get("text") is not True for side in (before, after)):
            raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Text row must have text sides.")
    if list(changes) != _order([dict(row) for row in changes]):
        raise _error("CHANGE_SCOPE_ORDER", "/changes", "Patch changes must use canonical order.")
    if set(blob_by_digest) != used:
        raise _error("CHANGE_SCOPE_BINDING", "/content_blobs", "Patch manifest contains foreign or unused controller blobs.")
    public = {"schema_version": "1.0.0", "artifact": "change-input", "input_kind": "patch_manifest", "repository_id": manifest["repository_id"],
              "base": {"snapshot_sha256": manifest["base_snapshot_sha256"]}, "target": {"snapshot_sha256": manifest["target_snapshot_sha256"]}, "changes": [dict(row) for row in changes]}
    public["change_input_sha256"] = artifact_sha256(public)
    return _freeze(public)


def acquire_change_input(project: Path, spec: ChangeInputSpec, blob_resolver: BlobResolver | None = None) -> Mapping[str, Any]:
    """Acquire exactly one closed Git range, frozen worktree, or patch manifest."""
    modes = sum((spec.patch_manifest is not None, spec.worktree, spec.base is not None or spec.head is not None))
    if spec.patch_manifest is not None:
        if spec.worktree or spec.base is not None or spec.head is not None:
            raise _error("CHANGE_INPUT", "/change_input", "Patch manifest mode cannot be combined with Git mode.")
        return _patch(project, spec.patch_manifest, blob_resolver)
    if spec.worktree:
        if spec.base is None or spec.head is not None:
            raise _error("CHANGE_INPUT", "/change_input", "Worktree mode requires only an exact base.")
        return _worktree(project, spec.base)
    if spec.base is None or spec.head is None:
        raise _error("CHANGE_INPUT", "/change_input", "Git range mode requires exact base and head commits.")
    return _range(project, spec.base, spec.head)


def verify_change_input(project: Path, change_input: Mapping[str, Any]) -> None:
    """Reject later dirty-worktree bytes that differ from the frozen controller snapshot."""
    if not isinstance(change_input, _FrozenChangeInput):
        return
    for path, expected in change_input.files.items():
        candidate = project / Path(*path.split("/"))
        actual = candidate.read_bytes() if candidate.is_file() else None
        if actual != expected:
            raise _error("CHANGE_SOURCE_DRIFT", "/target", "Frozen worktree source bytes have changed.")
