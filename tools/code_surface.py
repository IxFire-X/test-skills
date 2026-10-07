"""Code surface of a module (wave 3, D): HTTP endpoints and their parameters, extracted by code.

* Spring MVC — ``@RestController``/``@Controller`` classes, a class-level ``@RequestMapping`` prefix,
  ``@GetMapping``/``@PostMapping``/``@PutMapping``/``@DeleteMapping``/``@PatchMapping`` and
  ``@RequestMapping(method = RequestMethod.X)``; parameters from ``@PathVariable``,
  ``@RequestParam`` and ``@RequestBody``;
* Flask — ``@app.route(path, methods=[...])`` and ``@bp.get(path)`` style decorators;
* FastAPI — ``@app.get(path)``/``@router.post(path)``, with an ``APIRouter(prefix=...)`` prefix.

``surface`` is deterministic (sorted, no line numbers in identities) so that a snapshot stored in
the suite manifest compares with a later scan; ``signature`` normalizes path variables
(``{id}``, ``<int:id>``, ``:id``) so a renamed variable is not a new endpoint.  ``requirement_paths``
finds the endpoints that requirement text names.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_JAVA_CLASS = re.compile(r"(?:public\s+|final\s+|abstract\s+)*class\s+([A-Za-z_]\w*)")
_JAVA_METHOD = re.compile(r"(?:public|protected|private|static|final|synchronized|\s)*[\w<>\[\],.?\s]+?\s+([A-Za-z_]\w*)\s*\(")
_MAPPING = re.compile(r"@(Get|Post|Put|Delete|Patch|Request)Mapping\b")
_HTTP = ("GET", "POST", "PUT", "DELETE", "PATCH")
_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_PY_ROUTE = re.compile(r"^\s*@(\w+)\.(route|get|post|put|delete|patch|api_route)\s*\((.*)$")
_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(([^)]*)")
_PY_PREFIX = re.compile(r"^\s*(\w+)\s*=\s*(?:\w+\.)?(?:APIRouter|Blueprint)\s*\((.*)\)", re.M)
_PATH_IN_TEXT = re.compile(r"(?:\b(GET|POST|PUT|DELETE|PATCH)\s+)?(/[A-Za-z0-9_\-{}<>:.]+(?:/[A-Za-z0-9_\-{}<>:.]*)*)")


def _annotation_args(text: str, start: int) -> tuple[str, int]:
    """The text inside the parentheses that follow ``start`` (empty when there are none)."""
    index = start
    while index < len(text) and text[index] in " \t":
        index += 1
    if index >= len(text) or text[index] != "(":
        return "", index
    depth, cursor = 0, index
    while cursor < len(text):
        char = text[cursor]
        if char == '"':
            cursor += 1
            while cursor < len(text) and text[cursor] != '"':
                cursor += 2 if text[cursor] == "\\" else 1
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[index + 1:cursor], cursor + 1
        cursor += 1
    return text[index + 1:], len(text)


def _paths_of(arguments: str) -> list[str]:
    """``value``/``path`` strings of a mapping annotation, else its leading positional strings, else ``[""]``."""
    named = re.search(r'\b(?:value|path)\s*=\s*(\{[^}]*\}|"(?:[^"\\]|\\.)*")', arguments)
    if named is None:
        named = re.match(r'\s*(\{[^}]*\}|"(?:[^"\\]|\\.)*")', arguments)
    return _STRING.findall(named.group(1)) if named else [""]


def _java_methods(arguments: str, kind: str) -> list[str]:
    if kind != "Request":
        return [kind.upper()]
    found = re.findall(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)", arguments)
    return sorted(set(found)) or ["ANY"]


def _join(prefix: str, path: str) -> str:
    value = "/" + "/".join(part for part in (prefix.strip("/"), path.strip("/")) if part)
    return value if value != "" else "/"


def mask_java_comments(source: str) -> str:
    """``//`` and ``/* */`` comments replaced by spaces (newlines kept), string literals untouched."""
    out: list[str] = []
    index, length = 0, len(source)
    while index < length:
        char = source[index]
        pair = source[index:index + 2]
        if char == '"':
            end = index + 1
            while end < length and source[end] != '"' and source[end] != "\n":
                end += 2 if source[end] == "\\" else 1
            out.append(source[index:end + 1])
            index = end + 1
        elif pair == "//":
            end = source.find("\n", index)
            end = length if end < 0 else end
            out.append(" " * (end - index))
            index = end
        elif pair == "/*":
            end = source.find("*/", index + 2)
            end = length if end < 0 else end + 2
            out.append("".join("\n" if c == "\n" else " " for c in source[index:end]))
            index = end
        else:
            out.append(char)
            index += 1
    return "".join(out)


