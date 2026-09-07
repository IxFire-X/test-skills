import hashlib
from pathlib import Path

import pytest

from tools.project_inventory import (
    InventoryError,
    build_execution_baseline,
    build_inventory,
    build_skillsrc_authority_receipt,
    freeze_execution_baseline,
    read_execution_baseline,
    runtime_identity,
    validate_execution_baseline,
)


def _file_id(inventory: dict, project_path: str) -> str:
    return next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == project_path)


def _authority(project: Path) -> dict:
    return build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes())


def _skillsrc(project: Path, module: str) -> None:
    (project / ".skillsrc").write_text(
        '{"schema_version":"5.0.0","version":"3.0","project":{"name":"fixture"},"discovery":{"on_missing":"automatic","conflict_policy":"ask_user"},'
        f'"modules":[{{"id":"selected","root":"{module}",'
        '"stack":{"language":"python","build_tool":"pip"},'
        '"paths":{"source":["src"],"tests":["tests"]},'
        '"test":{"framework":"pytest","adapter_id":"pytest:selected-symbols-v1",'
        '"interpreter":".venv/Scripts/python.exe","build_profile":"default","adapter_parameters":{}},"detected_from":["pyproject.toml"]}]}'
        '\n',
        encoding="utf-8",
    )


def test_pytest_baseline_binds_only_interpreter_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    module.mkdir(parents=True)
    _skillsrc(project, "service")
    (module / "pyproject.toml").write_text("[project]\nname = 'service'\n", encoding="utf-8")
    (module / "test_app.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    interpreter = project / ".venv" / "Scripts" / "python.exe"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_bytes(b"python-runtime-v1")
    inventory = build_inventory(project, module)

    baseline = build_execution_baseline(
        inventory, project_root=project,
        requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"),
        skillsrc_authority=_authority(project),
        execution_file_ids=[_file_id(inventory, "service/pyproject.toml"), _file_id(inventory, "service/test_app.py")],
        parent_build_file_ids=[],
        interpreter_path=".venv/Scripts/python.exe",
        interpreter_identity=runtime_identity(project, ".venv/Scripts/python.exe"),
        wrapper_path=None,
        wrapper_identity=None,
        adapter_id="pytest:selected-symbols-v1",
        build_profile="default",
        adapter_parameters={},
    )

    assert baseline["interpreter_identity"] == runtime_identity(project, ".venv/Scripts/python.exe")
    assert baseline["test_root"] == "tests"
    assert baseline["skillsrc_authority"]["skillsrc_digest"] == next(
        item["content_digest"] for item in baseline["inputs"] if item["opaque_id"] == baseline["skillsrc_file_id"]
    )
    assert "wrapper_path" not in baseline
    with pytest.raises(InventoryError, match="cover every eligible"):
        build_execution_baseline(
            inventory, project_root=project, requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
            skillsrc_file_id=_file_id(inventory, ".skillsrc"),
            skillsrc_authority=_authority(project),
            execution_file_ids=[_file_id(inventory, "service/test_app.py")], parent_build_file_ids=[],
            interpreter_path=".venv/Scripts/python.exe", interpreter_identity=runtime_identity(project, ".venv/Scripts/python.exe"),
            wrapper_path=None, wrapper_identity=None, adapter_id="pytest:selected-symbols-v1",
            build_profile="default", adapter_parameters={},
        )
    with pytest.raises(InventoryError, match="pytest forbids"):
        build_execution_baseline(
            inventory, project_root=project, requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
            skillsrc_file_id=_file_id(inventory, ".skillsrc"),
            skillsrc_authority=_authority(project),
            execution_file_ids=[_file_id(inventory, "service/pyproject.toml"), _file_id(inventory, "service/test_app.py")],
            parent_build_file_ids=[],
            interpreter_path=".venv/Scripts/python.exe", interpreter_identity=runtime_identity(project, ".venv/Scripts/python.exe"),
            wrapper_path="mvnw", wrapper_identity="sha256:" + "c" * 64,
            adapter_id="pytest:selected-symbols-v1", build_profile="default", adapter_parameters={},
        )
    with pytest.raises(InventoryError, match="confirmed replacement requires"):
        build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes(), acceptance_kind="confirmed-replacement")


