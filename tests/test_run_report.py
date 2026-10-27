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
    # FaT-6b: the first request has no earlier event, so its step runs from the request itself.
    assert first["stage"] == "tc-reviewer:canonical" and first["step_sec"] == 90.5 and first["transport_attempts"] == 2
    assert second["step_sec"] is None and second["transport_attempts"] == 1


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


def test_the_report_shows_the_measured_steps_and_warns_over_the_budget(tmp_path: Path) -> None:
    from tools.run_report import write_report

    run_root = _finished_run(tmp_path)
    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    assert report["step_sizes"] == [] and "STEP_OVER_BUDGET" not in report["warnings"]
    rows = [{"step": "tc-reviewer:canonical", "bytes": 732886, "input_bytes": 700000, "role_bytes": 32886, "role": "tc-reviewer",
             "files": 3, "tokens_est": 175893, "budget_bytes": 500000, "fits": False, "warning": "STEP_OVER_BUDGET", "at": "2026-10-28T01:00:00Z"},
            {"step": "context-marker", "bytes": 1000, "input_bytes": 900, "role_bytes": 100, "role": "context-marker",
             "files": 2, "tokens_est": 240, "budget_bytes": 500000, "fits": True, "warning": None, "at": "2026-10-28T00:59:00Z"}]
    (run_root / "step-sizes.jsonl").write_bytes(("".join(json.dumps(row) + "\n" for row in rows) + "not json\n").encode("utf-8"))
    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    assert [row["step"] for row in report["step_sizes"]] == ["tc-reviewer:canonical", "context-marker"]
    assert report["warnings"] == ["STEP_OVER_BUDGET"]
    assert "## Размер входа шагов" in markdown and "| tc-reviewer:canonical | 732886 |" in markdown
    assert "STEP_OVER_BUDGET" in markdown.split("## Предупреждения", 1)[1].split("##", 1)[0]


