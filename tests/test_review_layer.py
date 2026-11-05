"""FaT-U5: the review layer in one CLI — the seven reviewer-package inputs come from the journal, a file is only compared.

- `tools.api review-publish | session-start | review-open | review-select` without --candidate … --generator-fragment
  give byte for byte what the same commands give with the files (receipts, package/ledger, effective);
- a file that differs from the journal is REVIEW_INPUT_MISMATCH naming the field and its source, nothing is written;
- a missing step is REVIEW_INPUTS_UNAVAILABLE with its fix; `publish-evidence` is gone (review-evidence is the one way);
- the old `tools.orchestrate_test_case_revision` commands still work and say COMMAND_RENAMED;
- every CLI answers a refusal in one envelope {status, code, message, fix, next}."""

from __future__ import annotations

import json
from pathlib import Path

from tests.journal_helpers import assert_refused_once
from tests.test_review_requested import _api, _cmd, _complete, _generated_run, _journal, _put, _review, _review_answer


def _flags(run: dict) -> list[str]:
    return ["--run-root", str(run["run_root"]), "--attempt", run["attempt_id"]]


def _bundles(run: dict) -> str:
    return str(run["project"] / "bundles")


def _file_inputs(run: dict) -> dict:
    """The seven inputs as the agent passed them before FaT-U5 (files of tests.test_review_requested)."""
    from tools.batch_assembly import assemble_candidate
    from tools.orchestrate_test_case_revision import _load_receipt

    tmp = run["tmp"]
    plan, header = json.loads(Path(run["plan"]).read_bytes()), json.loads(Path(run["header"]).read_bytes())
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    candidate, assembly = assemble_candidate(header, plan, fragments)
    return {"candidate": candidate, "candidate_receipt": _load_receipt(tmp / "candidate-receipt.json"), "assembly_receipt": assembly,
            "inventory_receipt": json.loads(Path(run["inventory"]).read_bytes()),
            "context_receipts": [json.loads(Path(run["context"]).read_bytes())],
            "context_marker_output": json.loads(Path(run["marker"]).read_bytes()), "generator_fragments": fragments}


def _published(run: dict) -> dict:
    code, published = _api("review-publish", *_flags(run), "--output-dir", _bundles(run))
    assert code == 0 and published["result"]["status"] == "published", published
    _put(run["tmp"] / "candidate-receipt.json", published["result"]["candidate_bundle_receipt"])
    from tools.orchestrate_test_case_revision import journal_assembly

    candidate, assembly, _fragments = journal_assembly(run["run_root"], run["attempt_id"])
    _put(run["tmp"] / "candidate.json", candidate)
    _put(run["tmp"] / "assembly-receipt.json", assembly)
    return published["result"]


