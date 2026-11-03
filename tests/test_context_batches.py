"""FaT-AU3 (audit C3): a scan with more than one context batch — every generator batch reads the context receipts of the
plan (``context_receipt_digests``), taken in ``batch_index`` order while they add no batch; one context batch keeps the plan,
the inputs and the bytes of before."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import pilot_state

HEADER = "sha256:" + "c" * 64


def _requirements() -> list[dict]:
    from tests.test_batch_size_split import _requirement

    return [_requirement(1, "REQ-1 Оплата " + "а" * 1000), _requirement(2, "REQ-2 Возврат " + "б" * 1000)]


def _grouping(receipts: list[dict]) -> dict:
    grouping = {"header_digest": HEADER, "context_receipt_digest": receipts[0]["digest"]}
    if len(receipts) > 1:
        grouping["context_receipt_digests"] = [receipt["digest"] for receipt in receipts]
    return grouping


def _number(plan, pattern: str) -> int:
    return int(re.search(pattern, " ".join(plan.notes)).group(1))


@pytest.fixture
def three(tmp_path: Path):
    from tests.test_marker_parts import _run

    (tmp_path / "tests").mkdir()  # a project test, for the map of existing tests (FaT-EC)
    (tmp_path / "tests" / "test_part0.py").write_text("from part0 import X\n\n\ndef test_x():\n    assert X == 1\n", encoding="utf-8")
    root, attempt_id, baseline, receipts = _run(tmp_path, files=3)
    assert len(receipts) == 3
    return tmp_path, root, attempt_id, baseline, receipts


def test_one_context_batch_keeps_the_plan_and_its_notes(three) -> None:
    from tools.batch_assembly import canonical_bytes, plan_batches_for_project

    project, _root, _attempt, _baseline, receipts = three
    old = plan_batches_for_project(_requirements(), _grouping(receipts[:1]), project, [], receipts[0])
    listed = plan_batches_for_project(_requirements(), _grouping(receipts[:1]), project, [], [receipts[0]])
    assert canonical_bytes(old) == canonical_bytes(listed) and old.notes == listed.notes
    assert "context_receipt_digests" not in old and not any("контекст:" in note for note in old.notes)


def test_every_context_batch_that_adds_no_batch_is_in_the_plan_in_batch_index_order(three) -> None:
    from tools.batch_assembly import plan_batches_for_project

    project, _root, _attempt, _baseline, receipts = three
    plan = plan_batches_for_project(_requirements(), _grouping(receipts), project, [], list(reversed(receipts)))
    assert plan["context_receipt_digests"] == [receipt["digest"] for receipt in receipts]
    assert plan["context_receipt_digest"] == receipts[0]["digest"] and len(plan["batches"]) == 1
    assert any("видит 3 из 3 батчей контекста" in note for note in plan.notes), plan.notes


def test_a_context_batch_that_would_add_a_batch_stays_out_and_the_note_names_it(three, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import step_size
    from tools.batch_assembly import WINDOW_SHARE, plan_batches_for_project

    project, _root, _attempt, _baseline, receipts = three
    one = plan_batches_for_project(_requirements(), _grouping(receipts[:1]), project, [], receipts[0])
    step, answer = _number(one, r"вход шага (\d+) Б"), _number(one, r"оценка ответа (\d+) Б")
    # the answer fits beside one receipt in one batch, beside two (each ~72 KB) it would need two batches
    monkeypatch.setattr(step_size, "step_window", lambda _project: int((step + answer + 10_000) / WINDOW_SHARE))
    plan = plan_batches_for_project(_requirements(), _grouping(receipts), project, [], receipts)
    assert "context_receipt_digests" not in plan and len(plan["batches"]) == 1, plan.notes
    note = next(note for note in plan.notes if note.startswith("контекст:"))
    assert "видит 1 из 3" in note and f"не видит батчи 2 ({receipts[1]['byte_count']} Б), 3" in note, note


def test_plan_batches_needs_every_context_receipt_of_the_grouping(three) -> None:
    from tools.batch_assembly import BatchAssemblyError, plan_batches_for_project

    project, _root, _attempt, _baseline, receipts = three
    with pytest.raises(BatchAssemblyError, match="every context receipt of the grouping"):
        plan_batches_for_project(_requirements(), _grouping(receipts), project, [], receipts[:2])


def _generator_journal(three, listed: list[dict]):
    from tests.test_marker_parts import _marker

    _project, root, attempt_id, baseline, receipts = three
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="marker",
                                      input_digests=[baseline["requirements"]["digest"], baseline["inventory_digest"], *(receipt["digest"] for receipt in receipts)])
    marker = pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", _marker(["src/part0.py — x"]))
    header = pilot_state.publish_generator_input(root, attempt_id, "header", {"kind": "header", "title": "Заголовок"})
    plan = {"kind": "plan", "header_digest": header["digest"], "context_receipt_digest": listed[0]["digest"],
            "batches": [{"batch_id": "BATCH-fixture", "context_receipt_digest": listed[0]["digest"], "header_digest": header["digest"]}]}
    if len(listed) > 1:
        plan["context_receipt_digests"] = [receipt["digest"] for receipt in listed]
    published = pilot_state.publish_generator_input(root, attempt_id, "plan", plan)
    return root, attempt_id, marker["content_digest"], published["digest"], header["digest"]


def test_the_generator_request_takes_the_context_receipts_of_the_plan_in_its_order(three) -> None:
    from tools.step_size import request_files

    receipts = three[4]
    root, attempt_id, marker, plan, header = _generator_journal(three, receipts)
    contexts = [receipt["digest"] for receipt in receipts]
    with pytest.raises(ValueError, match="MODEL_INPUTS_MISMATCH") as refused:
        pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="g", invocation_id="gen",
                                          input_digests=[marker, contexts[0], plan, header])
    assert contexts[1] in str(refused.value) and "context_receipt_digests[1]" in str(refused.value)
    pilot_state.publish_model_request(root, attempt_id, "tc-generator:BATCH-fixture", model_id="g", invocation_id="gen",
                                      input_digests=[marker, *contexts, plan, header])
    found = request_files(root, "tc-generator:BATCH-fixture")
    assert found["unresolved"] == [], found
    assert {row["digest"] for row in found["files"] if not row.get("basis")} >= set(contexts)
    maps = [row for row in found["files"] if row.get("basis") == "карта"]
    assert [row["digest"] for row in maps] == [contexts[0]], maps  # one map for all three receipts, no repeat


def test_a_plan_with_a_context_that_is_not_selected_is_refused(three) -> None:
    receipts = three[4]
    fake = dict(receipts[1], digest="sha256:" + "d" * 64)
    with pytest.raises(ValueError, match="GENERATOR_INPUT_CONTEXT"):
        _generator_journal(three, [receipts[0], fake])


def test_the_fragment_binds_plan_and_header_after_the_context_receipts(three) -> None:
    receipts = three[4]
    root, attempt_id, marker, plan, header = _generator_journal(three, receipts)
    contexts = [receipt["digest"] for receipt in receipts]
    value = {"context_receipt_digest": contexts[0], "plan_digest": plan, "header_digest": header,
             "context_receipt": dict(pilot_state.read_context_selection(root, attempt_id, contexts[0]))}
    request = {"stage": "tc-generator", "input_digests": [marker, *contexts, plan, header]}
    pilot_state._validate_generator_artifact_binding(root, attempt_id, request, value)
    with pytest.raises(ValueError, match="generator fragment must match"):
        pilot_state._validate_generator_artifact_binding(root, attempt_id, request, dict(value, header_digest=plan))
    with pytest.raises(ValueError, match="generator fragment must match"):  # a non-context input before the plan
        pilot_state._validate_generator_artifact_binding(root, attempt_id, dict(request, input_digests=[marker, contexts[0], header, plan, header]),
                                                         dict(value, plan_digest=plan))


def test_the_reviewer_view_says_which_context_batches_the_generator_saw(three) -> None:
    from tools.review_view import run_context_note

    receipts = three[4]
    root, attempt_id, *_rest = _generator_journal(three, receipts[:2])
    note = run_context_note(root, attempt_id)
    assert note is not None and "видит 2 из 3" in note and "не видит батчи 3" in note, note


def test_with_one_context_batch_the_reviewer_view_has_no_context_line(tmp_path: Path) -> None:
    from tests.test_marker_parts import _run
    from tools.review_view import run_context_note

    found = _run(tmp_path, files=1)
    assert len(found[3]) == 1
    root, attempt_id, *_rest = _generator_journal((tmp_path, *found), found[3])
    assert run_context_note(root, attempt_id) is None


CAMPAIGN = Path(r"D:\AI-Projects\live\campaign-20261028")


@pytest.mark.skipif(not (CAMPAIGN / "jpetstore-6").is_dir(), reason="the campaign of 28.10 is on the work machine only")
def test_a_campaign_run_with_two_context_batches_takes_both_and_fits() -> None:
    """Read only: the jpetstore-6 run of 28.10 — its requirements, its two context receipts, its project (before FaT-AU3
    the generator saw one: 261 563 of 488 714 B); its two batches stay two."""
    from tools.batch_assembly import WINDOW_SHARE, plan_batches_for_project
    from tools.step_size import step_window

    project = CAMPAIGN / "jpetstore-6"
    root = project / ".pilot-runs" / "f6c976a2e9a349dc87e462e74dd6deaf"
    [attempt] = [path.name for path in (root / "context-selections").iterdir()]
    receipts = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((root / "context-selections" / attempt).glob("*.json"))]
    receipts.sort(key=lambda receipt: receipt["batch_index"])
    plan = json.loads(next((root / "attempt-receipts").rglob("batch-plan*.json")).read_text(encoding="utf-8"))["plan"] \
        if (root / "attempt-receipts").is_dir() else None
    requirements = plan["source_requirements"] if plan else _campaign_requirements(root, attempt)
    grouping = {"header_digest": HEADER, "context_receipt_digest": receipts[0]["digest"],
                "context_receipt_digests": [receipt["digest"] for receipt in receipts]}
    one = plan_batches_for_project(requirements, {key: value for key, value in grouping.items() if key != "context_receipt_digests"},
                                   project, [], receipts[0])
    result = plan_batches_for_project(requirements, grouping, project, [], receipts)
    taken = len(result.get("context_receipt_digests") or [result["context_receipt_digest"]])
    assert taken == len(receipts) == 2 and len(result["batches"]) == len(one["batches"]), result.notes
    step = int(re.search(r"вход шага (\d+) Б", " ".join(result.notes)).group(1)) if "вход шага" in " ".join(result.notes) else None
    assert step is None or step < step_window(project) * WINDOW_SHARE, result.notes
    assert any(f"видит {taken} из {len(receipts)}" in note for note in result.notes), result.notes


def _campaign_requirements(root: Path, attempt: str) -> list[dict]:
    for path in sorted((root / "model-stage-artifacts").rglob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        requirements = (((value.get("artifact") or value).get("artifacts") or {}).get("analytics_documentation") or {}).get("requirements")
        if requirements:
            return requirements
    raise AssertionError("no requirements in the campaign run")


def test_plan_batches_estimates_the_volume_of_the_whole_run(three) -> None:
    """P9: the output says how many batches and requirements, and a rough number of cases (2–3 per requirement)."""
    from tools.batch_assembly import plan_batches_for_project

    project, _root, _attempt, _baseline, receipts = three
    plan = plan_batches_for_project(_requirements(), _grouping(receipts[:1]), project, [], receipts[0])
    assert plan.notes[-1] == f"пачек {len(plan['batches'])}, требований 2, ориентировочно кейсов 2×2–3 (4–6)", plan.notes
