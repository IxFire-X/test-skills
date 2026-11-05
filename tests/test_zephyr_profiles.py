from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_requirement_traceability import canonical_fixture

PACK_ROOT = Path(__file__).resolve().parents[1]  # V0 (A6-3): the package, not the directory the run started in


V3 = "zephyr-scale-step-row-24-v3"
V4 = "zephyr-scale-step-row-24-v4"
V5 = "zephyr-scale-step-row-24-v5"


def test_publication_adds_readable_markdown_without_overwriting_a_changed_copy(tmp_path: Path) -> None:
    from tools.publish_test_case_bundle import BundleMismatchError, publish_bundle, verify_bundle
    from tools.test_case_projections import render_markdown

    document = canonical_fixture()
    original = json.dumps(document, ensure_ascii=False)
    receipt = publish_bundle(document, tmp_path)
    markdown = Path(receipt.json_path).with_suffix(".md")
    text = markdown.read_text(encoding="utf-8")
    assert markdown.read_bytes() == render_markdown(document).payload
    assert "## ТК-1." in text and "### Шаг 1" in text
    assert document["test_cases"][0]["title"] in text
    assert "**Предусловия:**" in text and "**Ожидаемый результат**" in text
    assert json.dumps(document, ensure_ascii=False) == original
    assert publish_bundle(document, tmp_path) == receipt == verify_bundle(document, tmp_path)
    markdown.write_text("Изменённая вручную копия", encoding="utf-8")
    with pytest.raises(BundleMismatchError, match="markdown"):
        publish_bundle(document, tmp_path)
    assert markdown.read_text(encoding="utf-8") == "Изменённая вручную копия"


def test_new_projection_public_defaults_are_exact_v5_and_receipts_use_preview_names(tmp_path: Path) -> None:
    import inspect

    from tools.publish_test_case_bundle import build_bundle, publish_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = canonical_fixture()
    bundle = build_bundle(document)
    assert bundle.preview_bytes.startswith(b"<!doctype html>")
    assert bundle.csv_bytes == render_zephyr_csv(document).payload
    assert bundle.markdown_bytes.startswith(b"# ")
    assert inspect.signature(build_bundle).parameters["csv_profile"].default == V5
    receipt = publish_bundle(document, tmp_path)
    assert receipt.csv_profile == V5
    assert receipt.preview_path.endswith(".html")
    assert receipt.preview_sha256.startswith("sha256:")
    assert "markdown" not in " ".join(receipt.__dataclass_fields__)


def test_historical_profile_is_rejected_for_new_publication_but_allowed_only_by_explicit_verification(tmp_path: Path) -> None:
    from tools.publish_test_case_bundle import BundleMismatchError, publish_bundle, verify_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = canonical_fixture()
    with pytest.raises(ValueError, match="historical"):
        publish_bundle(document, tmp_path, V3)
    with pytest.raises(ValueError, match="verification-only"):
        render_zephyr_csv(document, V3)
    with pytest.raises(BundleMismatchError):
        verify_bundle(document, tmp_path, V3)


def test_v4_csv_preserves_v3_transport_bytes() -> None:
    from tools.publish_test_case_bundle import build_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = canonical_fixture()
    assert render_zephyr_csv(document, V4).payload == build_bundle(document, V3, _allow_historical=True).csv_bytes


def test_orchestrator_schema_is_5_and_only_accepts_preview_receipt_fields() -> None:
    schema = json.loads((PACK_ROOT / "schemas" / "orchestrator-output.schema.json").read_text(encoding="utf-8"))
    receipt = schema["$defs"]["receipt"]
    assert schema["properties"]["schema_version"] == {"const": "5.0.0"}
    assert {"preview_path", "preview_sha256"}.issubset(receipt["required"])
    assert "markdown_path" not in receipt["properties"]
    result = schema["$defs"]["result"]
    assert "final_status" not in result["properties"]
    assert "company_execution_sha256" not in result["properties"]
    assert result["properties"]["lifecycle"]["$ref"] == "#/$defs/lifecycle"