"""PF-1 (Petclinic v0.2): the refusals of validate-fragment reach the journal of the run and the report; the fix of a fragment that
lacks its context_receipt and of a --prior-fragment that is not the recorded answer say what to give; diagnostics exist when
the fix points at them; model-response prints the receipt of the record, not the answer's body."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_batch_journal import _response, _run
from tests.test_digest_traps import _generator_request
from tests.test_review_requested import _api, _put

pytestmark = pytest.mark.contract


def _other_receipt(receipt: dict) -> dict:
    from tests.test_batch_assembly import _self_digest

    other = {**{key: value for key, value in receipt.items() if key != "digest"}, "batch_index": 7}
    return {**other, "digest": _self_digest(other)}  # a receipt that is whole and sealed, but not the one of the batch


def _validate(run: dict, fragment: Path, *extra: str) -> tuple[int, dict]:
    return _api("validate-fragment", "--fragment", str(fragment), "--plan", str(run["plan"]), "--header", str(run["header"]),
                "--context-receipt", str(run["context"]), *extra)


def test_a_fragment_with_another_context_receipt_gets_a_fix_that_names_the_object_and_its_source(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    bare = {**{key: value for key, value in fragments[0].items() if key != "digest"}, "context_receipt": _other_receipt(fragments[0]["context_receipt"])}  # V6: a missing receipt is put by the code; another one is a refusal
    code, refused = _validate(run, Path(_put(run["tmp"] / "bare.json", bare)))
    assert code == 2 and refused["code"] == "BATCH_SEMANTIC_CONFLICT", refused
    assert "смотри diagnostics" not in refused["fix"] and "context_receipt" in refused["fix"] and "--context-receipt" in refused["fix"], refused
    assert refused["diagnostics"] and refused["diagnostics"][0]["path"] == "/context_receipt" and refused["diagnostics"][0]["fix"] == refused["fix"], refused


def test_a_refusal_whose_fix_would_point_at_diagnostics_has_none_gets_a_fix_that_stands_alone() -> None:
    from tools.batch_assembly import BatchAssemblyError
    from tools.json_cli import error_fields

    fields = error_fields(BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "something single"))
    assert "смотри diagnostics" not in fields["fix"] and "message" in fields["fix"], fields


def test_a_prior_fragment_that_is_the_agents_file_is_named_as_not_recorded(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    raw = {key: value for key, value in fragments[0].items() if key not in ("digest", "plan_digest", "header_digest", "context_receipt_digest")}
    second = Path(run["fragments"][1])
    code, refused = _validate(run, second, "--prior-fragment", _put(run["tmp"] / "agents-b1.json", raw))
    assert code == 2 and refused["code"] == "BATCH_PARTITION_CONFLICT", refused
    assert "is given, but it is not the recorded answer" in refused["message"], refused
    assert "model-response" in refused["fix"] and "path" in refused["fix"] and "записанн" in refused["fix"], refused
    assert refused["diagnostics"] and refused["diagnostics"][0]["path"] == "/prior_fragment", refused


def test_a_prior_fragment_that_is_not_given_is_another_refusal_with_its_own_fix(tmp_path: Path) -> None:
    run, *_rest = _run(tmp_path)
    code, refused = _validate(run, Path(run["fragments"][1]))
    assert code == 2 and refused["code"] == "BATCH_PARTITION_CONFLICT", refused
    assert "is given" not in refused["message"] and "--prior-fragment" in refused["fix"] and "model-response" in refused["fix"], refused
    code, accepted = _validate(run, Path(run["fragments"][1]), "--prior-fragment", run["fragments"][0])
    assert code == 0, accepted  # the recorded one (with its plan_digest) goes


def test_a_validate_fragment_refusal_with_a_run_is_in_the_journal_and_in_the_report(tmp_path: Path) -> None:
    from tools.run_report import refusals

    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    bare = {**{key: value for key, value in fragments[0].items() if key != "digest"}, "context_receipt": _other_receipt(fragments[0]["context_receipt"])}  # V6: a missing receipt is put by the code; another one is a refusal
    path = Path(_put(run["tmp"] / "bare.json", bare))
    assert _validate(run, path, "--run-root", str(run["run_root"]))[0] == 2
    (line,) = (Path(run["run_root"]) / "refusals.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(line)
    assert row["command"] == "validate-fragment" and row["code"] == "BATCH_SEMANTIC_CONFLICT", row
    assert row["stage"] == f"tc-generator:{fragments[0]['batch_id']}" and row["batch"] == fragments[0]["batch_id"], row
    summary = refusals(Path(run["run_root"]))
    assert summary["by_stage"] == {row["stage"]: 1} and summary["by_command"] == {"validate-fragment": 1}, summary


def test_a_validate_fragment_refusal_without_a_run_writes_nothing_and_an_ok_is_not_a_refusal(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    bare = {**{key: value for key, value in fragments[0].items() if key != "digest"}, "context_receipt": _other_receipt(fragments[0]["context_receipt"])}  # V6: a missing receipt is put by the code; another one is a refusal
    assert _validate(run, Path(_put(run["tmp"] / "bare.json", bare)))[0] == 2
    assert _validate(run, Path(run["fragments"][0]), "--run-root", str(run["run_root"]))[0] == 0
    assert not (Path(run["run_root"]) / "refusals.jsonl").exists()


def test_model_response_prints_the_receipt_of_the_record_and_not_the_answers_body(tmp_path: Path) -> None:
    run, plan_digest, header_digest, fragments = _run(tmp_path)
    first = fragments[0]["batch_id"]
    big = copy.deepcopy(fragments[0])
    big.pop("digest", None)
    big["test_cases"][0]["objective"] = "Проверить сценарий " + "подробно " * 40_000
    assert _generator_request(run, first, plan_digest, header_digest)[0] == 0
    artifact = _put(run["tmp"] / "big.json", big)
    assert Path(artifact).stat().st_size > 300_000
    code, answered = _response(run, first, artifact)
    assert code == 0, answered
    assert "artifact" not in answered["result"], sorted(answered["result"])
    assert {"content_digest", "path", "byte_count", "output_bytes", "filled"} <= set(answered["result"]), sorted(answered["result"])
    assert len(json.dumps(answered, ensure_ascii=False)) < 4_000
    code, stored = _api("read-model-artifact", "--run-root", str(run["run_root"]), "--attempt", run["attempt_id"], "--stage", f"tc-generator:{first}",
                        "--digest", answered["result"]["content_digest"])
    assert code == 0 and stored["result"]["artifact"]["test_cases"][0]["objective"] == big["test_cases"][0]["objective"]  # the body is read apart


def test_a_run_root_that_is_no_run_leaves_the_refusal_as_it_was_and_writes_nothing(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    bare = {**{key: value for key, value in fragments[0].items() if key != "digest"}, "context_receipt": _other_receipt(fragments[0]["context_receipt"])}  # V6: a missing receipt is put by the code; another one is a refusal
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    code, refused = _validate(run, Path(_put(run["tmp"] / "bare.json", bare)), "--run-root", str(nowhere))
    assert code == 2 and refused["code"] == "BATCH_SEMANTIC_CONFLICT", refused  # the refusal of the check, not of the run
    assert not list(nowhere.rglob("refusals.jsonl")) and not (Path(run["run_root"]) / "refusals.jsonl").exists()
