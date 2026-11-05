"""FaT-IMP3 (import, stage 2): ``scan --cases`` with ``--docs`` — the model links the ready cases of the CSV to the source
requirements of the documents on the requirements step; the trace goes through the documents, the human fields and the
Zephyr management of a case stay byte for byte; a document requirement no case links is a gap for the analyst, a case
linked to none gets a requirement from code («вне документов»)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_cases_import import CSV, _generator_request, _project, _scan

PACK_ROOT = Path(__file__).resolve().parents[1]  # V0 (A6-3): the package, not the directory the run started in

DOC = "# Сумма\n\nR01. combine склеивает строки через двоеточие.\nR02. combine отказывает на None.\nR03. Результат — строка.\n"


def _docs_project(tmp_path: Path) -> Path:
    project = _project(tmp_path, CSV)
    (project / "docs" / "req.md").write_bytes(DOC.encode("utf-8"))
    return project


def _requirements() -> list[dict]:
    digest = "sha256:" + hashlib.sha256(DOC.encode("utf-8")).hexdigest()
    return [{"source_requirement_id": f"SREQ-000{n}", "display_order": n, "text": text, "provenance": [f"docs/req.md:{n + 2}"], "digest": digest}
            for n, text in enumerate(["combine склеивает строки через двоеточие.", "combine отказывает на None.", "Результат — строка."], start=1)]


LINKS = [{"row": 2, "source_requirement_ids": ["SREQ-0001", "SREQ-0003"]},
         {"row": 3, "source_requirement_ids": [], "reason": "пустая правая часть в документах не описана"}]


def _answer(links=LINKS) -> dict:
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    return {"schema_version": "5.0.0", "stage": "context-marker",
            "artifacts": {"analytics_documentation": {"requirements": _requirements(), **({"case_links": links} if links is not None else {})},
                          "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}},
            "warnings": []}


def _scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    project = _docs_project(tmp_path)
    code, scan = _scan(project, monkeypatch, docs=["docs/req.md"])
    assert code == 0, scan
    return project, Path(scan["run_root"]), scan["attempt_id"]


def _request_requirements(root: Path, attempt_id: str) -> None:
    from tools import context_marker_parts, pilot_state

    [step] = [row for row in context_marker_parts.plan(root)["parts"] if row["stage"] == "context-marker:requirements"]
    assert "by_code" not in step, step  # with --docs the model answers the requirements step
    pilot_state.publish_model_request(root, attempt_id, "context-marker:requirements", model_id="model-context",
                                      invocation_id="context-req", input_digests=step["input_digests"])


def _responses(root: Path) -> list[str]:
    from tools.pilot_state import derive_state

    return [str(event.get("stage_instance_id")) for event in derive_state(root)["events"] if event["event_type"] == "MODEL_RESPONSE_RECEIVED"]


def test_scan_cases_with_docs_makes_a_run_whose_requirements_step_reads_the_documents_and_the_csv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.step_size import journal_files

    project, root, _attempt = _scanned(tmp_path, monkeypatch)
    files = journal_files(project, root.name, "context-marker:requirements", None)
    assert "cases/zephyr.csv" in [path.relative_to(project).as_posix() for path in files]


@pytest.mark.parametrize(("links", "named"), [
    (None, "нет analytics_documentation.case_links"),
    ([LINKS[0]], "строка 3: нет связи"),
    ([LINKS[0], {"row": 3, "source_requirement_ids": ["SREQ-0009"]}], "SREQ-0009 не SREQ ответа"),
    ([LINKS[0], {"row": 3, "source_requirement_ids": []}], "строка 3: пусто без reason"),
    ([*LINKS, {"row": 4, "source_requirement_ids": ["SREQ-0002"]}], "строка 4: не строка кейса CSV"),
])
def test_wrong_case_links_are_refused_before_the_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, links, named: str) -> None:
    from tools import pilot_state
    from tools.cases_import import CasesImportError

    _project_dir, root, attempt_id = _scanned(tmp_path, monkeypatch)
    _request_requirements(root, attempt_id)
    with pytest.raises(CasesImportError, match="IMPORT_CASE_LINKS") as refused:
        pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", _answer(links))
    assert named in str(refused.value) and "--transport-attempts 2" in refused.value.fix
    assert _responses(root) == []


def _to_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from tests.test_batch_assembly import _header
    from tools import api, context_marker_parts, pilot_state
    from tools.batch_assembly import plan_batches

    project, root, attempt_id = _scanned(tmp_path, monkeypatch)
    with pytest.raises(Exception, match="IMPORT_LINKS_NOT_ANSWERED"):
        from tools.cases_import import import_requirements

        import_requirements(root, attempt_id)
    _request_requirements(root, attempt_id)
    answer = pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", _answer())
    assert api.main(["cases-import", "--run-root", str(root), "--attempt", attempt_id]) == 0
    [part] = [row for row in context_marker_parts.plan(root)["parts"] if row["stage"] == "context-marker:batch-001"]
    pilot_state.publish_model_request(root, attempt_id, "context-marker:batch-001", model_id="model-context", invocation_id="part-1",
                                      input_digests=part["input_digests"])
    value = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer["content_digest"])["artifact"]
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:batch-001", {
        **value, "artifacts": {**value["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine склеивает два значения"]}}})
    marker = context_marker_parts.merge_parts(root)
    context = next(pilot_state.read_context_selection(root, attempt_id, event["artifact_digest"]) for event in pilot_state.derive_state(root)["events"]
                   if event["event_type"] == "CONTEXT_SELECTED")
    header = pilot_state.publish_generator_input(root, attempt_id, "header", _header())
    imported = pilot_state.read_attempt_receipt(root, attempt_id, "imported-cases", "ARTIFACT_READ_BACK")["record"]["imported"]
    requirements = marker["artifact"]["artifacts"]["analytics_documentation"]["requirements"]
    plan = plan_batches(requirements, {"header_digest": header["digest"], "context_receipt_digest": context["digest"]}, imported=imported)
    published = pilot_state.publish_generator_input(root, attempt_id, "plan", plan)
    return {"project": project, "root": root, "attempt_id": attempt_id, "marker": marker["content_digest"], "requirements": requirements,
            "context": context, "header": _header(), "header_digest": header["digest"], "plan": plan, "plan_digest": published["digest"],
            "imported": imported, "batch_skeletons": published.get("batch_skeletons", {})}


def test_the_merged_marker_keeps_the_linked_requirements_and_adds_one_for_a_case_outside_the_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _to_plan(tmp_path, monkeypatch)
    csv_digest = "sha256:" + hashlib.sha256(CSV.encode("utf-8")).hexdigest()
    assert [(row["source_requirement_id"], row["provenance"]) for row in run["requirements"]] == [
        ("SREQ-0001", ["docs/req.md:3"]), ("SREQ-0003", ["docs/req.md:5"]), ("SREQ-CSV-3", ["cases/zephyr.csv#строка 3"])]
    assert run["requirements"][2]["text"] == "Проверить склейку с пустой правой частью" and run["requirements"][2]["digest"] == csv_digest
    imported = run["imported"]
    assert [case["source_requirement_ids"] for case in imported["cases"]] == [["SREQ-0001", "SREQ-0003"], ["SREQ-CSV-3"]]
    assert imported["gaps"] == [{"source_requirement_id": "SREQ-0002", "text": "combine отказывает на None."}]
    [batch] = run["plan"]["batches"]
    assert batch["owned_source_requirement_ids"] == ["SREQ-0001", "SREQ-0003", "SREQ-CSV-3"]


def test_the_cases_of_a_shared_requirement_and_its_requirements_are_one_group() -> None:
    from tools.batch_assembly import _imported_groups

    source = [{"source_requirement_id": item} for item in ("SREQ-1", "SREQ-2", "SREQ-3", "SREQ-4")]
    imported = {"cases": [{"source_requirement_ids": ["SREQ-1", "SREQ-3"]}, {"source_requirement_ids": ["SREQ-3"]},
                          {"source_requirement_ids": ["SREQ-2"]}, {"source_requirement_ids": ["SREQ-4"]}]}
    groups = _imported_groups(source, imported)
    assert groups["SREQ-1"] == groups["SREQ-3"] and len({groups["SREQ-1"], groups["SREQ-2"], groups["SREQ-4"]}) == 3


def _fragment(run: dict) -> dict:
    """The fragment of the one batch: one canonical requirement per owned source requirement, the human fields and the
    management of the import, a case traced to every requirement it is linked to; manual steps."""
    from tools.batch_assembly import with_digest
    from tools.cases_import import canonical_management, case_sources

    [batch] = run["plan"]["batches"]
    ns = batch["namespace"]
    owned = batch["owned_source_requirement_ids"]
    creq = {source: f"CREQ-{ns}-{n:03d}" for n, source in enumerate(owned, start=1)}
    text = {row["source_requirement_id"]: row["text"] for row in run["requirements"]}
    return with_digest({
        "schema_version": "1.0.0", "status": "COMPLETE", "batch_id": batch["batch_id"], "namespace": ns, "plan_digest": run["plan"]["digest"],
        "header_digest": run["plan"]["header_digest"], "context_receipt_digest": run["context"]["digest"], "context_receipt": run["context"],
        "owned_source_requirement_ids": owned,
        "requirements": [{"requirement_id": creq[source], "display_order": n, "text": text[source], "provenance": [source]}
                         for n, source in enumerate(owned, start=1)],
        "source_to_canonical_mappings": [{"source_requirement_id": source, "canonical_requirement_ids": [creq[source]]} for source in owned],
        "operation_capabilities": [],
        "test_cases": [{
            "case_id": f"TC-{ns}-{n:03d}", "display_order": n, "requirement_ids": [creq[source] for source in case_sources(row)], "title": row["title"],
            "objective": row["objective"], "categories": ["functional"], "priority": row["priority"], "preconditions": row["preconditions"],
            "management": canonical_management(row),
            "steps": [{"step_id": f"STEP-{ns}-{n:03d}-{k}", "display_order": k, "action": step["action"], "test_data": step["test_data"],
                       "manual_only": True, "manual_reason": "Импорт: машинная часть ещё не достроена.", "automation_blockers": [],
                       "operation": None, "inputs": [], "outputs": [],
                       "expectations": [{"expectation_id": f"EXP-{ns}-{n:03d}-{k}-{j}", "display_order": j, "text": item, "assertions": []}
                                        for j, item in enumerate(step["expectations"], start=1)]}
                      for k, step in enumerate(row["steps"], start=1)],
        } for n, row in enumerate(run["imported"]["cases"], start=1)],
        "diagnostics": [],
    })


def _changed(good: dict, change) -> dict:
    from tools.batch_assembly import with_digest

    value = json.loads(json.dumps({key: item for key, item in good.items() if key != "digest"}))
    change(value)
    return with_digest(value)


def test_the_trace_goes_through_the_documents_and_the_management_stays_byte_for_byte(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_requested import _api, _journal, _put

    run = _to_plan(tmp_path, monkeypatch)
    [batch] = run["plan"]["batches"]
    skeleton = run["batch_skeletons"][batch["batch_id"]]
    code, requested = _generator_request(run, run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"], skeleton)
    assert code == 0, requested
    good = _fragment(run)
    tmp = run["root"] / "tmp"
    response = ("model-response", "--run-root", str(run["root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{batch['batch_id']}")
    before = _journal(run["root"])

    def folder(value: dict) -> None:
        value["test_cases"][0]["management"]["folder"] = "/Другая папка"

    code, refused = _api(*response, "--artifact", _put(tmp / "folder.json", _changed(good, folder)))
    assert code == 2 and refused["code"] == "IMPORT_HUMAN_FIELD_CHANGED" and "/test_cases/0/management" in refused["message"], refused

    def trace(value: dict) -> None:
        value["test_cases"][0]["requirement_ids"] = value["test_cases"][0]["requirement_ids"][:1]  # SREQ-0003 dropped

    code, refused = _api(*response, "--artifact", _put(tmp / "trace.json", _changed(good, trace)))
    assert code == 2 and refused["code"] == "IMPORT_TRACE_CHANGED" and "SREQ-0001, SREQ-0003" in refused["message"], refused
    assert _journal(run["root"]) == before
    code, answered = _api(*response, "--artifact", _put(tmp / "good.json", good), "--transport-attempts", "2")
    assert code == 0, answered


def test_the_report_and_the_analyst_name_the_gap_and_the_case_outside_the_documents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_report import write_report

    run = _to_plan(tmp_path, monkeypatch)
    write_report(run["project"], run["root"].name)
    report = json.loads((run["root"] / "report.json").read_text(encoding="utf-8"))
    assert report["import"]["gaps"] == [{"source_requirement_id": "SREQ-0002", "text": "combine отказывает на None."}]
    assert report["import"]["outside"] == [3]
    assert "требований без кейса в CSV: 1 (SREQ-0002); кейсов вне документов: 1 (строки 3)" in (run["root"] / "report.md").read_text(encoding="utf-8")
    questions = (run["root"] / "questions-for-analyst.md").read_text(encoding="utf-8")
    assert "## Требования без кейса в CSV" in questions and "SREQ-0002: combine отказывает на None. — в CSV нет кейса" in questions
    assert "## Кейсы CSV вне документов" in questions and "строка 3" in questions


def test_the_review_view_names_the_gaps_of_an_import_with_documents() -> None:
    from tests.test_requirement_traceability import canonical_fixture
    from tools.review_view import render

    text = render(canonical_fixture(), imported=True, import_gaps=(["SREQ-0002"], ["строка 3"])).decode("utf-8")
    assert "требований без кейса в CSV — SREQ-0002; кейсов вне документов (SREQ-CSV-) — строка 3" in text
    assert "Импорт с документами" not in render(canonical_fixture(), imported=True).decode("utf-8")  # stage 1 keeps its bytes


def test_only_the_missing_openspec_requirement_of_a_gap_is_tolerated_on_the_merge() -> None:
    from tools.pilot_state import _OPENSPEC_MISSING_TAIL, _gap_marks, _openspec_refusals

    mark = "openspec/specs/catalog/spec.md:5 — capability=catalog; ### Requirement: Refuse"
    answer = {"artifacts": {"analytics_documentation": {"requirements": [
        {"source_requirement_id": "SREQ-0001", "provenance": ["openspec/specs/catalog/spec.md:3 — capability=catalog; ### Requirement: Join"]},
        {"source_requirement_id": "SREQ-0002", "provenance": [mark]}]}}}
    tolerated = _gap_marks(answer, [{"source_requirement_id": "SREQ-0001"}])
    assert tolerated == {mark} and _gap_marks(answer, None) == set()
    other = "openspec/specs/catalog/spec.md:3 — capability=catalog; ### Requirement: Join"
    rows = [{"path": "p", "code": "OPENSPEC_MISSING", "message": mark + _OPENSPEC_MISSING_TAIL},
            {"path": "p", "code": "OPENSPEC_MISSING", "message": other + _OPENSPEC_MISSING_TAIL},
            {"path": "p", "code": "OPENSPEC_EXTRA", "message": mark + " — absent from final selected specification"},
            {"path": "p", "code": "OPENSPEC_SOURCE_MISMATCH", "message": mark + " — normalized origin must retain exact source digest"}]
    assert [row["code"] for row in _openspec_refusals(rows, tolerated)] == ["OPENSPEC_MISSING", "OPENSPEC_EXTRA", "OPENSPEC_SOURCE_MISMATCH"]
    assert _openspec_refusals(rows, tolerated)[0]["message"].startswith(other)


SPEC = ("## Requirements\n### Requirement: Join\ncombine joins two strings.\n#### Scenario: Joined\n- WHEN combine\n- THEN a:b\n"
        "### Requirement: Refuse\ncombine refuses None.\n#### Scenario: Refused\n- WHEN None\n- THEN error\n")


def test_a_gap_of_an_openspec_requirement_still_merges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import api, context_marker_parts, pilot_state
    from tools.build_context import build_context
    from tools.context_marker_merge import REQUIREMENTS_PLACEHOLDER

    project = _docs_project(tmp_path)
    (project / "openspec" / "specs" / "catalog").mkdir(parents=True)
    (project / "openspec" / "specs" / "catalog" / "spec.md").write_bytes(SPEC.encode("utf-8"))
    code, scan = _scan(project, monkeypatch, docs=["docs/req.md"])
    assert code == 0, scan
    root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    entry = {"path": "openspec/specs/catalog/spec.md", "content": SPEC, "sha256": "sha256:" + hashlib.sha256(SPEC.encode("utf-8")).hexdigest()}
    rows = build_context(PACK_ROOT, docs_snapshot=[entry])["artifacts"]["analytics_documentation"]["requirements"]
    assert len(rows) == 2
    links = [{"row": 2, "source_requirement_ids": [rows[0]["source_requirement_id"]]}, {"row": 3, "source_requirement_ids": [], "reason": "нет в спеке"}]
    _request_requirements(root, attempt_id)
    answer = pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:requirements", {
        "schema_version": "5.0.0", "stage": "context-marker", "warnings": [],
        "artifacts": {"analytics_documentation": {"requirements": rows, "case_links": links}, "source_code_and_diff": {"sources": [REQUIREMENTS_PLACEHOLDER]}}})
    assert api.main(["cases-import", "--run-root", str(root), "--attempt", attempt_id]) == 0
    value = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:requirements", answer["content_digest"])["artifact"]
    for part in [row for row in context_marker_parts.plan(root)["parts"] if row["stage"].startswith("context-marker:batch-")]:
        pilot_state.publish_model_request(root, attempt_id, part["stage"], model_id="model-context", invocation_id=part["stage"][-3:],
                                          input_digests=part["input_digests"])
        pilot_state.publish_model_stage_artifact(root, attempt_id, part["stage"], {
            **value, "artifacts": {**value["artifacts"], "source_code_and_diff": {"sources": ["src/sample.py — combine"]}}})
    merged = context_marker_parts.merge_parts(root)  # SREQ «Refuse» of OpenSpec is a gap: no case of the CSV links it
    ids = [row["source_requirement_id"] for row in merged["artifact"]["artifacts"]["analytics_documentation"]["requirements"]]
    assert ids == [rows[0]["source_requirement_id"], "SREQ-CSV-3"]
