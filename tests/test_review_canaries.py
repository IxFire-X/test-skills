"""P7: canaries in the reviewer's view — seeded by code, bound to the journal, counted at ``submit`` of the review.

- ``seed`` is a pure function of the candidate: ⌈√n⌉ control errors (3..15), none in a parameter group, "assertion removed" first; the
  candidate, its bundles and its journal events stay as they are, only the reviewer's view shows them;
- the map ``<sha>.canaries.json`` is read through the receipt ``review-canaries`` (a journal event pair): a map, a view or a
  receipt that does not match is ``REVIEW_CANARY_MISMATCH``, never «0 hits»; a run without the receipt is NOT_SEEDED;
- a finding (any severity) or an edit of a canary text is a hit; an edit whose ``old`` is the mutated text never reaches the canon;
- a verdict that stands on canaries alone is refused before the answer is written;
- the soft threshold: under a third caught — REVIEW_UNRELIABLE and accepted=false in report and status, more than (n−1)//3 missed — weak,
  fewer — mostly caught, all — fine (up to three errors this is "0 — unreliable, 1..n-1 — weak, all — fine").

The flow tests seed fixed canaries (``_fake_seed``): the generator fixture of these tests has no assertions to mutate."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_review_requested import _generated_run
from tools import canonical_document, finalize_attempt, orchestrate_test_case_revision as orchestrate, pilot_state
from tools import review_canaries, stage_steps
from tools.canonical_document import document_sha256
from tools.orchestrate_test_case_revision import _PROFILE, journal_assembly, publish_command
from tools.orchestrate_test_case_revision import publish_unreviewed_candidate
from tools.pilot_state import publish_attempt_receipt, publish_model_stage_artifact, read_attempt_receipt
from tools.review_canaries import CLASSES, CLASS_NAMES, _options, canary_count, finding_hits, map_bytes, outcome, seed
from tools.review_edits import apply_edits
from tools.review_view import render
from tools.revision_selection import review_outcome
from tools.run_pipeline import _status_result
from tools.run_report import _canary_lines, _warning_line, build_report, render_markdown
from tools.schema_validation import schema_diagnostics

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
    from tools import review_canaries, stage_steps

    if seed:
        monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    return stage_steps.prepare(run["run_root"], "tc-reviewer:canonical", invocation_id=invocation, fresh_context=fresh)


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
    from tools import api, stage_steps

    slot = stage_steps.slot(run["run_root"], "tc-reviewer:canonical")
    slot.parent.mkdir(exist_ok=True)
    slot.write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    return stage_steps.submit(run["run_root"], "tc-reviewer:canonical", api._load_answer, **extra)


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
    document = _example()
    before = json.dumps(document, sort_keys=True)
    mutated, canaries = seed(document)
    assert json.dumps(document, sort_keys=True) == before  # the candidate itself is not touched
    assert seed(copy.deepcopy(document)) == (mutated, canaries)
    assert len(canaries) == 8 and len({row["case_id"] for row in canaries}) == len(canaries)  # 64 cases: ⌈√64⌉
    assert canaries[0]["class"] == "assertion_removed" and canaries[0]["mutated"] == "" and canaries[0]["original"]
    assert [row["canary_id"] for row in canaries] == [f"CAN-{index}" for index in range(1, len(canaries) + 1)]
    assert mutated != document
    for row in canaries:
        case = next(case for case in mutated["test_cases"] if case["case_id"] == row["case_id"])
        assert case != next(case for case in document["test_cases"] if case["case_id"] == row["case_id"])
    changed = [case["case_id"] for case, old in zip(mutated["test_cases"], document["test_cases"]) if case != old]
    assert sorted(changed) == sorted(row["case_id"] for row in canaries)  # nothing else is mutated


def test_seed_classes_are_the_four_and_each_row_validates_against_the_schema() -> None:
    document = _example()

    _mutated, canaries = seed(document)
    assert {row["class"] for row in canaries} <= set(CLASSES)
    root = Path(__file__).resolve().parents[1]
    value = json.loads(map_bytes(document_sha256(document), canaries))
    assert schema_diagnostics(value, root / "schemas" / "review-canaries.schema.json", root) == []


def test_every_class_has_material_in_a_candidate_with_checks() -> None:
    document = _example()
    case = next(case for case in document["test_cases"] if any(len(exp["assertions"]) >= 2 for step in case["steps"] for exp in step["expectations"]))
    found = {cls: bool(_options(cls, copy.deepcopy(case), "sha256:" + "0" * 64)) for cls in CLASSES}
    assert found["assertion_removed"] and found["state_check_removed"]


def test_a_small_candidate_gets_fewer_and_a_single_case_none() -> None:
    document = _example()
    one = copy.deepcopy(document)
    one["test_cases"] = one["test_cases"][:1]
    assert seed(one)[1] == []
    two = copy.deepcopy(document)
    two["test_cases"] = two["test_cases"][:2]
    assert len(seed(two)[1]) == 1


@pytest.mark.parametrize(("cases", "wanted"), [(0, 0), (1, 0), (2, 1), (4, 3), (9, 3), (10, 4), (64, 8), (212, 15), (5000, 15)])
def test_the_number_of_canaries_grows_with_the_set_from_three_to_fifteen(cases: int, wanted: int) -> None:
    assert canary_count(cases) == wanted


def _clone(document: dict, times: int) -> dict:
    """The cases of the document *times* over, every case, step, expectation and assertion ID made new."""
    import re

    ids = re.compile(r"((?:TC|STEP|EXP|ASSERT)-[A-Za-z0-9_.:-]*[A-Za-z0-9_])")
    big = copy.deepcopy(document)
    big["test_cases"] = [case for part in range(times) for case in json.loads(ids.sub(rf"\1.c{part}", json.dumps(document["test_cases"], ensure_ascii=False)))]
    return big


def test_a_set_of_212_cases_gets_fifteen_canaries_mostly_omissions_and_the_map_is_valid() -> None:
    document = _clone(_example(), 4)
    document["test_cases"] = document["test_cases"][:212]
    _mutated, canaries = seed(document)
    assert len(canaries) == 15 and len({row["case_id"] for row in canaries}) == 15
    assert [row["canary_id"] for row in canaries] == [f"CAN-{n}" for n in range(1, 16)]
    assert sum(row["class"] in {"assertion_removed", "state_check_removed"} for row in canaries) >= 8  # the errors a reviewer misses are the majority
    root = Path(__file__).resolve().parents[1]
    assert schema_diagnostics(json.loads(map_bytes(document_sha256(document), canaries)), root / "schemas" / "review-canaries.schema.json", root) == []


def test_the_canaries_do_not_make_the_view_bigger_by_a_tenth() -> None:
    document = _example()
    mutated, canaries = seed(document)
    assert len(canaries) == 8 and len(render(mutated, basis=document)) <= 1.10 * len(render(document))


def test_a_case_of_a_parameter_group_is_never_mutated(monkeypatch: pytest.MonkeyPatch) -> None:
    import tools.review_view  # noqa: F401  (imported before the patch: it binds param_groups at import)

    document = _example()
    grouped = [case for case in document["test_cases"][:-2]]
    monkeypatch.setattr(canonical_document, "param_groups", lambda _document: [{"group_id": "G", "parameter": {}, "cases": grouped}])
    _mutated, canaries = review_canaries.seed(document)
    assert canaries and not {row["case_id"] for row in canaries} & {case["case_id"] for case in grouped}


def test_the_view_shows_the_control_errors_with_the_header_and_hints_of_the_real_candidate() -> None:
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
    from tools.publish_test_case_bundle import build_bundle

    built = build_bundle(candidate)  # the set `select` publishes is built from the canon; before the verdict no file exists
    assert not list((run["run_root"] / "published").rglob("*.json"))
    assert mutated_text.encode("utf-8") not in built.json_bytes + built.preview_bytes + built.csv_bytes and document_sha256(candidate) == receipt["candidate_digest"]
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

    root = Path(__file__).resolve().parents[1]
    run = _generated_run(tmp_path)
    candidate = Path(run["project"]) / "r1.json"
    candidate.write_text(json.dumps(_candidate(run), ensure_ascii=False), encoding="utf-8")
    publish_unreviewed_candidate(_candidate(run), run["run_root"] / "published", run_root=run["run_root"], attempt_id=run["attempt_id"])  # the setup: r1 is in the journal
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


# --------------------------------------------------------------------------------------
# hits
# --------------------------------------------------------------------------------------

def _canaries(run: dict) -> list[dict]:
    from tools import review_canaries

    return review_canaries.verify(*_flags(run))


def test_a_finding_that_names_the_mutated_element_is_a_hit_of_any_severity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.mark.parametrize(("hits", "status"), [(15, "ALL_CAUGHT"), (12, "MOSTLY_CAUGHT"), (11, "MOSTLY_CAUGHT"), (10, "WEAK"), (5, "WEAK"), (4, "UNRELIABLE"), (0, "UNRELIABLE")])
def test_the_threshold_of_fifteen_is_a_share_not_one_miss(hits: int, status: str) -> None:
    canaries = [{"canary_id": f"CAN-{n}", "class": CLASSES[n % 4], "case_id": f"TC-{n}", "step_id": f"STEP-{n}", "expectation_id": f"EXP-{n}", "assertion_id": None,
                 "field": "assertions", "original": "a", "mutated": "", "markers": []} for n in range(1, 16)]
    answer = {"artifacts": {"validation_report": {"findings": [_finding("INFO", [f"EXP-{n}"]) for n in range(1, hits + 1)]}}}
    result = outcome(canaries, answer)
    assert result["status"] == status and (result["warning"] is not None) == (status in {"UNRELIABLE", "WEAK"})


def test_the_outcome_counts_the_caught_by_kind_of_error() -> None:
    canaries = [{"canary_id": f"CAN-{n}", "class": cls, "case_id": f"TC-{n}", "step_id": f"STEP-{n}", "expectation_id": f"EXP-{n}", "assertion_id": None,
                 "field": "assertions", "original": "a", "mutated": "", "markers": []} for n, cls in ((1, "boundary"), (2, "state_check_removed"), (3, "state_check_removed"), (4, "status"))]
    answer = {"artifacts": {"validation_report": {"findings": [_finding("INFO", ["EXP-1"]), _finding("INFO", ["EXP-2"]), _finding("INFO", ["EXP-4"])]}}}
    assert outcome(canaries, answer)["by_class"] == {"boundary": {"seeded": 1, "caught": 1}, "status": {"seeded": 1, "caught": 1}, "state_check_removed": {"seeded": 2, "caught": 1}}


def test_the_outcome_counts_the_blocking_findings_that_stand_on_canaries() -> None:
    canaries = [{"canary_id": f"CAN-{n}", "class": "assertion_removed", "case_id": f"TC-{n}", "step_id": f"STEP-{n}", "expectation_id": f"EXP-{n}", "assertion_id": None,
                 "field": "assertions", "original": "a", "mutated": "", "markers": []} for n in (1, 2)]
    findings = [_finding("BLOCKING", ["TC-1", "EXP-1"]), _finding("BLOCKING", ["TC-2", "EXP-2"]), _finding("BLOCKING", ["TC-3", "EXP-3"]), _finding("WARNING", ["TC-1", "EXP-1"])]
    assert outcome(canaries, {"artifacts": {"validation_report": {"findings": findings}}})["blocking"] == 2  # the real case TC-3 and a warning are not counted


def test_the_report_says_how_many_blocking_findings_were_on_canaries_and_that_they_reject_no_case() -> None:
    line = "- блокирующих замечаний ревьюера к контрольным ошибкам: 5 — они кейсы не отклоняют (отклоняют только замечания к настоящим кейсам)"
    base = {"status": "ALL_CAUGHT", "seeded": 10, "caught": 10, "note": "n"}
    assert line in "\n".join(_canary_lines({"review_canaries": {**base, "blocking": 5}}))
    assert "блокирующих замечаний" not in "\n".join(_canary_lines({"review_canaries": {**base, "blocking": 0}}))


def test_the_warning_of_an_unreliable_review_names_how_many_control_errors_were_caught() -> None:
    line = _warning_line("REVIEW_UNRELIABLE", {"review_canaries": {"seeded": 8, "caught": 2}})
    assert "поймано 2 из 8" in line and "ни одна" not in line
    assert "пропущено 3 из 8" in _warning_line("REVIEW_WEAK", {"review_canaries": {"seeded": 8, "caught": 5}})


def test_the_report_shows_the_catch_by_kind_and_the_model_of_the_reviewer() -> None:
    canaries = {"status": "MOSTLY_CAUGHT", "seeded": 15, "caught": 12, "missed_classes": ["state_check_removed"], "note": "n",
                "by_class": {"boundary": {"seeded": 5, "caught": 5}, "state_check_removed": {"seeded": 5, "caught": 2}}}
    text = "\n".join(_canary_lines({"review_canaries": canaries, "review_model": "haiku-5-5"}))
    assert "- по виду «сдвинута граница»: поймано 5 из 5 (100 %)" in text and "- по виду «убран пункт проверок»: поймано 2 из 5 (40 %)" in text and "haiku-5-5" in text


@pytest.mark.parametrize(("label", "shown"), [("haiku-5-5", "haiku-5-5"), ("vendor/haiku-5-5", None), ("h" * 100, None)])  # prepare lets a label of 256 signs with a slash in; the report takes the narrower identifier only
def test_the_report_names_the_model_the_reviewer_was_given_as_an_identifier_or_not_at_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str, shown: str | None) -> None:
    run = _generated_run(tmp_path)
    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    stage_steps.prepare(run["run_root"], "tc-reviewer:canonical", model_id=label, invocation_id="reviewer-subagent-1", fresh_context=True)
    _select(run, _answer(run))
    report, _w = build_report(run["project"], run["run_root"].name)
    assert report["review_model"] == shown and set(report["review_canaries"]["by_class"]) == {"boundary", "assertion_removed"}
    assert label not in json.dumps(report, ensure_ascii=False) or shown == label


def test_the_role_asks_for_the_model_label_at_the_step_of_the_review() -> None:
    line = next(row for row in (Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8").splitlines()
                if "prepare --stage tc-reviewer:canonical" in row)
    assert "--model-id" in line  # without it review_model of the report stays empty in a live run


def test_a_copy_of_the_response_code_in_the_body_is_not_removed_as_a_control_error() -> None:
    def assertion(kind: str, value: int, **actual: str) -> dict:
        return {"assertion_id": f"ASSERT-{kind}-{actual.get('pointer', '')}", "operator": "equals", "actual": {"kind": kind, **actual}, "expected": {"kind": "literal", "value": value}}

    code, copy_of_code, other = assertion("http_status", 404), assertion("http_body", 404, pointer="/status"), assertion("http_body", 7, pointer="/count")
    exp = {"expectation_id": "EXP-1", "text": "404", "assertions": [code, copy_of_code, other]}
    case = {"case_id": "TC-1", "steps": [{"step_id": "STEP-1", "expectations": [exp]}]}
    options = _options("state_check_removed", case, "sha256:" + "0" * 64)
    assert [row["assertion_id"] for row, _apply in options] == [other["assertion_id"]]
    exp["assertions"] = [code, copy_of_code]  # nothing but the code and its copy: no error that a reviewer can see
    assert _options("state_check_removed", case, "sha256:" + "0" * 64) == []


def test_fifteen_canaries_are_bound_by_the_receipt_and_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def many(document):
        work, rows = _fake_seed(document)
        return work, [{**rows[n % 2], "canary_id": f"CAN-{n + 1}"} for n in range(15)]

    run = _generated_run(tmp_path)
    monkeypatch.setattr(review_canaries, "seed", many)
    _open(run, monkeypatch, seed=False)
    assert [row["canary_id"] for row in review_canaries.verify(*_flags(run))] == [f"CAN-{n}" for n in range(1, 16)]


def test_a_vague_blocking_finding_on_a_canary_case_rejects_the_case_at_three_and_at_eight_canaries() -> None:
    document = _example()
    _mutated, canaries = seed(document)
    case_id = canaries[0]["case_id"]
    vague = {"findings": [{"severity": "BLOCKING", "code": "REVIEW_NOTE", "message": "Ошибка в кейсе.", "evidence": [], "related_ids": [case_id]}]}
    for rows in (canaries[:3], canaries):
        done = review_outcome(document, vague, rows)
        assert done["kind"] == "PARTIAL" and done["rejected"] == [case_id]
    strict = {"findings": [{**vague["findings"][0], "related_ids": [canaries[0]["expectation_id"]]}]}
    assert review_outcome(document, strict, canaries)["kind"] == "ACCEPTED_WHOLE"  # the finding that names the expectation of the canary rejects nothing


def test_vague_blocking_findings_on_every_canary_case_are_held_by_the_share_limit() -> None:
    document = _example()
    _mutated, canaries = seed(document)
    wide = {"findings": [{"severity": "BLOCKING", "code": "REVIEW_NOTE", "message": "Ошибка.", "evidence": [], "related_ids": [row["case_id"]]} for row in canaries]}
    done = review_outcome(document, wide, canaries, share=10)
    assert done["kind"] == "REWORK" and done["reason"] == "OVER_SHARE"


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
    assert selected["status"] == "EFFECTIVE_SELECTED"
    done = _select(run, answer)
    assert done["review_canaries"]["hits"] == {"CAN-1": "edit"}
    from tools.pilot_state import read_effective_canonical

    effective = read_effective_canonical(*_flags(run))["document"]
    assert effective["test_cases"][0]["title"].endswith("(уточнено)")
    assert effective["test_cases"][0]["steps"][0]["expectations"][0]["text"] == one["steps"][0]["expectations"][0]["text"]  # not the mutated text
    assert done["review_canaries"]["caught"] == 1


def test_a_trial_run_does_not_tell_what_the_canaries_and_the_word_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    answer = _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [one["steps"][0]["expectations"][0]["expectation_id"]], "Нет проверки.")])
    trial = _select(run, answer, dry_run=True)
    assert {"review_canaries", "codeword", "review_warnings", "independence"}.isdisjoint(trial) and trial["status"] == "REWORK_PENDING"
    assert {"review_canaries", "codeword"} <= set(_select(run, answer))  # the count and the word are for the real call


def test_fixes_on_canaries_alone_accept_the_set_as_is_through_finish_and_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    """AUTO_FIX_APPLIED whose edits all stand on canaries: no refusal, no retry — the candidate is the set (as a ТРЕБУЕТ ДОРАБОТКИ on canaries
    alone is).  The ledger binds the recorded answer, the effective canonical is the candidate, finish and the report agree."""
    from tests.test_finish import _finish
    from tools.canonical_document import document_sha256
    from tools.run_report import build_report

    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    answer = _answer(run, "AUTO_FIX_APPLIED", edits=[_canary_edit(run)], corrections=[_correction("FIX-1")])
    done = _select(run, answer)
    assert done["status"] == "EFFECTIVE_SELECTED" and done["review_canaries"]["caught"] >= 1, done  # the edit on a canary is a catch
    effective = pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"])["document"]  # the receipt check binds it
    assert document_sha256(effective) == document_sha256(_candidate(run))
    assert pilot_state.terminal_reviewer_evidence(run["run_root"], run["attempt_id"])["authoritative_verdict"] == "ACCEPTED"
    _code, finished = _finish(run["project"], run["run_root"], capsys)  # cases-only: MANUAL_ONLY, accepted=false is the norm (as for ПРИНЯТО)
    assert finished["completion"] == "COMPLETE" and [path.name for path in (run["run_root"] / "published").glob("*.zephyr-scale.csv")] == [
        f"{effective['document_id']}.r1.zephyr-scale.csv"], finished  # the candidate itself is the published set
    acceptance = build_report(run["project"], run["run_root"].name)[0]["acceptance"]
    assert (acceptance["verdict_given"], acceptance["outcome"]) == ("AUTO_FIX_APPLIED", "ACCEPTED_WHOLE")


def _boundary_and_removed(run: dict) -> tuple[dict, dict]:
    one, two = _ids(run)
    return (_finding("BLOCKING", [one["steps"][0]["expectations"][0]["expectation_id"]], "Ожидание называет 6, а требование — 5.", code="BOUNDARY"),
            _finding("BLOCKING", [two["steps"][0]["expectations"][0]["expectation_id"]], "Нет проверки delta = 1."))


def test_a_rework_that_stands_on_canaries_alone_accepts_the_whole_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    done = _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=_boundary_and_removed(run)))
    assert done["status"] == "EFFECTIVE_SELECTED" and done["effective_source"]["revision"] == 1
    assert done["review_canaries"]["status"] == "ALL_CAUGHT" and done["review_canaries"]["caught"] == 2


def test_an_accepting_ledger_with_no_effective_canonical_does_not_close_the_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer goes back whole (a blocking finding names no case: ledger REJECTED, no effective canonical).  A ledger that said ACCEPTED instead
    would have to come with an effective canonical; without it ``finish`` refuses (FINISH_FACT_UNDETERMINED) and the attempt stays open."""
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    done = _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", ["CREQ-batch-a-001"], "Требование не покрыто.")]))
    assert done["status"] == "REWORK" and finalize_attempt._durable_branch_facts(run["run_root"], run["attempt_id"])[2]["reason_code"] == "REWORK"
    real = pilot_state.terminal_reviewer_evidence(run["run_root"], run["attempt_id"])
    monkeypatch.setattr(pilot_state, "terminal_reviewer_evidence", lambda *_args: {**real, "authoritative_verdict": "ACCEPTED", "isolation": "verified"})
    with pytest.raises(finalize_attempt.FinalizationError, match="FINISH_FACT_UNDETERMINED"):
        finalize_attempt._durable_branch_facts(run["run_root"], run["attempt_id"])


