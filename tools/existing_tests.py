"""The map of what the project's own tests already exercise — a hint to the case generator and its reviewer.

Code reads only the test files of the frozen scan inventory (by their content digest) and never runs them.  A key is an
HTTP endpoint the tests call (``МЕТОД /путь``, numbers and ``{x}`` as ``{}``) or a production class (Java) or module
(Python) of the batch context that a test names.  Under a key: the test classes and up to ``NAMES`` method names, else
their count, and per method up to ``OUTCOMES`` outcomes — an HTTP status, a view name, an exception class.  No code, no
test bodies, no data literals.  The map is cut at ``LIMIT`` bytes with a line «ещё N ключей».

The map is a pure function of the inventory, the project files behind it and the classes of the context: the same inputs
give the same bytes.  ``repeats`` — the cases whose HTTP step calls an endpoint a project test calls (path compared by
whole segments from the end: ``/owners`` is ``/api/owners``, not ``/petowners``) with the same outcome; one function for
the reviewer's hint ``CV_EXISTING_TEST`` and the run report.  A hint, never a verdict: a case for a requirement stays.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

HINT = "CV_EXISTING_TEST"
LIMIT = 16_000
NAMES = 3
OUTCOMES = 3
SUFFIX = ".existing-tests.md"

_TEST_ANNOTATION = re.compile(r"@\s*(?:Test|ParameterizedTest|RepeatedTest)\b")
_METHOD_NAME = re.compile(r"\b(\w+)\s*\(")
_DIGEST_TEXT = re.compile(r"sha256:[0-9a-f]{64}")
_HTTP_CALL = re.compile(r"\b(get|post|put|delete|patch)\s*\(\s*\"(/[^\"]*)\"", re.IGNORECASE)
_STATUS = re.compile(r"status\(\)\s*\.\s*(is\w+)\s*\(\s*(\d{3})?\s*\)|HttpStatus\s*\.\s*([A-Z_]+)|statusCode\s*\(\s*(\d{3})\s*\)"
                     r"|status_code\s*==\s*(\d{3})")
_VIEW = re.compile(r"view\(\)\s*\.\s*name\s*\(\s*\"([^\"]+)\"\s*\)")
# the model attribute (first quoted name) is left out, the field names after it are kept
# the names are one unambiguous list — a blank between two names belongs to the second one's ``\s*``, or to the comma's ``\s*,`` when a comma follows —
# so a call that is never closed is tried once, not 2^k ways (16 names in quotes without commas — 3.3 s).
_FIELD_ERRORS = re.compile(r"attributeHasFieldErrors\s*\(\s*\"\w+\"\s*,((?:\s*\"\w+\"(?:\s*,)?)+)(?:(?<=,)|(?<!,)\s*)\)"
                           r"|attributeHasFieldErrorsCode\s*\(\s*\"\w+\"\s*,(\s*\"\w+\")")
_EXCEPTION = re.compile(r"\b([A-Z]\w*(?:Exception|Error))\s*\.\s*class\b|pytest\.raises\s*\(\s*([A-Z]\w*(?:Exception|Error))\b")
_NAMED_STATUS = {"isOk": 200, "isCreated": 201, "isAccepted": 202, "isNoContent": 204, "isFound": 302, "isBadRequest": 400,
                 "isUnauthorized": 401, "isForbidden": 403, "isNotFound": 404, "isConflict": 409, "OK": 200, "CREATED": 201,
                 "NO_CONTENT": 204, "FOUND": 302, "BAD_REQUEST": 400, "UNAUTHORIZED": 401, "FORBIDDEN": 403, "NOT_FOUND": 404,
                 "CONFLICT": 409}
_PY_TEST = re.compile(r"^(?:async\s+)?def\s+(test_\w+)\s*\(", re.MULTILINE)
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w, ]+)|import\s+([\w., ]+))", re.MULTILINE)


def _normal_path(path: str) -> str:
    path = re.sub(r"\{[^}]*\}", "{}", path.split("?")[0])
    path = re.sub(r"(?<=/)\d+(?=/|$)", "{}", path)
    return path.rstrip("/") or "/"


def _outcomes(body: str) -> list[str]:
    """Up to ``OUTCOMES`` names and numbers, the most specific first: fields with errors, exception classes, statuses, views."""
    found = [f"field {name}" for fields, code_field in _FIELD_ERRORS.findall(body) for name in re.findall(r"\"(\w+)\"", fields or code_field)]
    found += [java or python for java, python in _EXCEPTION.findall(body)]
    for named, number, constant, code, python_code in _STATUS.findall(body):
        value = number or code or python_code or _NAMED_STATUS.get(named or constant)
        if value:
            found.append(str(value))
    found += [f"view {name}" for name in _VIEW.findall(body)]
    return list(dict.fromkeys(found))[:OUTCOMES]


def _without_comments(text: str) -> str:
    """Java text with comments blanked and string literals kept (the paths and view names are in strings)."""
    result, index, state = list(text), 0, "code"
    while index < len(text):
        char = text[index]
        if state == "code":
            if text.startswith("//", index):
                state = "line"
                continue
            if text.startswith("/*", index):
                state = "block"
                continue
            if char == '"':
                state = "string"
        elif state == "string":
            if char == "\\":
                index += 2
                continue
            if char == '"' or char == "\n":
                state = "code"
        elif state == "line":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
        elif state == "block":
            if text.startswith("*/", index):
                result[index:index + 2] = "  "
                index += 2
                state = "code"
                continue
            if char != "\n":
                result[index] = " "
        index += 1
    return "".join(result)


def _java_methods(text: str) -> Iterable[tuple[str, str]]:
    """(name, body) of each test method: from its annotation to the next test annotation (comments and strings of the
    method head masked, its body kept for the quoted paths and view names)."""
    from tools.java_text import mask_java_noncode

    masked = mask_java_noncode(text)
    text = _without_comments(text)
    marks = [match.start() for match in _TEST_ANNOTATION.finditer(masked)]
    for index, start in enumerate(marks):
        end = marks[index + 1] if index + 1 < len(marks) else len(text)
        head = masked[start:end]
        brace = head.find("{")
        name = _METHOD_NAME.findall(head[:brace] if brace >= 0 else head)
        if name:
            yield name[-1], text[start:end]


def _python_tests(text: str) -> Iterable[tuple[str, str]]:
    marks = list(_PY_TEST.finditer(text))
    for index, match in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        yield match.group(1), text[match.start():end]


def _python_names(text: str) -> set[str]:
    names: set[str] = set()
    for module, imported, plain in _PY_IMPORT.findall(text):
        for part in (module.split(".") if module else []) + [item.strip() for item in imported.split(",")]:
            names.add(part.strip())
        for item in plain.split(","):
            names.update(item.strip().split(" ")[0].split("."))
    return {name for name in names if name}


def own_files(project: Path, inventory: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """``({project path: run id}, ids of the runs not read)``: the files of ``inventory`` the package wrote in an earlier run of the project and nobody
    changed since — their digest is a digest the run recorded (written, or quarantined).  Reads the receipts of the runs, not the files.

    A run that does not read whole (moved project, damaged journal or receipt, a lock of another process) hides nothing and is counted:
    a file we cannot tell stays in the model input.  A run planted whole — journal and receipt, consistent — is not told apart: its
    ``test_root`` is taken from its own receipt, so it can hide from the model any file of the inventory whose digest it names, except the task
    documents, ``.skillsrc`` and the closed manifests.  It gives no right to write (the baseline is the full inventory).

    The same planted run also hides the change of such a file from «the same project» (``pilot_state._comparable``: a reuse of the marker, and of the plan, the
    batches and the review once the project is otherwise the same).  That comparison leaves out an own file only if it is a test by its kind under a test root
    of the module by the human's ``.skillsrc``, not by the root of the receipt: a planted receipt hides no file of the source and no test elsewhere."""
    from tools import pilot_state

    digests = {str(row.get("project_path")): row.get("content_digest") for row in inventory.get("files") or []}
    own: dict[str, str] = {}
    unread: list[str] = []
    for run_root in sorted(path for path in (project / ".pilot-runs").glob("*") if re.fullmatch(r"[0-9a-f]{32}", path.name) and (path / "materialization-ownership").is_dir()):  # a name of no run is not looked at, not printed
        try:
            written = pilot_state.written_files(run_root)
        except (OSError, KeyError, TypeError, AttributeError, ValueError):
            unread.append(run_root.name[:8])
            continue
        own.update({path: run_root.name[:8] for path, found in written.items() if digests.get(path) and digests[path] in found})
    return own, unread


