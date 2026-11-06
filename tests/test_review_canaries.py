"""P7: canaries in the reviewer's view — seeded by code, bound to the journal, counted at ``review-select``.

- ``seed`` is a pure function of the candidate: 0..3 control errors, none in a parameter group, "assertion removed" first; the
  candidate, its bundles and its journal events stay as they are, only the reviewer's view shows them;
- the map ``<sha>.canaries.json`` is read through the receipt ``review-canaries`` (a journal event pair): a map, a view or a
  receipt that does not match is ``REVIEW_CANARY_MISMATCH``, never «0 hits»; a run without the receipt is NOT_SEEDED;
- a finding (any severity) or an edit of a canary text is a hit; an edit whose ``old`` is the mutated text never reaches the canon;
- a verdict that stands on canaries alone is refused before the answer is written;
- the soft threshold: 0 of n — REVIEW_UNRELIABLE and accepted=false in report and status, 1..n-1 — weak, all — fine.

The flow tests seed fixed canaries (``_fake_seed``): the generator fixture of these tests has no assertions to mutate."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tests.test_review_requested import _generated_run

STATUS_TEXT = "Результат"


def _fake_seed(document):
    """Two canaries on the fixture candidate: a boundary in the expectation text of case 1, a removed assertion of case 2."""
    work = copy.deepcopy(dict(document))
    first, second = work["test_cases"][0], work["test_cases"][1]
    step, exp = first["steps"][0], first["steps"][0]["expectations"][0]
    original = exp["text"]
    exp["text"] = original + " 6"
    other_step, other_exp = second["steps"][0], second["steps"][0]["expectations"][0]
    rows = [
        {"canary_id": "CAN-1", "class": "boundary", "case_id": first["case_id"], "step_id": step["step_id"], "expectation_id": exp["expectation_id"],
         "assertion_id": None, "field": "expectation", "original": original, "mutated": exp["text"], "markers": ["6"]},
        {"canary_id": "CAN-2", "class": "assertion_removed", "case_id": second["case_id"], "step_id": other_step["step_id"],
         "expectation_id": other_exp["expectation_id"], "assertion_id": None, "field": "assertions", "original": "delta = 1", "mutated": "",
         "markers": ["delta = 1", "delta"]},
    ]
    return work, rows


def _flags(run: dict) -> tuple[Path, str]:
    return run["run_root"], run["attempt_id"]


def _open(run: dict, monkeypatch: pytest.MonkeyPatch, seed: bool = True, invocation: str = "reviewer-subagent-1", fresh: bool = True) -> dict:
    from tools import review_canaries
    from tools.orchestrate_test_case_revision import review_open_step

    if seed:
        monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    return review_open_step(run["run_root"], run["attempt_id"], files={}, output_dir=run["project"] / "bundles", model_id="model-reviewer",
                            invocation_id=invocation, fresh_context=fresh)


def _candidate(run: dict) -> dict:
    from tools.orchestrate_test_case_revision import journal_assembly

    return journal_assembly(*_flags(run))[0]


def _answer(run: dict, verdict: str = "ПРИНЯТО", findings=(), edits=None, corrections=(), effective="candidate", codeword=None) -> dict:
    from tools.canonical_document import document_sha256

    candidate = _candidate(run)
    ref = {"document_id": candidate["document_id"], "revision": candidate["revision"], "document_sha256": document_sha256(candidate)}
    rejected = verdict == "ТРЕБУЕТ ДОРАБОТКИ"
    report = {"verdict": verdict, "candidate": ref, "effective": None if rejected else ref if effective == "candidate" else effective,
              "reviewed_case_ids": [case["case_id"] for case in candidate["test_cases"]], "findings": list(findings), "corrections": list(corrections)}
    artifacts = {"validation_report": report, **({"successor_edits": edits} if edits else {})}
    answer = {"schema_version": "5.0.0", "stage": "tc-reviewer", "artifacts": artifacts, "warnings": []}
    if codeword is not False:
        answer["codeword_seen"] = codeword
    return answer


def _finding(severity: str, related: list[str], message: str = "Контрольная ошибка в ожидании.", code: str = "REVIEW_NOTE") -> dict:
    return {"severity": severity, "code": code, "message": message, "evidence": ["REQ"], "related_ids": related}


def _select(run: dict, answer: dict, **extra) -> dict:
    from tools.orchestrate_test_case_revision import review_select_step

    return review_select_step(run["run_root"], run["attempt_id"], files={}, output_dir=run["project"] / "bundles", verdict=answer, **extra)


def _events(run_root: Path) -> list[dict]:
    return [json.loads(line) for line in (run_root / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def _ids(run: dict) -> tuple[dict, dict]:
    candidate = _candidate(run)
    return candidate["test_cases"][0], candidate["test_cases"][1]


# --------------------------------------------------------------------------------------
# seed: a pure function of the candidate
# --------------------------------------------------------------------------------------

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_seed_is_deterministic_leaves_the_candidate_and_puts_the_removed_assertion_first() -> None:
    from tools.review_canaries import seed

    document = _example()
    before = json.dumps(document, sort_keys=True)
    mutated, canaries = seed(document)
    assert json.dumps(document, sort_keys=True) == before  # the candidate itself is not touched
    assert seed(copy.deepcopy(document)) == (mutated, canaries)
    assert 2 <= len(canaries) <= 3 and len({row["case_id"] for row in canaries}) == len(canaries)
    assert canaries[0]["class"] == "assertion_removed" and canaries[0]["mutated"] == "" and canaries[0]["original"]
    assert [row["canary_id"] for row in canaries] == [f"CAN-{index}" for index in range(1, len(canaries) + 1)]
    assert mutated != document
    for row in canaries:
        case = next(case for case in mutated["test_cases"] if case["case_id"] == row["case_id"])
        assert case != next(case for case in document["test_cases"] if case["case_id"] == row["case_id"])
    changed = [case["case_id"] for case, old in zip(mutated["test_cases"], document["test_cases"]) if case != old]
    assert sorted(changed) == sorted(row["case_id"] for row in canaries)  # nothing else is mutated


def test_seed_classes_are_the_four_and_each_row_validates_against_the_schema() -> None:
    from tools.review_canaries import CLASSES, map_bytes, seed
    from tools.schema_validation import schema_diagnostics

    document = _example()
    from tools.canonical_document import document_sha256

    _mutated, canaries = seed(document)
    assert {row["class"] for row in canaries} <= set(CLASSES)
    root = Path(__file__).resolve().parents[1]
    value = json.loads(map_bytes(document_sha256(document), canaries))
    assert schema_diagnostics(value, root / "schemas" / "review-canaries.schema.json", root) == []


def test_every_class_has_material_in_a_candidate_with_checks() -> None:
    from tools.review_canaries import CLASSES, _options

    document = _example()
    case = next(case for case in document["test_cases"] if any(len(exp["assertions"]) >= 2 for step in case["steps"] for exp in step["expectations"]))
    found = {cls: bool(_options(cls, copy.deepcopy(case), "sha256:" + "0" * 64)) for cls in CLASSES}
    assert found["assertion_removed"] and found["state_check_removed"]


def test_a_small_candidate_gets_fewer_and_a_single_case_none() -> None:
    from tools.review_canaries import seed

    document = _example()
    one = copy.deepcopy(document)
    one["test_cases"] = one["test_cases"][:1]
    assert seed(one)[1] == []
    two = copy.deepcopy(document)
    two["test_cases"] = two["test_cases"][:2]
    assert len(seed(two)[1]) == 1


def test_a_case_of_a_parameter_group_is_never_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.review_view  # noqa: F401  (imported before the patch: it binds param_groups at import)
    from tools import canonical_document, review_canaries

    document = _example()
    grouped = [case for case in document["test_cases"][:-2]]
    monkeypatch.setattr(canonical_document, "param_groups", lambda _document: [{"group_id": "G", "parameter": {}, "cases": grouped}])
    _mutated, canaries = review_canaries.seed(document)
    assert canaries and not {row["case_id"] for row in canaries} & {case["case_id"] for case in grouped}


def test_the_view_shows_the_control_errors_with_the_header_and_hints_of_the_real_candidate() -> None:
    from tools.canonical_document import document_sha256
    from tools.review_canaries import seed
    from tools.review_view import render

    document = _example()
    mutated, canaries = seed(document)
    plain, with_canaries = render(document).decode("utf-8"), render(mutated, basis=document).decode("utf-8")
    assert plain != with_canaries
    assert f"candidate document_sha256: {document_sha256(document)}" in with_canaries and document_sha256(mutated) not in with_canaries
    removed = next(row for row in canaries if row["class"] == "assertion_removed")
    assert removed["original"] in plain and removed["original"] not in _case_block(with_canaries, removed["case_id"], removed["expectation_id"])


def _case_block(view: str, case_id: str, expectation_id: str) -> str:
    start = view.index(f"### {case_id} ")
    rest = view[start:]
    end = rest.find("\n### ", 4)
    block = rest if end == -1 else rest[:end]
    line = next(row for row in block.splitlines() if f"[{expectation_id}]" in row)
    after = block.splitlines()[block.splitlines().index(line) + 1:][:1]
    return line + "\n" + "\n".join(after)


# --------------------------------------------------------------------------------------
# the receipt binds the map and the view to the journal; the canon is not touched
# --------------------------------------------------------------------------------------

def test_review_open_seeds_the_view_binds_it_and_leaves_the_canon_and_the_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries
    from tools.canonical_document import document_sha256
    from tools.pilot_state import read_attempt_receipt

    run = _generated_run(tmp_path)
    opened = _open(run, monkeypatch)
    assert opened["canaries"]["seeded"] == 2 and "CAN-" not in json.dumps(opened, ensure_ascii=False)  # classes and cases are not printed
    candidate = _candidate(run)
    digest = document_sha256(candidate)
    receipt = read_attempt_receipt(*_flags(run), "review-canaries", "ARTIFACT_READ_BACK")["record"]
    view, mapped = review_canaries.view_target(run["run_root"], digest), review_canaries.map_target(run["run_root"], digest)
    assert str(view) == opened["reviewer_view"]
    assert receipt["candidate_digest"] == digest and receipt["canary_count"] == 2
    assert receipt["view_digest"] == review_canaries._digest_of(view.read_bytes()) and receipt["canary_map_digest"] == review_canaries._digest_of(mapped.read_bytes())
    pair = [row["event_type"] for row in _events(run["run_root"]) if row.get("artifact_digest") == receipt["digest"]]
    assert pair == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
    # the view shows the mutated text, the canon and the published bundle keep the original
    mutated_text = candidate["test_cases"][0]["steps"][0]["expectations"][0]["text"] + " 6"
    assert mutated_text in view.read_text(encoding="utf-8")
    bundle = b"".join(path.read_bytes() for path in (run["project"] / "bundles").rglob("*") if path.is_file())
    assert mutated_text.encode("utf-8") not in bundle and document_sha256(candidate) == receipt["candidate_digest"]
    assert review_canaries.verify(*_flags(run)) == json.loads(mapped.read_bytes())["canaries"]


def test_the_same_open_again_changes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    journal = (run["run_root"] / "events.jsonl").read_bytes()
    files = {str(path): path.read_bytes() for path in (run["run_root"] / "review-views").iterdir()}
    _open(run, monkeypatch)
    assert (run["run_root"] / "events.jsonl").read_bytes() == journal
    assert {str(path): path.read_bytes() for path in (run["run_root"] / "review-views").iterdir()} == files


def test_the_view_command_writes_the_view_with_canaries_for_the_assembled_candidate(tmp_path: Path) -> None:
    import subprocess
    import sys

    from tests.test_review_requested import _cmd

    root = Path(__file__).resolve().parents[1]
    run = _generated_run(tmp_path)
    candidate = tmp_path / "r1.json"
    candidate.write_text(json.dumps(_candidate(run), ensure_ascii=False), encoding="utf-8")
    code, _published = _cmd("tools.orchestrate_test_case_revision", "publish", "--candidate", str(candidate), "--output-dir",
                            str(run["run_root"] / "published"), "--run-root", str(run["run_root"]), "--attempt-id", run["attempt_id"])
    assert code == 0
    command = [sys.executable, "-m", "tools.review_view", "--project", str(run["project"]), "--run", run["run_root"].name, "--candidate", str(candidate)]
    out = json.loads(subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8").stdout)
    assert out["status"] == "ok" and out["canaries"] == 0 and out["canaries_created"] is True, out  # the fixture has no checks to mutate
    again = json.loads(subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8").stdout)
    assert again["canaries_created"] is False and again["bytes"] == out["bytes"]


# --------------------------------------------------------------------------------------
# forgery: a map, a view or a receipt that does not match the journal is a refusal
# --------------------------------------------------------------------------------------

def _forge(run: dict, what: str) -> None:
    from tools import review_canaries
    from tools.canonical_document import document_sha256

    digest = document_sha256(_candidate(run))
    mapped, view = review_canaries.map_target(run["run_root"], digest), review_canaries.view_target(run["run_root"], digest)
    if what == "map-edited":
        value = json.loads(mapped.read_bytes())
        value["canaries"] = []  # «no canaries» is the forger's best answer
        mapped.write_bytes(review_canaries.canonical_bytes(value))
    elif what == "map-deleted":
        mapped.unlink()
    elif what == "view-edited":
        view.write_bytes(view.read_bytes().replace(" 6".encode(), b""))
    elif what == "view-deleted":
        view.unlink()
    elif what == "receipt-deleted":
        (run["run_root"] / "review-canaries" / f"{run['attempt_id']}.json").unlink()
    elif what == "receipt-edited":
        target = run["run_root"] / "review-canaries" / f"{run['attempt_id']}.json"
        value = json.loads(target.read_bytes())
        value["canary_count"] = 0
        target.write_bytes(review_canaries.canonical_bytes(value))


@pytest.mark.parametrize("what", ["map-edited", "map-deleted", "view-edited", "view-deleted", "receipt-edited", "receipt-deleted"])
def test_a_forged_or_missing_canary_file_is_a_refusal_not_zero_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    _forge(run, what)
    before = (run["run_root"] / "events.jsonl").read_bytes()
    with pytest.raises(ValueError, match="REVIEW_CANARY_MISMATCH"):
        _select(run, _answer(run))
    assert (run["run_root"] / "events.jsonl").read_bytes() == before  # nothing recorded


def test_a_run_without_the_receipt_is_not_seeded_and_warns_unmeasured(tmp_path: Path) -> None:
    from tests.test_review_requested import _complete, _reviewed, _select as legacy_select
    from tools import review_canaries
    from tools.run_report import build_report

    run, review = _reviewed(tmp_path, start="command")  # the nine commands: no review-open of P7, no receipt
    assert review_canaries.verify(*_flags(run)) is None
    assert _complete(run)[0] == 0 and legacy_select(run, review)[0] == 0  # the old commands still work
    found = review_canaries.summary(*_flags(run))
    assert found["status"] == "NOT_SEEDED" and found["warning"] == "REVIEW_UNMEASURED"
    report, _warnings = build_report(run["project"], run["run_root"].name)
    assert "REVIEW_UNMEASURED" in report["warnings"] and report["review_canaries"]["status"] == "NOT_SEEDED"


# --------------------------------------------------------------------------------------
# hits
# --------------------------------------------------------------------------------------

def _canaries(run: dict) -> list[dict]:
    from tools import review_canaries

    return review_canaries.verify(*_flags(run))


def test_a_finding_that_names_the_mutated_element_is_a_hit_of_any_severity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, two = _ids(run)
    exp_one, exp_two = one["steps"][0]["expectations"][0]["expectation_id"], two["steps"][0]["expectations"][0]["expectation_id"]
    answer = _answer(run, findings=[_finding("INFO", [exp_one]), _finding("WARNING", [two["case_id"], two["steps"][0]["step_id"]])])
    assert review_canaries.hit_ids(_canaries(run), answer) == {"CAN-1": "finding", "CAN-2": "finding"}
    bare = _answer(run, findings=[_finding("WARNING", [one["case_id"], two["case_id"]], "Во всех кейсах нет проверки.")])
    assert review_canaries.hit_ids(_canaries(run), bare) == {}  # a bare case ID in a general finding is no hit
    with_value = _answer(run, findings=[_finding("WARNING", [one["case_id"]], "Ожидание называет 6 дней, а требование — 5.")])
    assert review_canaries.hit_ids(_canaries(run), with_value) == {"CAN-1": "finding"}


@pytest.mark.parametrize(("hits", "status", "note"), [
    (0, "UNRELIABLE", "REVIEW_UNRELIABLE"), (1, "WEAK", "пропущено 2 из 3"), (2, "WEAK", "пропущено 1 из 3"), (3, "ALL_CAUGHT", "поймано 3 из 3")])
def test_the_soft_threshold_of_three(hits: int, status: str, note: str) -> None:
    from tools.review_canaries import outcome

    canaries = [{"canary_id": f"CAN-{n}", "class": cls, "case_id": f"TC-{n}", "step_id": f"STEP-{n}", "expectation_id": f"EXP-{n}", "assertion_id": None,
                 "field": "assertions", "original": "a", "mutated": "", "markers": []}
                for n, cls in ((1, "assertion_removed"), (2, "boundary"), (3, "status"))]
    findings = [_finding("INFO", [f"EXP-{n}"]) for n in range(1, hits + 1)]
    answer = {"artifacts": {"validation_report": {"findings": findings}}}
    result = outcome(canaries, answer)
    assert result["status"] == status and result["seeded"] == 3 and result["caught"] == hits and note in result["note"]
    assert (result["warning"] is not None) == (status in {"UNRELIABLE", "WEAK"})
    if status == "WEAK":
        assert "классы" in result["note"] and "сигнал качества ревью, не защита" in result["note"]
    assert outcome([], answer)["status"] == "NONE"
    assert outcome(None, answer)["status"] == "NOT_SEEDED"


# --------------------------------------------------------------------------------------
# the canary never reaches the canon; a verdict on canaries alone is refused before it is written
# --------------------------------------------------------------------------------------

def _canary_edit(run: dict, new: str = "Результат") -> dict:
    one, _two = _ids(run)
    step, exp = one["steps"][0], one["steps"][0]["expectations"][0]
    return {"correction_id": "FIX-1", "case_id": one["case_id"], "field": "expectation", "step_id": step["step_id"],
            "expectation_id": exp["expectation_id"], "old": exp["text"] + " 6", "new": new}


def _correction(identifier: str) -> dict:
    return {"id": identifier, "correction_kind": "MECHANICAL", "related_ids": ["TC-B1-payment"], "description": "Правка.", "evidence": ["REQ"]}


def test_an_edit_with_the_mutated_text_is_dropped_and_the_canon_keeps_the_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.canonical_document import document_sha256
    from tools.review_edits import apply_edits

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    candidate = _candidate(run)
    one, _two = _ids(run)
    real = {"correction_id": "FIX-2", "case_id": one["case_id"], "field": "title", "old": one["title"], "new": one["title"] + " (уточнено)"}
    successor, rows = apply_edits(candidate, [real], {"FIX-2"})
    assert rows == []
    answer = _answer(run, "AUTO_FIX_APPLIED", edits=[_canary_edit(run), real], corrections=[_correction("FIX-1"), _correction("FIX-2")],
                     effective={"document_id": successor["document_id"], "revision": successor["revision"], "document_sha256": document_sha256(successor)})
    selected = _select(run, answer, dry_run=True)
    assert selected["status"] == "EFFECTIVE_SELECTED" and selected["review_canaries"]["caught"] == 1
    assert selected["review_canaries"]["hits"] == {"CAN-1": "edit"}
    done = _select(run, answer)
    from tools.pilot_state import read_effective_canonical

    effective = read_effective_canonical(*_flags(run))["document"]
    assert effective["test_cases"][0]["title"].endswith("(уточнено)")
    assert effective["test_cases"][0]["steps"][0]["expectations"][0]["text"] == one["steps"][0]["expectations"][0]["text"]  # not the mutated text
    assert done["review_canaries"]["caught"] == 1


def test_an_answer_whose_only_edits_act_on_canaries_is_refused_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    before = (run["run_root"] / "events.jsonl").read_bytes()
    answer = _answer(run, "AUTO_FIX_APPLIED", edits=[_canary_edit(run)], corrections=[_correction("FIX-1")])
    with pytest.raises(ValueError, match="REVIEW_ONLY_CANARY_FIXES"):
        _select(run, answer)
    assert (run["run_root"] / "events.jsonl").read_bytes() == before


def test_a_rework_that_stands_on_canaries_alone_is_refused_and_a_retry_with_info_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, two = _ids(run)
    exp_one, exp_two = one["steps"][0]["expectations"][0]["expectation_id"], two["steps"][0]["expectations"][0]["expectation_id"]
    before = (run["run_root"] / "events.jsonl").read_bytes()
    only = _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [exp_one], "Ожидание называет 6, а требование — 5.", code="BOUNDARY"),
                                                                _finding("BLOCKING", [exp_two], "Нет проверки delta = 1.")])
    with pytest.raises(ValueError) as refused:
        _select(run, only)
    message = str(refused.value)
    assert "REVIEW_CANARY_ONLY_VERDICT" in message and "#1 BOUNDARY" in message and "INFO" in message and "--transport-attempts 2" in message
    assert (run["run_root"] / "events.jsonl").read_bytes() == before  # nothing recorded: the stage keeps its retry
    retry = _answer(run, "ПРИНЯТО", findings=[_finding("INFO", [exp_one]), _finding("INFO", [exp_two])])
    done = _select(run, retry, transport_attempts=2)
    assert done["status"] == "EFFECTIVE_SELECTED" and done["review_canaries"]["status"] == "ALL_CAUGHT" and done["review_canaries"]["caught"] == 2
    assert done["review_warnings"] == []


def test_a_rework_with_a_real_blocking_finding_beside_the_canaries_stands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    exp_one = one["steps"][0]["expectations"][0]["expectation_id"]
    answer = _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [exp_one], "Ожидание называет 6, а требование — 5."),
                                                        _finding("BLOCKING", ["REQ-OTHER"], "Нет кейса на требование.")])
    done = _select(run, answer)
    assert done["status"] == "REWORK" and done["review_canaries"]["caught"] == 1 and done["review_canaries"]["status"] == "WEAK"
    assert "REVIEW_WEAK" in done["review_warnings"]


# --------------------------------------------------------------------------------------
# the report and the status
# --------------------------------------------------------------------------------------

def test_zero_hits_make_the_review_unreliable_in_the_report_and_the_status_and_not_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_pipeline import _status_result
    from tools.run_report import build_report, render_markdown

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    done = _select(run, _answer(run))  # ПРИНЯТО, no finding: the verdict stands
    assert done["status"] == "EFFECTIVE_SELECTED" and done["review_canaries"]["status"] == "UNRELIABLE"
    assert "REVIEW_UNRELIABLE" in done["review_warnings"]
    report, _warnings = build_report(run["project"], run["run_root"].name)
    assert report["review_canaries"] == {**done["review_canaries"]} and "REVIEW_UNRELIABLE" in report["warnings"]
    assert report["counts"]["accepted"] is False
    text = render_markdown(report)
    assert "REVIEW_UNRELIABLE" in text and "сигнал качества ревью, не защита" in text
    assert "delta = 1" not in json.dumps(report, ensure_ascii=False)  # the map itself is not in the report
    status = _status_result(run["project"], run["run_root"].name)
    assert status["review_unreliable"] is True and status["review_canaries"]["caught"] == 0 and "REVIEW_UNRELIABLE" in status["warnings"]


def test_a_weak_review_is_a_warning_not_a_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_report import build_report

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    done = _select(run, _answer(run, findings=[_finding("INFO", [one["steps"][0]["expectations"][0]["expectation_id"]])]))
    assert done["review_canaries"]["status"] == "WEAK" and "пропущено 1 из 2" in done["review_canaries"]["note"]
    assert "assertion_removed" in done["review_canaries"]["note"]
    report, _w = build_report(run["project"], run["run_root"].name)
    assert report["review_canaries"]["status"] == "WEAK" and "REVIEW_WEAK" in report["warnings"] and "REVIEW_UNRELIABLE" not in report["warnings"]


# --------------------------------------------------------------------------------------
# the journal decides «seeded or not»; a refusal stands on a strict hit only
# --------------------------------------------------------------------------------------

def test_the_receipt_events_are_marked_and_a_run_without_them_is_not_seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries

    run = _generated_run(tmp_path)
    assert review_canaries.verify(*_flags(run)) is None  # no event of the receipt: not seeded, nothing to read
    _open(run, monkeypatch)
    marked = [row for row in _events(run["run_root"]) if row.get("batch_id") == review_canaries.BATCH_ID]
    assert [row["event_type"] for row in marked] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]


def test_a_deleted_receipt_file_is_a_mismatch_in_report_and_status_even_before_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries
    from tools.run_pipeline import _status_result
    from tools.run_report import build_report

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    _forge(run, "receipt-deleted")
    with pytest.raises(review_canaries.CanaryMismatch, match="REVIEW_CANARY_MISMATCH"):
        review_canaries.verify(*_flags(run))
    found = review_canaries.summary(*_flags(run))
    assert found["status"] == "MISMATCH" and found["warning"] == "REVIEW_CANARY_MISMATCH"
    report, _warnings = build_report(run["project"], run["run_root"].name)
    assert report["review_canaries"]["status"] == "MISMATCH" and "REVIEW_CANARY_MISMATCH" in report["warnings"]
    status = _status_result(run["project"], run["run_root"].name)
    assert status["review_canaries"]["status"] == "MISMATCH" and "REVIEW_CANARY_MISMATCH" in status["warnings"]


def test_any_failure_to_read_the_canaries_of_a_seeded_run_is_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)

    def broken(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(review_canaries, "_read_canaries", broken)
    found = review_canaries.summary(*_flags(run))
    assert found["status"] == "MISMATCH" and found["warning"] == "REVIEW_CANARY_MISMATCH" and "disk" in found["note"]


def _canary(**overrides) -> dict:
    return {"canary_id": "CAN-1", "class": "boundary", "case_id": "TC-S", "step_id": "STEP-S1", "expectation_id": "EXP-S1", "assertion_id": None,
            "field": "expectation", "original": "5 дней", "mutated": "6 дней", "markers": ["6", "5"], **overrides}


def test_ids_are_compared_whole_s1_is_not_s10() -> None:
    from tools.review_canaries import finding_hits

    other = {"severity": "WARNING", "code": "X", "message": "В STEP-S10 называется 6.", "evidence": ["STEP-S10"], "related_ids": ["STEP-S10", "EXP-S10"]}
    assert not finding_hits(_canary(), other) and not finding_hits(_canary(), other, strict=True)
    own = {**other, "message": "В STEP-S1 называется 6.", "related_ids": ["STEP-S1"]}
    assert finding_hits(_canary(), own) and finding_hits(_canary(), own, strict=True)


def test_a_strict_hit_needs_the_id_in_related_ids_and_the_value_in_the_text() -> None:
    from tools.review_canaries import finding_hits

    about_else = {"severity": "BLOCKING", "code": "X", "message": "Нет кейса на требование.", "evidence": ["REQ"], "related_ids": ["STEP-S1"]}
    assert finding_hits(_canary(), about_else) and not finding_hits(_canary(), about_else, strict=True)  # statistics only
    by_text = {**about_else, "message": "В STEP-S1 называется 6.", "related_ids": ["TC-S"]}
    assert finding_hits(_canary(), by_text) and not finding_hits(_canary(), by_text, strict=True)  # the ID is only in the text
    case_and_value = {**about_else, "message": "Называется 6.", "related_ids": ["TC-S"]}
    assert finding_hits(_canary(), case_and_value) and not finding_hits(_canary(), case_and_value, strict=True)


def test_a_real_blocking_finding_in_the_step_of_a_canary_about_something_else_makes_the_rework_stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    step = one["steps"][0]["step_id"]
    answer = _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [step], "Шаг не проверяет требование REQ-9 вовсе.", code="REQ_GAP")])
    done = _select(run, answer)  # not refused: the finding does not name the value of the canary
    assert done["status"] == "REWORK"


def test_a_receipt_map_and_view_planted_before_review_open_do_not_switch_the_canaries_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The files agree with each other (count 0, an empty map, a view without control errors) and the journal has no event of them:
    review-open does not take them for a cut-short seeding — they are not what the seeding of the journal's candidate gives."""
    from tools import review_canaries
    from tools.canonical_document import document_sha256
    from tools.orchestrate_test_case_revision import _PROFILE, journal_assembly, publish_command
    from tools.pilot_state import publish_attempt_receipt
    from tools.review_view import render

    run = _generated_run(tmp_path)
    candidate = journal_assembly(*_flags(run))[0]
    publish_command(candidate, run["project"] / "bundles", _PROFILE, run_root=run["run_root"], attempt_id=run["attempt_id"])
    digest = document_sha256(candidate)
    plain_view, empty_map = render(candidate), review_canaries.map_bytes(digest, [])
    (run["run_root"] / "review-views").mkdir(parents=True, exist_ok=True)
    review_canaries.view_target(run["run_root"], digest).write_bytes(plain_view)
    review_canaries.map_target(run["run_root"], digest).write_bytes(empty_map)
    publish_attempt_receipt(*_flags(run), "review-canaries", review_canaries.receipt_facts(digest, plain_view, empty_map, 0))  # a file, no events
    assert review_canaries.verify(*_flags(run)) is None  # the journal knows nothing of it
    with pytest.raises(ValueError, match="REVIEW_CANARY_MISMATCH"):
        _open(run, monkeypatch)  # the seeding gives two canaries here, the planted files give none
    assert [row for row in _events(run["run_root"]) if row.get("batch_id") == review_canaries.BATCH_ID] == []  # no event was added for them
