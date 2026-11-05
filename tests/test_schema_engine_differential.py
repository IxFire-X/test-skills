"""Differential test: tools.schema_engine against jsonschema, the oracle, which lives only in requirements-dev.txt.

Every (schema, instance) pair must give the same list of errors: the same (path, validator) pairs (the order of errors of an additionalProperties schema depends on
PYTHONHASHSEED in jsonschema, so the lists are compared sorted), the
same ``_error_code`` and ``_error_path`` of schema_validation, the same schema path and message. The instances are the
fixtures of tests/, the artifacts of earlier runs (``live/*/.pilot-runs``, read only; skipped when absent) and mutations of
the valid ones: a removed required key, a changed type, an extra property, a value outside the enum or the bounds, a
duplicated item, a broken oneOf. By default a deterministic sample of every kind of artifact is taken; set
``TRASSA_DIFF_FULL=1`` for every file of every run.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

import pytest

jsonschema = pytest.importorskip("jsonschema")
referencing = pytest.importorskip("referencing")

from tools import schema_engine  # noqa: E402
from tools.schema_validation import _error_code, _error_path, loads_json_strict  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIVE = Path(os.environ.get("TRASSA_LIVE_DIR", r"D:\AI-Projects\live"))
FULL = os.environ.get("TRASSA_DIFF_FULL") == "1"
SAMPLE_PER_KIND = int(os.environ.get("TRASSA_DIFF_SAMPLE", "2"))
MAX_MUTANTS = 60 if FULL else 20
MUTATE_UP_TO_BYTES = 10**9 if FULL else 60_000  # mutants of a multi-megabyte inventory only repeat the same keywords, slowly

SCHEMA_FILES = sorted((ROOT / "schemas").glob("*.json"))
RAW = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in SCHEMA_FILES}
STORE = {contents["$id"]: contents for contents in RAW.values()}


def _ecma_schema(node: Any) -> Any:
    """A copy of the schema whose patterns are written for the oracle of ECMA 262: a backslash-Z where they say ``$`` (the end of the string) and
    a class of everything but a line terminator where they say ``.``."""
    if isinstance(node, dict):
        return {key: (_ecma_pattern(value) if key == "pattern" and isinstance(value, str) else _ecma_schema(value)) for key, value in node.items()}
    if isinstance(node, list):
        return [_ecma_schema(value) for value in node]
    return node


_ECMA_PATTERNS: dict[str, str] = {}  # repr of the pattern as the oracle has it -> repr of the pattern as written (the message of an error names it)
_ECMA_DOT = r"[^\n\r\u2028\u2029]"


def _ecma_pattern(pattern: str) -> str:
    """Written here on its own, not through tools.schema_engine.  The patterns of the package put no dollar or dot at the head of a class, escape no
    dollar and have no empty class (asserted here), so a walk that skips escapes and classes is exact."""
    assert "\\$" not in pattern and "[]" not in pattern and "[^]" not in pattern, pattern
    out, index, in_class = [], 0, False
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            out.append(pattern[index:index + 2])
            index += 2
            continue
        if in_class:
            assert char != "$", pattern
            in_class = char != "]"
            out.append(char)
        elif char == "[":
            in_class = True
            out.append(char)
        else:
            out.append("\\Z" if char == "$" else _ECMA_DOT if char == "." else char)
        index += 1
    result = "".join(out)
    _ECMA_PATTERNS[repr(result)] = repr(pattern)
    return result


def _build_oracle(raw: dict[str, Any] = RAW) -> dict[str, Any]:
    registry = referencing.Registry()
    for contents in raw.values():
        registry = registry.with_resource(contents["$id"], referencing.Resource.from_contents(contents))
    return {name: jsonschema.Draft202012Validator(contents, registry=registry, format_checker=jsonschema.FormatChecker())
            for name, contents in raw.items()}


ORACLE = _build_oracle()
ECMA_ORACLE = _build_oracle({name: _ecma_schema(contents) for name, contents in RAW.items()})
ECMA_END_OF_STRING: list[str] = []  # the named allowances: the labels of the pairs each forgave (the tests below need them to be exercised)
ECMA_DOT: list[str] = []
ENGINE = {name: schema_engine.Draft202012Validator(contents, registry=STORE, format_checker=schema_engine.FormatChecker())
          for name, contents in RAW.items()}


def _signature(error: Any, depth: int = 2, ecma: bool = False) -> tuple:
    """*ecma*: the signature of an error of the ECMA oracle, whose copy of the schema has other patterns — the message names the pattern as written."""
    context = tuple(sorted((_signature(child, depth - 1, ecma) for child in error.context), key=repr)) if depth else len(error.context)
    message = error.message
    if ecma and error.validator == "pattern":
        head, separator, tail = message.rpartition(" does not match ")
        message = head + separator + _ECMA_PATTERNS.get(tail, tail)
    return (tuple(error.absolute_path), error.validator, tuple(error.absolute_schema_path), message, _error_path(error), _error_code(error), context)


def _holds_a_string(instance: Any, wanted: Any) -> bool:
    """A string value or key of the instance for which *wanted* is true."""
    if isinstance(instance, str):
        return wanted(instance)
    if isinstance(instance, dict):
        return any(_holds_a_string(key, wanted) or _holds_a_string(value, wanted) for key, value in instance.items())
    if isinstance(instance, list):
        return any(_holds_a_string(value, wanted) for value in instance)
    return False


def _ends_on_a_newline(text: str) -> bool:
    return text.endswith("\n")


def _holds_a_line_terminator(text: str) -> bool:
    """A character that Python's "." takes and the "." of ECMA 262 does not (a newline neither dialect takes)."""
    return any(char in text for char in "\r\u2028\u2029")


