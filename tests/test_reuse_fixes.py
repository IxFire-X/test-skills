"""V5b-1f: the findings of the second reading of V5b-1 (the reuse of the cases) — each one a test that reproduces the case.  The helpers are those of ``test_reuse_cases``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_reuse_cases import REVIEW, _all, _follow, _journal, _new, _reuse, _scanned, _source, accepted  # noqa: F401  (the fixture)
from tests.test_review_canaries import _acceptance, _answer, _fake_seed, _select
from tests.test_review_requested import _generated_run
from tests.test_reuse_rework import accepted as reworked  # noqa: F401  (the fixture: the first review sent a case back, the rework and its second round accepted it)
from tools import api, pilot_state, stage_steps
from tools.json_cli import error_envelope

MARKER = "context-marker:baseline"


def _damaged(run_root: Path, folder: str, needle: bytes) -> tuple[Path, bytes]:
    """The first file of ``folder`` of a run holding ``needle``, damaged; the caller restores it."""
    path = next(item for item in sorted((run_root / folder).rglob("*.json")) if needle in item.read_bytes())
    kept = path.read_bytes()
    path.write_bytes(kept.replace(needle, needle[:-1] + b"x", 1))
    return path, kept


def _marked(src: dict) -> tuple[Path, str]:
    """A new run of the project whose marker is the source's, taken the ordinary way — up to the plan."""
    run_root, attempt_id = _scanned(src)
    pilot_state.publish_model_request(run_root, attempt_id, MARKER, model_id="m", invocation_id="m-1",
                                      input_digests=pilot_state.read_model_request(src["run_root"], src["attempt_id"], MARKER, next(
                                          row["artifact_digest"] for row in pilot_state.derive_state(src["run_root"])["events"]
                                          if row["event_type"] == "MODEL_REQUESTED" and row["stage_instance_id"] == MARKER))["input_digests"])
    pilot_state.publish_model_stage_artifact(run_root, attempt_id, MARKER, json.loads(Path(src["marker"]).read_bytes()))
    return run_root, attempt_id


# ---- 1: the plan is not written to a closed attempt -------------------------------------------------------------------------------------

def test_the_plan_of_a_source_is_not_taken_into_a_closed_attempt(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """No command closes an attempt before the review (``finish`` refuses it), so the closed state is the seam ``_refuse_terminal`` — what the ordinary ``submit --stage plan`` meets."""
    run_root, _attempt = _marked(accepted)
    monkeypatch.setattr(stage_steps, "_refuse_terminal", lambda state, attempt_id: (_ for _ in ()).throw(ValueError("ATTEMPT_TERMINAL: closed")))
    taken: list[str] = []
    monkeypatch.setattr(pilot_state, "reuse_plan", lambda *args: taken.append("plan") or {})  # what the call would write
    with pytest.raises(ValueError, match="ATTEMPT_TERMINAL"):
        stage_steps.prepare(run_root, "plan", reuse_from=accepted["run_root"].name)
    assert taken == []


# ---- 2: a request recorded as a reuse that the source no longer answers: the texts say so --------------------------------------------------

def _stuck(src: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    """A reuse cut after its request was written, then the answer of the source for that stage damaged."""
    run = _follow(src)
    stage = run["stages"][0]
    real = pilot_state.publish_model_stage_artifact
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("cut")))
    with pytest.raises(OSError):
        _reuse(run, stage)
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", real)
    return {**run, "stage": stage}


