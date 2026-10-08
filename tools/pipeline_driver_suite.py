"""``local-pilot-v1 --suite``: write the living suite after the terminal result (wave 3, S).

The suite is written only when the run consented (``suite_requested`` in the run-scoped
authorization) and every generated test file stayed in the project (``RETAINED``, or
``QUARANTINED`` under the quarantine policy).  The attempt is not changed: the step reads its
receipts and writes the suite directory and a receipt in the driver directory.  Calling it
again gives the same bytes; a suite of another run is never overwritten (that is the job of
``suite-update-v1``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from tools import pipeline_driver as driver
from tools.suite_manifest import SuiteError, build_manifest, canonical_bytes, projections, read_suite, sha256_bytes, suite_directory, write_suite

_PACK_ROOT = Path(__file__).resolve().parents[1]
_KEPT = {"RETAINED", "QUARANTINED"}


def package_version() -> str:
    try:
        return str(json.loads((_PACK_ROOT / "release" / "manifest.json").read_text(encoding="utf-8"))["package_version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


def _label(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-") or "suite"


def _skillsrc(project: Path) -> dict[str, Any]:
    from tools.skillsrc_manifest import load_skillsrc

    path = project / ".skillsrc"
    return dict(load_skillsrc(path)) if path.is_file() else {}


def _module(skillsrc: Mapping[str, Any], module_id: str | None) -> dict[str, Any]:
    modules = [dict(row) for row in skillsrc.get("modules") or []]
    return next((row for row in modules if row.get("id") == module_id), modules[0] if len(modules) == 1 else {"id": module_id or "root", "root": "."})


def _case_state(run_id: str, report: Mapping[str, Any], automation: Mapping[str, Any], dispositions: Mapping[str, str],
                plan: Mapping[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Per case: ACTIVE with this run as the last green one when every one of its methods passed;
    QUARANTINED with reason and reference when the quarantine policy marked one of its methods (A5.2)."""
    artifacts = automation.get("artifacts") or {}
    status_of = {row.get("symbol_id"): row.get("status") for row in report.get("execution_evidence") or []}
    file_of = {row["symbol_id"]: row["file_id"] for row in artifacts.get("generated_symbols") or []}
    methods: dict[str, set[str]] = {}
    for relation in artifacts.get("implementation_relations") or []:
        methods.setdefault(relation["case_id"], set()).add(relation["symbol_id"])
    state: dict[str, dict[str, Any]] = {}
    for case_id, symbols in methods.items():
        green = all(status_of.get(symbol) == "PASSED" for symbol in symbols) and all(dispositions.get(file_of.get(symbol)) in _KEPT for symbol in symbols)
        state[case_id] = {"status": "ACTIVE", "quarantine": None, "last_green_run": run_id if green else None}
    marked = {symbol for row in (plan or {}).get("files") or [] if row.get("operation") == "QUARANTINE_IF_EXACT"
              and dispositions.get(row.get("file_id")) == "QUARANTINED" for symbol in row.get("quarantine_symbols") or []}
    for case_id, symbols in methods.items():
        for symbol in sorted(symbols & marked):
            # The reference is the text of the method's mark (generated_delta.quarantined_bytes).
            reason = "ASSERTION_FAILED" if status_of.get(symbol) == "FAILED" else "TEST_ERROR"
            state[case_id] = {"status": "QUARANTINED", "last_green_run": None,
                              "quarantine": {"reason": reason, "ref": f"run {run_id[:8]} {symbol}", "since_run": run_id}}
            break
    return state


def _strength(run_root: Path, attempt_id: str, run_id: str) -> dict[str, dict[str, Any]]:
    from tools.pilot_state import read_mutation_receipt_if_present

    receipt = read_mutation_receipt_if_present(run_root, attempt_id) or {}
    payload = receipt.get("payload") if isinstance(receipt.get("payload"), dict) else receipt
    if payload.get("status") != "MEASURED":
        return {}
    return {row["case_id"]: {"covered": int(row["covered"]), "killed": int(row["killed"]), "run_id": run_id} for row in payload.get("cases") or []}


