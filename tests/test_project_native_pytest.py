from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import venv
import pytest

from tests.test_automation_revision_budget import automated_document, automation, durable_boundary, host_evidence, review
from tests.test_generated_delta import _effective_bundle_digest
from tests.helpers import MODULE_PYTHON


def test_inline_runner_review_precedes_materialization(tmp_path: Path) -> None:
    from tools.run_tests import validate_artifact_runner_compatibility

    document = automated_document()
    artifact = automation(document)
    check = lambda **options: validate_artifact_runner_compatibility(tmp_path, "python", document, artifact, **options)
    assert check(materialized=False).status == "READY"
    assert not list(tmp_path.iterdir())
    assert check().status == "NOT_RUNNABLE"

    locator = artifact["artifacts"]["generated_symbols"][0]["locator"]
    locator["function_name"] = "test_missing"
    assert any(row["code"] == "RUNNER_LOCATOR" for row in check(materialized=False).diagnostics)
    locator["function_name"] = "test_products"
    row = artifact["artifacts"]["generated_files"][0]
    target = tmp_path / row["path"]
    target.parent.mkdir()
    target.write_bytes(row["content"].encode("utf-8"))
    assert check().status == "READY"
    target.write_bytes(b"def test_products():\n    assert False\n")
    assert any(row["code"] == "RUNNER_FILE_DIGEST" for row in check().diagnostics)


def test_inline_java_review_resolves_only_direct_class_methods(tmp_path: Path) -> None:
    from tools.run_tests import validate_artifact_runner_compatibility

    document = automated_document()
    artifact = automation(document)
    content = "package sample;\nclass ProductsTest { void products() {} class Nested { void hidden() {} } }\n"
    artifact["artifacts"]["generated_files"][0].update(
        path="src/test/java/sample/ProductsTest.java", language="java", framework="junit5",
        content=content, content_digest="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
    )
    locator = {"kind": "java_class_method", "class_fqn": "sample.ProductsTest", "method_name": "products"}
    artifact["artifacts"]["generated_symbols"][0]["locator"] = locator
    assert validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False).status == "READY"
    locator["method_name"] = "hidden"
    result = validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False)
    assert any(row["code"] == "RUNNER_LOCATOR" for row in result.diagnostics)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("annotation", [
    '@DisplayName("Owner, pet and visit lifecycle")',
    "@org.junit.jupiter.api.DisplayName(\"x\")",
    "@DisplayNameGeneration(DisplayNameGenerator.ReplaceUnderscores.class)",
    "@IndicativeSentencesGeneration(separator = \" -> \")",
])
def test_inline_java_review_rejects_class_display_name(tmp_path: Path, annotation: str) -> None:
    """FaT-DN: a class display name becomes the Surefire classname — the report cannot match the class FQN."""
    from tools.run_tests import validate_artifact_runner_compatibility

    document = automated_document()
    artifact = automation(document)
    content = (
        "package sample;\nimport org.junit.jupiter.api.*;\n"
        f"@Tag(\"x\")\n{annotation}\nclass ProductsTest {{\n"
        "  @Test\n  @DisplayName(\"TC-1 method name is fine\")\n  void products() {}\n}\n"
    )
    artifact["artifacts"]["generated_files"][0].update(
        path="src/test/java/sample/ProductsTest.java", language="java", framework="junit5",
        content=content, content_digest="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
    )
    artifact["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "java_class_method", "class_fqn": "sample.ProductsTest", "method_name": "products"}
    result = validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False)
    assert result.status == "NOT_RUNNABLE"
    rows = [row for row in result.diagnostics if row["code"] == "RUNNER_CLASS_DISPLAY_NAME"]
    assert len(rows) == 1 and rows[0]["path"] == "/artifacts/generated_files/0"
    assert "убери" in rows[0]["message"] and "у методов можно" in rows[0]["message"]
    assert "Owner, pet" not in rows[0]["message"]