def _compare(name: str, instance: Any, label: str, problems: list[str]) -> bool:
    """True when the oracle accepts the instance. Appends one line per difference to *problems*.

    Two named allowances. The oracle matches patterns with Python ``re.search``; the engine follows ECMA 262.
    ``ECMA_END_OF_STRING``: ``$`` also matches before a final newline in Python, it does not in ECMA.
    ``ECMA_DOT``: ``.`` also matches ``\\r``, ``\\u2028`` and ``\\u2029`` in Python, it does not in ECMA.
    A difference is forgiven only if the instance holds a string that can bring it about (one with a final newline, one with a line terminator) and the
    engine gives exactly the errors of the same oracle over the schemas written the ECMA way — any other difference stays one."""
    expected = sorted((_signature(error) for error in ORACLE[name].iter_errors(instance)), key=repr)
    try:
        actual = sorted((_signature(error) for error in ENGINE[name].iter_errors(instance)), key=repr)
    except Exception as error:  # an engine crash is a difference too
        problems.append(f"{label} x {name}: engine raised {type(error).__name__}: {error}")
        return not expected
    if ENGINE[name].is_valid(instance) != (not actual):  # F8: the boolean walk is the verdict of the error walk, on every pair
        problems.append(f"{label} x {name}: is_valid says {ENGINE[name].is_valid(instance)}, the engine reports {len(actual)} errors")
    if actual != expected:
        newline = _holds_a_string(instance, _ends_on_a_newline)
        terminator = _holds_a_string(instance, _holds_a_line_terminator)
        if newline or terminator:
            ecma = sorted((_signature(error, ecma=True) for error in ECMA_ORACLE[name].iter_errors(instance)), key=repr)
            if actual == ecma:
                if newline:
                    ECMA_END_OF_STRING.append(f"{label} x {name}")
                if terminator:
                    ECMA_DOT.append(f"{label} x {name}")
                return not ecma
        only_oracle = [item for item in expected if item not in actual][:2]
        only_engine = [item for item in actual if item not in expected][:2]
        problems.append(f"{label} x {name}: oracle-only {only_oracle!r} engine-only {only_engine!r}")
    return not expected


