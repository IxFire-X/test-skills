"""FaT-TN: human projections name the test design technique in Russian; the machine field keeps its code."""
from __future__ import annotations

import copy
import json


def _with_bva() -> dict:
    from tests.test_review_view import _example

    document = copy.deepcopy(_example())
    document["test_cases"][0]["technique"] = "BVA"
    return document


def test_md_and_html_say_granichnye_znacheniya_and_json_keeps_bva() -> None:
    from tools.test_case_projections import render_html_preview, render_markdown

    document = _with_bva()
    assert "Техника: Граничные значения" in render_markdown(document).payload.decode("utf-8")
    assert "Техника: Граничные значения" in render_html_preview(document).payload.decode("utf-8")
    assert "Техника: BVA" not in render_markdown(document).payload.decode("utf-8")
    assert json.loads(json.dumps(document, ensure_ascii=False))["test_cases"][0]["technique"] == "BVA"


def test_every_technique_code_has_one_russian_name() -> None:
    from tools.test_design_hints import TECHNIQUE_NAMES, TECHNIQUES, technique_name

    assert set(TECHNIQUE_NAMES) == set(TECHNIQUES)
    assert technique_name("EP") == "Классы эквивалентности" and technique_name("OTHER") == "Другая техника"
