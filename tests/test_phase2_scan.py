import json
from pathlib import Path
from types import SimpleNamespace

from tools import run_pipeline
from tools.init_skillsrc import _payload


def _skillsrc() -> bytes:
    document = {
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "nested", "root": "services/api", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": ".venv/Scripts/python.exe", "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["services/api/pyproject.toml"],
        }],
    }
    return _payload(document)


def _args(project: Path, target: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(project=str(project), module="nested", target=target, docs=None)


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "project"
    module = project / "services" / "api"
    (module / "src").mkdir(parents=True)
    (project / ".skillsrc").write_bytes(_skillsrc())
    (module / "pyproject.toml").write_text("[project]\nname = 'api'\n", encoding="utf-8")
    runtime = module / ".venv" / "Scripts" / "python.exe"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"fixture-runtime-v1")
    return project, module


def _durable(payload: dict) -> tuple[Path, str]:
    run_root = Path(payload["run_root"])
    assert run_root == Path(payload["project"]) / ".pilot-runs" / payload["run_id"]
    return run_root, payload["attempt_id"]


def test_scan_publishes_full_nested_inventory_and_safe_receipts(tmp_path: Path, monkeypatch) -> None:
    project, module = _project(tmp_path)
    source = module / "src"
    for number in range(105):
        (source / f"unit_{number}.py").write_text(f"VALUE = {number}\n", encoding="utf-8")
    (source / ".env").write_text("API_TOKEN=raw-secret-never-publish\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    assert run_pipeline.cmd_scan(_args(project)) == 0

    from tools.pilot_state import derive_state, read_run
    from tools.project_inventory import read_exclusion_receipt, read_inventory_receipt

    scan = payloads[-1]
    run_root, attempt_id = _durable(scan)
    run = read_run(run_root)
    state = derive_state(run_root)
    inventory = read_inventory_receipt(run_root / "inventories" / (scan["inventory_digest"].removeprefix("sha256:") + ".json"))
    exclusions = read_exclusion_receipt(run_root / "exclusions" / (scan["exclusion_digest"].removeprefix("sha256:") + ".json"))
    context_receipts = sorted((run_root / "context-selections" / attempt_id).glob("*.json"))
    assert [event["event_type"] for event in state["events"]][:6] == [
        "RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND",
        "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED",
    ]
    assert all(event["event_type"] == "CONTEXT_SELECTED" for event in state["events"][6:])
    assert run["manifest"]["run_id"] == scan["run_id"]
    assert state["attempts"][0]["attempt_id"] == attempt_id
    assert inventory["module"] == "services/api"
    assert len(inventory["files"]) >= 106
    assert all("content" not in item for item in inventory["files"])
    assert any(item["project_path"] == "services/api/src/.env" for item in exclusions["exclusions"])
    assert context_receipts
    assert (run_root / "baselines" / (scan["baseline_digest"].removeprefix("sha256:") + ".json")).is_file()
    assert not (project / "docs" / "to_do" / "test-pipeline").exists()
    assert "raw-secret-never-publish" not in json.dumps(payloads)
    assert "VALUE = 0" not in json.dumps(payloads)

    assert run_pipeline.cmd_status(SimpleNamespace(project=str(project), run=scan["run_id"])) == 0
    status = payloads[-1]
    assert (status["run_id"], status["attempt_id"], status["attempt_state"]) == (scan["run_id"], attempt_id, "ACTIVE")
    execution = run_pipeline._validated_execution_coordinates(project, scan["run_id"])
    assert execution["attempt"]["attempt_id"] == attempt_id


def test_scan_target_narrows_only_context_and_never_executes(tmp_path: Path, monkeypatch) -> None:
    project, module = _project(tmp_path)
    source = module / "src"
    (source / "selected.py").write_text("SELECTED = True\n", encoding="utf-8")
    (source / "other.py").write_text("OTHER = True\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    assert run_pipeline.cmd_scan(_args(project, "src/selected.py")) == 0

    from tools.project_inventory import read_inventory_receipt

    scan = payloads[-1]
    run_root, attempt_id = _durable(scan)
    inventory = read_inventory_receipt(run_root / "inventories" / (scan["inventory_digest"].removeprefix("sha256:") + ".json"))
    assert {item["project_path"] for item in inventory["files"]} >= {
        "services/api/src/selected.py", "services/api/src/other.py",
    }
    context_path = next((run_root / "context-selections" / attempt_id).glob("*.json"))
    selected = json.loads(context_path.read_text(encoding="utf-8"))
    selected_paths = {item["project_path"] for item in selected["files"]}
    assert "services/api/src/selected.py" in selected_paths
    assert "services/api/src/other.py" not in selected_paths
    assert payloads[-1]["status"] == "ok"


def test_scan_rejects_an_oversized_context_file_without_silent_truncation(tmp_path: Path, monkeypatch) -> None:
    project, module = _project(tmp_path)
    source = module / "src"
    (source / "large.py").write_bytes(b"x" * (run_pipeline._SCAN_CONTEXT_BYTE_BUDGET + 1))
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    assert run_pipeline.cmd_scan(_args(project, "src/large.py")) == 2
    from tools.pilot_state import derive_state

    run_root, attempt_id = _durable(payloads[-1])
    assert not (run_root / "context-selections" / attempt_id).exists()
    assert [event["event_type"] for event in derive_state(run_root)["events"]] == [
        "RUN_CREATED", "MODULE_SELECTED", "INVENTORY_READY", "SNAPSHOT_BOUND",
        "EXECUTION_BASELINE_FROZEN", "ATTEMPT_CREATED",
    ]
    assert payloads[-1]["reason"] == "CONTEXT_SELECTION_INVALID"


def test_scan_denied_directory_keeps_diagnostic_run_without_inventory_ready(tmp_path: Path, monkeypatch) -> None:
    import os

    import pytest

    from tools.pilot_state import derive_state
    from tools.project_inventory import InventoryError, build_inventory

    project, module = _project(tmp_path)
    denied = module / "src"
    original_scandir = os.scandir

    def scandir(path):
        if Path(path) == denied:
            raise PermissionError(13, "raw-secret-never-publish", str(denied))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(InventoryError, match="directory is unreadable: services/api/src"):
        build_inventory(project, module)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    assert run_pipeline.cmd_scan(_args(project)) == 2

    scan = payloads[-1]
    run_root, attempt_id = _durable(scan)
    assert attempt_id is None
    assert [event["event_type"] for event in derive_state(run_root)["events"]] == [
        "RUN_CREATED", "MODULE_SELECTED",
    ]
    assert scan["reason"] == "INVENTORY_INVALID"
    assert scan["detail"] == "inventory directory is unreadable: services/api/src"
    assert scan["scope"]["module"] == "services/api"
    assert "raw-secret-never-publish" not in json.dumps(payloads)
    assert run_pipeline.cmd_status(SimpleNamespace(project=str(project), run=scan["run_id"])) == 0


def test_cases_only_scan_needs_no_runtime_and_cannot_supply_local_execution_proof(tmp_path: Path, monkeypatch) -> None:
    from tools.pilot_state import derive_state
    from tools.project_inventory import InventoryError, read_execution_baseline, read_inventory_receipt, validate_execution_baseline_binding
    import pytest

    project, module = _project(tmp_path)
    from tools.skillsrc_manifest import parse_skillsrc_bytes
    document = parse_skillsrc_bytes((project / ".skillsrc").read_bytes())
    document["modules"][0].pop("test")
    (project / ".skillsrc").write_bytes(_payload(document))
    (module / ".venv/Scripts/python.exe").unlink()
    (module / "src/app.py").write_text("VALUE = 1\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.profile = "cases-only-v1"

    assert run_pipeline.cmd_scan(args) == 0

    scan = payloads[-1]
    root, _attempt = _durable(scan)
    baseline = read_execution_baseline(root / "baselines" / (scan["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(root / "inventories" / (scan["inventory_digest"].removeprefix("sha256:") + ".json"))
    assert baseline["policy_profile"] == "cases-only-v1"
    assert not {"test_root", "adapter_id", "build_profile", "adapter_parameters", "interpreter_path", "wrapper_path", "executable_path"} & baseline.keys()
    assert "EXECUTION_BASELINE_FROZEN" in [event["event_type"] for event in derive_state(root)["events"]]
    with pytest.raises(InventoryError, match="profile"):
        validate_execution_baseline_binding(baseline, inventory, module)
    assert run_pipeline.cmd_scan(_args(project)) == 2
