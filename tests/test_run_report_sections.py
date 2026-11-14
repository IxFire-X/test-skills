"""P9: the report prints by itself what the pilot report template v2 asked the agent to count by hand."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tests.test_run_report import _finished_run


def _report(project: Path, run_root: Path) -> tuple[dict, str]:
    from tools.run_report import build_report, render_markdown

    report = build_report(project, run_root.name)[0]
    return report, render_markdown(report)


def _generated(tmp_path: Path) -> dict:
    from tests.test_review_requested import _generated_run

    return _generated_run(tmp_path)


def test_the_header_tells_model_calls_from_code_steps_and_sreq_from_creq(tmp_path: Path) -> None:
    run_root = _finished_run(tmp_path)
    report, markdown = _report(tmp_path, run_root)
    document = json.loads(next((run_root / "candidate-bundle").glob("*.json")).read_text(encoding="utf-8"))
    counts = report["counts"]
    assert counts["source_requirements"] == len(document["source_requirements"])
    assert counts["canonical_requirements"] == len(document["requirements"])
    assert counts["code_steps"] == 0
    assert f"Вызовов модели {counts['model_calls']}, шагов кодом 0" in markdown
    assert f"исходных SREQ {counts['source_requirements']}, канонических CREQ {counts['canonical_requirements']}" in markdown


def test_a_controller_merge_is_counted_as_a_step_of_code(tmp_path: Path) -> None:
    from tests.test_marker_parts import _marker, _publish_part, _publish_requirements, _run
    from tools.context_marker_parts import merge_parts

    root, attempt_id, baseline, receipts = _run(tmp_path, files=2)
    requirements = _publish_requirements(root, attempt_id, baseline)
    for index, receipt in enumerate(receipts, start=1):
        _publish_part(root, attempt_id, baseline, index, receipt, requirements, _marker([f"src/part{index - 1}.py — наблюдение"]))
    merge_parts(root)
    report, markdown = _report(tmp_path, root)
    assert report["counts"]["code_steps"] == 1
    assert f"Вызовов модели {report['counts']['model_calls']}, шагов кодом 1" in markdown


def test_no_measurements_say_so_and_the_tmp_folder_is_sized_not_removed(tmp_path: Path) -> None:
    run_root = _finished_run(tmp_path)
    tmp = run_root / "tmp"
    tmp.mkdir(exist_ok=True)
    before = sum(path.stat().st_size for path in tmp.rglob("*") if path.is_file())
    (tmp / "fragment.json").write_bytes(b"x" * 1234)
    measured = run_root / "step-sizes.jsonl"
    assert measured.is_file() and measured.stat().st_size > 0  # P1: prepare measures its steps itself
    measured.unlink()  # a run from before the measurements: none of them
    report, markdown = _report(tmp_path, run_root)
    assert report["step_sizes"] == []
    section = markdown.split("## Размер входа шагов", 1)[1].split("\n## ", 1)[0]
    assert "замеров нет" in section and "| Шаг |" not in section
    assert report["tmp"]["bytes"] == before + 1234
    assert report["tmp"]["path"] == f".pilot-runs/{run_root.name}/tmp"
    assert f".pilot-runs/{run_root.name}/tmp" in markdown and ("rm -rf" in markdown or "rmdir" in markdown)
    assert (tmp / "fragment.json").is_file()  # the report removes nothing


def test_the_phases_are_cut_by_the_events_of_the_journal(tmp_path: Path) -> None:
    run = _generated(tmp_path)
    report, markdown = _report(run["project"], run["run_root"])
    phases = {row["phase"]: row for row in report["phases"]}
    assert {"подготовка", "разметка", "план и заголовок", "сборка и пакет ревью", "ревью", "закрытие"} <= set(phases)
    assert phases["разметка"]["sec"] is not None and phases["подготовка"]["sec"] is not None
    assert phases["ревью"]["sec"] is None  # no review yet: not guessed
    batches = [row for row in report["phases"] if row["phase"] == "генерация"]
    assert len(batches) == 2 and all(row["batch"].startswith("BATCH-") and row["sec"] is not None for row in batches)
    assert "## Фазы по времени" in markdown and "генерация" in markdown.split("## Фазы по времени", 1)[1].split("\n## ", 1)[0]


def test_the_input_is_split_by_kind_for_every_model_call(tmp_path: Path) -> None:
    run = _generated(tmp_path)
    report, markdown = _report(run["project"], run["run_root"])
    rows = {row["stage"]: row for row in report["input_composition"]}
    assert set(rows) == {call["stage"] for call in report["model_calls"] if call["by"] == "model"}
    kinds = ("documents", "code", "markup", "plan_header", "role", "map", "other")
    generator = next(row for stage, row in rows.items() if stage.startswith("tc-generator:"))
    assert all(isinstance(generator[kind], int) for kind in kinds)
    call = next(call for call in report["model_calls"] if call["stage"] == generator["stage"])
    assert generator["role"] == call["role_bytes"]
    assert sum(generator[kind] for kind in kinds if kind != "role") == call["input_bytes"]
    assert generator["code"] > 0 and generator["markup"] > 0
    assert "## Состав входа по видам" in markdown


def test_refusals_are_summed_by_command_code_and_batch(tmp_path: Path) -> None:
    run = _generated(tmp_path)
    rows = [
        {"at": "2026-11-03T10:00:00Z", "command": "submit", "code": "BATCH_FRAGMENT_INVALID", "stage": "tc-generator", "batch": "BATCH-a"},
        {"at": "2026-11-03T10:01:00Z", "command": "submit", "code": "BATCH_FRAGMENT_INVALID", "stage": "tc-generator", "batch": "BATCH-a"},
        {"at": "2026-11-03T10:02:00Z", "command": "submit", "code": "BATCH_FRAGMENT_INVALID", "stage": "tc-generator", "batch": "BATCH-b"},
        {"at": "2026-11-03T10:03:00Z", "command": "assemble", "code": "BATCH_SEMANTIC_CONFLICT", "stage": None, "batch": None},
        {"at": "2026-11-03T10:04:00Z", "command": "/home/user/secret project", "code": "some text with a path C:\\x", "stage": None, "batch": None},
    ]
    lines = [json.dumps(row).encode("utf-8") for row in rows]
    (run["run_root"] / "refusals.jsonl").write_bytes(b"".join(line + b"\n" for line in lines) + b"not json\n")
    from tools import pilot_state  # J1: the journal counts — each of the five lines has its COMMAND_REFUSED event; the sixth line («not json») has none

    for line in lines:
        pilot_state.append_event(run["run_root"], "COMMAND_REFUSED", actor="controller", artifact_digest="sha256:" + hashlib.sha256(line).hexdigest())
    report, markdown = _report(run["project"], run["run_root"])
    refusals = report["refusals"]
    assert refusals["mismatch"] == {"lines_without_event": 1, "events_without_line": 0}, refusals  # the line no event confirms is named, not counted
    assert refusals["total"] == 5
    assert refusals["by_command"]["submit"] == 3 and refusals["by_command"]["assemble"] == 1
    assert refusals["by_code"]["BATCH_FRAGMENT_INVALID"] == 3
    assert refusals["by_batch"] == {"BATCH-a": 2, "BATCH-b": 1}
    assert refusals["first_at"] == "2026-11-03T10:00:00Z" and refusals["last_at"] == "2026-11-03T10:04:00Z"
    blob = json.dumps(refusals) + markdown
    assert "/home/user" not in blob and "secret project" not in blob  # only identifiers leave the run
    section = markdown.split("## Отказы", 1)[1].split("\n## ", 1)[0]
    assert "submit" in section and "BATCH_FRAGMENT_INVALID" in section


def test_a_run_before_v02_without_the_refusal_journal_says_so(tmp_path: Path) -> None:
    run = _generated(tmp_path)
    (run["run_root"] / "refusals.jsonl").unlink(missing_ok=True)
    (run["run_root"] / "step-sizes.jsonl").unlink(missing_ok=True)  # v0.2 measures every step it sends to the model; a run before v0.2 has neither file
    report, markdown = _report(run["project"], run["run_root"])
    assert report["refusals"] is None
    assert "журнала отказов нет (прогон до v0.2)" in markdown.split("## Отказы", 1)[1].split("\n## ", 1)[0]


def test_a_v02_run_without_a_single_refusal_prints_zero(tmp_path: Path) -> None:
    """The journal is created by the first refusal, so a clean v0.2 run has none; the step measurements are what only v0.2 writes."""
    run = _generated(tmp_path)
    (run["run_root"] / "refusals.jsonl").unlink(missing_ok=True)
    assert (run["run_root"] / "step-sizes.jsonl").is_file()
    report, markdown = _report(run["project"], run["run_root"])
    assert report["refusals"]["total"] == 0 and report["refusals"]["by_command_code"] == [] and report["refusals"]["first_at"] is None
    section = markdown.split("## Отказы", 1)[1].split("\n## ", 1)[0]
    assert "Всего: 0" in section and "до v0.2" not in section and "журнала отказов нет" not in section


def test_the_canon_in_numbers_and_the_empty_status_counter(tmp_path: Path) -> None:
    run_root = _finished_run(tmp_path)
    report, markdown = _report(tmp_path, run_root)
    document = json.loads(next((run_root / "candidate-bundle").glob("*.json")).read_text(encoding="utf-8"))
    canon = report["canon"]
    cases = document["test_cases"]
    assert canon["cases"] == len(cases) and canon["bytes"] > 0
    assert canon["bytes_per_case"] == round(canon["bytes"] / len(cases))
    steps = sum(len(case["steps"]) for case in cases)
    assert canon["steps_per_case"] == round(steps / len(cases), 1)
    assert canon["blockers"] == sum(len(step.get("automation_blockers") or []) for case in cases for step in case["steps"])
    assert "## Канон в числах" in markdown
    assert "status_after_pause" not in report["counts"] and "status_after_pause" not in markdown  # V9b: the field of P3 that was never filled is gone


# --- PH: the command that removes tmp/ is written for the shell of the machine ---------------------------------------------------------

def test_the_remove_command_is_powershell_first_with_the_cmd_form_beside_it_on_windows() -> None:
    from tools.run_report import remove_commands

    ps, cmd = remove_commands(".pilot-runs/r1/tmp", "win32")
    assert ps == r"Remove-Item -LiteralPath '.pilot-runs\r1\tmp' -Recurse -Force"
    assert cmd == r'rmdir /s /q ".pilot-runs\r1\tmp"'
    assert remove_commands(".pilot-runs/r1/tmp", "linux") == ('rm -rf ".pilot-runs/r1/tmp"', None)
    assert remove_commands(".pilot-runs/r1/tmp", "darwin") == ('rm -rf ".pilot-runs/r1/tmp"', None)


def test_a_path_with_a_space_or_an_apostrophe_is_quoted_for_each_shell() -> None:
    from tools.run_report import remove_commands

    ps, cmd = remove_commands(".pilot-runs/my run/tmp", "win32")
    assert ps == r"Remove-Item -LiteralPath '.pilot-runs\my run\tmp' -Recurse -Force" and cmd == r'rmdir /s /q ".pilot-runs\my run\tmp"'
    ps, cmd = remove_commands(".pilot-runs/o'brien/tmp", "win32")
    assert ps == r"Remove-Item -LiteralPath '.pilot-runs\o''brien\tmp' -Recurse -Force"  # the apostrophe is doubled inside single quotes
    assert cmd == r'rmdir /s /q ".pilot-runs\o' + "'" + r'brien\tmp"'
    assert remove_commands(".pilot-runs/o'brien/tmp", "linux")[0] == 'rm -rf ".pilot-runs/o\'brien/tmp"'


def test_the_report_carries_the_command_for_the_shell_and_says_cmd_beside_it(tmp_path: Path, monkeypatch) -> None:
    from tools import run_report

    root = tmp_path / ".pilot-runs" / "my o'run"
    (root / "tmp").mkdir(parents=True)
    (root / "tmp" / "a.json").write_bytes(b"x")
    monkeypatch.setattr(run_report.sys, "platform", "win32")
    usage = run_report.tmp_usage(root, tmp_path)
    assert usage["remove_command"] == r"Remove-Item -LiteralPath '.pilot-runs\my o''run\tmp' -Recurse -Force"
    assert usage["remove_command_cmd"] == r'rmdir /s /q ".pilot-runs\my o' + "'" + r'run\tmp"'
    lines = "\n".join(run_report._tmp_lines({"tmp": usage}))
    assert f"`{usage['remove_command']}`" in lines and f"в cmd: `{usage['remove_command_cmd']}`" in lines and "Отчёт ничего не удаляет" in lines
    monkeypatch.setattr(run_report.sys, "platform", "linux")
    usage = run_report.tmp_usage(root, tmp_path)
    assert usage["remove_command"] == 'rm -rf ".pilot-runs/my o\'run/tmp"' and "remove_command_cmd" not in usage
    assert "в cmd" not in "\n".join(run_report._tmp_lines({"tmp": usage}))
    assert (root / "tmp" / "a.json").is_file()  # the report removes nothing


def _phases_of_a_run_with_automation() -> dict[str, float | None]:
    """The journal of the live run pq1-v05d in outline: the context is selected again when the review opens, the automation follows the review of the cases."""
    from tools.run_report import phases

    rows = [("RUN_CREATED", None, 0), ("CONTEXT_SELECTED", None, 1), ("MODEL_REQUESTED", "context-marker:baseline", 5), ("MODEL_RESPONSE_RECEIVED", "context-marker:baseline", 100),
            ("REVIEW_REQUESTED", "tc-reviewer:canonical", 1000), ("CONTEXT_SELECTED", None, 1100), ("MODEL_RESPONSE_RECEIVED", "tc-reviewer:canonical", 1101),
            ("MODEL_RESPONSE_RECEIVED", "autotest-reviewer:r1", 1700), ("ATTEMPT_TERMINAL", None, 1800)]
    events = [{"seq": number, "event_type": kind, "stage_instance_id": stage, "observed_at": f"2026-11-14T12:{second // 60:02d}:{second % 60:02d}Z"} for number, (kind, stage, second) in enumerate(rows, 1)]
    return {row["phase"]: row["sec"] for row in phases(events)}


def test_the_preparation_ends_at_the_first_selection_of_the_context() -> None:
    assert _phases_of_a_run_with_automation()["подготовка"] == 1


def test_the_closing_does_not_hold_the_automation_of_the_run() -> None:
    result = _phases_of_a_run_with_automation()
    assert result["автотесты"] == 599 and result["закрытие"] == 100
