from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
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
    from tools.run_pipeline import run_phase_one_spine

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
    wait_for_input: bool = False,
) -> tuple[dict, dict, dict, dict, Path, str, dict, dict]:
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
    static_review = review(document, artifact, boundary, "ПРИНЯТО", session_id="static-session", run_root=run_root, attempt_id=attempt_id)
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


def test_v5_runner_uses_project_pyproject_plugin_conftest_and_exact_reviewed_nodeid(tmp_path: Path) -> None:
    from tools.execution_adapters import request_digest
    from tools.pilot_state import claim_execution_start
    from tools.run_tests import run_tests_v5

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

    result = run_tests_v5(
        request, authorization,
        document, artifact, static_review, host_isolation_receipt=evidence, generated_delta_receipt=delta, run_root=run_root, attempt_id=attempt_id,
        on_execution_start=lambda request_value: claim_execution_start(run_root, attempt_id, request_digest(request_value)),
    )

    assert result["schema_version"] == "5.0.0"
    assert result["verdict"] == "PASS"
    assert [(row["file_id"], row["symbol_id"]) for row in result["execution_evidence"]] == [("FILE-products", "SYMBOL-products")]


@pytest.mark.parametrize("marker", ["sk-AUDIT_ONLY_FAKE_0123456789abcdef", "glpat-FAKE_AUDIT_ONLY_0123456789"])
def test_sensitive_junit_identity_finalizes_unknown_without_publishing_value(tmp_path: Path, monkeypatch, marker: str) -> None:
    from tests.test_durable_execution_trace import _publish_execution
    from tools.execution_adapters import request_digest
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import claim_execution_start
    from tools.run_tests import run_tests_v5

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
        project, generated_content=content,
    )
    request = _request(project, document, artifact)
    report = run_tests_v5(
        request, authorization, document, artifact, review,
        host_isolation_receipt=host, generated_delta_receipt=delta,
        run_root=root, attempt_id=attempt_id,
        on_execution_start=lambda value: claim_execution_start(root, attempt_id, request_digest(value)),
    )
    assert report["verdict"] == "UNKNOWN"
    assert report["process_evidence"][0]["kind"] == "JUNIT_INVALID"
    _publish_execution(root, attempt_id, report)
    finalized = finalize_durable_execution_attempt(root, attempt_id)
    assert finalized["result"]["attempt_state"] == "TERMINAL"
    assert finalized["result"]["verification"] == "UNKNOWN"
    assert finalized["result"]["reason_code"] == "EXECUTION_UNKNOWN"
    assert finalized["result"]["accepted"] is False
    assert (project / "tests/test_generated.py").read_bytes() == content.encode()
    assert not any(marker.encode() in path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_pytest_zero_collection_is_tests_deselected_and_keeps_the_generated_test(
    tmp_path: Path,
) -> None:
    """Review decision 15: an explicit node ID that collects nothing is NOT_RUNNABLE, never FAIL."""
    from tests.test_durable_execution_trace import _publish_execution
    from tools.execution_adapters import request_digest
    from tools.finalize_attempt import finalize_durable_execution_attempt
    from tools.pilot_state import (
        claim_execution_start, read_attempt_receipt, read_effective_canonical,
    )
    from tools.run_tests import run_tests_v5

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    with (project / "conftest.py").open("a", encoding="utf-8") as stream:
        stream.write("\n\ndef pytest_collection_modifyitems(items):\n    items.clear()\n")
    _module_python(project)
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    request = _request(project, document, artifact)
    generated = project / "tests" / "test_generated.py"
    generated_bytes = generated.read_bytes()

    report = run_tests_v5(
        request, authorization, document, artifact, static_review,
        host_isolation_receipt=evidence,
        generated_delta_receipt=delta,
        run_root=run_root,
        attempt_id=attempt_id,
        on_execution_start=lambda value: claim_execution_start(
            run_root, attempt_id, request_digest(value),
        ),
    )

    retained_report = next(
        row for row in report["execution"]["artifact_evidence"]
        if row["kind"] == "native_report"
    )
    assert report["verdict"] == "NOT_RUNNABLE", (run_root / retained_report["path"]).read_text(encoding="utf-8")
    assert report["evidence_authoritative"] is False
    assert report["stats"]["total"] == 0
    assert [row["kind"] for row in report["process_evidence"]] == ["TESTS_DESELECTED"]
    artifacts = report["execution"]["artifact_evidence"]
    assert {row["kind"] for row in artifacts} == {
        "native_report", "generated_test", "runner_output",
    }
    for row in artifacts:
        assert (run_root / row["path"]).is_file()
    retained_generated = next(row for row in artifacts if row["kind"] == "generated_test")
    assert retained_generated["source_path"] == "tests/test_generated.py"
    assert (run_root / retained_generated["path"]).read_bytes() == generated_bytes
    output = (run_root / next(row for row in artifacts if row["kind"] == "runner_output")["path"]).read_bytes()
    assert 0 < len(output) <= 64 * 1024
    assert read_effective_canonical(run_root, attempt_id)["document"] == document

    _publish_execution(run_root, attempt_id, report)
    durable = read_attempt_receipt(
        run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK",
    )["record"]["payload"]
    assert durable["execution"]["artifact_evidence"] == artifacts
    finalized = finalize_durable_execution_attempt(run_root, attempt_id)

    assert finalized["exit_code"] == 2, json.dumps(finalized, indent=2)
    assert finalized["result"]["verification"] == "NOT_RUNNABLE"
    assert finalized["result"]["reason_code"] == "TESTS_DESELECTED"
    # The test is valid and only deselected by project configuration: it is not cleaned like a FAIL.
    assert generated.read_bytes() == generated_bytes
    assert (run_root / retained_generated["path"]).read_bytes() == generated_bytes


def test_controller_exec_keeps_nested_module_runtime_cwd_and_selector_without_root_fallback(tmp_path: Path, capsys) -> None:
    """A parent-owned run executes only its selected nested module, never project root."""
    from tools.pilot_state import derive_state, read_attempt_receipt, read_terminal_result
    from tools.run_pipeline import cmd_exec

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
    inputs = run_root / "inputs"
    inputs.mkdir()
    paths = {
        "canonical_document": inputs / "canonical.json", "automation_artifact": inputs / "automation.json",
        "autotest_review": inputs / "review.json", "authorization_receipt": inputs / "authorization.json",
        "host_isolation_receipt": inputs / "isolation.json", "generated_delta_receipt": inputs / "delta.json",
    }
    for key, value in {
        "canonical_document": document, "automation_artifact": artifact, "autotest_review": static_review,
        "authorization_receipt": authorization, "host_isolation_receipt": evidence, "generated_delta_receipt": delta,
    }.items():
        paths[key].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    code = cmd_exec(SimpleNamespace(
        project=str(project), run=run_root.name,
        module="python-pytest", language=None, target=None, executor="local",
        **{key: str(value) for key, value in paths.items()},
    ))

    output = json.loads(capsys.readouterr().out)
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
    from tools.run_tests import run_tests_v5

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    _module_python(project)
    document, artifact, static_review, evidence, run_root, attempt_id, _authorization, delta = _reviewed_inputs(project)
    monkeypatch.setattr("tools.run_tests.run_subprocess", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("project code must not start")))

    denied = run_tests_v5(
        _request(project, document, artifact), _sealed_authorization(profile="cases-only-v1", execution_requested=False),
        document, artifact, static_review, host_isolation_receipt=evidence, generated_delta_receipt=delta, run_root=run_root, attempt_id=attempt_id,
    )
    assert denied["verdict"] == "NOT_RUNNABLE"
    assert denied["run_id"] is None
    assert any(row["code"] == "RUNNER_AUTHORIZATION" for row in denied["diagnostics"])

    stolen = run_tests_v5(
        _request(project, document, artifact), _sealed_authorization(profile="local-pilot-v1", execution_requested=True),
        document, artifact, static_review, host_isolation_receipt=evidence, generated_delta_receipt=delta, run_root=run_root, attempt_id=attempt_id,
    )
    assert stolen["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_AUTHORIZATION" for row in stolen["diagnostics"])

    missing_delta = run_tests_v5(
        _request(project, document, artifact), _authorization, document, artifact, static_review,
        host_isolation_receipt=evidence, run_root=run_root, attempt_id=attempt_id,
    )
    assert missing_delta["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_GENERATED_DELTA" for row in missing_delta["diagnostics"])