def _acceptance(run: dict):
    from tools.run_report import build_report

    return build_report(run["run_root"].parent.parent, run["run_root"].name)[0]["acceptance"]


def test_the_report_says_nothing_of_acceptance_before_the_review_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The answer is recorded before ``select``: until the ledger has its verdict (and, for an accepting one, the effective canonical) there is no «Итог»."""
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    publish_model_stage_artifact(run["run_root"], run["attempt_id"], "tc-reviewer:canonical", _answer(run, "ПРИНЯТО"))
    assert _acceptance(run) is None
    assert _select(run, _answer(run, "ПРИНЯТО"))["status"] == "EFFECTIVE_SELECTED"
    assert _acceptance(run)["outcome"] == "ACCEPTED_WHOLE"


def test_a_real_blocking_finding_beside_canaries_takes_its_case_out_and_the_attempt_is_partially_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)  # one case of the two of the fixture
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 0)  # this test is the publication of the subtraction at the select; the rework is in test_rework_*
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, two = _ids(run)
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [one["case_id"]], "Нет проверки состояния.")]
    done = _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))
    assert done["status"] == "EFFECTIVE_SELECTED" and done["effective_source"]["revision"] == 2
    effective = pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"])["document"]
    assert [case["case_id"] for case in effective["test_cases"]] == [two["case_id"]]
    assert pilot_state.terminal_reviewer_evidence(run["run_root"], run["attempt_id"])["authoritative_verdict"] == "ACCEPTED"
    facts = finalize_attempt._durable_branch_facts(run["run_root"], run["attempt_id"])[2]
    assert (facts["completion"], facts["reason_code"], facts["prior_stage_cause"]) == ("PARTIAL", "PARTIAL_ACCEPTED", "PARTIAL_ACCEPTED")
    result = pilot_state.terminal_result({key: value for key, value in facts.items() if key != "policy_profile"}, facts["policy_profile"])
    assert pilot_state._validate_result_record(result) and result["accepted"] is False
    published = sorted(path.name for path in (run["run_root"] / "published").iterdir())  # r1 is not published; r2 is a bundle, the rejected case a file of its own without a CSV
    document_id = effective["document_id"]
    assert published == sorted(f"{document_id}.r2.{suffix}" for suffix in ("html", "json", "md", "zephyr-scale.csv", "REJECTED-CASES.html", "REJECTED-CASES.md"))
    page = (run["run_root"] / "published" / f"{document_id}.r2.md").read_text(encoding="utf-8")
    assert "**Принят частично: 1 из 2 кейсов.** Кейс ТК-1 «" in page and f"{document_id}.r2.REJECTED-CASES.md" in page and "\n## ТК-2. " in page
    assert "НЕ ПРИНЯТЫ РЕВЬЮЕРОМ" in (run["run_root"] / "published" / f"{document_id}.r2.REJECTED-CASES.md").read_text(encoding="utf-8")
    again = _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))  # the same answer again: the same files, nothing replaced
    assert again["status"] == "EFFECTIVE_SELECTED"
    from tools.canonical_document import document_sha256

    project, root = pilot_state.run_root_of(run["run_root"])  # mode B: the automation request reads r2 — the accepted cases only
    assert pilot_state._automation_inputs(project, root, pilot_state.derive_state(root), run["attempt_id"], "tc-to-autotest:r1")[0][0] == document_sha256(effective)
    from tools.run_report import build_report

    acceptance = build_report(run["project"], run["run_root"].name)[0]["acceptance"]  # the verdict as given, the outcome the code drew, the numbers — codes, no texts
    assert (acceptance["verdict_given"], acceptance["outcome"], acceptance["generated"], acceptance["accepted"], acceptance["rejected_by_findings"]) == ("ТРЕБУЕТ ДОРАБОТКИ", "PARTIAL", 2, 1, 1)
    assert acceptance["rejected_cases"][0]["codes"] == ["REVIEW_NOTE"] and "Нет проверки состояния." not in json.dumps(acceptance, ensure_ascii=False)


def test_a_free_text_code_of_a_blocking_finding_does_not_reach_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The code of a finding is the model's text (the schema asks only for a non-empty string): the report that leaves the perimeter holds it as a code or as OTHER."""
    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 0)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    phrase = "Требование REQ-7 нарушено: ожидание не проверяет баланс счёта клиента"
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [one["case_id"]], "Нет проверки состояния.", code=phrase)]
    assert _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))["status"] == "EFFECTIVE_SELECTED"
    report = build_report(run["project"], run["run_root"].name)[0]
    assert report["acceptance"]["rejected_cases"][0]["codes"] == ["OTHER"]
    assert "баланс счёта" not in json.dumps(report, ensure_ascii=False) and "баланс счёта" not in render_markdown(report)


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
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    done = _select(run, _answer(run, findings=[_finding("INFO", [one["steps"][0]["expectations"][0]["expectation_id"]])]))
    assert done["review_canaries"]["status"] == "WEAK" and "пропущено 1 из 2" in done["review_canaries"]["note"]
    assert CLASS_NAMES["assertion_removed"] in done["review_canaries"]["note"]
    report, _w = build_report(run["project"], run["run_root"].name)
    assert report["review_canaries"]["status"] == "WEAK" and "REVIEW_WEAK" in report["warnings"] and "REVIEW_UNRELIABLE" not in report["warnings"]


