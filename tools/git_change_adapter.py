"""Closed Git and controller patch acquisition without durable source bytes."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import weakref
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
    __slots__ = ("_public", "__weakref__")
    __hash__ = object.__hash__

    def __init__(self, public: Mapping[str, Any]):
        self._public = _freeze(public)

    def __getitem__(self, key: str) -> Any:
        return self._public[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._public)

    def __len__(self) -> int:
        return len(self._public)


_WORKTREE_SNAPSHOTS: weakref.WeakKeyDictionary[_FrozenChangeInput, Mapping[str, bytes | None]] = weakref.WeakKeyDictionary()


def _error(code: str, path: str, message: str) -> FlowError:
    return FlowError(code, path, message)


def _git(project: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
    except OSError:
        raise _error("CHANGE_INPUT", "/project", "Git could not be executed for the project.")
    if completed.returncode:
        raise _error("CHANGE_INPUT", "/git", "Git could not resolve the requested closed input.")
    return completed.stdout


def _one_line(project: Path, *args: str) -> str:
    try:
        return _git(project, *args).decode("ascii").strip()
    except UnicodeDecodeError:
        raise _error("CHANGE_INPUT", "/git", "Git returned an invalid object identity.")


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _repository_id(project: Path) -> str:
    common = _one_line(project, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return _digest(str(Path(common).resolve()).encode("utf-8"))


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
    except UnicodeDecodeError:
        raise _error("CHANGE_INPUT", "/changes", "Git path is not UTF-8.")
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
        except (IndexError, UnicodeDecodeError):
            raise _error("CHANGE_INPUT", "/git", "Git raw change metadata is malformed.")
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
    try:
        completed = subprocess.run(["git", "cat-file", "-e", f"{treeish}:{path}"], cwd=project, capture_output=True, check=False)
    except OSError:
        raise _error("CHANGE_INPUT", "/git", "Git object bytes could not be read.")
    if completed.returncode:
        return None
    return _git(project, "cat-file", "-p", f"{treeish}:{path}")


def _worktree_bytes(project: Path, path: str, *, required: bool = False) -> bytes | None:
    """Read one frozen worktree file without exposing filesystem exceptions."""
    candidate = project / Path(*path.split("/"))
    try:
        if not candidate.is_file():
            if required:
                raise _error("CHANGE_INPUT", "/changes", "A worktree rename destination could not be frozen.")
            return None
        return candidate.read_bytes()
    except OSError:
        raise _error("CHANGE_INPUT", "/changes", "Worktree source bytes could not be frozen.")


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
    handled: set[str] = set()
    for path, (old_path, similarity) in sorted(rename_hints.items()):
        before, after = _tree_bytes(project, base_commit, old_path), _worktree_bytes(project, path, required=True)
        if before is not None and _tree_bytes(project, base_commit, path) is None:
            frozen[old_path], frozen[path] = None, after
            rows.append(_row("renamed", path, (old_path, before), (path, after), old_path=old_path, similarity=similarity))
            handled.update((old_path, path))
    for path in sorted(names):
        if path in handled:
            continue
        before = _tree_bytes(project, base_commit, path)
        after = _worktree_bytes(project, path)
        frozen[path] = after
        if before is None and after is not None:
            rows.append(_row("added", path, None, (path, after)))
        elif before is not None and after is None:
            rows.append(_row("deleted", path, (path, before), None))
        elif before is not None and after is not None and before != after:
            rows.append(_row("modified", path, (path, before), (path, after)))
    repository_id = _repository_id(project)
    public = {"schema_version": "1.0.0", "artifact": "change-input", "input_kind": "git_worktree", "repository_id": repository_id,
              "base": {"commit": base_commit, "tree": base_tree, "snapshot_sha256": _snapshot_identity(repository_id, base_tree)},
              "target": {"snapshot_sha256": artifact_sha256({"repository_id": repository_id, "changes": _order(rows)})}, "changes": _order(rows)}
    public["change_input_sha256"] = artifact_sha256(public)
    result = _FrozenChangeInput(public)
    _WORKTREE_SNAPSHOTS[result] = MappingProxyType(dict(frozen))
    return result


_TOP = {"schema_version", "artifact", "repository_id", "base_snapshot_sha256", "target_snapshot_sha256", "changes", "content_blobs"}
_ROW_KEYS = {
    "added": {"change_id", "kind", "path", "after"}, "modified": {"change_id", "kind", "path", "before", "after"},
    "deleted": {"change_id", "kind", "path", "before"}, "renamed": {"change_id", "kind", "old_path", "new_path", "similarity_basis", "before", "after"},
    "binary": {"change_id", "kind", "path", "binary_change", "before", "after"},
}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE = re.compile(r"^SOURCE-[0-9a-f]{64}$")
_BLOB = re.compile(r"^BLOB-[A-Za-z0-9_.:-]+$")


def _digest_value(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST.fullmatch(value) is not None


def _row_paths(row: Mapping[str, Any]) -> tuple[str | None, str | None]:
    kind = row["kind"]
    if kind == "renamed":
        return row["old_path"], row["new_path"]
    path = row["path"]
    return (None if kind == "added" else path, None if kind == "deleted" else path)


def _resolve(resolver: BlobResolver | None, blob_id: str) -> bytes | None:
    if resolver is None:
        return None
    value = resolver.get(blob_id) if isinstance(resolver, Mapping) else resolver(blob_id)
    return value if isinstance(value, bytes) else None


def _patch(project: Path, path: Path, resolver: BlobResolver | None) -> Mapping[str, Any]:
    try:
        manifest = load_json_strict(path)
    except (OSError, StrictJsonError):
        raise _error("CHANGE_INPUT", "/patch_manifest", "Patch manifest could not be read as strict JSON.")
    if not isinstance(manifest, Mapping) or set(manifest) != _TOP or manifest.get("schema_version") != "1.0.0" or manifest.get("artifact") != "patch-manifest":
        raise _error("CHANGE_SCOPE_SHAPE", "/patch_manifest", "Patch manifest must use the closed V1 shape.")
    changes, blobs = manifest.get("changes"), manifest.get("content_blobs")
    if not _digest_value(manifest.get("repository_id")) or not _digest_value(manifest.get("base_snapshot_sha256")) or not _digest_value(manifest.get("target_snapshot_sha256")):
        raise _error("CHANGE_SCOPE_SHAPE", "/patch_manifest", "Patch manifest identities must be SHA-256 digests.")
    if not isinstance(changes, list) or not isinstance(blobs, list):
        raise _error("CHANGE_SCOPE_SHAPE", "/patch_manifest", "Patch manifest collections must be arrays.")
    blob_by_digest: dict[str, Mapping[str, Any]] = {}
    for blob in blobs:
        if not isinstance(blob, Mapping) or set(blob) != {"content_sha256", "size_bytes", "controller_blob_id"} or not _digest_value(blob.get("content_sha256")) or type(blob.get("size_bytes")) is not int or blob["size_bytes"] < 0 or not isinstance(blob.get("controller_blob_id"), str) or _BLOB.fullmatch(blob["controller_blob_id"]) is None:
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
        for path_key in ("path", "old_path", "new_path"):
            if path_key in row:
                if not isinstance(row[path_key], str):
                    raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}/{path_key}", "Patch paths must be strings.")
                try:
                    _path(row[path_key].encode("utf-8"))
                except FlowError:
                    raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}/{path_key}", "Patch paths must be safe relative POSIX paths.")
        row_copy = dict(row); change_id = row_copy.pop("change_id", None)
        if not isinstance(change_id, str) or change_id != "CHANGE-" + hashlib.sha256(canonical_bytes(row_copy)).hexdigest() or change_id in identifiers:
            raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}/change_id", "Patch change identifier does not bind its closed row.")
        identifiers.add(change_id)
        before, after = row.get("before"), row.get("after")
        if row["kind"] == "added" and (before is not None or not isinstance(after, Mapping)) or row["kind"] == "deleted" and (after is not None or not isinstance(before, Mapping)) or row["kind"] in ("modified", "renamed") and (not isinstance(before, Mapping) or not isinstance(after, Mapping)):
            raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Patch row side cardinality is invalid.")
        if row["kind"] == "renamed" and (row["old_path"] == row["new_path"] or not isinstance(row["similarity_basis"], str) or not row["similarity_basis"]):
            raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch rename relation is invalid.")
        before_path, after_path = _row_paths(row)
        for side, side_path in ((before, before_path), (after, after_path)):
            if side is None:
                continue
            if not isinstance(side, Mapping) or set(side) != {"source_id", "content_sha256", "size_bytes", "text"} or not isinstance(side.get("source_id"), str) or _SOURCE.fullmatch(side["source_id"]) is None or not _digest_value(side.get("content_sha256")) or type(side.get("size_bytes")) is not int or side["size_bytes"] < 0 or type(side.get("text")) is not bool:
                raise _error("CHANGE_SCOPE_SHAPE", f"/changes/{index}", "Patch side must use the closed shape.")
            digest = side.get("content_sha256")
            blob = blob_by_digest.get(digest)
            if blob is None:
                raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch side has no controller blob binding.")
            content = _resolve(resolver, str(blob.get("controller_blob_id")))
            if content is None or _digest(content) != digest or len(content) != side.get("size_bytes") or len(content) != blob.get("size_bytes"):
                raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch blob does not match its closed digest and size binding.")
            if side_path is None or side["source_id"] != _side(side_path, content)["source_id"] or side["text"] != _is_text(content):
                raise _error("CHANGE_SCOPE_BINDING", f"/changes/{index}", "Patch side identity or text binding is inconsistent.")
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
    snapshot = _WORKTREE_SNAPSHOTS.get(change_input)
    if snapshot is None:
        return
    for path, expected in snapshot.items():
        candidate = project / Path(*path.split("/"))
        actual = candidate.read_bytes() if candidate.is_file() else None
        if actual != expected:
            raise _error("CHANGE_SOURCE_DRIFT", "/target", "Frozen worktree source bytes have changed.")