def test_caller_supplied_host_interpreter_cannot_bypass_module_local_request(tmp_path: Path, monkeypatch) -> None:
    """Changing only request.executable must block before any project process starts."""
    from tools.execution_adapters import ExecutionRequest, PYTEST
    from tools.run_tests import run_tests_v5

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
    monkeypatch.setattr("tools.run_tests.run_subprocess", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("forged request started project code")))

    result = run_tests_v5(
        forged, authorization, document, artifact, static_review,
        host_isolation_receipt=evidence, generated_delta_receipt=delta,
        run_root=run_root, attempt_id=attempt_id,
    )

    assert result["verdict"] == "NOT_RUNNABLE"
    assert any(row["code"] == "RUNNER_REQUEST_PROVENANCE" for row in result["diagnostics"])


def test_cli_builds_closed_adapter_request_and_never_calls_legacy_runner(tmp_path: Path, monkeypatch) -> None:
    """The public runner must build the selected module's V5 request itself."""
    from tools import run_tests

    fixture = Path(__file__).parent / "fixtures" / "projects" / "python-pytest"
    project = tmp_path / "project"
    shutil.copytree(fixture, project)
    local_runtime = project / MODULE_PYTHON
    local_runtime.parent.mkdir(parents=True)
    local_runtime.write_text("closed runtime placeholder", encoding="utf-8")
    local_runtime.chmod(0o755)
    (project / ".skillsrc").write_text(
        json.dumps({
            "schema_version": "5.0.0", "version": "3.0",
            "project": {"name": "fixture"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [{
                "id": "pytest-module", "root": ".",
                "stack": {"language": "python", "framework": "pytest"},
                "detected_from": ["pyproject.toml"],
                "paths": {"source": ["src"], "tests": ["tests"]},
                "test": {
                    "framework": "pytest", "adapter_id": "pytest:selected-symbols-v1",
                    "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {},
                },
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    document, artifact, static_review, evidence, run_root, attempt_id, authorization, delta = _reviewed_inputs(project)
    canonical_path, automation_path = project / "canonical.json", project / "automation.json"
    review_path, authorization_path, isolation_path, delta_path = project / "review.json", project / "authorization.json", project / "isolation.json", project / "delta.json"
    for path, value in ((canonical_path, document), (automation_path, artifact), (review_path, static_review), (authorization_path, authorization), (isolation_path, evidence), (delta_path, delta)):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    captured = {}

    def closed_runner(request, authorization, *_args, **kwargs):
        captured.update(request=request, authorization=authorization, isolation=kwargs["host_isolation_receipt"], delta=kwargs["generated_delta_receipt"])
        return run_tests._report(
            "NOT_RUNNABLE", project, "python", artifact["artifacts"]["source"],
            "sha256:" + "a" * 64, "sha256:" + "b" * 64,
            diagnostics=[{"path": "/request", "code": "TEST", "message": "test seam"}],
        )

    monkeypatch.setattr(run_tests, "run_tests_v5", closed_runner)
    monkeypatch.setattr(run_tests, "run_tests_v3", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy runner must not be called")))

    code = run_tests.main([
        "--project", str(project), "--module", "pytest-module",
        "--canonical-document", str(canonical_path), "--automation-artifact", str(automation_path),
        "--autotest-review", str(review_path), "--authorization-receipt", str(authorization_path),
        "--host-isolation-receipt", str(isolation_path), "--generated-delta-receipt", str(delta_path), "--run-root", str(run_root), "--attempt-id", attempt_id,
    ])

    assert code == 2
    assert captured["request"].adapter_id == "pytest:selected-symbols-v1"
    assert captured["request"].selectors == ("tests/test_generated.py::test_selected",)
    assert captured["authorization"]["policy_profile"] == "local-pilot-v1"
    assert captured["delta"] == delta