def attempt_suite(project: Path, run_root: Path, attempt: Mapping[str, Any], *, migration: bool = False) -> tuple[str, dict[str, Any], dict[str, bytes]]:
    """``(suite_dir, manifest, payloads)`` of a terminal local attempt; raises ``SuiteError`` with the reason it cannot.

    The requirement keys come from a scan of the run's documents; when they changed since the run
    (only allowed for a ``migration``), from the run's own record of its source requirements.
    """
    from tools import pilot_state, run_pipeline
    from tools.code_surface import surface
    from tools.requirement_identity import identify, identities_from_record

    attempt_id = str(attempt["attempt_id"])
    run_id = run_root.name
    config = driver._config(run_root)
    skillsrc = _skillsrc(project)
    suite_dir = suite_directory(project, skillsrc)
    id_pattern = (skillsrc.get("requirements") or {}).get("id_pattern")
    terminal = dict(pilot_state.read_terminal_result(run_root, attempt_id))
    try:
        disposition = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    except (ValueError, KeyError, OSError) as error:
        raise SuiteError("SUITE_NO_TESTS", "the attempt has no disposition receipt (no generated test stayed in the project)") from error
    rows = list(disposition.get("files") or [])
    dispositions = {row["file_id"]: row["disposition"] for row in rows}
    if not rows or any(row["disposition"] not in _KEPT for row in rows):
        raise SuiteError("SUITE_TESTS_NOT_RETAINED", "every generated test file must stay in the project: "
                         + ", ".join(f"{row['path']} {row['disposition']}" for row in rows))
    existing = read_suite(project, suite_dir)
    if existing is not None and not migration and existing.get("source_run", {}).get("run_id") != run_id:
        raise SuiteError("SUITE_EXISTS", f"{suite_dir} already holds the suite of run {existing.get('source_run', {}).get('run_id')}; update it with suite-update-v1")
    effective = pilot_state.read_effective_canonical(run_root, attempt_id)
    document = dict(effective["document"])
    recorded_docs: dict[str, str] = {}
    for item in document.get("source_requirements") or []:
        marks = [str(mark) for mark in item.get("provenance") or []]
        if marks:
            recorded_docs.setdefault(marks[0].split(" — ", 1)[0].split(":", 1)[0], str(item.get("digest")))
    try:
        entries = run_pipeline._docs_entries(project, list(config.get("docs") or []))
    except (OSError, ValueError, RuntimeError):
        entries = []
    changed = not entries or any(entry["sha256"] not in set(recorded_docs.values()) for entry in entries)
    expected = [(item["source_requirement_id"], item["text"]) for item in document.get("source_requirements") or []]
    if changed and not migration:
        raise SuiteError("SUITE_DOCS_CHANGED", "a requirement document changed after the run; start a new run")
    if changed:
        identities = identities_from_record(document.get("source_requirements") or [], id_pattern)
        documents = [{"path": path, "sha256": digest} for path, digest in sorted(recorded_docs.items())]
    else:
        identities = identify(entries, id_pattern)
        documents = [{"path": entry["path"], "sha256": entry["sha256"]} for entry in entries]
        if [(row["source_requirement_id"], row["text"]) for row in identities] != expected:
            raise SuiteError("SUITE_REQUIREMENTS_MISMATCH", "the requirement scan does not reproduce the run's source requirements")
    automation = pilot_state.read_execution_inputs(run_root, attempt_id)["automation_artifact"]
    report = pilot_state.read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    plan = pilot_state.read_attempt_receipt(run_root, attempt_id, "disposition-plan", "ARTIFACT_READ_BACK")["record"]["payload"]
    test_files = _left_bytes(project, run_id, rows, {row["file_id"]: row for row in plan.get("files") or []}, automation, report)
    module = _module(skillsrc, config.get("module_id"))
    module_root = project / str(module.get("root") or ".")
    sources = list(((module.get("paths") or {}).get("source")) or ["src/main/java", "src", "app"])
    rendered = projections(document)
    source_run = {"run_id": run_id, "attempt_id": attempt_id, "profile": "local-pilot-v1",
                  "verification": terminal.get("verification"), "accepted": terminal.get("accepted")}
    manifest = build_manifest(
        suite_id=f"SUITE-{_label(project.name)}-{_label(str(module.get('id') or 'root'))}", module_id=str(module.get("id") or "root"),
        package_version=package_version(), document=document, identities=identities,
        documents=documents, id_pattern=id_pattern, automation=automation,
        test_files=test_files, case_state=_case_state(run_id, report, automation, dispositions, plan), strength=_strength(run_root, attempt_id, run_id),
        surface=surface(module_root, sources), suite_dir=suite_dir, suite_digests={name: sha256_bytes(data) for name, data in rendered.items()},
        source_run=source_run, history=[{"run_id": run_id, "profile": "local-pilot-v1", "action": "CREATED"}])
    _ask_about_quarantine(run_root, attempt_id, manifest, document)
    return suite_dir, manifest, {**rendered, "manifest": canonical_bytes(manifest)}


