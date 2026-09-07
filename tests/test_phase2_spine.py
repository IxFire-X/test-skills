from pathlib import Path

import pytest
from helpers import build_phase_two_baseline

from tools import pilot_state
from tools.project_inventory import build_execution_baseline, build_inventory, build_skillsrc_authority_receipt, runtime_identity


AUTHORIZATION = {"request_id": "request-1", "execution_requested": False}


def _file_id(inventory: dict, path: str) -> str:
    return next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == path)


def _baseline(project: Path, *, generated_root: Path | None = None) -> tuple[dict, dict]:
    (project / ".skillsrc").write_text('{"project":{"name":"x","language":"python","build_tool":"pip"},"paths":{"source":"src","tests":"tests"},"test":{"framework":"pytest"}}\n', encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (project / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    runtime = project / ".venv" / "Scripts" / "python.exe"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"fixture-runtime-v1")
    inventory = build_inventory(project, project, generated_roots=() if generated_root is None else (generated_root,))
    return inventory, build_execution_baseline(
        inventory, project_root=project, requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"),
        skillsrc_authority=build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes()),
        execution_file_ids=[_file_id(inventory, "pyproject.toml"), _file_id(inventory, "test_app.py")],
        parent_build_file_ids=[], interpreter_path=".venv/Scripts/python.exe",
        interpreter_identity=runtime_identity(project, ".venv/Scripts/python.exe"),
        wrapper_path=None, wrapper_identity=None, adapter_id="pytest:selected-symbols-v1", build_profile="default", adapter_parameters={},
    )


def test_phase_two_inputs_are_read_back_before_attempt_and_fake_digest_fails(tmp_path: Path) -> None:
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    root = Path(run["run_root"])
    inventory, baseline = _baseline(tmp_path, generated_root=root.parent)
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}
    pilot_state.append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    with pytest.raises(ValueError, match="baseline proof"):
        pilot_state.create_attempt(root, identity, {"digest": baseline["digest"]})

    frozen = pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline)
    attempt = pilot_state.create_attempt(root, identity, frozen["baseline"])

    assert [event["event_type"] for event in pilot_state.derive_state(root)["events"]] == [
        "RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND", "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED",
    ]
    assert attempt["baseline_digest"] == baseline["digest"] == frozen["baseline"]["digest"]


def test_freeze_rejects_a_digest_valid_but_semantically_incomplete_baseline(tmp_path: Path) -> None:
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    root = Path(run["run_root"])
    _inventory, baseline = _baseline(tmp_path, generated_root=root.parent)
    pilot_state.append_event(
        root, "MODULE_SELECTED", actor="controller",
        artifact_digest=pilot_state.module_selection_digest(tmp_path, "."),
    )
    forged = {**baseline, "inputs": baseline["inputs"][:-1]}
    forged.pop("digest")
    forged["digest"] = pilot_state._digest(forged)

    with pytest.raises(ValueError, match="phase two input receipt is invalid"):
        pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, forged)
    assert not (root / "inventories").exists()


def test_freeze_rejects_a_baseline_for_a_different_selected_module(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_identity = {"project": str(tmp_path.resolve()), "module": "nested", "policy_profile": "cases-only-v1"}
    baseline = build_phase_two_baseline(tmp_path, nested_identity, skill_pack_root=tmp_path / ".pilot-runs")
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    root = Path(run["run_root"])
    pilot_state.append_event(
        root, "MODULE_SELECTED", actor="controller",
        artifact_digest=pilot_state.module_selection_digest(tmp_path, "."),
    )

    with pytest.raises(ValueError, match="module selection binding"):
        pilot_state.freeze_phase_two_inputs(root, tmp_path, nested, baseline)


def test_durable_inventory_excludes_exact_skill_pack_and_generated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack = tmp_path / "pipeline"
    pack.mkdir()
    (pack / "SKILL.md").write_text("pipeline instructions\n", encoding="utf-8")
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": "cases-only-v1"}
    baseline = build_phase_two_baseline(
        tmp_path,
        identity,
        skill_pack_root=tmp_path / ".pilot-runs",
        resolved_skill_pack_root=pack,
    )
    run = pilot_state.create_run(tmp_path, "cases-only-v1", AUTHORIZATION)
    root = Path(run["run_root"])
    monkeypatch.setattr(pilot_state, "_PACK_ROOT", pack)
    pilot_state.append_event(
        root, "MODULE_SELECTED", actor="controller",
        artifact_digest=pilot_state.module_selection_digest(tmp_path, "."),
    )

    frozen = pilot_state.freeze_phase_two_inputs(root, tmp_path, tmp_path, baseline)

    assert all(not item["project_path"].startswith("pipeline/") for item in frozen["inventory"]["files"])
    assert {item["safe_label"] for item in frozen["inventory"]["exclusions"]} >= {"skill-pack", "pipeline-state"}
