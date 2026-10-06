from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path

import pytest
from tests.helpers import MODULE_PYTHON

from tests.test_project_native_pytest import _reviewed_inputs
from tools.automation_validation import automation_sha256, autotest_review_sha256
from tools.execution_adapters import ExecutionRequest, PYTEST, request_digest
from tools.pilot_state import (
    append_event,
    claim_execution_start,
    derive_state,
    publish_attempt_receipt,
    publish_run_artifact_bytes,
    read_attempt_receipt,
)
from tools.schema_validation import schema_diagnostics


def _execution_facts(
    tmp_path: Path, *, project: Path | None = None, failed: bool = False,
    wait_for_input: bool = False,
) -> tuple[Path, str, dict, ExecutionRequest, dict]:
    project = project or tmp_path / "project"
    project.mkdir(exist_ok=True)
    (project / "tests").mkdir(exist_ok=True)
    document, automation, review, _boundary, run_root, attempt_id, _authorization, durable = _reviewed_inputs(
        project, wait_for_input=wait_for_input,
    )
    attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    delta = durable["delta"]
    executable = project / MODULE_PYTHON
    selector = "tests/test_generated.py::test_selected"
    request = ExecutionRequest(
        adapter_id=PYTEST,
        executable=str(executable),
        argv=(str(executable), "-m", "pytest", "--junitxml", "test-results/pytest.xml", selector),
        cwd=str(project.resolve()),
        selectors=(selector,),
        timeout_seconds=600,
        report_paths=("test-results/pytest.xml",),
        environment_labels=("PROJECT_NATIVE_ENV",),
        build_profile="default",
        typed_parameters=(),
    )
    execution_digest = request_digest(request)
    report_bytes = (
        b"<testsuite tests='1' failures='1'><testcase classname='tests.test_generated' "
        b"name='test_selected'><failure message='synthetic failure'/></testcase></testsuite>"
        if failed else
        b"<testsuite tests='1'><testcase classname='tests.test_generated' name='test_selected'/></testsuite>"
    )
    report_digest = "sha256:" + hashlib.sha256(report_bytes).hexdigest()
    from tools.run_tests import (
        DURABLE_NATIVE_REPORT_NORMALIZATION,
        _normalize_durable_junit_report,
    )

    retained_report = publish_run_artifact_bytes(
        run_root, attempt_id, "reports/pytest.xml",
        _normalize_durable_junit_report(report_bytes),
    )
    generated_bytes = (project / "tests" / "test_generated.py").read_bytes()
    retained_generated = publish_run_artifact_bytes(
        run_root, attempt_id, "generated/tests/test_generated.py", generated_bytes,
    )
    artifact_evidence = [
        {
            "kind": "native_report", "source_path": "test-results/pytest.xml",
            "source_digest": report_digest,
            "normalization": DURABLE_NATIVE_REPORT_NORMALIZATION,
            "path": retained_report["path"], "digest": retained_report["digest"],
        },
        {
            "kind": "generated_test", "source_path": "tests/test_generated.py",
            "path": retained_generated["path"], "digest": retained_generated["digest"],
        },
    ]
    if failed:
        runner_output = publish_run_artifact_bytes(
            run_root, attempt_id, "runner-output.txt", b"synthetic failure\n",
        )
        artifact_evidence.append({
            "kind": "runner_output",
            "path": runner_output["path"],
            "digest": runner_output["digest"],
        })
    report = {
        "schema_version": "5.0.0",
        "stage": "run-tests",
        "source": dict(automation["artifacts"]["source"]),
        "automation_sha256": automation_sha256(automation),
        "autotest_review_sha256": autotest_review_sha256(review),
        "verdict": "FAIL" if failed else "PASS",
        "target": {
            "language": "python",
            "framework": "pytest",
            "runner": "pytest",
            "command": PYTEST,
        },
        "environment": {
            "status": "ready",
            "interpreter": str(executable),
            "interpreter_path": str(executable),
            "working_dir": str(project.resolve()),
            "missing": None,
            "safe_key_labels": ["PROJECT_NATIVE_ENV"],
        },
        "execution": {
            "adapter_id": PYTEST,
            "executable_path": str(executable),
            "cwd": str(project.resolve()),
            "run_id": attempt["run_id"],
            "attempt_id": attempt_id,
            "baseline_digest": attempt["baseline_digest"],
            "generated_delta_digest": delta["digest"],
            "build_profile": "default",
            "typed_parameters": {},
            "argv": list(request.argv),
            "selectors": list(request.selectors),
            "timeout_seconds": 600,
            "report_paths": ["test-results/pytest.xml"],
            "request_digest": execution_digest,
            "report_evidence": [{"path": "test-results/pytest.xml", "digest": report_digest}],
            "artifact_evidence": artifact_evidence,
        },
        "stats": {
            "total": 1, "passed": 0 if failed else 1, "failed": 1 if failed else 0,
            "errors": 0, "skipped": 0, "duration_sec": 0.1,
        },
        "failed_methods": None,
        "root_cause": None,
        "raw_output_excerpt": None,
        "ran_at": "2026-08-28T00:00:00Z",
        "exit_code": 1 if failed else 0,
        "run_id": "RUN-framework-1",
        "execution_evidence": [{
            "run_id": "RUN-framework-1",
            "source_digest": automation["artifacts"]["source"]["source_digest"],
            "file_id": "FILE-products",
            "symbol_id": "SYMBOL-products",
            "file_digest": automation["artifacts"]["generated_files"][0]["content_digest"],
            "status": "FAILED" if failed else "PASSED",
        }],
        "process_evidence": [],
        "evidence_authoritative": True,
        "diagnostics": [],
    }
    assert schema_diagnostics(report, Path("schemas/run-tests-output.schema.json"), Path.cwd()) == []
    durable["trace_inputs"] = {
        "automation_artifact": automation,
        "autotest_review": review,
        "canonical_document": document,
    }
    return run_root, attempt_id, report, request, durable