def own_tests(project: Path, inventory: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """``own_files`` narrowed to what may be hidden from the model and from «the same project»: a test (by its kind) under a test root of the module by the
    ``.skillsrc`` of the project root — not by the root of a receipt, which an agent may write.  One rule for the context filter and ``pilot_state._comparable``."""
    from tools import pilot_state

    own, unread = own_files(project, inventory)
    kinds, roots = {str(row.get("project_path")): row.get("kind") for row in inventory.get("files") or []}, pilot_state.module_test_roots(project, inventory)
    return {path: run for path, run in own.items() if kinds.get(path) == "test" and path.startswith(roots)}, unread


def build_map(project: Path, inventory: Mapping[str, Any], context_paths: Iterable[str]) -> dict[str, dict[str, dict[str, list[str]]]]:
    """``{key: {test class: {method: [outcome, …]}}}`` from the test files of ``inventory`` whose bytes still match."""
    project = Path(project)
    in_context = {str(path) for path in context_paths}
    sources = {Path(path).stem for path in in_context if Path(path).suffix in {".java", ".py"} and not Path(path).stem.startswith("test_")
               and not Path(path).stem.endswith(("Test", "Tests", "IT"))}
    hidden = {path for path in own_files(project, inventory)[0] if path not in in_context}  # an own test the model reads (the human put it in paths.include) is in the map
    result: dict[str, dict[str, dict[str, list[str]]]] = {}
    for row in sorted(inventory.get("files") or [], key=lambda item: str(item.get("project_path"))):
        relative = str(row.get("project_path"))
        if row.get("kind") != "test" or Path(relative).suffix not in {".java", ".py"} or relative in hidden:
            continue
        try:
            raw = (project / relative).read_bytes()
        except OSError:
            continue
        if "sha256:" + hashlib.sha256(raw).hexdigest() != row.get("content_digest"):
            continue  # a file changed after the scan is not the scanned project
        text = raw.decode("utf-8", errors="replace")
        test_class = Path(relative).stem
        java = relative.endswith(".java")
        methods = list(_java_methods(text)) if java else list(_python_tests(text))
        named = set(re.findall(r"\b[A-Z]\w*\b", text)) if java else _python_names(text)
        for name, body in methods:
            outcomes = _outcomes(body)
            keys = {f"{verb.upper()} {_normal_path(path)}" for verb, path in _HTTP_CALL.findall(body)}
            keys |= (set(re.findall(r"\b[A-Z]\w*\b", body)) if java else named) & sources
            for key in keys:
                result.setdefault(key, {}).setdefault(test_class, {})[name] = outcomes
    return result


def render_map(value: Mapping[str, Mapping[str, Mapping[str, list[str]]]]) -> bytes:
    """The map as lines «ключ: Класс (m1 [исходы], m2) ; …», cut at ``LIMIT`` bytes; empty map — empty bytes."""
    lines: list[str] = []
    size = 0
    keys = sorted(value)
    for index, key in enumerate(keys):
        parts = []
        for test_class, methods in sorted(value[key].items()):
            names = sorted(methods)
            if len(names) > NAMES:
                outcomes = sorted({item for name in names for item in methods[name]})[:OUTCOMES]
                parts.append(f"{test_class} ({len(names)} тестов" + (f"; исходы: {', '.join(outcomes)})" if outcomes else ")"))
            else:
                parts.append(f"{test_class} (" + ", ".join(name + (f" [{', '.join(methods[name])}]" if methods[name] else "")
                                                         for name in names) + ")")
        line = f"- {key}: " + "; ".join(parts)
        if size + len(line.encode("utf-8")) + 1 > LIMIT:
            lines.append(f"- ещё {len(keys) - index} ключей")
            break
        lines.append(line)
        size += len(line.encode("utf-8")) + 1
    return ("\n".join(lines) + "\n").encode("utf-8") if lines else b""


def view_bytes(value: Mapping[str, Any]) -> bytes:
    """The map file the generator reads: a fixed head and the lines; empty map — empty bytes (no file)."""
    body = render_map(value)
    if not body:
        return b""
    head = ("# Существующие тесты проекта\n\n"
            "Карта кода: какие эндпоинты и классы уже вызывают тесты самого проекта, без кода и тел тестов. Это справка: кейс по "
            "требованию пишется всё равно и требования к нему не снижаются.\n\n")
    return head.encode("utf-8") + body


def map_path(run_root: Path, context_digest: str) -> Path:
    return Path(run_root) / "review-views" / (str(context_digest).removeprefix("sha256:") + SUFFIX)


_IMPORT_LINE = re.compile(r"(?:import |from \S+ import )\S")


def held_bytes(project: Path, row: Mapping[str, Any]) -> bytes | None:
    """The bytes of the file of an inventory *row*; None when unreadable, not the scanned file any more, or a link or a reparse point lies on its way."""
    path = Path(project)
    try:
        for part in Path(str(row.get("project_path"))).parts:  # each part of the path from the project: as _input_files, no link is followed
            path /= part
            if path.is_symlink() or getattr(os.stat(path, follow_symlinks=False), "st_file_attributes", 0) & 0x400:
                return None
        raw = path.read_bytes()
    except OSError:
        return None
    return raw if "sha256:" + hashlib.sha256(raw).hexdigest() == row.get("content_digest") else None


def env_view(project: Path, inventory: Mapping[str, Any], head: list[str], limit: int = 8192) -> bytes:
    """*head*, then ``<files> <import>`` for each import line of the top level of the project's tests (not those the package wrote: a model's guess), most common first, cut at *limit*; b"" with none."""
    own, counts = own_files(Path(project), inventory)[0], {}
    for row in inventory.get("files") or []:
        if row.get("kind") == "test" and Path(str(row.get("project_path"))).suffix in {".java", ".py"} and str(row.get("project_path")) not in own:
            for line in {text.rstrip() for text in (held_bytes(project, row) or b"").decode("utf-8", errors="replace").splitlines() if _IMPORT_LINE.match(text)}:
                counts[line] = counts.get(line, 0) + 1
    lines, shown = [f"{count} {line}" for line, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))], 0
    while shown < len(lines) and sum(len(text.encode("utf-8")) + 1 for text in (*head, *lines[:shown + 1])) <= limit - 40:  # 40: the line about the rest
        shown += 1
    return ("\n".join([*head, *lines[:shown], *([f"… ещё {len(lines) - shown} строк"] if shown < len(lines) else [])]) + "\n").encode("utf-8") if lines else b""


