"""V7 «Вход модели»: O-14a (files that are no code stay in the inventory, not in the context), O-15 (the model reads a projection of the
inventory), O-16 (the generator reads the plan without the texts of the requirements; they are in the markup)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_first_run import _python_project, _write
from tests.test_review_requested import _generated_run, _journal
from tests.test_scan_guard import _scan
from tools import pilot_state, step_size

MODULE = "services/api"


# --- O-14a: the classes ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("path", "expected"), [
    ("LICENSE.txt", "license"), ("sub/NOTICE", "license"), ("COPYING.md", "license"), ("license.rst", "license"),
    ("LICENSES/a.txt", None), ("license_check.py", None), ("docs/license-notes.md", None),
    ("src/main/scss/header.scss", "style-source"), ("a.sass", "style-source"), ("a.less", "style-source"), ("site.css", None),
    ("static/spring-logo.svg", "image"), ("logo.png", None),
    ("res/messages_de.properties", "translation"), ("res/messages_pt_BR.properties", "translation"),
    ("res/messages.properties", None), ("res/application_dev.properties", None), ("res/orphan_de.properties", None),
    # the language of the cases (ru-RU) is the source of the texts of the expectations: never a translation to leave out
    ("res/messages_ru.properties", None), ("res/messages_ru_RU.properties", None), ("res/messages_de.properties", "translation"),
    # a page template under src/ is no licence text; the licence file of the project root is
    ("src/main/resources/templates/notice.html", None), ("src/main/resources/templates/license.html", None), ("src/LICENSE.txt", None),
    ("NOTICE", "license"), ("LICENSE", "license"),
])
def test_the_class_of_a_file_that_is_no_code(path: str, expected: str | None) -> None:
    from tools.scan_rules import non_code_class

    known = {"res/messages.properties", "res/application.properties"}
    found = non_code_class(path, known)
    assert (found[0] if found else None) == expected, found


def test_a_translation_without_its_main_file_stays_code() -> None:
    from tools.scan_rules import non_code_class

    assert non_code_class("res/messages_de.properties", {"res/messages_de.properties"}) is None
    assert non_code_class("res/messages_de.properties", {"other/messages.properties"}) is None  # the main file of another folder is no sibling


def _project_with_noise(tmp_path: Path) -> Path:
    project = _python_project(tmp_path, runtime=False)
    module = project / MODULE
    _write(module, "LICENSE.txt", "Apache License\n" * 40)
    _write(module, "res/messages.properties", "title=Title\n")
    _write(module, "res/messages_de.properties", "title=Titel\n")
    _write(module, "styles/header.scss", "$a: 1;\n")
    _write(module, "static/logo.svg", "<svg></svg>\n")
    return project


def _receipts(run_root: Path) -> list[dict]:
    attempt = pilot_state.derive_state(run_root)["attempts"][-1]["attempt_id"]
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((run_root / "context-selections" / attempt).glob("*.json"))]


def test_scan_keeps_the_files_that_are_no_code_in_the_inventory_and_out_of_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_noise(tmp_path)
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    run_root = Path(scan["run_root"])
    left = {f"{MODULE}/LICENSE.txt", f"{MODULE}/res/messages_de.properties", f"{MODULE}/styles/header.scss", f"{MODULE}/static/logo.svg"}
    in_context = {row["project_path"] for receipt in _receipts(run_root) for row in receipt["files"]}
    assert not left & in_context and f"{MODULE}/res/messages.properties" in in_context and f"{MODULE}/src/app.py" in in_context
    inventory = json.loads(next((run_root / "inventories").iterdir()).read_text(encoding="utf-8"))
    assert left <= {row["project_path"] for row in inventory["files"]}  # still counted, still bound by the baseline
    baseline = json.loads(next((run_root / "baselines").iterdir()).read_text(encoding="utf-8"))
    assert left <= {row["project_path"] for row in baseline["inputs"]}
    skipped = {row["project_path"]: row for receipt in _receipts(run_root) for row in receipt.get("skipped") or []}
    assert {path: skipped[path]["reason"] for path in left} == dict.fromkeys(left, "CONTEXT_NOT_CODE")
    assert skipped[f"{MODULE}/LICENSE.txt"]["rule"] == "license-file-v1"
    assert scan["context_skipped"]["by_reason"]["CONTEXT_NOT_CODE"]["files"] == 4


def test_paths_include_keeps_a_file_in_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project_with_noise(tmp_path)
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    skillsrc["modules"][0].setdefault("paths", {})["include"] = [f"{MODULE}/res/messages_de.properties", f"{MODULE}/LICENSE.txt"]
    (project / ".skillsrc").write_text(json.dumps(skillsrc, ensure_ascii=False), encoding="utf-8")
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    in_context = {row["project_path"] for receipt in _receipts(Path(scan["run_root"])) for row in receipt["files"]}
    assert {f"{MODULE}/res/messages_de.properties", f"{MODULE}/LICENSE.txt"} <= in_context
    assert f"{MODULE}/styles/header.scss" not in in_context  # the other classes stay out


def test_a_receipt_of_an_older_run_without_the_new_reason_is_still_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_project(tmp_path, runtime=False)  # nothing of the classes: the receipt has no CONTEXT_NOT_CODE
    code, scan = _scan(project, monkeypatch)
    assert code == 0 and "CONTEXT_NOT_CODE" not in json.dumps(scan.get("context_skipped") or {}), scan
    [receipt] = _receipts(Path(scan["run_root"]))
    assert "skipped" not in receipt


# --- O-15: the projection of the inventory ----------------------------------------------------------------------------

def _marker_stage(run_root: Path) -> str:
    return "context-marker:baseline"


def test_the_marker_reads_the_projection_of_the_inventory(tmp_path: Path) -> None:
    from tests.test_scope_pause import _paused_run

    root, attempt_id, baseline, _receipts_ = _paused_run(tmp_path)
    before = _journal(root)
    found = step_size.stage_inputs(root, "context-marker:baseline")
    [row] = [item for item in found["files"] if item["digest"] == baseline["inventory_digest"]]
    assert row["basis"] == "inventory-view-v1" and row["path"].endswith(".inventory-view.json")
    view = json.loads(Path(row["path"]).read_text(encoding="utf-8"))
    full = root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json")
    inventory = json.loads(full.read_text(encoding="utf-8"))
    assert view["columns"] == ["path", "kind", "size"] and view["inventory_digest"] == baseline["inventory_digest"]
    assert view["files"] == [[item["project_path"], item["kind"], item["size"]] for item in sorted(inventory["files"], key=lambda r: r["project_path"])]
    raw = Path(row["path"]).read_text(encoding="utf-8")
    assert raw.count("sha256") == 1 and "opaque_id" not in raw and "content_digest" not in raw  # the digest of the receipt, none per file
    assert row["bytes"] == Path(row["path"]).stat().st_size < full.stat().st_size
    assert _journal(root) == before  # the request and the journal are as they were: the projection adds no event
    request = next(event for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "MODEL_REQUESTED")
    assert baseline["inventory_digest"] in json.loads(
        (root / "model-requests" / attempt_id / (request["artifact_digest"].removeprefix("sha256:") + ".json")).read_text(encoding="utf-8"))["input_digests"]


def test_a_changed_projection_file_is_rewritten_and_does_not_change_the_measure(tmp_path: Path) -> None:
    from tests.test_scope_pause import _paused_run

    root, _attempt_id, baseline, _receipts_ = _paused_run(tmp_path)
    first = step_size.stage_inputs(root, "context-marker:baseline")
    [row] = [item for item in first["files"] if item["digest"] == baseline["inventory_digest"]]
    target = Path(row["path"])
    honest = target.read_bytes()
    target.write_bytes(b'{"view":"inventory-view-v1","files":[]}')  # the agent's file
    measured = step_size.stage_inputs(root, "context-marker:baseline", write_views=False)  # the report: measures, writes nothing
    assert target.read_bytes() == b'{"view":"inventory-view-v1","files":[]}'
    assert step_size.input_size(measured) == step_size.input_size(first)  # the bytes are the code's, not the file's
    again = step_size.stage_inputs(root, "context-marker:baseline")
    assert target.read_bytes() == honest and step_size.input_size(again) == step_size.input_size(first)


# --- O-16: the plan without the texts -----------------------------------------------------------------------------------

def _generator_stage(run: dict) -> str:
    return next(event["stage_instance_id"] for event in pilot_state.derive_state(run["run_root"])["events"]
                if event["event_type"] == "MODEL_REQUESTED" and str(event.get("stage_instance_id")).startswith("tc-generator:"))


def test_the_generator_reads_the_plan_without_the_texts_of_the_requirements(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    root, attempt_id = run["run_root"], run["attempt_id"]
    plan = json.loads(Path(run["plan"]).read_text(encoding="utf-8"))
    before = _journal(root)
    found = step_size.stage_inputs(root, _generator_stage(run))
    [row] = [item for item in found["files"] if item["digest"] == plan["digest"]]
    assert row["basis"] == "plan-view-v1" and row["path"].endswith(".plan-view.json")
    view = json.loads(Path(row["path"]).read_text(encoding="utf-8"))
    assert [item["source_requirement_id"] for item in view["source_requirements"]] == [item["source_requirement_id"] for item in plan["source_requirements"]]
    assert all("text" not in item for item in view["source_requirements"])
    assert all(item["display_order"] == original["display_order"] and item["provenance"] == original["provenance"] and item["digest"] == original["digest"]
               for item, original in zip(view["source_requirements"], plan["source_requirements"]))
    assert view["batches"] == plan["batches"] and view["view"] == "plan-view-v1"
    raw = Path(row["path"]).read_text(encoding="utf-8")
    assert not any(item["text"] in raw for item in plan["source_requirements"])  # no text of a requirement is left in the view
    # the texts the generator needs are those of the markup, byte for byte
    marker = pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", run["marker_digest"])["artifact"]
    texts = {item["source_requirement_id"]: item["text"] for item in marker["artifacts"]["analytics_documentation"]["requirements"]}
    assert texts == {item["source_requirement_id"]: item["text"] for item in plan["source_requirements"]}
    assert _journal(root) == before  # the plan of the journal is not changed, no event is added
    assert pilot_state._generator_input(run["project"], root, pilot_state.derive_state(root), attempt_id, "plan")["source_requirements"] == plan["source_requirements"]


def test_a_stage_that_is_no_generator_batch_gets_the_plan_as_it_is(tmp_path: Path) -> None:
    from tools.step_size import plan_view_bytes

    run = _generated_run(tmp_path)
    plan = json.loads(Path(run["plan"]).read_text(encoding="utf-8"))
    found = step_size.stage_inputs(run["run_root"], _generator_stage(run))
    assert {row["basis"] for row in found["files"] if row["digest"] == plan["digest"]} == {"plan-view-v1"}
    assert b'"text"' not in plan_view_bytes(plan)


def test_the_report_measures_the_projection_of_the_plan(tmp_path: Path) -> None:
    from tools.run_report import _composition

    run = _generated_run(tmp_path)
    root = run["run_root"]
    found = step_size.stage_inputs(root, _generator_stage(run), write_views=False)
    view_bytes = next(row["bytes"] for row in found["files"] if row.get("basis") == "plan-view-v1")
    assert _composition(found, run["project"], root)["plan_header"] >= view_bytes
