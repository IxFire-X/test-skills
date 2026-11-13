"""FaT-PA: the cases with canonical blockers stay manual, the rest are automated (run-two-repos 79e14cc2: 4 blocked
steps of 114 cases gave 0 autotests).

- the automation lists the blocked cases in ``excluded_cases``; the code checks the list against the canon (one reading:
  ``canonical_document.blocked_cases``) — a case without a blocker cannot be hidden, a blocked one cannot be left out;
- a PASS of the automated subset closes PARTIAL / AUTOMATION_PARTIAL, accepted=false (the frozen «no unresolved blocker»);
- the split «automated N of M, manual K: case — blocker code» is printed, reported and shown to the reviewer;
- an artifact without the field, and «every case blocked», take the old BLOCKED path unchanged."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tests.test_automation_revision_budget import automated_document, automation

pytestmark = pytest.mark.contract


def _renamed(value, old: str, new: str):
    text = json.dumps(value, ensure_ascii=False)
    for prefix in ("TC-", "STEP-", "EXP-", "ASSERT-"):
        text = text.replace(f'"{prefix}batch-a-{old}"', f'"{prefix}batch-a-{new}"')
    return json.loads(text)


def _blocked(step: dict, suffix: str) -> None:
    step["operation"] = None
    step["automation_blockers"] = [{"blocker_id": f"BLOCK-{suffix}", "code": "UNRESOLVED_OPERATION", "field_path": "/operation",
                                    "reason": "Адрес сервиса не подтверждён.", "provenance": ["test"]}]


def document(blocked: tuple[int, ...] = (3,), *, ready_step_in_blocked: bool = False) -> dict:
    """Three cases like the fixture case; the ``blocked`` ones (1-based) carry a blocker on their last step."""
    base = automated_document()
    template = base["test_cases"][0]
    cases = []
    for number in (1, 2, 3):
        case = _renamed(copy.deepcopy(template), "001", f"00{number}")
        case["display_order"] = number
        if number in blocked:
            if ready_step_in_blocked:
                second = _renamed(copy.deepcopy(case["steps"][0]), f"00{number}", f"00{number}b")
                second["display_order"] = 2
                _blocked(second, f"{number}")
                case["steps"].append(second)
            else:
                _blocked(case["steps"][0], f"{number}")
        cases.append(case)
    base["test_cases"] = cases
    return base


def partial_artifact(doc: dict, excluded: list[str] | None, *, automated: tuple[int, ...] = (1, 2), **bindings) -> dict:
    """GENERATED automation of the ``automated`` cases, one test function each; ``excluded`` None = an old artifact."""
    artifact = automation(doc, **bindings)
    body = artifact["artifacts"]
    names = [f"test_case_{number}" for number in automated]
    content = "".join(f"def {name}():\n    assert True\n\n\n" for name in names).rstrip() + "\n"
    body["generated_files"][0].update(content=content, content_digest="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
    body["generated_symbols"] = [{"file_id": "FILE-products", "symbol_id": f"SYMBOL-{number}",
                                  "locator": {"kind": "python_module_function", "function_name": f"test_case_{number}"}} for number in automated]
    body["implementation_relations"] = [
        relation for number in automated for relation in (
            {"kind": "operation", "case_id": f"TC-batch-a-00{number}", "step_id": f"STEP-batch-a-00{number}",
             "file_id": "FILE-products", "symbol_id": f"SYMBOL-{number}"},
            {"kind": "assertion", "case_id": f"TC-batch-a-00{number}", "step_id": f"STEP-batch-a-00{number}",
             "expectation_id": f"EXP-batch-a-00{number}", "assertion_id": f"ASSERT-batch-a-00{number}",
             "file_id": "FILE-products", "symbol_id": f"SYMBOL-{number}"})]
    if excluded is not None:
        body["excluded_cases"] = [{"case_id": case_id} for case_id in excluded]
    return artifact


def _codes(artifact: dict, doc: dict) -> list[str]:
    from tools.automation_validation import validate_automation_artifact

    return [row["code"] for row in validate_automation_artifact(artifact, doc)]


# --------------------------------------------------------------------------------------
# the validator
# --------------------------------------------------------------------------------------


def test_the_fixture_document_is_valid_and_its_blocked_cases_are_the_one_reading() -> None:
    from tools.canonical_document import blocked_cases, blocker_count, validate_canonical_document

    doc = document((2, 3))
    assert validate_canonical_document(doc) == []
    assert blocked_cases(doc) == [{"case_id": "TC-batch-a-002", "steps": [{"step_id": "STEP-batch-a-002", "codes": ["UNRESOLVED_OPERATION"]}]},
                                  {"case_id": "TC-batch-a-003", "steps": [{"step_id": "STEP-batch-a-003", "codes": ["UNRESOLVED_OPERATION"]}]}]
    assert blocker_count(doc) == 2


def test_a_partial_automation_with_the_blocked_case_excluded_is_valid() -> None:
    doc = document((3,))
    assert _codes(partial_artifact(doc, ["TC-batch-a-003"]), doc) == []


@pytest.mark.parametrize(("blocked", "excluded", "automated", "code"), [
    ((3,), [], (1, 2), "AUTOMATION_EXCLUSION_MISSING"),                       # a blocked case is left out of the list
    ((3,), ["TC-batch-a-002", "TC-batch-a-003"], (1,), "AUTOMATION_EXCLUSION_WITHOUT_BLOCKER"),  # a hard case hidden
    ((2, 3), ["TC-batch-a-003", "TC-batch-a-002"], (1,), "AUTOMATION_EXCLUSION_ORDER"),
    ((2, 3), ["TC-batch-a-002", "TC-batch-a-002", "TC-batch-a-003"], (1,), "AUTOMATION_EXCLUSION_ORDER"),
    ((1, 2, 3), ["TC-batch-a-001", "TC-batch-a-002", "TC-batch-a-003"], (), "AUTOMATION_NOTHING_TO_AUTOMATE"),
])
def test_an_exclusion_that_does_not_match_the_canon_is_refused_by_name(blocked, excluded, automated, code) -> None:
    doc = document(blocked)
    artifact = partial_artifact(doc, excluded, automated=automated)
    if not automated:
        body = artifact["artifacts"]
        body["generated_files"], body["generated_symbols"], body["implementation_relations"] = [], [], []
    assert code in _codes(artifact, doc)


def test_a_ready_step_of_an_excluded_case_is_not_automated() -> None:
    doc = document((3,), ready_step_in_blocked=True)
    assert _codes(partial_artifact(doc, ["TC-batch-a-003"]), doc) == []
    artifact = partial_artifact(doc, ["TC-batch-a-003"], automated=(1, 2, 3))
    assert "AUTOMATION_EXCLUDED_CASE_TARGET" in _codes(artifact, doc)


def test_an_old_artifact_and_every_case_blocked_keep_the_old_answers() -> None:
    doc = document((3,))
    old = _codes(partial_artifact(doc, None), doc)  # GENERATED over a blocker, no field: refused as before
    assert "AUTOMATION_BLOCKER_REQUIRES_BLOCKED" in old and not [code for code in old if "EXCLU" in code or "NOTHING" in code]
    every = document((1, 2, 3))
    blocked = automation(every)
    blocked["artifacts"].update(automation_status="BLOCKED", generated_files=[], generated_symbols=[], implementation_relations=[],
                                diagnostics=[{"path": "/artifacts", "code": "AUTOMATION_BLOCKED", "message": "Шаг заблокирован."}])
    assert _codes(blocked, every) == []
    blocked["artifacts"]["excluded_cases"] = [{"case_id": "TC-batch-a-001"}]
    assert "AUTOMATION_BLOCKED_CONTENT" in _codes(blocked, every)
    assert _codes(automation(automated_document()), automated_document()) == []  # no blocker, no field: unchanged


# --------------------------------------------------------------------------------------
# end to end: 3 cases, 1 blocked -> 2 autotests -> PASS -> PARTIAL
# --------------------------------------------------------------------------------------

_GENERATED = """import pytest

