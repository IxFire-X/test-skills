"""P1 «Диагностика разом»: validate-fragment отдаёт все диагностики за один проход (связки первыми и фатально), у каждой
есть fix; механику (порядок id и категорий, digest) делает код; одна таблица code → fix; журнал отказов refusals.jsonl;
голые ValueError стали кодами; model-request пишет замер шага."""
from __future__ import annotations

import ast
import copy
import json
import re
from pathlib import Path

import pytest

from tests.test_batch_assembly import _context_receipt, _fragment, _header, _plan, _refresh, _self_digest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
BATCH_CODES = {"BATCH_ID_CONFLICT", "BATCH_PARTITION_CONFLICT", "BATCH_SEMANTIC_CONFLICT"}


def _synthetic() -> tuple[dict, dict, dict, dict]:
    context = _context_receipt()
    header = _header()
    plan = _plan(header, context)
    return _fragment(plan, context, 0), plan, header, context


def _blocked_step(fragment: dict) -> None:
    """A step blocked the wrong way: a blocker whose code/path pair is invalid, no /operation blocker, no assertion blocker."""
    step = fragment["test_cases"][0]["steps"][0]
    step["manual_only"], step["manual_reason"] = False, None
    step["automation_blockers"] = [{"blocker_id": "BLOCK-" + fragment["namespace"] + "-1", "code": "UNRESOLVED_ASSERTION",
                                    "field_path": "/inputs", "reason": "Нет данных.", "provenance": ["x"]}]


def _batch_one_violations() -> tuple[dict, dict, dict, dict]:
    """The shape of the work's batch 1: a wrong plan_digest, a wrong blocker, an uncovered requirement, a shuffled order."""
    fragment, plan, header, context = _synthetic()
    _blocked_step(fragment)
    extra = dict(fragment["requirements"][0], requirement_id=fragment["requirements"][0]["requirement_id"] + "-B", display_order=100)
    fragment["requirements"].append(extra)
    fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(extra["requirement_id"])
    fragment["test_cases"][0]["categories"] = ["functional", "positive"]
    fragment["plan_digest"] = "sha256:" + "9" * 64  # the sha256 of the plan file, not the plan's own digest
    fragment.pop("digest", None)
    return fragment, plan, header, context


def test_one_validate_fragment_shows_every_diagnostic_each_with_a_fix() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    fragment, plan, header, context = _batch_one_violations()
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(fragment, plan, header, context)
    rows = list(refused.value.diagnostics)
    codes = [row["code"] for row in rows]
    assert {"SEMANTIC_BLOCKER_PATH", "SEMANTIC_MISSING_OPERATION_BLOCKER", "SEMANTIC_MISSING_ASSERTION_BLOCKER",
            "BATCH_UNCOVERED_REQUIREMENT"} <= set(codes), codes
    assert any(row["path"] == "/plan_digest" for row in rows), rows
    assert all(set(row) >= {"path", "code", "message", "fix"} and row["fix"] for row in rows), rows
    assert "CANONICAL_CATEGORY_ORDER" not in codes  # the order is the code's work, not a refusal
    assert refused.value.code in BATCH_CODES and str(len(rows)) in str(refused.value)  # the envelope names how many there are


def test_the_bindings_stay_fatal_and_first() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    fragment, plan, header, context = _batch_one_violations()
    fragment["batch_id"] = "BATCH-unknown"
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(fragment, plan, header, context)
    assert refused.value.code == "BATCH_ID_CONFLICT"
    assert not getattr(refused.value, "diagnostics", ())  # a binding is refused alone, nothing is collected behind it
    fragment, plan, header, context = _batch_one_violations()
    fragment["owned_source_requirement_ids"] = ["SREQ-other"]
    with pytest.raises(BatchAssemblyError) as owned:
        validate_fragment(fragment, plan, header, context)
    assert owned.value.code == "BATCH_PARTITION_CONFLICT" and not getattr(owned.value, "diagnostics", ())


