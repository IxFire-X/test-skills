"""Suite migration by format version (wave 3, G).

Every suite step first reads the manifest's ``format_version``; an older suite is brought to the
package's format by deterministic migrators, checked by the schemas, and only then used.  The
migration summary goes into the pull request description.

Format ``0`` is the layout before wave 3: a published bundle of a run (``TCDOC-….rN.json`` with its
``.html``/``.md``/``.zephyr-scale.csv``) copied into the suite directory, no manifest.  The migrator
finds the run that produced it under ``.pilot-runs`` (or takes ``--run``), links the cases to the
run's retained tests exactly like ``local-pilot-v1 --suite`` and keeps the people's version of the
cases: the canonical JSON of the suite is the bundle's document, while the manifest records the
digests the package produced, so a case a person edited is seen as edited and never overwritten.
Bundle files are left in place; the summary lists them.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from tools.suite_manifest import FORMAT_VERSION, MANIFEST, SuiteError, canonical_bytes, case_digest, read_suite, suite_directory, validate, write_suite

_BUNDLE = re.compile(r"^(?P<document>TCDOC-[A-Za-z0-9._-]+?)\.r(?P<revision>[0-9]+)\.json$")


def detect(project: Path, suite_dir: str) -> str | None:
    """``1.0.0`` (or the manifest's version), ``0`` for a bundle without a manifest, None for no suite."""
    manifest = read_suite(project, suite_dir)
    if manifest is not None:
        return str(manifest.get("format_version"))
    directory = Path(project) / suite_dir
    if directory.is_dir() and any(_BUNDLE.match(path.name) for path in directory.iterdir()):
        return "0"
    return None


def _legacy_document(directory: Path) -> tuple[Path, dict[str, Any]]:
    bundles = sorted(((int(match.group("revision")), path) for path in directory.iterdir() if (match := _BUNDLE.match(path.name))), key=lambda row: (row[0], row[1].name))
    if not bundles:
        raise SuiteError("MIGRATION_SOURCE_MISSING", f"{directory} has no TCDOC-*.rN.json bundle")
    path = bundles[-1][1]
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SuiteError("MIGRATION_SOURCE_INVALID", f"{path.name}: {error}") from error


def _source_run(project: Path, document: Mapping[str, Any], run_id: str | None) -> tuple[Path, dict[str, Any]]:
    """The run and attempt whose effective canonical has the bundle's document ID (newest revision first)."""
    from tools.pilot_state import derive_state, read_effective_canonical_if_present

    runs = Path(project) / ".pilot-runs"
    candidates = [runs / run_id] if run_id else sorted(path for path in runs.iterdir() if path.is_dir() and re.fullmatch(r"[0-9a-f]{32}", path.name)) if runs.is_dir() else []
    found = []
    for run_root in candidates:
        try:
            attempts = derive_state(run_root)["attempts"]
        except (OSError, ValueError, KeyError):
            continue
        for attempt in attempts:
            effective = read_effective_canonical_if_present(run_root, attempt["attempt_id"])
            if effective is None or effective["document"].get("document_id") != document.get("document_id"):
                continue
            found.append((int(effective["document"].get("revision") or 0), run_root.name, run_root, dict(attempt)))
    if not found:
        raise SuiteError("MIGRATION_SOURCE_MISSING", f"no run under .pilot-runs produced {document.get('document_id')}; pass --run")
    found.sort(key=lambda row: (row[0], row[1]))
    return found[-1][2], found[-1][3]


def record_migration(project: Path, migration: Mapping[str, Any], *, authorization: str) -> Path:
    """The receipt of a written migration (who authorized it, from which run, which manifest) beside the runs."""
    import time

    from tools.suite_manifest import sha256_bytes

    directory = Path(project) / ".pilot-runs" / "suite-migrations"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = Path(project) / str(migration.get("manifest") or "")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = directory / f"{stamp}-{str(migration.get('run_id') or 'run')[:8]}.json"
    receipt = {"schema_version": "1.0.0", "authorization": authorization, "migration": dict(migration),
               "manifest_sha256": sha256_bytes(manifest.read_bytes()) if manifest.is_file() else None}
    path.write_bytes(canonical_bytes(receipt))
    return path


def migrate(project: Path, *, run_id: str | None = None, write: bool = True) -> dict[str, Any]:
    """Bring the project's suite to ``FORMAT_VERSION``; returns the migration summary."""
    from tools.pipeline_driver_suite import _skillsrc, attempt_suite
    from tools.suite_manifest import projections, sha256_bytes

    project = Path(project)
    suite_dir = suite_directory(project, _skillsrc(project))
    version = detect(project, suite_dir)
    if version is None:
        raise SuiteError("SUITE_MISSING", f"{suite_dir} holds no suite")
    if version == FORMAT_VERSION:
        manifest = read_suite(project, suite_dir)
        problems = validate(manifest)
        if problems:
            raise SuiteError("SUITE_MANIFEST_INVALID", "; ".join(problems[:5]))
        return {"status": "CURRENT", "from_format": version, "to_format": FORMAT_VERSION, "suite_dir": suite_dir, "steps": []}
    if version != "0":
        raise SuiteError("MIGRATION_UNKNOWN_FORMAT", f"no migrator from format {version} to {FORMAT_VERSION}")
    legacy_path, legacy = _legacy_document(project / suite_dir)
    run_root, attempt = _source_run(project, legacy, run_id)
    _dir, manifest, _payloads = attempt_suite(project, run_root, attempt, migration=True)
    produced = {case["case_id"]: case for case in manifest["cases"]}
    people = {case.get("case_id"): case for case in legacy.get("test_cases") or [] if isinstance(case, Mapping)}
    try:
        rendered = projections(legacy)
    except ValueError as error:  # CanonicalDocumentError: the bundle is no longer a valid canonical document
        raise SuiteError("MIGRATION_SOURCE_INVALID", f"{legacy_path.name}: {error}") from error
    manifest = {**manifest,
                "suite_files": {name: {"path": row["path"], "sha256": sha256_bytes(rendered[name])} for name, row in manifest["suite_files"].items()},
                "history": [{"run_id": run_root.name, "profile": "local-pilot-v1", "action": "CREATED"},
                            {"run_id": run_root.name, "profile": "migration", "action": "MIGRATED", "from_format": "0"}]}
    problems = validate(manifest)
    if problems:
        raise SuiteError("SUITE_MANIFEST_INVALID", "; ".join(problems[:5]))
    summary = {
        "status": "MIGRATED", "from_format": "0", "to_format": FORMAT_VERSION, "suite_dir": suite_dir, "source": legacy_path.name,
        "run_id": run_root.name, "attempt_id": attempt["attempt_id"],
        "steps": ["0->1.0.0: manifest from the run's retained tests; the bundle's document kept as the suite's canonical JSON"],
        "cases": len(manifest["cases"]), "files": len(manifest["files"]),
        "cases_edited_by_people": sorted(case_id for case_id, case in people.items() if case_id in produced and case_digest(case) != produced[case_id]["case_digest"]),
        "cases_added_by_people": sorted(case_id for case_id in people if case_id not in produced),
        "cases_missing_from_bundle": sorted(case_id for case_id in produced if case_id not in people),
        "legacy_files": sorted(path.name for path in (project / suite_dir).iterdir() if _BUNDLE.match(path.name) or path.name.startswith(legacy_path.name.rsplit(".json", 1)[0])),
    }
    scanned = {row["path"]: row["sha256"] for row in manifest["documents"]}
    try:
        from tools.run_pipeline import _docs_entries

        current = {entry["path"]: entry["sha256"] for entry in _docs_entries(project, sorted(scanned))}
    except (OSError, ValueError, RuntimeError):
        current = {}
    summary["requirement_keys_from"] = "scan" if current == scanned else "run_record"
    if write:
        write_suite(project, suite_dir, {**rendered, "manifest": canonical_bytes(manifest)})
    summary["manifest"] = suite_dir + MANIFEST
    return summary
