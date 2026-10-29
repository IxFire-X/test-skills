"""FaT-TD: light test design in the generator's own answer — technique, design, and code hints that never stop a run."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_requirement_traceability import canonical_fixture

GROWTH_LIMIT_PCT = 5.0


def _designed(text: str = "Имя владельца от 1 до 30 символов, телефон из 10 цифр.") -> dict:
    document = canonical_fixture()
    requirement = document["requirements"][0]
    requirement["text"] = text
    requirement["design"] = {"parameters": [
        {"id": "P-name", "name": "имя", "valid": [{"id": "C-name-ok"}], "invalid": [{"id": "C-name-empty"}, {"id": "C-name-long"}],
         "bounds": {"min": 1, "max": 30, "unit": "символ"}},
        {"id": "P-phone", "name": "телефон", "valid": [{"id": "C-phone-ok"}], "invalid": [{"id": "C-phone-bad", "label": "не цифры"}]},
    ]}
    case = document["test_cases"][0]
    case["technique"] = "BVA"
    case["design_refs"] = [{"parameter": "P-name", "bound": "max", "value": "30"}]
    return document


def _case(document: dict, case_id: str, technique: str, refs: list[dict]) -> None:
    case = copy.deepcopy(document["test_cases"][0])
    case["case_id"] = case_id
    case["display_order"] = len(document["test_cases"]) + 1
    for step in case["steps"]:
        step["step_id"] = step["step_id"] + "-" + case_id
        for expectation in step["expectations"]:
            expectation["expectation_id"] = expectation["expectation_id"] + "-" + case_id
            for assertion in expectation["assertions"]:
                assertion["assertion_id"] = assertion["assertion_id"] + "-" + case_id
    case["technique"] = technique
    case["design_refs"] = refs
    document["test_cases"].append(case)


def _codes(document: dict) -> list[str]:
    from tools.test_design_hints import design_hints

    return sorted(hint["code"] for hint in design_hints(document))


# --- schema --------------------------------------------------------------------------------------

def test_a_document_with_and_without_the_new_fields_is_valid() -> None:
    from tools.canonical_document import validate_canonical_document

    assert validate_canonical_document(canonical_fixture()) == []
    assert validate_canonical_document(_designed()) == []


def test_a_label_longer_than_40_characters_is_refused() -> None:
    from tools.canonical_document import validate_canonical_document

    document = _designed()
    document["requirements"][0]["design"]["parameters"][0]["valid"][0]["label"] = "x" * 41
    assert validate_canonical_document(document)


def test_the_old_document_keeps_its_digest() -> None:
    from tools.canonical_document import document_sha256

    assert document_sha256(canonical_fixture()) == document_sha256(json.loads(json.dumps(canonical_fixture())))
    assert "technique" not in json.dumps(canonical_fixture())
    # FaT-WT: a reference digest — the comparison above holds for any deterministic function, even one that adds empty
    # design/technique fields; the digest of a document without them is pinned (develop 79a5906) and recomputed by hand
    pinned = "sha256:586d34a13b02ee4dd8a68e2d773fe6fe28efe78bdf4b883450022f4b638cc135"
    by_hand = json.dumps(canonical_fixture(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert document_sha256(canonical_fixture()) == pinned == "sha256:" + hashlib.sha256(by_hand).hexdigest()


# --- hints ---------------------------------------------------------------------------------------

def test_a_document_without_the_new_fields_gives_no_hints() -> None:
    from tools.test_design_hints import design_hints, uses_test_design

    document = canonical_fixture()
    document["requirements"][0]["text"] = "Имя не более 30 символов."
    assert uses_test_design(document) is False and design_hints(document) == []


def test_missing_negative_case_and_boundary_are_hinted() -> None:
    codes = _codes(_designed())
    assert codes.count("TD_NEGATIVE_MISSING") == 3  # C-name-empty, C-name-long, C-phone-bad
    assert "TD_BOUNDARY_MISSING" in codes  # min, below_min, above_max are not covered


def test_full_coverage_gives_no_hints() -> None:
    document = _designed()
    for index, (technique, refs) in enumerate([
        ("BVA", [{"parameter": "P-name", "bound": "min"}]),
        ("BVA", [{"parameter": "P-name", "bound": "below_min"}]),
        ("BVA", [{"parameter": "P-name", "bound": "above_max"}]),
        ("EP", [{"parameter": "P-name", "class": "C-name-empty"}]),
        ("EP", [{"parameter": "P-name", "class": "C-name-long"}]),
        ("EP", [{"parameter": "P-phone", "class": "C-phone-bad"}]),
    ]):
        _case(document, f"TC-batch-a-1{index:02d}", technique, refs)
    assert _codes(document) == []


def test_boundary_hint_names_the_missing_side() -> None:
    from tools.test_design_hints import design_hints

    document = _designed()
    details = sorted(hint["detail"] for hint in design_hints(document) if hint["code"] == "TD_BOUNDARY_MISSING")
    assert details == ["above_max", "below_min", "min"]


@pytest.mark.parametrize(("text", "expected"), [
    ("3.1 Владелец создаётся по имени.", False),
    ("O01. Владелец создаётся.", False),
    ("Имя не более 30 символов.", True),
    ("Возраст от 18 до 99 лет.", True),
    ("Сумма ≤ 1000.", True),
    ("Максимум 5 питомцев.", True),
    ("Комментарий до 200 символов.", True),
    ("В заявке 2 подписи.", False),
])
def test_a_comparison_phrase_without_bounds_is_hinted(text: str, expected: bool) -> None:
    document = _designed(text)
    del document["requirements"][0]["design"]["parameters"][0]["bounds"]
    assert ("TD_BOUNDARY_UNDECLARED" in _codes(document)) is expected


def _three_parameters(cases: list[dict]) -> dict:
    document = _designed()
    document["requirements"][0]["design"] = {"parameters": [
        {"id": f"P-{name}", "name": name, "valid": [{"id": f"C-{name}-1"}, {"id": f"C-{name}-2"}], "invalid": []}
        for name in ("a", "b", "c")
    ]}
    document["test_cases"][0]["technique"] = "OTHER"
    document["test_cases"][0]["design_refs"] = []
    for index, combination in enumerate(cases):
        _case(document, f"TC-batch-a-2{index:02d}", "PAIRWISE",
              [{"parameter": f"P-{name}", "class": f"C-{name}-{value}"} for name, value in combination.items()])
    return document


def test_pairs_covered_by_fewer_cases_than_the_full_product_give_no_hints() -> None:
    rows = [{"a": 1, "b": 1, "c": 1}, {"a": 1, "b": 2, "c": 2}, {"a": 2, "b": 1, "c": 2}, {"a": 2, "b": 2, "c": 1}]
    assert "TD_PAIR_UNCOVERED" not in _codes(_three_parameters(rows))


def test_an_uncovered_pair_is_hinted_and_two_parameters_are_not_checked() -> None:
    from tools.test_design_hints import design_hints

    document = _three_parameters([{"a": 1, "b": 1, "c": 1}, {"a": 2, "b": 2, "c": 2}])
    pairs = [hint for hint in design_hints(document) if hint["code"] == "TD_PAIR_UNCOVERED"]
    assert pairs and all("=" in hint["detail"] or hint["detail"].startswith("и ещё ") for hint in pairs)
    two = _three_parameters([{"a": 1, "b": 1}])
    two["requirements"][0]["design"]["parameters"].pop()
    assert "TD_PAIR_UNCOVERED" not in _codes(two)


def test_an_unknown_reference_is_hinted() -> None:
    document = _designed()
    document["test_cases"][0]["design_refs"] = [{"parameter": "P-nope", "class": "C-x"}]
    assert "TD_REF_UNKNOWN" in _codes(document)


def test_hints_are_capped_at_four_kilobytes_with_a_remainder() -> None:
    from tools.test_design_hints import HINT_BYTES, capped

    hints = [{"code": "TD_NEGATIVE_MISSING", "requirement_id": f"CREQ-x-{index:03d}", "parameter": "P-x", "detail": "C-" + "y" * 40}
             for index in range(200)]
    kept = capped(hints)
    assert len(json.dumps(kept, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= HINT_BYTES
    assert kept[-1]["code"] == "TD_MORE" and kept[-1]["detail"] == f"и ещё {200 - (len(kept) - 1)}"


# --- assembly, report, projections, growth -------------------------------------------------------

def test_the_assembly_receipt_carries_the_hints_and_stays_the_same_without_new_fields() -> None:
    from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh
    from tools.batch_assembly import assemble_candidate

    header, context = _header(), _context_receipt()
    plan = _plan(header, context)
    fragments = [_fragment(plan, context, 0), _fragment(plan, context, 1)]
    _document, plain = assemble_candidate(header, plan, fragments)
    assert all(warning["code"] == "POSSIBLE_DUPLICATE" for warning in plain["warnings"])
    requirement = fragments[0]["requirements"][0]
    requirement["design"] = {"parameters": [{"id": "P-sum", "name": "сумма", "valid": [{"id": "C-sum-ok"}], "invalid": [{"id": "C-sum-neg"}]}]}
    fragments[0]["test_cases"][0]["technique"] = "EP"
    fragments[0]["test_cases"][0]["design_refs"] = [{"parameter": "P-sum", "class": "C-sum-ok"}]
    _refresh(fragments[0])
    _document, designed = assemble_candidate(header, plan, fragments)
    hints = [warning for warning in designed["warnings"] if warning["code"].startswith("TD_")]
    assert hints == [{"code": "TD_NEGATIVE_MISSING", "requirement_id": requirement["requirement_id"], "parameter": "P-sum", "detail": "C-sum-neg"}]


def test_the_report_counts_techniques_and_says_when_the_fields_were_not_used() -> None:
    from tools.run_report import test_design_summary

    used = test_design_summary(_designed())
    assert used["used"] is True and used["techniques"]["BVA"] == 1 and used["hints"]["TD_NEGATIVE_MISSING"] == 3
    assert all(set(gap) <= {"code", "requirement_id", "parameter", "detail"} for gap in used["gaps"])
    assert "Проверить" not in json.dumps(used, ensure_ascii=False)
    unused = test_design_summary(canonical_fixture())
    assert unused["used"] is False and unused["gaps"] == []


def test_the_report_markdown_names_an_unused_test_design() -> None:
    from tools.run_report import UNUSED_TEST_DESIGN, render_test_design

    assert UNUSED_TEST_DESIGN in render_test_design({"used": False, "techniques": {}, "hints": {}, "gaps": []})


def test_projections_show_the_technique_and_the_csv_does_not_change() -> None:
    from tools.test_case_projections import render_html_preview, render_markdown, render_zephyr_csv

    designed, plain = _designed(), canonical_fixture()
    assert "Техника: Граничные значения" in render_markdown(designed).payload.decode("utf-8")
    assert "Техника: Граничные значения" in render_html_preview(designed).payload.decode("utf-8")
    assert "Техника" not in render_markdown(plain).payload.decode("utf-8")
    assert render_zephyr_csv(designed).payload == render_zephyr_csv(plain).payload


def test_reviewer_input_grows_at_most_five_percent_on_the_upper_case() -> None:
    example = Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"
    assert example.is_file()
    from tools.batch_assembly import canonical_bytes

    document = json.loads(example.read_text(encoding="utf-8"))
    skill = sum(path.stat().st_size for path in [Path("skills/tc-reviewer/SKILL.md"), *Path("skills/tc-reviewer/references").rglob("*")] if path.is_file())
    enriched = copy.deepcopy(document)
    order = {}
    for index, requirement in enumerate(enriched["requirements"]):
        order[requirement["requirement_id"]] = index
        requirement["design"] = {"parameters": [
            {"id": f"P-{index}-name", "name": "имя владельца", "valid": [{"id": f"C-{index}-name-ok"}],
             "invalid": [{"id": f"C-{index}-name-empty"}, {"id": f"C-{index}-name-long"}], "bounds": {"min": 1, "max": 30, "unit": "символ"}},
            {"id": f"P-{index}-phone", "name": "телефон", "valid": [{"id": f"C-{index}-phone-ok"}], "invalid": [{"id": f"C-{index}-phone-bad"}]},
        ]}
    for case in enriched["test_cases"]:
        index = order[case["requirement_ids"][0]]
        case["technique"] = "BVA"
        case["design_refs"] = [{"parameter": f"P-{index}-name", "bound": "above_max", "value": "31"}]
    from tools.test_design_hints import HINT_BYTES

    base = len(canonical_bytes(document)) + skill
    after = len(canonical_bytes(enriched)) + skill + HINT_BYTES
    assert 100 * (after - base) / base <= GROWTH_LIMIT_PCT


def test_maximum_length_ids_keep_every_hint_within_the_receipt_schema() -> None:
    from tools.batch_assembly import _ASSEMBLY_SCHEMA
    from tools.schema_validation import schema_diagnostics
    from tools.test_design_hints import capped, design_hints

    document = _designed()
    long = lambda prefix, tag: prefix + tag + "x" * (40 - len(prefix) - len(tag))  # noqa: E731
    document["requirements"][0]["design"] = {"parameters": [
        {"id": long("P-", name), "name": name, "valid": [{"id": long("C-", name + "1")}, {"id": long("C-", name + "2")}], "invalid": []}
        for name in ("a", "b", "c")
    ]}
    case = document["test_cases"][0]
    case["case_id"] = "TC-" + "y" * 300
    case["technique"] = "PAIRWISE"
    case["design_refs"] = [{"parameter": long("P-", "z"), "class": long("C-", "z")}]
    hints = design_hints(document)
    assert {"TD_PAIR_UNCOVERED", "TD_REF_UNKNOWN"} <= {hint["code"] for hint in hints}
    assert all(len(hint["detail"]) <= 200 for hint in hints)
    receipt = {"schema_version": "1.0.0", "plan_digest": "sha256:" + "a" * 64, "header_digest": "sha256:" + "b" * 64,
               "fragment_digests": [{"batch_id": "BATCH-a", "digest": "sha256:" + "c" * 64}], "document_digest": "sha256:" + "d" * 64,
               "pre_review_audit": {"schema": "PASS", "semantic": "PASS", "coverage": "PASS", "provenance": "PASS"},
               "warnings": capped(hints), "digest": "sha256:" + "e" * 64}
    assert [row for row in schema_diagnostics(receipt, _ASSEMBLY_SCHEMA, Path.cwd()) if row["path"].startswith("/warnings")] == []


def test_many_pairs_never_push_out_a_missing_negative_case() -> None:
    from tools.test_design_hints import capped, design_hints

    document = _designed()
    document["requirements"][0]["design"] = {"parameters": [
        {"id": f"P-{name}", "name": name, "valid": [{"id": f"C-{name}-{index}"} for index in range(3)], "invalid": []}
        for name in ("a", "b", "c", "d")
    ]}
    document["test_cases"][0]["design_refs"] = []
    document["requirements"][1]["design"] = {"parameters": [{"id": "P-sum", "name": "сумма", "valid": [{"id": "C-sum-ok"}], "invalid": [{"id": "C-sum-neg"}]}]}
    hints = design_hints(document)
    pairs = [hint for hint in hints if hint["code"] == "TD_PAIR_UNCOVERED"]
    assert len(pairs) == 4 and pairs[-1]["detail"] == "и ещё 51 пар"
    kept = capped(hints)
    assert {"code": "TD_NEGATIVE_MISSING", "requirement_id": document["requirements"][1]["requirement_id"], "parameter": "P-sum",
            "detail": "C-sum-neg"} in kept
    assert kept[0]["code"] == "TD_NEGATIVE_MISSING"


def test_vague_expectations_never_push_the_design_hints_out_of_the_receipt() -> None:
    """FaT-CV2 (2.6): the hunter's scenario — 56 vague + 5 boundaries + 1 pair keeps all 6 design hints."""
    from tools.test_design_hints import GROUP_BYTES, GROUP_LIMIT, HINT_BYTES, HINT_LIMIT, capped_code_hints

    vague = [{"code": "TD_VAGUE_EXPECTATION", "case_id": f"TC-night-{index:03d}", "step_id": f"STEP-night-{index:03d}",
              "detail": "отказ виден только как флаг failed=true"} for index in range(56)]
    bounds = [{"code": "TD_BOUNDARY_MISSING", "requirement_id": f"CREQ-night-{index}", "parameter": "P-age", "detail": "max"}
              for index in range(5)]
    pair = [{"code": "TD_PAIR_UNCOVERED", "requirement_id": "CREQ-night-9", "detail": "C-a-1 × C-b-2"}]
    kept = capped_code_hints(sorted(vague + bounds + pair, key=lambda hint: {"TD_VAGUE_EXPECTATION": 1}.get(hint["code"], 3)))
    assert [hint for hint in kept if hint["code"] in {"TD_BOUNDARY_MISSING", "TD_PAIR_UNCOVERED"}] == bounds + pair
    def more_of(rows):
        return [hint for hint in rows if hint["code"] == "TD_MORE"]

    shown = [hint for hint in kept if hint["code"] == "TD_VAGUE_EXPECTATION"]
    assert 0 < len(shown) < GROUP_LIMIT and len(json.dumps(shown + more_of(kept), ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= GROUP_BYTES
    more = more_of(kept)
    assert more == [{"code": "TD_MORE", "detail": f"и ещё {56 - len(shown)} (ожидания)"}]
    assert len(kept) <= HINT_LIMIT and len(json.dumps(kept, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= HINT_BYTES


def test_the_receipt_hints_without_overflow_are_the_same_as_before() -> None:
    from tools.test_design_hints import capped, capped_code_hints

    hints = [{"code": "TD_NEGATIVE_MISSING", "requirement_id": "CREQ-a", "parameter": "P-x", "detail": "C-neg"},
             {"code": "TD_VAGUE_EXPECTATION", "case_id": "TC-a", "step_id": "STEP-a", "detail": "флаг"}]
    assert capped_code_hints(hints) == capped(hints) == hints
