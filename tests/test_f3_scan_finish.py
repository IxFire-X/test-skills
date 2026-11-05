"""F3 «scan и finish»: hunt 8.2 = A2-3 (scan cut between context batches), A1-4 (finish branch by the journal), 8.8 (--abandon on a
closed attempt), 8.6 (the drift of a context file is named as drift).  Each test asserts the right behaviour."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_requested import _api, _generated_run, _journal, _put
from tests.test_review_two_commands import _bundles, _events, _flags, _open, _verdict


def _main(capsys, argv: list[str]) -> tuple[int, dict]:
    from tools import api

    code = api.main(argv)
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _pipeline(capsys, argv: list[str]) -> tuple[int, dict]:
    from tools import run_pipeline

    code = run_pipeline.main(argv)
    out = capsys.readouterr().out
    body = out.rsplit("\nrun_id: ", 1)[0] if "\nrun_id: " in out else out
    return code, json.loads(body.strip())


def _crash_nth(monkeypatch, owner, name: str, nth: int = 1) -> None:
    """The nth call of ``owner.name`` raises OSError once; every other call runs the real function."""
    real = getattr(owner, name)
    state = {"seen": 0}

    def crash(*args, **kwargs):
        state["seen"] += 1
        if state["seen"] == nth:
            raise OSError(f"simulated crash in {name} (call #{nth})")
        return real(*args, **kwargs)

    monkeypatch.setattr(owner, name, crash)


# --- 8.2 = A2-3: scan cut between the context batches ---------------------------------------------------------------

def _two_batch_project(tmp_path: Path) -> Path:
    from tests.test_first_run import _python_project

    project = _python_project(tmp_path, runtime=False)
    for index in range(6):
        (project / "services" / "api" / "src" / f"part{index}.py").write_text(f"VALUE_{index} = {index}\n" * 40, encoding="utf-8")
    value = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    value["limits"] = {"context_batch_bytes": 1024}  # the schema's minimum
    (project / ".skillsrc").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return project


def _scan_argv(project: Path, *extra: str) -> list[str]:
    return ["scan", "--project", str(project), "--docs", "services/api/docs/feature.md", "--module", "nested", "--profile", "cases-only-v1", *extra]


def _selected(project: Path, run_id: str) -> int:
    return sum(1 for row in _events(project / ".pilot-runs" / run_id) if row["event_type"] == "CONTEXT_SELECTED")


def _runs(project: Path) -> list[Path]:
    return sorted(path for path in (project / ".pilot-runs").iterdir() if path.is_dir())


def _cut_scan(tmp_path: Path, monkeypatch, capsys, nth: int) -> tuple[Path, int, dict]:
    """A project of several context batches, a scan cut on the nth ``publish_context_selection``; the whole scan's batch count."""
    from tools import pilot_state

    whole = _two_batch_project(tmp_path / "whole")
    code, done = _pipeline(capsys, _scan_argv(whole))
    assert code == 0, done
    expected = _selected(whole, done["run_id"])
    assert expected >= 2, "the fixture must split into several context batches"
    project = _two_batch_project(tmp_path / "cut")
    with monkeypatch.context() as broken:
        _crash_nth(broken, pilot_state, "publish_context_selection", nth)
        code, cut = _pipeline(capsys, _scan_argv(project))
    assert code == 2, cut
    return project, expected, cut


def test_a_scan_cut_between_the_context_batches_is_finished_by_the_same_scan(tmp_path: Path, monkeypatch, capsys) -> None:
    project, expected, _cut = _cut_scan(tmp_path, monkeypatch, capsys, nth=2)
    [root] = _runs(project)
    assert _selected(project, root.name) == 1
    code, again = _pipeline(capsys, _scan_argv(project))
    assert code == 0, again
    assert again["run_id"] == root.name and len(_runs(project)) == 1, again  # the same run, not a second one
    assert _selected(project, root.name) == expected and again["context_batch_count"] == expected and again.get("resumed") is True, again
    code, third = _pipeline(capsys, _scan_argv(project))  # the finished run is the live one now
    assert code == 2 and third["reason"] == "SCAN_RUN_IN_PROGRESS" and _selected(project, root.name) == expected, third


