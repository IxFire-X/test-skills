"""Pure, immutable reviewed feature-change scope state machine."""

from __future__ import annotations

import hashlib
import re
import weakref
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from tools.baseline_lifecycle import ScopePredecessor, scope_predecessor_projection
from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes
from tools.git_change_adapter import verify_change_input
from tools.schema_validation import schema_diagnostics

_ROOT = Path(__file__).resolve().parents[1]


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_OBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SOURCE_ID = re.compile(r"SOURCE-[0-9a-f]{64}\Z")
_FILE_ID = re.compile(r"FILE-[0-9a-f]{64}\Z")
_SYMBOL_ID = re.compile(r"SYMBOL-[0-9a-f]{64}\Z")
_REASONS = ("DIRECT_TEXT_CHANGE", "DIRECT_BINARY_CHANGE", "ADDED_SOURCE", "DELETED_SOURCE", "RENAMED_SOURCE", "SYMBOL_RELATION", "IMPORT_RELATION", "ROUTE_BINDING_RELATION", "CONFIG_RUNTIME_RELATION", "REQUIREMENT_SOURCE_RELATION", "TEST_EVIDENCE_RELATION", "DOMAIN_WIDENING", "MODULE_WIDENING", "FULL_REFRESH")
_RELATIONS = _REASONS[5:11]
_FALSE_CODES = {"IMPACT_SOURCE_UNRELATED", "IMPACT_LEXICAL_COINCIDENCE", "IMPACT_TEST_ONLY_ORIGIN", "IMPACT_WIDENING_UNNECESSARY"}
_OMISSION_CODES = {"IMPACT_SOURCE_OMITTED", "IMPACT_TRANSITIVE_OMITTED", "IMPACT_DELETION_EFFECT_OMITTED", "IMPACT_RENAME_EFFECT_OMITTED", "IMPACT_PUBLIC_CONTRACT_OMITTED", "IMPACT_RUNTIME_CONFIG_OMITTED", "IMPACT_ROUTE_BINDING_OMITTED", "IMPACT_CLOSURE_INCOMPLETE"}
_CANDIDATE_KEYS = ("schema_version", "artifact", "run_mode", "selected_module", "baseline_receipt_sha256", "analytics_sha256", "change_input_sha256", "current_source_inventory_sha256", "current_test_inventory_sha256", "generation", "parent_candidate_sha256", "triggering_audit_sha256s", "changes", "included_sources", "included_test_symbols", "relations", "baseline_requirement_ids", "widening_level")