def _segments(path: str) -> list[str]:
    return [part for part in _normal_path(path).split("/") if part]


def _same_endpoint(case_key: str, test_key: str) -> bool:
    """Same method, and the shorter path is the tail of the longer one by whole segments."""
    case_method, _, case_path = case_key.partition(" ")
    test_method, _, test_path = test_key.partition(" ")
    if case_method != test_method:
        return False
    left, right = _segments(case_path), _segments(test_path)
    short, long_ = (left, right) if len(left) <= len(right) else (right, left)
    return bool(short) and long_[len(long_) - len(short):] == short


def _case_steps(case: Mapping[str, Any]) -> Iterable[tuple[str, set[str]]]:
    """(``МЕТОД /путь``, outcomes) of each HTTP step: the operation or the literal method/path inputs; asserted status
    numbers, view names and exception classes."""
    for step in case.get("steps") or []:
        literal = {str((item.get("target") or {}).get("name")): (item.get("source") or {}).get("value")
                   for item in step.get("inputs") or [] if (item.get("source") or {}).get("kind") == "literal"}
        operation = step.get("operation") or {}
        method = operation.get("method") or literal.get("method") or literal.get("http_method")
        path = operation.get("path") or literal.get("path") or literal.get("path_template") or literal.get("url")
        if not (isinstance(method, str) and isinstance(path, str) and path.startswith("/")):
            continue
        outcomes: set[str] = set()
        for expectation in step.get("expectations") or []:
            for assertion in expectation.get("assertions") or []:
                expected = assertion.get("expected") or {}
                actual = json.dumps(assertion.get("actual") or {}, ensure_ascii=False).casefold()
                value = expected.get("value") if expected.get("kind") == "literal" else None
                if isinstance(value, int) and "status" in actual:
                    outcomes.add(str(value))
                elif isinstance(value, list) and "field" in actual:
                    outcomes.update(f"field {item}" for item in value if isinstance(item, str))
                elif isinstance(value, str) and "view" in actual:
                    outcomes.add(f"view {value}")
                elif isinstance(value, str) and re.fullmatch(r"[A-Z]\w*(?:Exception|Error)", value):
                    outcomes.add(value)
        yield f"{method.upper()} {_normal_path(path)}", outcomes