from sample import combine


@pytest.mark.native
def test_selected(plugin_value: str, conftest_value: str) -> None:
    assert combine(plugin_value, conftest_value) == "from-project-plugin:from-conftest"


def test_case_2() -> None:
    assert True
"""


def partial_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    """The test_finish fixture project with the three-case document and its partial automation, statically reviewed."""

    import tests.test_project_native_pytest as native
    from tests.test_finish import _project

    doc = document((3,))
    with monkeypatch.context() as patched:
        patched.setattr(native, "automated_document", lambda: copy.deepcopy(doc))
        patched.setattr(native, "automation", lambda value, **bindings: partial_artifact(value, ["TC-batch-a-003"], **bindings))
        original = native._reviewed_inputs
        patched.setattr(native, "_reviewed_inputs", lambda project, **kw: original(project, generated_content=_GENERATED, materialize=False, **kw))
        import tests.test_finish as finish

        patched.setattr(finish, "_reviewed_inputs", native._reviewed_inputs)
        project, run_root, attempt_id, _values = _project(tmp_path)
    return project, run_root, attempt_id


@pytest.mark.slow
def test_three_cases_one_blocked_run_two_autotests_and_close_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    from tests.p4_flow import run_the_build
    from tools.pilot_state import read_terminal_result

    project, run_root, attempt_id = partial_project(tmp_path, monkeypatch)
    capsys.readouterr()
    code, output = run_the_build(project, run_root, capsys)
    assert output["verdict"] == "PASS" and output["finalization"] == "complete", output
    result = read_terminal_result(run_root, attempt_id)
    assert (result["completion"], result["verification"], result["reason_code"], result["accepted"]) == (
        "PARTIAL", "PASS", "AUTOMATION_PARTIAL", False), result
    assert result["evidence"]["blocker_count"] == 1 and result["evidence"]["generated_retained_count"] == 1, result
    assert code != 0
    split = output["automation_split"]
    assert split["automated"] == ["TC-batch-a-001", "TC-batch-a-002"] and split["total"] == 3, split
    assert split["manual"] == [{"case_id": "TC-batch-a-003", "steps": [{"step_id": "STEP-batch-a-003", "codes": ["UNRESOLVED_OPERATION"]}]}]
    assert "автоматизировано 2 из 3" in split["summary"] and "TC-batch-a-003 — UNRESOLVED_OPERATION" in split["summary"], split

    # finish answers the same closed result with the same split
    from tools.run_pipeline import main

    capsys.readouterr()
    main(["finish", "--project", str(project), "--run", run_root.name])
    again = json.loads(capsys.readouterr().out)
    assert again["reason_code"] == "AUTOMATION_PARTIAL" and again["automation_split"] == split, again

    # the report says it is not the whole set
    from tools.run_report import build_report, render_markdown

    report, _warnings = build_report(project, run_root.name)
    assert report["automation_split"] == split and report["counts"]["accepted"] is False
    assert "- Автоматизация частичная: автоматизировано 2 из 3 кейсов, вручную 1: TC-batch-a-003 — UNRESOLVED_OPERATION" in render_markdown(report)

    # Zephyr: «да» only for the automated cases
    from tools.zephyr_template_export import _automation_facts

    assert _automation_facts(run_root) == (True, {"TC-batch-a-001", "TC-batch-a-002"})

    # the published trace: its exclusion list is checked against the canon, never taken on its word
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical
    from tools.trace_check import check

    trace = dict(read_attempt_receipt(run_root, attempt_id, "execution-trace", "ARTIFACT_READ_BACK")["record"]["payload"])
    canon = read_effective_canonical(run_root, attempt_id)["document"]
    assert check(trace, require_execution=True, canonical=canon)["valid"] is True
    assert "TRACE_EXCLUSION_UNVERIFIED" in check(trace, require_execution=True)["trace_audit"]["errors"]
    forged = {**trace, "excluded_cases": [{"case_id": "TC-batch-a-002"}, {"case_id": "TC-batch-a-003"}]}  # a ready case hidden
    errors = check(forged, require_execution=True, canonical=canon)["trace_audit"]["errors"]
    assert {"TRACE_EXCLUSION_MISMATCH", "TRACE_EXCLUSION_WITHOUT_BLOCKER", "TRACE_EXCLUDED_CASE"} <= set(errors), errors
    dropped = {**trace, "excluded_cases": []}  # the blocked case left out
    assert "TRACE_GENERATED_BRANCH" in check(dropped, require_execution=True, canonical=canon)["trace_audit"]["errors"]


def test_the_autotest_view_names_the_manual_cases_only_when_there_are_blockers() -> None:
    from tools.canonical_document import document_sha256
    from tools.review_view import render

    view = render(document((3,)), "autotest").decode("utf-8")
    assert "## Кейсы с блокерами — вручную, в excluded_cases" in view and "- TC-batch-a-003: STEP-batch-a-003 UNRESOLVED_OPERATION" in view
    assert "автоматизируется 2 из 3 кейсов" in view
    plain = automated_document()
    assert "Кейсы с блокерами" not in render(plain, "autotest").decode("utf-8")
    assert document_sha256(plain)  # the document itself is untouched by the view


def test_automation_partial_is_only_a_pass_of_the_subset(tmp_path: Path) -> None:
    """Only a PASS of the subset is AUTOMATION_PARTIAL; the tuple check refuses it anywhere else."""
    from tools.pilot_state import _result_facts

    base = {"run_id": "a" * 32, "attempt_id": "b" * 32, "attempt_state": "TERMINAL", "completion": "PARTIAL", "verification": "FAIL",
            "coverage": "FULL", "reason_code": "AUTOMATION_PARTIAL", "prior_stage_cause": "EXECUTION_FAIL"}
    with pytest.raises(ValueError):
        _result_facts({**base, **_evidence(blocker_count=1)}, "local-pilot-v1", external_cause_available=False)
    with pytest.raises(ValueError):
        _result_facts({**base, "verification": "PASS", **_evidence(blocker_count=0)}, "local-pilot-v1", external_cause_available=False)
    facts, accepted = _result_facts({**base, "verification": "PASS", **_evidence(blocker_count=1)}, "local-pilot-v1", external_cause_available=False)
    assert accepted is False and facts["reason_code"] == "AUTOMATION_PARTIAL"


def _evidence(*, blocker_count: int) -> dict:
    return {"canonical_schema_valid": True, "canonical_semantics_valid": True, "canonical_provenance_valid": True,
            "reviewer_session_complete": True, "authoritative_verdict": "ACCEPTED", "authoritative_verdict_count": 1,
            "reviewer_pre_verdict_abort": False, "reviewer_isolation_state": "verified", "blocker_count": blocker_count,
            "trace_valid": True, "finalization_completed": True, "finalization_read_back": True, "finalization_valid": True,
            "materialization_applicability": "REQUIRED", "execution_applicability": "REQUIRED", "automation_accepted": True,
            "generated_required_count": 1, "generated_materialized_count": 1, "generated_retained_count": 1,
            "exact_target_pass": True, "mixed_manual_traceable": False, "operational_reliable": True}


# --------------------------------------------------------------------------------------
# FaT-PA2: «вручную» is every case without code; a blocker the operators express is a WARNING
# --------------------------------------------------------------------------------------


def test_a_case_of_manual_only_steps_is_manual_too_and_the_counts_add_up() -> None:
    from tools.automation_validation import validate_automation_artifact
    from tools.canonical_document import validate_canonical_document
    from tools.finalize_attempt import split_cases

    doc = document((3,))
    step = doc["test_cases"][1]["steps"][0]
    step.update(manual_only=True, manual_reason="Проверяется глазами на стенде.", operation=None, inputs=[], outputs=[])
    for expectation in step["expectations"]:
        expectation["assertions"] = []
    assert validate_canonical_document(doc) == []
    artifact = partial_artifact(doc, ["TC-batch-a-003"], automated=(1,))
    artifact["artifacts"]["manual_dispositions"] = [{"case_id": "TC-batch-a-002", "step_id": "STEP-batch-a-002"}]
    assert validate_automation_artifact(artifact, doc) == []
    split = split_cases(doc, artifact)
    assert split["automated"] == ["TC-batch-a-001"] and split["total"] == 3
    assert split["manual"] == [{"case_id": "TC-batch-a-002", "steps": [{"step_id": "STEP-batch-a-002", "codes": ["manual_only"]}]},
                               {"case_id": "TC-batch-a-003", "steps": [{"step_id": "STEP-batch-a-003", "codes": ["UNRESOLVED_OPERATION"]}]}]
    assert len(split["automated"]) + len(split["manual"]) == split["total"]
    assert split["summary"] == ("автоматизировано 1 из 3 кейсов, вручную 2: TC-batch-a-002 — manual_only; "
                                "TC-batch-a-003 — UNRESOLVED_OPERATION")


def test_an_expressible_blocker_is_a_warning_in_the_view_and_the_reviewer_skill() -> None:
    from tools.review_view import render

    view = render(document((3,)), "autotest").decode("utf-8")
    assert "замечание WARNING: вопрос к следующему прогону, автоматизацию не отклоняет" in view and "BLOCKING" not in view
    skill = (Path(__file__).resolve().parents[1] / "skills" / "autotest-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    rule = next(line for line in skill.splitlines() if "excluded_cases" in line)
    assert "is a WARNING finding" in rule and "is a BLOCKING finding" not in rule
