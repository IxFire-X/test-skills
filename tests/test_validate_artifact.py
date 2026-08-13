from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN_RESULT_SCHEMA = ROOT / "schemas" / "run-tests-output.schema.json"
sys.path.insert(0, str(ROOT))

from tools.validate_artifact import validate  # noqa: E402


def valid_run_result() -> dict[str, object]:
    """Return a schema-valid V3 PASS report with one complete evidence record."""
    source_digest = "sha256:" + "a" * 64
    return {
        "schema_version": "3.0.0",
        "stage": "run-tests",
        "source": {
            "document_id": "TCDOC-validator-fixture",
            "revision": 1,
            "source_digest": source_digest,
        },
        "verdict": "PASS",
        "target": {
            "language": "python",
            "framework": "pytest",
            "runner": "pytest",
            "command": "pytest -q",
        },
        "environment": {
            "status": "ready",
            "interpreter": "Python 3",
            "interpreter_path": "python",
            "working_dir": ".",
            "missing": None,
        },
        "stats": {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "errors": 0,
            "skipped": 0,
            "duration_sec": 0,
        },
        "failed_methods": None,
        "root_cause": None,
        "raw_output_excerpt": None,
        "ran_at": "2026-08-12T00:00:00+00:00",
        "exit_code": 0,
        "run_id": "RUN-validator-fixture",
        "execution_evidence": [{
            "run_id": "RUN-validator-fixture",
            "source_digest": source_digest,
            "file_id": "FILE-validator-fixture",
            "symbol_id": "SYMBOL-validator-fixture",
            "file_digest": "sha256:" + "b" * 64,
            "status": "PASSED",
        }],
        "evidence_authoritative": True,
        "diagnostics": [],
    }


class ValidateArtifactTests(unittest.TestCase):
    def test_public_function_accepts_schema_valid_v3_run_result(self) -> None:
        """Calling execution semantics without cross-artifact context must not break schema validation."""
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "run-result.json"
            artifact.write_text(json.dumps(valid_run_result()), encoding="utf-8")

            self.assertEqual((0, {"status": "valid", "errors": []}), validate(str(RUN_RESULT_SCHEMA), str(artifact)))

    def test_cli_accepts_schema_valid_v3_run_result(self) -> None:
        """The standalone CLI must retain schema-only acceptance for a valid V3 run result."""
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "run-result.json"
            artifact.write_text(json.dumps(valid_run_result()), encoding="utf-8")

            result = subprocess.run(
                [sys.executable, str(ROOT / "tools" / "validate_artifact.py"), str(RUN_RESULT_SCHEMA), str(artifact)],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(0, result.returncode)
            self.assertEqual({"status": "valid", "errors": []}, json.loads(result.stdout))

    def test_public_function_rejects_invalid_run_result_shape(self) -> None:
        """Removing required V3 fields must still return schema diagnostics, not an acceptance."""
        invalid = valid_run_result()
        del invalid["target"]
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / "run-result.json"
            artifact.write_text(json.dumps(invalid), encoding="utf-8")

            exit_code, report = validate(str(RUN_RESULT_SCHEMA), str(artifact))

        self.assertEqual(1, exit_code)
        self.assertEqual("invalid", report["status"])
        self.assertTrue(any(error["path"] == "/target" for error in report["errors"]))


if __name__ == "__main__":
    unittest.main()
