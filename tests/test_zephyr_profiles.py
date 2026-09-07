from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_requirement_traceability import canonical_fixture


V3 = "zephyr-scale-step-row-24-v3"
V4 = "zephyr-scale-step-row-24-v4"


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


def test_new_projection_public_defaults_are_exact_v4_and_receipts_use_preview_names(tmp_path: Path) -> None:
    from tools.publish_test_case_bundle import _parser, build_bundle, publish_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = canonical_fixture()
    bundle = build_bundle(document)
    assert bundle.preview_bytes.startswith(b"<!doctype html>")
    assert bundle.csv_bytes == render_zephyr_csv(document).payload
    assert bundle.markdown_bytes.startswith(b"# ")
    assert next(action.default for action in _parser()._actions if action.dest == "csv_profile") == V4
    receipt = publish_bundle(document, tmp_path)
    assert receipt.csv_profile == V4
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


def test_v4_csv_preserves_v3_transport_bytes_and_xml_receipt_is_observed_unverified(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.export_test_cases_xml import main as export_xml
    from tools.publish_test_case_bundle import build_bundle
    from tools.test_case_projections import render_zephyr_csv

    document = canonical_fixture()
    assert render_zephyr_csv(document).payload == build_bundle(document, V3, _allow_historical=True).csv_bytes
    source = tmp_path / "canonical.json"
    source.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert export_xml(["--input", str(source), "--output", str(tmp_path / "candidate.xml")]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["profile"] == "zephyr-scale-xml-observed-v1"
    assert receipt["profile_status"] == "observed_unverified"


def test_orchestrator_schema_is_5_and_only_accepts_preview_receipt_fields() -> None:
    schema = json.loads((Path.cwd() / "schemas" / "orchestrator-output.schema.json").read_text(encoding="utf-8"))
    receipt = schema["$defs"]["receipt"]
    assert schema["properties"]["schema_version"] == {"const": "5.0.0"}
    assert {"preview_path", "preview_sha256"}.issubset(receipt["required"])
    assert "markdown_path" not in receipt["properties"]
    result = schema["$defs"]["result"]
    assert "final_status" not in result["properties"]
    assert "company_execution_sha256" not in result["properties"]
    assert result["properties"]["lifecycle"]["$ref"] == "#/$defs/lifecycle"


def test_finalize_orchestration_emits_schema_valid_v5_output(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import tools.automation_validation as automation_validation
    import tools.build_trace_document as trace_builder
    import tools.orchestrate_test_case_revision as orchestration
    import tools.trace_check as trace_check
    from tools.publish_test_case_bundle import Receipt
    from tools.schema_validation import schema_diagnostics

    document = canonical_fixture()
    source = orchestration._source(document)
    digest = "sha256:" + "a" * 64
    receipt = Receipt(document["document_id"], 1, V4, str(tmp_path / "canonical.json"), str(tmp_path / "preview.html"), str(tmp_path / "cases.csv"), digest, digest, digest)
    automation = {"artifacts": {"automation_status": "BLOCKED", "source": source}}
    review = {"artifacts": {"autotest_review": {"source": source}}}
    audit = {
        "schema_version": "5.0.0", "stage": "trace-check", "valid": True,
        "trace_audit": {"verdict": "PASS", "trace_sha256": digest, "source": source, "lifecycle": {"projection": "PRE_FINALIZATION", "verification": "NOT_APPLICABLE"}, "required_symbol_pairs": [], "relation_count": 0, "errors": []},
        "errors": [], "warnings": [], "summary": {name: 0 for name in ("requirements", "test_cases", "steps", "expectations", "assertions", "files", "symbols", "relations", "manual_dispositions", "evidence")},
    }
    trace = {"source": source, "lifecycle": {"projection": "PRE_FINALIZATION", "verification": "NOT_APPLICABLE"}}
    monkeypatch.setattr(orchestration, "_readback_rows", lambda *_args: [])
    monkeypatch.setattr(orchestration, "_review_rows", lambda *_args: [])
    monkeypatch.setattr(orchestration, "validate_review_decision", lambda *_args: [])
    monkeypatch.setattr(orchestration, "select_effective_document", lambda *_args: document)
    monkeypatch.setattr(automation_validation, "validate_automation_artifact", lambda *_args: [])
    monkeypatch.setattr(automation_validation, "validate_accepted_autotest_review", lambda *_args: [])
    monkeypatch.setattr(automation_validation, "required_symbol_pairs", lambda *_args: [])
    monkeypatch.setattr(automation_validation, "automation_sha256", lambda *_args: digest)
    monkeypatch.setattr(trace_builder, "validate_trace_document", lambda *_args: [])
    monkeypatch.setattr(trace_check, "check", lambda *_args, **_kwargs: audit)

    result = orchestration.finalize_orchestration(document, {"artifacts": {"validation_report": {}}}, document, receipt, automation, review, None, trace, audit)

    assert result["schema_version"] == "5.0.0"
    projection = result["artifacts"]["orchestration_result"]
    assert projection["lifecycle"] == trace["lifecycle"]
    assert "final_status" not in projection and "company_execution_sha256" not in projection
    # The public result is frozen after the producer's own schema validation;
    # validate its JSON-shaped readback rather than treating MappingProxyType
    # as a mutable JSON object.
    assert schema_diagnostics(orchestration._plain(result), orchestration.OUTPUT_SCHEMA, orchestration.ROOT) == []
