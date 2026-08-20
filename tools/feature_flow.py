"""Small, resumable Pipeline 6 semantic-prefix facade.

This module deliberately stops before the existing reviewer/automation/trace tail.
It only turns reviewed semantic evidence into the one closed handoff that the tail
can consume; it does not pretend that a generator result was reviewed or published.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

from tools.baseline_lifecycle import (
    bind_effective_baseline, bind_scope_predecessor, choose_run_mode,
    effective_baseline_projection, validate_baseline_receipt,
)
from tools.batch_promotion import PromotionEvidence, ReviewController, advance_promotion, record_promotion, start_promotion
from tools.behavior_context_planning import (
    build_change_context_plan, build_context_plan, compose_behavior_context,
)
from tools.change_scope import ScopeInputs, advance_scope, record_scope, start_scope
from tools.document_delta import apply_document_delta, delta_application_receipt, unchanged_document_selection
from tools.flow_artifacts import FlowError, artifact_sha256, canonical_bytes, write_create_only
from tools.git_change_adapter import BlobResolver, ChangeInputSpec, acquire_change_input
from tools.init_skillsrc import ensure_skillsrc
from tools.publish_test_case_bundle import Receipt
from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics
from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc, resolve_module_root, select_module
from tools.test_classification import build_source_inventories


_ROOT = Path(__file__).resolve().parents[1]
_KINDS = frozenset({
    "RUN_FULL_BASELINE", "PRODUCE_CHANGE_SCOPE", "RUN_SCOPE_FALSE_INCLUSION_AUDIT",
    "RUN_SCOPE_OMISSION_AUDIT", "PRODUCE_BATCH_CANDIDATE", "RUN_BATCH_FALSE_CLAIM_AUDIT",
    "RUN_BATCH_OMISSION_AUDIT", "PROMOTE_BATCH", "BUILD_CONTEXT", "GENERATE_CHANGED_BEHAVIOR",
    "COMPLETE", "BLOCKED",
})


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _diag(code: str, path: str, message: str) -> Mapping[str, str]:
    return _freeze({"path": path, "code": code, "message": message})


@dataclass(frozen=True)
class FeatureFlowAction:
    """The entire public control surface; all values are safe readback bindings."""

    kind: str
    artifact: Mapping[str, Any] | None
    record_path: Path | None
    diagnostics: tuple[Mapping[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in _KINDS or (self.artifact is not None and not isinstance(self.artifact, Mapping)) or not all(isinstance(row, Mapping) for row in self.diagnostics):
            raise ValueError("FeatureFlowAction must use the closed public shape.")
        if self.artifact is not None:
            object.__setattr__(self, "artifact", _freeze(_plain(self.artifact)))
        object.__setattr__(self, "diagnostics", tuple(_freeze(dict(row)) for row in self.diagnostics))


def _blocked(error: Exception) -> FeatureFlowAction:
    if isinstance(error, FlowError):
        diagnostic = _diag(error.code, error.path, error.message)
    else:
        diagnostic = _diag("FEATURE_FLOW_INPUT", "/input", "Feature flow input could not be validated.")
    return FeatureFlowAction("BLOCKED", None, None, (diagnostic,))


def _read_json(path: Path, code: str, pointer: str) -> Mapping[str, Any]:
    try:
        value = loads_json_strict(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, StrictJsonError):
        raise FlowError(code, pointer, "Artifact could not be read as strict UTF-8 JSON.") from None
    if not isinstance(value, Mapping):
        raise FlowError(code, pointer, "Artifact must be a JSON object.")
    return value


def _safe_digest(path: Path, code: str, pointer: str) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise FlowError(code, pointer, "Input could not be read.") from None


def _artifact(run_root: Path, value: Mapping[str, Any]) -> Path:
    digest = artifact_sha256(_plain(value))
    return write_create_only(run_root, PurePosixPath("artifacts") / f"{digest[7:]}.json", _plain(value)).path


def _binding(run_root: Path, value: Mapping[str, Any]) -> Mapping[str, str]:
    path = _artifact(run_root, value)
    return _freeze({"path": path.relative_to(run_root.resolve()).as_posix(), "sha256": artifact_sha256(_plain(value))})


def _action_payload(run_root: Path, kind: str, prompt: Mapping[str, Any], prerequisites: Mapping[str, Mapping[str, str]] | None = None) -> Mapping[str, Any]:
    """Expose only a bounded prompt plus exact persisted bindings to the caller."""
    return _freeze({
        "run_mode": prompt.get("run_mode"), "action": kind, "artifact": _binding(run_root, prompt),
        "prerequisites": {} if prerequisites is None else _plain(prerequisites), "prompt": _plain(prompt),
    })


def _stored(root: Path, digest: str, pointer: str) -> Mapping[str, Any]:
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise FlowError("BASELINE_BINDING", pointer, "Artifact digest is invalid.")
    path = root / "payloads" / "sha256" / f"{digest[7:]}.json"
    value = _read_json(path, "BASELINE_BINDING", pointer)
    if canonical_bytes(value) != path.read_bytes() or artifact_sha256(value) != digest:
        raise FlowError("BASELINE_BINDING", pointer, "Stored artifact does not bind its canonical digest.")
    return value


def _git(project: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=project, capture_output=True, check=False)
    except OSError:
        raise FlowError("FEATURE_FLOW_INPUT", "/project", "Git proof is unavailable.") from None
    if result.returncode:
        raise FlowError("FEATURE_FLOW_INPUT", "/project", "Git proof could not be established.")
    try:
        return result.stdout.decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        raise FlowError("FEATURE_FLOW_INPUT", "/project", "Git proof is not canonical text.") from None


def _git_provenance(project: Path) -> Mapping[str, Any] | None:
    """Return the complete Git identity, or ``None`` for a local project only."""
    try:
        probe = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=project, capture_output=True, check=False)
    except OSError:
        return None
    if probe.returncode:
        if b"not a git repository" in probe.stderr.lower():
            return None
        raise FlowError("FEATURE_FLOW_INPUT", "/project", "Git proof could not be established.")
    if probe.stdout.strip() != b"true":
        return None
    return {
        "repository_id": _repository_id(project),
        "commit": _git(project, "rev-parse", "HEAD^{commit}"),
        "tree": _git(project, "rev-parse", "HEAD^{tree}"),
        "status": _git(project, "status", "--porcelain=v1", "--untracked-files=all"),
    }


def _git_blob_reader(project: Path, commit: str):
    def read(portable_path: str) -> bytes:
        try:
            result = subprocess.run(["git", "show", f"{commit}:{portable_path}"], cwd=project, capture_output=True, check=False)
        except OSError:
            raise FlowError("FEATURE_FLOW_INPUT", "/project", "Git snapshot bytes are unavailable.") from None
        if result.returncode:
            raise FlowError("FEATURE_FLOW_INPUT", "/inventory", "Git snapshot does not contain an enumerated project file.")
        return result.stdout
    return read


def _inventory_snapshot_reader(project: Path, mode: str, change: Mapping[str, Any] | None, source_revision: Mapping[str, Any] | None):
    if mode == "FULL" and isinstance(source_revision, Mapping) and source_revision.get("durability") == "DURABLE":
        return _git_blob_reader(project, str(source_revision["commit"]))
    if mode == "CHANGE_SET" and isinstance(change, Mapping) and change.get("input_kind") == "git_range":
        target = change.get("target")
        if not isinstance(target, Mapping) or _git(project, "rev-parse", "HEAD^{commit}") != target.get("commit") or _git(project, "rev-parse", "HEAD^{tree}") != target.get("tree"):
            raise FlowError("FEATURE_FLOW_INPUT", "/change_input/target", "Git range target must equal the checked-out revision for inventory enumeration.")
        if _git(project, "status", "--porcelain=v1", "--untracked-files=all"):
            raise FlowError("FEATURE_FLOW_INPUT", "/project", "Git range inventory requires a clean target checkout.")
        return _git_blob_reader(project, str(target["commit"]))
    return None


def _full_provenance(provenance: Mapping[str, Any], bootstrap_status: str, change: Mapping[str, Any] | None, existing: Mapping[str, Any] | None, skillsrc_sha256: str) -> Mapping[str, Any]:
    """Freeze the exact revision inventoried by a FULL run and its durability."""
    status = provenance["status"]
    dirty_paths = tuple(line.split(maxsplit=1)[-1] for line in status.splitlines() if line.split(maxsplit=1))
    worktree = isinstance(change, Mapping) and change.get("input_kind") in {"git_worktree", "patch_manifest"}
    skillsrc_only = bool(dirty_paths) and all(path == ".skillsrc" for path in dirty_paths)
    if not dirty_paths:
        durability = "DURABLE"
    elif worktree or skillsrc_only:
        durability = "PROVISIONAL"
    else:
        raise FlowError("FEATURE_FLOW_INPUT", "/project", "A FULL run requires committed sources unless an explicit worktree snapshot is supplied.")
    return {
        "commit": provenance["commit"],
        "tree": provenance["tree"],
        "skillsrc_sha256": skillsrc_sha256,
        "durability": durability,
        "baseline_eligible": durability == "DURABLE",
    }


def _repository_id(project: Path) -> str:
    """Use the same opaque repository identity as the change/baseline modules."""
    common = Path(_git(project, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    return "sha256:" + hashlib.sha256(str(common).encode("utf-8")).hexdigest()


def _module(project: Path, changed_paths: tuple[str, ...], module_id: str | None) -> tuple[Mapping[str, Any], str]:
    skillsrc = project / ".skillsrc"
    bootstrap_status = "unchanged"
    if not skillsrc.exists():
        try:
            bootstrap = ensure_skillsrc(project, {}, write=True)
        except (OSError, TypeError, ValueError):
            raise FlowError("FEATURE_FLOW_INPUT", "/.skillsrc", "Project skill bootstrap could not be completed.") from None
        if (
            not isinstance(bootstrap, Mapping)
            or schema_diagnostics(_plain(bootstrap), _ROOT / "schemas" / "skillsrc-init-output.schema.json", _ROOT)
            or bootstrap.get("status") not in {"created", "updated", "unchanged"}
        ):
            raise FlowError("FEATURE_FLOW_INPUT", "/.skillsrc", "Project skill bootstrap requires safe user input or resolution.")
        bootstrap_status = str(bootstrap["status"])
    try:
        normalized = normalize_skillsrc(load_skillsrc(skillsrc))
    except (OSError, SkillsrcError):
        raise FlowError("FEATURE_FLOW_INPUT", "/.skillsrc", "A valid project .skillsrc is required.") from None
    modules = list(normalized["modules"])
    if module_id is not None:
        if not isinstance(module_id, str) or not module_id:
            raise FlowError("FEATURE_FLOW_INPUT", "/module", "Module selection must name one exact configured module.")
        selected_id = module_id
    elif not changed_paths:
        if len(modules) != 1:
            raise FlowError("FEATURE_FLOW_INPUT", "/module", "Exactly one module must be selected.")
        selected_id: str | None = None
    else:
        selected_rows = []
        for row in modules:
            root = str(row.get("root", "")).strip("/")
            root = "" if root == "." else root
            prefix = "" if not root else root.rstrip("/") + "/"
            if all(path == root or path.startswith(prefix) for path in changed_paths):
                selected_rows.append(row)
        if len(selected_rows) != 1:
            raise FlowError("FEATURE_FLOW_INPUT", "/module", "Changed paths do not select exactly one module.")
        selected_id = str(selected_rows[0]["id"])
    try:
        selected = dict(select_module(normalized, selected_id))
        resolve_module_root(project.resolve(), selected)
    except SkillsrcError:
        raise FlowError("FEATURE_FLOW_INPUT", "/module", "Selected module root is unsafe.") from None
    root = str(selected.get("root", "")).strip("/")
    root = "" if root == "." else root
    prefix = "" if not root else root + "/"
    if changed_paths and not all(path == root or path.startswith(prefix) for path in changed_paths):
        raise FlowError("FEATURE_FLOW_INPUT", "/module", "Selected module does not contain every changed path.")
    return selected, bootstrap_status


def _baseline_root(receipt_path: Path) -> Path:
    parent = receipt_path.resolve().parent
    return parent.parent if parent.name == "receipts" else parent


def _current_fingerprint_projection() -> Mapping[str, str]:
    """Use live Pipeline 6 bytes, never the receipt's self-attested digests."""
    from tools.contract_check import materialize_fingerprint_registries
    try:
        contract = json.loads((_ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))
        registries = materialize_fingerprint_registries(_ROOT, contract)
        return {name + "_sha256": row["sha256"] for name, row in registries.items()}
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        raise FlowError("BASELINE_FINGERPRINT", "/contracts/pipeline.json", "Current Pipeline 6 fingerprints are unavailable.") from None


