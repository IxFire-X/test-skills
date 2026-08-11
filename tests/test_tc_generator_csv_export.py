import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "tc-generator" / "scripts" / "export_test_cases_csv.py"
SKILL = ROOT / "skills" / "tc-generator" / "SKILL.md"
CONTRACT = ROOT / "skills" / "tc-generator" / "references" / "case-generation-contract.md"
SOURCE = ROOT / "docs" / "to_do" / "real-chains" / "flask" / "chain-01" / "attempt-01" / "02-tc-generator-output.json"


def _run(input_path: Path, output_path: Path, *extra: str):
    return subprocess.run(
        [sys.executable, SCRIPT, "--input", input_path, "--output", output_path, *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_exports_lossless_ordered_csv_and_is_idempotent(tmp_path):
    input_path = tmp_path / "tc-generator-output.json"
    input_path.write_bytes(SOURCE.read_bytes())
    output_path = tmp_path / "tc-generator-output.csv"

    first = _run(input_path, output_path)
    assert first.returncode == 0, first.stderr or first.stdout
    first_report = json.loads(first.stdout)
    assert first_report["status"] == "valid"
    assert first_report["test_case_count"] == 8
    first_bytes = output_path.read_bytes()

    with output_path.open("r", encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert list(rows[0]) == [
        "test_case_id",
        "requirement_links",
        "title",
        "priority",
        "categories",
        "preconditions",
        "test_data",
        "ordered_steps",
        "expected_outcome",
    ]
    assert [row["test_case_id"] for row in rows] == [f"TC-{number:04d}" for number in range(1, 9)]
    assert json.loads(rows[0]["requirement_links"]) == ["REQ-0001"]
    assert json.loads(rows[0]["ordered_steps"]) == [
        {
            "order": 1,
            "action": "POST the object using the test client's json parameter.",
            "expected_result": "HTTP 200; request.is_json is true; parsed request and JSON response equal the supplied object; response mimetype is application/json.",
        }
    ]

    second = _run(input_path, output_path)
    assert second.returncode == 0
    assert output_path.read_bytes() == first_bytes
    assert json.loads(second.stdout)["csv_sha256"] == first_report["csv_sha256"]


def test_verify_only_rejects_tampered_csv_without_repair(tmp_path):
    input_path = tmp_path / "tc-generator-output.json"
    input_path.write_bytes(SOURCE.read_bytes())
    output_path = tmp_path / "tc-generator-output.csv"
    assert _run(input_path, output_path).returncode == 0
    tampered = output_path.read_text(encoding="utf-8").replace("TC-0001", "TC-9999", 1)
    output_path.write_text(tampered, encoding="utf-8", newline="")

    result = _run(input_path, output_path, "--verify-only")

    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "mismatch"
    assert "TC-9999" in output_path.read_text(encoding="utf-8")


def test_malformed_input_is_an_error_and_creates_no_csv(tmp_path):
    input_path = tmp_path / "bad.json"
    input_path.write_text('{"stage":"tc-generator"}', encoding="utf-8")
    output_path = tmp_path / "bad.csv"

    result = _run(input_path, output_path)

    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "error"
    assert not output_path.exists()


def test_skill_requires_csv_companion_without_replacing_canonical_json():
    skill = " ".join(SKILL.read_text(encoding="utf-8").split())
    contract = " ".join(CONTRACT.read_text(encoding="utf-8").split())

    for required in (
        "JSON remains the sole machine authority",
        "always create the sibling CSV companion",
        "export_test_cases_csv.py",
        "JSON↔CSV equivalence",
    ):
        assert required in skill
    assert "Structured list/object fields are canonical compact JSON inside CSV cells" in contract
