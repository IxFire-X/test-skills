"""FaT-P3: the pause after the context marker — the human excludes requirements with gaps or goes on; the plan of the batches
carries the answer as ``out_of_scope``.  The code lists, checks and records; it decides nothing."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_batch_assembly import _fragment, _header, _header_digest
from tests.test_batch_size_split import _context_receipt, _requirement
from tests.test_marker_parts import _marker, _requirements, _run
from tools import api, pilot_state

GAP = "REQ-1 сценарий (docs/feature.md#pay) — нет наблюдаемого канала — блокирует проверку оплаты — как проверить оплату?"


def _marker_rows() -> list[dict]:
    texts = ["REQ-1 Оплата заказа картой.", "REQ-2 Возврат средств клиенту."]
    return [dict(row, text=text) for row, text in zip(_requirements(), texts)]


def _paused_run(tmp_path: Path, warnings: list[str] | None = None):
    """A run whose single context marker is answered with two requirements and (by default) one gap warning."""
    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline",
                                             _marker(["src/part0.py — x"], [GAP] if warnings is None else warnings, requirements=_marker_rows()))
    return root, attempt_id, baseline, receipts


def _plan_arguments(root: Path, attempt_id: str, receipts: list[dict], *extra: str) -> list[str]:
    tmp = root / "tmp"
    tmp.mkdir(exist_ok=True)
    (tmp / "req.json").write_text(json.dumps(_marker_rows(), ensure_ascii=False), encoding="utf-8")
    (tmp / "grouping.json").write_text(json.dumps({"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": receipts[0]["digest"]}), encoding="utf-8")
    (tmp / "context.json").write_text(json.dumps(receipts[0], ensure_ascii=False), encoding="utf-8")
    (tmp / "marker.json").write_text("{}", encoding="utf-8")
    return ["plan-batches", "--requirements", str(tmp / "req.json"), "--grouping", str(tmp / "grouping.json"), "--project", str(root.parent.parent),
            "--input-file", str(tmp / "marker.json"), "--context-receipt", str(tmp / "context.json"), "--out", str(tmp / "plan.json"),
            "--run-root", str(root), "--attempt", attempt_id, *extra]


def _api(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict]:
    code = api.main(argv)
    return code, json.loads(capsys.readouterr().out)


# --- the plan: out_of_scope --------------------------------------------------------------------------------

def test_an_excluded_requirement_is_owned_by_no_batch_and_the_rest_is_renumbered() -> None:
    from tools.batch_assembly import in_scope_requirements, plan_batches

    requirements = [_requirement(number, f"REQ-{number} текст") for number in (1, 2, 3)]
    evidence = {"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": "sha256:" + "d" * 64}
    plain = plan_batches(requirements, evidence)
    plan = plan_batches(requirements, evidence, out_of_scope=[{"source_requirement_id": "SREQ-0002", "reason": "нет стенда"}])
    assert plan["out_of_scope"] == [{"source_requirement_id": "SREQ-0002", "reason": "нет стенда"}]
    assert [row["source_requirement_id"] for row in plan["source_requirements"]] == ["SREQ-0001", "SREQ-0003"]
    assert [row["display_order"] for row in plan["source_requirements"]] == [1, 2]
    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001", "SREQ-0003"]]
    assert plan["source_requirements"] == in_scope_requirements(requirements, plan)
    assert "out_of_scope" not in plain and plan["digest"] != plain["digest"]
    assert plan_batches(requirements, evidence, out_of_scope=[]) == plain  # no exclusion: the bytes of the plan as before


def test_the_plan_refuses_an_unknown_or_reasonless_exclusion_and_an_empty_scope() -> None:
    from tools.batch_assembly import BatchAssemblyError, plan_batches

    requirements = [_requirement(number, f"REQ-{number} текст") for number in (1, 2)]
    evidence = {"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": "sha256:" + "d" * 64}
    for rows in ([{"source_requirement_id": "SREQ-9999", "reason": "нет"}], [{"source_requirement_id": "SREQ-0001", "reason": " "}],
                 [{"source_requirement_id": "SREQ-0001", "reason": "а"}, {"source_requirement_id": "SREQ-0002", "reason": "б"}]):
        with pytest.raises(BatchAssemblyError, match="BATCH_PARTITION_CONFLICT"):
            plan_batches(requirements, evidence, out_of_scope=rows)


def test_a_candidate_is_assembled_without_the_excluded_requirement_and_no_case_is_written_for_it() -> None:
    """The coverage rules stay: every requirement of a batch has its case; the excluded one is in no batch, so none is asked."""
    from tools.batch_assembly import assemble_candidate, plan_batches, validate_fragment

    header, context = _header(), _context_receipt()
    requirements = [_requirement(number, letter + "01. " + "а" * 4000) for number, letter in enumerate("ABC", start=1)]
    plan = plan_batches(requirements, {"header_digest": _header_digest(header), "context_receipt_digest": context["digest"]},
                        window_bytes=300_000, input_bytes=100_000, out_of_scope=[{"source_requirement_id": "SREQ-0002", "reason": "нет канала"}])
    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001"], ["SREQ-0003"]]
    fragments = [_fragment(plan, context, index) for index in range(2)]
    for index, fragment in enumerate(fragments):
        validate_fragment(fragment, plan, header, context, fragments[:index])
    document, _receipt = assemble_candidate(header, plan, fragments)
    assert [row["source_requirement_id"] for row in document["source_requirements"]] == ["SREQ-0001", "SREQ-0003"]
    assert [row["display_order"] for row in document["source_requirements"]] == [1, 2]
    assert len(document["test_cases"]) == 2


def test_the_schema_of_the_plan_knows_out_of_scope_and_refuses_a_reasonless_row() -> None:
    from tools.batch_assembly import BatchAssemblyError, _plan_batches, plan_batches, with_digest

    requirements = [_requirement(number, f"REQ-{number} текст") for number in (1, 2)]
    evidence = {"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": "sha256:" + "d" * 64}
    plan = dict(plan_batches(requirements, evidence, out_of_scope=[{"source_requirement_id": "SREQ-0002", "reason": "нет стенда"}]))
    assert _plan_batches(plan)
    broken = with_digest({key: value for key, value in plan.items() if key != "digest"} | {"out_of_scope": [{"source_requirement_id": "SREQ-0002"}]})
    with pytest.raises(BatchAssemblyError):
        _plan_batches(broken)
    inside = with_digest({key: value for key, value in plan.items() if key != "digest"}
                         | {"out_of_scope": [{"source_requirement_id": "SREQ-0001", "reason": "уже в плане"}]})
    with pytest.raises(BatchAssemblyError, match="out_of_scope"):
        _plan_batches(inside)


# --- the pause --------------------------------------------------------------------------------------------

def test_gaps_in_the_marker_make_the_scope_pending_and_name_the_requirement(tmp_path: Path) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path)
    scope = scope_pause.scope_state(root, attempt_id)
    assert scope["pending"] is True and scope["decision"] is None
    assert [row["source_requirement_ids"] for row in scope["gaps"]] == [["SREQ-batch-a-001"]]
    assert scope["gaps"][0]["missing"] == "нет наблюдаемого канала"


def test_a_marker_without_gap_warnings_does_not_pause(tmp_path: Path) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path, warnings=["док — общее замечание без четырёх полей"])
    assert scope_pause.scope_state(root, attempt_id)["pending"] is False


def test_the_scan_flag_no_pause_lifts_the_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_pipeline, scope_pause
    from tools.run_pipeline import build_parser

    args = build_parser().parse_args(["scan", "--project", str(tmp_path), "--no-pause"])
    request = run_pipeline._scan_request(args)
    assert request["no_pause"] is True and "no_pause" not in run_pipeline._scan_request(build_parser().parse_args(["scan", "--project", str(tmp_path)]))
    assert "--no-pause" in run_pipeline._scan_command(tmp_path, request)
    (tmp_path / "run").mkdir()
    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path / "run")
    monkeypatch.setattr(scope_pause, "_no_pause", lambda _root: True)
    scope = scope_pause.scope_state(root, attempt_id)
    assert scope["pending"] is False and scope["no_pause"] is True and scope["gaps"]


def test_the_manifest_schema_takes_no_pause() -> None:
    from tools.schema_validation import schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    request = {"module": None, "docs": [], "target": None, "profile": "cases-only-v1", "accept_self_review": False, "disposition_policy": None}
    for extra, ok in (({}, True), ({"no_pause": True}, True), ({"no_pause": False}, False)):
        value = {"scan_request": {**request, **extra}}
        found = [row for row in schema_diagnostics(value, root / "schemas" / "run-manifest.schema.json", root) if "scan_request" in row["path"]]
        assert (found == []) is ok, (extra, found)


def test_plan_batches_without_an_answer_is_scope_pending_with_a_fix_and_a_draft(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    code, payload = _api(capsys, _plan_arguments(root, attempt_id, receipts))
    assert code == 2 and payload["code"] == "SCOPE_PENDING", payload
    assert "SREQ-batch-a-001" in payload["message"] and "--exclude" in payload["fix"] and "--continue" in payload["fix"]
    draft = (root / "questions-for-analyst.md").read_text(encoding="utf-8")  # the same builder as the report, the marker's warnings only
    assert "REQ-1" in draft and "как проверить оплату?" in draft


def test_plan_batches_with_exclude_records_the_answer_and_the_plan_names_the_requirement(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    code, payload = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--exclude", "SREQ-batch-a-001", "--reason", "нет стенда с картами"))
    assert code == 0, payload
    plan = json.loads((root / "tmp" / "plan.json").read_text(encoding="utf-8"))
    assert plan["out_of_scope"] == [{"source_requirement_id": "SREQ-batch-a-001", "reason": "нет стенда с картами"}]
    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-batch-a-002"]]
    decision = scope_pause.read_decision(root, attempt_id)
    assert decision["decision"] == "exclude" and decision["excluded"] == plan["out_of_scope"]
    # the answer is a run artifact: a later plan-batches without flags takes it, the same answer again changes nothing
    code, _payload = _api(capsys, _plan_arguments(root, attempt_id, receipts))
    assert code == 0 and json.loads((root / "tmp" / "plan.json").read_text(encoding="utf-8"))["out_of_scope"] == plan["out_of_scope"]
    code, _payload = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--exclude", "SREQ-batch-a-001", "--reason", "нет стенда с картами"))
    assert code == 0
    code, payload = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--continue"))
    assert code == 2 and payload["code"] == "SCOPE_DECISION_CONFLICT", payload


def test_plan_batches_with_continue_plans_every_requirement(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    code, payload = _api(capsys, _plan_arguments(root, attempt_id, receipts, "--continue"))
    assert code == 0, payload
    plan = json.loads((root / "tmp" / "plan.json").read_text(encoding="utf-8"))
    assert "out_of_scope" not in plan and plan["batches"][0]["owned_source_requirement_ids"] == ["SREQ-batch-a-001", "SREQ-batch-a-002"]


@pytest.mark.parametrize("extra, code_name", [
    (("--exclude", "SREQ-9999", "--reason", "нет"), "SCOPE_UNKNOWN_REQUIREMENT"),
    (("--exclude", "SREQ-batch-a-001"), "SCOPE_REASON_REQUIRED"),
    (("--exclude", "SREQ-batch-a-001", "--exclude", "SREQ-batch-a-002", "--reason", "всё"), "SCOPE_EMPTY"),
    (("--exclude", "SREQ-batch-a-001", "--reason", "нет", "--continue"), "SCOPE_ANSWER_INVALID"),
])
def test_a_wrong_answer_is_refused_and_nothing_is_recorded(tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple, code_name: str) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    code, payload = _api(capsys, _plan_arguments(root, attempt_id, receipts, *extra))
    assert code == 2 and payload["code"] == code_name, payload
    assert scope_pause.read_decision(root, attempt_id) is None


def test_the_published_plan_must_carry_the_answer(tmp_path: Path) -> None:
    """Whoever skips plan-batches and its refusal meets the same one at publish-generator-input."""
    from tools import scope_pause
    from tools.batch_assembly import plan_batches

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Заголовок"})["digest"]
    evidence = {"header_digest": header, "context_receipt_digest": receipts[0]["digest"]}
    plain = dict(plan_batches(_marker_rows(), evidence))
    with pytest.raises(ValueError, match="SCOPE_PENDING"):
        pilot_state.publish_generator_input(root, attempt_id, "plan", plain)
    scope_pause.record_decision(root, attempt_id, excluded=["SREQ-batch-a-001"], reason="нет стенда", proceed=False)
    with pytest.raises(ValueError, match="SCOPE_PLAN_MISMATCH"):  # a plan that keeps what the human excluded
        pilot_state.publish_generator_input(root, attempt_id, "plan", plain)
    carrying = dict(plan_batches(_marker_rows(), evidence, out_of_scope=scope_pause.out_of_scope(scope_pause.read_decision(root, attempt_id))))
    assert pilot_state.publish_generator_input(root, attempt_id, "plan", carrying)["digest"] == carrying["digest"]


def test_the_published_plan_after_continue_has_no_out_of_scope(tmp_path: Path) -> None:
    from tools import scope_pause
    from tools.batch_assembly import plan_batches

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Заголовок"})["digest"]
    plain = dict(plan_batches(_marker_rows(), {"header_digest": header, "context_receipt_digest": receipts[0]["digest"]}))
    scope_pause.record_decision(root, attempt_id, excluded=[], reason=None, proceed=True)
    assert pilot_state.publish_generator_input(root, attempt_id, "plan", plain)["digest"] == plain["digest"]


def test_status_shows_the_pause_with_the_gaps_and_the_commands(tmp_path: Path) -> None:
    from tools import scope_pause
    from tools.run_pipeline import _scope_pause_step

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path)
    step = {"code": "GENERATOR_PLAN", "stage": "tc-generator", "action": "план", "skill_section": "3", "command": "plan-batches"}
    paused = _scope_pause_step(root, attempt_id, step)
    assert paused["code"] == "SCOPE_PENDING" and paused["gaps"][0]["source_requirement_ids"] == ["SREQ-batch-a-001"]
    assert "--exclude" in paused["command"] and "--continue" in paused["command"] and "--no-pause" in paused["no_pause"]
    assert _scope_pause_step(root, attempt_id, {**step, "code": "MARKER"})["code"] == "MARKER"
    scope_pause.record_decision(root, attempt_id, excluded=[], reason=None, proceed=True)
    assert _scope_pause_step(root, attempt_id, step) == step


def test_the_merge_of_the_parts_leaves_the_draft_questions(tmp_path: Path) -> None:
    from tests.test_marker_parts import _publish_part, _publish_requirements
    from tools.context_marker_parts import merge_parts

    root, attempt_id, baseline, receipts = _run(tmp_path)
    requirements = _publish_requirements(root, attempt_id, baseline)
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — x"], [GAP]))
    merged = merge_parts(root)
    assert merged["questions_draft"] == str(root / "questions-for-analyst.md")
    assert "как проверить оплату?" in (root / "questions-for-analyst.md").read_text(encoding="utf-8")


def test_the_checks_that_compare_the_document_with_the_marker_leave_out_the_excluded() -> None:
    from tools.batch_assembly import in_scope_requirements

    rows = [_requirement(number, f"REQ-{number}") for number in (1, 2, 3)]
    plan = {"out_of_scope": [{"source_requirement_id": "SREQ-0001", "reason": "нет"}]}
    assert in_scope_requirements(rows, None) == rows and in_scope_requirements(rows, {}) == rows
    kept = in_scope_requirements(rows, plan)
    assert [(row["source_requirement_id"], row["display_order"]) for row in kept] == [("SREQ-0002", 1), ("SREQ-0003", 2)]


def test_the_reviewer_and_compatibility_checks_take_the_plan_of_the_run(tmp_path: Path) -> None:
    """A run with an exclusion: the effective document holds the marker's requirements without the excluded one."""
    from tools import scope_pause
    from tools.batch_assembly import plan_batches

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    marker_rows = _marker_rows()
    assert scope_pause.marker_requirements_in_scope(marker_rows, root, attempt_id) == marker_rows  # no plan yet
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Заголовок"})["digest"]
    scope_pause.record_decision(root, attempt_id, excluded=["SREQ-batch-a-001"], reason="нет стенда", proceed=False)
    plan = plan_batches(marker_rows, {"header_digest": header, "context_receipt_digest": receipts[0]["digest"]},
                        out_of_scope=scope_pause.out_of_scope(scope_pause.read_decision(root, attempt_id)))
    pilot_state.publish_generator_input(root, attempt_id, "plan", dict(plan))
    assert scope_pause.marker_requirements_in_scope(marker_rows, root, attempt_id) == plan["source_requirements"]


