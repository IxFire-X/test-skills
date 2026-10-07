"""Confined generated-test delta transaction; this module never executes tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.confined_output import (
    OutputConfinementError,
    create_confined_bytes_exclusive,
    read_confined_bytes,
    remove_confined_bytes_if_equal,
)
from tools.automation_validation import (
    automation_sha256,
    autotest_review_sha256,
    validate_accepted_autotest_review,
    validate_automation_revision_chain,
)
from tools.canonical_document import validate_canonical_document


def _run_operation(function):
    """Hold the run lock for the whole command (imported lazily to avoid an import cycle)."""
    from functools import wraps

    @wraps(function)
    def operation(*args, **kwargs):
        from tools.pilot_state import run_operation
        return run_operation(function)(*args, **kwargs)
    return operation



class GeneratedDeltaError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(value if isinstance(value, bytes) else _bytes(value)).hexdigest()


def _seal(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result.pop("digest", None)
    result["digest"] = _digest(result)
    return result


def _module_name(project: Path, module: Path) -> str:
    try:
        relative = module.resolve().relative_to(project.resolve())
    except ValueError as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "module is outside the selected project") from error
    return relative.as_posix() or "."


def _target(module: Path, test_root: str, path: str) -> Path:
    normalized = path.replace("\\", "/")
    raw = Path(normalized)
    prefix = test_root.rstrip("/") + "/"
    if raw.is_absolute() or ".." in raw.parts or not normalized.startswith(prefix):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated path escapes selected native test root")
    return module / raw


def _file_row(file_id: str, path: str, content: bytes, baseline: Mapping[str, Any], automation_digest: str, review_digest: str, materialization: str) -> dict[str, Any]:
    return {
        "file_id": file_id, "path": path,
        "content_digest": _digest(content),
        "baseline_digest": baseline.get("digest"), "automation_digest": automation_digest, "review_digest": review_digest,
        "baseline_absent": True,
        "ownership_digest": _digest(
            {"baseline": baseline.get("digest"), "automation": automation_digest, "review": review_digest, "path": path}
        ),
        "materialization": materialization,
    }


def _delta(*, project: Path, module: Path, test_root: str, baseline: Mapping[str, Any], automation_digest: str, review_digest: str, effective_canonical_digest: str, effective_bundle_receipt_digest: str, files: list[dict[str, Any]], facts: Mapping[str, Any]) -> dict[str, Any]:
    return _seal(
        {
            "schema_version": "1.0.0",
            "module": _module_name(project, module),
            "test_root": test_root,
            "baseline_digest": baseline.get("digest"),
            "automation_digest": automation_digest,
            "review_digest": review_digest,
            "effective_canonical_digest": effective_canonical_digest,
            "effective_bundle_receipt_digest": effective_bundle_receipt_digest,
            "files": files,
            "facts": dict(facts),
        }
    )


def _conflict(error: Exception) -> GeneratedDeltaError:
    return GeneratedDeltaError("MATERIALIZATION_CONFLICT", str(error))


def _reviewed_generated_files(automation: Mapping[str, Any], review: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], str, str]:
    """Return exact V5 source bytes only after a matching accepted static review."""
    artifacts = automation.get("artifacts")
    reviewed_wrapper = review.get("artifacts")
    reviewed = reviewed_wrapper.get("autotest_review") if isinstance(reviewed_wrapper, Mapping) else None
    if not isinstance(artifacts, Mapping) or not isinstance(reviewed, Mapping):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "V5 automation and static review artifacts are required")
    generated = artifacts.get("generated_files")
    automation_digest = automation_sha256(automation)
    if (
        artifacts.get("automation_status") != "GENERATED"
        or not isinstance(generated, list)
        or not generated
        or reviewed.get("verdict") != "ПРИНЯТО"
        or reviewed.get("automation_sha256") != automation_digest
    ):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "automation review is not accepted and exactly bound")
    expected_files = [
        {"file_id": row.get("file_id"), "content_digest": row.get("content_digest")}
        for row in generated if isinstance(row, Mapping)
    ]
    if len(expected_files) != len(generated) or reviewed.get("reviewed_files") != expected_files:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "static review does not cover the complete generated file set")
    return generated, automation_digest, autotest_review_sha256(review)


def _baseline_test_paths(baseline: Mapping[str, Any]) -> set[str]:
    """Express frozen project paths in the selected module's test-root namespace."""
    module = baseline.get("module")
    if not isinstance(module, str):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "baseline module is invalid")
    prefix = "" if module == "." else module.rstrip("/") + "/"
    paths: set[str] = set()
    for row in baseline.get("inputs", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("project_path"), str):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "baseline input is invalid")
        path = row["project_path"].replace("\\", "/")
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        paths.add(path)
    return paths


