"""V5b-3: the answer of the autotest generator (``tc-to-autotest:r1``) of an earlier run is the answer of a new run of the same project; the review of the autotests
is done anew, in the ordinary way.  By the commands of a delivery of mode B (the helpers of ``test_profile_flows``); the sources are real runs, the new run reuses
the cases (V5b-1) and then the autotests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import test_profile_flows as flows
from tests.test_pack_profile import copy_profile
from tools import pilot_state, stage_steps

GENERATOR, REVIEWER = "tc-to-autotest:r1", "autotest-reviewer:r1"
FIELD = "effective_bundle_receipt_digest"  # the one field of the answer that holds the path of the run


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("reuse3") / "pack", "mode-b")


def _ok(pack: Path, command: str, run: dict, stage: str, *flags: str) -> dict:
    code, out = flows._api(pack, command, "--run-root", str(run["run_root"]), "--stage", stage, *flags)
    assert code == 0, (command, stage, out)
    return out["result"]


def _answer(run: dict, stage: str) -> dict:
    stages = pilot_state.model_lifecycle_projection(run["run_root"], run["attempt_id"])["stages"]
    return pilot_state.read_model_stage_artifact(run["run_root"], run["attempt_id"], stage, stages[stage]["MODEL_RESPONSE_RECEIVED"]["artifact_digest"])["artifact"]


def _effective(run: dict) -> dict:
    return dict(pilot_state.read_effective_canonical(run["run_root"], run["attempt_id"]))


def _generate(pack: Path, run: dict, revision: int = 1) -> None:
    """The generator by the agent: ``prepare``, the answer in the slot, ``submit`` (r2 names r1 and its review)."""
    from tools.automation_validation import automation_sha256, autotest_review_sha256

    effective = _effective(run)
    answer = flows._automation_answer(effective["document"], effective)
    if revision == 2:
        answer["artifacts"].update(automation_revision=2, predecessor_automation_sha256=automation_sha256(_answer(run, GENERATOR)),
                                   correction_review_sha256=autotest_review_sha256(_answer(run, REVIEWER)))
    stage = f"tc-to-autotest:r{revision}"
    flows._put(Path(_ok(pack, "prepare", run, stage, "--model-id", "model-automation")["answer_slot"]), answer)
    _ok(pack, "submit", run, stage)


def _review(pack: Path, run: dict, verdict: str = "ПРИНЯТО", revision: int = 1, same_session: bool = False) -> None:
    from tests.test_automation_revision_budget import review

    stage = f"autotest-reviewer:r{revision}"
    flags = ("--fresh-context", "no") if same_session else ("--fresh-context", "yes", "--invocation-id", f"automation-reviewer-subagent-{revision}")
    slot = _ok(pack, "prepare", run, stage, *flags)["answer_slot"]
    record = dict(pilot_state.read_attempt_receipt(run["run_root"], run["attempt_id"], f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK")["record"])
    answer = review(_effective(run)["document"], _answer(run, f"tc-to-autotest:r{revision}"), record, verdict, session_id=record["reviewer_session_id"])
    for key in ("automation_sha256", "reviewed_relations_sha256", "host_isolation_sha256"):  # the code puts them
        del answer["artifacts"]["autotest_review"][key]
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        answer["artifacts"]["autotest_review"].update(corrections=[], findings=[{"severity": "BLOCKING", "code": "ASSERTION_MISSING", "message": "Проверка шага не реализована.",
                                                                                "evidence": ["relation"], "related_ids": ["FILE-products"]}])
    flows._put(Path(slot), answer)
    _ok(pack, "submit", run, stage)


def _source(pack: Path, tmp_path: Path, *verdicts: str, same_session: bool = False) -> dict:
    """A run whose cases the review accepted and whose autotests went through the review (one verdict per revision) — then the writing of the tests failed."""
    patch = pytest.MonkeyPatch()
    if same_session:
        real = flows._skillsrc

        def with_self(project: Path, window: int | None) -> None:
            real(project, window)
            manifest = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
            (project / ".skillsrc").write_text(json.dumps({**manifest, "review": {"self": True}}, ensure_ascii=False), encoding="utf-8")

        patch.setattr(flows, "_skillsrc", with_self)
    try:
        run = flows._front(pack, tmp_path, "local-pilot-v1")
        flows._cases_to_effective(pack, run, same_session=same_session)
    finally:
        patch.undo()
    for revision, verdict in enumerate(verdicts, start=1):
        _generate(pack, run, revision)
        _review(pack, run, verdict, revision, same_session)
    return run


@pytest.fixture(scope="module")
def source(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _source(pack, tmp_path_factory.mktemp("accepted"), "ПРИНЯТО")


@pytest.fixture(scope="module")
def corrected(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _source(pack, tmp_path_factory.mktemp("corrected"), "AUTO_FIX_APPLIED", "ПРИНЯТО")


@pytest.fixture(scope="module")
def rejected(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _source(pack, tmp_path_factory.mktemp("rejected"), "ТРЕБУЕТ ДОРАБОТКИ")


@pytest.fixture(scope="module")
def self_source(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _source(pack, tmp_path_factory.mktemp("self"), "ПРИНЯТО", same_session=True)


def _scan(pack: Path, project: Path) -> dict:
    code, scanned = flows._scan(pack, "--project", str(project), "--docs", "docs/requirements.md", "--profile", "local-pilot-v1", "--module", "python-pytest", "--new-run")
    assert code == 0, scanned
    return scanned


def _follow(pack: Path, src: dict, *, autotests: bool = True) -> dict:
    """A new run of the same project that takes from ``src`` the marker, the plan, the batches, the review — and the autotests unless ``autotests`` is off: no model is called.
    (The scan suggests the newest run that can be a source, so the stages are named from ``src``, not read from the scan.)"""
    scanned = _scan(pack, src["project"])
    run = {"run_root": Path(scanned["run_root"]), "attempt_id": scanned["attempt_id"], "project": src["project"], "scanned": scanned, "source": src["run_root"].name}
    answered = [row["stage_instance_id"] for row in pilot_state.derive_state(src["run_root"])["events"] if row["event_type"] == "MODEL_RESPONSE_RECEIVED"]
    markers = [stage for stage in answered if stage.startswith("context-marker:")]
    for stage in [*markers, "plan", *(stage for stage in answered if stage not in markers and pilot_state.reuse_family(stage) in ("batch", "review", "automation") and (autotests or stage != GENERATOR))]:
        code, out = flows._api(pack, "prepare", "--run-root", str(run["run_root"]), "--stage", stage, "--reuse-from", run["source"])
        assert code == 0 and out["result"]["answered"] is True and out["result"].get("model_call") is False, (stage, out)
    return run


def _journal(run: dict) -> list[tuple]:
    """The events of the run but the refused commands (the journal says every one)."""
    return [(row["event_type"], row.get("stage_instance_id")) for row in pilot_state.derive_state(run["run_root"])["events"] if row["event_type"] != "COMMAND_REFUSED"]


def _asked_of_a_model(run: dict) -> list[str]:
    state = pilot_state.derive_state(run["run_root"])
    return [row["stage_instance_id"] for row in state["events"] if row["event_type"] == "MODEL_REQUESTED"
            and "reuse" not in pilot_state.read_model_request(run["run_root"], run["attempt_id"], row["stage_instance_id"], row["artifact_digest"])]


@pytest.fixture(scope="module")
def followed(pack: Path, source: dict) -> dict:
    return _follow(pack, source)


# ---- the autotests of the source in the new run ------------------------------------------------------------------------------

def test_the_new_run_takes_the_autotests_and_calls_one_model_before_the_materialization(pack: Path, source: dict, followed: dict) -> None:
    flows._status(pack, followed["project"], followed["run_root"].name, "automation_review", "prepare")
    _review(pack, followed)
    flows._status(pack, followed["project"], followed["run_root"].name, "materialize", "materialize")
    assert _asked_of_a_model(followed) == [REVIEWER]


def test_the_replayed_answer_is_the_source_answer_but_for_the_field_of_the_path_of_the_run(source: dict, followed: dict) -> None:
    mine, theirs = _answer(followed, GENERATOR), _answer(source, GENERATOR)
    assert mine["artifacts"]["source"][FIELD] == _effective(followed)[FIELD] != theirs["artifacts"]["source"][FIELD]
    mine["artifacts"]["source"][FIELD] = theirs["artifacts"]["source"][FIELD] = None
    assert mine == theirs


def test_the_autotests_of_a_source_with_another_baseline_are_not_taken() -> None:
    refusal = pilot_state._automation_refusal(Path("."), {"attempt": {"baseline_digest": "sha256:a"}, "stages": {}}, {"baseline_digest": "sha256:b"})
    assert refusal and "baseline" in refusal


@pytest.mark.parametrize("name, said", [("corrected", "ревизии 2"), ("rejected", "не принято")])
def test_autotests_not_accepted_on_the_first_revision_are_not_taken_and_the_cases_are(pack: Path, request: pytest.FixtureRequest, name: str, said: str) -> None:
    src = request.getfixturevalue(name)
    run = _follow(pack, src, autotests=False)
    assert run["scanned"]["reuse"]["automation"]["reused"] is False and said in run["scanned"]["reuse"]["automation"]["reason"]
    code, refused = flows._api(pack, "prepare", "--run-root", str(run["run_root"]), "--stage", GENERATOR, "--reuse-from", run["source"])
    assert code == 2 and refused["code"] == "MODEL_REUSE_REFUSED" and said in refused["message"] and "prepare без --reuse-from" in refused["fix"], refused


def test_a_damaged_answer_of_the_source_is_refused_and_the_journal_of_the_new_run_is_as_it_was(pack: Path, source: dict) -> None:
    run = _follow(pack, source, autotests=False)
    target = next(path for path in sorted((source["run_root"] / "model-stage-artifacts").rglob("*.json")) if b"generated_files" in path.read_bytes())
    data, before = target.read_bytes(), _journal(run)
    target.write_bytes(data.replace(b"generated_files", b"generated_filex", 1))
    try:
        code, refused = flows._api(pack, "prepare", "--run-root", str(run["run_root"]), "--stage", GENERATOR, "--reuse-from", run["source"])
        assert code == 2 and refused["code"] == "REUSE_SOURCE_INVALID" and "model-stage-artifacts" in refused["message"], refused
        assert _journal(run) == before
    finally:
        target.write_bytes(data)


# ---- in the process of the test: what the commands cannot show ----------------------------------------------------------------

@pytest.fixture
def inside(pack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The library reads the package of the copy ``pack`` as the one the source was made with."""
    digest = json.loads((pack / "release" / "manifest.json").read_text(encoding="utf-8"))["skill_pack_digest"]
    monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: digest)


