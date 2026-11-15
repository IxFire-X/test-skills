"""v0.6 fix10: the receipts of the inventory and of the context are written through ``confined_output``; a receipt that lies is taken only when its bytes are the fresh ones."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tools import pilot_state
from tools.project_inventory import ContextSelectionError, _canonical_bytes, freeze_context_receipt, freeze_inventory_receipt


def _spine_run(project: Path, *, other: bool = False) -> tuple[Path, dict, dict]:
    """A run with its module selected, the fresh inventory and the baseline of the project (``other``: with another test file)."""
    from tests.test_phase2_spine import AUTHORIZATION as authorization, _baseline

    root = Path(pilot_state.create_run(project, "cases-only-v1", authorization)["run_root"])
    inventory, baseline = _baseline(project, generated_root=root.parent)
    if other:  # the receipts of another, consistent inventory
        (project / "test_app.py").write_text("def test_other(): pass" + chr(10), encoding="utf-8")
        inventory, baseline = _rebuilt(project, root.parent)
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(project, "."))
    return root, inventory, baseline


def _rebuilt(project: Path, generated_root: Path) -> tuple[dict, dict]:
    from tests.test_phase2_spine import _file_id
    from tools.project_inventory import build_execution_baseline, build_inventory, build_skillsrc_authority_receipt

    inventory = build_inventory(project, project, generated_roots=(generated_root,))
    return inventory, build_execution_baseline(
        inventory, project_root=project, requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"), skillsrc_authority=build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes()),
        execution_file_ids=[_file_id(inventory, "pyproject.toml"), _file_id(inventory, "test_app.py")], parent_build_file_ids=[], policy_profile="cases-only-v1",
    )


def _name(digest: str) -> str:
    return digest.removeprefix("sha256:") + ".json"


# ---- 2: the bytes of the receipts, an equal file, a failed write -----------------------------------------------------------------------------------

def test_the_receipts_of_the_phase_two_inputs_are_the_canonical_bytes_under_their_digests(tmp_path: Path) -> None:
    root, inventory, baseline = _spine_run(tmp_path)
    frozen = pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline)
    for folder, receipt in (("inventories", frozen["inventory"]), ("exclusions", frozen["exclusions"]), ("baselines", frozen["baseline"])):
        assert (root / folder / _name(receipt["digest"])).read_bytes() == _canonical_bytes(receipt), folder


def test_a_receipt_that_lies_with_equal_bytes_is_taken_again_with_the_same_answer(tmp_path: Path) -> None:
    root, inventory, baseline = _spine_run(tmp_path)
    first = pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline)
    assert pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline) == first
    target = root / "inventories" / _name(first["inventory"]["digest"])
    assert freeze_inventory_receipt(tmp_path, root, target, inventory) == first["inventory"]


def _context_receipt() -> dict:
    from tests.test_batch_assembly import _context_receipt

    return _context_receipt()


def test_a_failed_write_of_a_context_receipt_leaves_no_receipt_and_the_repeat_goes_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "run").mkdir()
    target = tmp_path / "run" / "c.json"
    receipt = _context_receipt()
    real, failed = os.fsync, []

    def fsync_that_fails_once(descriptor: int) -> None:
        if not failed:
            failed.append(descriptor)
            raise OSError("no space left")
        real(descriptor)

    monkeypatch.setattr(os, "fsync", fsync_that_fails_once)
    with pytest.raises(ContextSelectionError):
        freeze_context_receipt(tmp_path, tmp_path / "run", target, receipt)
    assert not target.exists()
    assert freeze_context_receipt(tmp_path, tmp_path / "run", target, receipt) == receipt


# ---- 3: a receipt that lies there is no receipt unless its bytes are the fresh ones ----------------------------------------------------------------

def test_a_consistent_pair_of_inventory_and_baseline_laid_under_the_fresh_names_is_refused_and_left_as_it_is(tmp_path: Path) -> None:
    other_inventory, other_baseline = _spine_run(tmp_path, other=True)[1:]
    shutil.rmtree(tmp_path / ".pilot-runs")
    root, inventory, baseline = _spine_run(tmp_path)
    assert other_inventory["digest"] != inventory["digest"] and other_baseline["digest"] != baseline["digest"]
    laid = {
        root / "inventories" / _name(inventory["digest"]): other_inventory,
        root / "inventories" / _name(other_inventory["digest"]): other_inventory,
        root / "baselines" / _name(baseline["digest"]): other_baseline,
        root / "baselines" / _name(other_baseline["digest"]): other_baseline,
    }
    for path, receipt in laid.items():
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(_canonical_bytes(receipt))
    before_events = len(pilot_state.derive_state(root)["events"])
    before_files = {path: path.read_bytes() for path in laid}
    with pytest.raises(ValueError, match="phase two input receipt is invalid"):
        pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline)
    assert len(pilot_state.derive_state(root)["events"]) == before_events
    assert {path: path.read_bytes() for path in laid} == before_files and not (root / "exclusions").exists()


# ---- 5: the receipts of the context are read through the pinned root --------------------------------------------------------------------------

def test_a_context_directory_that_is_a_junction_to_a_copy_outside_the_run_is_no_receipt(tmp_path: Path) -> None:
    from tests.test_f1 import _opened
    from tests.test_stage_steps import _junction

    run = _opened(tmp_path)
    root, attempt = Path(run["run_root"]), run["attempt_id"]
    folder = root / "context-selections" / attempt
    digest = "sha256:" + next(folder.glob("*.json")).stem
    outside = tmp_path / "outside" / attempt
    shutil.copytree(folder, outside)
    shutil.rmtree(folder)
    _junction(folder, outside)
    with pytest.raises(ValueError):
        pilot_state.read_context_selection(root, attempt, digest)