def test_a_stage_whose_reuse_request_is_recorded_says_so_in_every_refusal(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    run = _stuck(accepted, monkeypatch)
    path, kept = _damaged(accepted["run_root"], "model-stage-artifacts", run["stage"].partition(":")[2].encode())  # the answer of the source to this very stage
    try:
        said, fixes = [], []
        for call in (lambda: _reuse(run, run["stage"]), lambda: stage_steps.prepare(run["run_root"], run["stage"]),
                     lambda: stage_steps.submit(run["run_root"], run["stage"], api._load_answer)):
            try:
                call()
            except Exception as error:  # noqa: BLE001 - the text of whatever is refused
                said.append(str(error))
                fixes.append(str(error_envelope(error)["fix"]))
            else:
                said.append("не отказано")
    finally:
        path.write_bytes(kept)
    assert all("уже записан как повтор из прогона " + accepted["run_root"].name in text for text in said), "\n--\n".join(said)
    assert "model-stage-artifacts" in said[0] and "причина: " in said[1], said  # the cause of the refusal stays in the text: the file of the source, the failure of its read
    assert not [text for text in fixes if "prepare без --reuse-from" in text], fixes  # the fix does not send to the way that is closed


# ---- 3: the fingerprint of the package is the stage's own -----------------------------------------------------------------------------

def _parts_source(project: Path, monkeypatch: pytest.MonkeyPatch, part_pack: str | None) -> tuple[str, str]:
    """A source marked in parts, its merge under the package of this tree and its part under ``part_pack``; stubbed at the seams ``_run_source`` and ``_input_differences``."""
    source_id = "f" * 32
    (project / ".pilot-runs" / source_id).mkdir(parents=True)
    (project / ".pilot-runs" / source_id / "events.jsonl").write_text("", encoding="utf-8")
    pack, inputs = pilot_state.skill_pack_digest(), ["sha256:" + "1" * 64, "sha256:" + "2" * 64, "sha256:" + "3" * 64]
    part = {"input_digests": inputs, "model_id": "m", "role_policy": "x", "invocation_id": "m-1", **({"skill_pack_digest": part_pack} if part_pack else {})}
    stages = {"context-marker:batch-001": (part, "sha256:" + "a" * 64), MARKER: ({"input_digests": inputs, "model_id": "controller:merge", "skill_pack_digest": pack}, "sha256:" + "c" * 64)}
    fake = {"run_id": source_id, "root": project / ".pilot-runs" / source_id, "state": {}, "attempt": {"attempt_id": "e" * 32, "module": "."}, "stages": stages, "pack": pack,
            "verdict": "ПРИНЯТО", "drift": False}
    monkeypatch.setattr(pilot_state, "_run_source", lambda project, run_id, whole=True: fake)
    monkeypatch.setattr(pilot_state, "_input_differences", lambda source, root, attempt: [])
    monkeypatch.setattr(pilot_state, "_marker_differences", lambda *args: [])  # the source is no run on disk: its project is not compared here (as in ``test_marker_reuse``)
    return source_id, "context-marker:batch-001"


def test_a_part_of_the_marker_made_under_another_package_is_not_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _source(tmp_path)
    run_root, attempt_id = _new(src["project"])
    source_id, stage = _parts_source(src["project"], monkeypatch, "sha256:" + "e" * 64)  # the merge is under the package of this tree, the part is not
    with pytest.raises(pilot_state.ModelReuseRefused, match="пакет иной"):
        pilot_state.check_reuse(run_root, attempt_id, stage, source_id)


def test_the_package_of_the_source_stands_for_a_stage_recorded_without_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _source(tmp_path)
    run_root, attempt_id = _new(src["project"])
    source_id, stage = _parts_source(src["project"], monkeypatch, None)  # a request before V5b-1 carries no fingerprint
    monkeypatch.setattr(pilot_state, "_read_model_stage_artifact_with_state", lambda *args: {})  # the stub has no files
    assert pilot_state.check_reuse(run_root, attempt_id, stage, source_id)["fresh_context"] is None  # no refusal


# ---- 4: a source of another package is named so, not damaged ----------------------------------------------------------------------------

def test_a_source_of_another_package_is_refused_as_such_even_when_its_answers_do_not_read(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    run = _follow(accepted)
    monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: "sha256:" + "e" * 64)
    path, kept = _damaged(accepted["run_root"], "model-stage-artifacts", b"test_cases")  # what a reader of today does not take of a past version
    try:
        with pytest.raises(pilot_state.ModelReuseRefused, match="пакет иной"):
            _reuse(run, run["stages"][0])
    finally:
        path.write_bytes(kept)


# ---- 5: the marker is taken of a source whose later answers are damaged ---------------------------------------------------------------------

def test_the_marker_of_a_source_with_a_damaged_batch_is_taken_and_the_batch_is_not(accepted: dict) -> None:  # noqa: F811
    run_root, attempt_id = _scanned(accepted)
    stage = "tc-generator:" + json.loads(Path(accepted["plan"]).read_bytes())["batches"][0]["batch_id"]
    path, kept = _damaged(accepted["run_root"], "model-stage-artifacts", b"test_cases")
    try:
        offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
        assert offered["run_id"] == accepted["run_root"].name  # the scan offers the marker
        assert stage_steps.prepare(run_root, MARKER, reuse_from=accepted["run_root"].name)["answered"] is True
        for kind in ("header", "plan"):
            pilot_state.publish_generator_input(run_root, attempt_id, kind, json.loads(Path(accepted[kind]).read_bytes()))
        run = {**accepted, "run_root": run_root, "attempt_id": attempt_id, "source": accepted["run_root"].name}
        with pytest.raises(pilot_state.ReuseSourceInvalid):
            _reuse(run, stage)
    finally:
        path.write_bytes(kept)


# ---- 6: the line about the date names a run only for a stage whose input is the plan -----------------------------------------------------

def _note(monkeypatch: pytest.MonkeyPatch, *stages: str) -> str | None:
    from tools import run_report

    monkeypatch.setattr(pilot_state, "read_attempt_receipt", lambda *args, **kwargs: {"record": {"header": {"metadata": {"date": "2026-01-01"}}}})
    calls = [{"stage": stage, "by": "reuse", "reused_from": f"{index}" * 32} for index, stage in enumerate(stages, start=1)]
    return run_report._header_date_note(Path("."), "a", [{"observed_at": "2026-02-02T00:00:00Z"}], calls)


def test_a_marker_reused_from_one_run_does_not_name_the_source_of_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "1" * 8 not in _note(monkeypatch, MARKER)


def test_the_batch_of_a_run_names_the_source_of_the_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "2" * 8 in _note(monkeypatch, MARKER, "tc-generator:BATCH-001") and "1" * 8 not in _note(monkeypatch, MARKER, "tc-generator:BATCH-001")


# ---- a stage outside the list is refused by the command itself ------------------------------------------------------------------------------

def test_a_stage_outside_the_list_is_refused_by_prepare_and_nothing_is_written(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    """The stages of a cases-only run are all on the list: the list is cut by one, so the batches are the stages outside it."""
    run = _follow(accepted)
    monkeypatch.setattr(pilot_state, "REUSE_STAGES", {pattern: family for pattern, family in pilot_state.REUSE_STAGES.items() if family != "batch"})
    before = _journal(run["run_root"])
    with pytest.raises(pilot_state.ModelReuseRefused, match="не переиспользуется"):
        _reuse(run, run["stages"][0])
    assert _journal(run["run_root"]) == before


# ---- reuse4: a source whose reviewer caught a control error says ТРЕБУЕТ ДОРАБОТКИ and its outcome is ACCEPTED_WHOLE -------------------------------

@pytest.fixture(scope="module")
def caught(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The cycle of the agent with a real seeding: the one BLOCKING finding stands on the place of the control error (case, step, expectation), no real case is rejected."""
    from tools import review_canaries

    run = _generated_run(tmp_path_factory.mktemp("caught"))
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(review_canaries, "seed", _fake_seed)  # the two cases of the fixture seed nothing of their own
        stage_steps.prepare(run["run_root"], REVIEW, invocation_id="reviewer-subagent-1", fresh_context=True)
    place = review_canaries.verify(run["run_root"], run["attempt_id"])[0]
    finding = {"severity": "BLOCKING", "code": "ORACLE_MISSING", "message": "Граница названа неверно: 6.", "evidence": ["REQ"], "related_ids": [place["case_id"], place["step_id"], place["expectation_id"]]}
    assert _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=[finding]))["outcome"] == "ACCEPTED_WHOLE"
    return run


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries

    monkeypatch.setattr(review_canaries, "seed", _fake_seed)  # a pure function of the candidate: the new run seeds what the source did


def test_the_scan_offers_a_source_whose_reviewer_caught_a_control_error(caught: dict) -> None:
    run_root, attempt_id = _scanned(caught)
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] == caught["run_root"].name and "cases" not in offered, offered  # no refusal named for the cases
    assert [row["stage"] for row in offered["stages"]][-1] == REVIEW and "plan" in [row["stage"] for row in offered["stages"]], offered


def test_every_stage_of_such_a_source_is_taken_by_prepare(caught: dict, seeded: None) -> None:
    run = _follow(caught)
    assert _all(run)["status"] == "EFFECTIVE_SELECTED"


def test_the_new_run_ends_as_the_source_did_with_the_same_control_errors(caught: dict, seeded: None) -> None:
    from tools import review_canaries

    run = _follow(caught)
    _all(run)
    assert _acceptance(run)["outcome"] == _acceptance(caught)["outcome"] == "ACCEPTED_WHOLE"
    assert review_canaries.verify(run["run_root"], run["attempt_id"]) == review_canaries.verify(caught["run_root"], caught["attempt_id"])  # the places are the source's
    assert review_canaries.summary(run["run_root"], run["attempt_id"]) == review_canaries.summary(caught["run_root"], caught["attempt_id"])  # caught and missed


def test_a_source_that_rejected_a_real_case_gives_its_marker_and_the_scan_names_the_outcome_for_the_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import orchestrate_test_case_revision as orchestrate, review_canaries
    from tests.test_review_canaries import _finding, _ids

    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)  # one case of the two of the fixture goes out: PARTIAL
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 0)  # and goes back nowhere
    src = _generated_run(tmp_path)
    stage_steps.prepare(src["run_root"], REVIEW, invocation_id="reviewer-subagent-1", fresh_context=True)
    one, _two = _ids(src)
    assert _select(src, _answer(src, "ТРЕБУЕТ ДОРАБОТКИ", findings=[_finding("BLOCKING", [one["case_id"]], "Нет проверки состояния.")]))["outcome"] == "PARTIAL"
    run_root, attempt_id = _scanned(src)
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] == src["run_root"].name and [row["stage"] for row in offered["stages"]] == ["context-marker:baseline"], offered
    assert offered["cases"]["reused"] is False and "исход PARTIAL" in offered["cases"]["reason"], offered


def test_a_source_of_another_package_is_named_so_even_with_the_review_that_needs_its_outcome(caught: dict, monkeypatch: pytest.MonkeyPatch, seeded: None) -> None:
    run = _follow(caught)
    monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: "sha256:" + "e" * 64)
    path, kept = _damaged(caught["run_root"], "model-stage-artifacts", b"test_cases")  # what a reader of today does not take of a past version
    try:
        with pytest.raises(pilot_state.ModelReuseRefused, match="пакет иной"):  # not REUSE_SOURCE_INVALID: the outcome is worked out after the package
            _reuse(run, run["stages"][0])
    finally:
        path.write_bytes(kept)


def test_the_scan_does_not_work_out_the_outcome_of_a_run_whose_inputs_differ(caught: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import orchestrate_test_case_revision as orchestrate

    asked: list[str] = []
    real = orchestrate.acceptance_summary
    monkeypatch.setattr(orchestrate, "acceptance_summary", lambda *args: asked.append("outcome") or real(*args))
    added = caught["project"] / "src" / "added_for_reuse4.py"
    added.write_text("x = 1\n", encoding="utf-8")
    try:
        run_root, attempt_id = _new(caught["project"])
        offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    finally:
        added.unlink()
    assert offered["run_id"] is None and asked == []  # the project holds other runs too (their reasons come first): only the outcome is the point here


def test_the_scan_names_the_files_of_the_project_that_changed_since_the_source(tmp_path: Path) -> None:
    src = _source(tmp_path)
    (src["project"] / "src" / "added_one.py").write_text("x = 1\n", encoding="utf-8")
    run_root, attempt_id = _new(src["project"])
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] is None and offered["files"] == {"added": {"count": 1, "paths": ["src/added_one.py"]}}, offered
    assert offered["reason"] == "входы иные: в проекте изменились файлы — появились 1: src/added_one.py"


def test_a_build_directory_that_appeared_since_the_source_is_no_change_of_the_project(tmp_path: Path) -> None:
    src = _source(tmp_path)
    (src["project"] / "build").mkdir()
    (src["project"] / "build" / "out.txt").write_text("x\n", encoding="utf-8")
    run_root, attempt_id = _new(src["project"])
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert "files" not in offered and "inventory" not in [row["input"] for row in offered["differences"]], offered  # the new run has no context receipt yet: that is its only difference


def test_a_difference_of_the_inventories_that_is_in_no_file_and_no_exclusion_leaves_no_dash_and_no_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    inventory = {"files": [{"project_path": "a.py", "content_digest": "d"}], "exclusions": [{"project_path": ".tools", "reason_code": "EXCLUDED_DIRECTORY"}]}
    monkeypatch.setattr(pilot_state, "_frozen_inventory", lambda *_args: inventory)
    monkeypatch.setattr(pilot_state, "frozen_baseline", lambda *_args: {"inventory_digest": "x"})
    assert pilot_state._changed_files(Path("."), Path("r"), {}, {"root": Path("s"), "attempt": {}}) == ("", None)


def test_an_excluded_path_of_another_reason_is_named_with_its_code(monkeypatch: pytest.MonkeyPatch) -> None:
    mine = {"files": [], "exclusions": [{"project_path": "gen", "reason_code": "BINARY_OR_GENERATED"}]}
    inventories = iter([mine, {"files": [], "exclusions": []}])
    monkeypatch.setattr(pilot_state, "_frozen_inventory", lambda *_args: next(inventories))
    monkeypatch.setattr(pilot_state, "frozen_baseline", lambda *_args: {"inventory_digest": "x"})
    said, found = pilot_state._changed_files(Path("."), Path("r"), {}, {"root": Path("s"), "attempt": {}})
    assert said == ": в проекте появились исключённые пути: gen (BINARY_OR_GENERATED)" and found["excluded_added"]["paths"] == ["gen (BINARY_OR_GENERATED)"]


# ---- reuse4f: the order of the refusals, the files named once, the finding addressed to the step -----------------------------------------------

def test_the_marker_of_a_source_with_a_finished_rework_and_a_damaged_batch_is_taken(reworked: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    """The source is good by its rework, the outcome is not what decides.  The limits and the seeding are those the source was built under: without them its outcome is ``None`` before the
    candidate is read (the limit of the rejection differs from the one of the select), and the test would stand on that."""
    from tools import orchestrate_test_case_revision as orchestrate, review_canaries
    from tools.review_canaries import recorded_answer

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    monkeypatch.setattr(orchestrate, "rework_rounds", lambda *_args: 1)
    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    source, attempt = reworked["run_root"], reworked["attempt_id"]
    assert orchestrate.acceptance_summary(source, attempt, recorded_answer(source, attempt))["outcome"] == "ACCEPTED_WHOLE"  # the outcome is there to be read
    run_root, attempt_id = _scanned(reworked)
    path, kept = _damaged(source, "model-stage-artifacts", json.loads(Path(reworked["plan"]).read_bytes())["batches"][0]["batch_id"].encode())  # the answer of the first batch
    try:
        assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] == source.name  # the scan does not skip the run
        assert stage_steps.prepare(run_root, MARKER, reuse_from=source.name)["answered"] is True
    finally:
        path.write_bytes(kept)


def test_the_files_of_the_project_are_compared_once_for_the_first_refusal_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _source(tmp_path)
    _all(_follow(src))  # a second earlier run, taken whole from the first: its review accepted, its inputs are what the file changes
    (src["project"] / "src" / "added_two.py").write_text("x = 1" + chr(10), encoding="utf-8")
    run_root, attempt_id = _new(src["project"])
    asked: list[str] = []
    real = pilot_state._changed_files
    monkeypatch.setattr(pilot_state, "_changed_files", lambda *args: asked.append("files") or real(*args))
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["files"]["added"]["paths"] == ["src/added_two.py"] and asked == ["files"], asked


def test_a_finding_addressed_to_the_step_of_a_removed_check_is_the_canary_s_and_the_source_is_taken(tmp_path: Path) -> None:
    """The form V8-W7 allows: the case and the step of the canary in ``related_ids``, the id of its expectation in ``evidence`` (the second canary of ``_fake_seed`` removes a check)."""
    from tools import review_canaries

    src = _generated_run(tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(review_canaries, "seed", _fake_seed)
        stage_steps.prepare(src["run_root"], REVIEW, invocation_id="reviewer-subagent-1", fresh_context=True)
        place = review_canaries.verify(src["run_root"], src["attempt_id"])[1]
        assert place["class"] == "assertion_removed"
        finding = {"severity": "BLOCKING", "code": "ORACLE_MISSING", "message": "Нет проверки.", "evidence": [place["expectation_id"]], "related_ids": [place["case_id"], place["step_id"]]}
        assert _select(src, _answer(src, "ТРЕБУЕТ ДОРАБОТКИ", findings=[finding]))["outcome"] == "ACCEPTED_WHOLE"
        run_root, attempt_id = _scanned(src)
        offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
        assert offered["run_id"] == src["run_root"].name and "cases" not in offered, offered
        run = _follow(src)
        assert _all(run)["status"] == "EFFECTIVE_SELECTED"
        assert _acceptance(run)["outcome"] == _acceptance(src)["outcome"] == "ACCEPTED_WHOLE"
        assert review_canaries.summary(run["run_root"], run["attempt_id"]) == review_canaries.summary(src["run_root"], src["attempt_id"])
