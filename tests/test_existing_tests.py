"""FaT-EC: the map of what the project's own tests already call — code reads the scanned test files, never runs them."""
from __future__ import annotations

import copy
import glob
import hashlib
import json
import os
from pathlib import Path

import pytest

OWNER_CONTROLLER = """package org.example.owner;

public class OwnerController {
    public String show(int id) { return "owners/ownerDetails"; }
}
"""

OWNER_TESTS = """package org.example.owner;

import org.junit.jupiter.api.Test;

class OwnerControllerTests {
    @Test
    void showsOwner() throws Exception {
        mockMvc.perform(get("/api/owners/{ownerId}", 1).param("lastName", "Franklin"))
            .andExpect(status().isOk())
            .andExpect(view().name("owners/ownerDetails"));
    }

    @Test
    void rejectsEmptyForm() throws Exception {
        // a comment with get("/hidden")
        mockMvc.perform(post("/api/owners/new").param("firstName", "George")).andExpect(status().isBadRequest());
    }

    @Test
    void refusesNegativeId() {
        OwnerController controller = new OwnerController();
        assertThrows(IllegalArgumentException.class, () -> controller.show(-1));
    }
}
"""

PY_TESTS = """import pytest
from app import create_app


def test_missing_item(client):
    response = client.get("/items/17")
    assert response.status_code == 404


def test_bad_value():
    with pytest.raises(ValueError):
        create_app("Secret-Value-123")
"""