def test_a_wrong_plan_digest_says_remove_the_field_and_an_empty_one_passes() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    fragment, plan, header, context = _synthetic()
    wrong = copy.deepcopy(fragment)
    wrong["plan_digest"] = "sha256:" + "9" * 64
    _refresh(wrong)
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(wrong, plan, header, context)
    (row,) = refused.value.diagnostics
    assert row["path"] == "/plan_digest" and "удали поле plan_digest" in row["fix"] and "код поставит" in row["fix"], row
    for empty in ("", None):
        free = copy.deepcopy(fragment)
        free["plan_digest"] = empty
        free.pop("digest")
        validate_fragment(free, plan, header, context)


def test_a_given_self_digest_that_does_not_match_says_remove_the_field() -> None:
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    fragment, plan, header, context = _synthetic()
    fragment["digest"] = "sha256:" + "8" * 64
    with pytest.raises(BatchAssemblyError) as refused:
        validate_fragment(fragment, plan, header, context)
    (row,) = refused.value.diagnostics
    assert row["path"] == "/digest" and "удали поле digest" in row["fix"], row
    fragment.pop("digest")
    validate_fragment(fragment, plan, header, context)  # empty: the code puts it


def test_a_shuffled_order_passes_names_what_it_normalized_and_the_canon_is_byte_equal() -> None:
    from tools.batch_assembly import canonical_bytes, fill_fragment_bindings, normalize_fragment, validate_fragment

    fragment, plan, header, context = _synthetic()
    second = dict(fragment["requirements"][0], requirement_id=fragment["requirements"][0]["requirement_id"] + "-B", display_order=100)
    fragment["requirements"].append(second)
    fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(second["requirement_id"])
    first_id = fragment["requirements"][0]["requirement_id"]
    right = copy.deepcopy(fragment)
    right["test_cases"][0]["requirement_ids"] = [first_id, second["requirement_id"]]
    right["test_cases"][0]["categories"] = ["positive", "functional"]
    _refresh(right)
    mixed = copy.deepcopy(fragment)
    mixed["test_cases"][0]["requirement_ids"] = [second["requirement_id"], first_id]
    mixed["test_cases"][0]["categories"] = ["functional", "positive"]
    mixed.pop("digest", None)
    assert validate_fragment(right, plan, header, context) is None  # nothing to normalize: the output is unchanged
    result = validate_fragment(mixed, plan, header, context)
    assert result == {"normalized": ["/test_cases/0/categories", "/test_cases/0/requirement_ids"]}, result
    done, names = normalize_fragment(mixed)
    assert names == result["normalized"]
    assert canonical_bytes(fill_fragment_bindings(done, plan)) == canonical_bytes(right)


def test_model_response_stores_the_normalized_answer_and_names_it(tmp_path: Path) -> None:
    """model-response and validate-fragment share one normalization: the stored canon has the order assemble wants."""
    from tests.test_batch_journal import _response, _run
    from tests.test_digest_traps import _generator_request
    from tests.test_review_requested import _api, _put

    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first = fragments[0]["batch_id"]
    shuffled = {key: value for key, value in fragments[0].items() if key != "digest"}
    shuffled["test_cases"] = copy.deepcopy(shuffled["test_cases"])
    shuffled["test_cases"][0]["categories"] = ["functional", "positive"]
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    code, answered = _response(run, first, _put(run["tmp"] / "shuffled.json", shuffled))
    assert code == 0, answered
    assert answered["result"]["normalized"] == ["/test_cases/0/categories"] and answered["result"]["filled"] == ["/digest"], answered
    code, stored = _api("read-model-artifact", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{first}",
                        "--digest", answered["result"]["content_digest"])
    assert code == 0 and stored["result"]["artifact"]["test_cases"][0]["categories"] == ["positive", "functional"], stored


# --------------------------------------------------------------------------------------
# one table code -> fix
# --------------------------------------------------------------------------------------

