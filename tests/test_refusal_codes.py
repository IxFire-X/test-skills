"""R&D-29: the line of a refusal carries the codes of the diagnostic rows a bag code hides (MODEL_STAGE_ARTIFACT_INVALID,
BATCH_SEMANTIC_CONFLICT, MATERIALIZATION_CONFLICT) — identifiers only — and report.json counts them; a run whose lines carry none
has no such key (the runs before and the golden run do not move)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_automation_revision_budget import automated_document, automation
from tests.test_run_lock import _run
from tools import api
from tools.json_cli import record_refusal
from tools.model_records import ModelStageArtifactInvalid
from tools.model_steps import check_model_stage_artifact
from tools.run_report import refusals


def _line(root: Path) -> dict:
    return json.loads((root / "refusals.jsonl").read_bytes().splitlines()[-1])


def test_the_codes_of_the_rows_are_sorted_identifiers(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "MODEL_STAGE_ARTIFACT_INVALID", codes=["SCHEMA_TYPE", "SCHEMA_REQUIRED", "SCHEMA_TYPE", "/a/b", "a text", 5])
    assert _line(root)["codes"] == ["SCHEMA_REQUIRED", "SCHEMA_TYPE"]


def test_a_refusal_without_row_codes_has_no_codes_field(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "INVALID_ARGUMENT")
    assert "codes" not in _line(root)


def test_a_schema_refusal_of_an_answer_keeps_the_codes_of_its_rows() -> None:
    answer = automation(automated_document())
    answer["artifacts"] = None
    with pytest.raises(ModelStageArtifactInvalid) as refused:
        check_model_stage_artifact("tc-to-autotest:r1", answer)
    assert refused.value.row_codes and all(code.startswith("SCHEMA_") for code in refused.value.row_codes)


@pytest.mark.parametrize("raised", ["bag", "diagnostics"])
def test_submit_writes_the_codes_of_the_rows_of_its_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys, raised: str) -> None:
    from tools.batch_assembly import BatchAssemblyError

    def refuse(_args):
        if raised == "bag":
            raise ModelStageArtifactInvalid("invalid model stage artifact: x", row_codes=["MATERIALIZATION_CONFLICT"])
        raise BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "x", diagnostics=[{"path": "/cases/0", "code": "CASE_EXPECTED_MISSING", "message": "m"}])

    root = _run(tmp_path)
    monkeypatch.setitem(api.COMMANDS, "submit", (refuse, *api.COMMANDS["submit"][1:]))
    assert api.main(["submit", "--run-root", str(root), "--stage", "plan"]) == 2
    capsys.readouterr()
    assert _line(root)["codes"] == (["MATERIALIZATION_CONFLICT"] if raised == "bag" else ["CASE_EXPECTED_MISSING"])


def test_the_report_counts_the_codes_of_the_rows(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "MODEL_STAGE_ARTIFACT_INVALID", codes=["SCHEMA_TYPE"])
    record_refusal(root, "submit", "MODEL_STAGE_ARTIFACT_INVALID", codes=["SCHEMA_REQUIRED", "SCHEMA_TYPE"])
    assert refusals(root)["row_codes"] == {"SCHEMA_REQUIRED": 1, "SCHEMA_TYPE": 2}


def test_a_report_of_lines_without_codes_has_no_row_codes(tmp_path: Path) -> None:
    root = _run(tmp_path)
    record_refusal(root, "submit", "INVALID_ARGUMENT")
    assert "row_codes" not in refusals(root)