def _write(root: Path, relative: str, text: str) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return {"project_path": relative, "kind": "test" if "test" in relative.lower() else "source",
            "content_digest": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def _project(tmp_path: Path) -> tuple[Path, dict, list[str]]:
    rows = [_write(tmp_path, "src/main/java/org/example/owner/OwnerController.java", OWNER_CONTROLLER),
            _write(tmp_path, "src/test/java/org/example/owner/OwnerControllerTests.java", OWNER_TESTS),
            _write(tmp_path, "app.py", "def create_app(value=None):\n    return value\n"),
            _write(tmp_path, "tests/test_items.py", PY_TESTS)]
    return tmp_path, {"files": rows}, ["src/main/java/org/example/owner/OwnerController.java", "app.py"]


def test_the_map_names_endpoints_classes_and_outcomes_without_code_or_data(tmp_path: Path) -> None:
    from tools.existing_tests import build_map, render_map

    project, inventory, context = _project(tmp_path)
    value = build_map(project, inventory, context)
    assert value["GET /api/owners/{}"] == {"OwnerControllerTests": {"showsOwner": ["200", "view owners/ownerDetails"]}}
    assert value["POST /api/owners/new"] == {"OwnerControllerTests": {"rejectsEmptyForm": ["400"]}}
    assert value["OwnerController"]["OwnerControllerTests"]["refusesNegativeId"] == ["IllegalArgumentException"]
    assert value["GET /items/{}"] == {"test_items": {"test_missing_item": ["404"]}}
    assert value["app"]["test_items"]["test_bad_value"] == ["ValueError"]
    assert "GET /hidden" not in value  # a commented-out call is not a call
    text = render_map(value).decode("utf-8")
    for code in ("mockMvc", "perform", "param(", "Franklin", "George", "Secret-Value-123", "assertThrows", "{", "->"):
        assert code not in text.replace("{}", ""), code
    assert render_map(value) == render_map(copy.deepcopy(value))


def test_a_test_file_changed_after_the_scan_and_a_generated_one_are_not_in_the_map(tmp_path: Path) -> None:
    from tools.existing_tests import build_map

    project, inventory, context = _project(tmp_path)
    (project / "tests" / "test_items.py").write_text(PY_TESTS + "\n# changed\n", encoding="utf-8")
    run = project / ".pilot-runs" / "r" / "materialization-ownership"
    run.mkdir(parents=True)
    (run / "a.json").write_text(json.dumps({"payload": {"files": [{"path": "src/test/java/org/example/owner/OwnerControllerTests.java"}]}}),
                                encoding="utf-8")
    assert build_map(project, inventory, context) == {}


def test_the_map_is_cut_at_its_limit_with_a_line_for_the_rest() -> None:
    from tools.existing_tests import LIMIT, render_map

    value = {f"GET /resource{index:04d}/{{}}": {"ResourceTests": {f"case{index}": ["200"]}} for index in range(2000)}
    data = render_map(value)
    assert len(data) <= LIMIT + 100 and data.decode("utf-8").splitlines()[-1].startswith("- ещё ")
    many = {"Owner": {"OwnerTests": {f"m{index}": ["200", "404"] for index in range(9)}}}
    assert render_map(many).decode("utf-8") == "- Owner: OwnerTests (9 тестов; исходы: 200, 404)\n"


def _case(path: str, status: int, method: str = "GET") -> dict:
    return {"case_id": f"TC-{method}-{path}", "steps": [{
        "inputs": [{"target": {"name": "method"}, "source": {"kind": "literal", "value": method}},
                   {"target": {"name": "path"}, "source": {"kind": "literal", "value": path}}],
        "operation": {"kind": "project_action", "capability_id": "CAP-x"},
        "expectations": [{"assertions": [{"actual": {"kind": "project_result", "name": "status"}, "operator": "equals",
                                          "expected": {"kind": "literal", "value": status}}]}]}]}


@pytest.mark.parametrize(("path", "status", "method", "repeated"), [
    ("/owners/{ownerId}", 200, "GET", True),     # /api prefix of the project tests: whole segments from the end
    ("/owners/7", 200, "GET", True),
    ("/petowners/{id}", 200, "GET", False),      # not a substring match
    ("/owners/{ownerId}", 404, "GET", False),    # another outcome is a new condition
    ("/owners/{ownerId}", 200, "DELETE", False),
    ("/owners/new", 400, "POST", True),
])
def test_a_repeat_is_the_same_endpoint_by_whole_segments_and_the_same_outcome(tmp_path: Path, path: str, status: int, method: str, repeated: bool) -> None:
    from tools.existing_tests import build_map, repeats

    project, inventory, context = _project(tmp_path)
    case = _case(path, status, method)
    assert repeats({"test_cases": [case]}, build_map(project, inventory, context)) == ([case["case_id"]] if repeated else [])


def test_a_setup_step_alone_does_not_make_a_repeat_and_field_errors_are_an_outcome(tmp_path: Path) -> None:
    from tools.existing_tests import build_map, repeats

    form = OWNER_TESTS.replace('.andExpect(status().isBadRequest());',
                               '.andExpect(model().attributeHasFieldErrors("owner", "address", "telephone"));')
    (tmp_path / "src/test/java/org/example/owner").mkdir(parents=True)
    rows = [_write(tmp_path, "src/test/java/org/example/owner/OwnerControllerTests.java", form)]
    value = build_map(tmp_path, {"files": rows}, [])
    assert value["POST /api/owners/new"]["OwnerControllerTests"]["rejectsEmptyForm"] == ["field address", "field telephone"]
    case = _case("/owners/{ownerId}", 200)
    new = copy.deepcopy(case["steps"][0])
    new["inputs"][1]["source"]["value"], new["inputs"][0]["source"]["value"] = "/owners/new", "POST"
    new["expectations"][0]["assertions"][0] = {"actual": {"kind": "project_result", "name": "error_fields"}, "operator": "equals",
                                               "expected": {"kind": "literal", "value": ["lastName"]}}
    case["steps"].append(new)  # a tested setup step and a field the project tests never check
    assert repeats({"test_cases": [case]}, value) == []
    new["expectations"][0]["assertions"][0]["expected"]["value"] = ["telephone"]
    assert repeats({"test_cases": [case]}, value) == [case["case_id"]]


def test_the_review_view_adds_the_hint_and_the_map_only_with_a_map(tmp_path: Path) -> None:
    from tools.existing_tests import render_map
    from tools.review_view import render

    document = json.loads((Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json").read_text(encoding="utf-8"))
    assert render(document) == render(document, existing={}) == render(document, existing=None)
    value = {"Owner": {"OwnerTests": {"createsOwner": ["201"]}}}
    view = render(document, existing=value).decode("utf-8")
    hints = view.split("## Подсказки кода\n", 1)[1].split("\n## ", 1)[0]
    assert "Существующие тесты проекта" in hints and render_map(value).decode("utf-8").strip() in hints


def _generator_run_with_tests(tmp_path: Path, with_tests: bool):
    from tests.test_step_inputs import _generator_run, _request

    tmp_path.mkdir(parents=True, exist_ok=True)
    if with_tests:
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_part0.py").write_text("from part0 import X\n\n\ndef test_x():\n    assert X == 1\n", encoding="utf-8")
    root, attempt_id, _baseline, receipts, marker, plan, header, _plan_path, _header_path = _generator_run(tmp_path)
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    return root, receipts[0]["digest"]


def test_step_inputs_give_the_generator_the_map_and_rebuild_a_changed_one(tmp_path: Path) -> None:
    from tools.existing_tests import map_path
    from tools.step_size import request_files

    root, context = _generator_run_with_tests(tmp_path, with_tests=True)
    found = request_files(root, "tc-generator:BATCH-fixture")
    rows = [row for row in found["files"] if row.get("basis") == "карта"]
    target = map_path(root, context)
    assert rows == [{"digest": context, "path": str(target), "bytes": target.stat().st_size, "basis": "карта"}], found
    original = target.read_bytes()
    assert b"- part0: test_part0 (test_x)" in original
    target.write_bytes(original.replace(b"test_x", b"test_y"))  # a changed map is built again from the frozen inputs
    request_files(root, "tc-generator:BATCH-fixture")
    assert target.read_bytes() == original


def test_without_project_tests_the_generator_inputs_are_the_ones_before(tmp_path: Path) -> None:
    from tools.step_size import request_files

    root, _context = _generator_run_with_tests(tmp_path, with_tests=False)
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert not [row for row in found["files"] if row.get("basis")] and not list((root / "review-views").glob("*.existing-tests.md"))


CAMPAIGN = Path(r"D:\AI-Projects\live\campaign-20261028")
WINDOW_TOKENS = 239_000
WORST_BATCH_TOKENS = 201_000  # FaT-BO: the worst batch estimate of the campaign plans (Petclinic and rest)


@pytest.mark.skipif(not (CAMPAIGN / "spring-petclinic").is_dir(), reason="the campaign of 28.10 is on the work machine only")
@pytest.mark.parametrize("project", ["spring-petclinic", "spring-petclinic-rest", "jsoup", "jpetstore-6", "spring-petclinic-microservices", "commons-lang"])
def test_the_map_of_a_campaign_project_keeps_the_worst_batch_in_the_window(project: str) -> None:
    """Only reads the campaign: the map of the generator's last context, its tokens on top of the worst batch."""
    from tools.existing_tests import for_context, view_bytes
    from tools.step_size import TOKENS_PER_BYTE

    run = sorted(glob.glob(str(CAMPAIGN / project / ".pilot-runs" / "*" / "")), key=os.path.getmtime)[-1]
    receipts = sorted(glob.glob(os.path.join(run, "context-selections", "*", "*.json")), key=os.path.getmtime)
    data = view_bytes(for_context(CAMPAIGN / project, json.loads(Path(receipts[-1]).read_text(encoding="utf-8"))))
    assert len(data) <= 16_500 and WORST_BATCH_TOKENS + len(data) * TOKENS_PER_BYTE <= WINDOW_TOKENS


@pytest.mark.parametrize("with_tests", [True, False])
def test_the_report_counts_the_map_in_the_generator_input_as_step_inputs_do(tmp_path: Path, with_tests: bool) -> None:
    """FaT-RE (hunt 3, 3.12): the report measured the batch input without the map that step-inputs give the generator."""
    from tools.run_report import build_report
    from tools.step_size import request_files

    root, _context = _generator_run_with_tests(tmp_path, with_tests=with_tests)
    found = request_files(root, "tc-generator:BATCH-fixture")
    report, _rest = build_report(root.parent.parent, root.name)
    [call] = [row for row in report["model_calls"] if row["stage"] == "tc-generator:BATCH-fixture"]
    maps = sum(row["bytes"] for row in found["files"] if row.get("basis") == "карта")
    assert (maps > 0) is with_tests
    assert call["input_bytes"] == sum(row["bytes"] for row in found["files"]) + sum(row["bytes"] for row in found["bindings"]), (call, found)
