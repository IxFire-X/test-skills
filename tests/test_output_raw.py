"""FaT-O2 (2.5): the answer of a model call is measured as the bytes the model gave; a JSON cut in the middle is its own code."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"


def _api(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from tools import api

    code = api.main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_every_answer_is_measured_by_the_raw_bytes_of_the_artifact_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools import pilot_state, step_size
    from tools.run_report import write_report

    published: list[tuple[dict, int]] = []
    original = pilot_state.publish_model_stage_artifact

    def through_the_command(run_root, attempt_id, stage, artifact, **kwargs):
        # the model answers with indents; the command publishes that very file
        answer = tmp_path / f"answer-{len(published)}.json"
        answer.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
        monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", original)
        try:
            code, output = _api(["model-response", "--run-root", str(run_root), "--attempt", attempt_id, "--stage", stage,
                                 "--artifact", str(answer)], capsys)
        finally:
            monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", through_the_command)
        assert code == 0, output
        published.append((output["result"], answer.stat().st_size))
        # PF-1: the command prints the receipt of the record, not the answer's body; the callers get it read back by the digest of the receipt
        code, stored = _api(["read-model-artifact", "--run-root", str(run_root), "--attempt", attempt_id, "--stage", stage,
                             "--digest", output["result"]["content_digest"]], capsys)
        assert code == 0, stored
        return {**output["result"], "artifact": stored["result"]["artifact"]}

    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", through_the_command)
    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    monkeypatch.setattr(step_size, "DEFAULT_OUTPUT_WARN", 1000)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    assert published
    for result, raw in published:
        assert result["output_bytes"] == raw > result["byte_count"] and result["output_basis"] == "raw"
        assert result["warning"] == (step_size.OUTPUT_LARGE if raw > 1000 else None)
    assert any(raw > 1000 >= result["byte_count"] for result, raw in published), "a raw answer over the threshold whose compact form is not"

    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", original)
    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    raw_by_digest = {result["content_digest"]: raw for result, raw in published}
    answered = [call for call in report["model_calls"] if call["response_digest"] in raw_by_digest]
    # V9a: the raw size is the agent's measurement (a file the journal does not bind): the report prints it apart and judges by the stored answer
    assert answered and all(call["output_raw_bytes"] == raw_by_digest[call["response_digest"]] for call in answered)
    assert all(call["output_basis"] == "compact" and call["output_bytes"] <= call["output_raw_bytes"] for call in answered)
    markdown = (run_root / "report.md").read_text(encoding="utf-8")  # the table shows what the model wrote, not the stored answer
    assert all(f"| {call['output_raw_bytes']}" in markdown for call in answered)


def test_a_direct_publication_without_raw_bytes_is_measured_as_before(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.run_report import write_report

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    write_report(tmp_path, run_root.name)
    calls = [call for call in json.loads((run_root / "report.json").read_text(encoding="utf-8"))["model_calls"] if call["response_digest"]]
    assert calls and all(call["output_basis"] == "compact" for call in calls)


def test_the_report_totals_the_model_calls_and_prints_the_raw_answer_size(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools.run_report import write_report
    from tools.step_size import TOKENS_PER_BYTE

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    calls, totals = [call for call in report["model_calls"] if call["by"] == "model"], report["model_totals"]
    assert totals["calls"] == len(calls) > 0
    assert totals["input_bytes"] == sum(call["input_bytes"] + call["role_bytes"] for call in calls)
    assert totals["input_tokens_est"] == sum(call["tokens_est"] for call in calls)
    assert totals["output_bytes"] == sum(call["output_raw_bytes"] or call["output_bytes"] or 0 for call in calls)
    assert totals["output_tokens_est"] == sum(call["output_tokens_est"] for call in calls)
    assert all(call["output_tokens_est"] == round((call["output_raw_bytes"] or call["output_bytes"]) * TOKENS_PER_BYTE) for call in calls)
    assert "| Итого по прогону |" in (run_root / "report.md").read_text(encoding="utf-8")


def _in_run(tmp_path: Path, path: Path) -> tuple[Path, Path]:
    """SEC-A: the answer file of a command lies in the run — the run root and the copy of *path* in its tmp/."""
    from tests.test_output_guard import _run

    _project, run_root, _attempt = _run(tmp_path)
    (run_root / "tmp").mkdir(exist_ok=True)
    answer = run_root / "tmp" / path.name
    answer.write_bytes(path.read_bytes())
    return run_root, answer


def _cut_answer(tmp_path: Path) -> tuple[Path, int]:
    text = json.dumps(json.loads(EXAMPLE.read_text(encoding="utf-8")), ensure_ascii=False, indent=2)
    data = text[: len(text) * 2 // 3].encode("utf-8")
    path = tmp_path / "cut.json"
    path.write_bytes(data)
    return path, len(data)


def test_an_answer_cut_in_the_middle_is_model_output_truncated_with_its_bytes_and_a_hint(tmp_path: Path,
                                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    from tools.step_size import OUTPUT_TRUNCATED

    path, size = _cut_answer(tmp_path)
    run_root, answer = _in_run(tmp_path, path)
    code, output = _api(["model-response", "--run-root", str(run_root), "--attempt", "a", "--stage", "tc-reviewer:canonical",
                         "--artifact", str(answer)], capsys)  # F4b: refused in main with its code and fix, before the function
    message = output["message"]
    assert code == 2 and output["status"] == "error" and OUTPUT_TRUNCATED == "MODEL_OUTPUT_TRUNCATED" and output["code"] == OUTPUT_TRUNCATED
    assert f"{OUTPUT_TRUNCATED}: ответ модели оборван на {size} Б" in message and "без отступов" in message and output["fix"]


def test_validate_artifact_names_a_cut_answer_and_gives_a_retry_prompt(tmp_path: Path) -> None:
    from tests.helpers import run_tmp
    from tools.validate_artifact import validate

    path, size = _cut_answer(run_tmp(tmp_path))
    code, report = validate(str(ROOT / "schemas" / "canonical-test-document.schema.json"), str(path))
    assert code == 2 and report["status"] == "error"
    assert report["errors"][0]["code"] == "MODEL_OUTPUT_TRUNCATED" and f"{size} Б" in report["errors"][0]["message"]
    assert "без отступов" in report["retry_prompt"]


@pytest.mark.parametrize("text", ['{"a": 1,, "b": 2}', '{"a": 1} trailing', '[1, 2]]'])
def test_a_broken_but_closed_json_is_not_called_truncated(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.step_size import looks_truncated
    from tools.validate_artifact import validate

    assert looks_truncated(text) is False
    path = tmp_path / "broken.json"
    path.write_text(text, encoding="utf-8")
    code, report = validate(str(ROOT / "schemas" / "canonical-test-document.schema.json"), str(path))
    assert code == 2 and "MODEL_OUTPUT_TRUNCATED" not in json.dumps(report, ensure_ascii=False)
    run_root, answer = _in_run(tmp_path, path)
    code, output = _api(["model-response", "--run-root", str(run_root), "--attempt", "a", "--stage", "tc-reviewer:canonical",
                         "--artifact", str(answer)], capsys)
    assert code == 2 and output["code"] == "ANSWER_JSON_INVALID" and "MODEL_OUTPUT_TRUNCATED" not in json.dumps(output, ensure_ascii=False)


@pytest.mark.parametrize("text", ['{"a": "abc', '{"a": [1, 2', '{"a": {"b": 1}', '{"a": "x\\"', '  '])
def test_an_open_string_or_bracket_at_the_end_is_truncated(text: str) -> None:
    from tools.step_size import looks_truncated

    assert looks_truncated(text) is (text.strip() != "")
