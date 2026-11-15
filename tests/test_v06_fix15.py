"""v0.6 fix15: the receipts of the inventory are read through the pinned run root, a refused ``.skillsrc`` write says why."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tools import pilot_state
from tools.project_inventory import InventoryError, read_exclusion_receipt, read_execution_baseline, read_inventory_receipt

READERS = [(read_inventory_receipt, "inventories", "inventory"), (read_exclusion_receipt, "exclusions", "exclusions"), (read_execution_baseline, "baselines", "baseline")]


def _frozen(tmp_path: Path) -> tuple[Path, dict]:
    from tests.test_v06_fix10 import _spine_run

    root, _inventory, baseline = _spine_run(tmp_path)
    return root, pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline)


@pytest.mark.parametrize(("reader", "folder", "key"), READERS)
def test_a_receipt_is_read_below_the_run_root(tmp_path: Path, reader, folder: str, key: str) -> None:
    root, frozen = _frozen(tmp_path)
    receipt = frozen[key]
    assert reader(tmp_path, root, root / folder / (receipt["digest"].removeprefix("sha256:") + ".json")) == receipt


@pytest.mark.parametrize(("reader", "folder", "key"), READERS)
def test_a_run_root_that_is_a_junction_is_no_place_to_read_a_receipt_from(tmp_path: Path, reader, folder: str, key: str) -> None:
    from tests.test_stage_steps import _junction

    root, frozen = _frozen(tmp_path)
    link = root.parent / "link"
    _junction(link, root)
    with pytest.raises(InventoryError, match="symlink or reparse point"):
        reader(tmp_path, link, link / folder / (frozen[key]["digest"].removeprefix("sha256:") + ".json"))


@pytest.mark.parametrize(("reader", "folder", "key"), READERS)
def test_a_receipt_folder_that_is_a_junction_to_a_copy_outside_the_run_is_no_receipt(tmp_path: Path, reader, folder: str, key: str) -> None:
    from tests.test_stage_steps import _junction

    root, frozen = _frozen(tmp_path)
    outside = tmp_path / "outside"
    shutil.copytree(root / folder, outside)
    shutil.rmtree(root / folder)
    _junction(root / folder, outside)
    with pytest.raises(InventoryError, match="symlink or reparse point"):
        reader(tmp_path, root, root / folder / (frozen[key]["digest"].removeprefix("sha256:") + ".json"))


@pytest.mark.parametrize(("reader", "folder", "key"), READERS)
def test_a_receipt_that_is_not_there_is_unreadable(tmp_path: Path, reader, folder: str, key: str) -> None:
    root, _frozen_receipts = _frozen(tmp_path)
    with pytest.raises(InventoryError, match="is unreadable"):
        reader(tmp_path, root, root / folder / ("0" * 64 + ".json"))


def test_init_says_why_a_dangling_link_in_the_place_of_skillsrc_is_not_written(tmp_path: Path) -> None:
    from tests.test_skillsrc_human_keys import _project as _configured_project
    from tests.test_v06_fix13 import _link
    from tools.init_skillsrc import ensure_skillsrc

    project = _configured_project(tmp_path)
    (tmp_path / "gone").write_text("x", encoding="utf-8")
    _link(project / ".skillsrc", tmp_path / "gone")
    (tmp_path / "gone").unlink()
    report = ensure_skillsrc(project, {"module:root:test.framework": "pytest"}, write=True)
    assert (report["status"], report["errors"]) == ("error", ["write_error"]) and "не пишет поверх ссылок" in report["_message"] and "обычным файлом" in report["_fix"], report


# ---- the project named through a junction above it (a redirected folder): the same answers as before the readers went through the core -----------------

def _through_a_junction(tmp_path: Path) -> tuple[dict, dict]:
    """A run made at the plain path and the same run named through ``via``, a junction to the folder that holds the project."""
    from tests.test_stage_steps import _generator, _junction

    (tmp_path / "real").mkdir()
    run = _generator(tmp_path / "real")
    via = tmp_path / "via"
    _junction(via, tmp_path / "real")
    return run, {**run, "root": via / Path(run["root"]).relative_to(tmp_path / "real")}


def test_the_steps_of_a_run_named_through_a_junction_above_the_project_answer_as_before(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_stage_steps import _answer

    _run, named = _through_a_junction(tmp_path)
    assert _answer(capsys, named, 0)[0] == 0
    code, out = _answer(capsys, named, 1)  # the last batch: the candidate reads the inventory of the run for the share of the manual steps
    steps = [step for value in named["values"] for case in value["test_cases"] for step in case["steps"]]
    assert code == 0 and out["result"]["candidate"]["warnings"].count("MANUAL_SHARE_HIGH") == 1 and out["result"]["candidate"]["manual_share"]["manual_steps"] == len(steps), out


def test_the_status_of_a_project_named_through_a_junction_above_it_says_what_it_said_before(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_f1 import _pipeline

    run, named = _through_a_junction(tmp_path)
    plain_root, via_root = Path(run["root"]), Path(named["root"])
    code, plain = _pipeline(capsys, ["status", "--project", str(plain_root.parents[1]), "--run", plain_root.name])
    via_code, via = _pipeline(capsys, ["status", "--project", str(via_root.parents[1]), "--run", via_root.name])
    assert (via_code, "exclusion_summary" in via) == (code, True) and "exclusion_summary" in plain  # the summary is read from the frozen inventory
    assert {key: value for key, value in via.items() if key not in {"project", "run_root", "answer_slot", "evidence_path", "warning_evidence_path"}} == {key: value for key, value in plain.items() if key not in {"project", "run_root", "answer_slot", "evidence_path", "warning_evidence_path"}}