def _frozen_attempt(
    project: Path,
    module: Path,
    baseline: Mapping[str, Any],
    canonical_document: Mapping[str, Any],
    run_root: Path | None,
    attempt_id: str | None,
) -> tuple[dict[str, Any], Any]:
    """Fail closed unless controller-owned run, attempt and baseline all agree."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "controller-owned run and attempt coordinates are required")
    try:
        from tools.pilot_state import derive_state, read_run
        from tools.project_inventory import (
            InventoryError,
            read_execution_baseline,
            read_inventory_receipt,
            validate_execution_baseline_binding,
        )

        run = read_run(run_root)
        manifest = run["manifest"]
        if Path(str(manifest["project"])).resolve() != project or manifest.get("policy_profile") != "local-pilot-v1" or run["authorization"].get("execution_requested") is not True:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "local-pilot execution authorization is absent")
        state = derive_state(run_root)
        attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
        if attempt is None or attempt["state"] == "TERMINAL" or attempt.get("policy_profile") != "local-pilot-v1":
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "attempt is not active local-pilot state")
        if attempt.get("module") != _module_name(project, module) or attempt.get("baseline_digest") != baseline.get("digest"):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "attempt identity or baseline binding is invalid")
        baseline_name = str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"
        frozen = read_execution_baseline(Path(run_root) / "baselines" / baseline_name)
        inventory_name = str(frozen["inventory_digest"]).removeprefix("sha256:") + ".json"
        inventory = read_inventory_receipt(Path(run_root) / "inventories" / inventory_name)
        validate_execution_baseline_binding(frozen, inventory, module)
    except GeneratedDeltaError:
        raise
    except (ValueError, OSError, InventoryError) as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "immutable execution baseline is invalid or unreadable") from error
    if dict(baseline) != frozen or baseline.get("module") != _module_name(project, module):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "caller baseline is not the exact frozen receipt")
    if not isinstance(canonical_document, Mapping) or validate_canonical_document(dict(canonical_document)):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "canonical document is invalid")
    return frozen, run


def _effective_selection(run_root: Path, attempt_id: str, canonical_document: Mapping[str, Any]) -> Mapping[str, Any]:
    """Require the single durable Phase-4 selection for this exact attempt."""
    try:
        from tools.canonical_document import document_sha256
        from tools.pilot_state import read_effective_canonical

        effective = read_effective_canonical(run_root, attempt_id)
    except (ImportError, KeyError, TypeError, ValueError) as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "effective canonical selection is absent or unreadable") from error
    if (
        not isinstance(effective, Mapping)
        or effective.get("document") != dict(canonical_document)
        or effective.get("document_digest") != document_sha256(dict(canonical_document))
        or not isinstance(effective.get("effective_bundle_receipt_digest"), str)
    ):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "effective canonical selection does not match caller document")
    return effective


def _durable_boundary(run_root: Path, attempt_id: str, revision: int) -> Mapping[str, Any]:
    try:
        from tools.pilot_state import read_attempt_receipt

        return read_attempt_receipt(run_root, attempt_id, f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "static review boundary is not durable/read-back") from error


def _accepted_chain(
    automation: Mapping[str, Any], review: Mapping[str, Any], canonical_document: Mapping[str, Any],
    run_root: Path, attempt_id: str, automation_history: Sequence[Mapping[str, Any]], review_history: Sequence[Mapping[str, Any]],
) -> None:
    try:
        revision = automation["artifacts"]["automation_revision"]
    except (KeyError, TypeError) as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "automation revision is invalid") from error
    if type(revision) is not int or revision not in {1, 2} or len(automation_history) != revision - 1 or len(review_history) != revision - 1:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "automation revision history is incomplete")
    versions = [*automation_history, automation]
    reviews = [*review_history, review]
    boundaries = [_durable_boundary(run_root, attempt_id, index) for index in range(1, revision + 1)]
    effective = _effective_selection(run_root, attempt_id, canonical_document)
    source = automation.get("artifacts", {}).get("source") if isinstance(automation.get("artifacts"), Mapping) else None
    reviewed_source = review.get("artifacts", {}).get("autotest_review", {}).get("source") if isinstance(review.get("artifacts"), Mapping) and isinstance(review.get("artifacts", {}).get("autotest_review"), Mapping) else None
    expected_source = {
        "document_id": canonical_document.get("document_id"), "revision": canonical_document.get("revision"),
        "source_digest": effective.get("document_digest"), "effective_bundle_receipt_digest": effective.get("effective_bundle_receipt_digest"),
    }
    if source != expected_source or reviewed_source != expected_source:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "automation or static review source differs from effective canonical selection")
    rows = validate_automation_revision_chain(
        versions, reviews, dict(canonical_document), host_isolation_receipts=boundaries, run_root=run_root, attempt_id=attempt_id,
    )
    rows.extend(validate_accepted_autotest_review(
        review, automation, dict(canonical_document), host_isolation_receipt=boundaries[-1], run_root=run_root, attempt_id=attempt_id,
    ))
    if rows:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "accepted reviewed automation chain is invalid")


def _read_durable_delta(run_root: Path, attempt_id: str, delta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        from tools.pilot_state import read_attempt_receipt

        record = read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")["record"]
    except (KeyError, TypeError, ValueError) as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated delta is not durable/read-back") from error
    durable = record.get("delta")
    if not isinstance(durable, Mapping) or (delta is not None and dict(delta) != dict(durable)):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated delta does not match its attempt-owned receipt")
    return dict(durable)


@_run_operation
def materialize_delta(
    project: Path, module_root: Path, baseline: Mapping[str, Any], automation: Mapping[str, Any], review: Mapping[str, Any], *,
    canonical_document: Mapping[str, Any] | None = None, run_root: Path | None = None, attempt_id: str | None = None,
    automation_history: Sequence[Mapping[str, Any]] = (), review_history: Sequence[Mapping[str, Any]] = (),
    fail_after: int | None = None, interrupt_after: int | None = None,
) -> dict[str, Any]:
    """Materialize a complete, reviewed generated file set exactly once.

    ``fail_after`` and ``interrupt_after`` are injected test seams only.  The
    latter models a process stop after project writes but before a final delta.
    """
    project, module = Path(project).resolve(), Path(module_root).resolve()
    frozen, _run = _frozen_attempt(project, module, baseline, canonical_document or {}, run_root, attempt_id)
    effective = _effective_selection(run_root, attempt_id, canonical_document or {})
    _accepted_chain(automation, review, canonical_document or {}, run_root, attempt_id, automation_history, review_history)
    try:
        from tools.pilot_state import publish_execution_inputs

        durable_inputs = publish_execution_inputs(run_root, attempt_id, automation, review)["record"]
    except (KeyError, TypeError, ValueError, OSError) as error:
        raise GeneratedDeltaError(
            "MATERIALIZATION_CONFLICT", "reviewed execution inputs are not durable/read-back",
        ) from error
    if (
        durable_inputs.get("automation_artifact") != dict(automation)
        or durable_inputs.get("autotest_review") != dict(review)
    ):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "durable execution inputs differ")
    generated, automation_digest, review_digest = _reviewed_generated_files(automation, review)
    test_root = frozen.get("test_root")
    if not isinstance(test_root, str) or not test_root or not isinstance(generated, list) or not generated:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "selected module test root or generated file set is invalid")
    root = module / test_root
    if not root.is_dir() or root.is_symlink():
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "selected native test root is unavailable or unsafe")
    baseline_paths = _baseline_test_paths(frozen)
    # A retry is safe only when immutable ownership proves the exact same first
    # materialization and the bytes are still unchanged.  Any other existing path
    # remains a conflict; it is never silently reclassified as not materialized.
    receipt_path = Path(run_root) / "generated-deltas" / f"{attempt_id}.json"
    if receipt_path.exists():
        try:
            existing = _read_durable_delta(run_root, attempt_id)
        except GeneratedDeltaError as error:
            try:
                from tools.pilot_state import recover_phase5_receipt_events

                existing = recover_phase5_receipt_events(run_root, attempt_id, "generated-delta")["record"]["delta"]
            except (KeyError, TypeError, ValueError) as recovery_error:
                raise error from recovery_error
        if (
            existing.get("module") != _module_name(project, module)
            or existing.get("test_root") != test_root
            or existing.get("baseline_digest") != frozen.get("digest")
            or existing.get("automation_digest") != automation_digest
            or existing.get("review_digest") != review_digest
            or existing.get("effective_canonical_digest") != effective.get("document_digest")
            or existing.get("effective_bundle_receipt_digest") != effective.get("effective_bundle_receipt_digest")
        ):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "existing attempt-owned delta differs from requested materialization")
        expected = [(item.get("file_id"), item.get("path"), item.get("content_digest")) for item in generated]
        actual = [(item.get("file_id"), item.get("path"), item.get("content_digest")) for item in existing.get("files", []) if isinstance(item, Mapping)]
        if expected != actual:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "existing attempt-owned delta cannot be safely reused")
        if existing.get("facts", {}).get("completion") == "PARTIAL":
            requested = {
                row["path"]: ("CLEANED" if row.get("materialization") == "MATERIALIZED" else "NOT_MATERIALIZED")
                for row in existing.get("files", []) if isinstance(row, Mapping) and isinstance(row.get("path"), str)
            }
            apply_dispositions(project, existing, requested, run_root=run_root, attempt_id=attempt_id, verification="NOT_APPLICABLE")
            return existing
        if existing.get("facts", {}).get("completion") != "COMPLETE" or inspect_delta(project, existing) != {"valid": True}:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "existing attempt-owned delta cannot be safely reused")
        return existing
    planned: list[tuple[str, str, bytes, Path]] = []
    seen_paths: set[str] = set()
    seen_components: list[tuple[str, ...]] = []
    try:
        from tools.pilot_state import read_attempt_receipt

        ownership = read_attempt_receipt(run_root, attempt_id, "materialization-ownership", "ARTIFACT_READ_BACK")["record"]["payload"]
    except (KeyError, ValueError):
        ownership = None
    try:
        for item in generated:
            if not isinstance(item, Mapping):
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated file is invalid")
            file_id, path, source = item.get("file_id"), item.get("path"), item.get("content")
            if not isinstance(file_id, str) or not isinstance(path, str) or not isinstance(source, str) or path in baseline_paths:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated file lacks baseline-absence proof")
            content = source.encode("utf-8")
            if item.get("content_digest") != _digest(content):
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated file content digest does not bind UTF-8 source bytes")
            if path.casefold() in seen_paths:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated file set has a case-colliding path")
            seen_paths.add(path.casefold())
            target = _target(module, test_root, path)
            components = tuple(part.casefold() for part in target.relative_to(module).parts)
            if any(
                components[:len(previous)] == previous or previous[:len(components)] == components
                for previous in seen_components
            ):
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated file set has component-colliding paths")
            seen_components.append(components)
            existing = read_confined_bytes(project, root, target)
            if existing is not None and ownership is None:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "existing file is not pipeline-owned")
            if existing is not None and existing != content:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "existing owned file content conflicts")
            planned.append((file_id, path, content, target))
    except OutputConfinementError as error:
        raise _conflict(error) from error

    intent = {
        "schema_version": "1.0.0", "stage": "materialization-ownership",
        "module": _module_name(project, module), "test_root": test_root,
        "baseline_digest": frozen["digest"], "automation_digest": automation_digest, "review_digest": review_digest,
        "effective_canonical_digest": effective["document_digest"], "effective_bundle_receipt_digest": effective["effective_bundle_receipt_digest"],
        "files": [
            {key: row[key] for key in ("file_id", "path", "content_digest", "baseline_absent", "ownership_digest")}
            for row in (_file_row(file_id, path, content, frozen, automation_digest, review_digest, "MATERIALIZED") for file_id, path, content, _target in planned)
        ],
    }
    ownership_created = False
    if ownership is None:
        try:
            from tools.pilot_state import publish_phase5_receipt

            published_ownership = publish_phase5_receipt(run_root, attempt_id, "materialization-ownership", intent)
            ownership, ownership_created = published_ownership["record"]["payload"], published_ownership["created"]
        except (ValueError, OSError) as error:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "materialization ownership publication is uncertain") from error
    elif dict(ownership) != intent:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "existing materialization ownership differs")
    files: list[dict[str, Any]] = []
    written: list[tuple[dict[str, Any], bytes, Path, Any]] = []
    failed = False
    try:
        for index, (file_id, path, content, target) in enumerate(planned):
            if fail_after is not None and index >= fail_after:
                raise OSError("injected materialization failure")
            if read_confined_bytes(project, root, target) == content:
                if ownership_created:
                    raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "target appeared after ownership preflight")
                files.append(_file_row(file_id, path, content, frozen, automation_digest, review_digest, "MATERIALIZED"))
                continue
            created, identity = create_confined_bytes_exclusive(project, root, target, content, return_identity=True)
            if not created or identity is None or read_confined_bytes(project, root, target) != content:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "exclusive materialization readback failed")
            row = _file_row(file_id, path, content, frozen, automation_digest, review_digest, "MATERIALIZED")
            files.append(row)
            written.append((row, content, target, identity))
            if interrupt_after is not None and len(written) >= interrupt_after:
                raise GeneratedDeltaError("MATERIALIZATION_INTERRUPTED", "injected interruption before generated-delta publication")
    except GeneratedDeltaError as error:
        if error.code == "MATERIALIZATION_INTERRUPTED":
            raise
        # A preflight conflict cannot have produced a pipeline delta. A later
        # create/readback race may have followed prior writes, and is therefore
        # handled below as incomplete materialization with safe cleanup.
        if not written:
            raise
        failed = True
    except (OSError, OutputConfinementError):
        failed = True
    if failed:
        for file_id, path, content, _ in planned[len(files) :]:
            row = _file_row(file_id, path, content, frozen, automation_digest, review_digest, "NOT_MATERIALIZED")
            files.append(row)
        delta = _delta(
            project=project, module=module, test_root=test_root, baseline=frozen, automation_digest=automation_digest, review_digest=review_digest, effective_canonical_digest=effective["document_digest"], effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"], files=files,
            facts={"completion": "PARTIAL", "verification": "NOT_APPLICABLE", "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False},
        )
    else:
        delta = _delta(
        project=project, module=module, test_root=test_root, baseline=frozen, automation_digest=automation_digest, review_digest=review_digest, effective_canonical_digest=effective["document_digest"], effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"], files=files,
        facts={"completion": "COMPLETE", "verification": None, "accepted": None},
    )
    try:
        from tools.pilot_state import publish_generated_delta

        receipt = publish_generated_delta(run_root, attempt_id, delta)
    except (ValueError, OSError) as error:
        # The project write may now be uncertain.  Do not pretend it was absent;
        # preserve bytes for operator inspection and report a hard conflict.
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated delta receipt publication is uncertain") from error
    if receipt.get("record", {}).get("delta") != delta:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated delta receipt readback mismatch")
    if failed:
        requested = {
            row["path"]: ("CLEANED" if row.get("materialization") == "MATERIALIZED" else "NOT_MATERIALIZED")
            for row in delta["files"]
        }
        apply_dispositions(project, delta, requested, run_root=run_root, attempt_id=attempt_id, verification="NOT_APPLICABLE")
    return delta


def _verify_delta_digest(delta: Mapping[str, Any]) -> bool:
    actual = delta.get("digest")
    return isinstance(actual, str) and _seal(delta).get("digest") == actual


def _delta_root(project: Path, delta: Mapping[str, Any]) -> tuple[Path, Path]:
    module_name, test_root = delta.get("module"), delta.get("test_root")
    if not isinstance(module_name, str) or not isinstance(test_root, str):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "delta lacks selected module/test root")
    raw_module = Path(module_name)
    if raw_module.is_absolute() or ".." in raw_module.parts:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "delta module escapes the selected project")
    module = Path(project).resolve() / raw_module
    try:
        module.resolve().relative_to(Path(project).resolve())
    except ValueError as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "delta module escapes the selected project") from error
    return module, module / test_root


def inspect_delta(project: Path, delta: Mapping[str, Any]) -> dict[str, Any]:
    """Re-read every materialized/retained file; no receipt carries raw content."""
    if not _verify_delta_digest(delta):
        return {"valid": False, "reason_code": "DELTA_DIGEST_MISMATCH"}
    try:
        module, root = _delta_root(project, delta)
        for row in delta.get("files", []):
            if row.get("materialization") != "MATERIALIZED":
                continue
            path, expected = row.get("path"), row.get("content_digest")
            if not isinstance(path, str) or not isinstance(expected, str):
                return {"valid": False, "reason_code": "DELTA_FILE_INVALID"}
            content = read_confined_bytes(Path(project), root, _target(module, delta["test_root"], path))
            if content is None or _digest(content) != expected:
                return {"valid": False, "reason_code": "CONTENT_DRIFT"}
    except (GeneratedDeltaError, OutputConfinementError):
        return {"valid": False, "reason_code": "DELTA_CONFINEMENT_INVALID"}
    return {"valid": True}


def inspect_attempt_delta(project: Path, delta: Mapping[str, Any], *, run_root: Path | None = None, attempt_id: str | None = None) -> dict[str, Any]:
    """Public controller seam for exact owned/drift planning from a durable delta."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "controller-owned run and attempt coordinates are required")
    owned = _read_durable_delta(run_root, attempt_id, delta)
    if not _verify_delta_digest(owned):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "durable delta digest does not verify")
    module, root = _delta_root(project, owned)
    files: list[dict[str, str]] = []
    for row in owned.get("files", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "durable delta file is invalid")
        if row.get("materialization") == "NOT_MATERIALIZED":
            files.append({"path": row["path"], "state": "NOT_MATERIALIZED"})
            continue
        try:
            current = read_confined_bytes(Path(project), root, _target(module, owned["test_root"], row["path"]))
        except OutputConfinementError:
            files.append({"path": row["path"], "state": "OWNERSHIP_CONFLICT", "reason_code": "CONFINEMENT_INVALID"})
            continue
        if current is None:
            files.append({"path": row["path"], "state": "OWNERSHIP_CONFLICT", "reason_code": "FILE_MISSING"})
        elif _digest(current) == row.get("content_digest"):
            files.append({"path": row["path"], "state": "UNCHANGED_OWNED"})
        else:
            files.append({"path": row["path"], "state": "CONTENT_DRIFT", "reason_code": "CONTENT_CONFLICT"})
    return {"valid": True, "generated_delta_digest": owned["digest"], "files": files}