def _parameter_name(arguments: str | None, variable: str) -> str:
    named = re.search(r'\b(?:value|name)\s*=\s*"((?:[^"\\]|\\.)*)"', arguments or "")
    if named:
        return named.group(1)
    positional = re.match(r'\s*\(\s*"((?:[^"\\]|\\.)*)"', arguments or "")
    return positional.group(1) if positional else variable


def _java_parameters(signature: str) -> list[str]:
    names = []
    for match in re.finditer(r"@(PathVariable|RequestParam|RequestBody|ModelAttribute)\b(\s*\([^)]*\))?\s*(?:final\s+)?([\w<>\[\],.?]+)\s+(\w+)", signature):
        kind, arguments, type_name, variable = match.groups()
        if kind == "RequestBody":
            names.append(f"body:{type_name}")
        elif kind == "ModelAttribute":
            names.append(f"form:{type_name}")
        else:
            names.append(f"{'path' if kind == 'PathVariable' else 'query'}:{_parameter_name(arguments, variable)}")
    return names


def java_endpoints(path: str, source: str) -> list[dict[str, Any]]:
    endpoints: list[dict[str, Any]] = []
    source = mask_java_comments(source)
    if "Mapping" not in source or not re.search(r"@(?:Rest)?Controller\b", source):
        return []
    class_match = _JAVA_CLASS.search(source)
    prefix = ""
    head = source[:class_match.start()] if class_match else ""
    class_mapping = list(re.finditer(r"@RequestMapping\b", head))
    if class_mapping:
        arguments, _end = _annotation_args(head, class_mapping[-1].end())
        prefix = (_paths_of(arguments) or [""])[0]
    owner = class_match.group(1) if class_match else Path(path).stem
    body_start = class_match.end() if class_match else 0
    for match in _MAPPING.finditer(source, body_start):
        arguments, end = _annotation_args(source, match.end())
        # The handler signature follows the annotations: up to the opening brace of the method body.
        brace = source.find("{", end)
        signature = source[end:brace if brace >= 0 else len(source)]
        method_match = None
        for candidate in re.finditer(r"([A-Za-z_]\w*)\s*\(", signature):
            if candidate.group(1) not in {"PathVariable", "RequestParam", "RequestBody", "ModelAttribute", "Valid", "Validated"} and \
                    not signature[:candidate.start()].rstrip().endswith("@"):
                method_match = candidate
                break
        handler = method_match.group(1) if method_match else "?"
        line = source.count("\n", 0, match.start()) + 1
        for http in _java_methods(arguments, match.group(1)):
            for value in _paths_of(arguments):
                endpoints.append({"method": http, "path": _join(prefix, value), "parameters": _java_parameters(signature),
                                  "handler": f"{owner}#{handler}", "source": f"{path}:{line}"})
    return endpoints


def python_endpoints(path: str, source: str) -> list[dict[str, Any]]:
    prefixes = {}
    for match in _PY_PREFIX.finditer(source):
        explicit = re.search(r"(?:url_)?prefix\s*=\s*['\"]([^'\"]*)['\"]", match.group(2))
        prefixes[match.group(1)] = explicit.group(1) if explicit else ""
    lines = source.splitlines()
    endpoints: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = _PY_ROUTE.match(line)
        if match is None:
            continue
        target, verb, rest = match.groups()
        arguments = rest
        cursor = index
        while arguments.count("(") >= arguments.count(")") and cursor + 1 < len(lines) and ")" not in arguments:
            cursor += 1
            arguments += lines[cursor]
        value = re.match(r"\s*['\"]([^'\"]*)['\"]", arguments)
        if value is None:
            continue
        if verb in {"route", "api_route"}:
            methods = re.findall(r"['\"](GET|POST|PUT|DELETE|PATCH)['\"]", arguments.upper()) or ["GET"]
        else:
            methods = [verb.upper()]
        handler, parameters = "?", []
        for follow in lines[index + 1:index + 12]:
            definition = _PY_DEF.match(follow)
            if definition:
                handler = definition.group(1)
                parameters = [f"arg:{name.split(':')[0].split('=')[0].strip()}" for name in definition.group(2).split(",")
                              if name.strip() and name.split(':')[0].split('=')[0].strip() not in {"self", "request"}]
                break
        for http in sorted(set(methods)):
            endpoints.append({"method": http, "path": _join(prefixes.get(target, ""), value.group(1)), "parameters": parameters,
                              "handler": f"{Path(path).stem}#{handler}", "source": f"{path}:{index + 1}"})
    return endpoints


