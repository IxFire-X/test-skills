"""V6t: a set accepted in part (r2 of a ``ТРЕБУЕТ ДОРАБОТКИ`` answer keeps the display_order of its cases, so it has holes) runs through the
automation, the build and the close: the trace audit takes the holes.  Before, the attempt closed FINALIZATION_INVALID (live run 12.11)."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import MODULE_PYTHON
from tests.p4_flow import run_the_build
from tests.test_review_canaries import _answer, _boundary_and_removed, _finding, _ids, _open, _select
from tests.test_review_requested import _api, _generated_run, _put

FIXTURE = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
FAILING = "def test_selected() -> None:\n    assert 1 == 2\n"


def _fixture_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_generated_run`` over the fixture project of a build, with automated cases: its run is a run of local-pilot-v1."""
    import tests.test_batch_assembly as batch
    import tests.test_reviewer_protocol as protocol
    from tests.test_exec_prepares import _module_python
    from tests.test_profile_flows import _automated
    from tests.test_project_native_pytest import _local_attempt

    def new_run(project: Path):
        shutil.copytree(FIXTURE, project, dirs_exist_ok=True)
        (project / ".skillsrc").write_text(json.dumps({
            "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [{"id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
                         "paths": {"source": ["src"], "tests": ["tests"]},
                         "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON,
                                  "build_profile": "default", "adapter_parameters": {}},
                         "detected_from": ["pyproject.toml"]}],
        }, ensure_ascii=False), encoding="utf-8")
        _module_python(project)
        return _local_attempt(project)[:2]

    fragment = batch._fragment
    monkeypatch.setattr(protocol, "_new_run", new_run)
    monkeypatch.setattr(batch, "_fragment", lambda *args: _automated(fragment(*args)))


def _accepted_in_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str | None) -> dict:
    """Two cases generated, the first sent back by the reviewer (r2: the second one, display_order [2]), its automation accepted."""
    from tests.test_automation_revision_budget import review
    from tests.test_profile_flows import _automation_answer
    from tests.test_review_requested import _generated_run
    from tools import orchestrate_test_case_revision as orchestrate, pilot_state

    monkeypatch.setattr(orchestrate, "reject_share", lambda *_args: 50)
    _fixture_run(monkeypatch)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    one, _two = _ids(run)
    findings = [*_boundary_and_removed(run), _finding("BLOCKING", [one["case_id"]], "Нет проверки состояния.")]
    assert _select(run, _answer(run, "ТРЕБУЕТ ДОРАБОТКИ", findings=findings))["effective_source"]["revision"] == 2
    root, attempt = run["run_root"], run["attempt_id"]
    flags, tmp = ("--run-root", str(root), "--attempt", attempt), root / "tmp"
    document = pilot_state.read_effective_canonical(root, attempt)["document"]
    assert [case["display_order"] for case in document["test_cases"]] == [2]
    effective = _api("read-effective", *flags)[1]["result"]
    assert _api("model-request", *flags, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1",
                "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])[0] == 0
    artifact = _automation_answer(document, effective)
    if content is not None:
        file = artifact["artifacts"]["generated_files"][0]
        file.update(content=content, content_digest="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
    assert _api("model-response", *flags, "--stage", "tc-to-autotest:r1", "--artifact", _put(tmp / "automation.json", artifact))[0] == 0
    statement = ("--fresh-context", "yes", "--reviewer-invocation-id", "automation-reviewer-subagent-1")
    assert _api("automation-boundary-facts", *flags, "--revision", "1", *statement, "--out", str(tmp / "facts.json"))[0] == 0
    assert _api("open-automation-boundary", *flags, "--facts", str(tmp / "facts.json"))[0] == 0
    digest = json.loads((tmp / "facts.json").read_bytes())["automation_digest"]
    assert _api("review-requested", *flags, "--stage", "autotest-reviewer:r1", "--digest", digest)[0] == 0
    record = _api("read-receipt", *flags, "--kind", "automation-review-boundary-r1", "--event", "ARTIFACT_READ_BACK")[1]["result"]["record"]
    assert _api("model-request", *flags, "--stage", "autotest-reviewer:r1", "--model-id", "model-automation-reviewer",
                "--invocation-id", record["reviewer_invocation_id"], "--input-digest", record["automation_digest"], "--input-digest", record["digest"])[0] == 0
    verdict = review(document, artifact, record, "ПРИНЯТО", session_id=record["reviewer_session_id"])
    for key in ("automation_sha256", "reviewed_relations_sha256", "host_isolation_sha256"):
        del verdict["artifacts"]["autotest_review"][key]
    assert _api("model-response", *flags, "--stage", "autotest-reviewer:r1", "--artifact", _put(tmp / "review.json", verdict))[0] == 0
    return run


def test_a_set_accepted_in_part_closes_partial_when_the_autotests_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                         capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import read_terminal_result

    run = _accepted_in_part(tmp_path, monkeypatch, None)
    code, output = run_the_build(tmp_path / "project", run["run_root"], capsys)
    assert output["verdict"] == "PASS" and output["finalization"] == "complete", output
    result = read_terminal_result(run["run_root"], run["attempt_id"])
    assert (result["completion"], result["verification"], result["reason_code"], result["accepted"]) == ("PARTIAL", "PASS", "PARTIAL_ACCEPTED", False), result


def test_a_set_accepted_in_part_closes_as_any_failed_run_when_the_autotests_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                                capsys: pytest.CaptureFixture[str]) -> None:
    from tools.pilot_state import read_terminal_result

    run = _accepted_in_part(tmp_path, monkeypatch, FAILING)
    code, output = run_the_build(tmp_path / "project", run["run_root"], capsys)
    assert output["verdict"] == "FAIL" and output["finalization"] == "complete", output
    result = read_terminal_result(run["run_root"], run["attempt_id"])
    assert (result["completion"], result["verification"], result.get("reason_code"), result["accepted"]) == ("COMPLETE", "FAIL", None, False), result
