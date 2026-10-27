"""FaT-6: a run report that can leave the workplace, analyst questions, and a precise schema retry hint."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

GAP_A = (
    "REQ-owner-create сценарий «пустое имя» (specs/owner.md#create) — не задан код ответа при пустом имени"
    " — блокирует проверку отказа POST /owners — какой статус вернуть: 400 или 422?"
)
GAP_A2 = (
    "REQ-owner-create сценарий «длинное имя» (specs/owner.md#create) — нет предела длины имени"
    " — блокирует граничную проверку — какая максимальная длина имени?"
)
GAP_B = (
    "REQ-owner-find (specs/owner.md#find) — не сказано, что вернуть, если владельца нет"
    " — блокирует проверку 404 — пустой список или 404?"
)
LOOSE = "REQ-pet-list (specs/pet.md) — поведение при пагинации не описано"


# --- questions for the analyst ----------------------------------------------------------------

def test_questions_group_four_part_gaps_by_requirement_and_keep_the_rest() -> None:
    from tools.run_report import analyst_questions

    text = analyst_questions([GAP_A, GAP_B, GAP_A2, LOOSE])
    assert text.startswith("# Вопросы аналитику")
    assert text.count("## REQ-owner-create") == 1 and text.count("## REQ-owner-find") == 1
    assert text.index("## REQ-owner-create") < text.index("## REQ-owner-find")
    for part in ("не задан код ответа при пустом имени", "блокирует проверку отказа POST /owners", "какой статус вернуть: 400 или 422?"):
        assert part in text
    assert "## Прочее" in text and LOOSE in text.split("## Прочее", 1)[1]


def test_no_gaps_gives_a_single_line() -> None:
    from tools.run_report import analyst_questions

    assert analyst_questions([]) == "вопросов нет\n"


# --- schema retry hint and the retry itself ---------------------------------------------------

def test_a_schema_error_prints_the_path_the_expectation_and_a_retry_prompt(tmp_path: Path) -> None:
    from tools.validate_artifact import validate

    artifact = tmp_path / "answer.json"
    artifact.write_text(json.dumps({"schema_version": "5.0.0", "stage": "context-marker", "artifacts": {}, "warnings": ["x" * 400]}), encoding="utf-8")
    code, report = validate("schemas/context-marker-output.schema.json", str(artifact))
    assert code == 1 and report["errors"]
    prompt = report["retry_prompt"]
    assert "/artifacts" in prompt and "ожидается" in prompt and "пришло" in prompt
    assert "x" * 81 not in prompt
    assert prompt.rstrip().endswith("Исправь только эти поля и верни весь JSON заново по той же схеме.")


@pytest.mark.parametrize("stage", ["context-marker:baseline", "context-marker:requirements", "context-marker:batch-001", "tc-generator:BATCH-a", "tc-reviewer:canonical", "tc-to-autotest:r1", "autotest-reviewer:r1"])
def test_every_model_stage_may_retry_once(stage: str) -> None:
    from tools.pilot_state import model_transport_attempts_allowed

    assert model_transport_attempts_allowed(stage, 1) and model_transport_attempts_allowed(stage, 2)
    assert not model_transport_attempts_allowed(stage, 3) and not model_transport_attempts_allowed(stage, 0)


# --- the report -------------------------------------------------------------------------------

def _event(seq: int, event_type: str, at: str | None, stage: str | None = None, **extra) -> dict:
    row = {"seq": seq, "event_type": event_type, "attempt_id": "a" * 32, **({"observed_at": at} if at else {}), **extra}
    if stage:
        row["stage_instance_id"] = stage
    return row


def test_model_calls_are_timed_from_request_to_response_and_show_attempts() -> None:
    from tools.run_report import model_calls

    events = [
        _event(1, "MODEL_REQUESTED", "2026-10-27T10:00:00.000000Z", "tc-reviewer:canonical", artifact_digest="sha256:" + "1" * 64),
        _event(2, "MODEL_RESPONSE_RECEIVED", "2026-10-27T10:01:30.500000Z", "tc-reviewer:canonical", transport_attempts=2),
        _event(3, "MODEL_REQUESTED", None, "tc-generator:BATCH-a", artifact_digest="sha256:" + "2" * 64),
        _event(4, "MODEL_RESPONSE_RECEIVED", None, "tc-generator:BATCH-a", transport_attempts=1),
    ]
    first, second = model_calls(events)
    assert first["stage"] == "tc-reviewer:canonical" and first["duration_sec"] == 90.5 and first["transport_attempts"] == 2
    assert second["duration_sec"] is None and second["transport_attempts"] == 1


def _finished_run(tmp_path: Path):
    from tests.test_quarantine import THREE_FUNCTIONS, _fail
    from tools.finalize_attempt import decide_dispositions

    run_root, attempt_id, delta = _fail(tmp_path, content=THREE_FUNCTIONS)
    decide_dispositions(tmp_path, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    return run_root


def test_the_report_has_steps_counts_and_no_project_content(tmp_path: Path) -> None:
    from tools.run_report import GUARANTEE, write_report

    run_root = _finished_run(tmp_path)
    written = write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    assert sorted(Path(path).name for path in written) == ["questions-for-analyst.md", "report.json", "report.md"]

    assert report["guarantee"] == GUARANTEE and GUARANTEE in markdown
    assert "время по часам машины, не проверяется" in markdown
    assert report["steps"] and all({"seq", "event", "at"} <= set(row) for row in report["steps"])
    assert report["model_calls"] and all("transport_attempts" in row and "tokens_est" in row and "budget_bytes" in row for row in report["model_calls"])
    assert {"requirements", "batches", "cases", "review_verdict", "automation", "execution"} <= set(report["counts"])
    assert report["quarantine"] == [{"file": "tests/test_generated.py", "method": "test_selected", "ref": "ASSERT-batch-a-001"}]
    assert {"python", "os", "adapter"} <= set(report["environment"])

    blob = (run_root / "report.json").read_bytes() + markdown.encode("utf-8")
    for fragment in ("combine(plugin_value", "assert 1 == 1", "from-project-plugin", "expected: _2_ but was", "def test_other_one"):
        assert fragment.encode("utf-8") not in blob
    for doc in (tmp_path / "openspec").rglob("*.md") if (tmp_path / "openspec").exists() else ():
        for line in doc.read_text(encoding="utf-8").splitlines():
            if len(line.strip()) > 30:
                assert line.strip().encode("utf-8") not in blob


def test_report_is_a_pipeline_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import main

    run_root = _finished_run(tmp_path)
    assert main(["report", "--project", str(tmp_path), "--run", run_root.name]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["stage"] == "report" and out["report_md"].endswith("report.md")


def test_a_long_error_list_is_cut_to_twenty_and_four_kilobytes() -> None:
    from tools.validate_artifact import RETRY_BYTES, RETRY_TAIL, retry_prompt

    errors = [{"path": f"/artifacts/items/{index}/" + "x" * 60, "code": "SCHEMA_TYPE", "message": "y" * 200} for index in range(100)]
    prompt = retry_prompt({"artifacts": {}}, errors)
    assert len(prompt.encode("utf-8")) <= RETRY_BYTES
    assert prompt.count("\n- ") <= 20 and "и ещё " in prompt and prompt.rstrip().endswith(RETRY_TAIL)
    shown = prompt.count("\n- ")
    assert f"и ещё {100 - shown} ошибок" in prompt


def test_questions_read_the_merged_marker_of_all_parts(tmp_path: Path) -> None:
    from tests.test_marker_parts import _marker, _publish_part, _publish_requirements, _run
    from tools.context_marker_parts import merge_parts
    from tools.run_report import write_questions_next_to

    root, attempt_id, baseline, receipts = _run(tmp_path, files=2)
    requirements = _publish_requirements(root, attempt_id, baseline)
    gaps = [GAP_A, GAP_B]
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — наблюдение"], [gaps[index - 1]]))
    merge_parts(root)
    cases = tmp_path / "cases"
    cases.mkdir()
    path, code = write_questions_next_to(cases, root)
    text = (cases / "questions-for-analyst.md").read_text(encoding="utf-8")
    assert code is None and path == str((cases / "questions-for-analyst.md").resolve())
    assert "## REQ-owner-create" in text and "## REQ-owner-find" in text


def test_the_questions_write_never_raises_and_never_follows_a_link(tmp_path: Path) -> None:
    from tools.run_report import write_questions_next_to

    run_root = _finished_run(tmp_path)
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "questions-for-analyst.md").write_text("чужой файл\n", encoding="utf-8")
    assert write_questions_next_to(cases, run_root) == (None, "ANALYST_QUESTIONS_CONFLICT")
    assert (cases / "questions-for-analyst.md").read_text(encoding="utf-8") == "чужой файл\n"
    assert write_questions_next_to(tmp_path / "missing", run_root) == (None, "ANALYST_QUESTIONS_WRITE_FAILED")
    assert sorted(path.name for path in cases.iterdir()) == ["questions-for-analyst.md"]


def test_publication_puts_the_questions_beside_the_case_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    import tools.orchestrate_test_case_revision as revision

    run_root = _finished_run(tmp_path)
    cases = tmp_path / "cases"
    cases.mkdir()
    candidate = tmp_path / "candidate.json"
    candidate.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(revision, "publish_unreviewed_candidate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(revision, "_receipt_json", lambda _receipt: None)
    code = revision.main(["publish", "--candidate", str(candidate), "--output-dir", str(cases), "--run-root", str(run_root), "--attempt-id", "a" * 32])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["status"] == "published" and out["warnings"] == []
    assert out["questions_for_analyst"] == str((cases / "questions-for-analyst.md").resolve())
    assert (cases / "questions-for-analyst.md").is_file()


def test_status_and_a_second_report_still_work_after_the_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_pipeline import main

    run_root = _finished_run(tmp_path)
    assert main(["report", "--project", str(tmp_path), "--run", run_root.name]) == 0
    first = (run_root / "report.json").read_bytes()
    capsys.readouterr()
    assert main(["status", "--project", str(tmp_path), "--run", run_root.name]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["evidence_errors"] == []
    assert main(["report", "--project", str(tmp_path), "--run", run_root.name]) == 0
    assert json.loads((run_root / "report.json").read_bytes())["steps"] == json.loads(first)["steps"]