def test_the_report_names_the_excluded_requirements_with_the_reason(tmp_path: Path) -> None:
    from tools import scope_pause
    from tools.run_report import build_report, render_markdown

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path)
    report, _warnings = build_report(root.parent.parent, root.name)
    assert report["scope"] == {"gaps": 1, "answer": "пауза не снималась: ответа человека нет", "out_of_scope": []}
    scope_pause.record_decision(root, attempt_id, excluded=["SREQ-batch-a-001"], reason="нет стенда с картами", proceed=False)
    report, _warnings = build_report(root.parent.parent, root.name)
    assert report["scope"] == {"gaps": 1, "answer": "исключены",
                               "out_of_scope": [{"source_requirement_id": "SREQ-batch-a-001", "reason": "нет стенда с картами"}]}
    markdown = render_markdown(report)
    assert "## Охват после разметки" in markdown and "вне охвата SREQ-batch-a-001: нет стенда с картами" in markdown


def test_a_run_without_gaps_has_no_scope_section(tmp_path: Path) -> None:
    from tools.run_report import build_report, render_markdown

    root, _attempt_id, _baseline, _receipts = _paused_run(tmp_path, warnings=["док — общее замечание"])
    report, _warnings = build_report(root.parent.parent, root.name)
    assert report["scope"] is None and "Охват после разметки" not in render_markdown(report)