def _calls(names: set[str]):
    for path in sorted((ROOT / "tools").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
                if name in names:
                    yield path, node, name


def test_the_fix_table_covers_every_code_of_the_three_error_families() -> None:
    from tools.fixes import FIXES

    missing: set[str] = set()
    for path, node, name in _calls({"BatchAssemblyError", "OrchestrationError", "HostStop"}):
        code = node.args[0] if node.args else None
        if not isinstance(code, ast.Constant):
            continue
        own_fix = name == "HostStop" and (len(node.args) >= 3 or any(item.arg == "fix" for item in node.keywords))
        if not own_fix and code.value not in FIXES:  # a HostStop with a fix at its raise site needs no entry
            missing.add(f"{path.name}:{code.value}")
    assert not missing, sorted(missing)


def test_the_fix_table_covers_the_codes_of_the_work() -> None:
    from tools.fixes import FIXES, fix_for

    from_work = ["SEMANTIC_HUMAN_EXPECTATION_BODY_MISSING", "SEMANTIC_HUMAN_BODY_UNBOUND", "SEMANTIC_BODY_TARGET_OVERLAP", "SEMANTIC_BLOCKER_PATH",
                 "SEMANTIC_MISSING_ASSERTION_BLOCKER", "SEMANTIC_SPURIOUS_BLOCKER", "CANONICAL_REQUIREMENT_ID_ORDER", "CANONICAL_CATEGORY_ORDER",
                 "SCHEMA_ENUM", "SCHEMA_ONE_OF", "SCHEMA_REQUIRED", "BATCH_UNCOVERED_REQUIREMENT", "MODEL_REQUEST_INVALID", "MODEL_INPUTS_MISMATCH",
                 "INDEPENDENCE_UNVERIFIED", "REVIEWER_PROTOCOL", "ATTEMPT_TERMINAL", "ZEPHYR_CSV_PROFILE", "FINISH_FACT_UNDETERMINED"]
    assert [code for code in from_work if not FIXES.get(code)] == []
    assert "positive" in FIXES["SCHEMA_ENUM"] or "перечн" in FIXES["SCHEMA_ENUM"]
    assert "tc-generator" in FIXES["MODEL_REQUEST_INVALID"]  # the list of stages
    assert fix_for("SCHEMA_TYPE")  # every SCHEMA_* has the generic one
    assert fix_for("NO_SUCH_CODE") is None


def test_every_code_the_canonical_audit_emits_has_a_fix() -> None:
    from tools.fixes import fix_for

    codes = set()
    for name in ("canonical_document.py", "schema_validation.py"):
        codes |= set(re.findall(r'"((?:SEMANTIC|CANONICAL)_[A-Z0-9_]+)"', (ROOT / "tools" / name).read_text(encoding="utf-8")))
    assert [code for code in sorted(codes) if not fix_for(code)] == []


def test_the_blocker_codes_share_one_rule_with_an_example() -> None:
    from tools.fixes import FIXES

    one = {FIXES[code] for code in ("SEMANTIC_BLOCKER_PATH", "SEMANTIC_MISSING_ASSERTION_BLOCKER", "SEMANTIC_SPURIOUS_BLOCKER")}
    assert len(one) == 1 and "field_path" in next(iter(one)) and "UNRESOLVED_" in next(iter(one))


def test_the_envelope_takes_the_fix_from_the_table_when_the_error_has_none() -> None:
    from tools.json_cli import error_envelope

    class Refused(ValueError):
        code = "ATTEMPT_TERMINAL"

    assert "report.md" in error_envelope(Refused("closed"))["fix"]
    own = Refused("closed; fix: свой текст")
    assert error_envelope(own)["fix"] == "свой текст"  # the error's own fix wins


def test_host_stop_without_a_fix_gets_the_table_one() -> None:
    from tools.run_pipeline import HostStop

    assert HostStop("FINISH_FACT_UNDETERMINED", "no attempt").fix
    assert HostStop("FINISH_FACT_UNDETERMINED", "no attempt", "свой").fix == "свой"
    assert HostStop("NO_SUCH_STOP", "x").fix is None


# --------------------------------------------------------------------------------------
# bare ValueError -> codes
# --------------------------------------------------------------------------------------

def test_an_unknown_zephyr_profile_is_a_coded_refusal() -> None:
    from tools.json_cli import error_envelope
    from tools.test_case_projections import _reject_unknown_profile

    with pytest.raises(ValueError) as refused:
        _reject_unknown_profile("v9")
    envelope = error_envelope(refused.value)
    assert envelope["code"] == "ZEPHYR_CSV_PROFILE" and "v5" in envelope["fix"], envelope


def test_the_ledger_of_a_closed_attempt_is_refused_with_attempt_terminal(tmp_path: Path, monkeypatch) -> None:
    from tests.test_marker_parts import _run
    from tools import pilot_state
    from tools.json_cli import error_envelope

    root, attempt_id, _baseline, _receipts = _run(tmp_path, files=1)
    real = pilot_state.derive_state

    def closed(run_root):
        state = real(run_root)
        for attempt in state["attempts"]:
            attempt["state"] = "TERMINAL"
        return state

    monkeypatch.setattr(pilot_state, "derive_state", closed)
    with pytest.raises(ValueError) as refused:
        pilot_state.publish_reviewer_session_ledger(root, attempt_id, {"digest": "sha256:" + "0" * 64})
    envelope = error_envelope(refused.value)
    assert envelope["code"] == "ATTEMPT_TERMINAL" and "report.md" in envelope["fix"], envelope


def test_model_request_flags_given_to_model_response_are_an_invalid_argument(capsys) -> None:
    from tools import api

    with pytest.raises(SystemExit) as stopped:
        api.main(["model-response", "--run-root", "x", "--attempt", "a", "--stage", "s", "--artifact", str(ROOT / "schemas" / "batch-plan.schema.json"),
                  "--model-id", "m"])
    envelope = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert stopped.value.code == 2 and envelope["code"] == "INVALID_ARGUMENT" and "model-request" in envelope["fix"], envelope
    assert envelope["errors"]  # the old field stays


# --------------------------------------------------------------------------------------
# step measurement, refusal log
# --------------------------------------------------------------------------------------

def test_model_request_writes_a_step_measurement_for_the_stages_the_journal_resolves(tmp_path: Path) -> None:
    from tests.test_step_inputs import _generator_run, _request
    from tools.run_report import step_sizes

    root, attempt_id, _baseline, receipts, marker, plan, header, *_rest = _generator_run(tmp_path)
    steps = lambda: [json.loads(line)["step"] for line in (root / "step-sizes.jsonl").read_text(encoding="utf-8").splitlines()]  # noqa: E731
    assert steps() == ["context-marker:baseline"]  # a part of the marker is measured, the merge by the code (controller:) is not a model step
    _request(root, attempt_id, "tc-generator:BATCH-fixture", [marker["content_digest"], receipts[0]["digest"], plan["digest"], header["digest"]])
    assert steps() == ["context-marker:baseline", "tc-generator:BATCH-fixture"]
    assert json.loads((root / "step-sizes.jsonl").read_text(encoding="utf-8").splitlines()[-1])["bytes"] > 0
    assert step_sizes(root)  # the report section is not empty


def test_a_failed_measurement_is_named_in_the_output_and_never_refuses_the_request(tmp_path: Path, monkeypatch) -> None:
    from tests.test_marker_parts import _run
    from tools import pilot_state, step_size

    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)

    def broken(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(step_size, "record", broken)
    request = pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker",
                                                input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]])
    assert request["step_measure_error"] == {"code": "STEP_MEASURE_FAILED", "error": "OSError"} and request["digest"]


