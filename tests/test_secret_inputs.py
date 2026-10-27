"""FaT-S: a secret-suspected execution input enters the baseline as a fingerprint only, and never stops the cases."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_first_run import _python_project, _write
from tests.test_phase2_scan import _args
from tools import run_pipeline

SECRET = "s3cr3t-XYZ-not-real"
SECRET_PATH = "services/api/config/app.yml"


def _secret_project(tmp_path: Path) -> tuple[Path, str]:
    project = _python_project(tmp_path, runtime=True)
    path = _write(project, SECRET_PATH, f"db:\n  password: {SECRET}\n")
    return project, hashlib.sha256(path.read_bytes()).hexdigest()


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict, str]:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.profile = "local-pilot-v1"
    code = run_pipeline.cmd_scan(args)
    out = capsys.readouterr()
    return code, payloads[-1], json.dumps(payloads, ensure_ascii=False) + out.out + out.err


def _baseline(run_root: Path) -> dict:
    [path] = list((run_root / "baselines").glob("*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_secret_execution_config_enters_the_baseline_as_a_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    project, digest = _secret_project(tmp_path)
    code, scan, printed = _scan(project, monkeypatch, capsys)
    assert code == 0, scan
    assert scan["policy_profile"] == "local-pilot-v1" and "execution_skipped" not in scan
    root = Path(scan["run_root"])
    baseline = _baseline(root)
    secret_file = project / SECRET_PATH
    assert baseline["secret_inputs"] == [{"project_path": SECRET_PATH, "content_digest": "sha256:" + digest, "byte_count": secret_file.stat().st_size}]
    for path in root.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert SECRET.encode("utf-8") not in data, path
            if path.parent.name != "baselines":
                assert digest.encode("ascii") not in data, path
    assert SECRET not in printed and digest not in printed


def test_a_changed_or_missing_secret_file_is_baseline_drift(tmp_path: Path) -> None:
    from tests.test_execution_baseline import _authority, _file_id
    from tools.project_inventory import build_execution_baseline, build_inventory, runtime_identity, validate_execution_baseline

    project, digest = _secret_project(tmp_path)
    module = project / "services" / "api"
    inventory = build_inventory(project, module)
    baseline = build_execution_baseline(
        inventory, project_root=project, requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"), skillsrc_authority=_authority(project),
        execution_file_ids=[item["opaque_id"] for item in inventory["files"] if item["project_path"] != ".skillsrc"],
        parent_build_file_ids=[], interpreter_path=".venv/Scripts/python.exe",
        interpreter_identity=runtime_identity(module, ".venv/Scripts/python.exe"),
        adapter_id="pytest:selected-symbols-v1", build_profile="default", adapter_parameters={},
    )
    check = lambda: validate_execution_baseline(  # noqa: E731
        baseline, build_inventory(project, module), runtime_identity_value=baseline["interpreter_identity"], project_root=project)
    assert check()["status"] == "UNCHANGED"
    secret_file = project / SECRET_PATH
    secret_file.write_text(f"db:\n  password: {SECRET}-2\n", encoding="utf-8")
    result = check()
    assert result["status"] == "DECLARED_INPUT_DRIFT"
    assert SECRET not in json.dumps(result) and digest not in json.dumps(result)
    secret_file.unlink()
    assert check()["status"] == "DECLARED_INPUT_DRIFT"


def test_the_drift_stop_names_no_secret_and_no_digest(tmp_path: Path) -> None:
    """The exec stop text is fixed: it carries neither the file bytes nor its fingerprint."""
    import inspect

    source = inspect.getsource(run_pipeline._revalidate_execution_baseline)
    assert "child attempt" not in source and "_drift_report" in source  # FaT-G: the stop names paths only
    assert "secret_inputs" not in source and "content_digest" not in source


def test_the_report_carries_no_secret_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.run_report import write_report

    project, digest = _secret_project(tmp_path)
    _code, scan, _printed = _scan(project, monkeypatch, capsys)
    written = write_report(project, scan["run_id"])
    for path in written:
        data = Path(path).read_bytes()
        assert digest.encode("ascii") not in data and SECRET.encode("utf-8") not in data, path


def test_ready_is_clean_for_a_secret_execution_config(tmp_path: Path) -> None:
    from tools.ready import check as inspect_project

    project, _digest = _secret_project(tmp_path)
    report = inspect_project(project, ["services/api/docs/feature.md"], profile="local-pilot-v1")
    assert not [check for check in report["checks"] if check["level"] == "STOP"], report


def _two_test_roots(tmp_path: Path) -> Path:
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import load_skillsrc

    project = _python_project(tmp_path, runtime=True)
    document = load_skillsrc(project / ".skillsrc")
    document["modules"][0]["paths"]["tests"] = ["tests", "it"]
    (project / ".skillsrc").write_bytes(_payload(document))
    return project


def test_ready_shows_a_baseline_blocker_with_its_file_and_fix(tmp_path: Path) -> None:
    from tools.ready import check as inspect_project

    report = inspect_project(_two_test_roots(tmp_path), ["services/api/docs/feature.md"], profile="local-pilot-v1")
    stops = [check for check in report["checks"] if check["id"].startswith("execution:baseline:")]
    assert [(check["block"], check["level"]) for check in stops] == [("execution", "WARN")], stops
    assert "тестовый корень" in stops[0]["message"] and stops[0]["fix"]


def test_a_baseline_blocker_lowers_the_scan_to_cases_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    code, scan, _printed = _scan(_two_test_roots(tmp_path), monkeypatch, capsys)
    assert code == 0, scan
    assert scan["policy_profile"] == "cases-only-v1" and scan["requested_policy_profile"] == "local-pilot-v1"
    assert "тестовый корень" in scan["execution_skipped"]["reason"] and scan["execution_skipped"]["fix"]


@pytest.mark.parametrize("change", ["edit", "delete"])
def test_exec_stops_on_a_changed_secret_file_without_printing_it(tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str) -> None:
    """End to end through run_pipeline exec: both callers of validate_execution_baseline pass the project root."""
    import shutil

    import tests.test_finish as finish
    from tests.test_project_native_pytest import _module_python, _reviewed_inputs
    from tools.project_inventory import read_execution_baseline
    from tools.pilot_state import derive_state

    fixture = Path(finish.__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    secret_file = project / "config" / "app.yml"
    secret_file.parent.mkdir()
    secret_file.write_text(f"db:\n  password: {SECRET}\n", encoding="utf-8")
    digest = hashlib.sha256(secret_file.read_bytes()).hexdigest()
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": finish.MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(project)
    _document, _artifact, _review, _evidence, run_root, attempt_id, _authorization, _delta = _reviewed_inputs(project)
    attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    assert baseline["secret_inputs"][0]["project_path"] == "config/app.yml"

    if change == "edit":
        secret_file.write_text(f"db:\n  password: {SECRET}-changed\n", encoding="utf-8")
    else:
        secret_file.unlink()
    capsys.readouterr()
    code = run_pipeline.main(["exec", "--project", str(project), "--run", run_root.name, "--module", "python-pytest"])
    out = capsys.readouterr()
    answer = json.loads(out.out)
    assert code == 2 and answer["status"] == "error" and answer["reason"] == "BASELINE_DRIFT", answer
    printed = out.out + out.err
    assert SECRET not in printed and digest not in printed