def test_a_byte_of_the_answer_other_than_the_field_of_the_path_is_refused(pack: Path, source: dict, inside: None, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _follow(pack, source, autotests=False)
    real = pilot_state.publish_model_stage_artifact

    def changed(root, attempt, stage, value, **kwargs):
        value = json.loads(json.dumps(value))
        value["warnings"] = ["one more word"]  # nothing else binds it: only the comparison with the source does
        return real(root, attempt, stage, value, **kwargs)

    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", changed)
    with pytest.raises(pilot_state.ModelReuseMismatch):
        stage_steps.prepare(run["run_root"], GENERATOR, reuse_from=run["source"])
    assert "MODEL_RESPONSE_RECEIVED" not in pilot_state.model_lifecycle_projection(run["run_root"], run["attempt_id"])["stages"].get(GENERATOR, {})


def test_a_repeat_of_a_cut_prepare_finishes_the_reuse(pack: Path, source: dict, inside: None, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _follow(pack, source, autotests=False)
    real = pilot_state.publish_model_stage_artifact

    def cut(*args, **kwargs):
        raise RuntimeError("cut after the request")

    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", cut)
    with pytest.raises(RuntimeError):
        stage_steps.prepare(run["run_root"], GENERATOR, reuse_from=run["source"])
    monkeypatch.setattr(pilot_state, "publish_model_stage_artifact", real)
    done = stage_steps.prepare(run["run_root"], GENERATOR, reuse_from=run["source"])
    assert done["answered"] is True and done["model_call"] is False and done["reused_from"] == run["source"]
    assert _answer(run, GENERATOR)["artifacts"]["source"][FIELD] == _effective(run)[FIELD]


# ---- the new review does not take the answer ------------------------------------------------------------------------------------

def test_a_review_that_does_not_take_the_replayed_autotests_goes_to_the_second_revision_by_the_model(pack: Path, source: dict) -> None:
    run = _follow(pack, source)
    _review(pack, run, "AUTO_FIX_APPLIED")
    flows._status(pack, run["project"], run["run_root"].name, "automation", "prepare")
    _generate(pack, run, 2)
    _review(pack, run, "ПРИНЯТО", 2)
    flows._status(pack, run["project"], run["run_root"].name, "materialize", "materialize")
    assert _asked_of_a_model(run) == [REVIEWER, "tc-to-autotest:r2", "autotest-reviewer:r2"]


# ---- SELF -------------------------------------------------------------------------------------------------------------------------

def test_a_self_source_stays_self_to_the_report(pack: Path, self_source: dict) -> None:
    from tests.p4_flow import human_run

    run = _follow(pack, self_source)
    offered = run["scanned"]["reuse"]["stages"]  # the only project the scan sees one source in: the autotests follow the review of the cases
    assert [row["stage"] for row in offered][-2:] == ["tc-reviewer:canonical", GENERATOR] and offered[-2]["review"] == "SELF"
    _review(pack, run, same_session=True)
    shared = ("--project", str(run["project"]), "--run", run["run_root"].name, "--module", "python-pytest")
    code, printed = flows._cli(pack, "tools.run_pipeline", "materialize", *shared)
    assert code == 0 and human_run(run["project"], printed).returncode == 0, printed
    code, ingested = flows._cli(pack, "tools.run_pipeline", "ingest", *shared)
    assert code == 0 and ingested["verdict"] == "PASS", ingested
    code, reported = flows._cli(pack, "tools.run_pipeline", "report", "--project", str(run["project"]), "--run", run["run_root"].name)
    review = json.loads((run["run_root"] / "report.json").read_text(encoding="utf-8"))["review"]
    assert code == 0 and "SELF" in review and "автотесты проверены в той же сессии" in review, review