def test_execution_receipt_is_attempt_owned_digest_bound_and_read_back(tmp_path: Path) -> None:
    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    started = claim_execution_start(run_root, attempt_id, request_digest(request))

    published = publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": report})
    append_event(run_root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    append_event(run_root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id, artifact_digest=published["digest"])
    readback = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")

    assert started["artifact_digest"] == report["execution"]["request_digest"]
    assert readback["record"]["payload"] == report
    assert readback["record"]["baseline_digest"] == report["execution"]["baseline_digest"]
    assert readback["record"]["generated_delta_digest"] == report["execution"]["generated_delta_digest"]
    assert readback["record"]["execution_request_digest"] == report["execution"]["request_digest"]

    generated = next(
        row for row in report["execution"]["artifact_evidence"]
        if row["kind"] == "generated_test"
    )
    (run_root / generated["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="execution receipt"):
        read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")


def test_execution_receipt_rejects_tampered_request_and_conflicting_republication(tmp_path: Path) -> None:
    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    claim_execution_start(run_root, attempt_id, request_digest(request))

    tampered = deepcopy(report)
    tampered["execution"]["timeout_seconds"] = 601
    with pytest.raises(ValueError, match="execution receipt"):
        publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": tampered})
    wrong_delta = deepcopy(report)
    wrong_delta["execution"]["generated_delta_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="execution receipt"):
        publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": wrong_delta})

    first = publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": report})
    conflicting = deepcopy(report)
    conflicting["ran_at"] = "2026-08-28T00:00:01Z"
    with pytest.raises(ValueError):
        publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": conflicting})
    assert first["record"]["payload"] == report


def test_prestart_not_runnable_receipt_requires_zero_execution_start_events(tmp_path: Path) -> None:
    run_root, attempt_id, executed, _request, durable = _execution_facts(tmp_path)
    report = deepcopy(executed)
    report.update({
        "verdict": "NOT_RUNNABLE",
        "target": {"language": "python", "framework": "pytest", "runner": "not_applicable", "command": None},
        "environment": {
            "status": "partial", "interpreter": None, "interpreter_path": None,
            "working_dir": str((tmp_path / "project").resolve()), "missing": ["runner_precondition"],
            "safe_key_labels": [],
        },
        "execution": {
            "adapter_id": None, "executable_path": None, "cwd": None,
            "run_id": None, "attempt_id": None, "baseline_digest": None,
            "generated_delta_digest": None, "build_profile": None, "typed_parameters": {},
            "argv": [], "selectors": [], "timeout_seconds": None, "report_paths": [],
            "request_digest": None, "report_evidence": [], "artifact_evidence": [],
        },
        "stats": None,
        "exit_code": None,
        "run_id": None,
        "execution_evidence": [],
        "process_evidence": [],
        "evidence_authoritative": False,
        "diagnostics": [{"path": "/execution", "code": "RUNNER_PRESTART", "message": "Execution cannot start."}],
    })
    assert schema_diagnostics(report, Path("schemas/run-tests-output.schema.json"), Path.cwd()) == []

    published = publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": report})

    assert published["record"]["execution_request_digest"] is None
    assert published["record"]["generated_delta_digest"] == durable["delta"]["digest"]
    assert not any(row["event_type"] == "EXECUTION_STARTED" for row in derive_state(run_root)["events"])


def test_prestart_receipt_is_rejected_after_execution_was_claimed(tmp_path: Path) -> None:
    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    claim_execution_start(run_root, attempt_id, request_digest(request))
    report["verdict"] = "NOT_RUNNABLE"
    report["run_id"] = None
    report["stats"] = None
    report["exit_code"] = None
    report["execution_evidence"] = []
    report["process_evidence"] = []
    report["evidence_authoritative"] = False
    report["diagnostics"] = [{"path": "/execution", "code": "RUNNER_PRESTART", "message": "Execution cannot start."}]

    with pytest.raises(ValueError, match="execution receipt"):
        publish_attempt_receipt(run_root, attempt_id, "execution-receipt", {"payload": report})


def test_runner_output_writer_and_helper_share_one_canonical_utf8_representation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.pilot_state as pilot_state
    from tools.run_tests import ProcessOutcome, _durable_execution_artifacts

    canonicalize = getattr(pilot_state, "_canonical_runner_output", None)
    assert callable(canonicalize)
    source = (
        "safe café\r\n"
        "  ghp_exampleplaceholder\r"
        "prefix ghp_exampleplaceholder\n"
        "password = example-value\r"
        "DATABASE_URL=postgres://sample:sample@host/db\r\n"
    )
    expected = (
        "safe café\n"
        "<redacted>\n"
        "<redacted>\n"
        "<redacted>\n"
        "<redacted>\n"
    ).encode("utf-8")

    assert canonicalize(source) == expected
    assert canonicalize(expected.decode("utf-8")) == expected

    limited = canonicalize("safe unicode\n" + "🙂" * 20000)
    assert 60_000 < len(limited) <= 64 * 1024  # review decision 16: the published tail is 64 KiB
    limited.decode("utf-8")
    assert canonicalize(limited.decode("utf-8")) == limited

    captured: dict[str, bytes] = {}

    def publish(_run_root, _attempt_id, name, data):
        captured[name] = bytes(data)
        return {
            "path": "artifacts/" + "a" * 32 + "/" + name,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }

    monkeypatch.setattr("tools.pilot_state.publish_run_artifact_bytes", publish)
    _durable_execution_artifacts(
        {},
        tmp_path,
        {"delta": {"files": []}},
        ProcessOutcome(1, source, ""),
        tmp_path / "run",
        "a" * 32,
    )

    assert captured["runner-output.txt"] == expected + b"\n"
    assert canonicalize(captured["runner-output.txt"].decode("utf-8")) == captured["runner-output.txt"]


def test_durable_native_report_keeps_junit_semantics_without_output_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.run_tests import ProcessOutcome, _durable_execution_artifacts

    source = (Path(__file__).parent / "fixtures" / "reports" / "junit-with-output.xml").read_bytes()
    source_path = tmp_path / "test-results" / "pytest.xml"
    captured: dict[str, bytes] = {}

    def publish(_run_root, _attempt_id, name, data):
        captured[name] = bytes(data)
        return {
            "path": "artifacts/" + "a" * 32 + "/" + name,
            "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
        }

    monkeypatch.setattr("tools.pilot_state.publish_run_artifact_bytes", publish)
    reports, artifacts = _durable_execution_artifacts(
        {source_path: source},
        tmp_path,
        {"delta": {"files": []}},
        ProcessOutcome(1, "", ""),
        tmp_path / "run",
        "a" * 32,
    )

    durable = captured["reports/pytest.xml"]
    assert b"SYSTEM_OUT_MARKER" not in durable
    assert b"SYSTEM_ERR_MARKER" not in durable
    assert b"FAILURE_PAYLOAD_MARKER" not in durable
    assert b"<failure" in durable
    assert b'name="test_one"' in durable
    assert reports == [{
        "path": "test-results/pytest.xml",
        "digest": "sha256:" + hashlib.sha256(source).hexdigest(),
    }]
    native = next(row for row in artifacts if row["kind"] == "native_report")
    assert native["source_digest"] == reports[0]["digest"]
    assert native["normalization"] == "junit-semantic-v1"
    assert source == (Path(__file__).parent / "fixtures" / "reports" / "junit-with-output.xml").read_bytes()


def test_durable_native_report_limit_fails_closed_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools.run_tests import ProcessOutcome, _durable_execution_artifacts

    testcase = b'<testcase classname="tests.test_sample" name="test_one"/>'
    source = b'<testsuite tests="20000">' + testcase * 20000 + b"</testsuite>"
    published: list[str] = []

    def publish(_run_root, _attempt_id, name, _data):
        published.append(name)
        return {"path": name, "digest": "sha256:" + "a" * 64}

    monkeypatch.setattr("tools.pilot_state.publish_run_artifact_bytes", publish)
    with pytest.raises(ValueError, match="RUNNER_NATIVE_REPORT_LIMIT"):
        _durable_execution_artifacts(
            {tmp_path / "test-results" / "pytest.xml": source},
            tmp_path,
            {"delta": {"files": []}},
            ProcessOutcome(0, "", ""),
            tmp_path / "run",
            "a" * 32,
        )

    assert published == []

    from tools import run_tests
    oversized = tmp_path / "oversized.xml"
    oversized.write_bytes(b"x" * 33)
    monkeypatch.setattr(run_tests, "NATIVE_REPORT_MAX_BYTES", 32)
    snapshots = run_tests._report_snapshots([oversized])
    assert snapshots == {oversized.resolve(): b""}
    with pytest.raises(ValueError, match="RUNNER_NATIVE_REPORT_LIMIT"):
        run_tests._normalize_durable_junit_report(oversized.read_bytes())


def test_execution_receipt_loader_rejects_noncanonical_runner_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.pilot_state as pilot_state

    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path)
    runner_output = publish_run_artifact_bytes(
        run_root, attempt_id, "runner-output.txt", b"safe output\r\n",
    )
    report["execution"]["artifact_evidence"].append({
        "kind": "runner_output",
        "path": runner_output["path"],
        "digest": runner_output["digest"],
    })
    claim_execution_start(run_root, attempt_id, request_digest(request))

    validator = pilot_state._validate_durable_execution_artifacts
    monkeypatch.setattr(pilot_state, "_validate_durable_execution_artifacts", lambda *_args: None)
    published = publish_attempt_receipt(
        run_root, attempt_id, "execution-receipt", {"payload": report},
    )
    append_event(
        run_root, "ARTIFACT_PUBLISHED", actor="controller", attempt_id=attempt_id,
        artifact_digest=published["digest"],
    )
    append_event(
        run_root, "ARTIFACT_READ_BACK", actor="controller", attempt_id=attempt_id,
        artifact_digest=published["digest"],
    )
    monkeypatch.setattr(pilot_state, "_validate_durable_execution_artifacts", validator)

    with pytest.raises(ValueError, match="execution receipt"):
        read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")
