import json
import hashlib
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
    requirement_file = project / "docs" / "feature.md"
    requirement_file.parent.mkdir(parents=True, exist_ok=True)
    if not requirement_file.exists():
        requirement_file.write_text("Пользователь видит карточку товара.\nПользователь может открыть список товаров.\n", encoding="utf-8")
    requirement_binding = {"module_id": "fixture", "selected_target": None, "docs": [{"path": "docs/feature.md", "sha256": "sha256:" + hashlib.sha256(requirement_file.read_bytes()).hexdigest()}]}
    interpreter = MODULE_PYTHON
    policy_profile = identity.get("policy_profile", "local-pilot-v1")
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
                **({"test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": interpreter, "build_profile": "default", "adapter_parameters": {}}} if policy_profile == "local-pilot-v1" else {}),
                "detected_from": ["pyproject.toml"],
            }],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
    if policy_profile == "local-pilot-v1":
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
    runtime_facts = {}
    if policy_profile == "local-pilot-v1":
        runtime_facts = {"interpreter_path": interpreter, "interpreter_identity": runtime_identity(module, interpreter), "adapter_id": "pytest:selected-symbols-v1", "build_profile": "default", "adapter_parameters": {}}
    return build_execution_baseline(
        inventory, project_root=project, requirements={"requirement_id": "fixture", "digest": "sha256:" + hashlib.sha256(json.dumps(requirement_binding, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()},
        skillsrc_file_id=by_path[".skillsrc"],
        skillsrc_authority=build_skillsrc_authority_receipt(skillsrc.read_bytes()),
        execution_file_ids=execution_ids, parent_build_file_ids=parent_ids,
        policy_profile=policy_profile, **runtime_facts,
    )


def review_payload(project, document, *, package=None, automation=None):
    content = (project / "docs/feature.md").read_bytes()
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return {"document": document, "automation": automation, "package_binding": package,
            "sources": [{"path": "docs/feature.md", "sha256": digest, "content": content.decode("utf-8")}], "contexts": [],
            "requirements_binding": {"module_id": "fixture", "selected_target": None, "docs": [{"path": "docs/feature.md", "sha256": digest}]}}


def complete_review_parts(run_root, attempt_id, key="canonical", *, findings=(), corrections=()):
    """Synthetic model assessments for protocol tests only, never qualification evidence."""
    from tools.pilot_state import next_review_part, open_review_part, submit_review_part, finish_review
    from tools.review_parts import review_digest
    first = True
    while (part := next_review_part(run_root, attempt_id, key)) is not None:
        isolation = {"fresh_context": True, "distinct_invocations": True, "role_policy": "canonical-reviewer-v2" if key == "canonical" else "autotest-static-reviewer-v2"}
        isolation["evidence_digest"] = review_digest(isolation)
        open_review_part(run_root, attempt_id, key, {"reviewer_invocation_id": f"review-{key}-{part['part_id']}", "model_id": "model-reviewer" if key == "canonical" else "model-automation-reviewer",
                                                  "host_isolation": isolation, "cli": "fixture", "cli_version": "1", "settings": "synthetic-test-assessment"})
        submit_review_part(run_root, attempt_id, key, part["part_id"], {
            "coverage": [{"scope_id": scope["scope_id"], "status": "CHECKED", "evidence": [item["artifact_digest"] + item["pointer"] for item in scope["inputs"]][:1], "assessment": "Synthetic protocol fixture assessment."} for scope in part["scopes"]],
            "findings": list(findings) if first else [], "corrections": list(corrections) if first else [], "required_checks": [],
        })
        first = False
    return finish_review(run_root, attempt_id, key)


def phase_two_baseline(root: Path, project: Path, identity: dict) -> dict:
    """Fixture-only real Phase 2 proof for tests that create durable attempts."""
    from tools.pilot_state import freeze_phase_two_inputs, read_run

    project = Path(project).resolve()
    module = project / identity["module"]
    identity = {**identity, "policy_profile": read_run(root)["manifest"]["policy_profile"]}
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


def make_junction(link: Path, target: Path, *, run=subprocess.run) -> None:
    """Create a Windows directory junction; console output is never decoded as UTF-8.

    ``cmd.exe`` prints in the OEM code page (cp866 on a Russian system), so
    ``text=True`` raised UnicodeDecodeError before the test could assert anything.
    """
    completed = run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)], check=False, capture_output=True)
    output = (completed.stdout or b"") + (completed.stderr or b"")
    assert completed.returncode == 0, output.decode("utf-8", errors="replace")