def _mutations(instance: Any) -> Iterator[tuple[str, Any]]:
    sites: list[tuple[tuple, Any]] = []

    def walk(node: Any, path: tuple) -> None:
        sites.append((path, node))
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node[:4]):
                walk(value, path + (index,))

    walk(instance, ())
    stride = max(1, len(sites) // 30)
    for path, node in sites[::stride]:
        def put(value: Any) -> Any:
            if not path:
                return value
            clone = copy.deepcopy(instance)
            target = clone
            for step in path[:-1]:
                target = target[step]
            target[path[-1]] = value
            return clone

        tag = "/".join(map(str, path)) or "<root>"
        if isinstance(node, dict):
            for key in list(node)[:3]:
                clone = copy.deepcopy(instance)
                target = clone
                for step in path:
                    target = target[step]
                del target[key]
                yield f"remove {tag}/{key}", clone
            yield f"extra {tag}", put({**node, "zz_extra": 1})
        elif isinstance(node, list):
            yield f"empty {tag}", put([])
            if node:
                yield f"dup {tag}", put(node + [copy.deepcopy(node[0])])
            yield f"null-item {tag}", put(node + [None])
        elif isinstance(node, bool):
            yield f"bool-to-int {tag}", put(0 if node is False else 1)
            yield f"bool-to-str {tag}", put("x")
        elif isinstance(node, str):
            yield f"str-to-int {tag}", put(1)
            yield f"str-outside-enum {tag}", put("__not_in_enum__")
            yield f"str-empty {tag}", put("")
            yield f"str-bad-date {tag}", put("2026-02-30")
            yield f"str-date {tag}", put("2026-11-03")
            yield f"str-final-newline {tag}", put(node + "\n")
            yield f"str-final-crlf {tag}", put(node + "\r\n")
            yield f"str-inner-cr {tag}", put(node[:1] + "\r" + node[1:])
            yield f"str-inner-ls {tag}", put(node[:1] + "\u2028" + node[1:])
            yield f"str-inner-ps {tag}", put(node[:1] + "\u2029" + node[1:])
            yield f"str-inner-nel {tag}", put(node[:1] + "\x85" + node[1:])
            yield f"str-datetime {tag}", put("2026-11-03T10:00:00Z")
            yield f"str-long {tag}", put(node + "x" * 300)
        elif isinstance(node, (int, float)):
            yield f"num-to-str {tag}", put("x")
            yield f"num-negative {tag}", put(-1)
            yield f"num-huge {tag}", put(10**10)
            yield f"num-float {tag}", put(1.5)
        elif node is None:
            yield f"null-to-str {tag}", put("x")
        yield f"to-object {tag}", put({})
        yield f"to-null {tag}", put(None)


def _differential(instances: list[tuple[str, Any]], mutate: bool) -> list[str]:
    problems: list[str] = []
    for label, instance in instances:
        small = mutate and len(json.dumps(instance)) <= MUTATE_UP_TO_BYTES
        for name in RAW:
            valid = _compare(name, instance, label, problems)
            if valid and small:
                mutants = list(_mutations(instance))
                for mutation, mutant in mutants[::max(1, len(mutants) // MAX_MUTANTS)][:MAX_MUTANTS]:
                    _compare(name, mutant, f"{label} [{mutation}]", problems)
    return problems


def _fixture_instances() -> list[tuple[str, Any]]:
    result = []
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*.json")):
        try:
            result.append((str(path.relative_to(ROOT)), loads_json_strict(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    for path in sorted((ROOT / "docs").rglob("*.json")):
        try:
            result.append((str(path.relative_to(ROOT)), loads_json_strict(path.read_text(encoding="utf-8"))))
        except ValueError:
            continue
    result.append(("contract", loads_json_strict((ROOT / "contracts" / "pipeline.json").read_text(encoding="utf-8"))))
    return result


def _live_files() -> list[Path]:
    runs = sorted(LIVE.glob("*/.pilot-runs")) + sorted(LIVE.glob("*/*/.pilot-runs"))
    kinds: dict[str, list[Path]] = {}
    for run in runs:
        for path in sorted(run.rglob("*.json")):
            relative = path.relative_to(run).parent.as_posix()
            kind = re.sub(r"[0-9a-f]{64}|[0-9a-f]{32}|[0-9a-f]{8}", "H", relative)
            kinds.setdefault(kind, []).append(path)
    if FULL:
        return [path for paths in kinds.values() for path in paths]
    chosen = []
    for paths in kinds.values():
        step = max(1, len(paths) // SAMPLE_PER_KIND)
        chosen.extend(paths[::step][:SAMPLE_PER_KIND])
    return chosen


def _live_instances() -> list[tuple[str, Any]]:
    result = []
    for path in _live_files():
        try:
            result.append((f"{path.parts[-5] if len(path.parts) > 5 else ''}/{path.name}", loads_json_strict(path.read_text(encoding="utf-8"))))
        except (ValueError, OSError):
            continue
    return result


def test_the_oracle_and_the_engine_cover_the_same_44_schemas() -> None:
    assert len(RAW) == 44 and set(ORACLE) == set(ENGINE)


def test_fixtures_and_documents_against_every_schema() -> None:
    instances = _fixture_instances()
    assert len(instances) >= 7
    problems = _differential(instances, mutate=True)
    assert not problems, "\n".join(problems[:40])


@pytest.mark.skipif(not LIVE.is_dir(), reason="no live/ directory with earlier runs on this machine")
def test_artifacts_of_earlier_runs_and_their_mutations() -> None:
    instances = _live_instances()
    if not instances:
        pytest.skip("live/ has no .pilot-runs artifacts")
    problems = _differential(instances, mutate=True)
    assert not problems, f"{len(problems)} differences\n" + "\n".join(problems[:40])


def test_the_named_allowance_ecma_end_of_string_is_exercised_and_is_nothing_more() -> None:
    """Every string of a valid fixture, with a final newline or CRLF: the engine and the oracle differ exactly where a pattern ends on "$"
    (the oracle accepts, the engine refuses), the allowance forgives them all, and no other difference is left."""
    forgiven_before = len(ECMA_END_OF_STRING)
    problems: list[str] = []
    refused = 0
    for label, instance in _fixture_instances():
        for name in RAW:
            if not _compare(name, instance, label, problems):
                continue
            for tail in ("\n", "\r\n"):
                for mutation, mutant in _final_newline_mutants(instance, tail):
                    _compare(name, mutant, f"{label} [{mutation}]", problems)
                    refused += (not ENGINE[name].is_valid(mutant)) and ORACLE[name].is_valid(mutant)
    assert not problems, "\n".join(problems[:40])
    assert refused >= 5 and len(ECMA_END_OF_STRING) - forgiven_before >= refused, (refused, len(ECMA_END_OF_STRING) - forgiven_before)


def _patterns_of(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                yield value
            else:
                yield from _patterns_of(value)
    elif isinstance(node, list):
        for value in node:
            yield from _patterns_of(value)


# strings that the patterns of the schemas (paths, ids, hashes, descriptions, the .skillsrc globs) accept
_SAMPLES = ["a.py", "src/a.py", "src/main/java/A.java", ".", "docs/a.md", "a.json", "BATCH-a", "SREQ-001", "EXP-a.b", "P-1", "a b", "a - b", "x " + chr(0x2014) + " y",
            "project-" + "a" * 24, "artifacts/" + "0" * 32 + "/generated/a.json", "artifacts/" + "0" * 32 + "/runner-output.txt", "mvnw", "gradlew.bat",
            "/operation", "/inputs", "token", "Aa1" + "x" * 30, "ghp_x", "0" * 32, "sha256:" + "0" * 64, "1.0.0", "a-v1", "case.schema.json", ""]


def _newline_and_terminator_variants(text: str) -> Iterator[str]:
    yield text
    for tail in ("\n", "\r\n"):
        yield text + tail
    for character in ("\r", "\u2028", "\u2029", "\x85"):
        yield text[:1] + character + text[1:]
        yield text + character
        yield character + text


def test_every_pattern_of_the_schemas_follows_ecma_262_where_python_differs_on_dollar_and_dot() -> None:
    """Pattern by pattern, string by string (a sample and the sample with a final newline, CR, LS, PS or NEL put in or on it): the engine gives the verdict
    of the oracle over the pattern written the ECMA way, and it differs from the oracle over the pattern as written only where a named allowance
    claims it — a string with a final newline (``ECMA_END_OF_STRING``) or with a CR, LS or PS (``ECMA_DOT``), never on a string without either."""
    ends_on_a_newline = dot_differs = 0
    patterns = sorted(set(_patterns_of(list(RAW.values()))))
    assert len(patterns) >= 60
    for pattern in patterns:
        engine = schema_engine.Draft202012Validator({"type": "string", "pattern": pattern})
        as_written = jsonschema.Draft202012Validator({"type": "string", "pattern": pattern})
        ecma = jsonschema.Draft202012Validator({"type": "string", "pattern": _ecma_pattern(pattern)})
        for sample in _SAMPLES:
            for text in _newline_and_terminator_variants(sample):
                verdict = engine.is_valid(text)
                assert verdict == ecma.is_valid(text), (pattern, text)
                if verdict != as_written.is_valid(text):
                    newline, terminator = _ends_on_a_newline(text), _holds_a_line_terminator(text)
                    assert newline or terminator, (pattern, text)  # no allowance would claim it
                    if terminator and _ECMA_DOT in _ecma_pattern(pattern):
                        dot_differs += 1
                    elif newline:
                        ends_on_a_newline += 1
    assert dot_differs >= 5 and ends_on_a_newline >= 20, (dot_differs, ends_on_a_newline)


def _final_newline_mutants(instance: Any, tail: str) -> Iterator[tuple[str, Any]]:
    """One mutant per string value of the instance: that value with *tail* after it (up to 40 of them, spread over the instance)."""
    return _string_mutants(instance, lambda value: value + tail)


def _string_mutants(instance: Any, change: Any, limit: int = 40) -> Iterator[tuple[str, Any]]:
    """One mutant per string value of the instance: that value as *change* makes it (up to *limit* of them, spread over the instance)."""
    sites: list[tuple[tuple, str]] = []

    def walk(node: Any, path: tuple) -> None:
        if isinstance(node, str):
            sites.append((path, node))
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (key,))
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, path + (index,))

    walk(instance, ())
    for path, value in sites[::max(1, len(sites) // limit)][:limit]:
        if not path:
            yield "<root>", change(value)
            continue
        clone = copy.deepcopy(instance)
        target = clone
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = change(value)
        yield "/".join(map(str, path)), clone


def test_hand_made_broken_instances_of_every_schema() -> None:
    """Every schema gets the empty object, a list, a scalar and an object full of wrong types."""
    garbage = [("empty", {}), ("list", []), ("scalar", "x"), ("null", None), ("number", 3),
               ("wrong", {key: [1, {"a": None}, "x"] for key in ("schema_version", "version", "digest", "id", "state", "events", "items", "modules", "cases")}),
               ("wrong2", {key: {"x": 1.5} for key in ("schema_version", "version", "digest", "id", "state", "events", "items", "modules", "cases")})]
    problems = _differential(garbage, mutate=False)
    assert not problems, "\n".join(problems[:40])


def test_standalone_validators_of_the_consumers_without_a_registry() -> None:
    """pilot_state, skillsrc_manifest and contract_check build a validator from one schema, without the others."""
    names = ["run-manifest", "run-authorization-receipt", "attempt", "terminal-result", "scenario-observation-receipt",
             "retained-native-rerun-receipt", "disposition-receipt", "finalization-receipt", "pre-finalization-trace",
             "derived-terminal-trace", "run-tests-output", "event", "model-request", "execution-inputs-receipt", "skillsrc", "pipeline"]
    instances = _fixture_instances() + [("empty", {}), ("list", [])]
    problems: list[str] = []
    for name in names:
        contents = RAW[f"{name}.schema.json"]
        for checker_old, checker_new in ((jsonschema.FormatChecker(), schema_engine.FormatChecker()), (None, None)):
            old = jsonschema.Draft202012Validator(contents, format_checker=checker_old)
            new = schema_engine.Draft202012Validator(contents, format_checker=checker_new)
            for label, instance in instances:
                expected = sorted((_signature(error) for error in old.iter_errors(instance)), key=repr)
                actual = sorted((_signature(error) for error in new.iter_errors(instance)), key=repr)
                if expected != actual:
                    problems.append(f"{name} x {label}: {expected[:1]!r} != {actual[:1]!r}")
                assert old.is_valid(instance) == new.is_valid(instance)
    assert not problems, "\n".join(problems[:20])


def test_format_checker_agrees_with_the_oracle_on_the_formats_of_the_schemas() -> None:
    samples = ["2026-11-03", "2026-02-29", "2024-02-29", "2026-13-01", "2026-00-10", "0000-01-01", "0001-01-01", "9999-12-31", "10000-01-01",
               "2026-1-03", "20261103", "2026-W45-2", "2026-11-03 ", " 2026-11-03", "2026-11-03\n", "2026-11-03T10:00:00Z",
               "2026-11-03T10:00:00+03:00", "2026-11-03t10:00:00z", "2026-11-03 10:00:00", "٢٠٢٦-١١-٠٣", "２０２６-１１-０３", "", "x", "2026/11/03",
               5, 1.5, None, True, [], {}]
    oracle, engine = jsonschema.FormatChecker(), schema_engine.FormatChecker()
    for name in ("date", "date-time"):  # the schemas use only these two (tests/test_schema_engine.py pins that)
        for sample in samples:
            assert engine.conforms(sample, name) == oracle.conforms(sample, name), (name, sample)