def execution_readiness(project: Path, delta: Mapping[str, Any], run_root: Path, attempt_id: str) -> dict[str, Any]:
    """Fail closed unless a complete attempt-owned delta still has exact bytes."""
    try:
        durable = _read_durable_delta(run_root, attempt_id, delta)
    except (GeneratedDeltaError, ValueError):
        return {"ready": False, "reason_code": "DURABLE_DELTA_UNAVAILABLE"}
    facts = durable.get("facts")
    if not isinstance(facts, Mapping) or facts.get("completion") != "COMPLETE":
        return {"ready": False, "reason_code": "DELTA_NOT_COMPLETE"}
    files = durable.get("files")
    if not isinstance(files, list) or not files or any(not isinstance(row, Mapping) or row.get("materialization") != "MATERIALIZED" for row in files):
        return {"ready": False, "reason_code": "MATERIALIZATION_INCOMPLETE"}
    if inspect_delta(project, durable) != {"valid": True}:
        return {"ready": False, "reason_code": "MATERIALIZATION_BYTES_MISMATCH"}
    return {"ready": True}


QUARANTINE_REASON_CODE = "FAILED_METHODS_QUARANTINED"


def resolve_disposition_policy(
    verification: str,
    materialization: str,
    *,
    retain_pass: bool,
    keep_deselected: bool = False,
    quarantine: str | None = None,
) -> tuple[str, str, Mapping[str, frozenset[tuple[str, str | None]]]]:
    """Return the only permitted plan and its legal durable outcomes.

    ``keep_deselected`` is the NOT_RUNNABLE/TESTS_DESELECTED branch: the reviewed
    tests are valid and were only skipped by the project's pytest configuration,
    so an exact file stays in place with reason ``TESTS_DESELECTED``.

    ``quarantine`` is the opt-in disposition policy after an authoritative FAIL
    (contract amendment A4): ``RETAIN`` for a file whose methods all passed,
    ``QUARANTINE`` for a file with failed methods, which the package marks.
    """
    if quarantine is not None:
        if quarantine not in {"RETAIN", "QUARANTINE"} or verification != "FAIL" or materialization != "MATERIALIZED" or retain_pass or keep_deselected:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "invalid quarantine policy input")
        conflict = frozenset({("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")})
        if quarantine == "RETAIN":
            exact = frozenset({("RETAINED", None), ("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")})
            return "RETAIN_IF_EXACT", "RETAINED", {"EXACT": exact, "MISSING": conflict, "DRIFT": conflict, "UNSAFE": conflict}
        # An interrupted effect leaves either the exact bytes or the plan's own quarantined bytes: both end QUARANTINED.
        exact = frozenset({("QUARANTINED", QUARANTINE_REASON_CODE), ("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")})
        return "QUARANTINE_IF_EXACT", "QUARANTINED", {"EXACT": exact, "MISSING": conflict, "DRIFT": conflict, "UNSAFE": conflict}
    if type(retain_pass) is not bool or verification != "PASS" and retain_pass:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "invalid disposition policy input")
    if type(keep_deselected) is not bool or verification != "NOT_RUNNABLE" and keep_deselected:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "invalid disposition policy input")
    if materialization == "NOT_MATERIALIZED":
        operation, requested = "NO_FILE", "NOT_MATERIALIZED"
        return operation, requested, {"NOT_APPLICABLE": frozenset({("NOT_MATERIALIZED", None)})}
    if materialization != "MATERIALIZED":
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "invalid materialization state")
    if verification == "PASS":
        operation, requested = ("RETAIN_IF_EXACT", "RETAINED") if retain_pass else ("CLEAN_IF_EXACT", "CLEANED")
    elif keep_deselected:
        operation, requested = "RETAIN_IF_EXACT", "RETAINED"
    else:
        try:
            operation, requested = {
                "FAIL": ("CLEAN_IF_EXACT", "CLEANED"),
                "NOT_RUNNABLE": ("CLEAN_IF_EXACT", "CLEANED"),
                "NOT_APPLICABLE": ("CLEAN_IF_EXACT", "CLEANED"),
                "UNKNOWN": ("PRESERVE_UNKNOWN", "PRESERVED_EXECUTION_UNKNOWN"),
            }[verification]
        except KeyError as error:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "unknown verification") from error
    exact = {
        "RETAIN_IF_EXACT": frozenset({("RETAINED", "TESTS_DESELECTED" if keep_deselected else None), ("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")}),
        "CLEAN_IF_EXACT": frozenset({("CLEANED", None), ("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT"), ("PRESERVED_CLEANUP_CONFLICT", "CLEANUP_CONFLICT")}),
        "PRESERVE_UNKNOWN": frozenset({("PRESERVED_EXECUTION_UNKNOWN", "EXECUTION_UNKNOWN"), ("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")}),
    }[operation]
    conflict = frozenset({("PRESERVED_CONTENT_CONFLICT", "CONTENT_CONFLICT")})
    return operation, requested, {"EXACT": exact, "MISSING": conflict, "DRIFT": conflict, "UNSAFE": conflict}


