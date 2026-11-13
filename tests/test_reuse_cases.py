"""V5b-1: the cases an earlier run accepted whole are reused through ``prepare --stage <stage> --reuse-from <run_id>`` — the batches of the generator
and the review, with the verdict of the source.  The new run writes its own chain and takes the answers of the source as they are; every check of the
source runs before anything is written to the new run.  (The plan and the end-to-end paths: ``test_reuse_cases_flow.py``.)"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_canaries import _answer, _fake_seed, _select
from tests.test_review_codeword import _with_words
from tests.test_review_requested import _generated_run
from tools import pilot_state, stage_steps

REVIEW = "tc-reviewer:canonical"


@pytest.fixture(scope="module")
def accepted(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _source(tmp_path_factory.mktemp("accepted"))


@pytest.fixture(scope="module")
def self_accepted(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _source(tmp_path_factory.mktemp("self"), self_review=True, fresh=False)


def _source(tmp_path: Path, *, self_review: bool = False, fresh: bool = True, answer: dict | None = None, evidence: list[str] | None = None) -> dict:
    """A run whose cases the review accepted whole (the cycle of the agent: ``prepare``, the answer in the slot, ``submit``)."""
    run = _generated_run(tmp_path, self_review=self_review)
    flags = {"invocation_id": "reviewer-subagent-1", "fresh_context": True} if fresh else {"fresh_context": False}
    stage_steps.prepare(run["run_root"], REVIEW, **flags)
    value = answer if answer is not None else _answer(run)
    _select(run, {**value, **({"evidence": evidence} if evidence else {})})
    return run


def _new(project: Path, consent: bool = False) -> tuple[Path, str]:
    from tests.helpers import build_phase_two_baseline
    from tests.spine_helpers import run_phase_one_spine

    identity = {"project": str(project.resolve()), "module": ".", "policy_profile": "cases-only-v1"}
    baseline = build_phase_two_baseline(project, identity, skill_pack_root=project / ".pilot-runs")
    started = run_phase_one_spine(project, "cases-only-v1", {"request_id": "follow-1", "execution_requested": False, **({"accept_self_review": True} if consent else {})},
                                  identity, baseline, {})
    return Path(started["run"]["run_root"]), started["state"]["attempts"][0]["attempt_id"]


def _scanned(src: dict, consent: bool = False) -> tuple[Path, str]:
    """A new run of the same project as a scan leaves it: the baseline and the context of the source's own."""
    run_root, attempt_id = _new(src["project"], consent)
    pilot_state.publish_context_selection(run_root, attempt_id, pilot_state.context_receipts(src["run_root"], src["attempt_id"], pilot_state.derive_state(src["run_root"]))[0])
    return run_root, attempt_id


def _own_context(run_root: Path, attempt_id: str, project: Path) -> None:
    """The context a scan of the project as it is now publishes (the files of the project differ from the source's)."""
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    attempt = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    pilot_state.publish_context_selection(run_root, attempt_id, select_context_batches(inventory, project, [], byte_budget=64 * 1024)[0]["receipt"])


def _follow(src: dict, *, consent: bool = False, reuse_marker: bool = True) -> dict:
    """A new run of the same project with the same context, the marker (reused), the header and the plan — up to the batches of the generator."""
    run_root, attempt_id = _scanned(src, consent)
    state = pilot_state.derive_state(src["run_root"])
    asked = next(row for row in state["events"] if row["event_type"] == "MODEL_REQUESTED" and row["stage_instance_id"] == "context-marker:baseline")
    inputs = pilot_state.read_model_request(src["run_root"], src["attempt_id"], "context-marker:baseline", asked["artifact_digest"])["input_digests"]
    pilot_state.publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="m-1", input_digests=inputs, reuse_from=src["run_root"].name if reuse_marker else None)
    pilot_state.publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", json.loads(Path(src["marker"]).read_bytes()))
    pilot_state.publish_generator_input(run_root, attempt_id, "header", json.loads(Path(src["header"]).read_bytes()))
    pilot_state.publish_generator_input(run_root, attempt_id, "plan", json.loads(Path(src["plan"]).read_bytes()))
    plan = json.loads(Path(src["plan"]).read_bytes())
    return {**src, "run_root": run_root, "attempt_id": attempt_id, "source": src["run_root"].name, "tmp": run_root / "tmp",
            "stages": [f"tc-generator:{row['batch_id']}" for row in plan["batches"]]}


