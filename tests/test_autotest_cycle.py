"""V5-A: the automation through the commands of the agent — ``status`` → ``prepare --stage tc-to-autotest:rN`` → the answer in its slot → ``submit`` →
``status`` → ``prepare --stage autotest-reviewer:rN`` (the boundary, ``REVIEW_REQUESTED`` and the request: one call) → the answer → ``submit`` → ``status``.

The old commands (``model-request``, ``model-response``, ``automation-boundary-facts`` …) are gone since V5-B3: the cycle is the only way."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_profile_flows import mode_b_pack  # noqa: F401  (the fixture: a copy of the delivery of mode B)
from tests.helpers import prepare_stage, put_in_slot, submit_stage
from tests.test_stage_steps import _events, _status
from tools import pilot_state, stage_steps

GENERATOR, REVIEWER = "tc-to-autotest:r1", "autotest-reviewer:r1"
INDEPENDENT = ("--invocation-id", "autotest-reviewer-subagent-1", "--fresh-context", "yes")


def autotest_run(tmp_path: Path, *, self_review: bool = False, profile: str = "local-pilot-v1") -> dict:
    """A run whose cases are accepted: the effective canonical is published, the automation is next."""
    from tests.helpers import build_phase_two_baseline
    from tests.spine_helpers import run_phase_one_spine
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _effective_bundle, _published_reviewer_ledger
    from tools.pilot_state import publish_effective_canonical

    (tmp_path / "tests").mkdir(exist_ok=True)
    document = automated_document()
    identity = {"project": str(tmp_path.resolve()), "module": ".", "policy_profile": profile}
    baseline = build_phase_two_baseline(tmp_path, identity, skill_pack_root=tmp_path / ".pilot-runs")
    authorization = {"request_id": "cycle", "execution_requested": profile == "local-pilot-v1", **({"accept_self_review": True} if self_review else {})}
    started = run_phase_one_spine(tmp_path, profile, authorization, identity, baseline, {})
    root, attempt = Path(started["run"]["run_root"]), started["state"]["attempts"][0]["attempt_id"]
    effective = publish_effective_canonical(root, attempt, document, _effective_bundle(document), _published_reviewer_ledger(root, attempt, document))["record"]
    return {"root": root, "attempt": attempt, "document": document, "bundle": effective["effective_bundle_receipt_digest"], "answers": {}}


def run_generator(run: dict, revision: int = 1) -> tuple[int, dict]:
    """``prepare`` the generator of ``revision``, its answer into the slot, ``submit``."""
    from tests.test_automation_revision_budget import automation
    from tools.automation_validation import automation_sha256, autotest_review_sha256

    stage, answers = f"tc-to-autotest:r{revision}", run["answers"]
    assert prepare_stage(run["root"], stage, "--model-id", "model-automation")[0] == 0
    before = answers.get(revision - 1)
    artifact = automation(run["document"], revision, effective_bundle_receipt_digest=run["bundle"], **(
        {"predecessor": automation_sha256(before[0]), "correction_review": autotest_review_sha256(before[1])} if before else {}))
    answers[revision] = [artifact]
    put_in_slot(run["root"], stage, artifact)
    return submit_stage(run["root"], stage, None)


def boundary_of(run: dict, revision: int = 1) -> dict:
    return dict(pilot_state.read_attempt_receipt(run["root"], run["attempt"], f"automation-review-boundary-r{revision}", "ARTIFACT_READ_BACK")["record"])


def run_reviewer(run: dict, verdict: str = "ПРИНЯТО", revision: int = 1, *flags: str) -> tuple[int, dict]:
    """``prepare`` the reviewer of ``revision`` (by default an independent one), its answer into the slot, ``submit``."""
    from tests.test_automation_revision_budget import review

    stage = f"autotest-reviewer:r{revision}"
    assert prepare_stage(run["root"], stage, *(flags or INDEPENDENT))[0] == 0
    record = boundary_of(run, revision)
    answer = review(run["document"], run["answers"][revision][0], record, verdict, session_id=record["reviewer_session_id"])
    if verdict not in ("ПРИНЯТО", "AUTO_FIX_APPLIED"):
        answer["artifacts"]["autotest_review"].update(corrections=[], findings=[{"severity": "BLOCKING", "code": "ASSERTION_MISSING", "message": "Проверка шага не реализована.",
                                                                                "evidence": ["relation"], "related_ids": ["FILE-products"]}])
    run["answers"][revision].append(answer)
    put_in_slot(run["root"], stage, answer)
    return submit_stage(run["root"], stage, None)


def _closed(run: dict, capsys) -> None:
    """The attempt closed by the real ``finish --abandon``: a chain that cannot go on."""
    from types import SimpleNamespace

    from tools.run_pipeline import cmd_finish

    cmd_finish(SimpleNamespace(project=str(run["root"].parents[1]), run=run["root"].name, abandon="STAGE_RETRY_EXHAUSTED"))
    capsys.readouterr()
    assert pilot_state.read_terminal_result(run["root"], run["attempt"]) is not None


def next_of(run: dict) -> tuple[str, str, str]:
    status = _status(run["root"])
    return status["state"], status["next"]["verb"], status["next"]["stage"]


# ---- the generator: status → prepare → the slot → submit → status ----

def test_status_names_prepare_with_the_revision_of_the_stage(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    assert next_of(run) == ("automation", "prepare", GENERATOR)


def test_prepare_publishes_the_request_and_names_the_slot_the_view_and_the_budget(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    code, out = prepare_stage(run["root"], GENERATOR, "--model-id", "model-automation")
    result = out["result"]
    assert code == 0 and result["answer_slot"] == str(stage_steps.slot(run["root"], GENERATOR)) and result["budget"]["fits"]
    assert result["read"]["by_basis"].get("вид") == 1 and _events(run["root"])[-1] == f"MODEL_REQUESTED:{GENERATOR}"
    assert next_of(run) == ("automation", "submit", GENERATOR) and _status(run["root"])["waiting_for"] == "model"


def test_a_repeated_prepare_of_the_generator_writes_nothing(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    before = _events(run["root"])
    code, again = prepare_stage(run["root"], GENERATOR)
    assert code == 0 and again["result"]["answer_slot"] and _events(run["root"]) == before


def test_submit_records_the_generator_and_status_names_the_reviewer(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    code, out = run_generator(run)
    assert code == 0 and "artifact" not in out["result"] and _events(run["root"])[-2:] == [f"MODEL_RESPONSE_RECEIVED:{GENERATOR}", f"CANDIDATE_PUBLISHED:{GENERATOR}"]
    assert next_of(run) == ("automation_review", "prepare", REVIEWER)


def test_a_submit_again_says_answered_and_writes_nothing(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    before = _events(run["root"])
    code, again = submit_stage(run["root"], GENERATOR, None)
    assert code == 0 and again["result"]["answered"] is True and _events(run["root"]) == before


def test_submit_dry_run_checks_and_writes_nothing(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automation

    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    put_in_slot(run["root"], GENERATOR, automation(run["document"], effective_bundle_receipt_digest=run["bundle"]))
    before = _events(run["root"])
    code, out = submit_stage(run["root"], GENERATOR, None, "--dry-run")
    assert code == 0 and out["result"]["dry_run"] is True and _events(run["root"]) == before


def test_submit_before_prepare_is_refused(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 2 and out["code"] == "STAGE_NOT_PREPARED" and "prepare" in out["fix"]


# ---- refusals before the write: the journal gets nothing ----

@pytest.mark.parametrize(("stage", "code"), [(REVIEWER, "STAGE_WAITS"), ("tc-to-autotest:r2", "STAGE_UNKNOWN"), ("tc-to-autotest:r3", "STAGE_UNKNOWN")])
def test_a_stage_out_of_order_is_refused(tmp_path: Path, stage: str, code: str) -> None:
    run = autotest_run(tmp_path)
    before = _events(run["root"])
    status, out = prepare_stage(run["root"], stage, *INDEPENDENT)
    assert status == 2 and out["code"] == code and _events(run["root"]) == before


@pytest.mark.parametrize("command", [prepare_stage, submit_stage])
def test_a_cases_only_run_refuses_the_stages_of_the_automation_by_its_profile(tmp_path: Path, command) -> None:
    run = autotest_run(tmp_path, profile="cases-only-v1")
    before = _events(run["root"])
    for stage in (GENERATOR, REVIEWER):
        code, out = command(run["root"], stage)
        assert code == 2 and out["code"] == "MODEL_STAGE_NOT_IN_PROFILE" and "local-pilot-v1" in out["fix"] and _events(run["root"]) == before


def test_a_canon_the_journal_accepted_and_the_disk_lost_is_a_damaged_run_not_an_unknown_stage(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    pilot_state._receipt_target(run["root"], run["attempt"], "effective-canonical").unlink()
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], GENERATOR)
    assert code == 2 and out["code"] != "STAGE_UNKNOWN" and "effective" in json.dumps(out, ensure_ascii=False).lower() and _events(run["root"]) == before


def test_the_slot_of_another_stage_is_not_read(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automation

    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    put_in_slot(run["root"], "tc-to-autotest:r2", automation(run["document"], 2, effective_bundle_receipt_digest=run["bundle"]))
    before = _events(run["root"])
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 2 and out["code"] == "ANSWER_FILE_UNREADABLE" and _events(run["root"]) == before


def test_an_answer_of_another_shape_is_refused_and_the_slot_stays(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    put_in_slot(run["root"], GENERATOR, {"schema_version": "5.0.0", "stage": "tc-to-autotest", "artifacts": {"automation_revision": 1}, "warnings": []})
    before = _events(run["root"])
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 2 and out["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and _events(run["root"]) == before and stage_steps.slot(run["root"], GENERATOR).is_file()


def test_an_answer_that_is_wrong_by_its_meaning_is_refused_before_it_is_written(tmp_path: Path) -> None:
    from tests.test_automation_revision_budget import automation

    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    artifact = automation(run["document"], effective_bundle_receipt_digest=run["bundle"])
    artifact["artifacts"]["implementation_relations"][0]["case_id"] = "TC-unknown"  # the form is right, the case is not the document's
    put_in_slot(run["root"], GENERATOR, artifact)
    before = _events(run["root"])
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 2 and out["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and out["fix"] and _events(run["root"]) == before


def test_code_that_the_runner_cannot_take_is_refused_before_it_is_written(tmp_path: Path) -> None:
    import hashlib

    from tests.test_automation_revision_budget import automation

    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    artifact = automation(run["document"], effective_bundle_receipt_digest=run["bundle"])
    row = artifact["artifacts"]["generated_files"][0]
    row["language"] = "java"  # the module is python: no runner for this file
    put_in_slot(run["root"], GENERATOR, artifact)
    before = _events(run["root"])
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 2 and out["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and out["fix"] and _events(run["root"]) == before
    assert hashlib.sha256(row["content"].encode()).hexdigest()  # the fixture is a real file


def test_a_closed_attempt_takes_no_answer_and_no_request(tmp_path: Path, capsys) -> None:
    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    from tests.test_automation_revision_budget import automation

    put_in_slot(run["root"], GENERATOR, automation(run["document"], effective_bundle_receipt_digest=run["bundle"]))
    _closed(run, capsys)
    before = _events(run["root"])
    for command in ("submit", "prepare"):
        code, out = (submit_stage if command == "submit" else prepare_stage)(run["root"], GENERATOR)
        assert code == 2 and out["code"] == "ATTEMPT_TERMINAL" and _events(run["root"]) == before, (command, out)


def test_a_big_answer_is_taken_with_the_warning_of_its_size(tmp_path: Path) -> None:
    import hashlib

    from tests.test_automation_revision_budget import automation

    run = autotest_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    artifact = automation(run["document"], effective_bundle_receipt_digest=run["bundle"])
    row = artifact["artifacts"]["generated_files"][0]
    row["content"] += "# " + "x" * 150_000 + "\n"
    row["content_digest"] = "sha256:" + hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
    put_in_slot(run["root"], GENERATOR, artifact)
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 0 and "STEP_OUTPUT_LARGE" in json.dumps(out), out


# ---- the reviewer: the boundary, REVIEW_REQUESTED and the request in one prepare ----

def test_prepare_of_the_reviewer_opens_the_boundary_and_the_request_in_one_call(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    code, out = prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    result = out["result"]
    assert code == 0 and result["answer_slot"] == str(stage_steps.slot(run["root"], REVIEWER)) and isinstance(result["for_human"], str)
    assert result["read"]["count"] >= 2 and result["generator_checks"]
    assert _events(run["root"])[-4:] == ["ARTIFACT_PUBLISHED:None", "ARTIFACT_READ_BACK:None", f"REVIEW_REQUESTED:{REVIEWER}", f"MODEL_REQUESTED:{REVIEWER}"]
    boundary = boundary_of(run)["host_isolation"]
    assert (boundary["fresh_context"], boundary["distinct_invocations"]) == (True, True) and next_of(run) == ("automation_review", "submit", REVIEWER)


def test_the_reviewer_reads_the_effective_document(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    listed = json.loads(next((run["root"] / "tmp").glob("step-inputs-autotest-reviewer-r1.json")).read_text(encoding="utf-8"))["files"]
    assert any(row.get("basis") == "вид документа" for row in listed)


def test_a_repeated_prepare_of_the_reviewer_writes_nothing(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    before = _events(run["root"])
    code, again = prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    assert code == 0 and again["result"]["answer_slot"] and _events(run["root"]) == before


def test_another_reviewer_is_refused_once_the_boundary_is_open(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, "--invocation-id", "autotest-reviewer-subagent-2", "--fresh-context", "yes")
    assert code == 2 and out["code"] == "REVIEW_ALREADY_OPEN" and out["fix"] and _events(run["root"]) == before


@pytest.mark.parametrize("cut", ["request_review", "publish_model_request"])
def test_a_prepare_cut_short_is_finished_by_the_repeat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cut: str) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    real = getattr(pilot_state, cut)

    def broken(*args, **kwargs):
        if cut == "request_review" or str(args[2]) == REVIEWER:
            raise OSError("cut")
        return real(*args, **kwargs)

    monkeypatch.setattr(pilot_state, cut, broken)
    assert prepare_stage(run["root"], REVIEWER, *INDEPENDENT)[0] != 0  # the call is cut short: its error is the agent's answer
    monkeypatch.setattr(pilot_state, cut, real)
    code, out = prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    assert code == 0 and out["result"]["answer_slot"]
    assert [event for event in _events(run["root"]) if event.startswith(("REVIEW_REQUESTED:autotest", "MODEL_REQUESTED:autotest"))] == [f"REVIEW_REQUESTED:{REVIEWER}", f"MODEL_REQUESTED:{REVIEWER}"]


def test_a_fresh_context_without_a_label_is_refused_before_anything_is_written(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, "--fresh-context", "yes")
    assert code == 2 and out["code"] == "REVIEW_INVOCATION_REQUIRED" and _events(run["root"]) == before


def test_a_fresh_context_with_the_label_of_the_generator_is_refused(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, "--invocation-id", "tc-to-autotest-r1", "--fresh-context", "yes")
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and _events(run["root"]) == before


def test_no_statement_of_the_context_is_refused_in_a_run_without_self(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, "--invocation-id", "autotest-reviewer-subagent-1")
    assert code == 2 and out["code"] == "FRESH_CONTEXT_REQUIRED" and _events(run["root"]) == before


def test_the_same_session_is_refused_in_a_run_without_self(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, "--fresh-context", "no")
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED" and _events(run["root"]) == before


# ---- SELF: a run that admitted it goes on in one session, and says so ----

@pytest.mark.parametrize("flags", [("--fresh-context", "no"), ("--fresh-context", "no", "--invocation-id", "tc-to-autotest-r1"),
                                   ("--fresh-context", "no", "--invocation-id", "same-session-reviewer"), ()])
def test_in_a_run_with_self_the_same_session_reviews_without_independence(tmp_path: Path, flags: tuple) -> None:
    run = autotest_run(tmp_path, self_review=True)
    run_generator(run)
    code, out = prepare_stage(run["root"], REVIEWER, *flags)
    boundary = boundary_of(run)
    assert code == 0 and out["result"]["answer_slot"] and "в этой же сессии" in out["result"]["for_human"]
    assert (boundary["host_isolation"]["fresh_context"], boundary["host_isolation"]["distinct_invocations"]) == (False, False)
    assert boundary["reviewer_invocation_id"] != boundary["generator_invocation_id"]
    assert boundary["reviewer_invocation_id"] == (flags[3] if len(flags) == 4 and flags[3] != "tc-to-autotest-r1" else "autotest-review-self-r1")


def test_a_repeat_of_the_same_session_with_the_label_of_the_generator_changes_nothing(tmp_path: Path) -> None:
    run = autotest_run(tmp_path, self_review=True)
    run_generator(run)
    flags = ("--fresh-context", "no", "--invocation-id", "tc-to-autotest-r1")
    prepare_stage(run["root"], REVIEWER, *flags)
    before = _events(run["root"])
    code, _out = prepare_stage(run["root"], REVIEWER, *flags)
    assert code == 0 and _events(run["root"]) == before


def test_another_label_after_the_boundary_is_refused_in_a_run_with_self(tmp_path: Path) -> None:
    run = autotest_run(tmp_path, self_review=True)
    run_generator(run)
    prepare_stage(run["root"], REVIEWER, "--fresh-context", "no")
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], REVIEWER, "--fresh-context", "no", "--invocation-id", "another-session")
    assert code == 2 and out["code"] == "REVIEW_ALREADY_OPEN" and _events(run["root"]) == before


def test_in_a_run_with_self_a_fresh_context_still_needs_a_label_of_its_own(tmp_path: Path) -> None:
    run = autotest_run(tmp_path, self_review=True)
    run_generator(run)
    code, out = prepare_stage(run["root"], REVIEWER, "--invocation-id", "tc-to-autotest-r1", "--fresh-context", "yes")
    assert code == 2 and out["code"] == "INDEPENDENCE_UNVERIFIED"


def test_the_report_names_the_automation_that_was_reviewed_in_the_same_session(tmp_path: Path) -> None:
    run = autotest_run(tmp_path, self_review=True)
    assert run_generator(run)[0] == 0
    assert run_reviewer(run, "ПРИНЯТО", 1, "--fresh-context", "no")[0] == 0
    assert next_of(run)[:2] == ("materialize", "materialize")
    from tools.run_report import build_report

    report = build_report(run["root"].parents[1], run["root"].name)[0]
    assert "автотесты проверены в той же сессии" in report["review"] and "независимость не засчитана" in report["review"]


def test_a_run_with_self_goes_from_the_cases_to_materialize_on_no_label_of_the_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode_b_pack: Path) -> None:
    """The user's rule: a run that is all one session is not stopped but says so.  The cases review (``no``), the generator, the automation review (``no``) — no refusal on the way."""
    from tests import test_profile_flows as flows
    from tests.test_review_two_commands import _verdict
    from tools.run_pipeline import _durable_status

    real = flows._skillsrc

    def with_self(project: Path, window: int | None) -> None:
        real(project, window)
        manifest = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
        (project / ".skillsrc").write_text(json.dumps({**manifest, "review": {"self": True}}, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(flows, "_skillsrc", with_self)
    pack, front = mode_b_pack, flows._front(mode_b_pack, tmp_path, "local-pilot-v1")
    root, attempt, project = front["run_root"], front["attempt_id"], front["project"]

    def step(command: str, stage: str, *flags: str) -> dict:
        code, out = flows._api(pack, command, "--run-root", str(root), "--stage", stage, *flags)
        assert code == 0, (command, stage, out)
        return out["result"]

    plan = json.loads(Path(front["plan"]).read_bytes())
    for batch, fragment in zip(plan["batches"], front["fragments"]):
        stage = f"tc-generator:{batch['batch_id']}"
        Path(step("prepare", stage, "--model-id", "model-generator")["answer_slot"]).write_bytes(Path(fragment).read_bytes())
        step("submit", stage)
    Path(step("prepare", "tc-reviewer:canonical", "--fresh-context", "no")["answer_slot"]).write_bytes(Path(_verdict(front)).read_bytes())
    step("submit", "tc-reviewer:canonical")
    effective = pilot_state.read_effective_canonical(root, attempt)
    assert _durable_status(project, root, stage="status")["next"] == {"verb": "prepare", "stage": GENERATOR}
    artifact = flows._automation_answer(json.loads((front["tmp"] / "candidate.json").read_bytes()), effective)
    Path(step("prepare", GENERATOR, "--model-id", "model-automation")["answer_slot"]).write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    step("submit", GENERATOR)
    assert _durable_status(project, root, stage="status")["next"] == {"verb": "prepare", "stage": REVIEWER}
    slot = step("prepare", REVIEWER, "--fresh-context", "no")["answer_slot"]
    from tests.test_automation_revision_budget import review

    record = dict(pilot_state.read_attempt_receipt(root, attempt, "automation-review-boundary-r1", "ARTIFACT_READ_BACK")["record"])
    Path(slot).write_text(json.dumps(review(effective["document"], artifact, record, "ПРИНЯТО", session_id=record["reviewer_session_id"]), ensure_ascii=False), encoding="utf-8")
    step("submit", REVIEWER)
    assert _durable_status(project, root, stage="status")["next"]["verb"] == "materialize"
    code, report = flows._cli(pack, "tools.run_pipeline", "report", "--project", str(project), "--run", root.name)
    assert code == 0 and "автотесты проверены в той же сессии" in json.loads((root / "report.json").read_text(encoding="utf-8"))["review"]


# ---- the outcomes of the review and the second revision ----

def test_an_accepted_review_leads_to_materialize(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    code, out = run_reviewer(run)
    assert code == 0 and "artifact" not in out["result"] and _events(run["root"])[-1] == f"MODEL_RESPONSE_RECEIVED:{REVIEWER}"
    status = _status(run["root"])
    assert (status["state"], status["next"]["verb"]) == ("materialize", "materialize")


def test_a_correction_leads_to_the_second_revision_and_its_acceptance_to_materialize(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "AUTO_FIX_APPLIED")
    assert next_of(run) == ("automation", "prepare", "tc-to-autotest:r2")
    assert run_generator(run, 2)[0] == 0
    assert next_of(run) == ("automation_review", "prepare", "autotest-reviewer:r2")
    assert run_reviewer(run, "ПРИНЯТО", 2, "--invocation-id", "autotest-reviewer-subagent-2", "--fresh-context", "yes")[0] == 0  # one session reviews one revision
    assert _status(run["root"])["next"]["verb"] == "materialize"


def test_a_rejection_of_the_last_revision_leads_to_finish(tmp_path: Path) -> None:
    """V7e: the first rejection no longer ends the chain (it offers r2: ``test_automation_second_try``); the rejection of r2 does."""
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "AUTO_FIX_APPLIED")
    run_generator(run, 2)
    run_reviewer(run, "ТРЕБУЕТ ДОРАБОТКИ", 2, "--invocation-id", "autotest-reviewer-subagent-2", "--fresh-context", "yes")
    status = _status(run["root"])
    assert (status["state"], status["next"]["verb"]) == ("closure", "finish")


def test_the_second_revision_reads_the_first_and_its_review(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "AUTO_FIX_APPLIED")
    code, out = prepare_stage(run["root"], "tc-to-autotest:r2", "--model-id", "model-automation")
    assert code == 0 and {"r1", "ревью r1"} <= set(out["result"]["read"]["by_basis"])


def test_the_second_revision_is_not_a_stage_after_the_materialization_began(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "AUTO_FIX_APPLIED")
    real = pilot_state.read_attempt_receipt
    monkeypatch.setattr(pilot_state, "read_attempt_receipt", lambda root, attempt, kind, event: {} if kind == "materialization-ownership" else real(root, attempt, kind, event))
    code, out = prepare_stage(run["root"], "tc-to-autotest:r2")
    assert code == 2 and out["code"] == "STAGE_UNKNOWN"


def test_a_file_in_the_directory_of_the_ownership_is_no_materialization(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "AUTO_FIX_APPLIED")
    target = pilot_state._receipt_target(run["root"], run["attempt"], "materialization-ownership")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}", encoding="utf-8")  # the agent can write there; only the journal says the materialization began
    assert next_of(run) == ("automation", "prepare", "tc-to-autotest:r2")


def test_a_view_changed_after_the_generator_is_made_again_for_the_reviewer(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    view = next((run["root"] / "review-views").glob("*.autotest.md"))
    good = view.read_bytes()
    view.write_bytes(b"changed")
    assert prepare_stage(run["root"], REVIEWER, *INDEPENDENT)[0] == 0 and view.read_bytes() == good


def test_a_reviewer_of_the_second_revision_is_a_new_session(tmp_path: Path) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    run_reviewer(run, "AUTO_FIX_APPLIED")
    run_generator(run, 2)
    before = _events(run["root"])
    code, out = prepare_stage(run["root"], "autotest-reviewer:r2", *INDEPENDENT)  # the label of r1
    assert code == 2 and out["code"] == "REVIEW_INVOCATION_REQUIRED" and "новой сессией" in out["fix"] and _events(run["root"]) == before


@pytest.mark.parametrize("name", ["_open_automation_review", "_automation_check"])
def test_the_calls_of_the_automation_run_under_one_cache_of_the_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    run = autotest_run(tmp_path)
    seen = []
    real = getattr(stage_steps, name)
    monkeypatch.setattr(stage_steps, name, lambda *args, **kwargs: (seen.append(pilot_state._CLOSURE_CACHE.get() is not None), real(*args, **kwargs))[1])
    run_generator(run)
    if name == "_open_automation_review":
        prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    assert seen == [True]


def test_a_closed_attempt_takes_no_answer_of_the_reviewer(tmp_path: Path, capsys) -> None:
    run = autotest_run(tmp_path)
    run_generator(run)
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    _closed(run, capsys)
    before = _events(run["root"])
    code, out = submit_stage(run["root"], REVIEWER, None)
    assert code == 2 and out["code"] == "ATTEMPT_TERMINAL" and _events(run["root"]) == before


def test_the_calls_of_the_cycle_start_no_process_and_no_model() -> None:
    import ast

    tree = ast.parse(Path(stage_steps.__file__).read_text(encoding="utf-8"))
    names = {alias.name for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert not names & {"subprocess", "os", "socket", "urllib", "tools.exec_runner"}