def repeats(document: Mapping[str, Any], value: Mapping[str, Mapping[str, Mapping[str, list[str]]]]) -> list[str]:
    """IDs of the cases that add no new (endpoint, outcome): every HTTP step with a checked outcome has its endpoint and
    that outcome in a project test (a setup step alone that a test also calls does not make a repeat)."""
    found = []
    endpoints = {key: rows for key, rows in value.items() if " " in key}
    for case in document.get("test_cases") or []:
        checked = [(key, outcomes) for key, outcomes in _case_steps(case) if outcomes]
        if checked and all(outcomes <= {item for test_key, rows in endpoints.items() if _same_endpoint(key, test_key)
                                        for methods in rows.values() for items in methods.values() for item in items}
                           for key, outcomes in checked):
            found.append(str(case["case_id"]))
    return found


def hint_line(case_ids: list[str]) -> str | None:
    if not case_ids:
        return None
    shown = ", ".join(case_ids[:10]) + (f" и ещё {len(case_ids) - 10}" if len(case_ids) > 10 else "")
    return (f"- {HINT}: кейсов {len(case_ids)} не добавляют нового сочетания «эндпоинт + исход» к тестам проекта — {shown}. "
            "Входных данных карта не видит, это верхняя оценка. Справка: кейс по требованию остаётся, требования к нему не "
            "снижаются; дубль без нового условия — замечание.")