def _reuse(run: dict, stage: str, **kwargs) -> dict:
    return stage_steps.prepare(run["run_root"], stage, reuse_from=run["source"], **kwargs)


def _events(root: Path) -> list[tuple]:
    return [(row["event_type"], row.get("stage_instance_id")) for row in pilot_state.derive_state(root)["events"]]


def _journal(root: Path) -> bytes:
    return (root / "events.jsonl").read_bytes()


def _answers(root: Path) -> dict[str, str]:
    return {row["stage_instance_id"]: row["artifact_digest"] for row in pilot_state.derive_state(root)["events"] if row["event_type"] == "MODEL_RESPONSE_RECEIVED"}


def _all(run: dict) -> dict:
    for stage in run["stages"]:
        _reuse(run, stage)
    return _reuse(run, REVIEW)


# ---- the whole set, stage by stage ----------------------------------------------------------------------------------------------------

def test_each_stage_is_answered_from_the_source_and_calls_no_model(accepted: dict) -> None:
    src = accepted
    run = _follow(src)
    for stage in run["stages"]:
        assert _reuse(run, stage)["answered"] is True
    done = _reuse(run, REVIEW)
    assert done["model_call"] is False and done["reused_from"] == src["run_root"].name and done["status"] == "EFFECTIVE_SELECTED"
    assert _answers(run["run_root"]) == _answers(src["run_root"])  # byte for byte: the digest of every answer is the source's


def test_the_effective_set_of_the_new_run_is_the_one_of_the_source(accepted: dict) -> None:
    src = accepted
    run = _follow(src)
    _all(run)
    effective = [dict(pilot_state.read_effective_canonical(row["run_root"], row["attempt_id"])) for row in (src, run)]
    assert effective[0]["document_digest"] == effective[1]["document_digest"]


def test_the_request_of_a_reused_batch_records_the_source_the_package_and_the_label_of_the_source(accepted: dict) -> None:
    src = accepted
    run = _follow(src)
    _reuse(run, run["stages"][0])
    asked = next(row for row in pilot_state.derive_state(run["run_root"])["events"] if row.get("stage_instance_id") == run["stages"][0] and row["event_type"] == "MODEL_REQUESTED")
    request = pilot_state.read_model_request(run["run_root"], run["attempt_id"], run["stages"][0], asked["artifact_digest"])
    assert request["reuse"]["run_id"] == src["run_root"].name and request["skill_pack_digest"] == pilot_state.skill_pack_digest()
    assert request["invocation_id"] == "invoke-1"  # the label of the source's request: the comparison of the generator's and the reviewer's labels means something


def test_one_call_is_one_stage(accepted: dict) -> None:
    run = _follow(accepted)
    before = _events(run["run_root"])
    _reuse(run, run["stages"][0])
    stage = run["stages"][0]
    assert _events(run["run_root"])[len(before):] == [("MODEL_REQUESTED", stage), ("MODEL_RESPONSE_RECEIVED", stage), ("CANDIDATE_PUBLISHED", stage)]


def test_a_repeated_call_writes_nothing(accepted: dict) -> None:
    run = _follow(accepted)
    _all(run)
    before = _journal(run["run_root"])
    assert _reuse(run, run["stages"][0])["answered"] is True and _reuse(run, REVIEW)["answered"] is True
    assert _journal(run["run_root"]) == before