def _error(code: str, path: str, message: str) -> FlowError:
    return FlowError(code, path, message)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _safe_public(value: Any, path: str = "") -> None:
    """Reject controller/private values before they can enter frozen public state."""
    if isinstance(value, (Path, PurePosixPath, bytes, bytearray, memoryview)):
        raise _error("CHANGE_SCOPE_SHAPE", path or "/", "Public scope artifacts cannot contain filesystem or raw-byte values.")
    if isinstance(value, Mapping):
        for key, item in value.items():
            _safe_public(key, path); _safe_public(item, f"{path}/{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value): _safe_public(item, f"{path}/{index}")


def _closed(value: Any, keys: set[str] | tuple[str, ...], path: str, code: str = "CHANGE_SCOPE_SHAPE") -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(keys):
        raise _error(code, path, "A closed object with the required fields is required.")
    return value


def _digest(value: Any, path: str, code: str = "CHANGE_SCOPE_BINDING", nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise _error(code, path, "A SHA-256 digest is required.")
    return value


def _safe_path(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise _error("CHANGE_SCOPE_SHAPE", path, "A safe relative POSIX path is required.")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or candidate.as_posix() != value or any(part in {"", ".", ".."} for part in candidate.parts):
        raise _error("CHANGE_SCOPE_SHAPE", path, "A safe relative POSIX path is required.")
    return value


def _source_inventory(value: Any, module: str, path: str) -> tuple[Mapping[str, Any], ...]:
    item = _closed(value, {"module_id", "sources"}, path)
    if item.get("module_id") != module or not isinstance(item.get("sources"), (list, tuple)):
        raise _error("CHANGE_SCOPE_SHAPE", path, "Source inventory module or sources are invalid.")
    rows: list[Mapping[str, Any]] = []
    ids: set[str] = set()
    for index, source in enumerate(item["sources"]):
        row = _closed(source, {"source_id", "kind", "path", "content_digest"} if isinstance(source, Mapping) and source.get("kind") == "product_file" else {"source_id", "kind", "content_digest"}, f"{path}/sources/{index}")
        if not isinstance(row.get("source_id"), str) or not row["source_id"] or row["source_id"] in ids or row.get("kind") not in {"product_file", "supplied_requirement"} or (row["kind"] == "product_file" and _SOURCE_ID.fullmatch(row["source_id"]) is None):
            raise _error("CHANGE_SCOPE_BINDING", f"{path}/sources/{index}", "Source identity is invalid.")
        if row["kind"] == "product_file":
            source_path = _safe_path(row.get("path"), f"{path}/sources/{index}/path")
            if row["source_id"] != "SOURCE-" + hashlib.sha256(b"product_file\0" + source_path.encode("utf-8")).hexdigest(): raise _error("CHANGE_SCOPE_BINDING", f"{path}/sources/{index}/source_id", "Product source identity does not bind the stable inventory path.")
        _digest(row.get("content_digest"), f"{path}/sources/{index}/content_digest")
        ids.add(row["source_id"]); rows.append(row)
    return tuple(rows)


def _test_inventory(value: Any, module: str) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    row = _closed(value, {"module_id", "test_roots", "files", "symbols"}, "/current_test_inventory")
    if row.get("module_id") != module or not isinstance(row.get("test_roots"), (list, tuple)) or not isinstance(row.get("files"), (list, tuple)) or not isinstance(row.get("symbols"), (list, tuple)):
        raise _error("CHANGE_SCOPE_SHAPE", "/current_test_inventory", "Technical inventory is invalid.")
    envelope = {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {"technical_test_inventory": _plain(row), "technical_test_inventory_sha256": artifact_sha256(_plain(row)), "authorized_behavior_sources": {"module_id": module, "sources": []}, "authorized_behavior_sources_sha256": artifact_sha256({"module_id": module, "sources": []})}, "warnings": []}
    if schema_diagnostics(envelope, _ROOT / "schemas/source-inventory-output.schema.json", _ROOT): raise _error("CHANGE_SCOPE_SHAPE", "/current_test_inventory", "Technical inventory violates the closed registry contract.")
    files: set[str] = set()
    for index, file in enumerate(row["files"]):
        item = _closed(file, {"file_id", "path", "language", "framework", "content_digest"}, f"/current_test_inventory/files/{index}")
        if not isinstance(item.get("file_id"), str) or _FILE_ID.fullmatch(item["file_id"]) is None or item["file_id"] in files: raise _error("CHANGE_SCOPE_BINDING", f"/current_test_inventory/files/{index}", "Test file identity is invalid.")
        _safe_path(item.get("path"), f"/current_test_inventory/files/{index}/path"); _digest(item.get("content_digest"), f"/current_test_inventory/files/{index}/content_digest"); files.add(item["file_id"])
    pairs: set[tuple[str, str]] = set()
    symbols: list[Mapping[str, Any]] = []
    for index, symbol in enumerate(row["symbols"]):
        item = _closed(symbol, {"file_id", "symbol_id", "locator"}, f"/current_test_inventory/symbols/{index}")
        pair = (item.get("file_id"), item.get("symbol_id"))
        if not all(isinstance(value, str) and value for value in pair) or _SYMBOL_ID.fullmatch(pair[1]) is None or pair[0] not in files or pair in pairs: raise _error("CHANGE_SCOPE_BINDING", f"/current_test_inventory/symbols/{index}", "Technical symbol identity is invalid.")
        pairs.add(pair); symbols.append(item)
    return row, tuple(symbols)


def _change_input(project: Path, value: Any) -> tuple[Mapping[str, Any], tuple[Mapping[str, Any], ...]]:
    if not isinstance(value, Mapping): raise _error("CHANGE_SCOPE_SHAPE", "/change_input", "A closed change input is required.")
    verify_change_input(project, value)
    kinds = {"git_range", "git_worktree", "patch_manifest"}
    expected = {"schema_version", "artifact", "input_kind", "repository_id", "base", "target", "changes", "change_input_sha256"}
    row = _closed(value, expected, "/change_input")
    if row.get("schema_version") != "1.0.0" or row.get("artifact") != "change-input" or row.get("input_kind") not in kinds or not isinstance(row.get("changes"), (list, tuple)):
        raise _error("CHANGE_SCOPE_SHAPE", "/change_input", "Change input is outside the public union.")
    base_keys = {"commit", "tree", "snapshot_sha256"} if row["input_kind"] in {"git_range", "git_worktree"} else {"snapshot_sha256"}
    target_keys = {"commit", "tree", "snapshot_sha256"} if row["input_kind"] == "git_range" else {"snapshot_sha256"}
    base = _closed(row.get("base"), base_keys, "/change_input/base"); target = _closed(row.get("target"), target_keys, "/change_input/target")
    _digest(row.get("repository_id"), "/change_input/repository_id")
    for key, location in ((base, "/change_input/base"), (target, "/change_input/target")):
        _digest(key.get("snapshot_sha256"), f"{location}/snapshot_sha256")
    if row["input_kind"] in {"git_range", "git_worktree"}:
        for key, location in ((base, "/change_input/base"),):
            if not isinstance(key.get("commit"), str) or _GIT_OBJECT.fullmatch(key["commit"]) is None or not isinstance(key.get("tree"), str) or _GIT_OBJECT.fullmatch(key["tree"]) is None:
                raise _error("CHANGE_SCOPE_SHAPE", location, "Git base commit and tree must be lowercase object identifiers.")
    if row["input_kind"] == "git_range":
        if not isinstance(target.get("commit"), str) or _GIT_OBJECT.fullmatch(target["commit"]) is None or not isinstance(target.get("tree"), str) or _GIT_OBJECT.fullmatch(target["tree"]) is None:
            raise _error("CHANGE_SCOPE_SHAPE", "/change_input/target", "Git target commit and tree must be lowercase object identifiers.")
    copy = _plain(row); reported = copy.pop("change_input_sha256")
    if reported != artifact_sha256(copy): raise _error("CHANGE_SCOPE_BINDING", "/change_input/change_input_sha256", "Change input digest does not bind its public artifact.")
    rows: list[Mapping[str, Any]] = []; identifiers: set[str] = set(); semantic_rows: set[bytes] = set()
    for index, change in enumerate(row["changes"]):
        if not isinstance(change, Mapping) or schema_diagnostics(_plain(change), _ROOT / "schemas/change-record.schema.json", _ROOT): raise _error("CHANGE_SCOPE_SHAPE", f"/change_input/changes/{index}", "Change record is not a closed public V1 variant.")
        for key in ("path", "old_path", "new_path"):
            if key in change: _safe_path(change[key], f"/change_input/changes/{index}/{key}")
        for key in ("before", "after"):
            if key in change and change[key] is not None:
                side = _closed(change[key], {"source_id", "content_sha256", "size_bytes", "text"}, f"/change_input/changes/{index}/{key}")
                _digest(side.get("content_sha256"), f"/change_input/changes/{index}/{key}/content_sha256")
                side_path = change.get("old_path" if key == "before" and change.get("kind") == "renamed" else "new_path" if key == "after" and change.get("kind") == "renamed" else "path")
                expected_source = "SOURCE-" + hashlib.sha256(canonical_bytes({"path": side_path, "content_sha256": side["content_sha256"]})).hexdigest()
                if side.get("source_id") != expected_source:
                    raise _error("CHANGE_SCOPE_BINDING", f"/change_input/changes/{index}/{key}/source_id", "Task-1 side identity does not bind its path and digest.")
        identity = _plain(change); change_id = identity.pop("change_id", None)
        if change_id != "CHANGE-" + hashlib.sha256(canonical_bytes(identity)).hexdigest(): raise _error("CHANGE_SCOPE_BINDING", f"/change_input/changes/{index}/change_id", "Change identifier is not deterministic.")
        if change_id in identifiers or canonical_bytes(identity) in semantic_rows:
            raise _error("CHANGE_SCOPE_BINDING", f"/change_input/changes/{index}", "Change rows must be unique public semantics.")
        identifiers.add(change_id); semantic_rows.add(canonical_bytes(identity))
        rows.append(change)
    if list(rows) != sorted(rows, key=lambda item: (str(item.get("path") or item.get("old_path")), item["kind"], str(item.get("new_path", "")))):
        raise _error("CHANGE_SCOPE_ORDER", "/change_input/changes", "Change records must use canonical Task-1 order.")
    if row["input_kind"] == "git_worktree" and row["target"]["snapshot_sha256"] != artifact_sha256({"repository_id": row["repository_id"], "changes": _plain(rows)}):
        raise _error("CHANGE_SCOPE_BINDING", "/change_input/target/snapshot_sha256", "Worktree target snapshot does not bind canonical frozen changes.")
    return row, tuple(rows)


def _relation_id(row: Mapping[str, Any]) -> str:
    return "RELATION-" + hashlib.sha256(canonical_bytes(row)).hexdigest()


def _domain(source: Mapping[str, Any], module: Mapping[str, Any]) -> str:
    from tools.behavior_context_planning import derive_domain_key
    return derive_domain_key(source, module)


def _validate_relations(rows: tuple[Mapping[str, Any], ...], sources: Mapping[str, tuple[Mapping[str, Any], ...]], files: set[str]) -> tuple[tuple[Mapping[str, Any], ...], bool]:
    accepted: list[Mapping[str, Any]] = []; unresolved = False
    for index, value in enumerate(rows):
        try:
            row = _closed(value, {"relation_kind", "from_source_id", "to_source_id", "evidence_locator"}, f"/relations/{index}")
            if row.get("relation_kind") not in _RELATIONS or not isinstance(row.get("from_source_id"), str) or not isinstance(row.get("to_source_id"), str): raise _error("CHANGE_SCOPE_AUDIT", f"/relations/{index}", "Relation identity is invalid.")
            locator = _closed(row.get("evidence_locator"), {"content_sha256", "start_byte", "end_byte"}, f"/relations/{index}/evidence_locator")
            if type(locator.get("start_byte")) is not int or type(locator.get("end_byte")) is not int or locator["start_byte"] < 0 or locator["end_byte"] <= locator["start_byte"]: raise _error("CHANGE_SCOPE_AUDIT", f"/relations/{index}/evidence_locator", "Relation range is invalid.")
            owners = sources.get(row["from_source_id"], ())
            if not owners or not any(owner.get("content_digest") == locator.get("content_sha256") for owner in owners) or (row["relation_kind"] == "TEST_EVIDENCE_RELATION" and row["to_source_id"] not in files) or (row["relation_kind"] != "TEST_EVIDENCE_RELATION" and row["to_source_id"] not in sources): raise _error("CHANGE_SCOPE_AUDIT", f"/relations/{index}", "Relation endpoint or locator is not authoritative.")
            accepted.append(row)
        except FlowError:
            raise
    if list(accepted) != list(rows) or list(accepted) != sorted(accepted, key=lambda row: (row["relation_kind"], row["from_source_id"], row["to_source_id"], canonical_bytes(row["evidence_locator"]))):
        raise _error("CHANGE_SCOPE_ORDER", "/relations", "Relations must use canonical order.")
    return tuple(sorted(accepted, key=lambda row: (row["relation_kind"], row["from_source_id"], row["to_source_id"], canonical_bytes(row["evidence_locator"])))), unresolved


@dataclass(frozen=True)
class ScopeInputs:
    project_root: Path
    run_mode: str
    selected_module: Mapping[str, Any]
    analytics_sha256: str
    change_input: Mapping[str, Any] | None
    current_source_inventory: Mapping[str, Any]
    current_test_inventory: Mapping[str, Any]
    predecessor: ScopePredecessor | None
    relations: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True, eq=False)
class ScopeSnapshot:
    inputs: Mapping[str, Any]
    candidate: Mapping[str, Any] | None = None
    false_audit: Mapping[str, Any] | None = None
    omission_audit: Mapping[str, Any] | None = None
    generation: int = 1
    widening_index: int = 0
    blocked: bool = False


@dataclass(frozen=True)
class ScopeAction:
    kind: str
    artifact: Mapping[str, Any] | None
    diagnostics: tuple[Mapping[str, Any], ...] = ()


class ReviewController:
    """Trusted hosts may subclass this internal marker; ordinary inputs cannot forge it."""


@dataclass(frozen=True)
class _ScopeGuard:
    project_root: Path
    change_input: Mapping[str, Any]


_GUARDS: weakref.WeakKeyDictionary[ScopeSnapshot, _ScopeGuard] = weakref.WeakKeyDictionary()


def _guard(snapshot: ScopeSnapshot) -> None:
    guard = _GUARDS.get(snapshot)
    if guard is None:
        raise _error("CHANGE_SCOPE_BINDING", "/snapshot", "Scope snapshot has no trusted transition guard.")
    verify_change_input(guard.project_root, guard.change_input)


def _next(snapshot: ScopeSnapshot, *args: Any, **kwargs: Any) -> ScopeSnapshot:
    result = ScopeSnapshot(*args, **kwargs)
    guard = _GUARDS.get(snapshot)
    if guard is None:
        raise _error("CHANGE_SCOPE_BINDING", "/snapshot", "Scope snapshot has no trusted transition guard.")
    _GUARDS[result] = guard
    return result


def start_scope(inputs: ScopeInputs) -> ScopeSnapshot:
    if not isinstance(inputs, ScopeInputs) or not isinstance(inputs.project_root, Path): raise _error("CHANGE_SCOPE_SHAPE", "/inputs", "Scope inputs are invalid.")
    module = inputs.selected_module.get("id") if isinstance(inputs.selected_module, Mapping) else None
    if not isinstance(module, str) or not module or inputs.run_mode not in {"FULL", "CHANGE_SET"}: raise _error("CHANGE_SCOPE_SHAPE", "/selected_module", "Selected module or mode is invalid.")
    if inputs.run_mode == "CHANGE_SET": verify_change_input(inputs.project_root, inputs.change_input)
    _digest(inputs.analytics_sha256, "/analytics_sha256")
    for name, value in (("selected_module", inputs.selected_module), ("current_source_inventory", inputs.current_source_inventory), ("current_test_inventory", inputs.current_test_inventory), ("relations", inputs.relations), ("change_input", inputs.change_input)):
        _safe_public(value, f"/{name}")
    current = _source_inventory(inputs.current_source_inventory, module, "/current_source_inventory")
    technical, symbols = _test_inventory(inputs.current_test_inventory, module)
    base_sources: tuple[Mapping[str, Any], ...] = () ; requirement_ids: tuple[str, ...] = () ; predecessor = None; change = None; rows: tuple[Mapping[str, Any], ...] = ()
    if inputs.run_mode == "FULL":
        if inputs.change_input is not None or inputs.predecessor is not None: raise _error("BASELINE_BINDING", "/inputs", "FULL cannot carry predecessor or diff authority.")
    else:
        predecessor = scope_predecessor_projection(inputs.predecessor)
        if predecessor["selected_module"] != module: raise _error("BASELINE_BINDING", "/predecessor", "Predecessor module is incompatible.")
        base_sources, requirement_ids = predecessor["sources"], predecessor["requirement_ids"]
        change, rows = _change_input(inputs.project_root, inputs.change_input)
        if predecessor["repository_id"] != change.get("repository_id"):
            raise _error("BASELINE_BINDING", "/change_input/repository_id", "Change input repository does not bind the baseline.")
        expected_base_snapshot = artifact_sha256({"repository_id": predecessor["repository_id"], "tree": predecessor["target_tree"]})
        base = change["base"]; target = change["target"]
        if change["input_kind"] in {"git_range", "git_worktree"}:
            if base["commit"] != predecessor["target_commit"] or base["tree"] != predecessor["target_tree"] or base["snapshot_sha256"] != expected_base_snapshot:
                raise _error("BASELINE_BINDING", "/change_input/base", "Git base does not exactly bind the validated predecessor.")
        elif base["snapshot_sha256"] != expected_base_snapshot:
            raise _error("BASELINE_BINDING", "/change_input/base/snapshot_sha256", "Patch base does not bind the validated predecessor tree.")
        if change["input_kind"] == "git_range" and target["snapshot_sha256"] != artifact_sha256({"repository_id": change["repository_id"], "tree": target["tree"]}):
            raise _error("CHANGE_SCOPE_BINDING", "/change_input/target/snapshot_sha256", "Git target snapshot does not bind its repository and tree.")
        current_join = {(row.get("path"), row.get("content_digest")) for row in current if row.get("kind") == "product_file"}
        baseline_join = {(row.get("path"), row.get("content_digest")) for row in base_sources if row.get("kind") == "product_file"}
        for index, changed in enumerate(rows):
            kind = changed["kind"]
            for side_name, joined in (("before", baseline_join), ("after", current_join)):
                side = changed.get(side_name)
                if side is None: continue
                path = changed.get("old_path" if side_name == "before" and kind == "renamed" else "new_path" if side_name == "after" and kind == "renamed" else "path")
                if (path, side.get("content_sha256")) not in joined:
                    raise _error("CHANGE_SCOPE_COVERAGE", f"/change_input/changes/{index}/{side_name}", "Required change side has no authoritative inventory match.")
    source_registry: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for source in (*base_sources, *current):
        source_registry[source["source_id"]] = (*source_registry.get(source["source_id"], ()), source)
    relations, unresolved = _validate_relations(tuple(inputs.relations), source_registry, {row["file_id"] for row in technical["files"]})
    state = {"module": module, "module_data": _freeze(_plain(inputs.selected_module)), "run_mode": inputs.run_mode, "analytics_sha256": inputs.analytics_sha256, "change": change, "changes": rows, "current": current, "technical": technical, "symbols": symbols, "predecessor": predecessor, "baseline_sources": base_sources, "requirements": requirement_ids, "relations": relations, "unresolved": unresolved}
    result = ScopeSnapshot(_freeze(state), widening_index=1 if unresolved else 0, blocked=False)
    _GUARDS[result] = _ScopeGuard(inputs.project_root, inputs.change_input) if inputs.run_mode == "CHANGE_SET" else _ScopeGuard(inputs.project_root, MappingProxyType({}))
    return result


def _candidate(state: Mapping[str, Any], generation: int, widening_index: int = 0, parent: str | None = None, triggers: tuple[str, ...] = (), remove: tuple[str, ...] = ()) -> Mapping[str, Any]:
    current = state["current"]; base = state["baseline_sources"]; changes = state["changes"]; relations = state["relations"]
    if state["run_mode"] == "FULL":
        included = [{"source_id": row["source_id"], "reason": "FULL_REFRESH", "relation_ids": []} for row in current]; test_pairs: list[dict[str, str]] = []; level = "full"
    else:
        by_current = {(row.get("path"), row["content_digest"]): row for row in current if row.get("kind") == "product_file"}; by_base = {(row.get("path"), row["content_digest"]): row for row in base if row.get("kind") == "product_file"}
        blocked = set(remove); direct: set[str] = set(); frontier: list[str] = []; reasons: dict[str, tuple[str, list[str]]] = {}
        for change in changes:
            kind = change["kind"]
            side_rows = (("before", by_base),) if kind == "deleted" else (("after", by_current),) if kind in {"added", "modified"} else (("before", by_base), ("after", by_current))
            for side_name, registry in side_rows:
                side = change.get(side_name)
                path = change.get("old_path" if side_name == "before" and kind == "renamed" else "new_path" if side_name == "after" and kind == "renamed" else "path")
                if isinstance(side, Mapping) and isinstance(path, str):
                    found = registry.get((path, side.get("content_sha256")))
                    if found is not None:
                        reason = "DIRECT_BINARY_CHANGE" if kind == "binary" else "ADDED_SOURCE" if kind == "added" else "DELETED_SOURCE" if kind == "deleted" else "RENAMED_SOURCE" if kind == "renamed" else "DIRECT_TEXT_CHANGE"
                        if found["source_id"] not in blocked:
                            if found["source_id"] not in direct: frontier.append(found["source_id"])
                            direct.add(found["source_id"]); reasons[found["source_id"]] = (reason, [])
        file_ids: set[str] = set()
        while frontier:
            source_id = frontier.pop(0)
            for relation in relations:
                if relation["relation_kind"] == "TEST_EVIDENCE_RELATION":
                    if relation["from_source_id"] == source_id: file_ids.add(relation["to_source_id"])
                    continue
                if relation["from_source_id"] == source_id: target = relation["to_source_id"]
                elif relation["to_source_id"] == source_id: target = relation["from_source_id"]
                else: continue
                rel_id = _relation_id(relation)
                if target not in blocked and target not in direct:
                    direct.add(target); reasons[target] = (relation["relation_kind"], [rel_id]); frontier.append(target)
        current_by_path = {row.get("path"): row for row in current if row.get("kind") == "product_file" and row["source_id"] in direct}
        ordered: list[Mapping[str, Any]] = []
        occupied_paths: set[str] = set()
        for row in base:
            path = row.get("path")
            replacement = current_by_path.get(path)
            if replacement is not None:
                ordered.append(replacement); occupied_paths.add(path)
            elif row["source_id"] in direct:
                ordered.append(row); occupied_paths.add(str(path))
        ordered.extend(row for row in current if row["source_id"] in direct and row.get("path") not in occupied_paths)
        included = [{"source_id": row["source_id"], "reason": reasons[row["source_id"]][0], "relation_ids": reasons[row["source_id"]][1]} for row in ordered]
        test_pairs = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in state["symbols"] if row["file_id"] in file_ids]
        level = ("symbol", "file", "domain", "module", "full")[widening_index]
        if widening_index == 1:
            test_pairs = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in state["symbols"]]
        elif widening_index == 2:
            domains = {_domain(row, state["module_data"]) for row in ordered}
            for row in current:
                if row["source_id"] not in {item["source_id"] for item in ordered} and _domain(row, state["module_data"]) in domains:
                    included.append({"source_id": row["source_id"], "reason": "DOMAIN_WIDENING", "relation_ids": []})
        elif widening_index == 3:
            current_by_path = {row["path"]: row for row in current if row.get("kind") == "product_file"}
            base_paths = {row["path"] for row in base if row.get("kind") == "product_file"}
            widened: list[Mapping[str, Any]] = []; seen: set[str] = set()
            for row in base:
                chosen = current_by_path.get(row["path"], row) if row.get("kind") == "product_file" else row
                if chosen["source_id"] not in seen: widened.append(chosen); seen.add(chosen["source_id"])
            for row in current:
                if row["source_id"] not in seen and (row.get("kind") != "product_file" or row["path"] not in base_paths): widened.append(row); seen.add(row["source_id"])
            included = [{"source_id": row["source_id"], "reason": "MODULE_WIDENING", "relation_ids": []} for row in widened]
            test_pairs = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in state["symbols"]]
        elif widening_index == 4:
            current_by_path = {row["path"]: row for row in current if row.get("kind") == "product_file"}
            base_paths = {row["path"] for row in base if row.get("kind") == "product_file"}
            widened = []; seen = set()
            for row in base:
                chosen = current_by_path.get(row["path"], row) if row.get("kind") == "product_file" else row
                if chosen["source_id"] not in seen: widened.append(chosen); seen.add(chosen["source_id"])
            for row in current:
                if row["source_id"] not in seen and (row.get("kind") != "product_file" or row["path"] not in base_paths): widened.append(row); seen.add(row["source_id"])
            included = [{"source_id": row["source_id"], "reason": "FULL_REFRESH", "relation_ids": []} for row in widened]
            test_pairs = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"]} for row in state["symbols"]]
        if remove:
            included = [row for row in included if row["source_id"] not in set(remove)]
            relations = tuple(row for row in relations if row["from_source_id"] not in set(remove) and row["to_source_id"] not in set(remove))
    return _freeze({"schema_version": "1.0.0", "artifact": "change-scope-candidate", "run_mode": state["run_mode"], "selected_module": state["module"], "baseline_receipt_sha256": None if state["run_mode"] == "FULL" else state["predecessor"]["receipt_sha256"], "analytics_sha256": state["analytics_sha256"], "change_input_sha256": None if state["run_mode"] == "FULL" else state["change"]["change_input_sha256"], "current_source_inventory_sha256": artifact_sha256({"module_id": state["module"], "sources": _plain(current)}), "current_test_inventory_sha256": artifact_sha256(_plain(state["technical"])), "generation": generation, "parent_candidate_sha256": parent, "triggering_audit_sha256s": list(triggers), "changes": _freeze(changes), "included_sources": included, "included_test_symbols": test_pairs, "relations": relations, "baseline_requirement_ids": state["requirements"], "widening_level": level})