# --------------------------------------------------------------------------------------
# the journal decides «seeded or not»; a refusal stands on a strict hit only
# --------------------------------------------------------------------------------------

def test_the_receipt_events_are_marked_and_a_run_without_them_is_not_seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _generated_run(tmp_path)
    assert review_canaries.verify(*_flags(run)) is None  # no event of the receipt: not seeded, nothing to read
    _open(run, monkeypatch)
    marked = [row for row in _events(run["run_root"]) if row.get("batch_id") == review_canaries.BATCH_ID]
    assert [row["event_type"] for row in marked] == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]


def test_a_deleted_receipt_file_is_a_mismatch_in_report_and_status_even_before_the_answer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    other = {"severity": "WARNING", "code": "X", "message": "В STEP-S10 называется 6.", "evidence": ["STEP-S10"], "related_ids": ["STEP-S10", "EXP-S10"]}
    assert not finding_hits(_canary(), other) and not finding_hits(_canary(), other, strict=True)
    own = {**other, "message": "В STEP-S1 называется 6.", "related_ids": ["STEP-S1"]}
    assert finding_hits(_canary(), own) and finding_hits(_canary(), own, strict=True)


def test_a_strict_hit_needs_the_id_in_related_ids_and_the_value_in_the_text() -> None:
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
    assert done["status"] == "REWORK_PENDING"  # one case of the two goes back: 50 %, within the half the rework takes