def for_context(project: Path, context_receipt: Mapping[str, Any] | list[Mapping[str, Any]], root: Path | None = None) -> dict[str, Any]:
    """The map for one context receipt: its frozen inventory (by digest, from the run that froze it) and its files. With ``root`` the inventory
    is that run's; without it (the plan knows only the project) the first run whose own journal froze that digest.

    For the context receipts of one plan (one inventory) the one map of all their files — the HTTP keys of the
    tests do not depend on the context, so a map per receipt would repeat them."""
    project = Path(project).resolve()
    receipts = list(context_receipt) if isinstance(context_receipt, list) else [context_receipt]
    if not receipts:
        return {}
    digest = str(receipts[0].get("inventory_digest", ""))
    inventory = None
    from tools import pilot_state

    runs = [Path(root)] if root is not None else sorted(path for path in (project / ".pilot-runs").glob("*") if (path / "inventories").is_dir())
    for run_root in runs:
        try:  # the inventory the journal of that run froze under this digest — never the first file of that name
            inventory = pilot_state.frozen_inventory(project, Path(run_root).resolve(), digest)
            break
        except (OSError, KeyError, TypeError, ValueError):
            continue
    if inventory is None:
        return {}
    return build_map(project, inventory, [str(row["project_path"]) for receipt in receipts for row in receipt.get("files") or []])


def context_view(run_root: Path, context_digests: str | list[str], attempt_id: str | None = None, named: list[str] | None = None) -> bytes:
    """The map bytes of the context of one generator batch, built from the frozen inputs; the digests that are no context
    selection of the attempt are skipped, empty when none is or the project has no hits. The one source of these
    bytes for the list of reading of prepare and the run report; one map for all the context receipts the batch reads."""
    from tools.pilot_state import _context_selection_target, derive_state, read_context_selection

    root = Path(run_root).resolve()
    attempt_id = attempt_id or str(derive_state(root)["attempts"][-1]["attempt_id"])
    digests = [context_digests] if isinstance(context_digests, str) else list(context_digests)
    selected = [str(digest) for digest in digests if _DIGEST_TEXT.fullmatch(str(digest))
                and _context_selection_target(root, attempt_id, str(digest)).is_file()]
    if not selected:
        return b""
    from tools.batch_assembly import generator_receipts
    return view_bytes(for_context(root.parent.parent, generator_receipts([read_context_selection(root, attempt_id, digest) for digest in selected], named)))


def ensure_view(run_root: Path, context_digests: str | list[str], named: list[str] | None = None) -> Path | None:
    """Write (or rewrite, when the bytes differ) the map file of one batch context — named by its first context receipt;
    None when the project has no hits."""
    from tools.confined_output import atomic_write_confined_bytes_at_root

    root = Path(run_root).resolve()
    data = context_view(root, context_digests, None, named)
    first = context_digests if isinstance(context_digests, str) else context_digests[0]
    target = map_path(root, first)
    if not data:
        return None
    try:
        current = target.read_bytes()
    except OSError:
        current = None
    if current != data:
        atomic_write_confined_bytes_at_root(root.parent.parent, root, target, data)
    return target
