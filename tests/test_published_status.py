"""RV: `published/` holds only the outcome of the review — the import CSV exists for an accepted set alone; a rejected set
and a run closed without a verdict leave a status file (REJECTED / UNREVIEWED) with a header, never a CSV."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _generated_run, _put
from tests.test_review_two_commands import _api, _bundles, _flags, _open, _select_verdict, _verdict


def _files(run: dict, pattern: str) -> list[Path]:
    return sorted(Path(_bundles(run)).glob(pattern))


def _rejected_run(tmp_path: Path, message: str | None = None) -> dict:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    answer = Path(_verdict(run, "ТРЕБУЕТ ДОРАБОТКИ"))
    if message is not None:
        answer.write_text(answer.read_text(encoding="utf-8").replace("Нужна доработка.", message), encoding="utf-8")
    code, selected = _select_verdict(run, str(answer))
    assert code == 0, selected
    return run


def test_review_publish_writes_no_import_csv_before_the_verdict(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    assert _files(run, "*.csv") == [] and _files(run, "*.json") == []


def test_an_accepted_set_has_the_import_csv(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    assert _select_verdict(run, _verdict(run))[0] == 0
    assert len(_files(run, "*.zephyr-scale.csv")) == 1 and len(_files(run, "*.json")) == 1


def test_a_rejected_set_has_no_csv_and_a_rejected_file_with_the_blocking_finding(tmp_path: Path) -> None:
    run = _rejected_run(tmp_path)
    assert _files(run, "*.csv") == []
    markdown, html = _files(run, "*.REJECTED.md"), _files(run, "*.REJECTED.html")
    assert len(markdown) == len(html) == 1
    text = markdown[0].read_text(encoding="utf-8")
    assert text.startswith("> **ОТКЛОНЁН РЕВЬЮЕРОМ") and r"REVIEW\_REJECTED" in text and "Нужна доработка." in text
    page = html[0].read_text(encoding="utf-8")
    assert "<body><div" in page and "ОТКЛОНЁН РЕВЬЮЕРОМ" in page and "REVIEW_REJECTED" in page


def test_the_reviewer_text_is_escaped_in_both_files(tmp_path: Path) -> None:
    run = _rejected_run(tmp_path, "<script>alert(1)</script></body> **x**")
    html = _files(run, "*.REJECTED.html")[0].read_text(encoding="utf-8")
    markdown = _files(run, "*.REJECTED.md")[0].read_text(encoding="utf-8")
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html and html.count("</body>") == 1
    assert "<script>" not in markdown and "&lt;/body&gt;" in markdown


def test_a_run_closed_without_a_verdict_leaves_an_unreviewed_file_and_no_csv(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_closure_deadlocks import _run

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    code, aborted = _api("review-abort", *_flags(run), "--reason", "REVIEW_UNUSABLE")
    assert code == 0, aborted
    session = _put(run["tmp"] / "session.json", {key: aborted[key] for key in aborted if key not in {"status", "stage"}} if "events" in aborted else aborted["result"])
    code, selected = _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--reviewer-session", session)
    assert code == 0, selected
    capsys.readouterr()
    _code, closed = _run(capsys, "finish", run["project"], run["run_root"])
    assert len(closed["unreviewed_files"]) == 2, closed  # the closure itself is negative (exit 1): nothing was reviewed
    assert _files(run, "*.csv") == [] and len(_files(run, "*.UNREVIEWED.html")) == 1
    assert _files(run, "*.UNREVIEWED.md")[0].read_text(encoding="utf-8").startswith("> **НЕ ПРОВЕРЕН РЕВЬЮЕРОМ")
    assert "<body><div" in _files(run, "*.UNREVIEWED.html")[0].read_text(encoding="utf-8")


def test_select_again_after_the_set_was_written_but_not_journaled_finishes_the_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state
    from tools.orchestrate_test_case_revision import OrchestrationError, review_select_step

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    raw = Path(_verdict(run)).read_bytes()
    select = lambda: review_select_step(run["run_root"], run["attempt_id"], files={}, output_dir=Path(_bundles(run)),
                                        verdict=json.loads(raw), verdict_bytes=len(raw))
    with monkeypatch.context() as patch:
        patch.setattr(pilot_state, "publish_effective_canonical", lambda *a, **k: (_ for _ in ()).throw(ValueError("crash")))
        with pytest.raises(OrchestrationError):
            select()
    written = {path.name: path.read_bytes() for path in _files(run, "*.*")}
    assert any(name.endswith(".zephyr-scale.csv") for name in written)
    assert select()["status"] == "EFFECTIVE_SELECTED"
    assert {path.name: path.read_bytes() for path in _files(run, "*.*") if path.name in written} == written
    assert _api("read-effective", *_flags(run))[1]["result"]["document_digest"]


def test_a_file_that_is_not_the_set_blocks_the_publication_of_the_accepted_set(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    answer = _verdict(run)
    (Path(_bundles(run))).mkdir(exist_ok=True)
    (Path(_bundles(run)) / "TCDOC-pilot.r1.json").write_bytes(b"{}")
    code, refused = _select_verdict(run, answer)
    assert code == 2 and refused["code"] == "ORCHESTRATION_PUBLICATION", refused
    assert _files(run, "*.csv") == []


def _report(run: dict) -> tuple[dict, str]:
    from tools.run_report import write_report

    write_report(run["project"], run["run_root"].name)
    return json.loads((run["run_root"] / "report.json").read_text(encoding="utf-8")), (run["run_root"] / "report.md").read_text(encoding="utf-8")


def test_status_report_and_the_reviewer_step_work_before_the_verdict_with_no_set_on_disk(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_closure_deadlocks import _run

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    assert _files(run, "*.json") == []
    code, status = _run(capsys, "status", run["project"], run["run_root"])
    assert code == 0 and status["status"] == "ok", status
    code, inputs = _api("step-inputs", *_flags(run)[:2], "--stage", "tc-reviewer:canonical")
    assert code == 0 and inputs["result"]["unresolved"] == [], inputs
    report, markdown = _report(run)
    reviewer = next(call for call in report["model_calls"] if call["stage"] == "tc-reviewer:canonical")
    assert reviewer["size_complete"] and report["canon"]["cases"] > 0 and "кандидат" in markdown and "канона нет" not in markdown


def test_finish_and_report_after_a_rejection_count_the_cases_of_the_candidate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_closure_deadlocks import _run
    from tools.orchestrate_test_case_revision import journal_assembly

    run = _rejected_run(tmp_path)
    cases = len(journal_assembly(run["run_root"], run["attempt_id"])[0]["test_cases"])
    _code, closed = _run(capsys, "finish", run["project"], run["run_root"])
    assert closed["status"] == "ok" and "unreviewed_files" not in closed, closed
    report, _markdown = _report(run)
    assert report["canon"]["cases"] == cases and report["counts"]["cases"] == cases
    assert _files(run, "*.csv") == [] and len(_files(run, "*.UNREVIEWED.*")) == 0


def test_a_preview_without_the_body_tag_is_refused_not_published_without_the_header(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import publish_test_case_bundle as bundle

    document = json.loads((Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json").read_text(encoding="utf-8"))
    real = bundle._render_html_preview_validated
    monkeypatch.setattr(bundle, "_render_html_preview_validated",
                        lambda *args: type("Page", (), {"payload": real(*args).payload.replace(b"<body>", b'<body class="x">')})())
    with pytest.raises(ValueError, match="STATUS_HEADER_NOT_PLACED"):
        bundle.status_payloads(document, bundle._PROFILE, "REJECTED")

