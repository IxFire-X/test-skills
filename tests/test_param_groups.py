"""FaT-PT (step 1): a parameter group in the canonical — one case per variant stays, the group is only shown together."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
NAMES = ("Anna", "Boris", "Clara")


def _variant(case: dict, number: int, name: str) -> dict:
    """A copy of the first example case whose owner first name is ``name``: only literals and texts change."""
    text = json.dumps(case, ensure_ascii=False).replace('"Anna"', json.dumps(name)).replace("Anna", name)
    variant = json.loads(text)
    suffix = f"-V{number}"
    variant["case_id"] = case["case_id"] + suffix
    ids = {step["step_id"]: step["step_id"] + suffix for step in variant["steps"]}
    for step in variant["steps"]:
        step["step_id"] = ids[step["step_id"]]
        for item in step["inputs"]:
            if item["source"]["kind"] == "step_output":
                item["source"]["step_id"] = ids[item["source"]["step_id"]]
        for expectation in step["expectations"]:
            expectation["expectation_id"] += suffix
            for assertion in expectation["assertions"]:
                assertion["assertion_id"] += suffix
                for key in ("actual", "expected"):
                    if assertion.get(key, {}).get("kind") == "step_output":
                        assertion[key]["step_id"] = ids[assertion[key]["step_id"]]
    return variant


def grouped(names: tuple[str, ...] = NAMES, *, version: str = "1.1.0") -> dict:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    document["schema_version"] = version
    first = document["test_cases"][0]
    requirement = next(item for item in document["requirements"] if item["requirement_id"] == first["requirement_ids"][0])
    requirement["design"] = {"parameters": [
        {"id": "P-first", "name": "имя", "valid": [{"id": f"C-first-{index}"} for index in range(len(names))], "invalid": []},
        {"id": "P-phone", "name": "телефон", "valid": [{"id": "C-phone-ok"}], "invalid": []},
    ]}
    variants = [first] + [_variant(first, number, name) for number, name in enumerate(names[1:], 1)]
    for index, case in enumerate(variants):
        case["param_group"] = "PG-first-name"
        case["design_refs"] = [{"parameter": "P-first", "class": f"C-first-{index}", "value": names[index]},
                               {"parameter": "P-phone", "class": "C-phone-ok"}]
    document["test_cases"][1:1] = variants[1:]
    for order, case in enumerate(document["test_cases"], 1):
        case["display_order"] = order
    return document


def _codes(document: dict) -> list[tuple[str, str]]:
    from tools.canonical_document import validate_canonical_document

    return [(item["path"], item["code"]) for item in validate_canonical_document(document)]


# --- the canonical ------------------------------------------------------------------------------

def test_a_group_whose_cases_differ_only_in_values_is_valid() -> None:
    assert _codes(grouped()) == []


def test_the_old_canonical_without_groups_stays_valid_in_both_versions() -> None:
    from tools.canonical_document import validate_canonical_document

    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert document["schema_version"] == "1.0.0" and validate_canonical_document(document) == []
    document["schema_version"] = "1.1.0"
    assert validate_canonical_document(document) == []


def test_a_group_needs_canonical_1_1_0() -> None:
    codes = _codes(grouped(version="1.0.0"))
    assert codes and all(path.startswith("/schema_version") or path == "" for path, _ in codes), codes


def _step_output_check(document: dict) -> None:
    """Every case of the group checks the count of its own first step at its last step (actual is a step output)."""
    for case in document["test_cases"][:3]:
        step = case["steps"][-1]
        assertion = step["expectations"][0]["assertions"][0]
        assertion["actual"] = {"kind": "step_output", "step_id": case["steps"][0]["step_id"], "output_id": "count"}


def test_a_group_whose_check_reads_an_earlier_step_of_its_own_case_is_valid() -> None:
    document = grouped()
    _step_output_check(document)
    assert _codes(document) == []
    document["test_cases"][1]["steps"][-1]["expectations"][0]["assertions"][0]["actual"]["step_id"] = document["test_cases"][1]["steps"][1]["step_id"]
    assert any(code == "SEMANTIC_PARAM_GROUP_MISMATCH" and "/actual/step_id" in path for path, code in _codes(document))


def test_a_group_with_an_exists_check_has_no_expected_and_is_compared() -> None:
    document = grouped()
    for case in document["test_cases"][:3]:
        assertion = case["steps"][-1]["expectations"][0]["assertions"][0]
        assertion["operator"] = "exists"
        del assertion["expected"]
    assert _codes(document) == []
    document["test_cases"][2]["steps"][-1]["expectations"][0]["assertions"][0]["operator"] = "not_exists"
    assert any(code == "SEMANTIC_PARAM_GROUP_MISMATCH" and path.endswith("/operator") for path, code in _codes(document))


def test_a_group_of_one_case_is_refused() -> None:
    assert ("/test_cases/0/param_group", "SEMANTIC_PARAM_GROUP_SINGLE") in _codes(grouped(NAMES[:1]))


def _set(document: dict, case_index: int, path: tuple, value) -> None:
    target = document["test_cases"][case_index]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(("path", "value"), [
    (("preconditions",), ["Другое предусловие для варианта."]),
    (("priority",), "LOW"),
    (("categories",), ["negative"]),
    (("requirement_ids",), ["CREQ-B1-001"]),
    (("steps", 1, "operation", "capability_id"), "CAP-B1-owners_state"),
    (("steps", 2, "inputs", 0, "target", "name"), "verb"),
    (("steps", 2, "expectations", 0, "assertions", 0, "operator"), "not_equals"),
    (("technique",), "EP"),
])
def test_a_group_whose_cases_differ_beyond_values_is_refused_at_the_first_difference(path: tuple, value) -> None:
    document = grouped()
    _set(document, 1, path, value)
    codes = _codes(document)
    assert any(code == "SEMANTIC_PARAM_GROUP_MISMATCH" and found.startswith("/test_cases/1/" + "/".join(map(str, path)))
               for found, code in codes), codes


def test_a_group_with_a_missing_step_is_refused() -> None:
    document = grouped()
    document["test_cases"][2]["steps"].pop()
    assert any(code == "SEMANTIC_PARAM_GROUP_MISMATCH" and path.startswith("/test_cases/2/steps") for path, code in _codes(document))


@pytest.mark.parametrize("edit", ["two parameters vary", "a variant twice", "an undeclared parameter", "no reference"])
def test_a_group_must_vary_exactly_one_declared_design_parameter(edit: str) -> None:
    document = grouped()
    refs = document["test_cases"][1]["design_refs"]
    if edit == "two parameters vary":
        refs[1]["class"] = "C-phone-bad"
    elif edit == "a variant twice":
        refs[0] = dict(document["test_cases"][0]["design_refs"][0])
    elif edit == "an undeclared parameter":
        for case in document["test_cases"][:3]:
            for ref in case["design_refs"]:
                if ref["parameter"] == "P-first":
                    ref["parameter"] = "P-unknown"
    else:
        document["test_cases"][1]["design_refs"] = []
    assert any(code == "SEMANTIC_PARAM_GROUP_PARAMETER" for _, code in _codes(document)), _codes(document)


def test_the_group_parameter_is_the_one_whose_variants_differ() -> None:
    from tools.canonical_document import param_groups

    groups = param_groups(grouped())
    assert [(group["group_id"], group["parameter"]["id"], [case["case_id"] for case in group["cases"]]) for group in groups] == [
        ("PG-first-name", "P-first", ["TC-B1-001", "TC-B1-001-V1", "TC-B1-001-V2"])]
    assert param_groups(json.loads(EXAMPLE.read_text(encoding="utf-8"))) == []


# --- projections --------------------------------------------------------------------------------

def test_html_shows_a_group_once_with_a_parameter_and_a_row_per_case() -> None:
    from tools.test_case_projections import render_html_preview

    plain = render_html_preview(json.loads(EXAMPLE.read_text(encoding="utf-8"))).payload.decode("utf-8")
    page = render_html_preview(grouped()).payload.decode("utf-8")
    assert page.count("<section>") == plain.count("<section>")
    assert "<code>&lt;имя&gt;</code>" in page and "PG-first-name" in page
    table = page[page.index("Варианты данных"):]
    table = table[:table.index("</table>")]
    for case_id, name in zip(("TC-B1-001", "TC-B1-001-V1", "TC-B1-001-V2"), NAMES):
        assert case_id in table and name in table
    assert "<th>Вариант</th>" not in table, "the same title in every row is not repeated"


def test_markdown_shows_a_group_once_with_a_parameter_and_a_row_per_case() -> None:
    from tools.test_case_projections import render_markdown

    plain = render_markdown(json.loads(EXAMPLE.read_text(encoding="utf-8"))).payload.decode("utf-8")
    document = grouped()
    document["test_cases"][1]["title"] += " (Boris)"
    document["test_cases"][1]["steps"][4]["expectations"][0]["text"] = "Количество владельцев изменилось на 2."
    text = render_markdown(document).payload.decode("utf-8")
    assert text.count("\n## ТК-") == plain.count("\n## ТК-")
    assert "<имя>" in text and "PG-first-name" in text
    rows = [line for line in text.splitlines() if line.startswith("| ") and "TC-B1-001" in line]
    assert len(rows) == 3 and all(name in row for row, name in zip(rows, NAMES))
    assert "(Boris)" in rows[1], "a title that differs is shown in its row"
    assert "| Ожидаемый результат |" in text and "| 2 | Boris | 2 |" in rows[1] and "| 1 | Anna | 1 |" in rows[0]


def test_the_zephyr_csv_keeps_one_case_per_variant() -> None:
    from tools.test_case_projections import render_zephyr_csv

    document = grouped()
    plain = copy.deepcopy(document)
    for case in plain["test_cases"]:
        case.pop("param_group", None)
    assert render_zephyr_csv(document).payload == render_zephyr_csv(plain).payload


def test_a_document_without_groups_renders_the_same_bytes() -> None:
    from tools.test_case_projections import render_html_preview, render_markdown

    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    newer = dict(document, schema_version="1.1.0")
    assert render_html_preview(newer).payload == render_html_preview(document).payload
    assert render_markdown(newer).payload == render_markdown(document).payload


# --- the reviewer view (step 2) ------------------------------------------------------------------

def test_the_reviewer_view_shows_a_group_once_with_a_row_per_case() -> None:
    from tools.review_view import render

    plain = render(json.loads(EXAMPLE.read_text(encoding="utf-8"))).decode("utf-8")
    view = render(grouped()).decode("utf-8")
    assert view.count("\n### ") == plain.count("\n### ")
    assert "reviewed_case_ids по порядку: TC-B1-001, TC-B1-001-V1, TC-B1-001-V2," in view
    block = view[view.index("### PG-first-name"):]
    block = block[:block.index("\n### ")]
    assert "TC-B1-001-V1" in block and "STEP-B1-001-03-V1" not in block, "the steps once, with the IDs of the first case"
    assert 'firstName="<имя>"' in block and '"firstName":"<имя>"' in block, "text and machine values slotted"
    rows = [line for line in block.splitlines() if line.startswith("| ") and "TC-B1-001" in line]
    assert len(rows) == 3 and all(name in row for row, name in zip(rows, NAMES))


def test_the_reviewer_view_of_a_group_is_smaller_and_without_groups_unchanged() -> None:
    from tools.review_view import render

    document = grouped()
    plain = copy.deepcopy(document)
    for case in plain["test_cases"]:
        case.pop("param_group", None)
    assert len(render(document)) < len(render(plain))
    example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    def without_digest(view: bytes) -> list[bytes]:  # the version is part of the document digest in the header
        return [line for line in view.splitlines() if b"document_sha256" not in line]

    assert without_digest(render(dict(example, schema_version="1.1.0"))) == without_digest(render(example))