def test_a_call_cut_short_after_the_request_is_finished_by_the_repeat(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _follow(accepted)
    real = pilot_state.publish_model_stage_artifact

    def cut(*args, **kwargs):
        raise OSError("cut")

    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", cut)
    with pytest.raises(OSError):
        _reuse(run, run["stages"][0])
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", real)
    assert _reuse(run, run["stages"][0])["answered"] is True
    assert _events(run["run_root"]).count(("MODEL_REQUESTED", run["stages"][0])) == 1


def test_a_review_cut_short_between_the_answer_and_the_selection_is_finished_by_the_repeat(accepted: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import orchestrate_test_case_revision as revision

    run = _follow(accepted)
    for stage in run["stages"]:
        _reuse(run, stage)
    real = revision.select_review

    def cut(*args, **kwargs):
        raise OSError("cut")

    monkeypatch.setattr(revision, "select_review", cut)
    with pytest.raises(OSError):
        _reuse(run, REVIEW)
    monkeypatch.setattr(revision, "select_review", real)
    done = _reuse(run, REVIEW)
    assert done["status"] == "EFFECTIVE_SELECTED" and done["model_call"] is False and (run["run_root"] / "published").is_dir()
    assert pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"])["document_digest"]


def test_a_stage_the_model_answered_the_ordinary_way_is_not_called_a_reuse(accepted: dict) -> None:
    run = _follow(accepted)
    stage = run["stages"][0]
    pilot_state.publish_model_request(run["run_root"], run["attempt_id"], stage, model_id="m", invocation_id="invoke-1", input_digests=[])
    pilot_state.publish_model_stage_artifact(run["run_root"], run["attempt_id"], stage, json.loads(Path(accepted["fragments"][0]).read_bytes()))
    assert _reuse(run, stage) == {"stage": stage, "answered": True}


def test_the_review_is_not_taken_into_a_published_directory_that_leads_out_of_the_run(accepted: dict, tmp_path: Path) -> None:
    from tests.test_stage_steps import _junction

    run = _follow(accepted)
    for stage in run["stages"]:
        _reuse(run, stage)
    outside = tmp_path / "outside"
    outside.mkdir()
    _junction(run["run_root"] / "published", outside)
    before = _journal(run["run_root"])
    with pytest.raises(Exception) as refused:
        _reuse(run, REVIEW)
    assert refused.value.code == "OUTPUT_DIR_OUTSIDE_RUN" and list(outside.iterdir()) == [] and _journal(run["run_root"]) == before


def test_the_stage_after_the_last_one_is_not_started_by_the_call(accepted: dict) -> None:
    run = _follow(accepted)
    for stage in run["stages"]:
        _reuse(run, stage)
    _reuse(run, REVIEW)
    assert not [row for row in _events(run["run_root"]) if row[1] and str(row[1]).startswith(("tc-to-autotest", "autotest-reviewer"))]


# ---- what is not taken, and why -------------------------------------------------------------------------------------------------------

def _refused(run: dict, stage: str = REVIEW, **kwargs) -> pilot_state.ModelReuseRefused:
    before = _journal(run["run_root"])
    with pytest.raises(pilot_state.ModelReuseRefused) as refused:
        _reuse(run, stage, **kwargs)
    assert refused.value.code == "MODEL_REUSE_REFUSED" and _journal(run["run_root"]) == before  # nothing is written to the new run
    return refused.value


def test_a_source_whose_cases_were_sent_back_is_not_taken(tmp_path: Path) -> None:
    src = _generated_run(tmp_path)
    stage_steps.prepare(src["run_root"], REVIEW, invocation_id="reviewer-subagent-1", fresh_context=True)
    _select(src, _answer(src, "ТРЕБУЕТ ДОРАБОТКИ", findings=[{"severity": "BLOCKING", "code": "REVIEW_REJECTED", "message": "Нужна доработка.", "evidence": ["REQ"],
                                                              "related_ids": [json.loads(Path(src["fragments"][0]).read_bytes())["test_cases"][0]["case_id"]]}], effective=None))
    run = _follow(src, reuse_marker=False)  # the marker of a source whose review did not accept is no reuse either
    assert "ревью кейсов не принято" in str(_refused(run, run["stages"][0]))


def test_a_source_without_a_review_is_not_taken(tmp_path: Path) -> None:
    run = _follow(_generated_run(tmp_path), reuse_marker=False)
    assert "ревью кейсов не принято (ревью нет)" in str(_refused(run, run["stages"][0]))


def test_a_review_that_asked_for_evidence_is_not_taken(tmp_path: Path) -> None:
    run = _follow(_source(tmp_path, evidence=["src/extra.py"]))
    assert "evidence" in str(_refused(run, run["stages"][0]))


def test_the_rework_stage_is_no_stage_of_the_reuse(accepted: dict) -> None:
    run = _follow(accepted)
    with pytest.raises(pilot_state.ModelReuseRefused):  # the exact list of the stages, not the prefix tc-generator:
        pilot_state.publish_model_request(run["run_root"], run["attempt_id"], "tc-generator:rework-1", model_id="m", invocation_id="x", input_digests=["sha256:" + "1" * 64],
                                          reuse_from=run["source"])


@pytest.mark.parametrize("change", ["file", "package"])
def test_other_inputs_cancel_the_reuse_and_the_input_is_named(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    src = _source(tmp_path)
    if change == "file":
        (src["project"] / "src" / "added.py").write_text("x = 1\n", encoding="utf-8")
    run_root, attempt_id = _new(src["project"])
    if change == "package":
        monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: "sha256:" + "e" * 64)
    stage = f"tc-generator:{json.loads(Path(src['plan']).read_bytes())['batches'][0]['batch_id']}"
    with pytest.raises(pilot_state.ModelReuseRefused) as refused:
        pilot_state.check_reuse(run_root, attempt_id, stage, src["run_root"].name)
    assert {"file": "inventory", "package": "пакет иной"}[change] in str(refused.value)


def test_the_scan_does_not_offer_a_source_with_other_inputs_and_names_them(tmp_path: Path) -> None:
    src = _source(tmp_path)
    (src["project"] / "src" / "added.py").write_text("x = 1\n", encoding="utf-8")
    run_root, attempt_id = _new(src["project"])
    _own_context(run_root, attempt_id, src["project"])
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] is None and any("inventory" in str(row["input"]) for row in offered["differences"])


def test_a_batch_generated_otherwise_in_the_new_run_makes_the_review_not_taken(accepted: dict) -> None:
    src = accepted
    run = _follow(src)
    _reuse(run, run["stages"][0])
    second = json.loads(Path(src["fragments"][1]).read_bytes())
    second["test_cases"][0]["title"] += " (иначе)"
    from tests.test_batch_assembly import _refresh

    _refresh(second)
    pilot_state.publish_prior_capabilities(run["run_root"], run["attempt_id"], run["stages"][1].partition(":")[2])
    pilot_state.publish_model_request(run["run_root"], run["attempt_id"], run["stages"][1], model_id="m", invocation_id="invoke-2", input_digests=[])
    pilot_state.publish_model_stage_artifact(run["run_root"], run["attempt_id"], run["stages"][1], second)
    assert run["stages"][1] in str(_refused(run))


# ---- the source is read whole and strictly -------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("folder", "hint", "old", "new"), [("events.jsonl", "events.jsonl", b"MODULE_SELECTED", b"MODULE_SELECTEX"), ("reviewer-session-ledgers", "ledger", b"COMPLETED", b"COMPLETEX"),
                                                            ("reviewer-session-boundaries", "reviewer-session-boundary", b"reviewer-session-boundary", b"reviewer-session-boundarx"),
                                                            ("effective-canonicals", "effective", b"effective", b"effectivx"), ("model-stage-artifacts", "model-stage-artifacts", b"test_cases", b"test_casex")])
