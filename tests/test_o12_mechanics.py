"""V6 / O-12: the mechanics of a generator fragment is the code's work.  The model leaves out display_order, the step, input, expectation and
assertion ids, the context receipt, the defaults (sensitive, manual_*, automation_blockers) and the semantic_type the value or the capability
fixes; the code puts them; the stored answer is the full form; the canon and every projection are the bytes of the full fragment."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan

NS, CASE = "B1", "TC-B1-payment"
SUFFIX = "B1-payment"


def _synthetic():
    context = _context_receipt()
    header = _header()
    plan = _plan(header, context)
    return plan, header, context


def _capabilities() -> list[dict]:
    string = {"kind": "json", "type": "string"}
    integer = {"kind": "json", "type": "integer"}
    return [
        {"capability_id": "CAP-B1-pay", "adapter": "native", "action": "pay", "provenance": ["source:pay"],
         "arguments": [{"name": "amount", "semantic_type": integer, "required": True}, {"name": "token", "semantic_type": string, "required": True}],
         "results": [{"name": "ok", "semantic_type": {"kind": "json", "type": "boolean"}}, {"name": "order_id", "semantic_type": integer}]},
        {"capability_id": "CAP-B1-status", "adapter": "native", "action": "status", "provenance": ["source:status"],
         "arguments": [{"name": "order_id", "semantic_type": integer, "required": True}],
         "results": [{"name": "state", "semantic_type": string}]},
    ]


def _full_case(plan_fragment: dict) -> dict:
    """The case in the full form — every id by the scheme, every default written."""
    base = copy.deepcopy(plan_fragment["test_cases"][0])
    string, integer = {"kind": "json", "type": "string"}, {"kind": "json", "type": "integer"}
    step1 = {
        "step_id": f"STEP-{SUFFIX}-01", "display_order": 1, "action": "Оплатить заказ", "test_data": "Сумма 100, токен оплаты", "manual_only": False,
        "manual_reason": None, "automation_blockers": [], "operation": {"kind": "project_action", "capability_id": "CAP-B1-pay"},
        "inputs": [
            {"input_id": f"INPUT-{SUFFIX}-01-1", "display_order": 1, "target": {"location": "arg", "name": "amount", "sensitive": False},
             "source": {"kind": "literal", "value": 100}, "semantic_type": integer},
            {"input_id": f"INPUT-{SUFFIX}-01-2", "display_order": 2, "target": {"location": "arg", "name": "token", "sensitive": True},
             "source": {"kind": "secret_handle", "handle": "payment_token", "safe_label": "токен оплаты"}, "semantic_type": string,
             "type_provenance": ["source:token"]},
        ],
        "outputs": [
            {"output_id": "order_id", "display_order": 1, "source": {"kind": "project_result", "name": "order_id"}, "semantic_type": integer},
            {"output_id": "ok", "display_order": 2, "source": {"kind": "project_result", "name": "ok"}, "semantic_type": {"kind": "json", "type": "boolean"}},
        ],
        "expectations": [{"expectation_id": f"EXP-{SUFFIX}-01-1", "display_order": 1, "text": "Оплата прошла", "assertions": [
            {"assertion_id": f"ASSERT-{SUFFIX}-01-1-1", "display_order": 1, "actual": {"kind": "project_result", "name": "ok"}, "operator": "equals",
             "expected": {"kind": "literal", "value": True}}]}],
    }
    step2 = {
        "step_id": f"STEP-{SUFFIX}-02", "display_order": 2, "action": "Проверить состояние заказа", "test_data": "Номер заказа из шага 1", "manual_only": False,
        "manual_reason": None, "automation_blockers": [], "operation": {"kind": "project_action", "capability_id": "CAP-B1-status"},
        "inputs": [{"input_id": f"INPUT-{SUFFIX}-02-1", "display_order": 1, "target": {"location": "arg", "name": "order_id", "sensitive": False},
                    "source": {"kind": "step_output", "step_id": f"STEP-{SUFFIX}-01", "output_id": "order_id"}, "semantic_type": integer}],
        "outputs": [{"output_id": "state", "display_order": 1, "source": {"kind": "project_result", "name": "state"}, "semantic_type": string}],
        "expectations": [{"expectation_id": f"EXP-{SUFFIX}-02-1", "display_order": 1, "text": "Заказ оплачен", "assertions": [
            {"assertion_id": f"ASSERT-{SUFFIX}-02-1-1", "display_order": 1, "actual": {"kind": "project_result", "name": "state"}, "operator": "equals",
             "expected": {"kind": "literal", "value": "PAID"}}]}],
    }
    step3 = {
        "step_id": f"STEP-{SUFFIX}-03", "display_order": 3, "action": "Сверить квитанцию глазами", "test_data": "Квитанция на экране", "manual_only": True,
        "manual_reason": "Проверка вручную", "automation_blockers": [], "operation": None, "inputs": [], "outputs": [],
        "expectations": [{"expectation_id": f"EXP-{SUFFIX}-03-1", "display_order": 1, "text": "Квитанция читаема", "assertions": []}],
    }
    base["steps"] = [step1, step2, step3]
    base["case_id"] = CASE
    return base


def _full_fragment(plan: dict, context: dict) -> dict:
    fragment = _fragment(plan, context, 0)
    fragment["operation_capabilities"] = _capabilities()
    fragment["test_cases"] = [_full_case(fragment)]
    fragment["test_cases"][0]["display_order"] = 1
    fragment["requirements"][0]["display_order"] = 1
    fragment.pop("digest", None)
    return fragment


def _lean(full: dict) -> dict:
    """What the model writes: the full form without anything the code puts."""
    lean = copy.deepcopy(full)
    for key in ("plan_digest", "header_digest", "context_receipt_digest", "context_receipt", "diagnostics"):
        lean.pop(key, None)
    for row in [*lean["requirements"], *lean["test_cases"]]:
        row.pop("display_order", None)
    for case in lean["test_cases"]:
        for step in case["steps"]:
            for key in ("step_id", "display_order"):
                step.pop(key, None)
            if step["manual_only"] is False:
                step.pop("manual_only")
                step.pop("manual_reason")
                step.pop("automation_blockers")
            for item in step["inputs"]:
                for key in ("input_id", "display_order", "semantic_type"):
                    item.pop(key, None)
                item["target"].pop("sensitive", None)
            for item in step["outputs"]:
                for key in ("output_id", "display_order", "semantic_type"):
                    item.pop(key, None)
            for expectation in step["expectations"]:
                expectation.pop("expectation_id", None)
                expectation.pop("display_order", None)
                for assertion in expectation["assertions"]:
                    assertion.pop("assertion_id", None)
                    assertion.pop("display_order", None)
    return lean


def _sealed(plan: dict, fragment: dict) -> dict:
    from tools.batch_assembly import fill_fragment_bindings

    return fill_fragment_bindings(fragment, plan)


def _without_seal(fragment: dict) -> dict:
    return {key: value for key, value in fragment.items() if key != "digest"}


# --- the fill ---------------------------------------------------------------------------------------------------------

def test_the_lean_fragment_is_filled_into_the_full_one() -> None:
    from tools.batch_assembly import fill_fragment_mechanics

    plan, _header_, context = _synthetic()
    full = _full_fragment(plan, context)
    lean = _lean(full)
    assert len(json.dumps(lean, ensure_ascii=False)) < 0.7 * len(json.dumps(full, ensure_ascii=False))
    filled, summary = fill_fragment_mechanics(lean)
    expected = {key: value for key, value in full.items() if key not in ("plan_digest", "header_digest", "context_receipt_digest", "context_receipt")}
    assert _without_seal(filled) == expected, (filled, expected)
    assert any(item.startswith("display_order ×") for item in summary) and any(item.startswith("step_id ×") for item in summary), summary
    assert any(item.startswith("semantic_type ×") for item in summary), summary


def test_a_full_fragment_is_filled_into_itself_and_says_nothing() -> None:
    from tools.batch_assembly import fill_fragment_mechanics

    plan, _header_, context = _synthetic()
    full = _full_fragment(plan, context)
    filled, summary = fill_fragment_mechanics(copy.deepcopy(full))
    assert filled == full and summary == []


def test_the_value_of_the_model_is_kept_where_it_is_not_mechanics() -> None:
    from tools.batch_assembly import fill_fragment_mechanics

    plan, _header_, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    lean["test_cases"][0]["steps"][0]["inputs"][0]["semantic_type"] = {"kind": "json", "type": "number"}  # the model says number for 100
    lean["test_cases"][0]["steps"][0]["inputs"][0]["target"]["sensitive"] = True
    lean["test_cases"][0]["steps"][1]["outputs"][0]["output_id"] = "own_name"
    filled, _summary = fill_fragment_mechanics(lean)
    first = filled["test_cases"][0]["steps"][0]["inputs"][0]
    assert first["semantic_type"] == {"kind": "json", "type": "number"} and first["target"]["sensitive"] is True
    assert filled["test_cases"][0]["steps"][1]["outputs"][0]["output_id"] == "own_name"


def test_a_type_that_cannot_be_derived_is_not_made_up() -> None:
    from tools.batch_assembly import fill_fragment_mechanics

    plan, _header_, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    step = lean["test_cases"][0]["steps"][0]
    step["operation"] = None
    step["manual_only"] = True
    step["manual_reason"] = "Нет операции"
    step["automation_blockers"] = []
    step["inputs"][1]["source"] = {"kind": "fixture", "name": "payment_fixture"}  # fixture: no literal, no capability to take the type from
    filled, _summary = fill_fragment_mechanics(lean)
    assert "semantic_type" not in filled["test_cases"][0]["steps"][0]["inputs"][1]


def test_the_number_of_the_step_in_the_ids_is_the_position_in_the_case() -> None:
    from tools.batch_assembly import fill_fragment_mechanics

    plan, _header_, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    filled, _summary = fill_fragment_mechanics(lean)
    steps = filled["test_cases"][0]["steps"]
    assert [step["step_id"] for step in steps] == [f"STEP-{SUFFIX}-01", f"STEP-{SUFFIX}-02", f"STEP-{SUFFIX}-03"]
    assert steps[1]["inputs"][0]["input_id"] == f"INPUT-{SUFFIX}-02-1" and steps[1]["expectations"][0]["assertions"][0]["assertion_id"] == f"ASSERT-{SUFFIX}-02-1-1"


# --- the check of a fragment and the canon ------------------------------------------------------------------------------------

def test_validate_fragment_accepts_the_lean_fragment_and_names_what_it_put() -> None:
    from tools.batch_assembly import validate_fragment

    plan, header, context = _synthetic()
    result = validate_fragment(_lean(_full_fragment(plan, context)), plan, header, context)
    assert "/context_receipt" in result["filled"] and any(item.startswith("step_id ×") for item in result["filled"]), result  # accepted, and says what it put


def test_the_canon_and_every_projection_of_the_lean_fragment_are_the_bytes_of_the_full_one() -> None:
    from tools.batch_assembly import assemble_candidate
    from tools.publish_test_case_bundle import build_bundle

    plan, header, context = _synthetic()
    full = _sealed(plan, _full_fragment(plan, context))
    full["context_receipt"] = context
    lean = _lean(_full_fragment(plan, context))
    lean["context_receipt"] = context  # assemble gets the recorded answer or the file with its receipt
    other = _fragment(plan, context, 1)
    candidate_full, receipt_full = assemble_candidate(header, plan, [copy.deepcopy(full), copy.deepcopy(other)])
    candidate_lean, receipt_lean = assemble_candidate(header, plan, [lean, copy.deepcopy(other)])
    assert candidate_lean == candidate_full and receipt_lean == receipt_full
    bundle_full, bundle_lean = build_bundle(candidate_full), build_bundle(candidate_lean)
    assert bundle_full == bundle_lean


def test_a_secret_literal_without_sensitive_is_judged_as_when_the_model_wrote_false() -> None:
    from tools.canonical_document import validate_canonical_document_all
    from tools.batch_assembly import assemble_candidate

    plan, header, context = _synthetic()
    full = _full_fragment(plan, context)
    step = full["test_cases"][0]["steps"][0]
    step["inputs"][1]["source"] = {"kind": "literal", "value": "p@ssw0rd-Secret!"}
    step["inputs"][1]["target"]["sensitive"] = False
    step["inputs"][1].pop("type_provenance")
    lean = _lean(full)
    other = _fragment(plan, context, 1)
    outcomes = []
    for fragment in (_sealed(plan, full), lean):
        fragment = copy.deepcopy(fragment)
        fragment["context_receipt"] = context
        try:
            candidate, _ = assemble_candidate(header, plan, [fragment, copy.deepcopy(other)])
            outcomes.append(validate_canonical_document_all(candidate))
        except Exception as error:  # noqa: BLE001 - the same refusal for both is the point
            outcomes.append(str(error))
    assert outcomes[0] == outcomes[1], outcomes


def test_a_wrong_display_order_is_refused_with_the_fix_that_removes_the_field() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    plan, header, context = _synthetic()
    full = _full_fragment(plan, context)
    full["test_cases"][0]["steps"][1]["display_order"] = 5
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(full, plan, header, context)
    rows = [row for row in refused.value.diagnostics if row["code"] == "CANONICAL_DISPLAY_ORDER"]
    assert rows and "удали поле display_order" in rows[0]["fix"], refused.value.diagnostics


def test_a_collision_of_a_written_id_with_a_put_one_is_refused() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    plan, header, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    lean["test_cases"][0]["steps"][2]["step_id"] = f"STEP-{SUFFIX}-01"  # step 1 is left to the code and gets exactly this id
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(lean, plan, header, context)
    assert any(row["code"] == "SEMANTIC_DUPLICATE_ID" for row in refused.value.diagnostics), refused.value.diagnostics


def test_a_reference_to_an_id_off_the_scheme_is_refused_with_the_scheme_in_the_fix() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    plan, header, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    lean["test_cases"][0]["steps"][1]["inputs"][0]["source"]["step_id"] = "STEP-B1-first"
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(lean, plan, header, context)
    assert any("STEP-" in row["fix"] and "NN" in row["fix"] for row in refused.value.diagnostics), refused.value.diagnostics


def test_a_missing_type_that_cannot_be_derived_says_so_in_the_fix() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    plan, header, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    step = lean["test_cases"][0]["steps"][0]
    step["operation"] = None
    step["manual_only"] = True
    step["manual_reason"] = "Нет операции"
    step["automation_blockers"] = []
    step["inputs"][1]["source"] = {"kind": "fixture", "name": "payment_fixture"}
    step["inputs"][1]["type_provenance"] = ["source:fixture"]
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(lean, plan, header, context)
    assert any("semantic_type" in row["fix"] and "не выводится" in row["fix"] for row in refused.value.diagnostics), refused.value.diagnostics


def test_validate_fragment_puts_the_context_receipt_it_was_given_and_a_different_one_is_refused() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    plan, header, context = _synthetic()
    lean = _lean(_full_fragment(plan, context))
    validate_fragment(copy.deepcopy(lean), plan, header, context)
    lean["context_receipt"] = dict(context, byte_count=context["byte_count"] + 1)
    with pytest.raises(BatchAssemblyError):
        validate_fragment(lean, plan, header, context)


# --- model-response ------------------------------------------------------------------------------------------------------

def test_model_response_stores_the_full_form_of_a_lean_answer_and_prints_the_summary(tmp_path: Path, capsys) -> None:
    from tests.test_batch_journal import _response, _run
    from tests.test_digest_traps import _generator_request
    from tests.test_review_requested import _api, _put

    run, plan_digest, header_digest, fragments = _run(tmp_path)
    plan = json.loads(Path(run["plan"]).read_bytes())
    context = json.loads(Path(run["context"]).read_bytes())
    first = fragments[0]["batch_id"]
    full = _full_fragment(plan, context)
    lean = _lean(full)
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    code, answered = _response(run, first, _put(run["tmp"] / "lean.json", lean))
    assert code == 0, answered
    filled = answered["result"]["filled"]
    assert "/context_receipt" in filled and any(item.startswith("display_order ×") for item in filled) and "/digest" in filled, filled
    code, stored = _api("read-model-artifact", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{first}",
                        "--digest", answered["result"]["content_digest"])
    assert code == 0, stored
    record = stored["result"]["artifact"]
    assert record["context_receipt"] == context and record["test_cases"][0]["steps"][0]["step_id"] == f"STEP-{SUFFIX}-01"
    assert {key: value for key, value in record.items() if key not in ("plan_digest", "header_digest", "context_receipt_digest", "context_receipt", "digest")} \
        == {key: value for key, value in full.items() if key not in ("plan_digest", "header_digest", "context_receipt_digest", "context_receipt", "digest")}


def test_the_context_digest_named_by_the_answer_never_picks_the_receipt_the_code_puts(tmp_path: Path) -> None:
    """The receipt put into a lean answer is the one the published plan binds to the batch; a digest of another receipt of the run in the answer
    is a refusal of the digest binding, and nothing is stored."""
    from tests.test_batch_journal import _response, _run
    from tests.test_digest_traps import _generator_request
    from tests.test_review_requested import _put
    from tools import pilot_state
    from tools.pilot_state import derive_state, publish_context_selection
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    run, plan_digest, header_digest, fragments = _run(tmp_path)
    root, attempt_id, project = Path(run["run_root"]), run["attempt_id"], run["project"]
    attempt = next(row for row in derive_state(root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    other = select_context_batches(inventory, project, [next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == "src/extra.py")],
                                   byte_budget=4096)[0]["receipt"]
    other_digest = publish_context_selection(root, attempt_id, other)["digest"]
    plan = json.loads(Path(run["plan"]).read_bytes())
    context = json.loads(Path(run["context"]).read_bytes())
    assert other_digest != context["digest"]
    lean = _lean(_full_fragment(plan, context))
    lean["context_receipt_digest"] = other_digest
    first = fragments[0]["batch_id"]
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    asked: list[str] = []
    real = pilot_state.read_context_selection

    def spy(run_root, attempt, digest):
        asked.append(str(digest))
        return real(run_root, attempt, digest)

    pilot_state.read_context_selection = spy
    try:
        code, refused = _response(run, first, _put(run["tmp"] / "other-context.json", lean))
    finally:
        pilot_state.read_context_selection = real
    assert code == 2 and refused["code"] == "MODEL_RESPONSE_DIGEST_MISMATCH", refused
    assert other_digest not in asked, asked  # the receipt the answer names is never read to be put into it
    assert not list((root / "model-stage-artifacts" / attempt_id).glob("*.json")) or all(
        json.loads(path.read_text(encoding="utf-8")).get("context_receipt_digest") != other_digest
        for path in (root / "model-stage-artifacts" / attempt_id).glob("*.json"))
    assert pilot_state.derive_state(root) is not None
