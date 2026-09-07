import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

MODULE_PYTHON = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python"

def build_phase_two_baseline(
    project: Path,
    identity: dict,
    *,
    skill_pack_root: Path | None = None,
    resolved_skill_pack_root: Path | None = None,
) -> dict:
    """Build one truthful Phase 2 baseline before a run boundary exists."""
    from tools.project_inventory import build_execution_baseline, build_inventory, build_skillsrc_authority_receipt

    project = Path(project).resolve()
    module = project / identity["module"]
    interpreter = MODULE_PYTHON
    if skill_pack_root is not None:
        Path(skill_pack_root).mkdir(parents=True, exist_ok=True)
    skillsrc = project / ".skillsrc"
    if not skillsrc.exists():
        skillsrc.write_text(json.dumps({
            "schema_version": "5.0.0",
            "version": "3.0",
            "project": {"name": project.name},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [{
                "id": "fixture", "root": identity["module"],
                "stack": {"language": "python", "build_tool": "pip"},
                "paths": {"source": ["src"], "tests": ["tests"]},
                "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": interpreter, "build_profile": "default", "adapter_parameters": {}},
                "detected_from": ["pyproject.toml"],
            }],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
    runtime = module / interpreter
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if not runtime.exists():
        runtime.write_bytes(b"fixture-runtime-v1")
        runtime.chmod(0o755)
    inventory = build_inventory(
        project,
        module,
        skill_pack_root=resolved_skill_pack_root,
        generated_roots=() if skill_pack_root is None else (skill_pack_root,),
    )
    by_path = {item["project_path"]: item["opaque_id"] for item in inventory["files"]}
    module_prefix = "" if identity["module"] == "." else identity["module"].replace("\\", "/") + "/"
    parent_build_names = {
        "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "pipfile", "pom.xml",
        "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "package.json", "go.mod", "go.work",
    }
    parent_ids = [
        item["opaque_id"] for item in inventory["files"]
        if item["project_path"] != ".skillsrc"
        and module_prefix
        and not item["project_path"].startswith(module_prefix)
        and Path(item["project_path"]).name.casefold() in parent_build_names
    ]
    execution_ids = [
        item["opaque_id"] for item in inventory["files"]
        if item["opaque_id"] != by_path[".skillsrc"] and item["opaque_id"] not in parent_ids
    ]
    from tools.project_inventory import runtime_identity
    return build_execution_baseline(
        inventory, project_root=project, requirements={"requirement_id": "fixture", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=by_path[".skillsrc"],
        skillsrc_authority=build_skillsrc_authority_receipt(skillsrc.read_bytes()),
        execution_file_ids=execution_ids, parent_build_file_ids=parent_ids,
        interpreter_path=interpreter, interpreter_identity=runtime_identity(module, interpreter),
        wrapper_path=None, wrapper_identity=None, adapter_id="pytest:selected-symbols-v1", build_profile="default", adapter_parameters={},
    )


def phase_two_baseline(root: Path, project: Path, identity: dict) -> dict:
    """Fixture-only real Phase 2 proof for tests that create durable attempts."""
    from tools.pilot_state import freeze_phase_two_inputs

    project = Path(project).resolve()
    module = project / identity["module"]
    baseline = build_phase_two_baseline(project, identity, skill_pack_root=Path(root).parent)
    return dict(freeze_phase_two_inputs(root, project, module, baseline)["baseline"])


def run_module(root: Path, module: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def projection_fixture(root: Path, target: Path) -> Path:
    from tools.render_contract_docs import _rendered_files

    contract_dir = target / "contracts"
    contract_dir.mkdir(parents=True)
    shutil.copy2(root / "contracts" / "pipeline.json", contract_dir / "pipeline.json")
    contract = json.loads((contract_dir / "pipeline.json").read_text(encoding="utf-8"))
    for relative_path, contents in _rendered_files(contract).items():
        path = target / relative_path
        path.write_text(contents, encoding="utf-8", newline="\n")
    return target