def _validate_candidate(value: Mapping[str, Any]) -> None:
    if schema_diagnostics(_plain(value), _ROOT / "schemas/change-scope-candidate.schema.json", _ROOT):
        raise _error("CHANGE_SCOPE_AUDIT", "/candidate", "Candidate does not satisfy its closed public contract.")
    relation_ids = {_relation_id(row) for row in value["relations"]}
    indirect = set(_RELATIONS)
    for index, source in enumerate(value["included_sources"]):
        reason, bound = source["reason"], source["relation_ids"]
        if reason in indirect and (not bound or any(identifier not in relation_ids for identifier in bound)):
            raise _error("CHANGE_SCOPE_AUDIT", f"/candidate/included_sources/{index}/relation_ids", "Indirect source reason must bind an exact candidate relation.")
        if reason not in indirect and bound:
            raise _error("CHANGE_SCOPE_AUDIT", f"/candidate/included_sources/{index}/relation_ids", "Direct or widening source reason cannot carry relation identifiers.")


def _projection(candidate: Mapping[str, Any]) -> str:
    plain = _plain(candidate)
    for key in ("generation", "parent_candidate_sha256", "triggering_audit_sha256s", "widening_level"): plain.pop(key, None)
    return artifact_sha256(plain)


def _omission_index(snapshot: ScopeSnapshot) -> int | None:
    current = _candidate(snapshot.inputs, snapshot.generation, snapshot.widening_index)
    for index in range(snapshot.widening_index + 1, 5):
        proposed = _candidate(snapshot.inputs, snapshot.generation + 1, index, artifact_sha256(snapshot.candidate), (artifact_sha256(snapshot.omission_audit),))
        if _projection(current) != _projection(proposed): return index
    return None