def test_a_data_mismatch_is_in_the_report_by_id_and_count_without_the_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_report
    from tools.review_view import DATA_MISMATCH

    run_root = _finished_run(tmp_path)
    document = json.loads((Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json").read_text(encoding="utf-8"))
    step = next(step for step in document["test_cases"][0]["steps"] if any(item["source"]["kind"] == "literal" for item in step["inputs"]))
    next(item for item in step["inputs"] if item["source"]["kind"] == "literal")["source"]["value"] = "Zebra-Street-77"
    monkeypatch.setattr(run_report, "case_document", lambda root, attempt_id: document)
    run_report.write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    assert DATA_MISMATCH in report["warnings"]
    assert report["data_mismatch"] == [{"case_id": document["test_cases"][0]["case_id"], "step_id": step["step_id"], "count": 1}]
    assert f"{document['test_cases'][0]['case_id']} {step['step_id']}: 1" in markdown
    assert b"Zebra-Street-77" not in (run_root / "report.json").read_bytes() + markdown.encode("utf-8")


def test_a_journaled_model_call_over_the_budget_is_a_warning_too() -> None:
    from tools.run_report import budget_warnings

    assert budget_warnings([{"by": "model", "fits": True}, {"by": "code", "fits": None}], []) == []
    assert budget_warnings([{"by": "model", "fits": False}], []) == ["STEP_OVER_BUDGET"]
    assert budget_warnings([], [{"fits": False}]) == ["STEP_OVER_BUDGET"]


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


# --- FaT-6b: the report on a real-shaped run ------------------------------------------------------

def _real_shaped_run(tmp_path: Path) -> tuple[Path, dict]:
    """A run directory shaped like a real one: case-sets/r1, a context receipt, nested receipts, model requests."""
    import hashlib

    from tests.test_requirement_traceability import canonical_fixture
    from tools.batch_assembly import canonical_bytes

    root = tmp_path / "run"
    document = canonical_fixture()
    doc_bytes = canonical_bytes(document)
    (root / "case-sets" / "r1").mkdir(parents=True)
    (root / "case-sets" / "r1" / "TCDOC-x.r1.json").write_bytes(doc_bytes)
    context = {"schema_version": "1.0.0", "files": [], "byte_count": 90000, "digest": "sha256:" + "c" * 64}
    (root / "context-selections" / "a").mkdir(parents=True)
    (root / "context-selections" / "a" / ("c" * 64 + ".json")).write_text(json.dumps(context), encoding="utf-8")
    bundle = {"kind": "bundle", "digest": "sha256:" + "b" * 64, "rows": ["x" * 500]}
    (root / "effective-canonicals").mkdir()
    (root / "effective-canonicals" / "a.json").write_text(json.dumps({"document": document, "effective_bundle_receipt": bundle,
                                                                      "digest": "sha256:" + "e" * 64}), encoding="utf-8")
    doc_digest = "sha256:" + hashlib.sha256(doc_bytes).hexdigest()
    return root, {"doc": doc_digest, "doc_bytes": len(doc_bytes), "context": context["digest"], "bundle": bundle["digest"],
                  "bundle_bytes": len(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode("utf-8")), "document": document}


def test_inputs_are_resolved_by_file_digest_receipt_digest_and_context_byte_count(tmp_path: Path) -> None:
    from tools.run_report import digest_index, input_bytes

    root, facts = _real_shaped_run(tmp_path)
    index = digest_index(root)
    resolved, unresolved = input_bytes(index, [facts["doc"], facts["context"], facts["bundle"], "sha256:" + "f" * 64])
    assert resolved == facts["doc_bytes"] + 90000 + facts["bundle_bytes"] and unresolved == 1


def test_the_case_document_is_found_in_the_case_sets(tmp_path: Path) -> None:
    from tools.run_report import case_document

    root, facts = _real_shaped_run(tmp_path)
    assert case_document(root, None) == facts["document"]
    (root / "case-sets" / "r2").mkdir()
    newer = dict(facts["document"], revision=2)
    (root / "case-sets" / "r2" / "TCDOC-x.r2.json").write_text(json.dumps(newer), encoding="utf-8")
    assert case_document(root, None)["revision"] == 2


def test_step_time_runs_from_the_previous_event_to_the_response() -> None:
    from tools.run_report import model_calls

    events = [
        _event(1, "CANDIDATE_PUBLISHED", "2026-10-27T10:00:00.000000Z", "assembly"),
        _event(2, "ARTIFACT_READ_BACK", "2026-10-27T10:00:10.000000Z"),
        _event(3, "MODEL_REQUESTED", "2026-10-27T10:09:20.000000Z", "tc-to-autotest:r1", artifact_digest="sha256:" + "1" * 64),
        _event(4, "MODEL_RESPONSE_RECEIVED", "2026-10-27T10:09:26.500000Z", "tc-to-autotest:r1", transport_attempts=1),
    ]
    [call] = model_calls(events)
    assert call["step_sec"] == 556.5 and call["journal_request_to_response_sec"] == 6.5
    assert "duration_sec" not in call


def test_the_markdown_names_the_step_time_honestly() -> None:
    from tools.run_report import STEP_TIME_NOTE

    assert "от предыдущего события" in STEP_TIME_NOTE and "с готовым ответом" in STEP_TIME_NOTE


def test_the_report_runs_inside_one_closure_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FaT-6b: on a real run the report hung for minutes — every read re-derived the journal without the FaT-9 cache."""
    import collections

    from tools import pilot_state
    from tools.run_report import write_report

    run_root = _finished_run(tmp_path)
    counts: collections.Counter = collections.Counter()
    original = pilot_state._derive_state
    monkeypatch.setattr(pilot_state, "_derive_state", lambda root: counts.update(["derive"]) or original(root))
    write_report(tmp_path, run_root.name)
    assert counts["derive"] <= 3, counts


def test_an_unresolved_input_and_a_code_step_are_marked_not_hidden() -> None:
    from tools.run_report import _size_cell, _tokens_cell

    partial = {"input_bytes": 100, "role_bytes": 5, "unresolved_inputs": 2, "size_complete": False, "by": "model", "tokens_est": 25}
    assert _size_cell(partial) == "100+5 (размер неполный: не найдено 2)" and _tokens_cell(partial) == "25"
    merge = {"input_bytes": 9, "role_bytes": 0, "unresolved_inputs": 0, "size_complete": True, "by": "code", "tokens_est": None}
    assert _size_cell(merge) == "9+0" and _tokens_cell(merge) == "код"


def test_a_controller_merge_is_a_code_step_outside_the_model_budget(tmp_path: Path) -> None:
    from tests.test_marker_parts import _marker, _publish_part, _publish_requirements, _run
    from tools.context_marker_parts import merge_parts
    from tools.run_report import build_report

    root, attempt_id, baseline, receipts = _run(tmp_path, files=2)
    requirements = _publish_requirements(root, attempt_id, baseline)
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — наблюдение"]))
    merge_parts(root)
    report, _warnings = build_report(tmp_path, root.name)
    merge = next(call for call in report["model_calls"] if call["stage"] == "context-marker:baseline")
    parts = [call for call in report["model_calls"] if call["stage"] != "context-marker:baseline"]
    assert merge["by"] == "code" and merge["fits"] is None and merge["budget_bytes"] is None and merge["tokens_est"] is None
    assert parts and all(call["by"] == "model" and call["fits"] is not None for call in parts)


def test_a_forged_copy_during_report_is_one_error_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import pilot_state
    from tools.run_pipeline import main

    run_root = _finished_run(tmp_path)
    copy = run_root / "events" / "0000000002.json"
    original = pilot_state._derive_state
    forged = {"done": False}

    def derive_then_forge(root):
        state = original(root)
        if not forged["done"] and pilot_state._CLOSURE_CACHE.get() is not None:
            forged["done"] = True
            copy.write_bytes(copy.read_bytes().replace(b'"seq":2', b'"seq":2 '))
        return state

    monkeypatch.setattr(pilot_state, "_derive_state", derive_then_forge)
    capsys.readouterr()
    code = main(["report", "--project", str(tmp_path), "--run", run_root.name])
    out = capsys.readouterr()
    assert forged["done"] and code == 2 and "Traceback" not in out.out + out.err
    answer = json.loads(out.out)
    assert answer["status"] == "error" and out.out.count('"status"') == 1


# --- FaT-O: the answer size of every model call ---------------------------------------------------

def test_every_model_answer_is_measured_and_a_large_one_is_a_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools import pilot_state, step_size
    from tools.run_report import write_report

    published: list = []
    original = pilot_state.publish_model_stage_artifact
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", lambda *a, **k: published.append(original(*a, **k)) or published[-1])
    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    monkeypatch.setattr(step_size, "DEFAULT_OUTPUT_WARN", 500)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    assert published and all(row["output_bytes"] == row["byte_count"] for row in published)
    large = [row for row in published if row["byte_count"] > 500]
    assert large and all(row["warning"] == step_size.OUTPUT_LARGE for row in large)
    assert all(row["warning"] is None for row in published if row["byte_count"] <= 500)

    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    sizes = {row["content_digest"]: row["byte_count"] for row in published}
    answered = [call for call in report["model_calls"] if call["response_digest"]]
    assert answered and all(call["output_bytes"] == sizes[call["response_digest"]] for call in answered)
    assert all(call["output_large"] == (call["output_bytes"] > 500) for call in answered)
    assert step_size.OUTPUT_LARGE in report["warnings"]
    markdown = (run_root / "report.md").read_text(encoding="utf-8")
    assert "Байты ответа" in markdown and f"{step_size.OUTPUT_LARGE}:" in markdown


def test_the_answer_warning_reads_the_skillsrc_threshold_and_never_stops(tmp_path: Path) -> None:
    from tests.test_phase2_scan import _skillsrc
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import parse_skillsrc_bytes
    from tools.step_size import DEFAULT_OUTPUT_WARN, OUTPUT_LARGE, output_warning

    assert output_warning(tmp_path, DEFAULT_OUTPUT_WARN) is None and output_warning(tmp_path, DEFAULT_OUTPUT_WARN + 1) == OUTPUT_LARGE
    document = parse_skillsrc_bytes(_skillsrc())
    document["limits"] = {"step_output_warn_bytes": 2000}
    (tmp_path / ".skillsrc").write_bytes(_payload(document))
    assert output_warning(tmp_path, 2001) == OUTPUT_LARGE and output_warning(tmp_path, 2000) is None
    (tmp_path / ".skillsrc").write_bytes(b"limits: [unclosed\n\x00")  # unreadable after the answer is published: the default applies
    assert output_warning(tmp_path, DEFAULT_OUTPUT_WARN) is None and output_warning(tmp_path, DEFAULT_OUTPUT_WARN + 1) == OUTPUT_LARGE


def test_a_broken_skillsrc_after_publication_still_returns_the_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run
    from tools import pilot_state, step_size

    published: list = []
    original = pilot_state.publish_model_stage_artifact
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", lambda *a, **k: published.append(original(*a, **k)) or published[-1])
    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    original_warning = step_size.output_warning

    def broken_then_measure(project, byte_count):  # .skillsrc is unreadable only at the moment of the measurement
        path = Path(project) / ".skillsrc"
        before = path.read_bytes() if path.is_file() else None
        path.write_bytes(b"limits: [unclosed\n\x00")
        try:
            return original_warning(project, byte_count)
        finally:
            path.write_bytes(before) if before is not None else path.unlink()

    monkeypatch.setattr(step_size, "output_warning", broken_then_measure)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    assert published and all(row["warning"] == (step_size.OUTPUT_LARGE if row["byte_count"] > step_size.DEFAULT_OUTPUT_WARN else None)
                             for row in published)