def test_the_cut_run_is_named_incomplete_until_the_scan_is_repeated(tmp_path: Path, monkeypatch, capsys) -> None:
    from tools import context_marker_parts

    project, expected, _cut = _cut_scan(tmp_path, monkeypatch, capsys, nth=2)
    [root] = _runs(project)
    code, status = _pipeline(capsys, ["status", "--project", str(project), "--run", root.name])
    assert status["next_step"]["code"] == "SCAN_INCOMPLETE" and "scan" in status["next_step"]["action"], status["next_step"]
    assert f"1 из {expected}" in status["next_step"]["action"]
    with pytest.raises(ValueError, match="SCAN_INCOMPLETE"):
        context_marker_parts.plan(root)
    _pipeline(capsys, _scan_argv(project))
    code, status = _pipeline(capsys, ["status", "--project", str(project), "--run", root.name])
    assert status["next_step"]["code"] != "SCAN_INCOMPLETE"
    assert context_marker_parts.plan(root)["parts"]


def test_a_cut_run_whose_files_drifted_is_abandoned_not_finished(tmp_path: Path, monkeypatch, capsys) -> None:
    project, expected, _cut = _cut_scan(tmp_path, monkeypatch, capsys, nth=2)
    [root] = _runs(project)
    (project / "services" / "api" / "src" / "part0.py").write_text("VALUE_0 = 'changed'\n" * 40, encoding="utf-8")
    code, again = _pipeline(capsys, _scan_argv(project))
    assert code == 0 and again["run_id"] != root.name, again
    assert any(root.name in row and "дрейф" in row for row in again.get("abandoned_runs") or []), again.get("abandoned_runs")
    assert _selected(project, again["run_id"]) == again["context_batch_count"]


def test_a_scan_cut_before_the_first_batch_leaves_a_named_abandoned_run(tmp_path: Path, monkeypatch, capsys) -> None:
    project, _expected, _cut = _cut_scan(tmp_path, monkeypatch, capsys, nth=1)
    [root] = _runs(project)
    assert _selected(project, root.name) == 0
    code, again = _pipeline(capsys, _scan_argv(project))
    assert code == 0 and again["run_id"] != root.name, again
    assert any(root.name in row and "до первой пачки" in row for row in again.get("abandoned_runs") or []), again.get("abandoned_runs")


def test_a_whole_scan_marks_its_batches_and_runs_before_the_mark_are_complete(tmp_path: Path, capsys) -> None:
    from tools import pilot_state

    project = _two_batch_project(tmp_path)
    code, done = _pipeline(capsys, _scan_argv(project))
    assert code == 0, done
    root = project / ".pilot-runs" / done["run_id"]
    attempt_id = pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]
    assert pilot_state.context_progress(root, attempt_id) == {"have": done["context_batch_count"], "total": done["context_batch_count"]}
    receipt_dir = root / "context-selections" / attempt_id
    for path in receipt_dir.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["batch_count"] == done["context_batch_count"]


# --- A1-4, 8.8: finish ----------------------------------------------------------------------------------------------

def _rework_closed(tmp_path: Path, capsys) -> dict:
    run = _generated_run(tmp_path)
    code, opened = _open(run)
    assert code == 0, opened
    code, selected = _main(capsys, ["review-select", *_flags(run), "--output-dir", _bundles(run), "--verdict", _verdict(run, "ТРЕБУЕТ ДОРАБОТКИ")])
    assert code == 0, selected
    return run


def _finish_argv(run: dict) -> list[str]:
    return ["finish", "--project", str(run["project"]), "--run", Path(run["run_root"]).name]


def test_a_planted_execution_receipt_does_not_make_finish_take_the_execution_branch(tmp_path: Path, capsys) -> None:
    run = _rework_closed(tmp_path, capsys)
    _put(Path(run["run_root"]) / "execution-receipts" / f"{run['attempt_id']}.json", {"planted": True})
    code, closed = _pipeline(capsys, _finish_argv(run))
    assert closed.get("status") == "ok" and closed["branch"] == "no-execution", closed


