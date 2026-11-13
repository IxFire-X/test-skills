"""P6a (bugs B-4, B-6 of the review of 02.11): a project moved to another folder, a package whose line endings were changed.

Before: a copied project lost all its runs (``EXECUTION_COORDINATES`` without a fix: the manifest holds the absolute path
and is compared byte for byte), and a CRLF package failed ``doctor`` with "does not match runtime bytes"."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools import run_pipeline
from tools.run_pipeline import HostStop, _read_run_coordinate

ROOT = Path(__file__).resolve().parents[1]


def _terminal_run(tmp_path: Path) -> Path:
    from tests.test_run_report import _finished_run

    return _finished_run(tmp_path)


def _open_run(tmp_path: Path) -> Path:
    from tests.test_review_notes import _selected_run, _two_notes

    return _selected_run(tmp_path, _two_notes)[0]


@pytest.fixture(scope="module")
def made_runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, tuple[Path, str]]:
    """One project with a run per kind, made once (a run takes a minute); each test moves a copy of it."""
    runs = {}
    for name, make in (("terminal", _terminal_run), ("open", _open_run)):
        source = tmp_path_factory.mktemp(name) / "work" / "petclinic"
        source.mkdir(parents=True)
        runs[name] = (source, make(source).name)
    return runs


def _moved(tmp_path: Path, runs: dict[str, tuple[Path, str]], kind: str) -> tuple[Path, Path, str]:
    source, run_id = runs[kind]
    target = tmp_path / "colleague" / "PetClinic-copy"
    target.parent.mkdir()
    shutil.copytree(source, target)
    return source, target, run_id


def _call(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    capsys.readouterr()
    code = run_pipeline.main(argv)
    return code, json.loads(capsys.readouterr().out.split("\nrun_id:")[0])


@pytest.mark.parametrize("kind", ["terminal", "open"])
def test_status_of_a_moved_project_works_and_says_it_was_moved(tmp_path: Path, capsys: pytest.CaptureFixture[str], made_runs, kind: str) -> None:
    source, target, run_id = _moved(tmp_path, made_runs, kind)
    code, status = _call(["status", "--project", str(target), "--run", run_id], capsys)
    assert code == 0 and status["status"] == "ok" and status["run_id"] == run_id, status
    [warning] = [item for item in status["warnings"] if "PROJECT_MOVED" in str(item)]
    assert str(source) in warning and str(target) in warning
    assert status["project_moved"]["code"] == "PROJECT_MOVED" and status["project_moved"]["fix"].startswith("проект перенесён:")
    # the answer is the one of the place where the run was made
    _, home = _call(["status", "--project", str(source), "--run", run_id], capsys)
    assert not [item for item in home.get("warnings", []) if "PROJECT_MOVED" in str(item)] and "project_moved" not in home
    assert status["attempt_state"] == home["attempt_state"]


def test_status_of_a_moved_open_attempt_makes_no_baseline_comparison(tmp_path: Path, capsys: pytest.CaptureFixture[str], made_runs) -> None:
    """The baseline binds the folder name and path of the scan: compared in a moved project it would report a drift of no file."""
    source, target, run_id = _moved(tmp_path, made_runs, "open")
    _, home = _call(["status", "--project", str(source), "--run", run_id], capsys)
    assert home["attempt_state"] != "TERMINAL" and "drift" not in home and "drift_check_error" not in home, home
    code, status = _call(["status", "--project", str(target), "--run", run_id], capsys)
    assert code == 0 and status["project_moved"]["code"] == "PROJECT_MOVED", status
    assert "drift" not in status and "drift_check_error" not in status, status
    assert "сверка файлов с baseline" in status["project_moved"]["fix"]
    # finish and exec keep the old refusal on the moved project
    with pytest.raises(HostStop) as stop:
        _read_run_coordinate(target, run_id)
    assert stop.value.code == "EXECUTION_COORDINATES" and stop.value.fix.startswith("проект перенесён:")


def test_report_of_a_moved_project_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str], made_runs) -> None:
    _source, target, run_id = _moved(tmp_path, made_runs, "terminal")
    code, report = _call(["report", "--project", str(target), "--run", run_id], capsys)
    assert code == 0 and report["status"] == "ok", report
    assert any("PROJECT_MOVED" in str(item) for item in report["warnings"]), report
    assert (target / ".pilot-runs" / run_id / "report.md").is_file()


def test_the_run_files_of_a_moved_project_are_not_rewritten(tmp_path: Path, capsys: pytest.CaptureFixture[str], made_runs) -> None:
    _source, target, run_id = _moved(tmp_path, made_runs, "terminal")
    run_root = target / ".pilot-runs" / run_id
    before = {path.relative_to(run_root): path.read_bytes() for path in run_root.rglob("*") if path.is_file()}
    _call(["status", "--project", str(target), "--run", run_id], capsys)
    after = {path.relative_to(run_root): path.read_bytes() for path in run_root.rglob("*") if path.is_file()}
    assert after == before


def test_a_changed_structure_is_refused_with_the_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str], made_runs) -> None:
    source, target, run_id = _moved(tmp_path, made_runs, "terminal")
    inventory = json.loads(next((target / ".pilot-runs" / run_id / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    gone = inventory["files"][0]["project_path"]  # the fixture project is small: its inventory is .skillsrc and little else
    (target / gone).unlink()
    for command in ("status", "report"):
        code, answer = _call([command, "--project", str(target), "--run", run_id], capsys)
        assert code != 0 and answer["reason"] == "EXECUTION_COORDINATES", answer
        assert answer["fix"].startswith("проект перенесён:") and gone in answer["fix"] and str(source) in answer["fix"], answer


def test_finish_and_exec_keep_the_old_refusal_but_now_with_the_fix(tmp_path: Path, made_runs) -> None:
    source, target, run_id = _moved(tmp_path, made_runs, "terminal")
    with pytest.raises(HostStop) as stop:
        _read_run_coordinate(target, run_id)
    assert stop.value.code == "EXECUTION_COORDINATES" and stop.value.fix and stop.value.fix.startswith("проект перенесён:")
    assert str(source) in stop.value.fix and "scan" in stop.value.fix


def test_a_run_that_does_not_exist_keeps_the_plain_refusal(tmp_path: Path) -> None:
    with pytest.raises(HostStop) as stop:
        _read_run_coordinate(tmp_path, "0" * 32)
    assert stop.value.code == "EXECUTION_COORDINATES" and not (stop.value.fix or "").startswith("проект перенесён")


# --- B-6: CRLF in the package folder, said in words -------------------------------------------------------------

def _pack_copy(tmp_path: Path) -> Path:
    pack = tmp_path / "pack"
    for name in ("contracts", "schemas", "skills", "tools", "evals", "release"):
        shutil.copytree(ROOT / name, pack / name, ignore=shutil.ignore_patterns("__pycache__"))
    from tools.release_manifest import _canonical, build_release_manifest

    (pack / "release" / "manifest.json").write_bytes(_canonical(build_release_manifest(pack)) + b"\n")  # the copy is self-consistent
    return pack


def _to_crlf(path: Path) -> None:
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))


def test_doctor_says_crlf_in_words_and_names_the_gitattributes_fix(tmp_path: Path) -> None:
    from tools.doctor import inspect_environment

    pack = _pack_copy(tmp_path)
    assert inspect_environment(pack)["status"] == "PASS"
    converted = [pack / "tools" / "doctor.py", pack / "skills" / next(p.name for p in (ROOT / "skills").iterdir() if p.is_dir()) / "SKILL.md"]
    for path in converted:
        _to_crlf(path)
    report = inspect_environment(pack)
    assert report["status"] == "NOT_RUNNABLE" and "invalid:release/manifest.json" in report["integrity"]["missing"]
    text = " ".join(report["messages"])
    assert "переносы строк" in text and "CRLF" in text and "* -text" in text and ".gitattributes" in text, text
    assert "tools/doctor.py" in text and "2 файл" in text
    assert report["crlf"]["count"] == 2 and "tools/doctor.py" in report["crlf"]["examples"]


def test_doctor_keeps_the_byte_check_for_a_real_change(tmp_path: Path) -> None:
    """Not a weakening: a changed byte that is not a line ending is still "does not match", with no CRLF explanation."""
    from tools.doctor import inspect_environment

    pack = _pack_copy(tmp_path)
    target = pack / "tools" / "doctor.py"
    target.write_bytes(target.read_bytes() + b"# changed\r\n")
    report = inspect_environment(pack)
    assert report["status"] == "NOT_RUNNABLE" and "invalid:release/manifest.json" in report["integrity"]["missing"]
    assert "CRLF" not in " ".join(report["messages"]) and "crlf" not in report
    assert "does not match runtime bytes" in " ".join(report["messages"])


def test_ready_pack_row_carries_the_crlf_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import ready

    pack = _pack_copy(tmp_path)
    _to_crlf(pack / "tools" / "doctor.py")
    monkeypatch.setattr(ready, "PACK", pack)
    row = ready.check_pack()
    assert row["level"] == "STOP" and "* -text" in row["fix"] and "переносы строк" in row["message"], row


# --- V9b: a tools.api command (prepare) on a moved project ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["terminal", "open"])
def test_a_tools_api_command_on_a_moved_project_names_the_move_and_the_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str], made_runs, kind: str) -> None:
    """Before: ``ValueError: invalid run boundary`` with ``fix: null`` on every command (status and report said PROJECT_MOVED)."""
    from tools import api

    source, target, run_id = _moved(tmp_path, made_runs, kind)
    run_root = target / ".pilot-runs" / run_id
    capsys.readouterr()
    code = api.main(["prepare", "--run-root", str(run_root), "--stage", "tc-reviewer:canonical"])
    answer = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert code == 2 and answer["status"] == "error" and answer["code"] == "PROJECT_MOVED", answer
    assert str(source) in answer["message"] and str(target) in answer["message"], answer
    assert answer["fix"] and "scan" in answer["fix"] and "status" in answer["fix"], answer
    # the run of the home project still answers as before
    home_code = api.main(["prepare", "--run-root", str(source / ".pilot-runs" / run_id), "--stage", "tc-reviewer:canonical"])
    assert "PROJECT_MOVED" not in capsys.readouterr().out and home_code in (0, 1, 2)
