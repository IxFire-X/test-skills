"""``python -m tools.suite status|impact|migrate`` — the living suite without a model (wave 3).

* ``status`` — the suite's format, cases by status, test files, what people edited since the
  package wrote it (``suite_manifest.verify``), and whether a migration is needed;
* ``impact`` — what changed against the manifest: requirements by key, affected cases, failed and
  broken tests of a JUnit report, new endpoints without a requirement (``tools.suite_impact``);
* ``migrate`` — bring an older suite to the package's format (``tools.suite_migrate``).

A project's CI can call ``status``/``impact`` to decide whether a model run is needed at all.
Output is one JSON object; exit code 0, or 2 with ``{"status": "error", "code": …}``.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tools.suite_manifest import FORMAT_VERSION, SuiteError, read_suite, suite_directory, validate, verify


def status(project: Path) -> dict[str, Any]:
    from tools.pipeline_driver_suite import _skillsrc
    from tools.suite_migrate import detect

    project = Path(project)
    suite_dir = suite_directory(project, _skillsrc(project))
    version = detect(project, suite_dir)
    if version is None:
        return {"status": "ok", "suite": None, "suite_dir": suite_dir, "migration_needed": False}
    if version != FORMAT_VERSION:
        return {"status": "ok", "suite": {"format_version": version}, "suite_dir": suite_dir, "migration_needed": True}
    manifest = read_suite(project, suite_dir)
    problems = validate(manifest)
    if problems:
        raise SuiteError("SUITE_MANIFEST_INVALID", "; ".join(problems[:5]))
    counts: dict[str, int] = {}
    for case in manifest["cases"]:
        counts[case["status"]] = counts.get(case["status"], 0) + 1
    return {"status": "ok", "suite_dir": suite_dir, "migration_needed": False,
            "suite": {"format_version": manifest["format_version"], "suite_id": manifest["suite_id"], "source_run": manifest["source_run"]["run_id"],
                      "last_run": manifest["last_run"]["run_id"], "cases": len(manifest["cases"]), "by_status": dict(sorted(counts.items())),
                      "automated": sum(case["automation"] == "AUTOMATED" for case in manifest["cases"]), "files": [row["path"] for row in manifest["files"]],
                      "quarantined": [{"case_id": case["case_id"], **case["quarantine"]} for case in manifest["cases"] if case["quarantine"]]},
            "edited": verify(project, manifest)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Living suite: status, impact analysis and migration without a model.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "impact", "migrate"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=True)
        if name == "impact":
            command.add_argument("--docs", action="append", default=None, help="Requirement documents (default: the suite's documents).")
            command.add_argument("--junit", action="append", default=[], help="JUnit XML report(s) of the project's own CI run.")
            command.add_argument("--git-range", help="Changed files between two revisions (BASE..HEAD) narrow the code under tests.")
        if name == "migrate":
            command.add_argument("--run", help="Run that produced a format-0 bundle (default: found under .pilot-runs).")
            command.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    project = Path(args.project).resolve()
    try:
        if args.command == "status":
            payload = status(project)
        elif args.command == "migrate":
            from tools.suite_migrate import migrate

            payload = {"status": "ok", "migration": migrate(project, run_id=args.run, write=not args.dry_run)}
        else:
            from tools.suite_impact import impact

            payload = {"status": "ok", **impact(project, docs=args.docs, junit=args.junit, git_range=args.git_range)}
    except SuiteError as error:
        print(json.dumps({"status": "error", "code": error.code, "message": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