def _validate_audit(value: Mapping[str, Any], candidate: Mapping[str, Any], kind: str, state: Mapping[str, Any]) -> Mapping[str, Any]:
    row = _closed(value, {"schema_version", "artifact", "candidate_sha256", "audit_kind", "verdict", "findings"}, "/audit")
    if row.get("schema_version") != "1.0.0" or row.get("artifact") != "change-scope-audit" or row.get("audit_kind") != kind or row.get("candidate_sha256") != artifact_sha256(candidate) or row.get("verdict") not in {"ACCEPT", "REWORK"} or not isinstance(row.get("findings"), (list, tuple)):
        raise _error("CHANGE_SCOPE_AUDIT", "/audit", "Scope audit does not bind the expected candidate and kind.")
    if (row["verdict"] == "ACCEPT") != (len(row["findings"]) == 0): raise _error("CHANGE_SCOPE_AUDIT", "/audit/findings", "Verdict and findings are inconsistent.")
    allowed = _FALSE_CODES if kind == "false_inclusion" else _OMISSION_CODES; sources = {item["source_id"]: item for item in (*state["baseline_sources"], *state["current"])}
    finding_ids: set[str] = set(); finding_sources: set[str] = set(); pointers: set[str] = set()
    for index, finding in enumerate(row["findings"]):
        item = _closed(finding, {"finding_id", "code", "candidate_pointer", "source_id", "evidence_locator", "summary"}, f"/audit/findings/{index}")
        if item.get("code") not in allowed or item.get("source_id") not in sources or not isinstance(item.get("candidate_pointer"), str) or not item["candidate_pointer"].startswith("/") or not isinstance(item.get("summary"), str) or not item["summary"] or len(item["summary"]) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in item["summary"]):
            raise _error("CHANGE_SCOPE_AUDIT", f"/audit/findings/{index}", "Finding is not safe or authoritative.")
        locator = _closed(item.get("evidence_locator"), {"content_sha256", "start_byte", "end_byte"}, f"/audit/findings/{index}/evidence_locator")
        if locator.get("content_sha256") != sources[item["source_id"]].get("content_digest") or type(locator.get("start_byte")) is not int or type(locator.get("end_byte")) is not int or locator["start_byte"] < 0 or locator["end_byte"] <= locator["start_byte"]: raise _error("CHANGE_SCOPE_AUDIT", f"/audit/findings/{index}", "Finding locator is invalid.")
        pointer = item["candidate_pointer"]; parts = pointer.split("/")
        omitted = kind == "omission" and item["code"] == "IMPACT_SOURCE_OMITTED"
        if omitted:
            if pointer != "/included_sources" or item["source_id"] in {source["source_id"] for source in candidate["included_sources"]}:
                raise _error("CHANGE_SCOPE_AUDIT", f"/audit/findings/{index}/candidate_pointer", "Omitted-source finding must cite the candidate source container and an absent authorized source.")
        elif len(parts) != 3 or parts[:2] != ["", "included_sources"] or not parts[2].isdigit() or str(int(parts[2])) != parts[2] or int(parts[2]) >= len(candidate["included_sources"]) or candidate["included_sources"][int(parts[2])]["source_id"] != item["source_id"]:
            raise _error("CHANGE_SCOPE_AUDIT", f"/audit/findings/{index}/candidate_pointer", "Finding must point to its exact candidate source row.")
        identity = _plain(item); identity.pop("finding_id")
        if item.get("finding_id") != "FINDING-" + hashlib.sha256(canonical_bytes(identity)).hexdigest() or item["finding_id"] in finding_ids or item["source_id"] in finding_sources or pointer in pointers: raise _error("CHANGE_SCOPE_AUDIT", f"/audit/findings/{index}", "Findings must have unique identities, sources, and pointers.")
        finding_ids.add(item["finding_id"]); finding_sources.add(item["source_id"]); pointers.add(pointer)
    return _freeze(_plain(row))


