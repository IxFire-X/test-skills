import csv
import hashlib
import json
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator

EXPECTED_COUNTS = {"java": 7, "python": 6}


def _path(root: Path, value: str) -> Path:
    return root.joinpath(*PurePosixPath(value).parts)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_schema(root: Path, artifact: Path, schema_name: str) -> None:
    schema = _load(root / "schemas" / schema_name)
    errors = list(Draft202012Validator(schema).iter_errors(_load(artifact)))
    assert not errors, [error.message for error in errors]


def test_java_and_python_real_chain_evidence_is_traceable_and_csv_backed(root):
    for language, expected_count in EXPECTED_COUNTS.items():
        acceptance = _load(root / "docs/to_do/e2e" / language / "acceptance.json")
        assert acceptance["status"] == "ACCEPT"
        assert acceptance["case_count"] == expected_count
        assert acceptance["method_count"] == expected_count

        artifacts = {item["role"]: item for item in acceptance["artifacts"]}
        for item in artifacts.values():
            path = _path(root, item["path"])
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
            if item.get("schema"):
                _assert_schema(root, path, item["schema"])

        manual_review = _load(_path(root, artifacts["manual_review"]["path"]))
        autotest_review = _load(_path(root, artifacts["autotest_review"]["path"]))
        assert manual_review["artifacts"]["validation_report"]["verdict"] == "ПРИНЯТО"
        assert autotest_review["artifacts"]["autotest_review"]["verdict"] == "ПРИНЯТО"

        generator = _load(_path(root, artifacts["test_cases"]["path"]))
        trace = _load(_path(root, artifacts["trace"]["path"]))
        case_ids = [
            item["id"]
            for item in generator["artifacts"]["generated_test_cases"]["test_cases"]
        ]
        assert [item["id"] for item in trace["test_cases"]] == case_ids
        assert len(trace["methods"]) == expected_count
        assert trace["final_verdict"] == "PASS"

        with _path(root, artifacts["csv"]["path"]).open(
            encoding="utf-8-sig", newline=""
        ) as stream:
            assert [row["test_case_id"] for row in csv.DictReader(stream)] == case_ids
