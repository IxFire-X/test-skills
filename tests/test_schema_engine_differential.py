"""Differential test: tools.schema_engine against jsonschema, the oracle, which lives only in requirements-dev.txt.

Every (schema, instance) pair must give the same list of errors: the same (path, validator) pairs in the same order, the
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


def _build_oracle() -> dict[str, Any]:
    registry = referencing.Registry()
    for contents in RAW.values():
        registry = registry.with_resource(contents["$id"], referencing.Resource.from_contents(contents))
    return {name: jsonschema.Draft202012Validator(contents, registry=registry, format_checker=jsonschema.FormatChecker())
            for name, contents in RAW.items()}


ORACLE = _build_oracle()
ENGINE = {name: schema_engine.Draft202012Validator(contents, registry=STORE, format_checker=schema_engine.FormatChecker())
          for name, contents in RAW.items()}


def _signature(error: Any, depth: int = 2) -> tuple:
    context = tuple(sorted((_signature(child, depth - 1) for child in error.context), key=repr)) if depth else len(error.context)
    return (tuple(error.absolute_path), error.validator, tuple(error.absolute_schema_path), error.message, _error_path(error), _error_code(error), context)


def _compare(name: str, instance: Any, label: str, problems: list[str]) -> bool:
    """True when the oracle accepts the instance. Appends one line per difference to *problems*."""
    expected = [_signature(error) for error in ORACLE[name].iter_errors(instance)]
    try:
        actual = [_signature(error) for error in ENGINE[name].iter_errors(instance)]
    except Exception as error:  # an engine crash is a difference too
        problems.append(f"{label} x {name}: engine raised {type(error).__name__}: {error}")
        return not expected
    if actual != expected:
        if sorted(actual, key=repr) == sorted(expected, key=repr):
            problems.append(f"{label} x {name}: same errors, other order")
        else:
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


def test_the_oracle_and_the_engine_cover_the_same_43_schemas() -> None:
    assert len(RAW) == 43 and set(ORACLE) == set(ENGINE)


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
                expected = [_signature(error) for error in old.iter_errors(instance)]
                actual = [_signature(error) for error in new.iter_errors(instance)]
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
