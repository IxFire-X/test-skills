"""F8: the schema layer does the work once per process — the snapshot of the schemas, the errors of an instance, the error objects of a valid one.

Measured on the Petclinic v0.3 run (review-select, 61 validations): 7 ms of every small one was the snapshot of 44 schema files, a 700 KB candidate was
validated four times because an instance over 64 KB was never cached, and the engine built 20 790 error objects to find out that the candidate is valid.
The cache lives in the memory of the process; its key is the exact bytes (the schemas as read, the instance as canonical JSON), never a path of a run.
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from tools import schema_engine, schema_validation
from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "schemas" / "canonical-test-document.schema.json"
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"


def _fresh_candidate() -> dict:
    """The 1.2 MB example of the package, valid, made unique (a new author) so that no earlier test has put its errors in the cache."""
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["metadata"]["author"] = f"f8-{uuid.uuid4().hex}"
    return document


class _Counter:
    def __init__(self) -> None:
        self.errors = 0
        self.walks = 0
        self.reads = 0


@pytest.fixture()
def counter(monkeypatch: pytest.MonkeyPatch) -> _Counter:
    counted = _Counter()
    original_init = schema_engine.ValidationError.__init__
    original_iter = schema_engine.Draft202012Validator.iter_errors
    original_read = Path.read_bytes

    def init(self, *args, **kwargs):
        counted.errors += 1
        original_init(self, *args, **kwargs)

    def iter_errors(self, instance):
        counted.walks += 1
        return original_iter(self, instance)

    def read_bytes(self):
        if self.suffix == ".json" and self.parent.name == "schemas":
            counted.reads += 1
        return original_read(self)

    monkeypatch.setattr(schema_engine.ValidationError, "__init__", init)
    monkeypatch.setattr(schema_engine.Draft202012Validator, "iter_errors", iter_errors)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    return counted


# --- O-3: a valid instance builds no error object ---------------------------------------------------------------------------------

def test_a_valid_candidate_builds_not_one_error_object(counter: _Counter) -> None:
    document = _fresh_candidate()
    schema_diagnostics(document, CANONICAL, ROOT)  # the schemas are compiled and read once here: the count below is the walk of the instance
    counter.errors = counter.walks = 0
    document["metadata"]["author"] += "-again"
    assert schema_diagnostics(document, CANONICAL, ROOT) == []
    assert counter.walks == 1 and counter.errors == 0, (counter.walks, counter.errors)


def test_an_invalid_candidate_still_reports_its_errors(counter: _Counter) -> None:
    document = _fresh_candidate()
    document["test_cases"][0]["priority"] = "__not_a_priority__"
    rows = schema_diagnostics(document, CANONICAL, ROOT)
    assert any(row["code"].startswith("SCHEMA_") and row["path"].startswith("/test_cases/0") for row in rows), rows[:3]
    assert counter.errors > 0


# --- O-2: the same instance is validated once, whatever its size ----------------------------------------------------------------------

def test_the_same_big_instance_is_validated_once(counter: _Counter) -> None:
    document = _fresh_candidate()
    assert len(json.dumps(document)) > 65536
    first = schema_diagnostics(document, CANONICAL, ROOT)
    walks = counter.walks
    for _ in range(3):
        assert schema_diagnostics(copy.deepcopy(document), CANONICAL, ROOT) == first  # equal bytes, another object
    assert counter.walks == walks == 1, (walks, counter.walks)


def test_a_small_instance_stays_cached(counter: _Counter) -> None:
    schema = ROOT / "schemas" / "pipeline.schema.json"
    instance = {"__f8": uuid.uuid4().hex}
    first = schema_diagnostics(instance, schema, ROOT)
    for _ in range(3):
        assert schema_diagnostics({**instance}, schema, ROOT) == first
    assert counter.walks == 1


def test_another_instance_is_validated_again(counter: _Counter) -> None:
    first, second = _fresh_candidate(), _fresh_candidate()
    schema_diagnostics(first, CANONICAL, ROOT)
    schema_diagnostics(second, CANONICAL, ROOT)
    assert counter.walks == 2


def test_the_cached_errors_are_the_errors_of_the_engine_and_cannot_be_spoiled_by_a_caller() -> None:
    document = _fresh_candidate()
    document["test_cases"][0]["priority"] = "__not_a_priority__"
    before = copy.deepcopy(document)
    first = schema_diagnostics(document, CANONICAL, ROOT)
    assert document == before  # the validation does not touch the instance
    first[0]["message"] = "spoiled"
    first.append({"path": "/x", "code": "X", "message": "x"})
    again = schema_diagnostics(copy.deepcopy(before), CANONICAL, ROOT)
    assert again != first and all(row["message"] != "spoiled" for row in again)
    validator = schema_validation.validator_for(CANONICAL, ROOT)
    direct = sorted(({"path": schema_validation._error_path(error), "code": schema_validation._error_code(error), "message": error.message}
                     for error in validator.iter_errors(before)), key=lambda item: (item["path"], item["code"], item["message"]))
    assert again == direct


def test_the_cache_is_bounded() -> None:
    schema = ROOT / "schemas" / "pipeline.schema.json"
    for index in range(schema_validation._ERRORS_LIMIT * 2):
        schema_diagnostics({"__f8_bound": index, "id": uuid.uuid4().hex}, schema, ROOT)
    assert len(schema_validation._ERRORS) <= schema_validation._ERRORS_LIMIT


def test_a_big_instance_is_keyed_by_its_digest_not_by_its_text() -> None:
    document = _fresh_candidate()
    key = schema_validation._instance_cache_key(document)
    assert key is not None and key.startswith("sha256:") and len(key) == len("sha256:") + 64
    assert schema_validation._instance_cache_key({"a": [1, 2.5, None, "x"]}) == '{"a":[1,2.5,null,"x"]}'  # a small one by its text
    assert schema_validation._instance_cache_key({"a": float("nan")}) is None  # not exact JSON: not cached
    assert schema_validation._instance_cache_key({1: "x"}) is None
    assert schema_validation._instance_cache_key((1, 2)) is None


def test_the_key_of_pilot_state_is_still_the_json_text_of_a_small_value_and_nothing_for_a_big_one() -> None:
    """pilot_state parses the key back; a digest in its place would not parse."""
    assert schema_validation._json_validation_key({"a": [1]}) == '{"a":[1]}'
    assert schema_validation._json_validation_key(_fresh_candidate()) is None


# --- O-1: the snapshot of the schemas is read once per process ---------------------------------------------------------------------

def _schema_root(tmp_path: Path, name: str = "root") -> Path:
    root = tmp_path / name
    (root / "schemas").mkdir(parents=True)
    (root / "schemas" / "a.schema.json").write_text(json.dumps({"$id": "https://example.test/a", "type": "object", "required": ["x"]}), encoding="utf-8")
    (root / "schemas" / "b.schema.json").write_text(json.dumps({"$id": "https://example.test/b", "type": "string"}), encoding="utf-8")
    return root


def test_the_snapshot_is_read_once_per_process(tmp_path: Path, counter: _Counter) -> None:
    root = _schema_root(tmp_path)
    schema = root / "schemas" / "a.schema.json"
    schema_diagnostics({}, schema, root)
    assert counter.reads == 2
    for index in range(5):
        schema_diagnostics({"__f8": index}, schema, root)
    assert counter.reads == 2, counter.reads


def test_the_real_schemas_are_read_once_for_many_validations(counter: _Counter) -> None:
    schema = ROOT / "schemas" / "pipeline.schema.json"
    schema_diagnostics({"__f8": uuid.uuid4().hex}, schema, ROOT)
    counter.reads = 0
    for index in range(20):
        schema_diagnostics({"__f8": uuid.uuid4().hex, "n": index}, schema, ROOT)
    assert counter.reads == 0, counter.reads


def test_a_changed_schema_is_read_again_and_its_errors_are_not_the_old_ones(tmp_path: Path, counter: _Counter) -> None:
    root = _schema_root(tmp_path)
    schema = root / "schemas" / "a.schema.json"
    assert [row["message"] for row in schema_diagnostics({}, schema, root)] == ["'x' is a required property"]
    schema.write_text(json.dumps({"$id": "https://example.test/a", "type": "object", "required": ["y"]}), encoding="utf-8")  # another size
    assert [row["message"] for row in schema_diagnostics({}, schema, root)] == ["'y' is a required property"]
    assert counter.reads == 4  # 2 files, twice


def test_a_schema_rewritten_with_the_same_size_but_another_time_is_read_again(tmp_path: Path) -> None:
    root = _schema_root(tmp_path)
    schema = root / "schemas" / "a.schema.json"
    assert [row["message"] for row in schema_diagnostics({}, schema, root)] == ["'x' is a required property"]
    before = schema.stat()
    schema.write_text(json.dumps({"$id": "https://example.test/a", "type": "object", "required": ["z"]}), encoding="utf-8")  # same length as with "x"
    assert schema.stat().st_size == before.st_size
    os.utime(schema, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))
    assert [row["message"] for row in schema_diagnostics({}, schema, root)] == ["'z' is a required property"]


def test_a_new_or_removed_schema_file_is_seen(tmp_path: Path) -> None:
    root = _schema_root(tmp_path)
    schema = root / "schemas" / "a.schema.json"
    schema_diagnostics({}, schema, root)
    (root / "schemas" / "c.schema.json").write_text(json.dumps({"$id": "https://example.test/c", "type": "string"}), encoding="utf-8")
    assert schema_diagnostics({}, root / "schemas" / "c.schema.json", root)[0]["code"] == "SCHEMA_TYPE"
    (root / "schemas" / "c.schema.json").unlink()
    with pytest.raises(schema_validation.SchemaRegistryError):
        schema_diagnostics({}, root / "schemas" / "c.schema.json", root)


def test_two_roots_with_the_same_schema_bytes_give_the_same_answers_and_two_roots_with_different_bytes_do_not(tmp_path: Path) -> None:
    first, second, third = _schema_root(tmp_path, "one"), _schema_root(tmp_path, "two"), _schema_root(tmp_path, "three")
    (third / "schemas" / "a.schema.json").write_text(json.dumps({"$id": "https://example.test/a", "type": "object", "required": ["w"]}), encoding="utf-8")
    answers = [schema_diagnostics({}, root / "schemas" / "a.schema.json", root) for root in (first, second, third)]
    assert answers[0] == answers[1] != answers[2]
    assert answers[2][0]["message"] == "'w' is a required property"
    # and back again: the first root is not answered with the errors of the third
    assert schema_diagnostics({}, first / "schemas" / "a.schema.json", first) == answers[0]


def test_nothing_of_a_run_is_in_the_keys(tmp_path: Path) -> None:
    run_like = tmp_path / ".pilot-runs" / ("r" * 32) / "published"
    run_like.mkdir(parents=True)
    document = _fresh_candidate()
    before = set(schema_validation._ERRORS)  # V8: only the keys this call adds — under xdist other tests fill the cache of the process with run-like paths
    schema_diagnostics(document, CANONICAL, ROOT)
    for key in set(schema_validation._ERRORS) - before:
        assert str(tmp_path) not in repr(key) and ".pilot-runs" not in repr(key)
    shutil.rmtree(run_like.parent.parent)