def test_a_damaged_file_of_the_source_is_refused_with_its_name_and_nothing_is_written(accepted: dict, folder: str, hint: str, old: bytes, new: bytes) -> None:
    src = accepted
    run = _follow(src)
    target = src["run_root"] / folder
    path = target if target.is_file() else next(item for item in sorted(target.rglob("*.json")) if old in item.read_bytes())
    kept = path.read_bytes()
    path.write_bytes(kept.replace(old, new, 1))
    before = _journal(run["run_root"])
    try:
        with pytest.raises(pilot_state.ReuseSourceInvalid) as refused:
            _reuse(run, run["stages"][0])
    finally:
        path.write_bytes(kept)
    assert refused.value.code == "REUSE_SOURCE_INVALID" and hint in str(refused.value) and _journal(run["run_root"]) == before


# ---- the independence of the review is the source's -----------------------------------------------------------------------------------

def test_an_independent_source_stays_independent_and_the_boundary_says_so(accepted: dict) -> None:
    run = _follow(accepted)
    _all(run)
    boundary = pilot_state.read_attempt_receipt(run["run_root"], run["attempt_id"], "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
    assert boundary["host_isolation"]["fresh_context"] is True and boundary["host_isolation"]["distinct_invocations"] is True
    assert boundary["reviewer_invocation_id"] == "reviewer-subagent-1"  # the label of the source's reviewer


def test_an_independent_source_stays_independent_in_a_run_that_admitted_self(accepted: dict) -> None:
    run = _follow(accepted, consent=True)
    _all(run)
    boundary = pilot_state.read_attempt_receipt(run["run_root"], run["attempt_id"], "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
    assert boundary["host_isolation"]["fresh_context"] is True


def test_a_self_source_needs_the_consent_of_the_human_in_the_new_run(self_accepted: dict) -> None:
    run = _follow(self_accepted)
    for stage in run["stages"]:
        _reuse(run, stage)
    before = _journal(run["run_root"])
    with pytest.raises(pilot_state.IndependenceUnverified) as refused:
        _reuse(run, REVIEW)
    assert refused.value.args[0].startswith("INDEPENDENCE_UNVERIFIED") and _journal(run["run_root"]) == before


@pytest.mark.parametrize("how", ["scan", "step"])
def test_a_self_source_with_the_consent_stays_self(self_accepted: dict, how: str) -> None:
    run = _follow(self_accepted, consent=how == "scan")
    for stage in run["stages"]:
        _reuse(run, stage)
    done = _reuse(run, REVIEW, **({"accept_self_review": True} if how == "step" else {}))
    boundary = pilot_state.read_attempt_receipt(run["run_root"], run["attempt_id"], "reviewer-session-boundary", "ARTIFACT_READ_BACK")["record"]
    assert done["independence"] == "independence_unverified" and boundary["host_isolation"]["fresh_context"] is False


def test_the_statement_of_the_host_does_not_go_with_a_reuse(accepted: dict) -> None:
    run = _follow(accepted)
    for stage in run["stages"]:
        _reuse(run, stage)
    for flags in ({"fresh_context": True}, {"invocation_id": "other-session"}):
        with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
            _reuse(run, REVIEW, **flags)


def test_the_consent_to_self_goes_with_a_self_source_only(accepted: dict) -> None:
    run = _follow(accepted)
    for stage in run["stages"]:
        _reuse(run, stage)
    with pytest.raises(ValueError, match="INVALID_ARGUMENT"):
        _reuse(run, REVIEW, accept_self_review=True)


def test_a_reviewer_who_named_the_word_in_a_source_that_said_fresh_is_not_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    words = _with_words(monkeypatch)
    src = _generated_run(tmp_path, self_review=True)  # SELF is admitted: the review that said fresh and named the word was recorded
    stage_steps.prepare(src["run_root"], REVIEW, invocation_id="reviewer-subagent-1", fresh_context=True)
    _select(src, _answer(src, codeword=next(iter(words.values()))))
    run = _follow(src)
    assert "слово" in str(_refused(run, run["stages"][0]))


def test_a_self_source_whose_reviewer_named_the_word_is_taken_and_the_report_does_not_say_otherwise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import run_report

    words = _with_words(monkeypatch)
    src = _generated_run(tmp_path, self_review=True)
    stage_steps.prepare(src["run_root"], REVIEW, fresh_context=False)
    _select(src, _answer(src, codeword=next(iter(words.values()))))
    run = _follow(src, consent=True)
    _all(run)
    report, _warnings = run_report.build_report(run["project"], run["run_root"].name)
    assert report["review_codeword"]["state"] == "reused" and "прогона" in report["review_codeword"]["note"]
    assert "ревью из прогона " + src["run_root"].name[:8] in report["review"]


# ---- the canaries ---------------------------------------------------------------------------------------------------------------------

def test_the_canaries_of_the_new_run_count_the_reused_answer_as_the_source_did(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import review_canaries

    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    src = _generated_run(tmp_path)
    stage_steps.prepare(src["run_root"], REVIEW, invocation_id="reviewer-subagent-1", fresh_context=True)
    from tests.test_review_canaries import _finding, _ids

    exp_one = _ids(src)[0]["steps"][0]["expectations"][0]["expectation_id"]
    _select(src, _answer(src, findings=[_finding("INFO", [exp_one])]))
    expected = review_canaries.summary(src["run_root"], src["attempt_id"])
    assert expected["seeded"] == 2 and expected["caught"] == 1
    run = _follow(src)
    _all(run)
    assert review_canaries.summary(run["run_root"], run["attempt_id"]) == expected


# ---- the suggestion of the scan -----------------------------------------------------------------------------------------------------

def test_the_scan_offers_the_plan_the_batches_and_the_review_of_an_accepted_source(tmp_path: Path) -> None:
    src = _source(tmp_path)
    run_root, attempt_id = _scanned(src)
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    stages = [row["stage"] for row in offered["stages"]]
    plan = json.loads(Path(src["plan"]).read_bytes())
    assert offered["run_id"] == src["run_root"].name
    assert stages[-(len(plan["batches"]) + 2):] == ["plan", *(f"tc-generator:{row['batch_id']}" for row in plan["batches"]), REVIEW]
    review = offered["stages"][-1]
    assert review["review"] == "independent" and f"--stage {REVIEW} --reuse-from {src['run_root'].name}" in review["command"]


def test_the_scan_names_a_self_review_and_the_consent_it_needs(tmp_path: Path) -> None:
    src = _source(tmp_path, self_review=True, fresh=False)
    run_root, attempt_id = _scanned(src)
    review = pilot_state.marker_reuse_suggestion(run_root, attempt_id)["stages"][-1]
    assert review["review"] == "SELF" and "SELF" in review["needs"] and "--accept-self-review" not in review["command"]


def test_the_scan_offers_the_marker_alone_and_says_why_when_the_cases_are_not_taken(tmp_path: Path) -> None:
    src = _source(tmp_path, evidence=["src/extra.py"])
    run_root, attempt_id = _scanned(src)
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] == src["run_root"].name and not [row for row in offered["stages"] if row["stage"] in ("plan", REVIEW)]
    assert offered["cases"]["reused"] is False and "evidence" in offered["cases"]["reason"]


# ---- the texts ------------------------------------------------------------------------------------------------------------------

def test_the_fix_of_a_changed_reused_answer_names_the_command_the_agent_has() -> None:
    from tools.fixes import FIXES

    assert "--reuse-from" in FIXES["MODEL_REUSE_MISMATCH"] and "--artifact" not in FIXES["MODEL_REUSE_MISMATCH"]
    assert "REUSE_SOURCE_INVALID" in FIXES and "prepare без --reuse-from" in FIXES["REUSE_SOURCE_INVALID"]
