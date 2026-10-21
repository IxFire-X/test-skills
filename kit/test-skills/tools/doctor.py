"""Report portable skill-pack integrity and unqualified runtime capabilities.

The delivery profile (``universal`` or ``host``, see ``tools.release_manifest``) is read from the copy itself: its runtime
files must be exactly one profile's registry.  The host copy carries no maintainer files (``evals/``): the doctor says
they are not delivered instead of missing.
"""

import importlib.util
import json
import platform
import sys
from pathlib import Path

if __package__:
    from .json_cli import JsonArgumentParser
else:  # direct CLI execution
    from json_cli import JsonArgumentParser


MAINTAINER_FILES = ("evals/release_eval.py", "evals/scenarios/pilot-critical.json")


def inspect_environment(root: Path, profile: str | None = None) -> dict[str, object]:
    """Return support and required-dependency availability for a skill pack (``profile``: the one it must be)."""
    root = root.resolve()
    qualification: dict[str, object] = {"state": "unavailable", "ready_tuple": None}
    delivery_profile: str | None = None
    required_files = (
        "contracts/pipeline.json",
        "schemas/canonical-test-document.schema.json",
        "schemas/tc-reviewer-output.schema.json",
        "schemas/tc-to-autotest-output.schema.json",
        "schemas/autotest-reviewer-output.schema.json",
        "schemas/run-tests-output.schema.json",
        "schemas/trace-document.schema.json",
        "schemas/orchestrator-output.schema.json",
        "schemas/skillsrc.schema.json",
        "schemas/project-discovery-output.schema.json",
        "schemas/skillsrc-init-output.schema.json",
        "schemas/compatibility-evidence.schema.json",
        "schemas/scenario-observation-receipt.schema.json",
        "schemas/release-eval-run.schema.json",
        "schemas/release-eval-receipt.schema.json",
        "schemas/release-manifest.schema.json",
        "release/manifest.json",
        "tools/orchestrate_test_case_revision.py",
        "tools/build_trace_document.py",
        "tools/run_tests.py",
        "tools/execution_adapters.py",
        "tools/scan_project.py",
        "tools/skillsrc_manifest.py",
        "tools/discover_project.py",
        "tools/init_skillsrc.py",
        "tools/compatibility_contract.py",
        "tools/release_manifest.py",
    )
    required_dirs = ("schemas", "tools", "contracts")
    missing_integrity = [item for item in required_files if not (root / item).is_file()]
    missing_integrity.extend(item for item in required_dirs if not (root / item).is_dir())
    if not root.is_dir():
        missing_integrity.insert(0, "root")
    for relative, expected in (
        ("contracts/pipeline.json", "pipeline"),
        ("schemas/orchestrator-output.schema.json", "schemas/orchestrator-output.schema.json"),
        ("schemas/tc-to-autotest-output.schema.json", "schemas/tc-to-autotest-output.schema.json"),
        ("schemas/skillsrc.schema.json", "schemas/skillsrc.schema.json"),
        ("schemas/project-discovery-output.schema.json", "schemas/project-discovery-output.schema.json"),
        ("schemas/skillsrc-init-output.schema.json", "schemas/skillsrc-init-output.schema.json"),
    ):
        candidate = root / relative
        if not candidate.is_file():
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if expected == "pipeline":
                valid = (
                    data.get("$schema") == "schemas/pipeline.schema.json"
                    and data.get("version") == "4.0"
                    and data.get("pipeline") == "portable-testing-skills-frozen-pilot"
                    and isinstance(data.get("artifact_registry"), list)
                )
            else:
                valid = data.get("$id") == expected and data.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
            if not valid:
                missing_integrity.append(f"invalid:{relative}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            missing_integrity.append(f"invalid:{relative}")
    dependencies = {
        name: {"required": True, "available": importlib.util.find_spec(name) is not None}
        for name in ("jsonschema", "yaml")
    }
    missing_modules = [name for name, item in dependencies.items() if not item["available"]]
    # The contract and manifest checks import jsonschema: without it they say nothing about the pack itself.
    messages = [
        f"модуль {name} не установлен в {sys.executable}: запустите через интерпретатор пакета (.venv) "
        "или установите зависимости из requirements.txt; проверка контракта и манифеста пропущена"
        for name in missing_modules
    ]
    pipeline_path = root / "contracts" / "pipeline.json"
    if pipeline_path.is_file() and not missing_modules:
        try:
            if __package__:
                from .contract_check import validate_pipeline_contract
            else:  # direct CLI execution
                from contract_check import validate_pipeline_contract
            contract_report = validate_pipeline_contract(json.loads(pipeline_path.read_text(encoding="utf-8")), root, check_drift=False)
            if contract_report["status"] != "passed":
                missing_integrity.append("invalid:pipeline_contract")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ImportError):
            missing_integrity.append("invalid:pipeline_contract")
    release_manifest_path = root / "release" / "manifest.json"
    if release_manifest_path.is_file() and not missing_modules:
        try:
            if __package__:
                from .release_manifest import delivery_profile_of, load_release_manifest
            else:  # direct CLI execution
                from release_manifest import delivery_profile_of, load_release_manifest
            manifest = load_release_manifest(root)
            qualification = dict(manifest["qualification"])
            delivery_profile = delivery_profile_of(root, manifest)
        except (ImportError, TypeError, ValueError) as error:
            missing_integrity.append("invalid:release/manifest.json")
            messages.append(f"release/manifest.json: {error}")
    if delivery_profile == "universal":
        missing_integrity.extend(item for item in MAINTAINER_FILES if not (root / item).is_file())
    if profile is not None and delivery_profile != profile:
        missing_integrity.append(f"profile:{profile}")
        messages.append(f"ожидался профиль поставки {profile}, копия — {delivery_profile or 'не определён'}")
    python_supported = sys.version_info >= (3, 11)
    ready = not missing_integrity and python_supported and all(item["available"] for item in dependencies.values())
    return {
        "status": "PASS" if ready else "NOT_RUNNABLE",
        "status_scope": "pack_integrity",
        "qualification": qualification,
        "python": {
            "supported": python_supported,
            "version": platform.python_version(),
        },
        "dependencies": dependencies,
        "messages": messages,
        "integrity": {"valid": not missing_integrity, "missing": missing_integrity},
        "delivery_profile": delivery_profile,
        # The release evaluation is a maintainer tool: the host delivery leaves it out on purpose.
        "maintainer_tools": "not_delivered" if delivery_profile == "host" else "present" if delivery_profile == "universal" else None,
        "languages": {
            "java": {"execution": True, "verified": False},
            "python": {"execution": True, "verified": False},
            "typescript": {"execution": False, "verified": False},
            "go": {"execution": False, "verified": False},
        },
        "adapters": {
            "local_project_native": {"core": True, "verified": False},
            "company_runner": {"core": False, "verified": False, "delivered": (root / "tools" / "company_runner.py").is_file()},
        },
        "root": str(root.resolve()),
    }


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(
        description="Report portable skill-pack runtime readiness as JSON."
    )
    parser.add_argument("--root", type=Path, required=True, help="Path to the skill pack")
    parser.add_argument("--profile", choices=("universal", "host"), help="The delivery profile the copy must be (default: whichever it is).")
    args = parser.parse_args()
    report = inspect_environment(args.root, args.profile)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
