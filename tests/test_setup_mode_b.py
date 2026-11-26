"""Mode B and the setup of a case (canonical 1.2.0): a setup helper performs it, nothing checks it, its failure is a cause of its own."""
from __future__ import annotations

from pathlib import Path

from tests.test_automation_revision_budget import automated_document, automation
from tools.build_trace_document import _construct
from tools.fixes import fix_for, human_for
from tools.quarantine import failure_groups, failure_note, setup_lines
from tools.schema_validation import schema_diagnostics
from tools.trace_check import check

PACK_ROOT = Path(__file__).resolve().parents[1]
SETUP = {"kind": "setup", "case_id": "TC-batch-a-001", "setup_id": "SETUP-batch-a-001-01", "file_id": "FILE-products", "symbol_id": "SYMBOL-setup"}


def setup_document() -> dict:
    document = automated_document()
    document["schema_version"] = "1.2.0"
    document["test_cases"][0]["setup"] = [{"setup_id": "SETUP-batch-a-001-01", "display_order": 1, "precondition_index": 0,
                                           "operation": dict(document["test_cases"][0]["steps"][0]["operation"], method="POST"), "inputs": [], "outputs": []}]
    return document


def setup_automation(document: dict, relation: dict | None = SETUP) -> dict:
    generated = automation(document)
    artifacts = generated["artifacts"]
    artifacts["generated_symbols"].append({"file_id": "FILE-products", "symbol_id": "SYMBOL-setup", "locator": {"kind": "python_module_function", "function_name": "prepare_products"}})
    if relation is not None:
        artifacts["implementation_relations"].insert(0, dict(relation))
    return generated


def _codes(generated: dict, document: dict) -> list[str]:
    from tools.automation_validation import validate_automation_artifact

    return [row["code"] for row in validate_automation_artifact(generated, document)]


def test_a_setup_helper_covers_the_setup() -> None:
    document = setup_document()
    assert _codes(setup_automation(document), document) == []


def test_an_automation_without_the_setup_helper_is_refused() -> None:
    document = setup_document()
    generated = setup_automation(document, None)
    generated["artifacts"]["generated_symbols"].pop()
    assert _codes(generated, document) == ["AUTOMATION_MISSING_SETUP_COVERAGE"]


def test_a_setup_helper_that_checks_is_refused() -> None:
    document = setup_document()
    generated = setup_automation(document, dict(SETUP, symbol_id="SYMBOL-products"))
    generated["artifacts"]["generated_symbols"].pop()
    assert _codes(generated, document) == ["AUTOMATION_SETUP_ASSERTED"]


def test_the_setup_relation_goes_before_the_steps_of_its_case() -> None:
    document = setup_document()
    generated = setup_automation(document)
    relations = generated["artifacts"]["implementation_relations"]
    relations.append(relations.pop(0))
    assert _codes(generated, document) == ["AUTOMATION_RELATION_ORDER"]


def test_the_trace_has_the_setup_without_checks() -> None:
    document = setup_document()
    trace = _construct(document, setup_automation(document), {}, None)
    assert trace["setups"] == [{"case_id": "TC-batch-a-001", "setup_id": "SETUP-batch-a-001-01", "display_order": 1}]
    assert schema_diagnostics(trace, PACK_ROOT / "schemas/trace-document.schema.json", PACK_ROOT) == []
    assert [row["code"] for row in check(trace)["errors"]] == ["TRACE_EXECUTION_REQUIRED"]  # no run in the fixture; nothing of the setup


def test_a_trace_without_setup_has_no_setup_rows() -> None:
    document = automated_document()
    assert "setups" not in _construct(document, automation(document), {}, None)


def test_a_failed_setup_is_its_own_cause_not_a_failed_check() -> None:
    ref, _message = failure_note("SetupFailed: SETUP-batch-a-001-01 POST /products answered 500")
    groups = failure_groups([{"file": "t.py", "method": "test_products", "ref": ref, "kind": "error", "message": ""}], setups=setup_lines(setup_document()))
    line = setup_document()["test_cases"][0]["preconditions"][0]
    assert ref == "SETUP-batch-a-001-01" and [group["cause"] for group in groups] == [
        f"Подготовка не удалась: SETUP-batch-a-001-01 «{line}» — 1 автотест(ов); проверки этих кейсов не выполнялись — сначала почините подготовку (адрес, данные, доступ)"]


def test_the_refusals_of_the_setup_say_the_approved_words() -> None:
    assert [(fix_for(code), human_for(code)) for code in ("AUTOMATION_MISSING_SETUP_COVERAGE", "AUTOMATION_SETUP_ASSERTED", "SETUP_PRECONDITION_UNKNOWN")] == [
        ("Автотест не выполняет подготовку <SETUP-id> кейса <TC-id>: вызови помощник подготовки до первого шага, проверять её не нужно",
         "Автотест пропускает подготовку данных из предусловия кейса"),
        ("Подготовка <SETUP-id> кейса <TC-id> проверяется в автотесте: убери проверку, помощник сам падает, если подготовка не удалась",
         "Автотест проверяет подготовку данных, а не поведение"),
        ("Подготовка <SETUP-id> ссылается на предусловие <N>, а у кейса их <M>", "Подготовка данных не привязана к предусловию кейса"),
    ]
