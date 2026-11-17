"""upd-b2 part 2: an import with documents judges the old cases — the model gives each judged row a verdict (fits / rewrite /
obsolete), code decides which rows are judged (the delta of OpenSpec) and refuses an answer that breaks the rules."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_cases_import import _project, _scan
from tests.test_cases_import_docs import DOC, PACK_ROOT, _request_requirements

SPEC = ("## Requirements\n### Requirement: Join\ncombine joins two strings.\n#### Scenario: Joined\n- WHEN combine\n- THEN a:b\n"
        "### Requirement: Refuse\ncombine refuses None.\n#### Scenario: Refused\n- WHEN None\n- THEN error\n")
MODIFY = "## MODIFIED Requirements\n### Requirement: Join\ncombine joins two strings with a colon.\n#### Scenario: Joined\n- WHEN combine\n- THEN a:b\n"
REMOVE = "## REMOVED Requirements\n### Requirement: Refuse\nReason: retired.\n"
BASE, LIVE, ARCHIVE = ("openspec/specs/catalog/spec.md", "openspec/changes/update/specs/catalog/spec.md",
                       "openspec/changes/archive/2026-11-01-update/specs/catalog/spec.md")


def _write(project: Path, path: str, text: str) -> None:
    (project / path).parent.mkdir(parents=True, exist_ok=True)
    (project / path).write_bytes(text.encode("utf-8"))


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delta: str | None, docs: list[str], path: str = LIVE) -> tuple[Path, Path, str, list[dict]]:
    """A ``--cases`` + ``--docs`` run, the requirements step asked: ``(project, root, attempt, requirements)`` — the requirements are
    what the documents compose (the live delta is applied, an archived one is not)."""
    from tools.build_context import build_context

    project = _project(tmp_path)
    _write(project, BASE, SPEC)
    entries = [{"path": BASE, "content": SPEC}]
    if delta is not None:
        _write(project, path, delta)
        entries.append({"path": path, "content": delta})
    import hashlib

    entries = [{**entry, "sha256": "sha256:" + hashlib.sha256(entry["content"].encode("utf-8")).hexdigest()} for entry in entries]
    code, scan = _scan(project, monkeypatch, docs=docs)
    assert code == 0, scan
    root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    rows = build_context(PACK_ROOT, docs_snapshot=entries)["artifacts"]["analytics_documentation"]["requirements"]
    _request_requirements(root, attempt_id)
    return project, root, attempt_id, rows


def _publish(root: Path, attempt_id: str, rows: list[dict], links: list[dict]):
    from tools import pilot_state
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    answer = {"schema_version": "5.0.0", "stage": "context-marker", "warnings": [],
              "artifacts": {"analytics_documentation": {"requirements": rows, "case_links": links},
                            "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}}}
    return pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer)


def _refused(root: Path, attempt_id: str, rows: list[dict], links: list[dict], named: str) -> None:
    from tools.cases_import import CasesImportError

    with pytest.raises(CasesImportError, match="IMPORT_CASE_LINKS") as refused:
        _publish(root, attempt_id, rows, links)
    assert named in str(refused.value)


def _by_name(rows: list[dict]) -> dict[str, str]:
    return {row["text"].split("\n")[0].split(": ")[-1]: row["source_requirement_id"] for row in rows}


LIVE_DOCS = ["openspec/specs/catalog/spec.md", "openspec/changes/update"]


def test_a_judged_row_without_a_verdict_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY, LIVE_DOCS)
    join = _by_name(rows)["Join"]
    _refused(root, attempt_id, rows, [{"row": 2, "source_requirement_ids": [join]}, {"row": 3, "source_requirement_ids": [], "reason": "нет"}], "строка 2: нет verdict")


def test_a_row_the_delta_does_not_touch_needs_no_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY, LIVE_DOCS)
    names = _by_name(rows)
    links = [{"row": 2, "source_requirement_ids": [names["Join"]], "verdict": "fits"},
             {"row": 3, "source_requirement_ids": [names["Refuse"]]}]
    assert _publish(root, attempt_id, rows, links)["content_digest"]


def test_a_rewrite_needs_a_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY, LIVE_DOCS)
    _refused(root, attempt_id, rows, [{"row": 2, "source_requirement_ids": [_by_name(rows)["Join"]], "verdict": "rewrite"}, {"row": 3, "source_requirement_ids": [], "reason": "нет"}],
             "строка 2: rewrite без reason")


def test_a_rewrite_of_a_row_the_delta_does_not_touch_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY, LIVE_DOCS)
    names = _by_name(rows)
    links = [{"row": 2, "source_requirement_ids": [names["Join"]], "verdict": "fits"},
             {"row": 3, "source_requirement_ids": [names["Refuse"]], "verdict": "rewrite", "reason": "x"}]
    _refused(root, attempt_id, rows, links, "строка 3: verdict rewrite вне области")


def test_an_obsolete_row_links_no_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY + REMOVE, LIVE_DOCS)
    names = _by_name(rows)
    links = [{"row": 2, "source_requirement_ids": [names["Join"]], "verdict": "fits"},
             {"row": 3, "source_requirement_ids": [names["Join"]], "verdict": "obsolete", "reason": "x", "removed": {"capability": "catalog", "name": "Refuse"}}]
    _refused(root, attempt_id, rows, links, "строка 3: obsolete со связями")


def test_an_unlinked_row_needs_a_verdict_when_the_delta_removes_a_requirement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY + REMOVE, LIVE_DOCS)
    links = [{"row": 2, "source_requirement_ids": [_by_name(rows)["Join"]], "verdict": "fits"}, {"row": 3, "source_requirement_ids": [], "reason": "нет в спеке"}]
    _refused(root, attempt_id, rows, links, "строка 3: нет verdict")


def test_an_obsolete_row_names_a_requirement_the_delta_removes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY + REMOVE, LIVE_DOCS)
    links = [{"row": 2, "source_requirement_ids": [_by_name(rows)["Join"]], "verdict": "fits"},
             {"row": 3, "source_requirement_ids": [], "verdict": "obsolete", "reason": "x", "removed": {"capability": "catalog", "name": "Join"}}]
    _refused(root, attempt_id, rows, links, "строка 3: removed не среди удалённых")


def test_an_obsolete_row_of_a_removed_requirement_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY + REMOVE, LIVE_DOCS)
    links = [{"row": 2, "source_requirement_ids": [_by_name(rows)["Join"]], "verdict": "fits"},
             {"row": 3, "source_requirement_ids": [], "verdict": "obsolete", "reason": "требование удалено", "removed": {"capability": "catalog", "name": "Refuse"}}]
    assert _publish(root, attempt_id, rows, links)["content_digest"]


def test_without_a_delta_every_row_is_judged_and_nothing_is_obsolete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, None, ["openspec/specs/catalog/spec.md"])
    names = _by_name(rows)
    _refused(root, attempt_id, rows, [{"row": 2, "source_requirement_ids": [names["Join"]]}, {"row": 3, "source_requirement_ids": [names["Refuse"]], "verdict": "fits"}], "строка 2: нет verdict")
    _refused(root, attempt_id, rows, [{"row": 2, "source_requirement_ids": [names["Join"]], "verdict": "fits"},
                                      {"row": 3, "source_requirement_ids": [], "verdict": "obsolete", "reason": "x", "removed": {"capability": "catalog", "name": "Refuse"}}],
             "строка 3: removed не среди удалённых")


def test_a_removed_without_obsolete_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, None, ["openspec/specs/catalog/spec.md"])
    names = _by_name(rows)
    links = [{"row": 2, "source_requirement_ids": [names["Join"]], "verdict": "fits", "removed": {"capability": "catalog", "name": "Join"}},
             {"row": 3, "source_requirement_ids": [names["Refuse"]], "verdict": "fits"}]
    _refused(root, attempt_id, rows, links, "строка 2: removed без obsolete")


@pytest.mark.parametrize("docs", [["openspec/specs/catalog/spec.md", "./" + ARCHIVE], ["openspec/specs/catalog/spec.md", "ABSOLUTE"]])
def test_a_named_archive_file_gives_the_archive_scope_however_it_is_spelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, docs: list[str]) -> None:
    project = tmp_path / "project"
    docs = [str(project / ARCHIVE) if item == "ABSOLUTE" else item for item in docs]
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, MODIFY, docs, path=ARCHIVE)
    names = _by_name(rows)  # the archive is not applied: Join stays as the base has it
    _refused(root, attempt_id, rows, [{"row": 2, "source_requirement_ids": [names["Join"]]}, {"row": 3, "source_requirement_ids": [names["Refuse"]]}], "строка 2: нет verdict")
    links = [{"row": 2, "source_requirement_ids": [names["Join"]], "verdict": "fits"}, {"row": 3, "source_requirement_ids": [names["Refuse"]]}]
    assert _publish(root, attempt_id, rows, links)["content_digest"]  # row 3 is not judged: the scope is «archive», not «all»


def test_a_delta_none_of_whose_names_is_a_requirement_stops_the_scan_before_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    refusal = _scan_refused(tmp_path, monkeypatch, {ARCHIVE: MODIFY.replace("Join", "Elsewhere"), BASE: SPEC}, [BASE, ARCHIVE], capsys, no_run=False)
    assert refusal["reason"] == "OPENSPEC_SOURCE_CONFLICT" and "catalog/Elsewhere" in refusal["detail"], refusal


def test_a_delta_some_of_whose_names_are_gone_names_them_in_the_warnings_of_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    for path, text in {ARCHIVE: MODIFY + MODIFY.replace("## MODIFIED Requirements\n", "").replace("Join", "Elsewhere"), BASE: SPEC}.items():
        _write(project, path, text)
    code, scan = _scan(project, monkeypatch, docs=[BASE, ARCHIVE])
    assert code == 0 and any("catalog/Elsewhere" in warning and "Join" not in warning for warning in scan["warnings"]), scan


def _twenty_rows_without_a_verdict(without_link: int | None = None) -> str:
    from tools.cases_import import CasesImportError, require_case_links
    from tools.cases_scope import Scope

    rows = range(2, 22)
    links = [{"row": row, "source_requirement_ids": ["SREQ-0001"]} for row in rows if row != without_link]
    with pytest.raises(CasesImportError, match="IMPORT_CASE_LINKS") as refused:
        require_case_links([{"row": row} for row in rows], [{"source_requirement_id": "SREQ-0001", "provenance": []}], links, Scope("all", frozenset(), frozenset()))
    return str(refused.value)


def test_twenty_rows_without_a_verdict_are_one_entry_that_names_them_all() -> None:
    message = _twenty_rows_without_a_verdict()
    assert "строки " + ", ".join(str(row) for row in range(2, 22)) + ": нет verdict" in message and message.count("нет verdict") == 1, message


def test_the_verdicts_are_refused_together_with_a_missing_link() -> None:
    message = _twenty_rows_without_a_verdict(without_link=3)
    assert "строка 3: нет связи" in message and "нет verdict" in message, message


def _scan_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, files: dict[str, str], docs: list[str], capsys: pytest.CaptureFixture[str], no_run: bool = True) -> dict:
    from tools import run_pipeline
    from types import SimpleNamespace

    project = _project(tmp_path)
    for path, text in files.items():
        _write(project, path, text)
    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    args = SimpleNamespace(project=str(project), module="python-pytest", target=None, docs=docs, cases="cases/zephyr.csv", profile="local-pilot-v1", new_run=True)
    assert run_pipeline.cmd_scan(args) == 2, printed
    assert not no_run or not (project / ".pilot-runs").exists() or not list((project / ".pilot-runs").iterdir()), "a refused scan leaves no run"
    return printed[-1]


def test_a_named_archive_proposal_stops_the_scan_and_names_the_delta_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    proposal = "openspec/changes/archive/2026-11-01-update/proposal.md"
    refusal = _scan_refused(tmp_path, monkeypatch, {proposal: "# Proposal\n", ARCHIVE: MODIFY, BASE: SPEC}, [BASE, proposal], capsys)
    assert refusal["reason"] == "SNAPSHOT_DOCS" and "specs/<область>/spec.md" in refusal["fix"], refusal


def test_a_live_change_without_an_operation_stops_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    refusal = _scan_refused(tmp_path, monkeypatch, {LIVE: "# nothing here\n", BASE: SPEC}, [BASE, LIVE], capsys)
    assert refusal["reason"] == "SNAPSHOT_DOCS" and "операц" in refusal["detail"] and "допишите операции" in refusal["fix"], refusal


def test_an_archived_delta_named_with_a_section_stops_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    refusal = _scan_refused(tmp_path, monkeypatch, {ARCHIVE: MODIFY, BASE: SPEC}, [BASE, ARCHIVE + "#MODIFIED Requirements"], capsys)
    assert refusal["reason"] == "SNAPSHOT_DOCS" and "целиком" in refusal["fix"], refusal


def test_a_large_file_outside_the_project_is_refused_as_outside_not_as_too_large(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "huge.md").write_bytes(b"x" * (2 * 1024 * 1024))  # beside the project folder
    refusal = _scan_refused(tmp_path, monkeypatch, {BASE: SPEC}, [BASE, "../huge.md"], capsys)
    assert refusal["reason"] == "SNAPSHOT_DOCS" and "outside the project" in refusal["detail"], refusal


SECOND = "openspec/changes/archive/2026-11-01-update/specs/other/spec.md"


def test_one_of_two_deltas_of_an_archived_change_stops_the_scan_and_names_the_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    refusal = _scan_refused(tmp_path, monkeypatch, {ARCHIVE: MODIFY, SECOND: MODIFY, BASE: SPEC}, [BASE, ARCHIVE], capsys, no_run=False)  # refused after the run is made, before any model call
    assert refusal["reason"] == "OPENSPEC_SOURCE_CONFLICT" and "openspec/changes/archive/2026-11-01-update" in refusal["fix"], refusal


def test_the_folder_of_an_archived_change_names_all_its_deltas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    for path, text in {ARCHIVE: MODIFY, SECOND: MODIFY, BASE: SPEC}.items():
        _write(project, path, text)
    code, scan = _scan(project, monkeypatch, docs=[BASE, "openspec/changes/archive/2026-11-01-update"])
    assert code == 0, scan


def test_an_archived_change_of_an_area_whose_spec_is_not_named_stops_the_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    refusal = _scan_refused(tmp_path, monkeypatch, {ARCHIVE: MODIFY, BASE: SPEC}, [ARCHIVE], capsys, no_run=False)  # refused after the run is made, before any model call
    assert refusal["reason"] == "OPENSPEC_SOURCE_CONFLICT" and f'--docs "{BASE}"' in refusal["fix"], refusal


def _ready_stop(tmp_path: Path, files: dict[str, str], docs: list[str]) -> tuple[str, dict]:
    from tools import ready

    project = _project(tmp_path)
    for path, text in files.items():
        _write(project, path, text)
    report = ready.check(project, docs, "cases-only-v1", cases="cases/zephyr.csv")
    return report["status"], next(row for row in report["checks"] if row["id"] == "context:openspec")


def test_ready_with_cases_stops_where_scan_stops_on_an_archived_area_not_named(tmp_path: Path) -> None:
    status, row = _ready_stop(tmp_path, {ARCHIVE: MODIFY, BASE: SPEC}, [ARCHIVE])  # was READY
    assert status == "BLOCKED" and row["level"] == "STOP" and BASE in row["fix"], row


def test_ready_with_cases_stops_where_scan_stops_on_one_of_two_deltas_of_an_archive(tmp_path: Path) -> None:
    status, row = _ready_stop(tmp_path, {ARCHIVE: MODIFY, SECOND: MODIFY, BASE: SPEC}, [BASE, ARCHIVE])
    assert status == "BLOCKED" and "openspec/changes/archive/2026-11-01-update" in row["fix"], row


ADD = "## ADDED Requirements\n### Requirement: Trim\ncombine trims blanks.\n#### Scenario: Trimmed\n- WHEN combine\n- THEN trimmed\n"


def test_a_row_linked_to_a_requirement_the_delta_adds_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, ADD, LIVE_DOCS)
    names = _by_name(rows)
    links = [{"row": 2, "source_requirement_ids": [names["Join"], names["Trim"]], "verdict": "fits"}, {"row": 3, "source_requirement_ids": [names["Refuse"]]}]
    _refused(root, attempt_id, rows, links, f"строка 2: {names['Trim']} добавлено изменением")


def test_an_added_requirement_no_row_links_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project_dir, root, attempt_id, rows = _run(tmp_path, monkeypatch, ADD, LIVE_DOCS)
    names = _by_name(rows)
    links = [{"row": 2, "source_requirement_ids": [names["Join"]]}, {"row": 3, "source_requirement_ids": [names["Refuse"]]}]
    assert _publish(root, attempt_id, rows, links)["content_digest"]
