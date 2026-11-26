"""pm3: the marker of an earlier accepted run is reused when the project is the same by its composition — the tests the package wrote and the
build directories are no change; every other field of the inventory, of the context receipts and of the request still is."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_marker_reuse import _fresh, _marker, _request, _source
from tests.test_phase2_scan import _skillsrc
from tests.test_quarantine import THREE_FUNCTIONS, _fail
from tests.test_self_review import _run
from tools import pilot_state
from tools.init_skillsrc import _payload
from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches
from tools.skillsrc_manifest import parse_skillsrc_bytes

TEST_PATH = "tests/test_written.py"


def _own(project: Path, source_root: Path, monkeypatch: pytest.MonkeyPatch, path: str = TEST_PATH, content: bytes = b"def test_a():\n    assert True\n") -> None:
    """A test the package wrote in the source run: the file, and the ownership receipts' reader naming its digest (the real receipts: ``test_own_tests`` and the E2E below)."""
    from tools import pilot_state

    (project / path).parent.mkdir(parents=True, exist_ok=True)
    (project / path).write_bytes(content)
    (source_root / "materialization-ownership").mkdir(exist_ok=True)
    monkeypatch.setattr(pilot_state, "written_files", lambda run_root: {path: {"sha256:" + hashlib.sha256(content).hexdigest()}})


def _verdict(project: Path, source_root: Path) -> tuple[bool, bool]:
    """(the scan offers the source, ``prepare --reuse-from`` takes it) for a new run of the project — the two must be one answer."""
    from tools import pilot_state

    run_root, attempt_id, inputs = _fresh(project)
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] == source_root.name
    try:
        _request(run_root, attempt_id, inputs, source_root.name)
    except (pilot_state.ModelReuseRefused, pilot_state.ReuseSourceInvalid):  # a changed context file: the marker of the source no longer reads against the files
        return offered, False
    return offered, True