def test_a_receipt_map_and_view_planted_before_review_open_do_not_switch_the_canaries_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The files agree with each other (count 0, an empty map, a view without control errors) and the journal has no event of them:
    prepare of the review does not take them for a cut-short seeding — they are not what the seeding of the journal's candidate gives."""
    run = _generated_run(tmp_path)
    candidate = journal_assembly(*_flags(run))[0]
    publish_command(candidate, run["run_root"] / "published", _PROFILE, run_root=run["run_root"], attempt_id=run["attempt_id"])
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


# --------------------------------------------------------------------------------------
# V0a: a refusal stands on the place of a canary, not on a quotation of it
# --------------------------------------------------------------------------------------

REMOVED = {"class": "assertion_removed", "field": "assertions", "original": "owner_id = 1", "mutated": "", "markers": ["owner_id = 1"]}


def _blocking(ids: list[str], message: str = "Проверки нет.") -> dict:
    return {"severity": "BLOCKING", "code": "ORACLE_MISSING", "message": message, "evidence": [], "related_ids": ids}


def _is_canary(canary: dict, finding: dict, cases: set[str] | None = None) -> bool:
    from tools.review_canaries import canary_finding

    return canary_finding([canary], finding, set() if cases is None else cases)


def test_a_blocking_finding_on_the_place_of_a_canary_needs_no_quotation() -> None:
    assert _is_canary(_canary(**REMOVED), _blocking(["TC-S", "STEP-S1", "EXP-S1", "CREQ-1"]), {"TC-S"})