def test_abandon_on_a_closed_cases_only_attempt_points_to_report_not_ingest(tmp_path: Path, capsys) -> None:
    run = _rework_closed(tmp_path, capsys)
    code, closed = _pipeline(capsys, _finish_argv(run))
    assert closed.get("status") == "ok", closed
    code, refused = _pipeline(capsys, [*_finish_argv(run), "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 2 and refused.get("reason") == "ABANDON_NOT_NEEDED", refused
    assert "ingest" not in str(refused.get("fix")) and "report" in str(refused.get("fix")), refused.get("fix")


# --- 8.6: the drift of a context file --------------------------------------------------------------------------------

def _plan_argv(run: dict) -> list[str]:
    from tests.test_batch_assembly import _requirements

    tmp = run["tmp"]
    context = json.loads(Path(run["context"]).read_bytes())
    grouping = _put(tmp / "grouping.json", {"context_receipt_digest": context["digest"], "context_receipt_digests": [context["digest"]]})
    requirements = _put(tmp / "requirements.json", _requirements())
    return ["plan", "--header", run["header"], "--requirements", requirements, "--grouping", grouping, "--project", str(run["project"]),
            "--input-file", run["marker"], "--context-receipt", run["context"], *_flags(run),
            "--out-plan", str(tmp / "plan-out.json"), "--out-published", str(tmp / "published.json")]


def test_plan_after_a_context_file_drift_names_the_drift(tmp_path: Path, capsys) -> None:
    run = _generated_run(tmp_path, generated=False)
    (run["project"] / "src" / "evidence.py").write_text("value = 2\n", encoding="utf-8")
    code, refused = _main(capsys, _plan_argv(run))
    assert code == 2, refused
    assert refused["code"] == "CONTEXT_DRIFTED" and "scan --new-run" in str(refused.get("fix")), refused
    assert "not a context receipt" not in refused["message"], refused["message"]


# --- the receipts are the old ones plus batch_count; an honest scan has at least one batch ----------------------------

def test_the_receipts_are_the_old_ones_with_batch_count_added(tmp_path: Path, capsys) -> None:
    from tools import pilot_state
    from tools.project_inventory import _context_receipt, read_inventory_receipt

    project = _two_batch_project(tmp_path)
    code, done = _pipeline(capsys, _scan_argv(project))
    assert code == 0, done
    root = project / ".pilot-runs" / done["run_id"]
    attempt_id = pilot_state.derive_state(root)["attempts"][-1]["attempt_id"]
    receipts = sorted((json.loads(path.read_text(encoding="utf-8")) for path in (root / "context-selections" / attempt_id).glob("*.json")),
                      key=lambda row: row["batch_index"])
    assert len(receipts) == done["context_batch_count"] > 1
    inventory = read_inventory_receipt(root / "inventories" / (done["inventory_digest"].removeprefix("sha256:") + ".json"))
    for receipt in receipts:
        old = _context_receipt(inventory, receipt["batch_index"], receipt["files"], receipt["byte_count"], b"", receipt.get("skipped"))
        old.pop("byte_set_digest"), old.pop("digest")
        new = {key: value for key, value in receipt.items() if key not in {"batch_count", "byte_set_digest", "digest"}}
        assert new == old, "only batch_count (and the digest it changes) is new"
        assert receipt["batch_count"] == len(receipts)


def test_a_receipt_without_batch_count_is_a_whole_set(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.project_inventory import _context_receipt, read_execution_baseline, read_inventory_receipt
    from tests.test_reviewer_protocol import _new_run

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "evidence.py").write_text("value = 1\n", encoding="utf-8")
    run_root, attempt_id = _new_run(project)
    attempt = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    item = next(row for row in inventory["files"] if row["project_path"] == "src/evidence.py")
    legacy = _context_receipt(inventory, 1, [item], int(item["size"]), b"")  # the receipt of a run before F3: no batch_count
    assert "batch_count" not in legacy
    # a legacy receipt needs the real byte-set digest to bind: take the one select_context_batches makes
    from tools.project_inventory import select_context_batches

    made = select_context_batches(inventory, project, [item["opaque_id"]], byte_budget=4096)[0]["receipt"]
    legacy = {key: value for key, value in made.items() if key not in {"batch_count", "digest"}}
    from tools.project_inventory import _digest

    legacy["digest"] = _digest(legacy)
    pilot_state.publish_context_selection(run_root, attempt_id, legacy)
    assert pilot_state.context_progress(run_root, attempt_id) == {"have": 1, "total": None}
    assert pilot_state.context_incomplete(run_root, attempt_id) is None


def test_a_documents_only_scan_still_has_a_context_batch_and_is_not_abandoned(tmp_path: Path, capsys) -> None:
    project = tmp_path / "p"
    (project / "docs").mkdir(parents=True)
    (project / "docs" / "a.md").write_text("# Spec\n\nR01. Text.\n", encoding="utf-8")
    code, done = _pipeline(capsys, ["scan", "--project", str(project), "--docs", "docs/a.md#Spec", "--profile", "cases-only-v1"])
    assert code == 0 and done["context_batch_count"] >= 1, done  # .skillsrc is a closed manifest: always in the context
    code, again = _pipeline(capsys, ["scan", "--project", str(project), "--docs", "docs/a.md#Spec", "--profile", "cases-only-v1"])
    assert code == 2 and again["reason"] == "SCAN_RUN_IN_PROGRESS", again  # the honest run is the live one, not abandoned
