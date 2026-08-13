"""Report portable skill-pack runtime capabilities as deterministic JSON."""

import importlib.util
import json
import platform
import sys
from pathlib import Path

if __package__:
    from .json_cli import JsonArgumentParser
else:  # direct CLI execution
    from json_cli import JsonArgumentParser


def inspect_environment(root: Path) -> dict[str, object]:
    """Return support and required-dependency availability for a skill pack."""
    root = root.resolve()
    required_files = (
        "contracts/pipeline.json",
        "schemas/test-symbol-registry.schema.json",
        "schemas/source-inventory-output.schema.json",
        "schemas/test-classifier-output.schema.json",
        "schemas/test-classifier-reviewer-output.schema.json",
        "schemas/effective-technical-evidence.schema.json",
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
        "tools/orchestrate_test_case_revision.py",
        "tools/build_trace_document.py",
        "tools/run_tests.py",
        "tools/scan_project.py",
        "tools/skillsrc_manifest.py",
        "tools/discover_project.py",
        "tools/init_skillsrc.py",
        "tools/test_classification.py",
        "skills/test-classifier/SKILL.md",
        "skills/test-classifier-reviewer/SKILL.md",
    )
    required_dirs = ("schemas", "tools", "contracts")
    missing_integrity = [item for item in required_files if not (root / item).is_file()]
    missing_integrity.extend(item for item in required_dirs if not (root / item).is_dir())
    if not root.is_dir():
        missing_integrity.insert(0, "root")
    for relative, expected in (
        ("contracts/pipeline.json", "pipeline"),
        ("schemas/orchestrator-output.schema.json", "schemas/orchestrator-output.schema.json"),
        ("schemas/test-symbol-registry.schema.json", "schemas/test-symbol-registry.schema.json"),
        ("schemas/source-inventory-output.schema.json", "schemas/source-inventory-output.schema.json"),
        ("schemas/test-classifier-output.schema.json", "schemas/test-classifier-output.schema.json"),
        ("schemas/test-classifier-reviewer-output.schema.json", "schemas/test-classifier-reviewer-output.schema.json"),
        ("schemas/effective-technical-evidence.schema.json", "schemas/effective-technical-evidence.schema.json"),
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
                valid = data.get("$schema") == "schemas/pipeline.schema.json" and data.get("version") == "5.0" and data.get("pipeline") == "test-pipeline" and isinstance(data.get("steps"), list)
            else:
                valid = data.get("$id") == expected and data.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
            if not valid:
                missing_integrity.append(f"invalid:{relative}")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            missing_integrity.append(f"invalid:{relative}")
    pipeline_path = root / "contracts" / "pipeline.json"
    if pipeline_path.is_file():
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
    dependencies = {
        name: {"required": True, "available": importlib.util.find_spec(name) is not None}
        for name in ("jsonschema", "yaml")
    }
    python_supported = sys.version_info >= (3, 10)
    ready = not missing_integrity and python_supported and all(item["available"] for item in dependencies.values())
    return {
        "status": "PASS" if ready else "NOT_RUNNABLE",
        "python": {
            "supported": python_supported,
            "version": platform.python_version(),
        },
        "dependencies": dependencies,
        "integrity": {"valid": not missing_integrity, "missing": missing_integrity},
        "languages": {
            "java": {"execution": True},
            "python": {"execution": True},
            "typescript": {"execution": False},
            "go": {"execution": False},
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
    args = parser.parse_args()
    report = inspect_environment(args.root)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