def test_a_finding_that_names_only_the_case_or_a_requirement_is_not_a_canary() -> None:
    assert not _is_canary(_canary(**REMOVED), _blocking(["TC-S"]), {"TC-S"})
    assert not _is_canary(_canary(**REMOVED), _blocking(["REQ-OTHER"]))


def test_a_finding_that_names_another_expectation_too_is_not_a_canary() -> None:
    assert not _is_canary(_canary(**REMOVED), _blocking(["EXP-S1", "EXP-S2"]))


def test_a_finding_that_names_another_case_too_is_not_a_canary() -> None:
    assert not _is_canary(_canary(**REMOVED), _blocking(["EXP-S1"]), {"TC-S", "TC-OTHER"})


def test_the_step_is_a_place_only_for_a_canary_without_an_expectation() -> None:
    finding = _blocking(["STEP-S1"])
    assert _is_canary(_canary(**REMOVED, expectation_id=None), finding)
    assert not _is_canary(_canary(**REMOVED), finding)


def test_a_boundary_finding_on_the_place_without_the_value_is_not_a_canary() -> None:
    """The quotation stays the condition for the classes whose view keeps the changed value."""
    assert not _is_canary(_canary(), _blocking(["EXP-S1"]))
    assert _is_canary(_canary(), _blocking(["EXP-S1"], "Называется 6."))