def signature(method: str, path: str) -> str:
    """Method and path with every path variable as ``{}`` (``{id}``, ``<int:id>``, ``:id`` and digits alike)."""
    parts = []
    for part in path.strip().rstrip("/").split("/"):
        if re.fullmatch(r"\{[^}]*\}|<[^>]*>|:\w+|\d+", part):
            parts.append("{}")
        else:
            parts.append(part.casefold())
    return f"{method.upper()} {'/'.join(parts) or '/'}"


def surface(module_root: Path, source_dirs: Sequence[str] = ("src/main/java", "src", "app", ".")) -> dict[str, Any]:
    """Endpoints of the module's product sources (tests excluded), sorted, with a digest of the identities."""
    module_root = Path(module_root)
    seen: set[Path] = set()
    endpoints: list[dict[str, Any]] = []
    for directory in source_dirs:
        base = (module_root / directory).resolve()
        if not base.is_dir():
            continue
        for file in sorted(base.rglob("*")):
            if file in seen or not file.is_file() or file.suffix not in {".java", ".py"}:
                continue
            relative = file.relative_to(module_root.resolve()).as_posix()
            parts = set(relative.split("/"))
            if parts & {"test", "tests", ".pilot-runs", ".git", ".tools", "node_modules", "target", "build", ".venv", "venv", "__pycache__"} or \
                    Path(relative).name.startswith("test_"):
                continue
            seen.add(file)
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            endpoints.extend(java_endpoints(relative, text) if file.suffix == ".java" else python_endpoints(relative, text))
    rows = sorted({(row["method"], row["path"], tuple(row["parameters"]), row["handler"], row["source"]) for row in endpoints})
    endpoints = [{"method": method, "path": path, "signature": signature(method, path), "parameters": list(parameters), "handler": handler, "source": source}
                 for method, path, parameters, handler, source in rows]
    identities = sorted({(row["signature"], tuple(sorted(row["parameters"]))) for row in endpoints})
    return {"endpoints": endpoints, "digest": "sha256:" + hashlib.sha256(json.dumps(identities, ensure_ascii=False).encode("utf-8")).hexdigest()}


def requirement_paths(texts: Iterable[str]) -> set[str]:
    """Signatures and bare paths named in requirement text (``GET /students/{id}`` or just ``/students/{id}``)."""
    found: set[str] = set()
    for text in texts:
        for method, path in _PATH_IN_TEXT.findall(text or ""):
            if len(path) < 2 or path.startswith("//"):
                continue
            path = path.rstrip(".,;:)")
            if method:
                found.add(signature(method, path))
            found.add(signature("ANY", path).split(" ", 1)[1])
    return found


def new_endpoints(current: Mapping[str, Any], snapshot: Mapping[str, Any] | None, requirement_texts: Iterable[str]) -> list[dict[str, Any]]:
    """Endpoints of ``current`` missing from ``snapshot`` (by signature) that no requirement names."""
    known = {row["signature"] for row in (snapshot or {}).get("endpoints", [])}
    named = requirement_paths(requirement_texts)
    rows = []
    for row in current.get("endpoints", []):
        bare = row["signature"].split(" ", 1)[1]
        if row["signature"] in known or row["signature"] in named or bare in named:
            continue
        if any(row["signature"] == other["signature"] for other in rows):
            continue
        rows.append(dict(row))
    return rows