def test_inline_java_review_keeps_method_display_name_and_commented_class_annotation(tmp_path: Path) -> None:
    from tools.run_tests import validate_artifact_runner_compatibility

    document = automated_document()
    artifact = automation(document)
    content = (
        "package sample;\nimport org.junit.jupiter.api.*;\n"
        "// @DisplayName(\"commented out\")\n/** {@code @DisplayName} is not used here */\n"
        "@Tag(\"@DisplayName(\\\"in a string\\\")\")\nclass ProductsTest {\n"
        "  @Test\n  @DisplayName(\"TC-1 method\")\n  void products() {}\n"
        "  @Nested\n  @DisplayName(\"nested\")\n  class Inner { @Test void hidden() {} }\n}\n"
    )
    artifact["artifacts"]["generated_files"][0].update(
        path="src/test/java/sample/ProductsTest.java", language="java", framework="junit5",
        content=content, content_digest="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
    )
    artifact["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "java_class_method", "class_fqn": "sample.ProductsTest", "method_name": "products"}
    assert validate_artifact_runner_compatibility(tmp_path, "java", document, artifact, materialized=False).status == "READY"


def _sealed_authorization(*, profile: str, execution_requested: bool) -> dict:
    body = {
        "schema_version": "1.0.0", "run_id": "a" * 32, "request_id": "request-1",
        "policy_profile": profile, "execution_requested": execution_requested,
    }
    body["digest"] = "sha256:" + hashlib.sha256(
        (json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    ).hexdigest()
    return body


def _local_attempt(project: Path, *, module: str = ".") -> tuple[Path, str, dict, dict]:
    from tests.helpers import build_phase_two_baseline
    from tools.pilot_state import read_run
    from tests.spine_helpers import run_phase_one_spine

    # The copied fixture names a Windows interpreter; bind the host path before freezing.
    skillsrc = project / ".skillsrc"
    if skillsrc.is_file():
        config = json.loads(skillsrc.read_text(encoding="utf-8"))
        for entry in config["modules"]:
            if entry["root"] == module:
                entry["test"]["interpreter"] = MODULE_PYTHON
        skillsrc.write_text(json.dumps(config, ensure_ascii=False) + "\n", encoding="utf-8")
    identity = {"project": str(project.resolve()), "module": module, "policy_profile": "local-pilot-v1"}
    baseline = build_phase_two_baseline(project, identity, skill_pack_root=project / ".pilot-runs")
    result = run_phase_one_spine(
        project, "local-pilot-v1", {"request_id": "local-execution", "execution_requested": True}, identity, baseline, {}
    )
    run_root = Path(result["run"]["run_root"])
    return run_root, result["state"]["attempts"][0]["attempt_id"], dict(read_run(run_root)["authorization"]), baseline


def _reviewed_inputs(
    project: Path, *, module: str = ".", generated_content: str | None = None,
    wait_for_input: bool = False, materialize: bool = True, publish_static_review: bool = True, static_verdict: str = "ПРИНЯТО",
) -> tuple[dict, dict, dict, dict, Path, str, dict, dict | None]:
    document = automated_document()
    artifact = automation(document, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    generated_content = generated_content or """import pytest

from sample import combine


@pytest.mark.native
def test_selected(plugin_value: str, conftest_value: str) -> None:
    assert combine(plugin_value, conftest_value) == \"from-project-plugin:from-conftest\"
"""
    artifact["artifacts"]["generated_files"][0].update({
        "path": "tests/test_generated.py",
        "content": generated_content,
        "content_digest": "sha256:" + hashlib.sha256(generated_content.encode("utf-8")).hexdigest(),
    })
    artifact["artifacts"]["generated_symbols"][0]["locator"] = {
        "kind": "python_module_function", "function_name": "test_selected",
    }
    module_root = project if module == "." else project.joinpath(*module.split("/"))
    run_root, attempt_id, authorization, baseline = _local_attempt(project, module=module)
    if wait_for_input:
        from tools.pilot_state import append_event, read_attempt_receipt

        waiting = read_attempt_receipt(
            run_root, attempt_id, "structured-result", "WAITING_FOR_MODEL",
        )
        append_event(
            run_root, "WAITING_FOR_INPUT", actor="controller", attempt_id=attempt_id,
            artifact_digest=waiting["digest"],
        )
    suffix = attempt_id[:12]
    evidence = host_evidence(
        "static-session",
        generator=f"automation-generator-{suffix}",
        reviewer=f"automation-reviewer-{suffix}",
    )
    _run_root, _attempt_id, boundary = durable_boundary(
        project, artifact, "static-session", evidence,
        run_root=run_root, attempt_id=attempt_id, document=document,
        generator=evidence["generator_invocation_id"], reviewer=evidence["reviewer_invocation_id"],
    )
    static_review = review(document, artifact, boundary, static_verdict, session_id="static-session")
    if static_verdict == "ТРЕБУЕТ ДОРАБОТКИ":  # a rework verdict carries a blocking finding and no corrections
        body = static_review["artifacts"]["autotest_review"]
        body["corrections"] = []
        body["findings"] = [{"severity": "BLOCKING", "code": "WEAK_ORACLE", "message": "Проверка не доказывает результат.",
                             "evidence": ["relation"], "related_ids": [artifact["artifacts"]["generated_files"][0]["file_id"]]}]
    from tools.automation_validation import automation_sha256
    from tools.pilot_state import append_event, publish_model_request, publish_model_stage_artifact, read_attempt_receipt

    if publish_static_review:
        reviewer_stage = "autotest-reviewer:r1"
        append_event(
            run_root, "REVIEW_REQUESTED", actor="controller", attempt_id=attempt_id,
            stage_instance_id=reviewer_stage, artifact_digest=automation_sha256(artifact),
        )
        boundary_receipt = read_attempt_receipt(
            run_root, attempt_id, "automation-review-boundary-r1", "ARTIFACT_READ_BACK",
        )
        publish_model_request(
            run_root, attempt_id, reviewer_stage,
            model_id="model-automation-reviewer",
            invocation_id=evidence["reviewer_invocation_id"],
            input_digests=[automation_sha256(artifact), boundary_receipt["digest"]],
        )
        publish_model_stage_artifact(run_root, attempt_id, reviewer_stage, static_review)
    if not materialize:  # FaT-E: the agent stops after the static review; exec prepares its inputs itself
        return document, artifact, static_review, boundary, run_root, attempt_id, authorization, None
    from tools.generated_delta import materialize_delta
    from tools.pilot_state import read_attempt_receipt

    materialize_delta(
        project, module_root, baseline, artifact, static_review,
        canonical_document=document, run_root=run_root, attempt_id=attempt_id,
    )
    durable_delta = read_attempt_receipt(run_root, attempt_id, "generated-delta", "ARTIFACT_READ_BACK")
    return document, artifact, static_review, boundary, run_root, attempt_id, authorization, dict(durable_delta["record"])


def _module_python(project: Path) -> Path:
    """Create a genuine module-local venv without installing test dependencies.

    The `.pth` exposes this test suite's already-installed pytest to the local
    interpreter. POSIX uses the same symlink layout as `python -m venv`.
    """
    environment = project / ".venv"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
    local_python = environment / "Scripts" / "python.exe"
    if os.name != "nt":
        local_python = environment / "bin" / "python"
    site_packages = next(path for path in sys.path if path.endswith("site-packages"))
    target = environment / ("Lib/site-packages" if (environment / "Lib").is_dir() else "lib")
    if target.name == "lib":
        target = next((environment / "lib").glob("python*/site-packages"))
    target.mkdir(parents=True, exist_ok=True)
    (target / "fixture-test-dependencies.pth").write_text(site_packages + "\n", encoding="utf-8")
    return local_python


def _request(project: Path, document: dict, artifact: dict):
    from tools.run_tests import build_closed_execution_request, resolve_execution_context

    execution_root, language, module = resolve_execution_context(project, project / ".skillsrc", "python-pytest", None)
    request, _ = build_closed_execution_request(execution_root, language, module, document, artifact)
    return request


def _the_run_of_a_person(request) -> dict[Path, bytes]:
    """What a person does with the printed command: run the closed request as it stands and bring the reports it wrote (the test may start a
    process; the package may not)."""
    import subprocess

    cwd = Path(request.cwd)
    subprocess.run(list(request.argv), cwd=cwd, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return {cwd / name: (cwd / name).read_bytes() for name in request.report_paths if (cwd / name).is_file()}


def _ingest(request, authorization, document, artifact, review, *, host, delta, run_root, attempt_id, snapshots, claim: bool = True):
    """``ingest_reports`` for the closed request, the reports given: what ``run_tests_v5`` returned after the run of the request."""
    from tools.execution_adapters import request_digest
    from tools.exec_runner import ingest_reports
    from tools.pilot_state import claim_execution_start

    return ingest_reports(
        request, authorization, document, artifact, review, host_isolation_receipt=host, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id, snapshots=snapshots,
        execution_source={"run_by": "human", "human_command_digest": "sha256:" + "0" * 64, "challenge": "not_available", "statement": "reports"},
        run_id="RUN-fixture", ran_at="2026-11-05T10:00:00+00:00", duration_sec=0.0,
        on_execution_start=lambda value: claim_execution_start(run_root, attempt_id, request_digest(value)) if claim else None,
    )


def test_the_reports_of_a_run_of_the_closed_request_use_project_pyproject_plugin_conftest_and_the_exact_reviewed_nodeid(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    _module_python(project)
    content = '''import pytest
from sample import combine

@pytest.mark.native
@pytest.mark.parametrize("value", [1, 2])
def test_selected(value, plugin_value, conftest_value):
    assert value > 0
    assert combine(plugin_value, conftest_value) == "from-project-plugin:from-conftest"
'''
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(
        project, generated_content=content,
    )

    request = _request(project, document, artifact)
    assert Path(request.executable) == project.resolve() / MODULE_PYTHON
    assert request.cwd == str(project.resolve())
    assert request.selectors == ("tests/test_generated.py::test_selected",)

    result = _ingest(request, authorization, document, artifact, static_review, host=evidence, delta=delta, run_root=run_root, attempt_id=attempt_id,
                     snapshots=_the_run_of_a_person(request))

    assert result["schema_version"] == "5.0.0"
    assert result["verdict"] == "PASS"
    assert [(row["file_id"], row["symbol_id"]) for row in result["execution_evidence"]] == [("FILE-products", "SYMBOL-products")]


@pytest.mark.parametrize("marker", ["sk-AUDIT_ONLY_FAKE_0123456789abcdef", "glpat-FAKE_AUDIT_ONLY_0123456789"])
def test_sensitive_junit_identity_finalizes_unknown_without_publishing_value(tmp_path: Path, monkeypatch, capsys, marker: str) -> None:
    """A report whose test identity carries a token is not published: the result is UNKNOWN (JUNIT_INVALID), the generated file is kept and
    nothing of the value reaches the run or the answer."""
    from tests.p4_flow import human_run, ingest, materialize
    from tools.pilot_state import read_attempt_receipt, read_terminal_result

    monkeypatch.setenv("AUDIT_CASE_VALUE", marker)
    project = tmp_path / "project"
    shutil.copytree(Path(__file__).parent / "fixtures/projects/python-pytest", project)
    _module_python(project)
    content = '''import os
import pytest
@pytest.mark.parametrize("value", [os.environ["AUDIT_CASE_VALUE"]])
def test_selected(value):
    assert value
'''
    document, artifact, review, host, root, attempt_id, authorization, delta = _reviewed_inputs(
        project, generated_content=content, materialize=False,
    )
    printed = materialize(project, root, capsys)
    assert human_run(project, printed).returncode == 0
    code, output = ingest(project, root, capsys)
    report = read_attempt_receipt(root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    assert report["verdict"] == "UNKNOWN"
    assert report["process_evidence"][0]["kind"] == "JUNIT_INVALID"
    assert code == 2 and output["verdict"] == "UNKNOWN" and output["accepted"] is False, output
    result = read_terminal_result(root, attempt_id)
    assert result["attempt_state"] == "TERMINAL"
    assert result["verification"] == "UNKNOWN"
    assert result["reason_code"] == "EXECUTION_UNKNOWN"
    assert result["accepted"] is False
    assert (project / "tests/test_generated.py").read_bytes() == content.encode()
    assert marker not in json.dumps(output, ensure_ascii=False)
    assert not any(marker.encode() in path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_zero_collection_is_fail_and_cleanup_uses_bound_durable_execution_artifacts(
    tmp_path: Path, capsys,
) -> None:
    from tests.p4_flow import human_run, ingest, materialize
    from tools.pilot_state import read_attempt_receipt, read_effective_canonical

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    with (project / "conftest.py").open("a", encoding="utf-8") as stream:
        stream.write("\n\ndef pytest_collection_modifyitems(items):\n    items.clear()\n")
    _module_python(project)
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project, materialize=False)
    printed = materialize(project, run_root, capsys)
    generated = project / "tests" / "test_generated.py"
    generated_bytes = generated.read_bytes()
    human_run(project, printed)  # pytest exits 5: nothing was collected, the report is there
    code, output = ingest(project, run_root, capsys)

    report = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    retained_report = next(row for row in report["execution"]["artifact_evidence"] if row["kind"] == "native_report")
    assert report["verdict"] == "FAIL", (run_root / retained_report["path"]).read_text(encoding="utf-8")
    assert report["evidence_authoritative"] is True
    assert report["stats"]["total"] == 0
    assert [row["kind"] for row in report["process_evidence"]] == ["NO_TESTS_COLLECTED"]
    artifacts = report["execution"]["artifact_evidence"]
    assert {row["kind"] for row in artifacts} == {
        "native_report", "generated_test", "runner_output",
    }
    for row in artifacts:
        assert (run_root / row["path"]).is_file()
    retained_generated = next(row for row in artifacts if row["kind"] == "generated_test")
    assert retained_generated["source_path"] == "tests/test_generated.py"
    assert (run_root / retained_generated["path"]).read_bytes() == generated_bytes
    runner_output = (run_root / next(row for row in artifacts if row["kind"] == "runner_output")["path"]).read_bytes()
    assert len(runner_output) <= 2000  # nobody observed a process: the tail is empty (the human has the output)
    assert read_effective_canonical(run_root, attempt_id)["document"] == document

    assert code == 1, json.dumps(output, indent=2)
    assert output["verdict"] == "FAIL"
    assert output["finalization"] == "complete"
    from tools.pilot_state import read_terminal_result

    assert read_terminal_result(run_root, attempt_id)["reason_code"] == "NO_TESTS_COLLECTED"
    assert not generated.exists()
    assert (run_root / retained_generated["path"]).read_bytes() == generated_bytes


def test_the_printed_command_keeps_nested_module_runtime_cwd_and_selector_without_root_fallback(tmp_path: Path, capsys) -> None:
    """A parent-owned run is built only in its selected nested module, never project root: the command names that module, and the run of it is the result."""
    from tests.p4_flow import human_run, ingest, materialize
    from tools.pilot_state import derive_state, read_attempt_receipt, read_terminal_result

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project, module_root = tmp_path / "project", tmp_path / "project" / "modules" / "child"
    module_root.parent.mkdir(parents=True)
    shutil.copytree(fixture, module_root)
    (project / "tests").mkdir()
    (project / "tests" / "test_generated.py").write_text(
        "def test_selected():\n    assert False, 'root fallback executed'\n", encoding="utf-8"
    )
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "parent"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": "modules/child",
            "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(module_root)
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(
        project, module="modules/child"
    )
    printed = materialize(project, run_root, capsys)
    assert printed["working_directory"] == "modules/child" and "tests/test_generated.py::test_selected" in printed["command"], printed
    assert "root fallback executed" in (project / "tests" / "test_generated.py").read_text(encoding="utf-8")  # the root file is untouched and not the one named
    completed = human_run(project, printed)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    code, output = ingest(project, run_root, capsys)
    receipt = read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    dispositions = read_attempt_receipt(run_root, attempt_id, "disposition-receipt", "ARTIFACT_READ_BACK")["record"]["payload"]
    terminal = read_terminal_result(run_root, attempt_id)
    assert code == 0
    assert output["verdict"] == "PASS"
    assert output["finalization"] == "complete"
    assert output["accepted"] is True
    assert terminal["accepted"] is True and terminal["verification"] == "PASS"
    assert [row["disposition"] for row in dispositions["files"]] == ["RETAINED"]
    assert next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)["state"] == "TERMINAL"
    assert receipt["execution"]["cwd"] == str(module_root.resolve())
    assert receipt["environment"]["working_dir"] == str(module_root.resolve())
    assert Path(receipt["execution"]["executable_path"]) == module_root.resolve() / MODULE_PYTHON
    assert receipt["execution"]["selectors"] == ["tests/test_generated.py::test_selected"]


def test_cases_only_or_folder_presence_never_starts_project_native_process(tmp_path: Path, monkeypatch) -> None:
    import subprocess

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    _module_python(project)
    document, artifact, static_review, evidence, run_root, attempt_id, _authorization, delta = _reviewed_inputs(project)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("project code must not start")))
    request = _request(project, document, artifact)

    denied = _ingest(request, _sealed_authorization(profile="cases-only-v1", execution_requested=False), document, artifact, static_review,
                     host=evidence, delta=delta, run_root=run_root, attempt_id=attempt_id, snapshots={})
    assert denied["verdict"] == "NOT_RUNNABLE"
    assert denied["run_id"] is None
    assert any(row["code"] == "RUNNER_AUTHORIZATION" for row in denied["diagnostics"])

    stolen = _ingest(request, _sealed_authorization(profile="local-pilot-v1", execution_requested=True), document, artifact, static_review,
                     host=evidence, delta=delta, run_root=run_root, attempt_id=attempt_id, snapshots={})
    assert stolen["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_AUTHORIZATION" for row in stolen["diagnostics"])

    missing_delta = _ingest(request, _authorization, document, artifact, static_review,
                            host=evidence, delta=None, run_root=run_root, attempt_id=attempt_id, snapshots={})
    assert missing_delta["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_GENERATED_DELTA" for row in missing_delta["diagnostics"])


def test_caller_supplied_host_interpreter_cannot_bypass_module_local_request(tmp_path: Path, monkeypatch) -> None:
    """Changing only request.executable must block before the claim of the start: the request the reports are held against is the module's own."""
    import subprocess

    from tools.execution_adapters import ExecutionRequest, PYTEST
    from tools.pilot_state import derive_state

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    _module_python(project)
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    forged = ExecutionRequest(
        adapter_id=PYTEST,
        executable=sys.executable,
        argv=(sys.executable, "-m", "pytest", "--junitxml", "test-results/pytest.xml", "tests/test_generated.py::test_selected"),
        cwd=str(project),
        selectors=("tests/test_generated.py::test_selected",),
        timeout_seconds=30,
        report_paths=("test-results/pytest.xml",),
        environment_labels=("PROJECT_NATIVE_ENV",),
    )
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("forged request started project code")))

    result = _ingest(forged, authorization, document, artifact, static_review, host=evidence, delta=delta, run_root=run_root,
                     attempt_id=attempt_id, snapshots={})

    assert result["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_REQUEST_PROVENANCE" for row in result["diagnostics"])
    assert not [event for event in derive_state(run_root)["events"] if event["event_type"] == "EXECUTION_STARTED"]