def test_nested_maven_baseline_binds_root_skillsrc_and_detects_drift(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "parent" / "child"
    module.mkdir(parents=True)
    _skillsrc(project, "parent/child")
    (project / "parent" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (project / "parent" / ".mvn").mkdir()
    (project / "parent" / ".mvn" / "maven.config").write_text("-Dstyle.color=always\n", encoding="utf-8")
    (module / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (module / "mvnw").write_bytes(b"maven-wrapper-v1")
    (module / "src.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "outside.py").write_text("OUTSIDE = 1\n", encoding="utf-8")
    inventory = build_inventory(project, module)
    baseline = build_execution_baseline(
        inventory, project_root=project,
        requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"),
        skillsrc_authority=_authority(project),
        execution_file_ids=[
            _file_id(inventory, "parent/child/mvnw"),
            _file_id(inventory, "parent/child/pom.xml"),
            _file_id(inventory, "parent/child/src.py"),
        ],
        parent_build_file_ids=[_file_id(inventory, "parent/.mvn/maven.config"), _file_id(inventory, "parent/pom.xml")],
        interpreter_path=None,
        interpreter_identity=None,
        wrapper_path="mvnw",
        wrapper_identity=runtime_identity(module, "mvnw"),
        adapter_id="maven-wrapper:selected-symbols-v1",
        build_profile="default",
        adapter_parameters={},
    )
    frozen = freeze_execution_baseline(tmp_path / "baseline.json", baseline)
    assert read_execution_baseline(tmp_path / "baseline.json") == frozen
    assert frozen["skillsrc_file_id"] == _file_id(inventory, ".skillsrc")
    assert frozen["wrapper_identity"] == runtime_identity(module, "mvnw")
    assert "interpreter_path" not in frozen

    (project / "outside.py").write_text("OUTSIDE = 2\n", encoding="utf-8")
    assert validate_execution_baseline(
        frozen, build_inventory(project, module),
        runtime_identity_value=runtime_identity(module, "mvnw"),
    ) == {
        "status": "UNCHANGED", "requires_child_attempt": False,
    }
    (module / "src.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert validate_execution_baseline(
        frozen, build_inventory(project, module),
        runtime_identity_value=runtime_identity(module, "mvnw"),
    ) == {
        "status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True,
    }
    (module / "late_dependency.py").write_text("LATE = 1\n", encoding="utf-8")
    latest = build_inventory(project, module)
    assert validate_execution_baseline(
        frozen, latest, late_dependency_file_ids=[_file_id(latest, "parent/child/late_dependency.py")],
        runtime_identity_value=runtime_identity(module, "mvnw"),
    ) == {"status": "NOT_RUNNABLE", "reason_code": "BASELINE_INCOMPLETE", "requires_child_attempt": True}


def test_wrapper_adapter_forbids_interpreter_and_nonempty_parameters(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _skillsrc(project, ".")
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    inventory = build_inventory(project, project)
    common = {
        "requirements": {"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        "skillsrc_file_id": _file_id(inventory, ".skillsrc"),
        "skillsrc_authority": _authority(project),
        "execution_file_ids": [_file_id(inventory, "pom.xml")], "parent_build_file_ids": [],
        "wrapper_path": "mvnw", "wrapper_identity": "sha256:" + "c" * 64,
        "adapter_id": "maven-wrapper:selected-symbols-v1", "build_profile": "default",
    }
    with pytest.raises(InventoryError, match="wrapper adapters forbid"):
        build_execution_baseline(inventory, project_root=project, **common, interpreter_path="python", interpreter_identity="sha256:" + "b" * 64, adapter_parameters={})
    with pytest.raises(InventoryError, match="closed empty"):
        build_execution_baseline(inventory, project_root=project, **common, interpreter_path=None, interpreter_identity=None, adapter_parameters={"selection": "exact"})


def test_runtime_identity_rejects_absolute_parent_and_reparse_paths(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime.exe"
    runtime.write_bytes(b"runtime")
    with pytest.raises(InventoryError, match="safe relative"):
        runtime_identity(tmp_path, "../runtime.exe")
    with pytest.raises(InventoryError, match="safe relative"):
        runtime_identity(tmp_path, str(runtime.resolve()))
    link = tmp_path / "runtime-link.exe"
    try:
        link.symlink_to(runtime)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    with pytest.raises(InventoryError, match="symlink or reparse"):
        runtime_identity(tmp_path, "runtime-link.exe")


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX venv interpreter links")
def test_posix_venv_identity_binds_config_and_rejects_a_foreign_link(tmp_path: Path) -> None:
    import venv

    venv.EnvBuilder(with_pip=False, symlinks=True).create(tmp_path / ".venv")
    identity = runtime_identity(tmp_path, ".venv/bin/python")
    config = tmp_path / ".venv/pyvenv.cfg"
    config.write_text(config.read_text(encoding="utf-8") + "\ninclude-system-site-packages = true\n", encoding="utf-8")
    assert runtime_identity(tmp_path, ".venv/bin/python") != identity
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"not an interpreter")
    interpreter = tmp_path / ".venv/bin/python"
    interpreter.unlink()
    interpreter.symlink_to(foreign)
    with pytest.raises(InventoryError, match="runtime symlink"):
        runtime_identity(tmp_path, ".venv/bin/python")


def test_execution_baseline_rejects_same_named_unrelated_project_root(tmp_path: Path) -> None:
    project = tmp_path / "first" / "service"
    unrelated = tmp_path / "second" / "service"
    module = project / "module"
    module.mkdir(parents=True)
    unrelated.mkdir(parents=True)
    _skillsrc(project, "module")
    (module / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    runtime = module / ".venv" / "Scripts" / "python.exe"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime")
    _skillsrc(unrelated, "module")
    (unrelated / "module").mkdir()
    (unrelated / "module" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    inventory = build_inventory(project, module)

    with pytest.raises(InventoryError, match="frozen inventory"):
        build_execution_baseline(
            inventory, project_root=unrelated, requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
            skillsrc_file_id=_file_id(inventory, ".skillsrc"), skillsrc_authority=_authority(project),
            execution_file_ids=[_file_id(inventory, "module/app.py")], parent_build_file_ids=[],
            interpreter_path=".venv/Scripts/python.exe", interpreter_identity=runtime_identity(module, ".venv/Scripts/python.exe"),
            wrapper_path=None, wrapper_identity=None, adapter_id="pytest:selected-symbols-v1", build_profile="default", adapter_parameters={},
        )


def test_proved_sibling_dependency_is_bound_and_unproved_dependency_is_not_runnable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    dependency = project / "shared" / "domain.py"
    module.mkdir(parents=True)
    dependency.parent.mkdir()
    _skillsrc(project, "service")
    (module / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    dependency.write_text("DOMAIN = 1\n", encoding="utf-8")
    runtime = module / ".venv" / "Scripts" / "python.exe"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"runtime-v1")
    inventory = build_inventory(project, module, proved_dependency_files=[dependency])
    baseline = build_execution_baseline(
        inventory, project_root=project,
        requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"),
        skillsrc_authority=_authority(project),
        execution_file_ids=[_file_id(inventory, "service/app.py")],
        parent_build_file_ids=[],
        proved_dependency_file_ids=[_file_id(inventory, "shared/domain.py")],
        interpreter_path=".venv/Scripts/python.exe",
        interpreter_identity=runtime_identity(module, ".venv/Scripts/python.exe"),
        wrapper_path=None,
        wrapper_identity=None,
        adapter_id="pytest:selected-symbols-v1",
        build_profile="default",
        adapter_parameters={},
    )

    assert any(item["project_path"] == "shared/domain.py" for item in baseline["inputs"])
    assert validate_execution_baseline(
        baseline,
        inventory,
        late_dependency_file_ids=["file-" + "f" * 24],
        runtime_identity_value=runtime_identity(module, ".venv/Scripts/python.exe"),
    ) == {"status": "NOT_RUNNABLE", "reason_code": "BASELINE_INCOMPLETE", "requires_child_attempt": True}


@pytest.mark.parametrize("secret_path", ["service/pyproject.toml", "parent/.mvn/maven.config"])
def test_secret_suspected_execution_config_makes_baseline_not_runnable(tmp_path: Path, secret_path: str) -> None:
    project = tmp_path / "project"
    module = project / ("service" if secret_path.startswith("service/") else "parent/child")
    module.mkdir(parents=True)
    _skillsrc(project, "service" if secret_path.startswith("service/") else "parent/child")
    secret = project / Path(secret_path)
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text('api_key = "not-a-real-secret-value"\n', encoding="utf-8")
    source = module / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    inventory = build_inventory(project, module)
    serialized = repr(inventory)

    assert any(item["project_path"] == secret_path and item["reason_code"] == "SECRET_SUSPECTED" for item in inventory["exclusions"])
    assert "not-a-real-secret-value" not in serialized
    assert hashlib.sha256(secret.read_bytes()).hexdigest() not in serialized
    with pytest.raises(InventoryError, match="NOT_RUNNABLE/BASELINE_INCOMPLETE"):
        build_execution_baseline(
            inventory, project_root=project,
            requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
            skillsrc_file_id=_file_id(inventory, ".skillsrc"),
            skillsrc_authority=_authority(project),
            execution_file_ids=[_file_id(inventory, source.relative_to(project).as_posix())],
            parent_build_file_ids=[],
            interpreter_path=".venv/Scripts/python.exe",
            interpreter_identity="sha256:" + "b" * 64,
            wrapper_path=None,
            wrapper_identity=None,
            adapter_id="pytest:selected-symbols-v1",
            build_profile="default",
            adapter_parameters={},
        )


def test_unbound_dotenv_is_excluded_without_blocking_execution_baseline(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    module.mkdir(parents=True)
    _skillsrc(project, "service")
    dotenv = module / ".env"
    dotenv.write_text("API_TOKEN=not-a-real-secret-value\n", encoding="utf-8")
    source = module / "app.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    inventory = build_inventory(project, module)

    assert any(item["project_path"] == "service/.env" and item["reason_code"] == "SECRET_SUSPECTED" for item in inventory["exclusions"])
    assert "not-a-real-secret-value" not in repr(inventory)
    assert hashlib.sha256(dotenv.read_bytes()).hexdigest() not in repr(inventory)
    baseline = build_execution_baseline(
        inventory, project_root=project,
        requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=_file_id(inventory, ".skillsrc"),
        skillsrc_authority=_authority(project),
        execution_file_ids=[_file_id(inventory, "service/app.py")],
        parent_build_file_ids=[],
        interpreter_path=".venv/Scripts/python.exe",
        interpreter_identity="sha256:" + "b" * 64,
        wrapper_path=None,
        wrapper_identity=None,
        adapter_id="pytest:selected-symbols-v1",
        build_profile="default",
        adapter_parameters={},
    )

    assert {item["project_path"] for item in baseline["inputs"]} == {".skillsrc", "service/app.py"}