def quarantine_modes(authorization: Mapping[str, Any], verification: str, execution_payload: Mapping[str, Any] | None) -> dict[str, dict[str, str]] | None:
    """``{file_id: {symbol_id: FAILED|ERROR}}`` of failed methods under the quarantine policy, else None.

    A file that is absent from the map had no failed method and is retained.
    """
    if authorization.get("disposition_policy") != "quarantine" or verification != "FAIL" or not isinstance(execution_payload, Mapping):
        return None
    failed: dict[str, dict[str, str]] = {}
    for row in execution_payload.get("execution_evidence") or []:
        if isinstance(row, Mapping) and row.get("status") in {"FAILED", "ERROR"}:
            failed.setdefault(str(row.get("file_id")), {})[str(row.get("symbol_id"))] = str(row["status"])
    return failed


def quarantined_bytes(content: bytes, path: str, failed: Mapping[str, str], symbols: Sequence[Mapping[str, Any]], run_id: str) -> bytes:
    """The file with each failed method marked (``tools.quarantine``); deterministic for the same inputs."""
    from tools.quarantine import quarantine

    locators = {str(row["symbol_id"]): row["locator"] for row in symbols}
    methods = [(locators[symbol_id], "ASSERTION_FAILED" if status == "FAILED" else "TEST_ERROR", f"run {run_id[:8]} {symbol_id}")
               for symbol_id, status in sorted(failed.items()) if symbol_id in locators]
    if len(methods) != len(failed):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "a failed method has no reviewed locator")
    return quarantine(path, content.decode("utf-8"), methods).encode("utf-8")


