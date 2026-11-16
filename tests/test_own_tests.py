"""pm1: the tests the package wrote in an earlier run of the project do not go to the model again; inventory and baseline keep them."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from tests.test_context_filter import _receipts, _selected_paths, _skipped
from tests.test_quarantine import THREE_FUNCTIONS, _digest, _fail
from tools import execution_gate, pilot_state, run_pipeline
from tools.finalize_attempt import decide_dispositions


def _prior(project: Path, *, quarantined: bool = True) -> tuple[Path, str, str]:
    """A real earlier run of ``project``: it wrote ``tests/test_generated.py``; ``quarantined`` closes it with a quarantined method."""
    run_root, attempt_id, delta = _fail(project, content=THREE_FUNCTIONS)
    (project / "sample.py").write_text("def combine(a, b):\n    return a + b\n", encoding="utf-8")  # code of the project: the scan needs some besides the tests
    if quarantined:
        decide_dispositions(project, delta, "FAIL", pre_trace_valid=True, run_root=run_root, attempt_id=attempt_id)
    return run_root, attempt_id, delta["files"][0]["path"]


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict]:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    return run_pipeline.main(["scan", "--project", str(project), "--new-run"]), payloads[-1]


@pytest.fixture(scope="module")
def past(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str, str]:
    return _prior(tmp_path_factory.mktemp("past"))


# --- the reader of one run --------------------------------------------------------------------

def test_written_files_name_the_written_and_the_quarantined_digest(past: tuple[Path, str, str]) -> None:
    run_root, _attempt_id, path = past
    quarantined = _digest((run_root.parent.parent / path).read_bytes())
    assert pilot_state.written_files(run_root) == {path: {_digest(THREE_FUNCTIONS.encode()), quarantined}}


def test_the_reader_does_not_need_the_modules_of_mode_b(past: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    for name in execution_gate.MODE_B_MODULES:
        monkeypatch.setitem(sys.modules, f"tools.{name}", None)
    assert pilot_state.written_files(past[0])


def test_the_path_of_a_receipt_is_taken_from_the_module(past: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    run_root, _attempt_id, path = past
    real = pilot_state._read_artifact

    def in_module(project, root, target, label):
        receipt = real(project, root, target, label)
        return {**receipt, "payload": {**receipt["payload"], "module": "mod"}} if label == "materialization-ownership" else receipt

    monkeypatch.setattr(pilot_state, "_read_artifact", in_module)
    assert set(pilot_state.written_files(run_root)) == {"mod/" + path}


def test_a_receipt_path_outside_the_test_root_refuses_the_run(past: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    real = pilot_state._read_artifact

    def elsewhere(project, root, target, label):
        receipt = real(project, root, target, label)
        return {**receipt, "payload": {**receipt["payload"], "test_root": "src"}} if label == "materialization-ownership" else receipt

    monkeypatch.setattr(pilot_state, "_read_artifact", elsewhere)
    with pytest.raises(ValueError):
        pilot_state.written_files(past[0])


def test_a_receipt_the_journal_did_not_read_back_refuses_the_run(past: tuple[Path, str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    real = pilot_state.derive_state

    def unbound(root):
        state = real(root)
        return {**state, "events": [event for event in state["events"] if event["event_type"] != "ARTIFACT_READ_BACK"]}

    monkeypatch.setattr(pilot_state, "derive_state", unbound)
    with pytest.raises(ValueError):
        pilot_state.written_files(past[0])


# --- scan -------------------------------------------------------------------------------------

def test_a_quarantined_file_of_a_past_run_stays_out_of_the_context_but_in_the_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_root, _attempt_id, path = _prior(tmp_path)
    code, scan = _scan(tmp_path, monkeypatch)
    receipts = _receipts(scan)
    assert code == 0 and path not in _selected_paths(receipts), scan
    assert _skipped(receipts)[path][0] == "CONTEXT_OWN_TEST" and scan["own_files"] == 1
    inventory = json.loads(next((Path(scan["run_root"]) / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    assert path in {row["project_path"] for row in inventory["files"]}


def test_a_file_the_run_wrote_and_nobody_quarantined_stays_out_too(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_root, _attempt_id, path = _prior(tmp_path, quarantined=False)
    code, scan = _scan(tmp_path, monkeypatch)
    assert code == 0 and path not in _selected_paths(_receipts(scan)), scan


def test_a_file_a_person_changed_stays_in_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _run_root, _attempt_id, path = _prior(tmp_path)
    (tmp_path / path).write_text((tmp_path / path).read_text(encoding="utf-8") + "# edited by a person\n", encoding="utf-8")
    code, scan = _scan(tmp_path, monkeypatch)
    assert code == 0 and path in _selected_paths(_receipts(scan)) and "own_files" not in scan, scan


def test_an_own_file_the_human_named_in_paths_include_stays_in_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import load_skillsrc

    _run_root, _attempt_id, path = _prior(tmp_path)
    document = load_skillsrc(tmp_path / ".skillsrc")
    document["modules"][0]["paths"]["include"] = [path]
    (tmp_path / ".skillsrc").write_bytes(_payload(document))
    code, scan = _scan(tmp_path, monkeypatch)
    assert code == 0 and path in _selected_paths(_receipts(scan)) and "own_files" not in scan, scan


def test_a_run_that_does_not_read_whole_hides_nothing_and_is_said(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_root, _attempt_id, path = _prior(tmp_path)
    receipt = next((run_root / "materialization-ownership").glob("*.json"))
    receipt.write_bytes(receipt.read_bytes().replace(b"FILE-products", b"FILE-productz"))  # the digest of the body no longer fits
    code, scan = _scan(tmp_path, monkeypatch)
    assert code == 0 and path in _selected_paths(_receipts(scan)) and "own_files" not in scan, scan
    assert scan["own_runs_unread"] == 1 and any("прошлых прогонов" in line for line in scan["warnings"]), scan


# --- the map of existing tests ----------------------------------------------------------------

def test_the_map_skips_the_own_file_and_takes_it_back_once_a_person_changed_it(tmp_path: Path) -> None:
    from tools.existing_tests import build_map

    _run_root, _attempt_id, path = _prior(tmp_path)

    def inventory() -> dict:
        return {"files": [{"project_path": path, "kind": "test", "content_digest": _digest((tmp_path / path).read_bytes())}]}

    assert build_map(tmp_path, inventory(), ["sample.py"]) == {}
    (tmp_path / path).write_text((tmp_path / path).read_text(encoding="utf-8") + "# edited by a person\n", encoding="utf-8")
    assert "sample" in build_map(tmp_path, inventory(), ["sample.py"])