def _baseline(project: Path, receipt_path: Path, module: Mapping[str, Any]) -> tuple[Any, Any, Any, Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    receipt = _read_json(receipt_path, "BASELINE_MISSING", "/baseline_receipt")
    baseline = validate_baseline_receipt(receipt, project, str(module["id"]), _current_fingerprint_projection())
    root = _baseline_root(receipt_path)
    source = _stored(root, baseline.receipt["artifacts"]["authorized_behavior_sources_sha256"], "/baseline/sources")
    context = _stored(root, baseline.receipt["artifacts"]["managed_behavior_context_sha256"], "/baseline/context")
    behavior_receipt = _stored(root, baseline.receipt["artifacts"]["behavior_context_receipt_sha256"], "/baseline/behavior_receipt")
    predecessor = bind_scope_predecessor(baseline, source, context, behavior_receipt)
    document = _stored(root, baseline.document_sha256, "/baseline/effective_document")
    bundle = _stored(root, baseline.bundle_sha256, "/baseline/effective_bundle_receipt")
    try:
        effective = bind_effective_baseline(baseline, document, Receipt(**bundle))
    except (TypeError, ValueError):
        raise FlowError("BASELINE_BINDING", "/baseline/effective_document", "Baseline effective document does not bind its receipt.") from None
    return baseline, predecessor, effective, source, context, behavior_receipt


def _require_materialized_patch(project: Path, change: Mapping[str, Any], base: str) -> None:
    base_commit, base_tree = _git(project, "rev-parse", f"{base}^{{commit}}"), _git(project, "rev-parse", f"{base}^{{tree}}")
    if _git(project, "rev-parse", "HEAD^{commit}") != base_commit or _git(project, "rev-parse", "HEAD^{tree}") != base_tree:
        raise FlowError("CHANGE_SOURCE_DRIFT", "/patch_manifest", "Current checkout is not at the supplied patch base.")
    checkout = acquire_change_input(project, ChangeInputSpec(base, None, True, None))
    checkout_base, patch_base = checkout.get("base"), change.get("base")
    if (
        checkout.get("repository_id") != change.get("repository_id")
        or not isinstance(checkout_base, Mapping)
        or not isinstance(patch_base, Mapping)
        or checkout_base.get("snapshot_sha256") != patch_base.get("snapshot_sha256")
        or _plain(checkout.get("changes")) != _plain(change.get("changes"))
    ):
        raise FlowError("CHANGE_SOURCE_DRIFT", "/patch_manifest", "Current checkout does not exactly materialize the supplied patch target.")


def _manifest(run_root: Path, value: Mapping[str, Any]) -> None:
    """Freeze the input in the same ordered prefix namespace as resume records."""
    path = run_root / "feature-flow" / "prefix" / "000000-flow-input.json"
    if path.exists():
        existing = _read_json(path, "FLOW_CONFLICT", "/run_root")
        if _plain(existing) != _plain(value):
            if existing.get("run_mode") == value.get("run_mode") == "FULL" and (
                existing.get("source_revision") != value.get("source_revision")
                or existing.get("source_snapshot_sha256") != value.get("source_snapshot_sha256")
            ):
                raise FlowError("CHANGE_SOURCE_DRIFT", "/project", "The frozen FULL source revision has changed.")
            if existing.get("change_input_sha256") and value.get("change_input_sha256") and existing.get("run_mode") == value.get("run_mode") == "FULL":
                raise FlowError("CHANGE_SOURCE_DRIFT", "/change_input", "Frozen provisional change input has changed.")
            raise FlowError("FLOW_CONFLICT", "/run_root", "Run root already belongs to a different feature flow.")
        return
    write_create_only(run_root, PurePosixPath("feature-flow") / "prefix" / "000000-flow-input.json", value)


_RECORD_NAME = re.compile(r"^(?P<ordinal>[0-9]{6})-(?P<artifact>[a-z0-9-]+)\.json$")


def _records(run_root: Path) -> list[Mapping[str, Any]]:
    directory = run_root / "feature-flow" / "prefix"
    if not directory.exists():
        return []
    result: list[Mapping[str, Any]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") and path.name.endswith(".tmp"):
            continue
        if path.name == "000000-flow-input.json":
            continue
        match = _RECORD_NAME.match(path.name)
        if not path.is_file() or match is None or match.group("ordinal") == "000000":
            raise FlowError("FEATURE_FLOW_INPUT", "/feature-flow/prefix", "Run records must use contiguous canonical paths.")
        result.append(_read_json(path, "FEATURE_FLOW_INPUT", "/records"))
    for index, value in enumerate(result, 1):
        matches = list(directory.glob(f"{index:06d}-*.json"))
        if len(matches) != 1 or canonical_bytes(value) != matches[0].read_bytes():
            raise FlowError("FEATURE_FLOW_INPUT", "/feature-flow/prefix", "Run records are malformed, branched, or out of order.")
    return result


def _record_bindings(run_root: Path) -> tuple[Mapping[str, Any], ...]:
    """Bind the authoritative ordered prefix files and mirror each in content storage."""
    directory = run_root / "feature-flow" / "prefix"
    rows: list[Mapping[str, Any]] = []
    if not directory.exists():
        return ()
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        match = _RECORD_NAME.match(path.name)
        if match is None or match.group("ordinal") == "000000":
            continue
        value = _read_json(path, "FEATURE_FLOW_INPUT", "/feature-flow/prefix")
        _artifact(run_root, value)
        rows.append(_freeze({
            "index": int(match.group("ordinal")), "kind": match.group("artifact"),
            "path": path.relative_to(run_root.resolve()).as_posix(), "sha256": artifact_sha256(value),
        }))
    return tuple(rows)


def _record_values(run_root: Path, kind: str) -> tuple[Mapping[str, Any], ...]:
    directory = run_root / "feature-flow" / "prefix"
    return tuple(
        _read_json(path, "FEATURE_FLOW_INPUT", "/feature-flow/prefix")
        for path in sorted(directory.glob(f"*-{kind}.json"), key=lambda item: item.name)
    )


def _append_record(run_root: Path, records: list[Mapping[str, Any]], value: Mapping[str, Any], label: str) -> Path:
    _artifact(run_root, value)
    return write_create_only(run_root, PurePosixPath("feature-flow") / "prefix" / f"{len(records) + 1:06d}-{label}.json", _plain(value)).path


def _recorded(expected: Path, supplied: Path | None) -> Mapping[str, Any]:
    if supplied is None or supplied.resolve() != expected.resolve():
        raise FlowError("FEATURE_FLOW_INPUT", "/record", "Record must use the exact canonical destination for the current action.")
    return _read_json(expected, "FEATURE_FLOW_INPUT", "/record")


def _record_label(kind: str) -> str:
    return kind.lower().replace("_", "-")


def _record_path(run_root: Path, index: int, kind: str) -> Path:
    directory = run_root / "feature-flow" / "prefix"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{index:06d}-{_record_label(kind)}.json"


def _read_binding(run_root: Path, binding: Any, pointer: str) -> Mapping[str, Any]:
    if not isinstance(binding, Mapping) or set(binding) != {"path", "sha256"} or not isinstance(binding.get("path"), str):
        raise FlowError("FEATURE_FLOW_EVIDENCE", pointer, "A closed immutable artifact binding is required.")
    relative = PurePosixPath(binding["path"])
    if relative.is_absolute() or ".." in relative.parts or "\\" in binding["path"]:
        raise FlowError("FEATURE_FLOW_EVIDENCE", pointer, "Artifact binding path is unsafe.")
    try:
        root = run_root.resolve(strict=True)
        path = (root / Path(*relative.parts)).resolve(strict=True)
        path.relative_to(root)
        value = _read_json(path, "FEATURE_FLOW_EVIDENCE", pointer)
        raw = path.read_bytes()
    except (OSError, ValueError):
        raise FlowError("FEATURE_FLOW_EVIDENCE", pointer, "Bound artifact is unavailable.") from None
    if raw != canonical_bytes(value) or artifact_sha256(value) != binding.get("sha256"):
        raise FlowError("FEATURE_FLOW_EVIDENCE", pointer, "Bound artifact does not match its digest.")
    return value


def _change_side_source(change: Mapping[str, Any] | None, change_id: Any, side: str) -> Mapping[str, Any] | None:
    """Return one frozen change side with its portable path restored."""
    if not isinstance(change, Mapping) or not isinstance(change_id, str):
        return None
    rows = [row for row in change.get("changes", ()) if isinstance(row, Mapping) and row.get("change_id") == change_id]
    if len(rows) != 1 or not isinstance(rows[0].get(side), Mapping):
        return None
    row, source = rows[0], rows[0][side]
    path = row.get("old_path" if side == "before" and row.get("kind") == "renamed" else "new_path" if side == "after" and row.get("kind") == "renamed" else "path")
    if not isinstance(path, str) or not isinstance(source.get("source_id"), str) or not isinstance(source.get("content_sha256"), str):
        return None
    return {"path": path, "source_id": source["source_id"], "content_digest": source["content_sha256"]}


def _batch_prompt(plan: Mapping[str, Any], prompt: Mapping[str, Any], inventory: Mapping[str, Any], change: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Attach the selected closed item projection without changing record authority."""
    batch_id = prompt.get("batch_id")
    batch = next((row for row in plan.get("batches", ()) if isinstance(row, Mapping) and row.get("batch_id") == batch_id), None)
    if not isinstance(batch, Mapping):
        raise FlowError("FEATURE_FLOW_EVIDENCE", "/plan", "Batch action does not name an exact planned batch.")
    sources = inventory.get("artifacts", {}).get("authorized_behavior_sources", {}).get("sources", ())
    by_id = {row.get("source_id"): row for row in sources if isinstance(row, Mapping)}
    items: list[Mapping[str, Any]] = []
    for item in batch.get("items", ()):
        if not isinstance(item, Mapping):
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/plan", "Batch item is invalid.")
        row = dict(_plain(item))
        source_id = row.get("source_id") or row.get("current_source_id")
        source = by_id.get(source_id)
        if not isinstance(source, Mapping):
            source = _change_side_source(change, row.get("change_id"), "before")
        if isinstance(source, Mapping):
            row.update({key: source[key] for key in ("path", "content_digest") if key in source})
        for evidence_side, identity_key in (("before", "baseline_source_id"), ("after", "current_source_id")):
            identity = row.get(identity_key)
            bound = by_id.get(identity)
            if not isinstance(bound, Mapping):
                bound = _change_side_source(change, row.get("change_id"), evidence_side)
            if isinstance(bound, Mapping):
                row.update({f"{evidence_side}_{key}": bound[key] for key in ("path", "content_digest") if key in bound})
        items.append(row)
    value = dict(_plain(prompt)); value["items"] = items
    return _freeze(value)


def _batch_action_payload(
    run_root: Path, kind: str, prompt: Mapping[str, Any], plan: Mapping[str, Any],
    inventory: Mapping[str, Any], manifest: Mapping[str, Any], change: Mapping[str, Any] | None, destination: Path,
) -> Mapping[str, Any]:
    prerequisites = {
        "context_plan": _binding(run_root, plan),
        "source_inventory": _binding(run_root, inventory),
        "flow_input": _binding(run_root, manifest),
    }
    if change is not None:
        prerequisites["change_input"] = _binding(run_root, change)
    bound_prompt = _batch_prompt(plan, prompt, inventory, change)
    carrier = {
        "schema_version": "1.0.0", "artifact": "feature-flow-batch-action", "action": kind,
        "record_path": destination.resolve().relative_to(run_root.resolve()).as_posix(),
        "run_mode": bound_prompt.get("run_mode"), "prerequisites": _plain(prerequisites), "request": _plain(bound_prompt),
    }
    return _freeze({
        "run_mode": carrier["run_mode"], "action": kind, "artifact": _binding(run_root, carrier),
        "prerequisites": carrier["prerequisites"], "prompt": carrier["request"],
    })


def _authenticated_evidence_action(run_root: Path, action: FeatureFlowAction) -> Mapping[str, Any]:
    """Require the caller's batch action to equal its persisted destination-bound carrier."""
    destination = _record_path(run_root, len(_records(run_root)) + 1, action.kind)
    if action.record_path != destination:
        raise FlowError("FEATURE_FLOW_EVIDENCE", "/action", "Batch action does not name the current canonical record destination.")
    artifact = action.artifact
    if (not isinstance(artifact, Mapping)
            or set(artifact) != {"run_mode", "action", "artifact", "prerequisites", "prompt"}
            or artifact.get("action") != action.kind or not isinstance(artifact.get("prompt"), Mapping)):
        raise FlowError("FEATURE_FLOW_EVIDENCE", "/action", "Batch action has no canonical persisted prompt.")
    carrier = _read_binding(run_root, artifact.get("artifact"), "/action/artifact")
    expected_path = destination.resolve().relative_to(run_root.resolve()).as_posix()
    if (set(carrier) != {"schema_version", "artifact", "action", "record_path", "run_mode", "prerequisites", "request"}
            or carrier.get("schema_version") != "1.0.0" or carrier.get("artifact") != "feature-flow-batch-action"
            or carrier.get("action") != action.kind or carrier.get("record_path") != expected_path
            or _plain({key: artifact.get(key) for key in ("run_mode", "action", "prerequisites", "prompt")})
            != _plain({"run_mode": carrier.get("run_mode"), "action": carrier.get("action"), "prerequisites": carrier.get("prerequisites"), "prompt": carrier.get("request")})):
        raise FlowError("FEATURE_FLOW_EVIDENCE", "/action", "Batch action does not match its canonical persisted carrier.")
    return artifact


def read_feature_flow_evidence(
    project: Path, run_root: Path, action: FeatureFlowAction, item_id: str, side: str | None = None,
    *, change_input: ChangeInputSpec | None = None, blob_resolver: BlobResolver | None = None,
) -> bytes:
    """Read exactly one digest-verified range named by a public batch action."""
    try:
        if not all(isinstance(value, Path) for value in (project, run_root)) or not isinstance(action, FeatureFlowAction) or action.kind not in {"PRODUCE_BATCH_CANDIDATE", "RUN_BATCH_FALSE_CLAIM_AUDIT", "RUN_BATCH_OMISSION_AUDIT"} or not isinstance(item_id, str):
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/action", "A batch action and item ID are required.")
        artifact = _authenticated_evidence_action(run_root, action)
        if not isinstance(artifact.get("prerequisites"), Mapping):
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/action", "Batch action has no closed evidence bindings.")
        prerequisites = artifact["prerequisites"]
        plan = _read_binding(run_root, prerequisites.get("context_plan"), "/action/prerequisites/context_plan")
        inventory = _read_binding(run_root, prerequisites.get("source_inventory"), "/action/prerequisites/source_inventory")
        flow = _read_binding(run_root, prerequisites.get("flow_input"), "/action/prerequisites/flow_input")
        prompt = artifact["prompt"]
        batch_id = prompt.get("batch_id")
        batches = [row for row in plan.get("batches", ()) if isinstance(row, Mapping) and row.get("batch_id") == batch_id]
        if len(batches) != 1:
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/action/prompt/batch_id", "Action batch is not uniquely bound.")
        items = [row for row in batches[0].get("items", ()) if isinstance(row, Mapping) and row.get("item_id") == item_id]
        if len(items) != 1:
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/item_id", "Item is not uniquely owned by the selected batch.")
        item = items[0]
        mode = flow.get("run_mode")
        change = None
        if mode == "FULL":
            if side is not None:
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/side", "FULL evidence has no before-or-after side.")
            source_id, range_row = item.get("source_id"), item
        else:
            change = _read_binding(run_root, prerequisites.get("change_input"), "/action/prerequisites/change_input")
            evidence = [row for row in item.get("evidence_sides", ()) if isinstance(row, Mapping) and row.get("side") == side]
            if side not in {"before", "after"} or len(evidence) != 1:
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/side", "Change evidence side is not uniquely bound.")
            source_id, range_row = (item.get("baseline_source_id") if side == "before" else item.get("current_source_id")), evidence[0]
        sources = inventory.get("artifacts", {}).get("authorized_behavior_sources", {}).get("sources", ())
        source = next((row for row in sources if isinstance(row, Mapping) and row.get("source_id") == source_id), None)
        frozen = _change_side_source(change, item.get("change_id"), str(side)) if mode != "FULL" else None
        if not isinstance(source, Mapping):
            source = frozen
        if not isinstance(source, Mapping) or not isinstance(source.get("path"), str):
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/item_id", "Item source is not in the bound inventory or frozen change input.")
        if source is not frozen and source.get("source_id") != source_id:
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/item_id", "Item source does not bind the requested evidence side.")
        path = PurePosixPath(source["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in source["path"]:
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/item_id", "Item source path is unsafe.")
        revision = flow.get("source_revision")
        if mode == "FULL" and isinstance(revision, Mapping) and revision.get("durability") == "DURABLE":
            data = _git_blob_reader(project, str(revision.get("commit")))(source["path"])
        elif mode != "FULL" and isinstance(change, Mapping) and change.get("input_kind") == "git_range":
            snapshot = change.get("base") if side == "before" else change.get("target")
            if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("commit"), str):
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/action/prerequisites/change_input", "Git range snapshot is not bound.")
            data = _git_blob_reader(project, snapshot["commit"])(source["path"])
        elif mode != "FULL" and isinstance(change, Mapping) and change.get("input_kind") == "git_worktree" and side == "before":
            snapshot = change.get("base")
            if not isinstance(snapshot, Mapping) or not isinstance(snapshot.get("commit"), str):
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/action/prerequisites/change_input", "Worktree base snapshot is not bound.")
            data = _git_blob_reader(project, snapshot["commit"])(source["path"])
        elif mode != "FULL" and isinstance(change, Mapping) and change.get("input_kind") == "patch_manifest":
            if not isinstance(change_input, ChangeInputSpec) or change_input.patch_manifest is None or blob_resolver is None:
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/change_input", "Patch evidence requires the original ChangeInputSpec and BlobResolver.")
            reacquired = acquire_change_input(project, change_input, blob_resolver)
            if artifact_sha256(_plain(reacquired)) != artifact_sha256(_plain(change)):
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/change_input", "Reacquired patch input does not match the bound change input.")
            manifest = _read_json(change_input.patch_manifest, "FEATURE_FLOW_EVIDENCE", "/change_input/patch_manifest")
            blobs = manifest.get("content_blobs", ())
            blob = next((row for row in blobs if isinstance(row, Mapping) and row.get("content_sha256") == range_row.get("content_sha256")), None)
            blob_id = blob.get("controller_blob_id") if isinstance(blob, Mapping) else None
            data = blob_resolver.get(blob_id) if isinstance(blob_resolver, Mapping) else blob_resolver(blob_id) if isinstance(blob_id, str) else None
            if not isinstance(data, bytes):
                raise FlowError("FEATURE_FLOW_EVIDENCE", "/change_input", "Patch blob could not be resolved from its bound controller identity.")
        else:
            target = (project.resolve() / Path(*path.parts)).resolve()
            target.relative_to(project.resolve())
            data = target.read_bytes()
        digest = source.get("content_digest") if mode == "FULL" else range_row.get("content_sha256")
        read_range = range_row.get("read_range")
        if not isinstance(digest, str) or digest != "sha256:" + hashlib.sha256(data).hexdigest() or not isinstance(read_range, Mapping):
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/item_id", "Evidence content no longer matches its bound digest.")
        start, end = read_range.get("start"), read_range.get("end")
        if type(start) is not int or type(end) is not int or start < 0 or end < start or end > len(data):
            raise FlowError("FEATURE_FLOW_EVIDENCE", "/item_id", "Evidence range is outside verified content.")
        return data[start:end]
    except FlowError:
        raise
    except Exception:
        raise FlowError("FEATURE_FLOW_EVIDENCE", "/evidence", "Evidence could not be read.") from None


def _replayed_record(run_root: Path, records: list[Mapping[str, Any]], index: int, kind: str, supplied: Path | None) -> tuple[Mapping[str, Any], Path | None]:
    """Consume one record only at its exact replay ordinal and action label."""
    expected = _record_path(run_root, index + 1, kind)
    matches = list(expected.parent.glob(f"{index + 1:06d}-*.json"))
    if len(matches) != 1 or matches[0].resolve() != expected.resolve():
        raise FlowError("FEATURE_FLOW_INPUT", "/record", "Record path does not match the current canonical action.")
    if supplied is not None:
        if supplied.resolve() == expected.resolve():
            supplied = None
    return records[index], supplied


def _validate_supplied_newest(run_root: Path, records: list[Mapping[str, Any]], supplied: Path | None) -> None:
    if supplied is None:
        return
    if not records:
        raise FlowError("FEATURE_FLOW_INPUT", "/record", "Record must be the newest exact canonical action destination.")
    matches = list((run_root / "feature-flow" / "prefix").glob(f"{len(records):06d}-*.json"))
    if len(matches) != 1 or matches[0].resolve() != supplied.resolve():
        raise FlowError("FEATURE_FLOW_INPUT", "/record", "Record must be the newest exact canonical action destination.")


def _change_paths(change_input: Mapping[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(change_input, Mapping):
        return ()
    rows = change_input.get("changes")
    if not isinstance(rows, (list, tuple)):
        return ()
    paths = []
    for row in rows:
        if not isinstance(row, Mapping):
            return ()
        for key in ("path", "old_path", "new_path"):
            value = row.get(key)
            if isinstance(value, str):
                paths.append(value)
    return tuple(sorted(set(paths)))


def _full_action(run_root: Path, module: Mapping[str, Any], analytics_sha256: str, change: Mapping[str, Any] | None) -> FeatureFlowAction:
    """Return the explicit initial/fallback boundary; never pretend it is scoped."""
    request = {
        "schema_version": "1.0.0", "artifact": "full-baseline-request", "run_mode": "FULL",
        "selected_module": module["id"], "analytics_sha256": analytics_sha256,
        "change_input_sha256": None if change is None else change["change_input_sha256"],
    }
    return FeatureFlowAction("RUN_FULL_BASELINE", _action_payload(run_root, "RUN_FULL_BASELINE", request), None)


def _byte_resolver(project: Path, baseline: Any, after_reader: Any | None = None):
    def resolve(side: str, source: Mapping[str, Any]) -> bytes:
        path = source.get("path")
        if not isinstance(path, str) or not path or "/" not in path and path.startswith("."):
            raise FlowError("CHANGE_SOURCE_DRIFT", "/source", "Source bytes cannot be resolved.")
        if side == "before":
            return subprocess.run(["git", "show", f"{baseline.target_commit}:{path}"], cwd=project, capture_output=True, check=True).stdout
        return after_reader(path) if after_reader is not None else (project / Path(*path.split("/"))).read_bytes()
    return resolve


def _generator_complete(run_root: Path, mode: str, generated: Mapping[str, Any], context: Mapping[str, Any], receipt: Mapping[str, Any], effective: Any | None) -> Mapping[str, Any]:
    if schema_diagnostics(_plain(generated), _ROOT / "schemas" / "tc-generator-output.schema.json", _ROOT):
        raise FlowError("FEATURE_FLOW_INPUT", "/record", "Generator output violates its closed mode-aware schema.")
    if generated.get("run_mode") != mode:
        raise FlowError("FEATURE_FLOW_INPUT", "/record", "Generator output run mode is incompatible.")
    artifacts = generated["artifacts"]
    result: dict[str, Any] = {
        "schema_version": "1.0.0", "artifact": "feature-flow-ready-handoff",
        "status": "READY_FOR_PIPELINE_TAIL", "run_mode": mode,
        "generator_output": _plain(_binding(run_root, generated)),
        "changed_behavior_context": _plain(_binding(run_root, context)),
        "behavior_context_receipt": _plain(_binding(run_root, receipt)),
    }
    if mode == "FULL":
        result["candidate_document"] = _plain(_binding(run_root, artifacts["canonical_document"]))
    else:
        if effective is None:
            raise FlowError("BASELINE_BINDING", "/baseline", "CHANGE_SET requires a validated effective baseline.")
        applied = apply_document_delta(effective_baseline_document(effective), artifacts["canonical_document_delta"], receipt, context)
        applied_receipt = delta_application_receipt(applied)
        result["delta_application_receipt"] = _plain(_binding(run_root, applied_receipt))
        if applied.status == "CHANGED":
            result["candidate_document"] = _plain(_binding(run_root, applied.candidate_document))
        else:
            selection = unchanged_document_selection(applied, effective)
            document, bundle = effective_baseline_projection(effective)
            result["unchanged_document_selection"] = _plain(_binding(run_root, selection))
            result["effective_baseline_document"] = _plain(_binding(run_root, document))
            result["effective_baseline_bundle_receipt"] = _plain(_binding(run_root, asdict(bundle)))
    _artifact(run_root, result)
    return _freeze(result)


def _prefix_ledger(run_root: Path, mode: str, module: Mapping[str, Any], analytics_sha256: str, values: Mapping[str, Any]) -> Mapping[str, Any]:
    """Close only the reviewed semantic prefix; pipeline-tail authority stays absent."""
    records = _record_bindings(run_root)
    ledger = {
        "schema_version": "1.0.0", "artifact": "feature-flow-prefix-ledger", "status": "READY_FOR_PIPELINE_TAIL",
        "run_mode": mode, "selected_module": module["id"], "analytics_sha256": analytics_sha256,
        "artifacts": {name: _plain(_binding(run_root, value)) for name, value in values.items() if value is not None},
        "records": _plain(records),
    }
    path = run_root / "feature-flow" / "prefix-ledger.json"
    if path.exists():
        existing = _read_json(path, "FLOW_CONFLICT", "/feature-flow/prefix-ledger")
        if _plain(existing) != _plain(ledger):
            raise FlowError("FLOW_CONFLICT", "/feature-flow/prefix-ledger", "Prefix ledger does not match deterministic replay.")
        return _freeze(existing)
    write_create_only(run_root, PurePosixPath("feature-flow") / "prefix-ledger.json", ledger)
    return _freeze(ledger)


def effective_baseline_document(effective: Any) -> Mapping[str, Any]:
    """Keep the capability projection at its public baseline-lifecycle seam."""
    from tools.baseline_lifecycle import effective_baseline_projection
    return effective_baseline_projection(effective)[0]


def advance_feature_flow(
    project: Path,
    analytics: Path,
    run_root: Path,
    baseline_receipt: Path | None = None,
    change_input: ChangeInputSpec | None = None,
    recorded_artifact: Path | None = None,
    controller: ReviewController | None = None,
    *,
    blob_resolver: BlobResolver | None = None,
    module_id: str | None = None,
) -> FeatureFlowAction:
    """Advance only one deterministic semantic-prefix action from canonical readback."""
    del controller  # Prefix assurance is always SEQUENTIAL; controller tuning is not public.
    try:
        if not all(isinstance(value, Path) for value in (project, analytics, run_root)):
            raise FlowError("FEATURE_FLOW_INPUT", "/input", "Project, analytics, and run root must be paths.")
        analytics_digest = _safe_digest(analytics, "FEATURE_FLOW_INPUT", "/analytics")
        if change_input is not None and not isinstance(change_input, ChangeInputSpec):
            raise FlowError("FEATURE_FLOW_INPUT", "/change_input", "Change input must use the closed public spec.")
        if change_input is not None and change_input.patch_manifest is not None and blob_resolver is None:
            raise FlowError("FEATURE_FLOW_INPUT", "/patch_manifest", "Patch manifests require an in-process BlobResolver.")
        change = acquire_change_input(project, change_input, blob_resolver) if change_input is not None else None
        module, bootstrap_status = _module(project, _change_paths(change), module_id)
        mode, baseline, predecessor, effective, source, context, behavior_receipt = "FULL", None, None, None, None, None, None
        if baseline_receipt is None:
            # A first-run range/worktree is frozen in the manifest for drift
            # detection, but FULL deliberately supplies no narrowing authority.
            pass
        else:
            try:
                baseline, predecessor, effective, source, context, behavior_receipt = _baseline(project, baseline_receipt, module)
            except FlowError as error:
                if error.code.startswith("BASELINE_"):
                    return _full_action(run_root, module, analytics_digest, change)
                raise
            target = {"input_kind": change.get("input_kind") if change else None, "repository_id": change.get("repository_id") if change else None,
                      "selected_module": module["id"], "base": _plain(change.get("base")) if change else None, "fingerprints": dict(baseline.fingerprints)}
            mode = choose_run_mode(target, baseline)
        if change is not None and change.get("input_kind") == "patch_manifest":
            _require_materialized_patch(project, change, "HEAD" if baseline is None else baseline.target_commit)
        if baseline is not None and (mode != "CHANGE_SET" or change is None):
            return _full_action(run_root, module, analytics_digest, change)
        existing_manifest = None
        manifest_path = run_root / "feature-flow" / "prefix" / "000000-flow-input.json"
        if manifest_path.exists():
            existing_manifest = _read_json(manifest_path, "FLOW_CONFLICT", "/run_root")
        skillsrc_sha256 = _safe_digest(project / ".skillsrc", "FEATURE_FLOW_INPUT", "/.skillsrc")
        provenance = _git_provenance(project)
        if mode == "CHANGE_SET" and provenance is None:
            raise FlowError("FEATURE_FLOW_INPUT", "/project", "Change-scoped execution requires Git provenance.")
        source_revision = (
            _full_provenance(provenance, bootstrap_status, change, existing_manifest, skillsrc_sha256)
            if mode == "FULL" and provenance is not None
            else None if provenance is None else {
                "commit": provenance["commit"], "tree": provenance["tree"], "skillsrc_sha256": skillsrc_sha256,
                "durability": "DURABLE" if change.get("input_kind") == "git_range" else "PROVISIONAL",
                "baseline_eligible": change.get("input_kind") == "git_range",
            }
        )
        snapshot_reader = _inventory_snapshot_reader(project, mode, change, source_revision)
        inventories = build_source_inventories(project, load_skillsrc(project / ".skillsrc"), module["id"], (), snapshot_reader=snapshot_reader)
        source_snapshot_sha256 = artifact_sha256({
            "selected_module": module["id"], "skillsrc_sha256": skillsrc_sha256,
            "authorized_behavior_sources_sha256": inventories.authorized_behavior_sources_sha256,
            "technical_test_inventory_sha256": inventories.technical_test_inventory_sha256,
        })
        manifest = {"schema_version": "1.0.0", "artifact": "feature-flow-input", "repository_id": change["repository_id"] if change else (None if provenance is None else provenance["repository_id"]),
                    "selected_module": module["id"], "run_mode": mode, "analytics_sha256": analytics_digest,
                    "baseline_receipt_sha256": None if baseline is None else baseline.receipt_sha256,
                    "change_input_sha256": None if change is None else change["change_input_sha256"], "skillsrc_sha256": skillsrc_sha256,
                    "source_revision": source_revision, "durability": "PROVISIONAL" if source_revision is None else source_revision["durability"],
                    "source_snapshot_sha256": source_snapshot_sha256}
        _manifest(run_root, manifest)
        source_envelope = {"schema_version": "1.0.0", "stage": "source-inventory", "artifacts": {
            "technical_test_inventory": _plain(inventories.technical_test_inventory), "technical_test_inventory_sha256": inventories.technical_test_inventory_sha256,
            "authorized_behavior_sources": _plain(inventories.authorized_behavior_sources), "authorized_behavior_sources_sha256": inventories.authorized_behavior_sources_sha256,
        }, "warnings": []}
        _artifact(run_root, source_envelope)
        if change is not None:
            _artifact(run_root, change)
        scope = start_scope(ScopeInputs(project, mode, module, analytics_digest, change if mode == "CHANGE_SET" else None, inventories.authorized_behavior_sources, inventories.technical_test_inventory, predecessor))
        records = _records(run_root)
        _validate_supplied_newest(run_root, records, recorded_artifact)
        index = 0
        # Scope records are the prefix of the one record log.  Replaying through
        # public state-machine calls is what makes stale paths and branches inert.
        while True:
            action = advance_scope(scope)
            if action.kind == "COMPLETE":
                scope_receipt = _plain(action.artifact)
                scope_candidate = _plain(scope.candidate) if scope.candidate is not None else None
                break
            if action.kind == "BLOCKED":
                return FeatureFlowAction("BLOCKED", None, None, tuple(action.diagnostics))
            if index >= len(records):
                if recorded_artifact is not None:
                    destination = _record_path(run_root, index + 1, action.kind)
                    supplied = _recorded(destination, recorded_artifact)
                    _artifact(run_root, supplied)
                    return advance_feature_flow(project, analytics, run_root, baseline_receipt, change_input, blob_resolver=blob_resolver, module_id=module_id)
                return FeatureFlowAction(action.kind, _action_payload(run_root, action.kind, action.artifact), _record_path(run_root, index + 1, action.kind))
            value, recorded_artifact = _replayed_record(run_root, records, index, action.kind, recorded_artifact)
            scope = record_scope(scope, value); index += 1
        _artifact(run_root, scope_receipt)
        if mode == "FULL":
            plan = _plain(build_context_plan(project, module, _plain(inventories.authorized_behavior_sources)))
        else:
            assert baseline is not None and source is not None and scope_candidate is not None
            plan = _plain(build_change_context_plan(project, module, scope_receipt, scope_candidate, source, source_envelope, _byte_resolver(project, baseline, snapshot_reader)))
        promotion = start_promotion(scope_receipt, plan)
        evidence: list[PromotionEvidence] = []
        current: list[Mapping[str, Any]] = []
        while True:
            action = advance_promotion(promotion)
            if action.kind == "COMPLETE":
                if current:
                    raise FlowError("FEATURE_FLOW_INPUT", "/records", "Promotion ledger is incomplete.")
                break
            if action.kind == "PROMOTE_BATCH":
                if index < len(records):
                    stored, recorded_artifact = _replayed_record(run_root, records, index, action.kind, recorded_artifact)
                    if _plain(stored) != _plain(action.artifact):
                        raise FlowError("FEATURE_FLOW_INPUT", "/records", "Promotion receipt does not match public replay.")
                    index += 1
                else:
                    _append_record(run_root, records, action.artifact, _record_label(action.kind))
                    return FeatureFlowAction("PROMOTE_BATCH", _action_payload(run_root, "PROMOTE_BATCH", action.artifact), None)
                assert action.next_snapshot is not None and promotion.candidate is not None
                evidence.append(PromotionEvidence(tuple(current), action.artifact))
                current = []; promotion = action.next_snapshot
                continue
            if index >= len(records):
                if recorded_artifact is not None:
                    destination = _record_path(run_root, index + 1, action.kind)
                    supplied = _recorded(destination, recorded_artifact)
                    _artifact(run_root, supplied)
                    return advance_feature_flow(project, analytics, run_root, baseline_receipt, change_input, blob_resolver=blob_resolver, module_id=module_id)
                destination = _record_path(run_root, index + 1, action.kind)
                payload = _batch_action_payload(run_root, action.kind, action.artifact, plan, source_envelope, manifest, change, destination) if action.kind in {"PRODUCE_BATCH_CANDIDATE", "RUN_BATCH_FALSE_CLAIM_AUDIT", "RUN_BATCH_OMISSION_AUDIT"} else _action_payload(run_root, action.kind, action.artifact)
                return FeatureFlowAction(action.kind, payload, destination)
            value, recorded_artifact = _replayed_record(run_root, records, index, action.kind, recorded_artifact)
            promotion = record_promotion(promotion, value); current.append(value); index += 1
        if index < len(records):
            if index + 1 != len(records):
                raise FlowError("FEATURE_FLOW_INPUT", "/records", "Run record graph has a gap or branch.")
            generated = records[index]
            generated, recorded_artifact = _replayed_record(run_root, records, index, "GENERATE_CHANGED_BEHAVIOR", recorded_artifact)
        else:
            resolver = _byte_resolver(project, baseline, snapshot_reader) if baseline is not None else (lambda side, row: snapshot_reader(str(row["path"])) if snapshot_reader is not None else (project / Path(*str(row["path"]).split("/"))).read_bytes())
            composed = compose_behavior_context(project, module, source_envelope, scope_receipt, plan, tuple(evidence), None if mode == "FULL" else {"predecessor": predecessor, "context": context, "receipt": behavior_receipt}, resolver)
            context_value, receipt_value = _plain(composed.context), _plain(composed.receipt)
            _artifact(run_root, context_value); _artifact(run_root, _plain(composed.changed_behavior_context)); _artifact(run_root, receipt_value)
            request = {"schema_version": "1.0.0", "artifact": "changed-behavior-generation-request", "run_mode": mode,
                       "changed_behavior_context_sha256": artifact_sha256(_plain(composed.changed_behavior_context)), "behavior_context_receipt_sha256": artifact_sha256(receipt_value)}
            if recorded_artifact is None:
                prerequisites = {
                    "changed_behavior_context": _binding(run_root, _plain(composed.changed_behavior_context)),
                    "behavior_context_receipt": _binding(run_root, receipt_value),
                }
                return FeatureFlowAction("GENERATE_CHANGED_BEHAVIOR", _action_payload(run_root, "GENERATE_CHANGED_BEHAVIOR", request, prerequisites), _record_path(run_root, index + 1, "GENERATE_CHANGED_BEHAVIOR"))
            generated = _recorded(_record_path(run_root, index + 1, "GENERATE_CHANGED_BEHAVIOR"), recorded_artifact)
            _artifact(run_root, generated)
            return advance_feature_flow(project, analytics, run_root, baseline_receipt, change_input, blob_resolver=blob_resolver, module_id=module_id)
        # Recompose on readback, then make the closed handoff.  We intentionally
        # do not run tc-reviewer, automation, trace, terminal receipt, or advance baseline.
        resolver = _byte_resolver(project, baseline, snapshot_reader) if baseline is not None else (lambda side, row: snapshot_reader(str(row["path"])) if snapshot_reader is not None else (project / Path(*str(row["path"]).split("/"))).read_bytes())
        composed = compose_behavior_context(project, module, source_envelope, scope_receipt, plan, tuple(evidence), None if mode == "FULL" else {"predecessor": predecessor, "context": context, "receipt": behavior_receipt}, resolver)
        context_value, changed_value, receipt_value = _plain(composed.context), _plain(composed.changed_behavior_context), _plain(composed.receipt)
        _artifact(run_root, context_value); _artifact(run_root, changed_value); _artifact(run_root, receipt_value)
        complete = _generator_complete(run_root, mode, generated, changed_value, receipt_value, effective)
        ledger = _prefix_ledger(run_root, mode, module, analytics_digest, {
            "flow_input": manifest, "change_input": change,
            "source_inventory": source_envelope, "change_scope_candidate": scope_candidate,
            "scope_false_inclusion_audit": _record_values(run_root, "run-scope-false-inclusion-audit")[-1],
            "scope_omission_audit": _record_values(run_root, "run-scope-omission-audit")[-1],
            "change_scope_receipt": scope_receipt, "context_plan": plan,
            "context_envelope": context_value, "changed_behavior_context": changed_value,
            "behavior_context_receipt": receipt_value, "generator_output": generated,
            "delta_handoff": complete,
        })
        return FeatureFlowAction("COMPLETE", ledger, None)
    except (FlowError, SkillsrcError, ValueError, OSError, subprocess.SubprocessError) as error:
        return _blocked(error)