def _quarantine_inputs(run_root: Path, attempt_id: str, verification: str, execution_payload: Mapping[str, Any] | None) -> tuple[dict[str, dict[str, str]] | None, list[Mapping[str, Any]]]:
    from tools.pilot_state import read_execution_inputs, read_run

    if verification != "FAIL" or read_run(run_root)["authorization"].get("disposition_policy") != "quarantine":
        return None, []
    modes = quarantine_modes(read_run(run_root)["authorization"], verification, execution_payload)
    if modes is None:
        return None, []
    automation = read_execution_inputs(run_root, attempt_id)["automation_artifact"]
    return modes, list((automation.get("artifacts") or {}).get("generated_symbols") or [])


@_run_operation
def apply_dispositions(
    project: Path, delta: Mapping[str, Any], requested: Mapping[str, str], *,
    run_root: Path | None = None, attempt_id: str | None = None, verification: str | None = None,
    execution_unknown_evidence_digest: str | None = None, interrupt_after_effects: int | None = None, interrupt_after_plan: bool = False,
) -> dict[str, Any]:
    """Record physical dispositions from one durable receipt; impossible choices fail closed."""
    if not isinstance(run_root, Path) or not isinstance(attempt_id, str) or verification not in {"PASS", "FAIL", "UNKNOWN", "NOT_RUNNABLE", "NOT_APPLICABLE"}:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "durable attempt coordinates and verification are required")
    if verification == "UNKNOWN":
        if not isinstance(execution_unknown_evidence_digest, str) or not _SHA256.fullmatch(execution_unknown_evidence_digest):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "UNKNOWN disposition requires execution-unknown evidence")
    elif execution_unknown_evidence_digest is not None:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "execution-unknown evidence is only valid for UNKNOWN")
    result = _read_durable_delta(run_root, attempt_id, delta)
    if not _verify_delta_digest(result):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "delta digest does not verify")
    if verification == "NOT_APPLICABLE" and result.get("facts") != {
        "completion": "PARTIAL", "verification": "NOT_APPLICABLE",
        "reason_code": "MATERIALIZATION_INCOMPLETE", "accepted": False,
    }:
        raise GeneratedDeltaError(
            "MATERIALIZATION_CONFLICT",
            "NOT_APPLICABLE cleanup requires partial materialization facts",
        )
    generated_delta_digest = result["digest"]
    durable_files = result.get("files")
    paths = [row.get("path") for row in durable_files if isinstance(row, Mapping)] if isinstance(durable_files, list) else []
    if not paths or not all(isinstance(path, str) for path in paths) or len({path.casefold() for path in paths}) != len(paths) or set(requested) != set(paths):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "a disposition is required for every generated file")
    modes: dict[str, dict[str, str]] | None = None
    quarantine_symbols: list[Mapping[str, Any]] = []
    from tools.pilot_state import _sealed as _seal_receipt, publish_phase5_receipt, read_attempt_receipt

    execution_payload = None
    if verification in {"FAIL", "NOT_RUNNABLE"}:
        try:
            execution_record = read_attempt_receipt(
                run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
            )["record"]
            execution_payload = execution_record["payload"]
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise GeneratedDeltaError(
                "MATERIALIZATION_CONFLICT",
                "execution-derived cleanup requires a read-back execution receipt",
            ) from error
        modes, quarantine_symbols = _quarantine_inputs(run_root, attempt_id, verification, execution_payload)
        deselected_rows = [
            row for row in execution_payload.get("process_evidence", ())
            if isinstance(row, Mapping) and row.get("kind") == "TESTS_DESELECTED"
        ] if isinstance(execution_payload, Mapping) else []
        if (
            not isinstance(execution_payload, Mapping)
            or execution_payload.get("verdict") != verification
            or execution_record.get("generated_delta_digest") != generated_delta_digest
            # Keeping a NOT_RUNNABLE file is legal only for a recorded deselection;
            # keeping a FAIL file only under the quarantine policy (decided per file below).
            or verification == "NOT_RUNNABLE" and any(requested[path] == "RETAINED" for path in paths) and not deselected_rows
            or verification == "FAIL" and any(requested[path] in {"RETAINED", "QUARANTINED"} for path in paths) and modes is None
        ):
            raise GeneratedDeltaError(
                "MATERIALIZATION_CONFLICT",
                "execution receipt does not authorize this cleanup outcome",
            )
    base_plan_rows: list[dict[str, Any]] = []
    for row in durable_files:
        if not isinstance(row, Mapping):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "durable generated file is invalid")
        path = row.get("path")
        if not isinstance(path, str):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "durable generated file path is invalid")
        mode = None
        if modes is not None and row.get("materialization") == "MATERIALIZED":
            mode = "QUARANTINE" if modes.get(str(row.get("file_id"))) else "RETAIN"
        operation, expected, _outcomes = resolve_disposition_policy(
            verification,
            str(row.get("materialization")),
            retain_pass=verification == "PASS" and requested[path] == "RETAINED",
            keep_deselected=verification == "NOT_RUNNABLE" and requested[path] == "RETAINED",
            quarantine=mode,
        )
        if requested[path] != expected:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition is impossible for verification outcome")
        base_plan_rows.append({
            key: row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")
        } | {"operation": operation, "requested_disposition": expected}
                              | ({"quarantine_symbols": sorted(modes[str(row.get("file_id"))])} if operation == "QUARANTINE_IF_EXACT" else {}))

    module, root = _delta_root(project, result)

    def fixed_exists(kind: str) -> bool:
        directory = {"disposition-plan": "disposition-plans", "disposition-receipt": "disposition-receipts"}[kind]
        return (run_root / directory / f"{attempt_id}.json").exists()

    try:
        existing_plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    except ValueError:
        if fixed_exists("disposition-plan"):
            try:
                from tools.pilot_state import recover_phase5_receipt_events

                existing_plan = recover_phase5_receipt_events(run_root, attempt_id, "disposition-plan")["record"]["payload"]
            except (KeyError, TypeError, ValueError) as recovery_error:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition plan is invalid or unbound") from recovery_error
        else:
            existing_plan = None
    if existing_plan is not None:
        plan_rows = existing_plan.get("files")
        if not isinstance(plan_rows, list) or existing_plan.get("generated_delta_digest") != generated_delta_digest or existing_plan.get("verification") != verification or existing_plan.get("execution_unknown_evidence_digest") != execution_unknown_evidence_digest or len(plan_rows) != len(base_plan_rows) or any(
            not isinstance(actual, Mapping) or any(actual.get(key) != expected.get(key) for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent", "operation", "requested_disposition", "quarantine_symbols"))
            for actual, expected in zip(plan_rows, base_plan_rows)
        ):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition replay facts conflict")
        plan = existing_plan
        durable_plan = existing_plan
    else:
        plan_rows = []
        for row in base_plan_rows:
            if row["operation"] == "NO_FILE":
                pre_effect_state = "NOT_APPLICABLE"
            else:
                target = _target(module, result["test_root"], row["path"])
                try:
                    current = read_confined_bytes(Path(project), root, target)
                except OutputConfinementError:
                    pre_effect_state = "UNSAFE"
                else:
                    pre_effect_state = "MISSING" if current is None else ("EXACT" if _digest(current) == row["content_digest"] else "DRIFT")
            extra: dict[str, Any] = {}
            if row["operation"] == "QUARANTINE_IF_EXACT" and pre_effect_state == "EXACT":
                failed = modes[str(row["file_id"])]
                extra["quarantined_content_digest"] = _digest(quarantined_bytes(current, str(row["path"]), failed, quarantine_symbols, run_root.name))
            plan_rows.append(dict(row) | {"pre_effect_state": pre_effect_state} | extra)
        plan_body: dict[str, Any] = {
            "schema_version": "1.0.0", "stage": "disposition-plan", "generated_delta_digest": generated_delta_digest,
            "verification": verification, "files": plan_rows,
        }
        if execution_unknown_evidence_digest is not None:
            plan_body["execution_unknown_evidence_digest"] = execution_unknown_evidence_digest
        plan = _seal_receipt(plan_body)
        try:
            published_plan = publish_phase5_receipt(run_root, attempt_id, "disposition-plan", plan)
            durable_plan = published_plan["record"]["payload"]
            if durable_plan != plan:
                raise ValueError("disposition plan read-back mismatch")
        except (ValueError, OSError) as error:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition plan publication is uncertain") from error
    if interrupt_after_plan:
        raise GeneratedDeltaError("MATERIALIZATION_INTERRUPTED", "injected interruption after disposition plan readback")

    try:
        final = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    except ValueError:
        if fixed_exists("disposition-receipt"):
            try:
                from tools.pilot_state import recover_phase5_receipt_events

                final = recover_phase5_receipt_events(run_root, attempt_id, "disposition-receipt")["record"]["payload"]
            except (KeyError, TypeError, ValueError) as recovery_error:
                raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "final disposition receipt is invalid or unbound") from recovery_error
        else:
            final = None
    if final is not None:
        try:
            prior_plan = read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
        except ValueError as error:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "final disposition receipt lacks its durable plan") from error
        if prior_plan != plan or final.get("disposition_plan_digest") != plan["digest"] or final.get("generated_delta_digest") != generated_delta_digest or final.get("verification") != verification:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition replay facts conflict")
        if final.get("execution_unknown_evidence_digest") != execution_unknown_evidence_digest:
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition replay evidence conflicts")
        final_rows = final.get("files")
        if not isinstance(final_rows, list) or len(final_rows) != len(plan_rows) or any(
            not isinstance(row, Mapping) or any(row.get(key) != plan_row.get(key) for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent"))
            for row, plan_row in zip(final_rows, plan_rows)
        ):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "final disposition receipt does not cover the generated set")
        return _merged_disposition_projection(result, final_rows)
    final_rows: list[dict[str, Any]] = []
    effects = 0
    for plan_row in plan_rows:
        final_row = {key: plan_row[key] for key in ("file_id", "path", "content_digest", "materialization", "ownership_digest", "baseline_absent")}
        if plan_row["operation"] == "NO_FILE":
            final_rows.append(final_row | {"disposition": "NOT_MATERIALIZED"})
            continue
        if plan_row["operation"] == "QUARANTINE_IF_EXACT":
            final_rows.append(final_row | _quarantine_effect(Path(project), root, _target(module, result["test_root"], plan_row["path"]), plan_row,
                                                             modes, quarantine_symbols, run_root.name))
            continue
        if plan_row.get("pre_effect_state") != "EXACT":
            final_rows.append(final_row | {"disposition": "PRESERVED_CONTENT_CONFLICT", "reason_code": "CONTENT_CONFLICT"})
            continue
        target = _target(module, result["test_root"], plan_row["path"])
        try:
            current = read_confined_bytes(Path(project), root, target)
        except OutputConfinementError:
            final_rows.append(final_row | {"disposition": "PRESERVED_CONTENT_CONFLICT", "reason_code": "CONTENT_CONFLICT"})
            continue
        exact = current is not None and _digest(current) == plan_row["content_digest"]
        if plan_row["operation"] == "PRESERVE_UNKNOWN":
            if exact:
                final_rows.append(final_row | {"disposition": "PRESERVED_EXECUTION_UNKNOWN", "reason_code": "EXECUTION_UNKNOWN"})
            else:
                final_rows.append(final_row | {"disposition": "PRESERVED_CONTENT_CONFLICT", "reason_code": "CONTENT_CONFLICT"})
            continue
        if plan_row["operation"] == "RETAIN_IF_EXACT":
            retained = {"disposition": "RETAINED"} | ({"reason_code": "TESTS_DESELECTED"} if verification == "NOT_RUNNABLE" else {})
            final_rows.append(final_row | (retained if exact else {"disposition": "PRESERVED_CONTENT_CONFLICT", "reason_code": "CONTENT_CONFLICT"}))
            continue
        if current is None:
            final_rows.append(final_row | {"disposition": "CLEANED"})
            continue
        if not exact:
            final_rows.append(final_row | {"disposition": "PRESERVED_CONTENT_CONFLICT", "reason_code": "CONTENT_CONFLICT"})
            continue
        try:
            removed = remove_confined_bytes_if_equal(Path(project), root, target, current)
        except (OSError, OutputConfinementError):
            removed = False
        if removed:
            final_rows.append(final_row | {"disposition": "CLEANED"})
            effects += 1
            if interrupt_after_effects is not None and effects >= interrupt_after_effects:
                raise GeneratedDeltaError("MATERIALIZATION_INTERRUPTED", "injected interruption after disposition effect")
        else:
            final_rows.append(final_row | {"disposition": "PRESERVED_CLEANUP_CONFLICT", "reason_code": "CLEANUP_CONFLICT"})
    final_body: dict[str, Any] = {
        "schema_version": "1.0.0", "stage": "dispositions", "disposition_plan_digest": durable_plan["digest"],
        "generated_delta_digest": generated_delta_digest, "verification": verification, "files": final_rows,
    }
    if execution_unknown_evidence_digest is not None:
        final_body["execution_unknown_evidence_digest"] = execution_unknown_evidence_digest
    final = _seal_receipt(final_body)
    try:
        published = publish_phase5_receipt(run_root, attempt_id, "disposition-receipt", final)
        if published["record"]["payload"] != final or read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"] != final:
            raise ValueError("disposition receipt read-back mismatch")
    except (ValueError, OSError) as error:
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition receipt publication is uncertain") from error
    return _merged_disposition_projection(result, final_rows)


