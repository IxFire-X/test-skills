"""Validation and normalization for portable ``.skillsrc`` manifests."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping, Sequence

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


_TEST_LEVELS = ("in-process", "stand")


def module_test_level(module: Mapping[str, Any]) -> tuple[str, str] | None:
    """P6b: ``(level, source)`` of a module's autotests — ``declared`` by ``test.level``, else ``derived`` by the old rule (a module that
    reads the sources of an application it does not start, ``paths.context``, is a ``stand``; any other is ``in-process``).  None when
    the module has no ``test`` block (documents): there is nothing to run, so no level."""
    test = module.get("test")
    if not isinstance(test, Mapping):
        return None
    declared = test.get("level")
    if declared in _TEST_LEVELS:
        return str(declared), "declared"
    return ("stand" if (module.get("paths") or {}).get("context") else "in-process"), "derived"


def level_derived_note(module: Mapping[str, Any]) -> tuple[str, str] | None:
    """P6b: ``(what, fix)`` when the level of the module's autotests is derived, not declared — the fix names the key and the value to write."""
    found = module_test_level(module)
    if found is None or found[1] != "derived":
        return None
    value, name = found[0], module.get("id")
    why = "есть paths.context: исходники приложения лежат отдельно" if value == "stand" else "paths.context нет"
    return f"уровень автотестов модуля {name} выведен ({value}: {why})", f"укажите в .skillsrc у модуля {name} test.level: {value}"


def _validate_test_levels(document: Mapping[str, Any]) -> None:
    """P6b: a declared ``test.level`` must agree with the keys that say the same: ``stand`` has the address of its stand
    (``service_url_env``), ``in-process`` has neither it nor ``paths.context``.  A derived level is never refused here."""
    for module in document.get("modules") or []:
        if not isinstance(module, Mapping) or not isinstance(module.get("test"), Mapping) or module["test"].get("level") not in _TEST_LEVELS:
            continue
        level, name = module["test"]["level"], module.get("id")
        has_env, has_context = bool(module.get("service_url_env")), bool((module.get("paths") or {}).get("context"))
        if level == "stand" and not has_env:
            raise SkillsrcError("test_level_conflict", f"модуль {name}: test.level: stand, но service_url_env не задан",
                                [{"path": "/modules", "id": name}],
                                fix=f"у модуля {name} укажите service_url_env: <ИМЯ> — переменную окружения с адресом поднятого стенда, "
                                    "или test.level: in-process, если стенд не нужен")
        if level == "in-process" and (has_env or has_context):
            keys = " и ".join(key for key, present in (("service_url_env", has_env), ("paths.context", has_context)) if present)
            raise SkillsrcError("test_level_conflict", f"модуль {name}: test.level: in-process, но заданы {keys}",
                                [{"path": "/modules", "id": name}],
                                fix=f"у модуля {name} уберите {keys} или укажите test.level: stand (с service_url_env)")


def skillsrc_file_problem(path: Path) -> str | None:
    """F5: why *path* cannot be read as ``.skillsrc`` — a directory, a junction or a link (the file must lie in the project itself),
    or an unreadable one; None when it is absent or a regular readable file."""
    import stat

    try:
        details = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        return f".skillsrc не читается ({type(error).__name__})"
    if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400 or not stat.S_ISREG(details.st_mode):
        return ".skillsrc — не обычный файл (каталог, junction или ссылка)"
    try:
        with open(path, "rb"):
            pass
    except OSError as error:
        return f".skillsrc не читается ({type(error).__name__})"
    return None


COMMENTS_MESSAGE = ".skillsrc — JSON с комментариями: JSON их не допускает"
COMMENTS_FIX = ("уберите комментарии (`//…` и `/* … */`) из .skillsrc, остальное оставьте как есть: файл не YAML, переименовывать его и переносить "
                "из него нечего")


def _strip_json_comments(text: str) -> str:
    """``text`` without the ``//…`` and ``/* … */`` comments that stand outside the strings; one pass."""
    out: list[str] = []
    position, size, in_string = 0, len(text), False
    while position < size:
        char = text[position]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(text[position + 1:position + 2])
                position += 1
            elif char == '"':
                in_string = False
            position += 1
        elif char == '"':
            in_string = True
            out.append(char)
            position += 1
        elif text.startswith("//", position):
            end = text.find("\n", position)
            position = size if end < 0 else end
        elif text.startswith("/*", position):
            end = text.find("*/", position + 2)
            position = size if end < 0 else end + 2
            out.append(" ")
        else:
            out.append(char)
            position += 1
    return "".join(out)


def json_with_comments(text: str) -> bool:
    """V9b (8.15): a text that is JSON once its comments are cut — it has some, and what is left opens an object or an array.  It is no YAML:
    the first character alone (``{``) does not tell, and a file that begins with ``//`` is not a YAML one."""
    source = text.removeprefix("\ufeff")
    stripped = _strip_json_comments(source)
    return stripped != source and stripped.lstrip()[:1] in ("{", "[")


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


def _fix_text(cause: str, project: Path | None) -> str:
    """F5: every refusal of a foreign ``.skillsrc`` says what is wrong (the key or the line) and how to get a valid one."""
    import sys

    where = f'"{project}"' if project is not None else "<проект>"
    return (f"причина: {cause}. Исправьте этот файл по схеме или переименуйте его в .skillsrc.bak и создайте новый: "
            f'"{sys.executable}" -m tools.init_skillsrc --project {where} --write (значения limits, review и другие перенесите из .bak)')


def _schema_cause(details: Sequence[Mapping[str, Any]]) -> str:
    """The first schema message that names a key or a type; the ones that repeat the whole document are left out."""
    for row in details:
        message = str(row.get("message", ""))
        if message and not message.startswith(("{", "[")):
            path = str(row.get("path", "/"))
            return f"{path if path != '/' else 'верхний уровень'}: {message[:200]}"
    return "файл не по схеме .skillsrc"


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
            fix=_fix_text("файл не в UTF-8", project),
        ) from error
    try:
        document = loads_json_strict(source.removeprefix("\ufeff"))
    except StrictJsonError as error:
        if json_with_comments(source):
            raise SkillsrcError("invalid_json", COMMENTS_MESSAGE, [{"path": "/", "message": str(error)}], fix=COMMENTS_FIX) from error
        legacy = _legacy_yaml_row(project)
        cause = "файл пуст" if not source.strip() else f"это не JSON ({error})"
        raise SkillsrcError(
            "invalid_json",
            legacy["message"] if legacy else ".skillsrc must be one JSON object",
            [{"path": "/", "message": str(error)}],
            fix=legacy.get("fix") if legacy else _fix_text(cause, project),
        ) from error
    if not isinstance(document, dict):
        raise SkillsrcError("invalid_shape", ".skillsrc must be a mapping", fix=_fix_text("на верхнем уровне должен быть объект JSON {…}", project))

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        details = [{"path": "/" + "/".join(map(str, error.absolute_path)), "message": error.message} for error in errors]
        raise SkillsrcError("schema_invalid", ".skillsrc failed schema validation", details, fix=_fix_text(_schema_cause(details), project))
    _validate_unique_module_ids(document)
    _validate_document_paths(document)
    _validate_test_levels(document)
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