def test_the_journal_inputs_are_the_files_byte_for_byte(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import REVIEW_INPUT_FIELDS, _review_key, review_inputs

    run = _generated_run(tmp_path)
    _published(run)
    journal = review_inputs(run["run_root"], run["attempt_id"], output_dir=Path(_bundles(run)))
    files = _file_inputs(run)
    for field in REVIEW_INPUT_FIELDS:
        assert _review_key(field, journal[field]) == _review_key(field, files[field]), field
    assert [item["digest"] for item in journal["context_receipts"]] == [files["context_receipts"][0]["digest"]]


def test_the_whole_review_by_commands_without_the_seven_files(tmp_path: Path) -> None:
    from tools.orchestrate_test_case_revision import review_inputs, review_package

    run = _generated_run(tmp_path)
    published = _published(run)
    code, start = _api("session-start", *_flags(run), "--output-dir", _bundles(run), "--fresh-context", "yes",
                       "--reviewer-invocation-id", "reviewer-subagent-1", "--out", str(run["tmp"] / "session-start.json"))
    assert code == 0, start
    with_files = review_package(run["run_root"], run["attempt_id"], _file_inputs(run))
    code, opened = _api("review-open", *_flags(run), "--output-dir", _bundles(run), "--session-start", start["out"])
    assert code == 0, opened
    # the package (the ledger's) is the one the files give, byte for byte
    assert json.dumps(opened["result"]["package_binding"], sort_keys=True) == json.dumps(with_files, sort_keys=True)
    candidate = published["candidate_bundle_receipt"]["document_sha256"]
    assert _review(run, candidate)[0] == 0
    boundary = opened["result"]["reviewer_session_boundary"]
    code, requested = _api("model-request", *_flags(run), "--stage", "tc-reviewer:canonical", "--model-id", "model-reviewer",
                           "--invocation-id", boundary["reviewer_invocation_id"],
                           "--input-digest", boundary["canonical_branch_digest"], "--input-digest", boundary["package_digest"])
    assert code == 0, requested
    code, answered = _api("model-response", *_flags(run), "--stage", "tc-reviewer:canonical", "--artifact", _review_answer(run))
    assert code == 0, answered
    assert _complete(run)[0] == 0
    # the review answer comes from the journal too: no --review, no --evidence-receipt
    code, selected = _api("review-select", *_flags(run), "--output-dir", _bundles(run), "--reviewer-session", str(run["tmp"] / "session.json"))
    assert code == 0 and selected["result"]["effective_bundle_receipt"] is not None, selected
    # the old command with every file answers the same selection (idempotent), byte for byte
    from tests.test_review_requested import _select

    code, again = _select(run, _review_answer(run))
    assert code == 0, again
    assert {key: again[key] for key in selected["result"]} == selected["result"]
    assert again["warnings"] == [{"code": "COMMAND_RENAMED", "message": "tools.api review-select"}]
    assert review_inputs(run["run_root"], run["attempt_id"], output_dir=Path(_bundles(run)))["context_receipts"] == _file_inputs(run)["context_receipts"]


def test_a_file_that_differs_from_the_journal_is_refused_by_field_and_source(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    _published(run)
    forged = json.loads(Path(run["marker"]).read_bytes())
    forged["warnings"] = [{"code": "X", "message": "другой маркер"}]
    path = _put(run["tmp"] / "forged-marker.json", forged)
    before = _journal(run["run_root"])
    code, refused = _api("session-start", *_flags(run), "--output-dir", _bundles(run), "--context-marker-output", path,
                         "--fresh-context", "yes", "--reviewer-invocation-id", "reviewer-subagent-1")
    assert code == 2 and refused["code"] == "REVIEW_INPUT_MISMATCH", refused
    assert "context_marker_output" in refused["message"] and "context-marker:baseline" in refused["message"], refused
    assert refused["fix"] and set(refused) >= {"status", "code", "message", "fix", "next"}, refused
    assert _journal(run["run_root"]) == before
    assert_refused_once(run["run_root"], before, "REVIEW_INPUT_MISMATCH", "session-start")  # J1: the one event the refusal itself added


def test_a_missing_step_is_unavailable_with_its_fix(tmp_path: Path) -> None:
    run = _generated_run(tmp_path, generated=False)  # the generator never answered
    code, refused = _api("review-publish", *_flags(run), "--output-dir", _bundles(run))
    assert code == 2 and refused["code"] in {"REVIEW_INPUTS_UNAVAILABLE", "MODEL_INPUTS_MISMATCH"} and refused.get("fix"), refused


def test_publish_evidence_is_gone_and_the_old_commands_say_renamed(tmp_path: Path) -> None:
    run = _generated_run(tmp_path)
    code, refused = _cmd("tools.orchestrate_test_case_revision", "publish-evidence", "--context-receipt", run["context"],
                         "--run-root", str(run["run_root"]), "--attempt-id", run["attempt_id"])
    assert code == 2 and "publish-evidence" in json.dumps(refused, ensure_ascii=False), refused
    from tools.orchestrate_test_case_revision import journal_assembly

    candidate = _put(run["tmp"] / "candidate.json", journal_assembly(run["run_root"], run["attempt_id"])[0])
    code, published = _cmd("tools.orchestrate_test_case_revision", "publish", "--candidate", candidate, "--output-dir", _bundles(run),
                           "--run-root", str(run["run_root"]), "--attempt-id", run["attempt_id"])
    assert code == 0 and published["status"] == "published", published
    assert {"code": "COMMAND_RENAMED", "message": "tools.api review-publish"} in published["warnings"], published


def test_every_cli_refuses_in_one_envelope(tmp_path: Path, capsys) -> None:
    from tools.run_pipeline import main

    run = _generated_run(tmp_path)
    code, api = _api("review-open", *_flags(run), "--output-dir", str(tmp_path / "none"), "--session-start", run["start"])
    assert code == 2 and set(api) >= {"status", "code", "message", "fix", "next"} and api["status"] == "error", api
    code, old = _cmd("tools.orchestrate_test_case_revision", "open-session", "--candidate", run["marker"], "--candidate-receipt", run["marker"],
                     "--assembly-receipt", run["marker"], "--inventory-receipt", run["inventory"], "--context-receipt", run["context"],
                     "--context-marker-output", run["marker"], "--generator-fragment", run["fragments"][0], "--session-start", run["start"],
                     "--run-root", str(run["run_root"]), "--attempt-id", run["attempt_id"])
    assert code != 0 and set(old) >= {"status", "code", "message", "fix", "next"} and old["code"] != "ORCHESTRATION_INPUT", old
    capsys.readouterr()
    assert main(["status", "--project", str(run["project"]), "--run", "0" * 32]) == 2
    pipeline = json.loads(capsys.readouterr().out)
    assert set(pipeline) >= {"status", "code", "message", "fix", "next"} and pipeline["code"] == pipeline["reason"], pipeline