def _quarantine_effect(project: Path, root: Path, target: Path, plan_row: Mapping[str, Any], modes: Mapping[str, Mapping[str, str]] | None,
                       symbols: Sequence[Mapping[str, Any]], run_id: str) -> dict[str, Any]:
    """Mark the failed methods of one exact file; the plan fixed the resulting digest beforehand."""
    from tools.confined_output import atomic_write_confined_bytes_at_root

    conflict = {"disposition": "PRESERVED_CONTENT_CONFLICT", "reason_code": "CONTENT_CONFLICT"}
    expected = plan_row.get("quarantined_content_digest")
    if not isinstance(expected, str) or modes is None:
        return conflict
    try:
        current = read_confined_bytes(project, root, target)
    except OutputConfinementError:
        return conflict
    if current is not None and _digest(current) == expected:
        return {"disposition": "QUARANTINED", "reason_code": QUARANTINE_REASON_CODE}
    if current is None or _digest(current) != plan_row["content_digest"]:
        return conflict
    data = quarantined_bytes(current, str(plan_row["path"]), modes[str(plan_row["file_id"])], symbols, run_id)
    if _digest(data) != expected:
        return conflict
    try:
        atomic_write_confined_bytes_at_root(project, root, target, data)
        written = read_confined_bytes(project, root, target)
    except (OSError, OutputConfinementError):
        return conflict
    return {"disposition": "QUARANTINED", "reason_code": QUARANTINE_REASON_CODE} if written is not None and _digest(written) == expected else conflict


def _merged_disposition_projection(delta: Mapping[str, Any], final_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return the legacy delta-shaped projection without altering its durable receipt."""
    result = json.loads(json.dumps(delta))
    files = result.get("files")
    if not isinstance(files, list) or len(files) != len(final_rows):
        raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition projection is incomplete")
    for row, final in zip(files, final_rows):
        if not isinstance(row, dict) or not isinstance(final, Mapping) or row.get("path") != final.get("path"):
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "disposition projection is mismatched")
        row["disposition"] = final["disposition"]
        if "reason_code" in final:
            row["reason_code"] = final["reason_code"]
    result.pop("digest", None)
    return _seal(result)