def _left_bytes(project: Path, run_id: str, rows: list[Mapping[str, Any]], plan: Mapping[str, Mapping[str, Any]], automation: Mapping[str, Any],
                report: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """The bytes the package left in each test file, rebuilt from the attempt's receipts (review 2.1 item 6).

    The manifest digests describe these bytes, never the file on disk: a person who edits the file
    between the run and the suite (or the migration) stays the author of that edit, and
    ``suite_manifest.verify`` reports it.  A RETAINED file is the generated content; a QUARANTINED
    one is that content with its failed methods marked, checked against the plan's digest.
    """
    from tools.generated_delta import GeneratedDeltaError, quarantined_bytes

    artifacts = automation.get("artifacts") or {}
    generated = {item["file_id"]: item for item in artifacts.get("generated_files") or []}
    symbols = list(artifacts.get("generated_symbols") or [])
    failed_of: dict[str, dict[str, str]] = {}
    for row in report.get("execution_evidence") or []:
        if row.get("status") in {"FAILED", "ERROR"}:
            failed_of.setdefault(str(row.get("file_id")), {})[str(row.get("symbol_id"))] = str(row["status"])
    out = {}
    for row in rows:
        if not (project / row["path"]).is_file():
            raise SuiteError("SUITE_TEST_FILE_MISSING", f"{row['path']} is not in the project")
        source = generated.get(row["file_id"])
        content = str((source or {}).get("content", "")).encode("utf-8")
        if source is None or sha256_bytes(content) != row.get("content_digest"):
            raise SuiteError("SUITE_RECEIPT_MISMATCH", f"{row['path']}: the generated content does not match the disposition receipt")
        if row["disposition"] == "QUARANTINED":
            plan_row = plan.get(row["file_id"]) or {}
            failed = {symbol: failed_of.get(row["file_id"], {}).get(symbol, "FAILED") for symbol in plan_row.get("quarantine_symbols") or []}
            try:
                content = quarantined_bytes(content, str(row["path"]), failed, symbols, run_id)
            except GeneratedDeltaError as error:
                raise SuiteError("SUITE_RECEIPT_MISMATCH", f"{row['path']}: {error}") from error
            if sha256_bytes(content) != plan_row.get("quarantined_content_digest"):
                raise SuiteError("SUITE_RECEIPT_MISMATCH", f"{row['path']}: the quarantined content does not match the disposition plan")
        out[row["file_id"]] = {"path": row["path"], "content": content.decode("utf-8")}
    return out


def quarantine_failures(run_root: Path, attempt_id: str, manifest: Mapping[str, Any]) -> dict[str, str | None]:
    """The failure text of each quarantined case, from the runner output the attempt kept.

    The durable JUnit copies keep only outcomes (no messages), so the text comes from the
    redacted ``runner-output.txt``: the lines after the last mention of the method's name
    (Surefire's ``Failures:`` summary, Gradle's ``FAILED`` block).
    """
    path = run_root / "artifacts" / attempt_id / "runner-output.txt"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path.is_file() else []
    except OSError:
        lines = []
    out: dict[str, str | None] = {}
    for case in manifest["cases"]:
        if case["status"] != "QUARANTINED":
            continue
        text = None
        for method in case["methods"]:
            name = method["locator"].rsplit("#", 1)[-1].rsplit(".", 1)[-1]
            hits = [index for index, line in enumerate(lines) if name in line and ("ERROR" in line or "FAILED" in line or "Failure" in line or "»" in line or "expected" in line.lower())]
            if not hits:
                continue
            block = [lines[hits[-1]]]
            for line in lines[hits[-1] + 1:hits[-1] + 12]:
                if line.startswith("[INFO]") or line.startswith("[ERROR]   ") or line.startswith("[ERROR] Tests run") or not line.strip():
                    break
                block.append(line)
            text = " ".join(" ".join(block).split())[:1500]
            break
        out[case["case_id"]] = text
    return out


def _ask_about_quarantine(run_root: Path, attempt_id: str, manifest: dict[str, Any], document: Mapping[str, Any]) -> None:
    """A question for the analysts on every quarantined case, with the failure the run observed."""
    from tools.suite_failures import analyst_question

    cases = {case["case_id"]: case for case in document.get("test_cases") or []}
    for case_id, failure in quarantine_failures(run_root, attempt_id, manifest).items():
        row = next(case for case in manifest["cases"] if case["case_id"] == case_id)
        row["quarantine"]["question"] = analyst_question(cases.get(case_id, {"case_id": case_id, "title": row.get("title")}), failure=failure, first_run=True)[:2000]


def quarantine_report(manifest: Mapping[str, Any], document: Mapping[str, Any], run_id: str, failures: Mapping[str, str | None]) -> str | None:
    """``quarantine.md``: the analysts' questions and a bug report draft per quarantined case."""
    from tools.suite_failures import bug_report

    quarantined = [case for case in manifest["cases"] if case["status"] == "QUARANTINED"]
    if not quarantined:
        return None
    cases = {case["case_id"]: case for case in document.get("test_cases") or []}
    lines = ["# Карантин после прогона", "", f"Прогон `{run_id}`: упавшие тесты отключены пометкой карантина, остальные тесты набора сохранены.",
             "Карантинный тест запускается явно и снимается, когда продукт снова ведёт себя по требованию.", "",
             "| Кейс | Тест | Причина | Ссылка |", "| --- | --- | --- | --- |"]
    lines += [f"| {case['case_id']} | `{method['locator']}` | {case['quarantine']['reason']} | {case['quarantine']['ref']} |"
              for case in quarantined for method in case["methods"]]
    lines += ["", "## Вопросы аналитикам", ""] + [f"- {case['quarantine']['question']}" for case in quarantined if case["quarantine"].get("question")] + [""]
    for case in quarantined:
        locator = case["methods"][0]["locator"] if case["methods"] else case["case_id"]
        lines += [bug_report(cases.get(case["case_id"], {"case_id": case["case_id"]}), locator=locator, failure=failures.get(case["case_id"]), run_id=run_id, first_run=True), ""]
    return "\n".join(lines)


def suite_summary(run_root: Path, attempt: Mapping[str, Any], summary: dict[str, Any]) -> None:
    """Write the suite when the run asked for it (summary 1.2.0); every stop is a status with a reason."""
    from tools.pilot_state import read_run

    if read_run(run_root)["authorization"].get("suite_requested") is not True:
        return
    project = Path(attempt["project"])
    summary["schema_version"] = "1.2.0"
    try:
        suite_dir, manifest, payloads = attempt_suite(project, run_root, attempt)
        written = write_suite(project, suite_dir, payloads)
    except SuiteError as error:
        summary["suite"] = {"status": "NOT_WRITTEN", "reason_code": error.code, "message": str(error), "cases": 0, "files": 0}
        return
    receipt = {"schema_version": "1.0.0", "run_id": run_root.name, "attempt_id": str(attempt["attempt_id"]), "suite_dir": suite_dir, "written": written}
    directory = driver.work_dir(run_root) / "suite"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "suite-receipt.json").write_bytes(canonical_bytes(receipt))
    summary["suite"] = {"status": "WRITTEN", "reason_code": None, "message": None, "cases": len(manifest["cases"]), "files": len(manifest["files"])}
    summary["paths"]["suite_dir"] = str(project / suite_dir)
    summary["paths"]["suite_manifest"] = str(project / suite_dir / "suite-manifest.json")
    quarantined = sum(case["status"] == "QUARANTINED" for case in manifest["cases"])
    if quarantined:
        from tools.pilot_state import read_effective_canonical

        attempt_id = str(attempt["attempt_id"])
        document = read_effective_canonical(run_root, attempt_id)["document"]
        text = quarantine_report(manifest, document, run_root.name, quarantine_failures(run_root, attempt_id, manifest))
        path = driver._bundle_dir(run_root, attempt) / "quarantine.md"
        driver._write_text(path, text)
        summary["suite"]["quarantined"] = quarantined
        summary["paths"]["quarantine_markdown"] = str(path)
