"""Validation and normalization for portable ``.skillsrc`` manifests."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

try:
    from tools.schema_engine import Draft202012Validator
    from tools.schema_validation import StrictJsonError, loads_json_strict
except ImportError:  # direct execution from tools/
    from schema_engine import Draft202012Validator
    from schema_validation import StrictJsonError, loads_json_strict


class SkillsrcError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, object]] | None = None,
        fix: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or []
        self.fix = fix


def load_skillsrc(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    """Load a JSON manifest and validate it against the bundled schema."""
    return parse_skillsrc_bytes(path.read_bytes(), schema_path, project=path.parent if path.name == ".skillsrc" else None)


def _legacy_yaml_row(project: Path | None) -> dict[str, Any] | None:
    """The row ``tools.ready`` gives an older YAML ``.skillsrc`` (message and fix, in Russian), or None when the file is not one."""
    if project is None:
        return None
    try:
        from tools.ready import check_skillsrc, legacy_yaml_keys

        return check_skillsrc(project)[0] if legacy_yaml_keys(project) is not None else None
    except (ImportError, OSError, IndexError):
        return None


def skillsrc_review_self(project: Path) -> bool:
    """P2 (item 7): ``.skillsrc`` ``review.self: true`` — the project admits a review in the same session at every run, so scan asks
    nothing.  A missing, unreadable or invalid manifest, a missing key or ``self: false`` is the question asked as before."""
    try:
        document = load_skillsrc(Path(project) / ".skillsrc")
    except (OSError, SkillsrcError):
        return False
    return (document.get("review") or {}).get("self") is True


def parse_skillsrc_bytes(data: bytes, schema_path: Path | None = None, *, project: Path | None = None) -> dict[str, Any]:
    """Parse and validate one exact UTF-8 manifest byte sequence; *project* is where the file lies, for the fix of an older YAML one."""
    schema_path = schema_path or Path(__file__).resolve().parents[1] / "schemas" / "skillsrc.schema.json"
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillsrcError(
            "invalid_encoding",
            ".skillsrc must be UTF-8",
            [{"path": "/", "message": str(error)}],
        ) from error
    try:
        document = loads_json_strict(source.removeprefix("\ufeff"))
    except StrictJsonError as error:
        legacy = _legacy_yaml_row(project)
        raise SkillsrcError(
            "invalid_json",
            legacy["message"] if legacy else ".skillsrc must be one JSON object (an older YAML .skillsrc is not read: tools.ready lists its keys to carry over)",
            [{"path": "/", "message": str(error)}],
            fix=legacy.get("fix") if legacy else None,
        ) from error
    if not isinstance(document, dict):
        raise SkillsrcError("invalid_shape", ".skillsrc must be a mapping")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        raise SkillsrcError(
            "schema_invalid",
            ".skillsrc failed schema validation",
            [
                {
                    "path": "/" + "/".join(map(str, error.absolute_path)),
                    "message": error.message,
                }
                for error in errors
            ],
        )
    _validate_unique_module_ids(document)
    _validate_document_paths(document)
    return document


def normalize_skillsrc(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the common project/module representation for v2 and v3 manifests."""
    _validate_unique_module_ids(document)
    _validate_document_paths(document)
    if document.get("version") == "3.0":
        project = deepcopy(document["project"])
        for key in ("version", "discovery", "resolution", "contracts", "skills_registry"):
            if key in document:
                project[key] = deepcopy(document[key])
        return {
            "project": project,
            "modules": deepcopy(document["modules"]),
        }

    project = deepcopy(dict(document["project"]))
    stack = {"language": project.pop("language")}
    for key in ("framework", "build_tool"):
        if key in project:
            stack[key] = project.pop(key)
    if "methodology" in document:
        project["methodology"] = deepcopy(document["methodology"])

    module: dict[str, Any] = {"id": "root", "root": ".", "stack": stack}
    paths = _array_values(document.get("paths", {}))
    if paths:
        module["paths"] = paths
    test = deepcopy(dict(document.get("test", {})))
    if test:
        module["test"] = test
    feature_sources = _array_values(document.get("sdd", {}))
    if feature_sources:
        module["feature_sources"] = feature_sources

    return {"project": project, "modules": [module]}


def select_module(normalized: Mapping[str, Any], module_id: str | None) -> dict[str, Any]:
    """Select a module, requiring an explicit ID for multi-module projects."""
    modules = list(normalized["modules"])
    if module_id is None and len(modules) == 1:
        return modules[0]
    if module_id is None:
        raise SkillsrcError("module_required", "module selection is required")
    matches = [(index, module) for index, module in enumerate(modules) if module["id"] == module_id]
    if not matches:
        from tools.module_ids import alias_match  # P6a: the id of the folder, in its current or old (hex) form

        alias = alias_match(modules, module_id)
        if alias is not None:
            return alias
        raise SkillsrcError("module_unknown", f"unknown module: {module_id}")
    if len(matches) > 1:
        raise SkillsrcError(
            "module_ambiguous",
            f"ambiguous module: {module_id}",
            [{"path": "/modules", "id": module_id, "indices": [index for index, _ in matches]}],
        )
    return matches[0][1]


