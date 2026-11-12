"""PF-1 (Petclinic v0.2): the refusals of the check of a fragment (submit) reach the journal of the run and the report; the fix of a fragment that
lacks its context_receipt and of a --prior-fragment that is not the recorded answer say what to give; diagnostics exist when
the fix points at them; submit prints the receipt of the record, not the answer's body."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.helpers import answer_stage
from tests.test_batch_journal import _run
from tests.test_review_requested import _put

pytestmark = pytest.mark.contract


def _other_receipt(receipt: dict) -> dict:
    from tests.test_batch_assembly import _self_digest

    other = {**{key: value for key, value in receipt.items() if key != "digest"}, "batch_index": 7}
    return {**other, "digest": _self_digest(other)}  # a receipt that is whole and sealed, but not the one of the batch


def _validate(run: dict, fragment: Path, *extra: str, dry_run: bool = True) -> tuple[int, dict]:
    """The check of a fragment: ``submit --dry-run`` of its batch after ``prepare``; the fragment goes into the slot of the stage."""
    stage = f"tc-generator:{json.loads(fragment.read_bytes())['batch_id']}"
    return answer_stage(run["run_root"], stage, fragment, *(("--dry-run",) if dry_run else ()), *extra)


def test_a_fragment_with_another_context_receipt_gets_a_fix_that_names_the_object_and_its_source(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    bare = {**{key: value for key, value in fragments[0].items() if key != "digest"}, "context_receipt": _other_receipt(fragments[0]["context_receipt"])}  # V6: a missing receipt is put by the code; another one is a refusal
    code, refused = _validate(run, Path(_put(run["tmp"] / "bare.json", bare)))
    assert code == 2 and refused["code"] == "BATCH_SEMANTIC_CONFLICT", refused
    assert "смотри diagnostics" not in refused["fix"] and "context_receipt" in refused["fix"] and "квитанци" in refused["fix"], refused
    assert refused["diagnostics"] and refused["diagnostics"][0]["path"] == "/context_receipt" and refused["fix"].startswith(refused["diagnostics"][0]["fix"]), refused


def test_a_refusal_whose_fix_would_point_at_diagnostics_has_none_gets_a_fix_that_stands_alone() -> None:
    from tools.batch_assembly import BatchAssemblyError
    from tools.json_cli import error_fields

    fields = error_fields(BatchAssemblyError("BATCH_SEMANTIC_CONFLICT", "something single"))
    assert "смотри diagnostics" not in fields["fix"] and "message" in fields["fix"], fields


def test_a_refusal_of_a_submit_is_in_the_journal_and_in_the_report(tmp_path: Path) -> None:
    from tools.run_report import refusals

    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    bare = {**{key: value for key, value in fragments[0].items() if key != "digest"}, "context_receipt": _other_receipt(fragments[0]["context_receipt"])}  # V6: a missing receipt is put by the code; another one is a refusal
    path = Path(_put(run["tmp"] / "bare.json", bare))
    assert _validate(run, path, dry_run=False)[0] == 2  # a dry run leaves no line of refusal
    (line,) = (Path(run["run_root"]) / "refusals.jsonl").read_text(encoding="utf-8").splitlines()
    row = json.loads(line)
    assert row["command"] == "submit" and row["code"] == "BATCH_SEMANTIC_CONFLICT", row
    assert row["stage"] == f"tc-generator:{fragments[0]['batch_id']}" and row["batch"] == fragments[0]["batch_id"], row
    summary = refusals(Path(run["run_root"]))
    assert summary["by_stage"] == {row["stage"]: 1} and summary["by_command"] == {"submit": 1} and summary["by_batch"] == {row["batch"]: 1}, summary


def test_a_passing_dry_run_is_not_a_refusal(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, _fragments = _run(tmp_path)
    assert _validate(run, Path(run["fragments"][0]))[0] == 0
    assert not (Path(run["run_root"]) / "refusals.jsonl").exists()


def test_submit_prints_the_receipt_of_the_record_and_not_the_answers_body(tmp_path: Path) -> None:
    from tools.pilot_state import read_model_stage_artifact

    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    stage = f"tc-generator:{fragments[0]['batch_id']}"
    big = copy.deepcopy(fragments[0])
    big.pop("digest", None)
    big["test_cases"][0]["objective"] = "Проверить сценарий " + "подробно " * 40_000
    artifact = _put(run["tmp"] / "big.json", big)
    assert Path(artifact).stat().st_size > 300_000
    code, answered = answer_stage(run["run_root"], stage, Path(artifact))
    assert code == 0, answered
    assert "artifact" not in answered["result"], sorted(answered["result"])
    assert {"content_digest", "path", "byte_count", "output_bytes", "filled"} <= set(answered["result"]), sorted(answered["result"])
    assert len(json.dumps(answered, ensure_ascii=False)) < 4_000
    stored = read_model_stage_artifact(run["run_root"], run["attempt_id"], stage, answered["result"]["content_digest"])["artifact"]
    assert stored["test_cases"][0]["objective"] == big["test_cases"][0]["objective"]  # the body is read apart


def test_a_refused_fragment_leaves_its_text_out_of_the_journal_of_refusals(tmp_path: Path) -> None:
    run, _plan_digest, _header_digest, fragments = _run(tmp_path)
    secret = "СЕКРЕТНЫЙ-ТЕКСТ-ФРАГМЕНТА"
    bad = {key: value for key, value in fragments[0].items() if key != "digest"}
    bad = {**bad, "namespace": "B9", "test_cases": [{**bad["test_cases"][0], "title": secret}, *bad["test_cases"][1:]]}
    assert _validate(run, Path(_put(run["tmp"] / "secret.json", bad)), dry_run=False)[0] == 2
    assert secret not in (Path(run["run_root"]) / "refusals.jsonl").read_text(encoding="utf-8")