def test_a_written_test_and_a_build_directory_keep_the_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root, _attempt = _source(tmp_path)
    _own(tmp_path, source_root, monkeypatch)
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "classes.txt").write_text("x", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (True, True)


def test_the_same_project_gives_one_verdict_to_the_scan_and_to_prepare(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    assert _verdict(tmp_path, source_root) == (True, True)


def test_a_build_directory_alone_keeps_the_reuse(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "out.txt").write_text("x", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (True, True)


def test_a_source_file_inside_a_build_directory_keeps_the_reuse(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "target" / "generated").mkdir(parents=True)
    (tmp_path / "target" / "generated" / "Made.java").write_text("class Made {}\n", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (True, True)


def test_an_own_test_deleted_since_keeps_the_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root, _attempt = _source(tmp_path)
    _own(tmp_path, source_root, monkeypatch)
    (tmp_path / TEST_PATH).unlink()
    assert _verdict(tmp_path, source_root) == (True, True)


def test_an_own_test_the_human_edited_is_a_file_of_the_project_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root, _attempt = _source(tmp_path)
    _own(tmp_path, source_root, monkeypatch)
    (tmp_path / TEST_PATH).write_bytes(b"def test_a():\n    assert 1 == 1\n")
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_test_nobody_wrote_with_the_package_refuses_the_reuse(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "tests" / "added.py").write_text("x = 1\n", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_file_removed_since_the_source_refuses_the_reuse(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "kept.py").write_text("x = 1\n", encoding="utf-8")
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "tests" / "kept.py").unlink()
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_file_changed_since_the_source_refuses_the_reuse(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "kept.py").write_text("x = 1\n", encoding="utf-8")
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "tests" / "kept.py").write_text("x = 2\n", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_new_directory_left_out_by_its_name_refuses_the_reuse(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_file_left_out_as_secret_since_the_source_refuses_the_reuse(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "token.txt").write_text("password = 'hunter2hunter2'\n", encoding="utf-8")
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_source_whose_context_lacks_a_file_the_new_one_holds_is_not_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The own test of the human's ``paths.include`` is in the context of the new run: the marker of the source was made without it (and the other way round: a source made before the tests were kept out)."""
    source_root, _attempt = _source(tmp_path)
    _own(tmp_path, source_root, monkeypatch)
    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    attempt = pilot_state._attempt_of(pilot_state.derive_state(run_root), attempt_id)
    baseline = read_execution_baseline(tmp_path, run_root, run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(tmp_path, run_root, run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    held = [row["opaque_id"] for row in inventory["files"] if row["project_path"] == TEST_PATH]
    pilot_state.publish_context_selection(run_root, attempt_id, select_context_batches(inventory, tmp_path, held, byte_budget=64 * 1024)[0]["receipt"])
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] is None


def test_the_differences_name_the_input_and_only_the_changed_ones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "tests" / "added.py").write_text("x = 1\n", encoding="utf-8")
    run_root, attempt_id, inputs = _fresh(tmp_path)
    reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert [row["input"] for row in reuse["differences"]] == ["inventory"] and reuse["files"]["added"]["paths"] == ["tests/added.py"]


def test_the_files_a_refusal_names_leave_out_the_own_tests_and_the_build_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source_root, _attempt = _source(tmp_path)
    _own(tmp_path, source_root, monkeypatch)
    (tmp_path / "target").mkdir()
    (tmp_path / "tests" / "added.py").write_text("x = 1\n", encoding="utf-8")
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert reuse["files"]["added"]["paths"] == ["tests/added.py"] and "excluded_added" not in reuse["files"] and "target" not in reuse["reason"]


def test_a_context_in_more_receipts_than_the_source_refuses_the_reuse(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    (tmp_path / "tests").mkdir(exist_ok=True)
    for name in ("one.py", "two.py"):
        (tmp_path / "tests" / name).write_text("x = 1\n" * 50, encoding="utf-8")
    run_root, attempt_id, inputs = _fresh(tmp_path)
    baseline = read_execution_baseline(tmp_path, run_root, run_root / "baselines" / (pilot_state.derive_state(run_root)["attempts"][-1]["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(tmp_path, run_root, run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    ids = [row["opaque_id"] for row in inventory["files"] if row["project_path"] in ("tests/one.py", "tests/two.py")]
    second = select_context_batches(inventory, tmp_path, ids, byte_budget=400)
    assert len(second) >= 2
    pilot_state.publish_context_selection(run_root, attempt_id, second[1]["receipt"])
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] is None


# --- the comparison itself: the inputs that are no file of the project, an unknown digest, the way the receipts of the source are read ---

def _differences(project: Path, mine: list[str], theirs: list[str]):
    from tools import pilot_state

    source_root, source_attempt = _source(project)
    run_root, attempt_id, _inputs = _fresh(project)
    state = pilot_state.derive_state(run_root)
    source = pilot_state._run_source(project, source_root.name, whole=False)
    return pilot_state._marker_differences(project, run_root, state, pilot_state._attempt_of(state, attempt_id), source, mine, theirs)


def test_a_changed_document_digest_is_a_difference(tmp_path: Path) -> None:
    assert [row["input"] for row in _differences(tmp_path, ["sha256:" + "1" * 64], ["sha256:" + "2" * 64])] == ["requirements"]


def test_an_unknown_digest_is_compared_as_it_is(tmp_path: Path) -> None:
    assert [row["input"] for row in _differences(tmp_path, ["sha256:" + "1" * 64, "sha256:" + "5" * 64], ["sha256:" + "1" * 64, "sha256:" + "6" * 64])] == ["inventory"]


def test_a_receipt_of_the_source_that_does_not_read_is_a_refusal_not_a_skip(tmp_path: Path) -> None:
    source_root, source_attempt = _source(tmp_path)
    for path in (source_root / "context-selections").rglob("*.json"):
        path.write_bytes(b"{")
    run_root, attempt_id, inputs = _fresh(tmp_path)
    with pytest.raises(pilot_state.ReuseSourceInvalid):
        _request(run_root, attempt_id, inputs, source_root.name)
    reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert reuse["run_id"] is None and reuse["reason"].startswith("REUSE_SOURCE_INVALID")


def test_the_receipt_of_the_new_run_is_read_against_the_live_files_as_before(tmp_path: Path) -> None:
    _source(tmp_path)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    receipt = pilot_state.read_context_selection(run_root, attempt_id, inputs[2])
    path = tmp_path / receipt["files"][0]["project_path"]
    path.write_bytes(path.read_bytes() + b"\n#")
    with pytest.raises(ValueError):
        pilot_state.read_context_selection(run_root, attempt_id, inputs[2])


def test_a_receipt_read_without_the_live_files_still_checks_its_own_digest(tmp_path: Path) -> None:
    _source(tmp_path)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    receipt = dict(pilot_state.read_context_selection(run_root, attempt_id, inputs[2]))
    assert pilot_state.read_context_selection(run_root, attempt_id, inputs[2], live=False) == receipt
    target = pilot_state._context_selection_target(run_root, attempt_id, inputs[2])
    target.write_text(json.dumps({**receipt, "byte_count": receipt["byte_count"] + 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        pilot_state.read_context_selection(run_root, attempt_id, inputs[2], live=False)


def test_a_receipt_read_without_the_live_files_does_not_see_a_changed_file(tmp_path: Path) -> None:
    _source(tmp_path)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    receipt = pilot_state.read_context_selection(run_root, attempt_id, inputs[2])
    path = tmp_path / receipt["files"][0]["project_path"]
    path.write_bytes(path.read_bytes() + b"\n#")
    assert pilot_state.read_context_selection(run_root, attempt_id, inputs[2], live=False)["digest"] == inputs[2]


# --- the whole way with the receipts a real run wrote (no stand-in for the owner of the file) ---

def test_the_test_a_real_run_wrote_and_a_build_directory_keep_the_reuse(tmp_path: Path) -> None:
    run_root, _attempt, delta = _fail(tmp_path, content=THREE_FUNCTIONS)  # the run: its marker and accepted review, and the test it wrote into the project
    (tmp_path / "target").mkdir()
    (tmp_path / "target" / "classes.txt").write_text("x", encoding="utf-8")
    new_root, attempt_id, inputs = _fresh(tmp_path)
    reuse = pilot_state.marker_reuse_suggestion(new_root, attempt_id)
    assert reuse["run_id"] == run_root.name, reuse
    assert _request(new_root, attempt_id, inputs, run_root.name)["reuse"]["run_id"] == run_root.name
    assert delta["files"][0]["path"] in {row["project_path"] for row in pilot_state._frozen_inventory(
        tmp_path, new_root, str(pilot_state.frozen_baseline(new_root, pilot_state._attempt_of(pilot_state.derive_state(new_root), attempt_id))["inventory_digest"]))["files"]}


def test_a_changed_skillsrc_refuses_the_reuse(tmp_path: Path) -> None:
    (tmp_path / ".skillsrc").write_bytes(_skillsrc())
    source_root, _attempt = _source(tmp_path)
    document = json.loads(json.dumps(parse_skillsrc_bytes(_skillsrc())))
    document["modules"][0]["paths"] = {**document["modules"][0].get("paths", {}), "exclude": ["docs/**"]}
    (tmp_path / ".skillsrc").write_bytes(_payload(document))
    assert _verdict(tmp_path, source_root) == (False, False)


def test_a_receipt_of_the_source_that_is_no_object_is_a_refusal_and_the_scan_goes_on(tmp_path: Path) -> None:
    source_root, _attempt = _source(tmp_path)
    for path in (source_root / "context-selections").rglob("*.json"):
        path.write_bytes(b"[]")
    run_root, attempt_id, inputs = _fresh(tmp_path)
    with pytest.raises(pilot_state.ReuseSourceInvalid):
        _request(run_root, attempt_id, inputs, source_root.name)
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] is None