def test_a_foreign_expectation_in_related_ids_is_no_canary_in_any_class() -> None:
    assert not _is_canary(_canary(), _blocking(["EXP-S1", "EXP-S2"], "Называется 6."))


def test_a_blocking_finding_addressed_to_the_step_that_names_the_expectation_is_the_canary_s() -> None:
    step = _blocking(["TC-S", "STEP-S1"], "Шаг без проверок.")
    assert _is_canary(_canary(**REMOVED), {**step, "evidence": ["STEP-S1", "EXP-S1"]}, {"TC-S"})
    assert _is_canary(_canary(**REMOVED), {**step, "message": "Шаг без проверок: ожидание EXP-S1."}, {"TC-S"})


def test_a_finding_addressed_to_the_step_with_no_expectation_named_is_not_a_canary() -> None:
    assert not _is_canary(_canary(**REMOVED), _blocking(["TC-S", "STEP-S1"], "Шаг без проверок."), {"TC-S"})
    assert not _is_canary(_canary(**REMOVED), {**_blocking(["TC-S", "STEP-S1"]), "evidence": ["EXP-S10"]}, {"TC-S"})  # S1 is not S10


def test_a_finding_addressed_to_the_step_that_names_another_step_or_expectation_is_not_a_canary() -> None:
    step = _blocking(["TC-S", "STEP-S1"])
    assert not _is_canary(_canary(**REMOVED), {**step, "evidence": ["EXP-S1", "EXP-S2"]}, {"TC-S"})
    assert not _is_canary(_canary(**REMOVED), {**step, "message": "Шаг без проверок; то же в STEP-S2.", "evidence": ["EXP-S1"]}, {"TC-S"})
    assert not _is_canary(_canary(**REMOVED), {**step, "evidence": ["EXP-S1", "ASSERT-S2-1"]}, {"TC-S"})


