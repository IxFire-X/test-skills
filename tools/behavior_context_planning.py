"""Closed, deterministic behavior-context batch planning and receipt validation.

This module never imports project code.  It reads only files already named by the
immutable V1 source inventory and keeps controller-supplied input paths out of
all emitted artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import weakref
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

try:
    from tools.schema_validation import schema_diagnostics
    from tools.test_classification import TestClassificationError, _confined, _digest, _freeze, _plain, _diag
except ImportError:  # direct script caller imports the sibling tool module
    from schema_validation import schema_diagnostics
    from test_classification import TestClassificationError, _confined, _digest, _freeze, _plain, _diag

_ROOT = Path(__file__).resolve().parents[1]
_BUDGET = 64 * 1024
_OVERLAP = 16 * 1024


@dataclass(frozen=True)
class ValidatedBehaviorContext:
    requirements: tuple[Mapping[str, Any], ...]
    receipt_sha256: str
    authorized_behavior_sources_sha256: str
    changed_requirements: tuple[Mapping[str, Any], ...] = ()
    retired_requirements: tuple[Mapping[str, Any], ...] = ()
    stable_requirement_links: tuple[Mapping[str, Any], ...] = ()


class ComposedBehaviorContext:
    """Opaque V6 authority minted only by promoted-evidence composition."""
    __slots__ = ("__weakref__",)

    @property
    def managed_behavior_context(self) -> Mapping[str, Any]:
        return _COMPOSED_CONTEXTS[self]["context"]["artifacts"]["managed_behavior_context"]

    @property
    def changed_behavior_context(self) -> Mapping[str, Any]:
        return _COMPOSED_CONTEXTS[self]["context"]["artifacts"]["changed_behavior_context"]

    @property
    def behavior_source_accounting(self) -> Mapping[str, Any]:
        return _COMPOSED_CONTEXTS[self]["context"]["artifacts"]["behavior_source_accounting"]

    @property
    def context(self) -> Mapping[str, Any]:
        return _COMPOSED_CONTEXTS[self]["context"]

    @property
    def receipt(self) -> Mapping[str, Any]:
        return _COMPOSED_CONTEXTS[self]["receipt"]


_COMPOSED_CONTEXTS: weakref.WeakKeyDictionary[ComposedBehaviorContext, Mapping[str, Any]] = weakref.WeakKeyDictionary()


def _fail(code: str, path: str, message: str) -> TestClassificationError:
    return TestClassificationError((_diag(path, code, message),))


def _bytes(source: Mapping[str, Any], project: Path, supplied: Mapping[str, bytes], snapshot_reader: Any | None = None) -> bytes:
    source_id = source["source_id"]
    if source["kind"] == "supplied_requirement":
        if source_id not in supplied:
            raise _fail("BEHAVIOR_SUPPLIED_INPUT", "/supplied-input", "Every supplied requirement needs exactly one controller input.")
        value = supplied[source_id]
        if not value or "sha256:" + hashlib.sha256(value).hexdigest() != source["content_digest"]:
            raise _fail("BEHAVIOR_SUPPLIED_INPUT", "/supplied-input", "Supplied input must be nonempty and match its inventory digest.")
        try: value.decode("utf-8")
        except UnicodeDecodeError as error: raise _fail("BEHAVIOR_PLAN", "/supplied-input", "Supplied input must be valid UTF-8.") from error
        return value
    path = source.get("path")
    if not isinstance(path, str) or Path(path).is_absolute() or ".." in PurePosixPath(path).parts:
        raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product paths must be safe project-relative paths.")
    candidate = project / path
    if candidate.is_symlink():
        raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product files cannot be symlinks.")
    try:
        resolved = _confined(project, candidate, "/sources")
        value = snapshot_reader(path) if snapshot_reader is not None else resolved.read_bytes()
    except (OSError, TestClassificationError) as error:
        if isinstance(error, TestClassificationError):
            raise
        raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product file could not be read.") from error
    if not isinstance(value, bytes):
        raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product file could not be read.")
    if "sha256:" + hashlib.sha256(value).hexdigest() != source["content_digest"]:
        raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product file bytes no longer match inventory.")
    try: value.decode("utf-8")
    except UnicodeDecodeError as error: raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product file must be valid UTF-8.") from error
    return value


def _source_roots(module: Mapping[str, Any]) -> tuple[str, ...]:
    roots = [str(value).replace("\\", "/").strip("/") for value in module.get("paths", {}).get("source", ())]
    return tuple(sorted(set(roots), key=lambda value: (-len(value), value)))


def derive_domain_key(source: Mapping[str, Any], module: Mapping[str, Any], project: Path | None = None) -> str:
    if source["kind"] == "supplied_requirement":
        return "_supplied"
    path = str(source["path"]).replace("\\", "/")
    if project is not None and isinstance(module.get("_resolved_root"), Path):
        try:
            prefix = module["_resolved_root"].resolve().relative_to(project.resolve()).as_posix().strip("/")
            if prefix and path.startswith(prefix + "/"):
                path = path[len(prefix) + 1:]
        except (OSError, ValueError):
            pass
    candidates = [root for root in _source_roots(module) if path == root or path.startswith(root + "/")]
    root = candidates[0] if candidates else ""
    remainder = path[len(root):].strip("/").split("/") if root else path.split("/")
    directories = remainder[:-1][:3]
    return "/".join(directories) if directories else "_root"


def stable_fragment_id(item: Mapping[str, Any], fragment: Mapping[str, Any]) -> str:
    """Derive the stable ID from closed semantic fields and the owning plan item."""
    payload = {key: _plain(value) for key, value in fragment.items() if key != "fragment_id"}
    payload["item_id"] = item["item_id"]; payload["source_id"] = item["source_id"]
    return "FRAGMENT-" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def stable_change_fragment_id(item: Mapping[str, Any], fragment: Mapping[str, Any]) -> str:
    """Derive the V2 identity from the closed change semantic fields."""
    payload = {"effect": fragment["effect"], "actor": fragment["actor"], "operation": fragment["operation"], "conditions": _plain(fragment["conditions"]), "outcomes": _plain(fragment["outcomes"]), "baseline_source_id": item["baseline_source_id"], "current_source_id": item["current_source_id"], "item_id": item["item_id"], "evidence_locators": _plain(fragment["evidence_locators"])}
    return "FRAGMENT-" + hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _cut(data: bytes, point: int, backwards: bool) -> int:
    point = max(0, min(len(data), point))
    if point in (0, len(data)):
        return point
    step = -1 if backwards else 1
    while 0 < point < len(data) and (data[point] & 0xC0) == 0x80:
        point += step
    return max(0, min(len(data), point))


def _ranges(data: bytes) -> list[tuple[int, int, int, int]]:
    if not data:
        return [(0, 0, 0, 0)]
    result: list[tuple[int, int, int, int]] = []
    start = 0
    while start < len(data):
        end = _cut(data, min(len(data), start + _BUDGET), True)
        if end <= start:  # a single code point larger than budget cannot occur in UTF-8
            end = _cut(data, min(len(data), start + _BUDGET), False)
        result.append((start, end, _cut(data, start - _OVERLAP, False), _cut(data, end + _OVERLAP, True)))
        start = end
    return result


def _v2_fail(code: str, path: str, message: str) -> TestClassificationError:
    return TestClassificationError((_diag(path, code, message),))


def _v2_shape(value: Any, schema: str, code: str) -> None:
    diagnostics = schema_diagnostics(_plain(value), _ROOT / "schemas" / schema, _ROOT)
    if diagnostics:
        raise _v2_fail(code, diagnostics[0]["path"], "Artifact does not satisfy its closed schema.")


def _v2_candidate(value: Mapping[str, Any]) -> None:
    _v2_shape(value, "change-scope-candidate.schema.json", "CHANGE_PLAN_SHAPE")


def _v2_inventory(value: Mapping[str, Any], selected_module: str, path: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, Mapping):
        raise _v2_fail("CHANGE_PLAN_SHAPE", path, "Inventory must be a closed JSON object.")
    if value.get("stage") == "source-inventory":
        _v2_shape(value, "source-inventory-output.schema.json", "CHANGE_PLAN_SHAPE")
        artifacts = value["artifacts"]
        if artifacts["authorized_behavior_sources_sha256"] != _digest(_plain(artifacts["authorized_behavior_sources"])):
            raise _v2_fail("CHANGE_PLAN_SHAPE", path, "Inventory envelope must bind its exact authorized sources.")
    authorized = _sources(value)
    if not isinstance(authorized, Mapping) or set(authorized) != {"module_id", "sources"} or authorized.get("module_id") != selected_module or not isinstance(authorized.get("sources"), list):
        raise _v2_fail("CHANGE_PLAN_SHAPE", path, "Inventory must contain the selected module's authorized sources.")
    rows = authorized["sources"]
    product_paths: list[str] = []
    seen_product = False
    for index, row in enumerate(rows):
        pointer = f"{path}/sources/{index}"
        if not isinstance(row, Mapping) or row.get("kind") not in {"supplied_requirement", "product_file"}:
            raise _v2_fail("CHANGE_PLAN_SHAPE", pointer, "Inventory source rows must use a closed supported variant.")
        if not isinstance(row.get("source_id"), str) or not row["source_id"] or not isinstance(row.get("content_digest"), str) or not row["content_digest"].startswith("sha256:") or len(row["content_digest"]) != 71 or any(character not in "0123456789abcdef" for character in row["content_digest"][7:]):
            raise _v2_fail("CHANGE_PLAN_SHAPE", pointer, "Inventory source identity and digest must be canonical.")
        if row["kind"] == "supplied_requirement":
            if set(row) != {"source_id", "kind", "content_digest"} or seen_product:
                raise _v2_fail("CHANGE_PLAN_SHAPE", pointer, "Supplied requirements must be closed and precede product files.")
            continue
        seen_product = True
        if set(row) != {"source_id", "kind", "path", "content_digest"} or not isinstance(row.get("path"), str) or not row["path"] or Path(row["path"]).is_absolute() or ".." in PurePosixPath(row["path"]).parts or "\\" in row["path"]:
            raise _v2_fail("CHANGE_PLAN_SHAPE", pointer, "Product sources must use safe canonical project-relative paths.")
        expected = "SOURCE-" + hashlib.sha256(b"product_file\0" + row["path"].encode("utf-8")).hexdigest()
        if row["source_id"] != expected:
            raise _v2_fail("CHANGE_PLAN_SHAPE", pointer, "Product source identity must equal its stable path identity.")
        product_paths.append(row["path"])
    if len({row["source_id"] for row in rows}) != len(rows) or product_paths != sorted(product_paths) or len(product_paths) != len(set(product_paths)):
        raise _v2_fail("CHANGE_PLAN_SHAPE", path, "Source identities must be unique and product paths canonically ordered.")
    return rows


def _v2_bytes(resolver: Any, side: str, row: Mapping[str, Any], digest: str, size: int) -> bytes:
    try:
        value = resolver(side, row)
    except TestClassificationError:
        raise
    except Exception as error:
        if getattr(error, "code", None) == "CHANGE_SOURCE_DRIFT" or "CHANGE_SOURCE_DRIFT" in str(error):
            raise _v2_fail("CHANGE_SOURCE_DRIFT", "/byte_resolver", "Authorized side bytes changed after planning.") from None
        raise _v2_fail("CHANGE_SIDE_BINDING", "/byte_resolver", "Authorized side bytes could not be resolved.") from None
    if not isinstance(value, bytes) or digest != "sha256:" + hashlib.sha256(value).hexdigest() or len(value) != size:
        raise _v2_fail("CHANGE_SOURCE_DRIFT", "/byte_resolver", "Resolved bytes must match the exact planned side digest and size.")
    return value


def _v2_sides(resolver: Any, change: Mapping[str, Any], before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for side, source in (("before", before), ("after", after)):
        if source is None:
            continue
        change_side = change.get(side)
        if not isinstance(change_side, Mapping) or change_side.get("content_sha256") != source["content_digest"] or change_side.get("size_bytes") is None:
            raise _v2_fail("CHANGE_SIDE_BINDING", f"/changes/{side}", "Change side must bind its matching authoritative inventory source.")
        text = bool(change_side.get("text"))
        data = _v2_bytes(resolver, side, source, source["content_digest"], int(change_side["size_bytes"]))
        if not text:
            result.append({"side": side, "content_sha256": source["content_digest"], "size_bytes": len(data), "text": False})
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise _v2_fail("CHANGE_SIDE_BINDING", f"/changes/{side}", "Text side must be valid UTF-8.") from error
            for start, end, read_start, read_end in _ranges(data):
                result.append({"side": side, "content_sha256": source["content_digest"], "accounted_range": {"start": start, "end": end}, "read_range": {"start": read_start, "end": read_end}})
    return result


def _v2_context_sides(resolver: Any, before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for side, source in (("before", before), ("after", after)):
        if source is None:
            continue
        try:
            value = resolver(side, source)
        except TestClassificationError:
            raise
        except Exception as error:
            if getattr(error, "code", None) == "CHANGE_SOURCE_DRIFT" or "CHANGE_SOURCE_DRIFT" in str(error):
                raise _v2_fail("CHANGE_SOURCE_DRIFT", "/byte_resolver", "Authorized side bytes changed after planning.") from None
            raise _v2_fail("CHANGE_SIDE_BINDING", "/byte_resolver", "Authorized side bytes could not be resolved.") from None
        if not isinstance(value, bytes) or source["content_digest"] != "sha256:" + hashlib.sha256(value).hexdigest():
            raise _v2_fail("CHANGE_SOURCE_DRIFT", "/byte_resolver", "Resolved bytes must match the exact inventory digest.")
        try:
            value.decode("utf-8")
        except UnicodeDecodeError:
            raise _v2_fail("CHANGE_SIDE_BINDING", "/byte_resolver", "Context source bytes must be valid UTF-8.") from None
        for start, end, read_start, read_end in _ranges(value):
            result.append({"side": side, "content_sha256": source["content_digest"], "accounted_range": {"start": start, "end": end}, "read_range": {"start": read_start, "end": read_end}})
    return result


def _v2_plan_semantics(plan: Mapping[str, Any]) -> None:
    _v2_shape(plan, "behavior-context-plan.schema.json", "CHANGE_PLAN_SHAPE")
    if plan.get("schema_version") != "2.0.0":
        raise _v2_fail("CHANGE_PLAN_SHAPE", "/schema_version", "Change-set validation requires Plan V2.")
    item_number = 0
    seen_pairs: set[tuple[Any, Any]] = set()
    for batch_number, batch in enumerate(plan["batches"], 1):
        if batch["batch_id"] != f"BATCH-{batch_number:06d}":
            raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "Plan batches must use canonical physical order.")
        for item in batch["items"]:
            item_number += 1
            if item["item_id"] != f"ITEM-{item_number:06d}":
                raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "Plan items must use canonical physical order.")
            pair = (item["baseline_source_id"], item["current_source_id"])
            if pair in seen_pairs:
                raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "A logical source pair may appear only once.")
            seen_pairs.add(pair)
            grouped: dict[str, list[Mapping[str, Any]]] = {"before": [], "after": []}
            for side in item["evidence_sides"]:
                grouped[side["side"]].append(side)
            expected = ({"before"} if item["baseline_source_id"] is not None else set()) | ({"after"} if item["current_source_id"] is not None else set())
            if {name for name, rows in grouped.items() if rows} != expected:
                raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "Evidence sides must equal the item's source identities.")
            if [side["side"] for side in item["evidence_sides"]] != [name for name in ("before", "after") for _ in grouped[name]]:
                raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "Evidence sides must be ordered before then after.")
            for rows in grouped.values():
                if not rows:
                    continue
                if len({row["content_sha256"] for row in rows}) != 1:
                    raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "Every logical side must use one content digest.")
                text_rows = [row for row in rows if "read_range" in row]
                if text_rows and len(text_rows) != len(rows):
                    raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "A logical side cannot mix text ranges and binary metadata.")
                if not text_rows and len(rows) != 1:
                    raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "A binary side has exactly one metadata row.")
                cursor = 0
                for row in text_rows:
                    accounted, read = row["accounted_range"], row["read_range"]
                    if accounted["start"] != cursor or accounted["end"] < accounted["start"] or read["start"] > accounted["start"] or read["end"] < accounted["end"]:
                        raise _v2_fail("CHANGE_PLAN_SHAPE", "/batches", "Text ranges must cover the side exactly once in order.")
                    cursor = accounted["end"]


def _v2_verify_planned_bytes(resolver: Any, item: Mapping[str, Any]) -> None:
    for side_name in ("before", "after"):
        rows = [row for row in item["evidence_sides"] if row["side"] == side_name]
        if not rows:
            continue
        size = rows[0]["size_bytes"] if "size_bytes" in rows[0] else max(row["accounted_range"]["end"] for row in rows)
        source_id = item["baseline_source_id"] if side_name == "before" else item["current_source_id"]
        binding = {"source_id": source_id, "content_digest": rows[0]["content_sha256"]}
        data = _v2_bytes(resolver, side_name, binding, rows[0]["content_sha256"], size)
        if "read_range" in rows[0]:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise _v2_fail("CHANGE_SIDE_BINDING", "/byte_resolver", "Planned text side must remain valid UTF-8.") from None


def build_change_context_plan(project: Path, selected_module: Mapping[str, Any], scope_receipt: Mapping[str, Any], scope_candidate: Mapping[str, Any], baseline_inventory: Mapping[str, Any], current_inventory: Mapping[str, Any], byte_resolver: Any) -> Mapping[str, Any]:
    """Build the closed V2 plan from a promoted receipt and its safe candidate."""
    _v2_shape(scope_receipt, "change-scope-receipt.schema.json", "CHANGE_PLAN_SHAPE")
    _v2_candidate(scope_candidate)
    if not isinstance(project, Path) or not isinstance(selected_module, Mapping) or not isinstance(selected_module.get("id"), str):
        raise _v2_fail("CHANGE_PLAN_SHAPE", "/selected_module", "A normalized project and selected module are required.")
    module = selected_module["id"]
    if scope_receipt["run_mode"] != "CHANGE_SET" or scope_candidate["run_mode"] != "CHANGE_SET" or scope_receipt["candidate_sha256"] != _digest(_plain(scope_candidate)):
        raise _v2_fail("CHANGE_PLAN_SHAPE", "/scope", "V2 requires an exact promoted CHANGE_SET candidate.")
    for key in ("baseline_receipt_sha256", "change_input_sha256", "analytics_sha256"):
        if scope_receipt[key] != scope_candidate[key]:
            raise _v2_fail("CHANGE_PLAN_SHAPE", "/scope", "Receipt and candidate bindings must agree.")
    if scope_candidate["selected_module"] != module:
        raise _v2_fail("CHANGE_PLAN_SHAPE", "/selected_module", "Candidate module must equal the selected module.")
    baseline, current = _v2_inventory(baseline_inventory, module, "/baseline_inventory"), _v2_inventory(current_inventory, module, "/current_inventory")
    if scope_candidate["current_source_inventory_sha256"] != _digest(_plain(_sources(current_inventory))):
        raise _v2_fail("CHANGE_PLAN_SHAPE", "/current_inventory", "Candidate must bind the exact current inventory.")
    by_base_path = {row["path"]: row for row in baseline if row["kind"] == "product_file"}
    by_current_path = {row["path"]: row for row in current if row["kind"] == "product_file"}
    by_base_id, by_current_id = {row["source_id"]: row for row in baseline}, {row["source_id"]: row for row in current}
    included_ids = list(scope_receipt["included_source_ids"])
    candidate_ids = [row["source_id"] for row in scope_candidate["included_sources"]]
    if included_ids != candidate_ids or len(included_ids) != len(set(included_ids)):
        raise _v2_fail("CHANGE_PLAN_SHAPE", "/included_source_ids", "Receipt must bind the candidate's exact included source projection.")
    included = set(included_ids)
    baseline_order = {row["source_id"]: index for index, row in enumerate(baseline)}
    current_order = {row["source_id"]: index for index, row in enumerate(current)}
    direct_ids: set[str] = set(); rows: list[tuple[int, int, int, dict[str, Any]]] = []
    changes = scope_candidate["changes"]
    for index, change in enumerate(changes):
        kind = change["kind"]
        old_path, new_path = change.get("old_path", change.get("path")), change.get("new_path", change.get("path"))
        before = by_base_path.get(old_path) if change.get("before") is not None else None
        after = by_current_path.get(new_path) if change.get("after") is not None else None
        if (change.get("before") is not None) != (before is not None) or (change.get("after") is not None) != (after is not None):
            raise _v2_fail("CHANGE_SIDE_BINDING", f"/changes/{index}", "Every change side must match one authoritative inventory row.")
        if not ({row["source_id"] for row in (before, after) if row} & included):
            continue
        direct_ids.update(row["source_id"] for row in (before, after) if row)
        sides = _v2_sides(byte_resolver, change, before, after)
        item = {"item_id": f"ITEM-{len(rows)+1:06d}", "change_id": change["change_id"], "change_kind": kind, "baseline_source_id": before["source_id"] if before else None, "current_source_id": after["source_id"] if after else None, "domain_key": derive_domain_key(after or before, selected_module, project), "evidence_sides": sides}
        slot = baseline_order[before["source_id"]] if before else current_order[after["source_id"]]
        rows.append((0 if before else 1, slot, index, item))
    for position, source_id in enumerate(included_ids):
        if source_id in direct_ids:
            continue
        before, after = by_base_id.get(source_id), by_current_id.get(source_id)
        if before is None and after is None:
            raise _v2_fail("CHANGE_SIDE_BINDING", "/included_source_ids", "Every included source must belong to a supplied inventory.")
        item = {"item_id": f"ITEM-{len(rows)+1:06d}", "change_id": None, "change_kind": "context", "baseline_source_id": before["source_id"] if before else None, "current_source_id": after["source_id"] if after else None, "domain_key": derive_domain_key(after or before, selected_module, project), "evidence_sides": _v2_context_sides(byte_resolver, before, after)}
        slot = baseline_order[source_id] if before else current_order[source_id]
        rows.append((0 if before else 1, slot, len(changes) + position, item))
    rows.sort(key=lambda value: value[:3])
    for number, row in enumerate(rows, 1):
        item = row[3]
        item["item_id"] = f"ITEM-{number:06d}"
    plan = {"schema_version": "2.0.0", "scope_receipt_sha256": _digest(_plain(scope_receipt)), "scope_candidate_sha256": _digest(_plain(scope_candidate)), "selected_module": module, "baseline_inventory_sha256": _digest(_plain(_sources(baseline_inventory))), "current_inventory_sha256": _digest(_plain(_sources(current_inventory))), "batches": [{"batch_id": f"BATCH-{index:06d}", "items": [row[3]]} for index, row in enumerate(rows, 1)]}
    _v2_plan_semantics(plan)
    return _freeze(plan)


def validate_change_batch_result(scope_receipt: Mapping[str, Any], plan: Mapping[str, Any], result: Mapping[str, Any], byte_resolver: Any) -> tuple[Mapping[str, str], ...]:
    try:
        _v2_shape(scope_receipt, "change-scope-receipt.schema.json", "CHANGE_RESULT_SHAPE")
        _v2_plan_semantics(plan)
        _v2_shape(result, "behavior-context-batch-result.schema.json", "CHANGE_RESULT_SHAPE")
        if result.get("schema_version") != "2.0.0":
            raise _v2_fail("CHANGE_RESULT_SHAPE", "/schema_version", "Change-set validation requires Batch Result V2.")
        if plan["scope_receipt_sha256"] != _digest(_plain(scope_receipt)) or result["scope_receipt_sha256"] != _digest(_plain(scope_receipt)) or result["plan_sha256"] != _digest(_plain(plan)):
            raise _v2_fail("CHANGE_RESULT_SHAPE", "/result", "Result must bind the exact receipt and plan.")
        batch = next((row for row in plan["batches"] if row["batch_id"] == result["batch_id"]), None)
        if batch is None or [row["item_id"] for row in result["items"]] != [row["item_id"] for row in batch["items"]]:
            raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Result items must equal the planned batch in order.")
        for item, row in zip(batch["items"], result["items"]):
            _v2_verify_planned_bytes(byte_resolver, item)
            outcome, fragments = row["outcome"], row.get("behavior_fragments", [])
            if item["change_kind"] == "context" and outcome not in {"supporting_context", "no_changed_observable_fact"}: raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Context items cannot promote changed fragments or tombstones.")
            if outcome == "supporting_context" and item["change_kind"] != "context": raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Only context items can emit supporting observations.")
            deleted_item = item["change_kind"] == "deleted" or (item["change_kind"] == "binary" and item["current_source_id"] is None)
            if outcome == "deleted_behavior_tombstones" and not deleted_item: raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Only a deleted source can emit deletion tombstones.")
            if outcome in {"supporting_context", "no_changed_observable_fact"} and fragments: raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Non-fragment outcomes cannot carry fragments.")
            if outcome in {"changed_behavior_fragments", "deleted_behavior_tombstones"} and not fragments: raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Behavior outcomes require fragments.")
            for fragment in fragments:
                if fragment["fragment_id"] != stable_change_fragment_id(item, fragment): raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Fragment ID must equal its closed deterministic derivation.")
                sides = {name: [side for side in item["evidence_sides"] if side["side"] == name] for name in ("before", "after")}
                locator_sides = {locator["side"] for locator in fragment["evidence_locators"]}
                required = {"after"} if fragment["effect"] == "added" else {"before"} if fragment["effect"] == "retired" else {"before", "after"}
                if not required <= locator_sides: raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Effect must bind its required evidence sides.")
                if outcome == "deleted_behavior_tombstones" and fragment["effect"] != "retired": raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Deletion tombstones may contain only retired effects.")
                if item["change_kind"] == "added" and fragment["effect"] != "added": raise _v2_fail("CHANGE_RESULT_SHAPE", "/items", "Added sources may emit only added effects.")
                for locator in fragment["evidence_locators"]:
                    matching = [side for side in sides[locator["side"]] if side.get("content_sha256") == locator["content_sha256"] and "read_range" in side and side["read_range"]["start"] <= locator["start_byte"] < locator["end_byte"] <= side["read_range"]["end"]]
                    if not matching:
                        raise _v2_fail("CHANGE_SIDE_BINDING", "/items", "Locator must remain inside an exact planned text side.")
                authoritative = "before" if fragment["effect"] == "retired" else "after"
                if not any(locator["side"] == authoritative and locator["start_byte"] <= fragment["anchor_byte"] < locator["end_byte"] for locator in fragment["evidence_locators"]):
                    raise _v2_fail("CHANGE_SIDE_BINDING", "/items", "Fragment anchor must belong to its authoritative evidence side.")
        return ()
    except TestClassificationError as error:
        return error.diagnostics


def _sources(inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    if inventory.get("stage") == "source-inventory":
        artifacts = inventory.get("artifacts")
        if isinstance(artifacts, Mapping):
            return artifacts.get("authorized_behavior_sources", {})
    return inventory


def build_context_plan(
    project: Path, module: Mapping[str, Any], inventory: Mapping[str, Any], supplied_inputs: Mapping[str, bytes] = {}, *, snapshot_reader: Any | None = None,
) -> Mapping[str, Any]:
    authorized = _sources(inventory)
    sources = authorized.get("sources") if isinstance(authorized, Mapping) else None
    if not isinstance(sources, list) or not isinstance(authorized.get("module_id"), str):
        raise _fail("BEHAVIOR_PLAN", "/inventory", "Authorized source inventory is invalid.")
    if module.get("id") != authorized["module_id"]:
        raise _fail("BEHAVIOR_PLAN", "/module", "Selected module must equal the authorized inventory module.")
    supplied_ids = {row.get("source_id") for row in sources if isinstance(row, Mapping) and row.get("kind") == "supplied_requirement"}
    if set(supplied_inputs) != supplied_ids:
        raise _fail("BEHAVIOR_SUPPLIED_INPUT", "/supplied-input", "Supplied input IDs must exactly equal authorized supplied requirements.")
    items: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, Mapping) or source.get("kind") not in {"product_file", "supplied_requirement"}:
            raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized sources must use supported closed kinds.")
        data = _bytes(source, project, supplied_inputs, snapshot_reader)
        domain = derive_domain_key(source, module, project)
        for start, end, read_start, read_end in _ranges(data):
            items.append({"item_id": f"ITEM-{len(items)+1:06d}", "source_id": source["source_id"], "domain_key": domain, "accounted_range": {"start": start, "end": end}, "read_range": {"start": read_start, "end": read_end}})
    batches = [{"batch_id": f"BATCH-{index:06d}", "items": items[index:index + 1]} for index in range(0, len(items), 1)]
    for index, batch in enumerate(batches, 1): batch["batch_id"] = f"BATCH-{index:06d}"
    return _freeze({"schema_version": "1.0.0", "selected_module": authorized["module_id"], "authorized_behavior_sources_sha256": _digest(authorized), "batches": batches})


def _range(value: Any, pointer: str) -> tuple[int, int]:
    if not isinstance(value, Mapping) or type(value.get("start")) is not int or type(value.get("end")) is not int or value["start"] < 0 or value["end"] < value["start"]:
        raise _fail("BEHAVIOR_BATCH_RESULT", pointer, "Ranges must be valid half-open byte ranges.")
    return value["start"], value["end"]


def validate_batch_result(plan: Mapping[str, Any], result: Mapping[str, Any]) -> tuple[Mapping[str, str], ...]:
    shape = schema_diagnostics(_plain(result), _ROOT / "schemas" / "behavior-context-batch-result.schema.json", _ROOT)
    if shape: return (_diag(shape[0]["path"], "BEHAVIOR_BATCH_RESULT", "Batch result does not satisfy its closed schema."),)
    batches = {row["batch_id"]: row for row in plan.get("batches", [])}
    batch = batches.get(result["batch_id"])
    if result.get("plan_sha256") != _digest(_plain(plan)) or batch is None:
        return (_diag("/batch_id", "BEHAVIOR_BATCH_RESULT", "Result must bind the exact plan and planned batch."),)
    expected = batch["items"]
    rows = result["items"]
    if [row["item_id"] for row in rows] != [row["item_id"] for row in expected]:
        return (_diag("/items", "BEHAVIOR_BATCH_RESULT", "Result items must equal planned items in order."),)
    for index, (item, row) in enumerate(zip(expected, rows)):
        fragments = row.get("behavior_fragments", [])
        if row["outcome"] == "behavior_fragments" and not fragments:
            return (_diag(f"/items/{index}", "BEHAVIOR_BATCH_RESULT", "Fragment outcome requires fragments."),)
        if row["outcome"] == "no_supported_observable_fact" and fragments:
            return (_diag(f"/items/{index}", "BEHAVIOR_BATCH_RESULT", "No-fact outcome cannot contain fragments."),)
        for fragment in fragments:
            start, end = _range(item["accounted_range"], "")
            if fragment["fragment_id"] != stable_fragment_id(item, fragment):
                return (_diag(f"/items/{index}/behavior_fragments", "BEHAVIOR_BATCH_RESULT", "Fragment ID must equal its deterministic closed derivation."),)
            anchor = fragment["anchor_byte"]
            if not start <= anchor < end:
                return (_diag(f"/items/{index}/behavior_fragments", "BEHAVIOR_BATCH_RESULT", "Fragment anchor must be owned by accounted range."),)
            rs, re = _range(item["read_range"], "")
            for evidence in fragment["evidence_ranges"]:
                es, ee = _range(evidence, "")
                if es < rs or ee > re:
                    return (_diag(f"/items/{index}/behavior_fragments", "BEHAVIOR_BATCH_RESULT", "Evidence must remain inside read range."),)
    return ()


def build_context_receipt(project: Path, module: Mapping[str, Any], inventory: Mapping[str, Any], plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]], supplied_inputs: Mapping[str, bytes] = {}) -> Mapping[str, Any]:
    authorized = _sources(inventory)
    expected_digest = _digest(_plain(authorized))
    if module.get("id") != authorized.get("module_id") or plan.get("authorized_behavior_sources_sha256") != expected_digest or plan.get("selected_module") != authorized.get("module_id"):
        raise _fail("BEHAVIOR_RECEIPT", "/plan", "Plan does not bind the selected inventory.")
    # Planning re-reads every bound byte, including supplied input, before receipt issuance.
    current = build_context_plan(project, module, authorized, supplied_inputs)
    if _plain(current) != _plain(plan):
        raise _fail("BEHAVIOR_RECEIPT", "/plan", "Plan is stale or not mechanically deterministic.")
    if not isinstance(results, Sequence) or any(not isinstance(row, Mapping) for row in results):
        raise _fail("BEHAVIOR_BATCH_RESULT", "/batch-result", "Every batch result must be a closed JSON object.")
    if len(results) != len(plan["batches"]): raise _fail("BEHAVIOR_RECEIPT", "/batch-result", "Exactly one result is required for every batch.")
    registry: list[dict[str, str]] = []; outcomes: list[dict[str, str]] = []
    item_rows = {item["item_id"]: item for batch in plan["batches"] for item in batch["items"]}
    result_by_batch = {row.get("batch_id"): row for row in results}
    if len(result_by_batch) != len(results) or set(result_by_batch) != {row["batch_id"] for row in plan["batches"]}:
        raise _fail("BEHAVIOR_RECEIPT", "/batch-result", "Results must cover planned batches exactly once.")
    source_has_fragments: set[str] = set()
    for batch in plan["batches"]:
        result = result_by_batch[batch["batch_id"]]
        diagnostics = validate_batch_result(plan, result)
        if diagnostics: raise TestClassificationError(diagnostics)
        for row in result["items"]:
            item = item_rows[row["item_id"]]
            for fragment in row.get("behavior_fragments", []):
                registry.append({"fragment_id": fragment["fragment_id"], "source_id": item["source_id"], "item_id": item["item_id"]}); source_has_fragments.add(item["source_id"])
    if len({row["fragment_id"] for row in registry}) != len(registry): raise _fail("BEHAVIOR_RECEIPT", "/fragment_registry", "Fragment IDs must be unique.")
    for source in authorized["sources"]:
        outcomes.append({"source_id": source["source_id"], "domain_key": derive_domain_key(source, module, project), "outcome": "behavior_fragments" if source["source_id"] in source_has_fragments else "no_supported_observable_fact"})
    return _freeze({"schema_version": "1.0.0", "selected_module": authorized["module_id"], "authorized_behavior_sources_sha256": expected_digest, "context_plan_sha256": _digest(_plain(plan)), "batch_result_sha256s": [_digest(_plain(result_by_batch[batch["batch_id"]])) for batch in plan["batches"]], "fragment_registry": registry, "source_outcomes": outcomes})


def _context_inventories(inventories: Mapping[str, Any], module_id: str) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    _v2_shape(inventories, "source-inventory-output.schema.json", "BEHAVIOR_ACCOUNTING_SHAPE")
    artifacts = inventories.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/inventories/artifacts", "Source inventory artifacts are required.")
    authorized = artifacts.get("authorized_behavior_sources")
    technical = artifacts.get("technical_test_inventory")
    if not isinstance(authorized, Mapping) or not isinstance(technical, Mapping):
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/inventories/artifacts", "Both source inventory projections are required.")
    if (artifacts.get("authorized_behavior_sources_sha256") != _digest(_plain(authorized))
            or artifacts.get("technical_test_inventory_sha256") != _digest(_plain(technical))
            or authorized.get("module_id") != module_id or technical.get("module_id") != module_id):
        raise _fail("BEHAVIOR_ACCOUNTING_DIGEST", "/inventories", "Inventories must bind the exact selected-module projections.")
    return authorized, technical


def _resolver_bytes(byte_resolver: Any, side: str, source: Mapping[str, Any]) -> bytes:
    try:
        value = byte_resolver(side, source)
    except TestClassificationError:
        raise
    except Exception as error:
        if getattr(error, "code", None) == "CHANGE_SOURCE_DRIFT" or "CHANGE_SOURCE_DRIFT" in str(error):
            raise _fail("CHANGE_SOURCE_DRIFT", "/byte_resolver", "Authorized source bytes changed after planning.") from None
        raise _fail("BEHAVIOR_RECEIPT", "/byte_resolver", "Authorized source bytes could not be resolved.") from None
    if not isinstance(value, bytes) or source.get("content_digest") != "sha256:" + hashlib.sha256(value).hexdigest():
        raise _fail("CHANGE_SOURCE_DRIFT", "/byte_resolver", "Resolved bytes must match the exact authorized digest.")
    return value


def _baseline_projection(scope_receipt: Mapping[str, Any], baseline_context: Any) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]] | None:
    if scope_receipt.get("run_mode") == "FULL":
        if baseline_context is not None:
            raise _fail("BEHAVIOR_RECEIPT", "/baseline_context", "FULL context composition cannot consume a predecessor.")
        return None
    if not isinstance(baseline_context, Mapping) or set(baseline_context) != {"predecessor", "context", "receipt"}:
        raise _fail("BEHAVIOR_RECEIPT", "/baseline_context", "CHANGE_SET requires the issued predecessor plus its exact context and receipt.")
    try:
        from tools.baseline_lifecycle import ScopePredecessor, scope_predecessor_projection
    except ImportError:
        from baseline_lifecycle import ScopePredecessor, scope_predecessor_projection
    predecessor = baseline_context["predecessor"]
    if not isinstance(predecessor, ScopePredecessor):
        raise _fail("BASELINE_BINDING", "/baseline_context/predecessor", "An issued predecessor capability is required.")
    try:
        projection = scope_predecessor_projection(predecessor)
    except Exception as error:
        raise _fail("BASELINE_BINDING", "/baseline_context/predecessor", "Predecessor capability is not authoritative.") from None
    context = baseline_context["context"]
    receipt = baseline_context["receipt"]
    context_shape = schema_diagnostics(_plain(context), _ROOT / "schemas" / "context-marker-output.schema.json", _ROOT)
    receipt_shape = schema_diagnostics(_plain(receipt), _ROOT / "schemas" / "behavior-context-receipt.schema.json", _ROOT)
    if context_shape or receipt_shape:
        raise _fail("BASELINE_BINDING", "/baseline_context", "Predecessor context and receipt must satisfy their closed schemas.")
    if (scope_receipt.get("baseline_receipt_sha256") != projection.get("receipt_sha256")
            or projection.get("selected_module") != receipt.get("selected_module")
            or projection.get("context_envelope_sha256") != _digest(_plain(context))
            or projection.get("behavior_context_receipt_sha256") != _digest(_plain(receipt))
            or context.get("schema_version") != "6.0.0" or receipt.get("schema_version") != "2.0.0"):
        raise _fail("BASELINE_BINDING", "/baseline_context", "Predecessor carriers do not bind the promoted scope baseline.")
    artifacts = context.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {"managed_behavior_context", "changed_behavior_context", "behavior_source_accounting"}:
        raise _fail("BASELINE_BINDING", "/baseline_context/context", "Predecessor must be a complete V6 context.")
    return projection, artifacts, receipt


def _promoted_results(
    scope_receipt: Mapping[str, Any],
    plan: Mapping[str, Any],
    promotions: Sequence[Any],
    byte_resolver: Any,
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], int]:
    try:
        from tools.batch_promotion import PromotionEvidence, replay_promotion_ledger
        from tools.flow_artifacts import FlowError
    except ImportError:
        from batch_promotion import PromotionEvidence, replay_promotion_ledger
        from flow_artifacts import FlowError
    batches = plan.get("batches")
    if not isinstance(batches, list) or not isinstance(promotions, Sequence) or isinstance(promotions, (str, bytes)) or len(promotions) != len(batches):
        raise _fail("PROMOTION_COVERAGE", "/promotions", "Exactly one promotion evidence chain is required for every planned batch.")
    if any(not isinstance(evidence, PromotionEvidence) for evidence in promotions):
        raise _fail("PROMOTION_COVERAGE", "/promotions", "Only replayable promotion evidence is authoritative.")
    try:
        validated_rows = replay_promotion_ledger(scope_receipt, plan, tuple(promotions))
    except FlowError as error:
        raise _fail(error.code, "/promotions", "Promotion ledger did not reproduce its exact authority.") from None
    results: list[Mapping[str, Any]] = []
    authorities: list[Mapping[str, Any]] = []
    reworks = 0
    for index, (batch, validated) in enumerate(zip(batches, validated_rows)):
        if validated.promotion.get("batch_id") != batch.get("batch_id"):
            raise _fail("PROMOTION_COVERAGE", f"/promotions/{index}", "Promotion order must equal physical plan batch order.")
        diagnostics = (validate_batch_result(plan, validated.result) if scope_receipt.get("run_mode") == "FULL"
                       else validate_change_batch_result(scope_receipt, plan, validated.result, byte_resolver))
        if diagnostics:
            raise TestClassificationError(diagnostics)
        results.append(validated.result)
        authorities.append(validated.promotion)
        reworks += validated.rework_count
    return tuple(results), tuple(authorities), reworks


def _semantic_tuple(fragment: Mapping[str, Any]) -> Mapping[str, Any]:
    return {key: _plain(fragment[key]) for key in ("actor", "operation", "conditions", "outcomes")}


def _semantic_requirement_id(semantic: Mapping[str, Any]) -> str:
    return "REQ-" + hashlib.sha256(json.dumps(_plain(semantic), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _semantic_requirement(semantic: Mapping[str, Any], source_ids: Sequence[str], display_order: int) -> Mapping[str, Any]:
    conditions = " and ".join(semantic["conditions"])
    text = f"{semantic['actor']} {semantic['operation']}"
    if conditions:
        text += f" when {conditions}"
    text += f"; outcomes: {'; '.join(semantic['outcomes'])}."
    return _freeze({"requirement_id": _semantic_requirement_id(semantic), "display_order": display_order, "text": text, "provenance": list(source_ids)})


def _retirement_rows(
    baseline_requirements: Mapping[str, Mapping[str, Any]], baseline_links: Mapping[str, tuple[str, ...]],
    current_claims: set[str], retired_fragments: Mapping[str, list[Mapping[str, Any]]],
) -> tuple[Mapping[str, Any], ...]:
    result: list[Mapping[str, Any]] = []
    for requirement_id, fragments in retired_fragments.items():
        baseline = baseline_requirements.get(requirement_id)
        if baseline is None:
            raise _fail("BEHAVIOR_RETIREMENT", "/promotions", "Retired semantic evidence must identify a baseline requirement.")
        former_sources = set(baseline_links[requirement_id])
        evidence_sources = {row["source_id"] for row in fragments}
        if requirement_id in current_claims or evidence_sources != former_sources:
            raise _fail("BEHAVIOR_RETIREMENT", "/promotions", "Retirement requires promoted evidence for every former support and no surviving semantic support.")
        result.append(_freeze({
            "object_kind": "requirement", "object_id": requirement_id,
            "baseline_object_sha256": _digest(_plain(baseline)),
            "reason": "REQUIREMENT_NO_LONGER_OBSERVABLE",
            "support_evidence": {"status": "NO_SURVIVING_SOURCE_SUPPORT", "surviving_source_ids": [], "retirement_fragment_ids": [row["fragment_id"] for row in fragments]},
        }))
    return tuple(result)


def compose_behavior_context(
    project: Path,
    module: Mapping[str, Any],
    inventories: Mapping[str, Any],
    scope_receipt: Mapping[str, Any],
    plan: Mapping[str, Any],
    promotions: Sequence[Any],
    baseline_context: Any,
    byte_resolver: Any,
) -> ComposedBehaviorContext:
    """Compose V6 only from dual-audited promoted semantic results."""
    _v2_shape(scope_receipt, "change-scope-receipt.schema.json", "BEHAVIOR_RECEIPT")
    _v2_shape(plan, "behavior-context-plan.schema.json", "BEHAVIOR_RECEIPT")
    if not isinstance(project, Path) or not isinstance(module, Mapping) or not isinstance(module.get("id"), str):
        raise _fail("BEHAVIOR_RECEIPT", "/module", "A normalized project and selected module are required.")
    run_mode = scope_receipt.get("run_mode")
    if run_mode not in {"FULL", "CHANGE_SET"} or plan.get("schema_version") != ("1.0.0" if run_mode == "FULL" else "2.0.0"):
        raise _fail("BEHAVIOR_RECEIPT", "/run_mode", "Run mode requires the exact FULL/V1 or CHANGE_SET/V2 plan.")
    authorized, technical = _context_inventories(inventories, module["id"])
    current_sources = authorized["sources"]
    for source in current_sources:
        _resolver_bytes(byte_resolver, "after", source)
    if run_mode == "FULL":
        if scope_receipt.get("included_source_ids") != [row["source_id"] for row in current_sources]:
            raise _fail("BEHAVIOR_RECEIPT", "/scope_receipt/included_source_ids", "FULL scope receipt must cover current authorized sources exactly in inventory order.")
        supplied = {row["source_id"]: _resolver_bytes(byte_resolver, "after", row) for row in current_sources if row["kind"] == "supplied_requirement"}
        current_source_reader = lambda path: _resolver_bytes(
            byte_resolver, "after", next(row for row in current_sources if row.get("path") == path)
        )
        if _plain(build_context_plan(project, module, authorized, supplied, snapshot_reader=current_source_reader)) != _plain(plan):
            raise _fail("BEHAVIOR_RECEIPT", "/plan", "FULL plan must equal the deterministic current-source plan.")
    else:
        if (plan.get("selected_module") != module["id"] or plan.get("scope_candidate_sha256") != scope_receipt.get("candidate_sha256")
                or plan.get("scope_receipt_sha256") != _digest(_plain(scope_receipt)) or plan.get("current_inventory_sha256") != _digest(_plain(authorized))):
            raise _fail("BEHAVIOR_RECEIPT", "/plan", "CHANGE_SET plan must bind the exact module, scope candidate, receipt, and current inventory.")
    baseline = _baseline_projection(scope_receipt, baseline_context)
    baseline_managed: Mapping[str, Any] | None = None
    baseline_receipt: Mapping[str, Any] | None = None
    baseline_sources: tuple[Mapping[str, Any], ...] = ()
    baseline_context_sha256: str | None = None
    if baseline is not None:
        projection, artifacts, baseline_receipt = baseline
        baseline_managed = artifacts["managed_behavior_context"]
        baseline_sources = tuple(projection["sources"])
        baseline_context_sha256 = projection["context_envelope_sha256"]
        if plan.get("baseline_inventory_sha256") != _digest({"module_id": module["id"], "sources": _plain(baseline_sources)}):
            raise _fail("BASELINE_BINDING", "/plan/baseline_inventory_sha256", "CHANGE_SET plan must bind predecessor sources.")
    results, authorities, rework_count = _promoted_results(scope_receipt, plan, promotions, byte_resolver)
    plan_items = {item["item_id"]: item for batch in plan["batches"] for item in batch["items"]}
    registry_rows: list[dict[str, Any]] = []
    claims: dict[str, dict[str, Any]] = {}
    retired_claims: dict[str, list[Mapping[str, Any]]] = {}
    continuity: dict[tuple[str, str, str], list[str]] = {}
    for result in results:
        for row in result["items"]:
            item = plan_items[row["item_id"]]
            if (run_mode == "CHANGE_SET" and item.get("change_kind") in {"modified", "renamed"}
                    and isinstance(item.get("change_id"), str) and isinstance(item.get("baseline_source_id"), str)
                    and isinstance(item.get("current_source_id"), str)):
                key = (item["change_id"], item["baseline_source_id"], item["current_source_id"])
                continuity.setdefault(key, []).append(row["outcome"])
            for fragment in row.get("behavior_fragments", []):
                if run_mode == "FULL":
                    source_id, effect = item["source_id"], "full"
                else:
                    source_id, effect = (item["baseline_source_id"] if fragment["effect"] == "retired" else item["current_source_id"]), fragment["effect"]
                if not isinstance(source_id, str):
                    raise _fail("BEHAVIOR_RECEIPT", "/promotions", "Promoted semantic fragments require an authoritative source owner.")
                record = {"fragment_id": fragment["fragment_id"], "source_id": source_id, "item_id": item["item_id"], "effect": effect}
                registry_rows.append(record)
                requirement_id = _semantic_requirement_id(_semantic_tuple(fragment))
                if effect == "retired":
                    retired_claims.setdefault(requirement_id, []).append(_freeze(record))
                else:
                    claim = claims.setdefault(requirement_id, {"semantic": _semantic_tuple(fragment), "source_ids": set(), "fragment_ids": []})
                    claim["source_ids"].add(source_id); claim["fragment_ids"].append(fragment["fragment_id"])
    registry = {row["fragment_id"]: row for row in registry_rows}
    if len(registry) != len(registry_rows):
        raise _fail("BEHAVIOR_RECEIPT", "/fragment_registry", "Promoted fragment identities must be globally unique.")

    baseline_requirements = {row["requirement_id"]: row for row in baseline_managed["requirements"]} if baseline_managed else {}
    baseline_order = list(baseline_requirements)
    baseline_links = {row["requirement_id"]: tuple(row["source_ids"]) for row in baseline_managed["requirement_sources"]} if baseline_managed else {}
    unchanged_bindings = []
    baseline_by_id = {row["source_id"]: row for row in baseline_sources}
    for source in current_sources:
        old = baseline_by_id.get(source["source_id"])
        if old is not None and old.get("content_digest") == source.get("content_digest"):
            unchanged_bindings.append({"source_id": source["source_id"], "baseline_content_digest": old["content_digest"], "current_content_digest": source["content_digest"]})
    unchanged_source_ids = {row["source_id"] for row in unchanged_bindings}
    continued_sources = {
        before: after for (_, before, after), outcomes in continuity.items()
        if outcomes and all(outcome == "no_changed_observable_fact" for outcome in outcomes)
    }
    retired = _retirement_rows(baseline_requirements, baseline_links, set(claims), retired_claims)
    retired_ids = {row["object_id"] for row in retired}
    retained: dict[str, set[str]] = {key: set(value["source_ids"]) for key, value in claims.items()}
    for requirement_id, links in baseline_links.items():
        surviving = (set(links) & unchanged_source_ids) | {continued_sources[source_id] for source_id in links if source_id in continued_sources}
        if requirement_id in retired_ids:
            continue
        if not surviving and requirement_id not in claims:
            raise _fail("BEHAVIOR_RETIREMENT", "/promotions", "A baseline requirement lost all support without promoted retirement evidence.")
        retained.setdefault(requirement_id, set()).update(surviving)
    for requirement_id, evidence in claims.items():
        if requirement_id in baseline_requirements:
            probe = _semantic_requirement(evidence["semantic"], sorted(retained[requirement_id]), 1)
            baseline_plain = {key: _plain(value) for key, value in baseline_requirements[requirement_id].items() if key not in {"display_order", "provenance"}}
            probe_plain = {key: _plain(value) for key, value in probe.items() if key not in {"display_order", "provenance"}}
            if baseline_plain != probe_plain:
                raise _fail("BEHAVIOR_RECEIPT", "/promotions", "A promoted semantic tuple cannot reuse a non-identical baseline requirement ID.")
    ordered_ids = [value for value in baseline_order if value in retained] + sorted(value for value in retained if value not in baseline_requirements)
    requirements: list[Mapping[str, Any]] = []
    for index, requirement_id in enumerate(ordered_ids, 1):
        links = sorted(retained[requirement_id])
        if requirement_id in baseline_requirements:
            value = dict(_plain(baseline_requirements[requirement_id])); value["display_order"] = index; value["provenance"] = links
            requirements.append(_freeze(value))
        else:
            requirements.append(_semantic_requirement(claims[requirement_id]["semantic"], links, index))
    requirement_ids = [row["requirement_id"] for row in requirements]
    current_requirement_by_id = {row["requirement_id"]: row for row in requirements}
    current_links = {requirement_id: tuple(sorted(retained[requirement_id])) for requirement_id in requirement_ids}
    changed_ids = [value for value in requirement_ids if value not in baseline_requirements]
    stable_ids = [value for value in requirement_ids if value in baseline_requirements]
    groups = [
        {"group_id": "GROUP-" + hashlib.sha256(requirement_id.encode("utf-8")).hexdigest(), "fragment_ids": claims[requirement_id]["fragment_ids"], "requirement_ids": [requirement_id]}
        for requirement_id in requirement_ids if requirement_id in claims
    ]
    grouped_ids = [fragment_id for group in groups for fragment_id in group["fragment_ids"]]
    inverse = {row["source_id"]: [] for row in current_sources}
    for requirement_id in requirement_ids:
        for source_id in current_links[requirement_id]: inverse[source_id].append(requirement_id)
    product_ids = {source_id for source_id, links in inverse.items() if links and next(row for row in current_sources if row["source_id"] == source_id)["kind"] == "product_file"}
    managed = _freeze({
        "authorized_behavior_sources_sha256": _digest(_plain(authorized)), "requirements": list(requirements),
        "product_sources": [{"source_id": row["source_id"], "kind": "product_file", "path": row["path"], "content_digest": row["content_digest"], "summary": f"Behavior evidence from {row['path']}."} for row in current_sources if row["source_id"] in product_ids],
        "requirement_sources": [{"requirement_id": value, "source_ids": list(current_links[value])} for value in requirement_ids],
    })
    dispositions: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, str]] = []
    changed_fragment_sources = {row["source_id"] for row in registry_rows if row["effect"] in {"full", "added", "modified"}}
    for source in current_sources:
        source_id, linked = source["source_id"], inverse[source["source_id"]]
        if linked:
            dispositions.append({"source_id": source_id, "disposition": "represented", "requirement_ids": linked})
            outcome = "behavior_fragments" if run_mode == "FULL" else "changed_behavior_fragments" if source_id in changed_fragment_sources else "preserved_behavior"
        else:
            if source["kind"] == "supplied_requirement":
                raise _fail("BEHAVIOR_RECEIPT", "/promotions", "Supplied requirements require supported observable behavior.")
            dispositions.append({"source_id": source_id, "disposition": "no_supported_observable_fact", "reason": "no_supported_actor_operation_or_outcome_after_full_review"})
            outcome = "no_supported_observable_fact"
        outcome_rows.append({"source_id": source_id, "domain_key": derive_domain_key(source, module, project), "outcome": outcome})
    retirement_fragment_ids = [fragment_id for row in retired for fragment_id in row["support_evidence"]["retirement_fragment_ids"]]
    expected_current_fragments = {fragment_id for fragment_id, row in registry.items() if row["effect"] != "retired"}
    expected_retired_fragments = {fragment_id for fragment_id, row in registry.items() if row["effect"] == "retired"}
    if set(grouped_ids) != expected_current_fragments or len(retirement_fragment_ids) != len(set(retirement_fragment_ids)) or set(retirement_fragment_ids) != expected_retired_fragments:
        raise _fail("BEHAVIOR_ACCOUNTING_COVERAGE", "/promotions", "Every promoted fragment must be consumed exactly once by a current group or retirement tombstone.")
    if run_mode == "FULL" and (stable_ids or retired or len(grouped_ids) != len(registry)):
        raise _fail("BEHAVIOR_RECEIPT", "/promotions", "FULL context must project every promoted fact and every requirement as new full evidence.")
    changed_requirements = tuple(_freeze(_plain(current_requirement_by_id[value])) for value in changed_ids)
    changed_requirement_sources = tuple(_freeze({"requirement_id": value, "source_ids": list(current_links[value])}) for value in changed_ids)
    stable_links = tuple(_freeze({"requirement_id": value, "baseline_requirement_sha256": _digest(_plain(baseline_requirements[value])), "source_ids": list(current_links[value])}) for value in stable_ids)
    promotion_digests = [_digest(_plain(row)) for row in authorities]
    independent_count = sum(row["review_mode"] == "INDEPENDENT" for row in authorities)
    sequential_count = len(authorities) - independent_count
    receipt = _freeze({
        "schema_version": "2.0.0", "artifact": "behavior-context-receipt", "run_mode": run_mode,
        "selected_module": module["id"], "baseline_receipt_sha256": scope_receipt.get("baseline_receipt_sha256"),
        "scope_receipt_sha256": _digest(_plain(scope_receipt)), "authorized_behavior_sources_sha256": _digest(_plain(authorized)),
        "context_plan_sha256": _digest(_plain(plan)), "promotion_sha256s": promotion_digests,
        "unchanged_source_bindings": unchanged_bindings, "fragment_registry": registry_rows,
        "source_outcomes": outcome_rows, "changed_requirement_ids": changed_ids,
        "retired_requirements": list(retired),
        "assurance": {"review_mode": "INDEPENDENT" if authorities and independent_count == len(authorities) else "SEQUENTIAL", "independent_promotion_count": independent_count, "sequential_promotion_count": sequential_count, "reworked_batch_count": rework_count},
    })
    receipt_sha256 = _digest(_plain(receipt))
    accounting = _freeze({"authorized_behavior_sources_sha256": _digest(_plain(authorized)), "context_receipt_sha256": receipt_sha256, "source_dispositions": _plain(dispositions), "behavior_fragment_groups": _plain(groups)})
    changed = _freeze({
        "schema_version": "1.0.0", "artifact": "changed-behavior-context", "run_mode": run_mode,
        "source": {"behavior_context_receipt_sha256": receipt_sha256, "baseline_context_sha256": baseline_context_sha256},
        "requirements": list(changed_requirements), "requirement_sources": list(changed_requirement_sources),
        "stable_requirement_links": list(stable_links), "retired_requirements": list(retired),
    })
    context = _freeze({"schema_version": "6.0.0", "stage": "context-marker", "artifacts": {"managed_behavior_context": _plain(managed), "changed_behavior_context": _plain(changed), "behavior_source_accounting": _plain(accounting)}, "warnings": []})
    context_shape = schema_diagnostics(_plain(context), _ROOT / "schemas" / "context-marker-output.schema.json", _ROOT)
    receipt_shape = schema_diagnostics(_plain(receipt), _ROOT / "schemas" / "behavior-context-receipt.schema.json", _ROOT)
    if context_shape or receipt_shape:
        first = (context_shape or receipt_shape)[0]
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", first["path"], "Composed context does not satisfy its closed V6 schema.")
    result = object.__new__(ComposedBehaviorContext)
    _COMPOSED_CONTEXTS[result] = _freeze({"context": context, "receipt": receipt, "context_sha256": _digest(_plain(context)), "receipt_sha256": receipt_sha256})
    return result


def _validate_v6_context_envelope(
    context: Mapping[str, Any],
    receipt: Mapping[str, Any],
    authorized: Mapping[str, Any],
    test_inventory: Mapping[str, Any],
    project: Path,
    module: Mapping[str, Any],
    supplied_inputs: Mapping[str, bytes],
) -> ValidatedBehaviorContext:
    try:
        from tools.test_classification import validate_managed_behavior_context
    except ImportError:
        from test_classification import validate_managed_behavior_context
    context, receipt = _plain(context), _plain(receipt)
    if context.get("schema_version") != "6.0.0" or receipt.get("schema_version") != "2.0.0":
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/schema_version", "V6 context requires behavior receipt V2.")
    artifacts = context.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {"managed_behavior_context", "changed_behavior_context", "behavior_source_accounting"}:
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/artifacts", "V6 context requires the three closed context projections.")
    managed = artifacts["managed_behavior_context"]
    changed = artifacts["changed_behavior_context"]
    accounting = artifacts["behavior_source_accounting"]
    diagnostics = validate_managed_behavior_context(_plain(managed), authorized, test_inventory, project)
    if diagnostics:
        raise TestClassificationError(diagnostics)
    authorized_digest = _digest(_plain(authorized))
    receipt_sha256 = _digest(_plain(receipt))
    if (module.get("id") != authorized.get("module_id") or receipt.get("selected_module") != authorized.get("module_id")
            or receipt.get("authorized_behavior_sources_sha256") != authorized_digest
            or managed.get("authorized_behavior_sources_sha256") != authorized_digest
            or accounting.get("authorized_behavior_sources_sha256") != authorized_digest
            or accounting.get("context_receipt_sha256") != receipt_sha256
            or changed.get("source", {}).get("behavior_context_receipt_sha256") != receipt_sha256
            or changed.get("run_mode") != receipt.get("run_mode")):
        raise _fail("BEHAVIOR_ACCOUNTING_DIGEST", "/artifacts", "V6 context projections must bind the exact receipt and authorized sources.")
    if receipt["run_mode"] == "FULL":
        if receipt.get("baseline_receipt_sha256") is not None or changed["source"].get("baseline_context_sha256") is not None:
            raise _fail("BEHAVIOR_RECEIPT", "/baseline_receipt_sha256", "FULL context cannot bind a predecessor.")
    elif receipt.get("baseline_receipt_sha256") is None or changed["source"].get("baseline_context_sha256") is None:
        raise _fail("BEHAVIOR_RECEIPT", "/baseline_receipt_sha256", "CHANGE_SET context must bind a predecessor.")
    sources = authorized.get("sources")
    if not isinstance(sources, list):
        raise _fail("BEHAVIOR_RECEIPT", "/authorized_behavior_sources/sources", "Authorized sources must be an array.")
    supplied_ids = {row["source_id"] for row in sources if isinstance(row, Mapping) and row.get("kind") == "supplied_requirement"}
    if set(supplied_inputs) != supplied_ids:
        raise _fail("BEHAVIOR_SUPPLIED_INPUT", "/supplied-input", "Supplied inputs must cover the authorized supplied sources exactly.")
    for source in sources:
        _bytes(source, project, supplied_inputs)
    source_ids = [row["source_id"] for row in sources]
    requirement_ids = [row["requirement_id"] for row in managed["requirements"]]
    inverse = {source_id: [] for source_id in source_ids}
    for row in managed["requirement_sources"]:
        for source_id in row["source_ids"]:
            inverse[source_id].append(row["requirement_id"])
    dispositions = accounting.get("source_dispositions")
    outcomes = receipt.get("source_outcomes")
    if ([row.get("source_id") if isinstance(row, Mapping) else None for row in dispositions or ()] != source_ids
            or [row.get("source_id") if isinstance(row, Mapping) else None for row in outcomes or ()] != source_ids):
        raise _fail("BEHAVIOR_ACCOUNTING_ORDER", "/source_outcomes", "V6 dispositions and outcomes must cover current sources in inventory order.")
    product_ids = {row["source_id"] for row in managed["product_sources"]}
    for source, disposition, outcome in zip(sources, dispositions, outcomes):
        linked = inverse[source["source_id"]]
        if disposition.get("disposition") == "represented":
            if disposition.get("requirement_ids") != linked or not linked or outcome.get("outcome") not in {"behavior_fragments", "changed_behavior_fragments", "preserved_behavior"}:
                raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/source_dispositions", "Represented sources must retain their exact current requirement links and supported outcome.")
        elif disposition.get("disposition") == "no_supported_observable_fact":
            if linked or source.get("kind") == "supplied_requirement" or source["source_id"] in product_ids or outcome.get("outcome") != "no_supported_observable_fact":
                raise _fail("BEHAVIOR_ACCOUNTING_DISPOSITION", "/source_dispositions", "No-fact sources cannot retain managed behavior links.")
        else:
            raise _fail("BEHAVIOR_ACCOUNTING_DISPOSITION", "/source_dispositions", "Disposition uses an unsupported variant.")
    registry_rows = receipt.get("fragment_registry")
    registry = {row.get("fragment_id"): row for row in registry_rows or () if isinstance(row, Mapping)}
    if len(registry) != len(registry_rows or ()):
        raise _fail("BEHAVIOR_RECEIPT", "/fragment_registry", "V6 fragment identities must be unique.")
    grouped: list[str] = []
    for group in accounting.get("behavior_fragment_groups", ()):
        if group.get("requirement_ids") != [value for value in requirement_ids if value in group.get("requirement_ids", ())]:
            raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/behavior_fragment_groups", "Group requirement IDs must use managed context order.")
        for fragment_id in group.get("fragment_ids", ()):
            fragment = registry.get(fragment_id)
            if fragment is None or fragment.get("effect") == "retired" or not set(group["requirement_ids"]) <= set(inverse.get(fragment.get("source_id"), ())):
                raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/behavior_fragment_groups", "Groups must bind promoted current fragments to their supported requirements.")
        grouped.extend(group["fragment_ids"])
    changed_requirements = changed.get("requirements")
    changed_ids = [row.get("requirement_id") for row in changed_requirements or ()]
    changed_sources = changed.get("requirement_sources")
    stable_links = changed.get("stable_requirement_links")
    retired = changed.get("retired_requirements")
    if (receipt.get("changed_requirement_ids") != changed_ids
            or [row.get("requirement_id") for row in changed_sources or ()] != changed_ids
            or any(_plain(current) != _plain(next(row for row in managed["requirements"] if row["requirement_id"] == current["requirement_id"])) for current in changed_requirements)
            or any(row.get("source_ids") != list(next(item for item in managed["requirement_sources"] if item["requirement_id"] == row.get("requirement_id"))["source_ids"]) for row in changed_sources or ())):
        raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/changed_behavior_context", "Changed requirement bodies and source links must be byte-identical managed projections.")
    stable_ids = [row.get("requirement_id") for row in stable_links or ()]
    retired_ids = [row.get("object_id") for row in retired or ()]
    if len(set(changed_ids + stable_ids + retired_ids)) != len(changed_ids + stable_ids + retired_ids) or set(changed_ids + stable_ids) != set(requirement_ids):
        raise _fail("BEHAVIOR_ACCOUNTING_COVERAGE", "/changed_behavior_context", "Changed, stable, and retired requirement identities must be disjoint and complete.")
    if _plain(receipt.get("retired_requirements")) != _plain(retired):
        raise _fail("BEHAVIOR_RETIREMENT", "/retired_requirements", "Changed context and receipt tombstones must be byte-identical.")
    retirement_fragments = [fragment_id for row in retired or () for fragment_id in row["support_evidence"]["retirement_fragment_ids"]]
    expected_current = {key for key, row in registry.items() if row.get("effect") != "retired"}
    expected_retired = {key for key, row in registry.items() if row.get("effect") == "retired"}
    if len(grouped) != len(set(grouped)) or set(grouped) != expected_current or len(retirement_fragments) != len(set(retirement_fragments)) or set(retirement_fragments) != expected_retired:
        raise _fail("BEHAVIOR_ACCOUNTING_COVERAGE", "/fragment_registry", "Every promoted fragment must be consumed exactly once.")
    if receipt["run_mode"] == "FULL" and (stable_links or retired or changed_ids != requirement_ids):
        raise _fail("BEHAVIOR_ACCOUNTING_COVERAGE", "/changed_behavior_context", "FULL changed context must equal the complete managed requirement projection.")
    bindings = receipt.get("unchanged_source_bindings")
    binding_ids: set[str] = set()
    source_by_id = {row["source_id"]: row for row in sources}
    for row in bindings or ():
        source = source_by_id.get(row.get("source_id"))
        if source is None or row["source_id"] in binding_ids or row.get("baseline_content_digest") != row.get("current_content_digest") or row.get("current_content_digest") != source.get("content_digest"):
            raise _fail("BEHAVIOR_RECEIPT", "/unchanged_source_bindings", "Unchanged bindings must be unique exact current digest equalities.")
        binding_ids.add(row["source_id"])
    assurance = receipt.get("assurance")
    if not isinstance(assurance, Mapping) or assurance.get("independent_promotion_count", 0) + assurance.get("sequential_promotion_count", 0) != len(receipt.get("promotion_sha256s", ())):
        raise _fail("BEHAVIOR_RECEIPT", "/assurance", "Assurance counts must cover every promotion.")
    return ValidatedBehaviorContext(
        tuple(_freeze(_plain(row)) for row in managed["requirements"]), receipt_sha256, authorized_digest,
        tuple(_freeze(_plain(row)) for row in changed_requirements), tuple(_freeze(_plain(row)) for row in retired),
        tuple(_freeze(_plain(row)) for row in stable_links),
    )


def validate_composed_behavior_context(
    composed: ComposedBehaviorContext, authorized: Mapping[str, Any], test_inventory: Mapping[str, Any],
    project: Path, module: Mapping[str, Any], supplied_inputs: Mapping[str, bytes],
) -> ValidatedBehaviorContext:
    """Validate an in-process V6 capability; persisted JSON is not V6 authority."""
    if not isinstance(composed, ComposedBehaviorContext) or composed not in _COMPOSED_CONTEXTS:
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/context", "V6 validation requires a composition-minted capability.")
    record = _COMPOSED_CONTEXTS[composed]
    context, receipt = record["context"], record["receipt"]
    if record["context_sha256"] != _digest(_plain(context)) or record["receipt_sha256"] != _digest(_plain(receipt)):
        raise _fail("BEHAVIOR_ACCOUNTING_DIGEST", "/context", "Composition capability no longer binds exact context and receipt bytes.")
    return _validate_v6_context_envelope(context, receipt, authorized, test_inventory, project, module, supplied_inputs)


def _validate_context_envelope(context: Mapping[str, Any], receipt: Mapping[str, Any], authorized: Mapping[str, Any], test_inventory: Mapping[str, Any], project: Path, module: Mapping[str, Any], supplied_inputs: Mapping[str, bytes]) -> ValidatedBehaviorContext:
    try:
        from tools.test_classification import validate_managed_behavior_context
    except ImportError:
        from test_classification import validate_managed_behavior_context
    if not isinstance(context, Mapping) or not isinstance(receipt, Mapping) or not isinstance(authorized, Mapping) or not isinstance(test_inventory, Mapping):
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/input", "Context validation inputs must be objects.")
    context_shape = schema_diagnostics(_plain(context), _ROOT / "schemas" / "context-marker-output.schema.json", _ROOT)
    if context_shape: raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", context_shape[0]["path"], "Context does not satisfy its closed V5 schema.")
    receipt_shape = schema_diagnostics(_plain(receipt), _ROOT / "schemas" / "behavior-context-receipt.schema.json", _ROOT)
    if receipt_shape: raise _fail("BEHAVIOR_RECEIPT", receipt_shape[0]["path"], "Receipt does not satisfy its closed schema.")
    if context.get("schema_version") == "6.0.0" or receipt.get("schema_version") == "2.0.0":
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/schema_version", "Loose V6 JSON is not authority; replay promotion evidence before validation.")
    if context.get("schema_version") != "5.0.0" or context.get("stage") != "context-marker": raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/schema_version", "Context must be the V5 context-marker envelope.")
    artifacts = context.get("artifacts")
    if not isinstance(artifacts, Mapping) or set(artifacts) != {"managed_behavior_context", "behavior_source_accounting"}: raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/artifacts", "V5 context requires closed sibling artifacts.")
    managed, accounting = artifacts["managed_behavior_context"], artifacts["behavior_source_accounting"]
    diagnostics = validate_managed_behavior_context(managed, authorized, test_inventory, project)
    if diagnostics: raise TestClassificationError(diagnostics)
    if not isinstance(accounting, Mapping) or set(accounting) != {"authorized_behavior_sources_sha256", "context_receipt_sha256", "source_dispositions", "behavior_fragment_groups"}: raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/artifacts/behavior_source_accounting", "Accounting sidecar must use closed V5 fields.")
    if accounting["authorized_behavior_sources_sha256"] != _digest(_plain(authorized)) or receipt.get("authorized_behavior_sources_sha256") != _digest(_plain(authorized)): raise _fail("BEHAVIOR_ACCOUNTING_DIGEST", "/artifacts/behavior_source_accounting", "Accounting and receipt must bind the authorized snapshot.")
    if accounting["context_receipt_sha256"] != _digest(_plain(receipt)): raise _fail("BEHAVIOR_ACCOUNTING_DIGEST", "/artifacts/behavior_source_accounting/context_receipt_sha256", "Accounting must bind the exact receipt.")
    sources = authorized.get("sources")
    if not isinstance(sources, list) or receipt.get("selected_module") != authorized.get("module_id"):
        raise _fail("BEHAVIOR_RECEIPT", "/selected_module", "Receipt must bind the selected authorized module.")
    ids = [row["source_id"] for row in sources if isinstance(row, Mapping) and isinstance(row.get("source_id"), str)]
    if len(ids) != len(sources): raise _fail("BEHAVIOR_RECEIPT", "/sources", "Authorized sources are malformed.")
    reqs = managed["requirements"]; req_ids = [row["requirement_id"] for row in reqs]
    dispositions = accounting["source_dispositions"]
    if not isinstance(dispositions, list) or [row.get("source_id") if isinstance(row, Mapping) else None for row in dispositions] != ids: raise _fail("BEHAVIOR_ACCOUNTING_ORDER", "/artifacts/behavior_source_accounting/source_dispositions", "Dispositions must cover authorized sources exactly in inventory order.")
    inverse = {source_id: [] for source_id in ids}
    for row in managed["requirement_sources"]:
        for source_id in row["source_ids"]: inverse[source_id].append(row["requirement_id"])
    outcomes = receipt.get("source_outcomes")
    if not isinstance(outcomes, list) or [row.get("source_id") for row in outcomes] != ids: raise _fail("BEHAVIOR_RECEIPT", "/source_outcomes", "Receipt outcomes must cover sources in inventory order.")
    registry = receipt.get("fragment_registry")
    groups = accounting.get("behavior_fragment_groups")
    if not isinstance(registry, list) or not isinstance(groups, list):
        raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/artifacts/behavior_source_accounting", "Receipt registry and fragment groups must be arrays.")
    registry_by_id = {row.get("fragment_id"): row for row in registry if isinstance(row, Mapping)}
    registry_ids = [row.get("fragment_id") for row in registry if isinstance(row, Mapping)]
    if len(registry_by_id) != len(registry) or any(not isinstance(value, str) for value in registry_ids):
        raise _fail("BEHAVIOR_RECEIPT", "/fragment_registry", "Receipt fragments must have unique stable IDs.")
    outcome_by_id = {row.get("source_id"): row for row in outcomes if isinstance(row, Mapping)}
    if len(outcome_by_id) != len(ids): raise _fail("BEHAVIOR_RECEIPT", "/source_outcomes", "Receipt outcomes must be unique closed source rows.")
    source_by_id = {row["source_id"]: row for row in sources}
    # The receipt gate owns current-byte verification for *every* authorized
    # product source, including an excluded no-fact source omitted from managed
    # product_sources.
    for source in sources:
        if source.get("kind") == "product_file":
            _bytes(source, project, {})
    if not isinstance(module, Mapping) or module.get("id") != authorized.get("module_id"):
        raise _fail("BEHAVIOR_RECEIPT", "/module", "Selected module must equal the authorized inventory module.")
    domain_module = module
    for source_id, source in source_by_id.items():
        if outcome_by_id[source_id].get("domain_key") != derive_domain_key(source, domain_module, project):
            raise _fail("BEHAVIOR_RECEIPT", "/source_outcomes", "Receipt domain keys must be mechanically recomputed.")
    for fragment in registry:
        if fragment.get("source_id") not in source_by_id or not isinstance(fragment.get("item_id"), str) or outcome_by_id[fragment["source_id"]].get("outcome") != "behavior_fragments":
            raise _fail("BEHAVIOR_RECEIPT", "/fragment_registry", "Receipt fragments must have authorized source ownership and represented outcomes.")
    plan = build_context_plan(project, module, authorized, supplied_inputs)
    if receipt.get("context_plan_sha256") != _digest(_plain(plan)):
        raise _fail("BEHAVIOR_RECEIPT", "/context_plan_sha256", "Receipt must bind the exact closed selected-module plan.")
    plan_items = {item["item_id"]: item for batch in plan["batches"] for item in batch["items"]}
    for fragment in registry:
        item = plan_items.get(fragment["item_id"])
        if item is None or item["source_id"] != fragment["source_id"]:
            raise _fail("BEHAVIOR_RECEIPT", "/fragment_registry", "Receipt fragment ownership must equal the mechanically bound plan item.")
    grouped_ids: list[str] = []
    group_requirements: dict[str, set[str]] = {source_id: set() for source_id in ids}
    for group_index, group in enumerate(groups):
        if not isinstance(group, Mapping) or set(group) != {"group_id", "fragment_ids", "requirement_ids"}:
            raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", f"/artifacts/behavior_source_accounting/behavior_fragment_groups/{group_index}", "Fragment groups must use closed fields.")
        fragments, linked_requirements = group.get("fragment_ids"), group.get("requirement_ids")
        if not isinstance(fragments, list) or not fragments or len(fragments) != len(set(fragments)) or not isinstance(linked_requirements, list) or not linked_requirements or linked_requirements != [item for item in req_ids if item in linked_requirements]:
            raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/artifacts/behavior_source_accounting/behavior_fragment_groups", "Groups require unique receipt fragments and canonical requirement IDs.")
        grouped_ids.extend(fragments)
        for fragment_id in fragments:
            fragment = registry_by_id.get(fragment_id)
            if fragment is None:
                raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/artifacts/behavior_source_accounting/behavior_fragment_groups", "Groups cannot reference foreign receipt fragments.")
            group_requirements[fragment["source_id"]].update(linked_requirements)
    if set(grouped_ids) != set(registry_ids) or len(grouped_ids) != len(set(grouped_ids)) or len(grouped_ids) != len(registry_ids):
        raise _fail("BEHAVIOR_ACCOUNTING_COVERAGE", "/artifacts/behavior_source_accounting/behavior_fragment_groups", "Every receipt fragment must appear exactly once.")
    for source, row, outcome in zip(sources, dispositions, outcomes):
        if not isinstance(row, Mapping): raise _fail("BEHAVIOR_ACCOUNTING_SHAPE", "/source_dispositions", "Disposition rows must be objects.")
        linked = [item for item in req_ids if item in inverse[source["source_id"]]]
        if row.get("disposition") == "represented":
            if set(row) != {"source_id", "disposition", "requirement_ids"} or row.get("requirement_ids") != linked or set(linked) != group_requirements[source["source_id"]] or not linked or outcome.get("outcome") != "behavior_fragments": raise _fail("BEHAVIOR_ACCOUNTING_LINK", "/source_dispositions", "Represented source links must equal fragment-group and inverse requirement links.")
        elif row.get("disposition") == "no_supported_observable_fact":
            if set(row) != {"source_id", "disposition", "reason"} or linked or group_requirements[source["source_id"]] or any(item.get("source_id") == source["source_id"] for item in registry) or source["kind"] == "supplied_requirement" or source["source_id"] in {item["source_id"] for item in managed["product_sources"]} or outcome.get("outcome") != "no_supported_observable_fact": raise _fail("BEHAVIOR_ACCOUNTING_DISPOSITION", "/source_dispositions", "Only fully no-fact product sources may be excluded.")
        else: raise _fail("BEHAVIOR_ACCOUNTING_DISPOSITION", "/source_dispositions", "Disposition must use a closed V5 variant.")
    return ValidatedBehaviorContext(tuple(_freeze(_plain(row)) for row in reqs), accounting["context_receipt_sha256"], accounting["authorized_behavior_sources_sha256"])
