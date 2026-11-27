"""A value a setup prepares (``setup_output``, canonical 1.2.0) in the path and in an input of a step: the review view, the curl block and
every CSV profile are built — the projections once took it for a secret and fell on ``safe_label`` (KeyError), the live run of the stand stopped."""
from __future__ import annotations

from tests.test_setup_mode_b import setup_document
from tools.review_view import render
from tools.test_case_projections import _orders, render_html_preview, render_zephyr_csv, request_block


def _document() -> dict:
    document = setup_document()
    case = document["test_cases"][0]
    case["setup"][0]["outputs"] = [{"output_id": "ownerId", "display_order": 1, "source": {"kind": "http_body", "pointer": "/id"},
                                    "semantic_type": {"kind": "json", "type": "integer"}, "type_provenance": ["test"]}]
    step = case["steps"][0]
    step["operation"] = dict(step["operation"], path="/owners/{ownerId}")
    step["inputs"] = [{"input_id": "INPUT-setup-out-1", "display_order": 1, "target": {"location": "path", "name": "ownerId", "sensitive": False},
                       "source": {"kind": "setup_output", "setup_id": "SETUP-batch-a-001-01", "output_id": "ownerId"},
                       "semantic_type": {"kind": "json", "type": "integer"}}]
    return document


def test_a_setup_output_in_the_path_builds_every_view() -> None:
    document = _document()
    case = document["test_cases"][0]
    for profile in ("zephyr-scale-step-row-24-v4", "zephyr-scale-step-row-24-v5", "zephyr-scale-step-row-24-v6"):
        assert "ownerId" in render_zephyr_csv(document, profile).payload.decode("utf-8-sig"), profile
    assert "&lt;ownerId из подготовки к предусловию 1&gt;" in render_html_preview(document).payload.decode("utf-8")
    assert "SETUP-batch-a-001-01.ownerId" in render(document).decode("utf-8")
    assert "{{ownerId}} — ownerId из подготовки к предусловию 1" in request_block(case["steps"][0], _orders(case), case)
