"""Closed, deterministic behavior-context batch planning and receipt validation.

This module never imports project code.  It reads only files already named by the
immutable V1 source inventory and keeps controller-supplied input paths out of
all emitted artifacts.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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


def _fail(code: str, path: str, message: str) -> TestClassificationError:
    return TestClassificationError((_diag(path, code, message),))


def _bytes(source: Mapping[str, Any], project: Path, supplied: Mapping[str, bytes]) -> bytes:
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
        value = resolved.read_bytes()
    except (OSError, TestClassificationError) as error:
        if isinstance(error, TestClassificationError):
            raise
        raise _fail("BEHAVIOR_PLAN", "/sources", "Authorized product file could not be read.") from error
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


def _sources(inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    if inventory.get("stage") == "source-inventory":
        artifacts = inventory.get("artifacts")
        if isinstance(artifacts, Mapping):
            return artifacts.get("authorized_behavior_sources", {})
    return inventory


def build_context_plan(project: Path, module: Mapping[str, Any], inventory: Mapping[str, Any], supplied_inputs: Mapping[str, bytes] = {}) -> Mapping[str, Any]:
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
        data = _bytes(source, project, supplied_inputs)
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