def test_a_finding_that_names_the_expectation_but_not_the_step_in_related_ids_is_not_a_canary_by_text_alone() -> None:
    assert not _is_canary(_canary(**REMOVED), {**_blocking(["TC-S"]), "evidence": ["EXP-S1"]}, {"TC-S"})


def test_a_boundary_canary_flips_the_operator_of_a_comparison() -> None:
    check = {"assertion_id": "ASSERT-1", "operator": "greater_than", "actual": {"kind": "http_body", "pointer": "/count"},
             "expected": {"kind": "literal", "value": "x"}}
    case = {"case_id": "TC-1", "steps": [{"step_id": "STEP-1", "expectations": [{"expectation_id": "EXP-1", "text": "больше", "assertions": [check]}]}]}
    options = _options("boundary", case, "sha256:" + "0" * 64)
    assert [row["field"] for row, _apply in options] == ["assertions"]
    options[0][1]()
    assert check["operator"] == "greater_or_equal"


def test_a_whole_successor_document_is_refused_when_errors_are_seeded_and_the_refusal_names_none() -> None:
    answer = {"artifacts": {"validation_report": {"verdict": "AUTO_FIX_APPLIED"}, "successor_document": {"revision": 2}}}
    with pytest.raises(ValueError) as refused:
        review_canaries.answer_over_canaries([{"canary_id": "CAN-1", "class": "boundary"}], answer, {})
    assert str(refused.value) == ("REVIEW_SUCCESSOR_DOCUMENT: в этом ревью целый документ (successor_document) не принимается — только правки; "
                                  "fix: дай исправления правками artifacts.successor_edits (SKILL tc-reviewer, AUTO_FIX), целый документ убери; "
                                  "отдай ответ единственным повтором стадии (--transport-attempts 2), ничего не записано")  # approved 27.11
    assert review_canaries.answer_over_canaries([], answer, {}) is None  # no errors seeded: the document goes on to its own checks
