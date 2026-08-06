"""Report portable skill-pack runtime capabilities as deterministic JSON."""

import argparse
import importlib.util
import json
import platform
import sys
from pathlib import Path


def inspect_environment(root: Path) -> dict[str, object]:
    """Return support and required-dependency availability for a skill pack."""
    dependencies = {
        name: {"required": True, "available": importlib.util.find_spec(name) is not None}
        for name in ("jsonschema", "yaml")
    }
    ready = all(item["available"] for item in dependencies.values())
    return {
        "status": "PASS" if ready else "NOT_RUNNABLE",
        "python": {
            "supported": sys.version_info >= (3, 10),
            "version": platform.python_version(),
        },
        "dependencies": dependencies,
        "languages": {
            "java": {"execution": True},
            "python": {"execution": True},
            "typescript": {"execution": False},
            "go": {"execution": False},
        },
        "root": str(root.resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report portable skill-pack runtime readiness as JSON."
    )
    parser.add_argument("--root", type=Path, required=True, help="Path to the skill pack")
    args = parser.parse_args()
    print(json.dumps(inspect_environment(args.root), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