def test_a_requirement_lost_without_an_out_of_scope_entry_is_refused(tmp_path: Path) -> None:
    """Every requirement of the marker is in the plan or in out_of_scope with a reason: a plan that drops one silently is refused
    at publication, and the document check against the marker (reviewer, compatibility) still sees the difference."""
    from tools import scope_pause
    from tools.batch_assembly import in_scope_requirements, plan_batches

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Заголовок"})["digest"]
    scope_pause.record_decision(root, attempt_id, excluded=[], reason=None, proceed=True)
    evidence = {"header_digest": header, "context_receipt_digest": receipts[0]["digest"]}
    lost = dict(plan_batches(_marker_rows()[:1], evidence))  # the second requirement is nowhere
    with pytest.raises(ValueError, match="SCOPE_PLAN_MISMATCH.*SREQ-batch-a-002"):
        pilot_state.publish_generator_input(root, attempt_id, "plan", lost)
    assert in_scope_requirements(_marker_rows(), lost) != lost["source_requirements"]  # the comparison with the marker fails as before


@pytest.mark.parametrize("content", [
    b"{not json", b"[]", b'{"decision":"continue"}',
    json.dumps({"schema_version": "1.0.0", "kind": "scope-decision", "decision": "continue",
                "excluded": [{"source_requirement_id": "SREQ-batch-a-001", "reason": "x"}]}).encode(),  # continue with a list
    json.dumps({"schema_version": "1.0.0", "kind": "scope-decision", "decision": "exclude", "excluded": []}).encode(),  # exclude of nothing
    json.dumps({"schema_version": "1.0.0", "kind": "scope-decision", "decision": "exclude", "extra": 1,
                "excluded": [{"source_requirement_id": "SREQ-batch-a-001", "reason": "x"}]}).encode(),
    json.dumps({"schema_version": "1.0.0", "kind": "scope-decision", "decision": "exclude",
                "excluded": [{"source_requirement_id": "SREQ-batch-a-001", "reason": ""}]}).encode(),
])
def test_a_forged_or_broken_decision_file_fails_closed(tmp_path: Path, content: bytes) -> None:
    from tools import scope_pause

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path)
    pilot_state.publish_run_artifact_bytes(root, attempt_id, scope_pause.DECISION_NAME, content)
    with pytest.raises(scope_pause.ScopeRefused) as stopped:
        scope_pause.read_decision(root, attempt_id)
    assert stopped.value.code == "SCOPE_DECISION_INVALID"
    with pytest.raises(scope_pause.ScopeRefused):  # and so is every use of it: the plan is not published on a forged answer
        scope_pause.scope_state(root, attempt_id)


def test_status_names_a_forged_decision_instead_of_going_on(tmp_path: Path) -> None:
    from tools import scope_pause
    from tools.run_pipeline import _scope_pause_step

    root, attempt_id, _baseline, _receipts = _paused_run(tmp_path)
    pilot_state.publish_run_artifact_bytes(root, attempt_id, scope_pause.DECISION_NAME, b"{}")
    step = {"code": "GENERATOR_PLAN", "stage": "tc-generator", "action": "план", "skill_section": "3", "command": "plan-batches"}
    assert _scope_pause_step(root, attempt_id, step)["code"] == "SCOPE_DECISION_INVALID"
