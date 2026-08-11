"""Validation and normalization for portable ``.skillsrc`` manifests."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml
from jsonschema import Draft202012Validator


class SkillsrcError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        details: list[dict[str, object]] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.details = details or []


def load_skillsrc(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    """Load a YAML manifest and validate it against the bundled schema."""
    schema_path = schema_path or Path(__file__).resolve().parents[1] / "schemas" / "skillsrc.schema.json"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    return document


def normalize_skillsrc(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return the common project/module representation for v2 and v3 manifests."""
    if document.get("version") == "3.0":
        return {
            "project": dict(document["project"]),
            "modules": [_copy_module(module) for module in document["modules"]],
        }

    project = dict(document["project"])
    stack = {"language": project.pop("language")}
    for key in ("framework", "build_tool"):
        value = project.pop(key, None)
        if _known_optional(value):
            stack[key] = value
    if "methodology" in document:
        project["methodology"] = document["methodology"]

    module: dict[str, Any] = {"id": "root", "root": ".", "stack": stack}
    paths = _array_values(document.get("paths", {}))
    if paths:
        module["paths"] = paths
    test = {
        key: value
        for key, value in document.get("test", {}).items()
        if _known_optional(value)
    }
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
    matches = [module for module in modules if module["id"] == module_id]
    if len(matches) != 1:
        raise SkillsrcError("module_unknown", f"unknown module: {module_id}")
    return matches[0]


def resolve_module_root(project_root: Path, module: Mapping[str, Any]) -> Path:
    """Resolve a module root only when it remains confined to the project root."""
    root = module.get("root")
    if not isinstance(root, str) or not root:
        raise SkillsrcError("unsafe_module_root", "unsafe module root")
    if _unsafe_module_root(root):
        raise SkillsrcError("unsafe_module_root", "unsafe module root")

    resolved_project_root = project_root.resolve()
    resolved_module_root = (resolved_project_root / root).resolve()
    try:
        common = os.path.commonpath([str(resolved_project_root), str(resolved_module_root)])
    except ValueError as error:
        raise SkillsrcError("unsafe_module_root", "unsafe module root") from error
    if common != str(resolved_project_root):
        raise SkillsrcError("unsafe_module_root", "unsafe module root")
    return resolved_module_root


def _copy_module(module: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(module)
    for key in ("stack", "paths", "test", "feature_sources"):
        if key in copied:
            copied[key] = dict(copied[key])
    return copied


def _array_values(values: Mapping[str, Any]) -> dict[str, list[Any]]:
    return {
        key: [value]
        for key, value in values.items()
        if isinstance(value, str) and _known_optional(value)
    }


def _known_optional(value: Any) -> bool:
    return value is not None and value != "" and value != "unknown"


def _unsafe_module_root(root: str) -> bool:
    windows_path = PureWindowsPath(root)
    posix_path = PurePosixPath(root)
    if root.startswith(("/", "\\")):
        return True
    if windows_path.is_absolute() or windows_path.drive or posix_path.is_absolute():
        return True
    return ".." in windows_path.parts or ".." in posix_path.parts