def resolve_module_root(project_root: Path, module: Mapping[str, Any]) -> Path:
    """Resolve an existing module root confined without symlink/reparse hops."""
    root = module.get("root")
    problem = _portable_path_problem(root) if isinstance(root, str) and root else "invalid path"
    if problem:
        raise SkillsrcError(
            "unsafe_module_root",
            "unsafe module root",
            [{"path": "/root", "message": problem}],
        )

    resolved_project_root = project_root.resolve()
    candidate = resolved_project_root / root
    probe = resolved_project_root
    for part in Path(root).parts:
        probe = probe / part
        if _is_reparse(probe):
            raise SkillsrcError(
                "unsafe_module_root",
                "unsafe module root",
                [{"path": "/root", "message": "symlink or reparse hop is not allowed"}],
            )
    if not candidate.exists() or not candidate.is_dir():
        raise SkillsrcError(
            "unsafe_module_root",
            "unsafe module root",
            [{"path": "/root", "message": "module root must be an existing directory"}],
        )
    resolved_module_root = candidate.resolve()
    try:
        common = os.path.commonpath([str(resolved_project_root), str(resolved_module_root)])
    except ValueError as error:
        raise SkillsrcError(
            "unsafe_module_root",
            "unsafe module root",
            [{"path": "/root", "message": "path escapes project root"}],
        ) from error
    if common != str(resolved_project_root):
        raise SkillsrcError(
            "unsafe_module_root",
            "unsafe module root",
            [{"path": "/root", "message": "path escapes project root"}],
        )
    return resolved_module_root


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & 0x400)


def _validate_unique_module_ids(document: Mapping[str, Any]) -> None:
    if document.get("version") != "3.0":
        return
    modules = document.get("modules")
    if not isinstance(modules, list):
        return
    indices_by_id: dict[str, list[int]] = {}
    for index, module in enumerate(modules):
        if isinstance(module, Mapping) and isinstance(module.get("id"), str):
            indices_by_id.setdefault(module["id"], []).append(index)
    duplicates = [
        (module_id, indices)
        for module_id, indices in indices_by_id.items()
        if len(indices) > 1
    ]
    if duplicates:
        raise SkillsrcError(
            "duplicate_module_id",
            "duplicate module id",
            [
                {
                    "path": f"/modules/{indices[1]}/id",
                    "message": f"duplicate module id: {module_id}",
                    "id": module_id,
                    "indices": indices,
                }
                for module_id, indices in duplicates
            ],
        )


def _validate_document_paths(document: Mapping[str, Any]) -> None:
    details: list[dict[str, object]] = []
    if document.get("version") == "3.0":
        _validate_v3_paths(document, details)
    else:
        _validate_v2_paths(document, details)
    _validate_registry_paths(document, details)
    if details:
        raise SkillsrcError("unsafe_path", "unsafe path", details)


def _validate_v2_paths(document: Mapping[str, Any], details: list[dict[str, object]]) -> None:
    for group_name in ("paths", "sdd"):
        group = document.get(group_name)
        if not isinstance(group, Mapping):
            continue
        for key, value in group.items():
            _append_path_error(details, f"/{group_name}/{key}", value)


def _validate_v3_paths(document: Mapping[str, Any], details: list[dict[str, object]]) -> None:
    modules = document.get("modules")
    if not isinstance(modules, list):
        return
    for index, module in enumerate(modules):
        if not isinstance(module, Mapping):
            continue
        base = f"/modules/{index}"
        _append_path_error(details, f"{base}/root", module.get("root"))
        for group_name in ("paths", "feature_sources"):
            group = module.get(group_name)
            if not isinstance(group, Mapping):
                continue
            for key, values in group.items():
                if isinstance(values, list):
                    for value_index, value in enumerate(values):
                        _append_path_error(details, f"{base}/{group_name}/{key}/{value_index}", value)
        detected_from = module.get("detected_from")
        if isinstance(detected_from, list):
            for value_index, value in enumerate(detected_from):
                _append_path_error(details, f"{base}/detected_from/{value_index}", value)
        test = module.get("test")
        if isinstance(test, Mapping):
            for key in ("wrapper", "interpreter"):
                if key in test:
                    _append_path_error(details, f"{base}/test/{key}", test[key])


def _validate_registry_paths(document: Mapping[str, Any], details: list[dict[str, object]]) -> None:
    registry = document.get("skills_registry")
    if not isinstance(registry, Mapping):
        return
    for skill_id, skill in registry.items():
        if not isinstance(skill, Mapping):
            continue
        for key in ("path", "skill_file", "input_contract", "output_contract"):
            if key in skill:
                _append_path_error(details, f"/skills_registry/{skill_id}/{key}", skill[key])


def _append_path_error(details: list[dict[str, object]], path: str, value: Any) -> None:
    if not isinstance(value, str):
        return
    problem = _portable_path_problem(value)
    if problem:
        details.append({"path": path, "message": problem})


def _portable_path_problem(value: str) -> str | None:
    windows_path = PureWindowsPath(value)
    posix_path = PurePosixPath(value)
    if value.startswith(("/", "\\")):
        return "absolute or rooted path is not allowed"
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        return "absolute or drive-relative path is not allowed"
    if ".." in windows_path.parts or ".." in posix_path.parts:
        return "parent traversal is not allowed"
    return None


def _array_values(values: Mapping[str, Any]) -> dict[str, list[Any]]:
    return {key: deepcopy(value) if isinstance(value, list) else [deepcopy(value)] for key, value in values.items()}
