"""Living suite in the project and its manifest (wave 3, S).

The suite directory (``.skillsrc`` ``suite.path``, default ``test-cases/``) holds the canonical
document, its Markdown, HTML and Zephyr CSV projections and ``suite-manifest.json``
(``schemas/suite-manifest.schema.json``).  The tests stay in the module's test directory; the
manifest names each case's test methods and the digest of every method slice and of each file's
SUPPORT code, so a later run sees what a person edited (``verify``) and changes only what still
matches (contract amendment A5).

Everything here is deterministic: the same run gives the same bytes.  Writing is exclusive — an
existing different file is never overwritten (``write_suite`` with ``replace`` is for
``suite-update-v1``, and only for files whose bytes match the manifest).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tools.code_slices import SliceError, slice_file
from tools.schema_validation import schema_diagnostics

FORMAT_VERSION = "1.0.0"
DEFAULT_PATH = "test-cases/"
MANIFEST = "suite-manifest.json"
SUITE_FILES = {"canonical": "test-cases.json", "markdown": "test-cases.md", "html": "test-cases.html", "csv": "test-cases.zephyr-scale.csv"}
_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "suite-manifest.schema.json"
QUARANTINE_REASONS = ("ASSERTION_FAILED", "BEHAVIOR_CHANGED_WITHOUT_SPEC", "FLAKY", "ENVIRONMENT", "REPAIR_FAILED")


class SuiteError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def case_digest(case: Mapping[str, Any]) -> str:
    return sha256_bytes(json.dumps(case, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _lines_digest(lines: Iterable[str]) -> str:
    """Line endings and trailing spaces do not count; blank lines between members do not either."""
    kept = [line.rstrip("\r").rstrip() for line in lines]
    return sha256_bytes("\n".join(line for line in kept if line).encode("utf-8"))


def suite_directory(project: Path, skillsrc: Mapping[str, Any] | None) -> str:
    """The suite path relative to the project, with a trailing slash; confined to the project."""
    value = str(((skillsrc or {}).get("suite") or {}).get("path") or DEFAULT_PATH).replace("\\", "/")
    value = value.rstrip("/") + "/"
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value) or any(part in {"", ".", ".."} for part in value.rstrip("/").split("/")):
        raise SuiteError("SUITE_PATH_INVALID", f"suite.path must be a relative directory inside the project: {value}")
    target = (Path(project) / value).resolve()
    try:
        target.relative_to(Path(project).resolve())
    except ValueError as error:
        raise SuiteError("SUITE_PATH_INVALID", f"suite.path leaves the project: {value}") from error
    return value


def locator_text(locator: Mapping[str, Any]) -> str:
    kind = locator.get("kind")
    if kind == "java_class_method":
        return f"{locator['class_fqn']}#{locator['method_name']}"
    if kind == "python_module_function":
        return str(locator["function_name"])
    if kind == "python_class_method":
        return f"{locator['qualified_class_name']}.{locator['method_name']}"
    raise SuiteError("SUITE_LOCATOR", f"unsupported locator {kind}")


def file_language(path: str) -> str:
    return "python" if path.endswith(".py") else "java"


def slices_of(path: str, file_id: str, content: str, symbols: Sequence[Mapping[str, Any]]):
    return slice_file({"file_id": file_id, "path": path, "content": content, "language": file_language(path)}, symbols)


def method_digests(path: str, file_id: str, content: str, symbols: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], str]:
    """``{symbol_id: slice digest}`` and the SUPPORT digest of one test file."""
    slices = slices_of(path, file_id, content, symbols)
    digests = {symbol_id: _lines_digest(slices.lines[member.start - 1:member.end]) for symbol_id, member in slices.symbols.items()}
    support = [line for start, end in slices.support_ranges() for line in slices.lines[start - 1:end]]
    return digests, _lines_digest(support)


def projections(document: Mapping[str, Any]) -> dict[str, bytes]:
    """The suite's canonical JSON and its human projections (the same renderers as the bundle)."""
    from tools.publish_test_case_bundle import build_bundle

    bundle = build_bundle(dict(document))
    return {"canonical": bundle.json_bytes, "markdown": bundle.markdown_bytes, "html": bundle.preview_bytes, "csv": bundle.csv_bytes}


