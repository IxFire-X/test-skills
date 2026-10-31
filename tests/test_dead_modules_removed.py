from __future__ import annotations

from pathlib import Path

from tools.doctor import inspect_environment

ROOT = Path(__file__).resolve().parents[1]

# FaT-AU6: unreachable modules inherited from 455ef01 (audit 5a) stay out of the package.
REMOVED = (
    "tools/assertion_dsl.py",
    "tools/http_binding_v1.py",
    "tools/execution_preflight.py",
    "tools/company_runner.py",
    "tools/scan_project.py",
    "schemas/company-execution-receipt.schema.json",
    "schemas/scan-project-output.schema.json",
)


def test_removed_modules_are_absent_and_doctor_does_not_require_them() -> None:
    assert [path for path in REMOVED if (ROOT / path).exists()] == []
    assert inspect_environment(ROOT)["integrity"]["missing"] == []