def _refused_model_request(root: Path, attempt_id: str, *extra: str) -> int:
    from tools import api

    return api.main(["model-request", "--run-root", str(root), "--attempt", attempt_id, "--stage", "tc-generator:BATCH-x", "--model-id", "m",
                     "--invocation-id", "i", "--input-digest", "sha256:" + "1" * 64, *extra])


def test_a_refusal_with_a_run_root_is_one_line_with_five_fields(tmp_path: Path, capsys) -> None:
    from tests.test_marker_parts import _run

    root, attempt_id, *_rest = _run(tmp_path, files=1)
    assert _refused_model_request(root, attempt_id) == 2
    capsys.readouterr()
    lines = (root / "refusals.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert sorted(row) == ["at", "batch", "code", "command", "stage"], row
    assert row["command"] == "model-request" and row["stage"] == "tc-generator:BATCH-x" and row["batch"] == "BATCH-x" and row["code"]
    assert _refused_model_request(root, attempt_id) == 2
    assert len((root / "refusals.jsonl").read_text(encoding="utf-8").splitlines()) == 2  # appended


def test_a_refusal_without_a_run_root_writes_nothing_and_never_the_content(tmp_path: Path, capsys, monkeypatch) -> None:
    from tools import api

    fragment, plan, header, context = _synthetic()
    secret = "СЕКРЕТНЫЙ-ТЕКСТ-ФРАГМЕНТА"
    fragment["test_cases"][0]["title"] = secret
    paths = {}
    for name, value in (("fragment", fragment), ("plan", plan), ("header", header), ("context", context)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    fragment["batch_id"] = "BATCH-unknown"
    paths["fragment"].write_text(json.dumps(fragment, ensure_ascii=False), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert api.main(["validate-fragment", "--fragment", str(paths["fragment"]), "--plan", str(paths["plan"]), "--header", str(paths["header"]),
                     "--context-receipt", str(paths["context"])]) == 2
    capsys.readouterr()
    assert not list(tmp_path.rglob("refusals.jsonl"))
    from tests.test_marker_parts import _run

    (tmp_path / "project").mkdir()
    root, attempt_id, *_rest = _run(tmp_path / "project", files=1)
    _refused_model_request(root, attempt_id)
    assert secret not in (root / "refusals.jsonl").read_text(encoding="utf-8")


def test_run_pipeline_refusals_of_a_named_run_go_to_the_same_journal(tmp_path: Path, capsys) -> None:
    from tests.test_marker_parts import AUTHORIZATION
    from tools import run_pipeline
    from tools.pilot_state import create_run

    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    assert run_pipeline.main(["finish", "--project", str(tmp_path), "--run", root.name]) == 2
    capsys.readouterr()
    (line,) = (root / "refusals.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(line)
    assert sorted(row) == ["at", "batch", "code", "command", "stage"] and row["command"] == "finish" and row["code"], row


def test_the_fix_of_the_call_site_beats_the_table_and_the_errors_own_beats_both() -> None:
    from tools.json_cli import error_fields

    class Refused(ValueError):
        code = "INVENTORY_INVALID"

    assert error_fields(Refused("x"))["fix"]  # the table's
    assert "fix" not in error_fields(Refused("x"), table=False)  # the call site falls back to its own
    assert error_fields(Refused("x; fix: свой"), table=False)["fix"] == "свой"


# --------------------------------------------------------------------------------------
# P1-fix2: assemble lays out the same canon as validate-fragment and model-response (bug hunt 6, 6.1, 6.6, 6.7)
# --------------------------------------------------------------------------------------

def _shuffled_batch_one():
    """The agent's file of batch 1: ids and categories not in the canon's order, no digest — validate-fragment accepts it."""
    fragment, plan, header, context = _synthetic()
    second = dict(fragment["requirements"][0], requirement_id=fragment["requirements"][0]["requirement_id"] + "-B", display_order=100)
    fragment["requirements"].append(second)
    fragment["source_to_canonical_mappings"][0]["canonical_requirement_ids"].append(second["requirement_id"])
    first_id = fragment["requirements"][0]["requirement_id"]
    fragment["test_cases"][0]["requirement_ids"] = [second["requirement_id"], first_id]
    fragment["test_cases"][0]["categories"] = ["functional", "positive"]
    fragment.pop("digest", None)
    return fragment, plan, header, context


def _others(plan: dict, context: dict) -> list[dict]:
    return [_fragment(plan, context, index) for index in range(1, len(plan["batches"]))]


def test_assemble_accepts_the_file_validate_fragment_accepted() -> None:
    from tools.batch_assembly import assemble_candidate, validate_fragment

    mixed, plan, header, context = _shuffled_batch_one()
    assert validate_fragment(copy.deepcopy(mixed), plan, header, context)["normalized"]
    candidate, _receipt = assemble_candidate(header, plan, [mixed, *_others(plan, context)])
    case = candidate["test_cases"][0]
    assert case["categories"] == ["positive", "functional"] and case["requirement_ids"] == sorted(case["requirement_ids"])


def test_assemble_of_the_agents_file_and_of_the_recorded_answer_is_the_same_candidate_and_receipt() -> None:
    from tools.batch_assembly import assemble_candidate, fill_fragment_bindings, normalize_fragment

    mixed, plan, header, context = _shuffled_batch_one()
    recorded = fill_fragment_bindings(normalize_fragment(copy.deepcopy(mixed))[0], plan)  # what model-response writes to the journal
    others = _others(plan, context)
    from_file = assemble_candidate(header, plan, [copy.deepcopy(mixed), *copy.deepcopy(others)])
    from_journal = assemble_candidate(header, plan, [recorded, *copy.deepcopy(others)])
    assert from_file == from_journal  # the candidate, the fragment digests and the receipt digest alike


def test_assemble_does_not_change_the_files_it_was_given() -> None:
    from tools.batch_assembly import assemble_candidate

    mixed, plan, header, context = _shuffled_batch_one()
    before = copy.deepcopy(mixed)
    assemble_candidate(header, plan, [mixed, *_others(plan, context)])
    assert mixed == before


def test_a_refusal_of_the_canonical_audit_in_assemble_gives_all_rows_and_its_own_fix() -> None:
    from tests.test_pg1 import _plain_split
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment
    from tools.json_cli import error_fields

    # Russian prose anywhere in the document is the rule of the whole document (PG1: checked at the last batch, test_pg1)
    plan, header, context, fragments = _plain_split()
    assert validate_fragment(copy.deepcopy(fragments[0]), plan, header, context) is None  # batch 1: another batch may have it
    with pytest.raises(BatchAssemblyError) as refused:
        assemble_candidate(header, plan, fragments)
    assert refused.value.code == "BATCH_SEMANTIC_CONFLICT"
    rows = refused.value.diagnostics
    assert rows and rows[0]["code"] == "SEMANTIC_RU_RU_HUMAN_FIELDS" and all(row["fix"] and row["path"] for row in rows), rows
    fields = error_fields(refused.value)
    assert fields["fix"] and "validate-fragment" not in fields["fix"], fields  # not the circle back to the check that said ok


def test_the_fix_of_a_canonical_order_is_true_for_assemble_too() -> None:
    from tools.fixes import fix_for

    for code in ("CANONICAL_REQUIREMENT_ID_ORDER", "CANONICAL_CATEGORY_ORDER"):
        assert "assemble" in fix_for(code), code


def test_the_skill_names_what_assemble_takes_as_the_fragment_file() -> None:
    text = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    assert "`--fragment` — файл ответа пачки как есть или записанный ответ" in text


def test_a_scope_pause_is_not_a_refusal_of_the_journal(tmp_path: Path, capsys) -> None:
    from tests.test_scope_pause import _api, _paused_run, _plan_arguments

    root, attempt_id, _baseline, receipts = _paused_run(tmp_path)
    code, payload = _api(capsys, _plan_arguments(root, attempt_id, receipts))
    assert code == 2 and payload["code"] == "SCOPE_PENDING", payload
    assert not (root / "refusals.jsonl").exists()  # the pause is routine; the report shows it, not the refusals


def test_the_fix_of_an_invalid_argument_names_the_module_that_refused(capsys) -> None:
    from tools.json_cli import JsonArgumentParser

    for prog, module in (("python -m tools.api", "tools.api"), ("run_pipeline.py", "tools.run_pipeline")):
        parser = JsonArgumentParser(prog=prog)
        parser.add_argument("--known")
        with pytest.raises(SystemExit):
            parser.parse_args(["--unknown"])
        fix = json.loads(capsys.readouterr().out.strip().splitlines()[-1])["fix"]
        assert module in fix and (module == "tools.api" or "tools.api" not in fix), (prog, fix)