def _false_corrections(candidate: Mapping[str, Any], audit: Mapping[str, Any]) -> tuple[str, ...] | None:
    direct = {row["source_id"] for row in candidate["included_sources"] if row["reason"] in {"DIRECT_TEXT_CHANGE", "DIRECT_BINARY_CHANGE", "ADDED_SOURCE", "DELETED_SOURCE", "RENAMED_SOURCE"}}
    removed: list[str] = []
    for finding in audit["findings"]:
        source_id = finding["source_id"]
        if finding["code"] == "IMPACT_WIDENING_UNNECESSARY":
            continue
        if source_id in direct or source_id not in {row["source_id"] for row in candidate["included_sources"]}:
            return None
        removed.append(source_id)
    return tuple(removed)


def record_scope(snapshot: ScopeSnapshot, candidate_or_audit: Mapping[str, Any]) -> ScopeSnapshot:
    if not isinstance(snapshot, ScopeSnapshot): raise _error("CHANGE_SCOPE_ORDER", "/snapshot", "Scope state cannot accept another artifact.")
    _guard(snapshot)
    if snapshot.blocked: raise _error("CHANGE_SCOPE_ORDER", "/snapshot", "Scope state cannot accept another artifact.")
    if not isinstance(candidate_or_audit, Mapping): raise _error("CHANGE_SCOPE_SHAPE", "/record", "A closed scope artifact is required.")
    if snapshot.candidate is None or (snapshot.false_audit is not None and snapshot.false_audit["verdict"] == "REWORK") or (snapshot.omission_audit is not None and snapshot.omission_audit["verdict"] == "REWORK"):
        _validate_candidate(candidate_or_audit)
        successor = snapshot.candidate is not None
        expected_generation = snapshot.generation + 1 if successor else snapshot.generation
        rejected = snapshot.false_audit if snapshot.false_audit is not None and snapshot.false_audit["verdict"] == "REWORK" else snapshot.omission_audit
        corrections = _false_corrections(snapshot.candidate, rejected) if rejected is not None and rejected is snapshot.false_audit else ()
        if corrections is None: raise _error("CHANGE_SCOPE_COVERAGE", "/audit/findings", "False-inclusion finding cannot safely correct scoped evidence.")
        narrow = rejected is not None and rejected is snapshot.false_audit and any(row["code"] == "IMPACT_WIDENING_UNNECESSARY" for row in rejected["findings"])
        if narrow and snapshot.widening_index == 0:
            raise _error("CHANGE_SCOPE_COVERAGE", "/audit/findings", "Symbol scope has no narrower substantive correction.")
        omission_index = _omission_index(snapshot) if rejected is not None and rejected is snapshot.omission_audit else None
        next_index = max(0, snapshot.widening_index - 1) if narrow else omission_index if omission_index is not None else snapshot.widening_index
        expected = _candidate(snapshot.inputs, expected_generation, next_index, artifact_sha256(snapshot.candidate) if successor else None, (artifact_sha256(rejected),) if rejected is not None else (), corrections or ())
        if _plain(candidate_or_audit) != _plain(expected): raise _error("CHANGE_SCOPE_BINDING", "/candidate", "Candidate does not exactly match deterministic scope evidence.")
        return _next(snapshot, snapshot.inputs, expected, None, None, expected_generation, next_index)
    if snapshot.false_audit is None:
        if candidate_or_audit.get("audit_kind") != "false_inclusion":
            raise _error("CHANGE_SCOPE_ORDER", "/audit/audit_kind", "False-inclusion audit must precede omission audit.")
        audit = _validate_audit(candidate_or_audit, snapshot.candidate, "false_inclusion", snapshot.inputs)
        return _next(snapshot, snapshot.inputs, snapshot.candidate, audit, None, snapshot.generation, snapshot.widening_index)
    if snapshot.omission_audit is None:
        if candidate_or_audit.get("audit_kind") != "omission":
            raise _error("CHANGE_SCOPE_ORDER", "/audit/audit_kind", "Omission audit must follow false-inclusion audit.")
        audit = _validate_audit(candidate_or_audit, snapshot.candidate, "omission", snapshot.inputs)
        return _next(snapshot, snapshot.inputs, snapshot.candidate, snapshot.false_audit, audit, snapshot.generation, snapshot.widening_index)
    raise _error("CHANGE_SCOPE_ORDER", "/record", "Scope generation is already sealed.")