def requirement_links(document: Mapping[str, Any], identities: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """Canonical requirement ID → keyed source requirements (through ``source_to_canonical_mappings``)."""
    by_sreq = {row["source_requirement_id"]: row for row in identities}
    links: dict[str, list[Mapping[str, Any]]] = {}
    for mapping in document.get("source_to_canonical_mappings") or []:
        row = by_sreq.get(mapping.get("source_requirement_id"))
        if row is None:
            continue
        for creq in mapping.get("canonical_requirement_ids") or []:
            links.setdefault(creq, [])
            if row not in links[creq]:
                links[creq].append(row)
    return links


def build_manifest(*, suite_id: str, module_id: str, package_version: str, document: Mapping[str, Any], identities: Sequence[Mapping[str, Any]],
                   documents: Sequence[Mapping[str, str]], id_pattern: str | None, automation: Mapping[str, Any] | None,
                   test_files: Mapping[str, Mapping[str, str]], case_state: Mapping[str, Mapping[str, Any]] | None = None,
                   strength: Mapping[str, Mapping[str, Any]] | None = None, surface: Mapping[str, Any] | None,
                   suite_dir: str, suite_digests: Mapping[str, str], source_run: Mapping[str, Any], last_run: Mapping[str, Any] | None = None,
                   history: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The manifest of one suite.

    ``test_files`` maps ``file_id`` to ``{"path": project path, "content": current text}``;
    ``case_state`` gives per case ``status``, ``quarantine`` and ``last_green_run`` (default
    ``ACTIVE``, none, none); ``strength`` the mutation counts per case.
    """
    links = requirement_links(document, identities)
    artifacts = (automation or {}).get("artifacts") or {}
    symbols = {row["symbol_id"]: row for row in artifacts.get("generated_symbols") or []}
    by_file: dict[str, list[Mapping[str, Any]]] = {}
    for row in symbols.values():
        by_file.setdefault(row["file_id"], []).append(row)
    digests: dict[str, str] = {}
    files = []
    for file_id in sorted(by_file):
        if file_id not in test_files:
            raise SuiteError("SUITE_TEST_FILE_MISSING", f"generated file {file_id} is not in the project")
        path, content = test_files[file_id]["path"], test_files[file_id]["content"]
        try:
            method_rows, support = method_digests(path, file_id, content, by_file[file_id])
        except SliceError as error:
            raise SuiteError("SUITE_SLICE", f"{path}: {error}") from error
        digests.update(method_rows)
        files.append({"path": path, "file_id": file_id, "language": file_language(path),
                      "file_digest": sha256_bytes(content.encode("utf-8")), "support_digest": support})
    methods_of: dict[str, list[dict[str, Any]]] = {}
    for relation in artifacts.get("implementation_relations") or []:
        symbol = symbols.get(relation["symbol_id"])
        if symbol is None:
            continue
        row = {"file": test_files[symbol["file_id"]]["path"], "symbol_id": symbol["symbol_id"], "locator": locator_text(symbol["locator"]),
               "slice_digest": digests[symbol["symbol_id"]]}
        if row not in methods_of.setdefault(relation["case_id"], []):
            methods_of[relation["case_id"]].append(row)
    cases = []
    for case in document.get("test_cases") or []:
        keyed = []
        for creq in case.get("requirement_ids") or []:
            for row in links.get(creq, []):
                if row not in keyed:
                    keyed.append(row)
        state = dict((case_state or {}).get(case["case_id"]) or {})
        methods = sorted(methods_of.get(case["case_id"], []), key=lambda row: (row["file"], row["locator"]))
        cases.append({
            "case_id": case["case_id"], "title": str(case.get("title") or ""),
            "requirement_ids": list(case.get("requirement_ids") or []),
            "requirement_keys": sorted({row["key"] for row in keyed}),
            "requirement_text_digests": {row["key"]: row["text_digest"] for row in sorted(keyed, key=lambda item: item["key"])},
            "case_digest": case_digest(case), "automation": "AUTOMATED" if methods else "MANUAL", "methods": methods,
            "zephyr_key": state.get("zephyr_key"), "status": state.get("status", "ACTIVE"), "quarantine": state.get("quarantine"),
            "last_green_run": state.get("last_green_run"), "strength": (strength or {}).get(case["case_id"]),
        })
    manifest = {
        "format_version": FORMAT_VERSION, "package_version": package_version, "suite_id": suite_id, "module_id": module_id,
        "source_run": dict(source_run), "last_run": dict(last_run or source_run), "id_pattern": id_pattern,
        "documents": [{"path": row["path"], "sha256": row["sha256"]} for row in sorted(documents, key=lambda item: item["path"])],
        "requirements": [{name: row[name] for name in ("key", "source_requirement_id", "path", "title", "explicit_id", "text_digest")} for row in identities],
        "cases": sorted(cases, key=lambda row: row["case_id"]),
        "files": files,
        "code_surface": None if surface is None else {"endpoints": [dict(row) for row in surface["endpoints"]], "digest": surface["digest"]},
        "suite_files": {name: {"path": suite_dir + SUITE_FILES[name], "sha256": suite_digests[name]} for name in SUITE_FILES},
        "history": [dict(row) for row in history],
    }
    problems = validate(manifest)
    if problems:
        raise SuiteError("SUITE_MANIFEST_INVALID", "; ".join(problems[:5]))
    return manifest


def validate(manifest: Mapping[str, Any]) -> list[str]:
    return [f"{row.get('path', '')}: {row.get('message', row)}" for row in schema_diagnostics(dict(manifest), _SCHEMA, _ROOT)]


def read_suite(project: Path, suite_dir: str) -> dict[str, Any] | None:
    """The manifest of the project's suite, or None when there is none; an invalid one is an error."""
    path = Path(project) / suite_dir / MANIFEST
    if not path.is_file():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SuiteError("SUITE_MANIFEST_INVALID", f"{suite_dir}{MANIFEST}: {error}") from error
    if not isinstance(manifest, dict):
        raise SuiteError("SUITE_MANIFEST_INVALID", f"{suite_dir}{MANIFEST} is not an object")
    return manifest


def write_suite(project: Path, suite_dir: str, payloads: Mapping[str, bytes], *, replace: Mapping[str, str] | None = None) -> list[dict[str, str]]:
    """Write the suite files.  A new file is created exclusively; an existing one is replaced only when
    ``replace`` gives its expected current digest and the bytes still have it (the manifest's record)."""
    project = Path(project).resolve()
    directory = (project / suite_dir).resolve()
    directory.relative_to(project)
    names = {**{name: SUITE_FILES[name] for name in SUITE_FILES}, "manifest": MANIFEST}
    plan = []
    for name, data in payloads.items():
        target = directory / names[name]
        if target.exists():
            current = sha256_bytes(target.read_bytes())
            if current == sha256_bytes(data):
                plan.append((name, target, None))
                continue
            if (replace or {}).get(name) != current:
                raise SuiteError("SUITE_FILE_CONFLICT", f"{suite_dir}{names[name]} exists with other content")
        plan.append((name, target, data))
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, target, data in plan:
        if data is not None:
            temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temporary.write_bytes(data)
            os.replace(temporary, target)
        written.append({"name": name, "path": target.relative_to(project).as_posix(), "sha256": sha256_bytes(target.read_bytes())})
    return written


def verify(project: Path, manifest: Mapping[str, Any], *, document: Mapping[str, Any] | None = None) -> dict[str, list[str]]:
    """What changed against the manifest since the package wrote it: by people, or not by the package.

    ``suite_files`` — suite files whose bytes differ; ``cases`` — cases of ``document`` (default: the
    suite's canonical JSON) whose digest differs; ``methods`` — ``locator`` of test methods whose
    slice differs; ``support`` — test files whose SUPPORT differs; ``missing`` — files that are gone.
    """
    project = Path(project)
    edits: dict[str, list[str]] = {"suite_files": [], "cases": [], "methods": [], "support": [], "missing": []}
    for name, row in sorted(manifest["suite_files"].items()):
        target = project / row["path"]
        if not target.is_file():
            edits["missing"].append(row["path"])
        elif sha256_bytes(target.read_bytes()) != row["sha256"]:
            edits["suite_files"].append(row["path"])
    if document is None:
        canonical = project / manifest["suite_files"]["canonical"]["path"]
        try:
            document = json.loads(canonical.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            document = {"test_cases": []}
    current = {case.get("case_id"): case for case in document.get("test_cases") or [] if isinstance(case, Mapping)}
    for row in manifest["cases"]:
        if row["status"] == "RETIRED":
            continue
        case = current.get(row["case_id"])
        if case is None or case_digest(case) != row["case_digest"]:
            edits["cases"].append(row["case_id"])
    symbols_of: dict[str, list[dict[str, Any]]] = {}
    for case in manifest["cases"]:
        for method in case["methods"]:
            symbols_of.setdefault(method["file"], [])
            if method not in symbols_of[method["file"]]:
                symbols_of[method["file"]].append(method)
    for file in manifest["files"]:
        target = project / file["path"]
        if not target.is_file():
            edits["missing"].append(file["path"])
            continue
        content = target.read_text(encoding="utf-8")
        methods = symbols_of.get(file["path"], [])
        locators = [{"symbol_id": method["symbol_id"], "file_id": file["file_id"], "locator": parse_locator(method["locator"], file["language"])} for method in methods]
        try:
            digests, support = method_digests(file["path"], file["file_id"], content, locators)
        except SliceError:
            edits["methods"].extend(sorted(method["locator"] for method in methods))
            edits["support"].append(file["path"])
            continue
        for method in methods:
            if digests.get(method["symbol_id"]) != method["slice_digest"]:
                edits["methods"].append(method["locator"])
        if support != file["support_digest"]:
            edits["support"].append(file["path"])
    return {key: sorted(set(value)) for key, value in edits.items()}


def parse_locator(text: str, language: str) -> dict[str, Any]:
    if language == "java":
        owner, method = text.rsplit("#", 1)
        return {"kind": "java_class_method", "class_fqn": owner, "method_name": method}
    if "." in text:
        owner, method = text.rsplit(".", 1)
        return {"kind": "python_class_method", "qualified_class_name": owner, "method_name": method}
    return {"kind": "python_module_function", "function_name": text}