def _receipt(snapshot: ScopeSnapshot) -> Mapping[str, Any]:
    candidate = snapshot.candidate; assert candidate is not None and snapshot.false_audit is not None and snapshot.omission_audit is not None
    return _freeze({"schema_version": "1.0.0", "artifact": "change-scope-receipt", "run_mode": candidate["run_mode"], "baseline_receipt_sha256": candidate["baseline_receipt_sha256"], "candidate_sha256": artifact_sha256(candidate), "audit_sha256s": {"false_inclusion": artifact_sha256(snapshot.false_audit), "omission": artifact_sha256(snapshot.omission_audit)}, "change_input_sha256": candidate["change_input_sha256"], "analytics_sha256": candidate["analytics_sha256"], "included_source_ids": [row["source_id"] for row in candidate["included_sources"]], "included_test_symbol_pairs": candidate["included_test_symbols"], "review_mode": "SEQUENTIAL", "independence_attestation_sha256": None})


def advance_scope(snapshot: ScopeSnapshot, controller: ReviewController | None = None) -> ScopeAction:
    if not isinstance(snapshot, ScopeSnapshot): raise _error("CHANGE_SCOPE_SHAPE", "/snapshot", "A scope snapshot is required.")
    _guard(snapshot)
    if snapshot.blocked: return ScopeAction("BLOCKED", None, (_freeze({"code": "CHANGE_SCOPE_COVERAGE", "path": "/scope", "message": "Scope evidence is unavailable."}),))
    if snapshot.candidate is None: return ScopeAction("PRODUCE_CHANGE_SCOPE", _candidate(snapshot.inputs, snapshot.generation, snapshot.widening_index))
    if snapshot.false_audit is None: return ScopeAction("RUN_SCOPE_FALSE_INCLUSION_AUDIT", snapshot.candidate)
    if snapshot.false_audit["verdict"] == "REWORK":
        corrections = _false_corrections(snapshot.candidate, snapshot.false_audit)
        if corrections is None: return ScopeAction("BLOCKED", None, (_freeze({"code": "CHANGE_SCOPE_COVERAGE", "path": "/audit/findings", "message": "False-inclusion evidence cannot be safely corrected."}),))
        narrow = any(row["code"] == "IMPACT_WIDENING_UNNECESSARY" for row in snapshot.false_audit["findings"])
        if narrow and snapshot.widening_index == 0:
            return ScopeAction("BLOCKED", None, (_freeze({"code": "CHANGE_SCOPE_COVERAGE", "path": "/audit/findings", "message": "Symbol scope has no narrower substantive correction."}),))
        return ScopeAction("PRODUCE_CHANGE_SCOPE", _candidate(snapshot.inputs, snapshot.generation + 1, max(0, snapshot.widening_index - 1) if narrow else snapshot.widening_index, artifact_sha256(snapshot.candidate), (artifact_sha256(snapshot.false_audit),), corrections))
    if snapshot.omission_audit is None: return ScopeAction("RUN_SCOPE_OMISSION_AUDIT", snapshot.candidate)
    if snapshot.omission_audit["verdict"] == "REWORK":
        next_index = _omission_index(snapshot)
        if next_index is None:
            return ScopeAction("BLOCKED", None, (_freeze({"code": "CHANGE_SCOPE_COVERAGE", "path": "/scope", "message": "No substantive full-scope correction is available."}),))
        return ScopeAction("PRODUCE_CHANGE_SCOPE", _candidate(snapshot.inputs, snapshot.generation + 1, next_index, artifact_sha256(snapshot.candidate), (artifact_sha256(snapshot.omission_audit),)))
    return ScopeAction("COMPLETE", _receipt(snapshot))
