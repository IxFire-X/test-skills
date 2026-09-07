from pathlib import Path
import os

import pytest


def _module(tmp_path: Path, adapter_id: str, runtime: str) -> dict:
    (tmp_path / runtime).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / runtime).write_text("runtime", encoding="utf-8")
    if os.name != "nt":
        (tmp_path / runtime).chmod(0o755)
    return {
        "module_root": str(tmp_path),
        "test": {
            "adapter_id": adapter_id,
            ("interpreter" if adapter_id.startswith("pytest:") else "wrapper"): runtime,
            "build_profile": "default",
            "adapter_parameters": {},
        },
    }


@pytest.mark.parametrize(
    ("adapter_id", "runtime", "selectors", "expected"),
    [
        ("pytest:selected-symbols-v1", ".venv/python", ("tests/test_a.py::test_a",), ("-m", "pytest", "--junitxml")),
        ("maven-wrapper:selected-symbols-v1", "mvnw", ("pkg.SampleTest#works",), ("-Pdefault", "-Dtest=pkg.SampleTest#works", "test")),
        ("gradle-wrapper:selected-symbols-v1", "gradlew", ("pkg.SampleTest.works",), ("test", "-Pprofile=default", "--tests", "pkg.SampleTest.works")),
    ],
)
def test_closed_adapters_build_module_native_argv_only(tmp_path: Path, adapter_id: str, runtime: str, selectors: tuple[str, ...], expected: tuple[str, ...]):
    from tools.execution_adapters import build_request

    request = build_request(adapter_id, _module(tmp_path, adapter_id, runtime), [{"selector": value} for value in selectors])
    assert request.adapter_id == adapter_id
    assert request.cwd == str(tmp_path.resolve())
    assert request.executable == str((tmp_path / runtime).resolve())
    assert request.selectors == selectors
    assert request.argv[0] == request.executable
    assert all(token not in {"|", ">", "<", "&&", ";"} for token in request.argv)
    assert all(token in request.argv for token in expected)
    assert request.environment_labels == ("PROJECT_NATIVE_ENV",)


def test_adapter_rejects_unknown_runtime_escape_shell_fields_and_unreviewed_targets(tmp_path: Path):
    from tools.execution_adapters import AdapterRequestError, build_request

    module = _module(tmp_path, "pytest:selected-symbols-v1", ".venv/python")
    module["test"]["argv_template"] = "pytest {target}"
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_request("pytest:selected-symbols-v1", module, [{"selector": "tests/test_a.py::test_a"}])
    module = _module(tmp_path, "pytest:selected-symbols-v1", ".venv/python")
    module["test"]["interpreter"] = "../python"
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_request("pytest:selected-symbols-v1", module, [{"selector": "tests/test_a.py::test_a"}])
    with pytest.raises(AdapterRequestError, match="NOT_RUNNABLE"):
        build_request("unknown", _module(tmp_path, "pytest:selected-symbols-v1", ".venv/python"), [{"not_selector": "x"}])


def test_prestart_runtime_or_configuration_failure_is_not_runnable(tmp_path: Path):
    from tools.execution_adapters import build_request, classify_prestart

    missing = {"module_root": str(tmp_path), "test": {"adapter_id": "pytest:selected-symbols-v1", "interpreter": "missing/python", "build_profile": "default", "adapter_parameters": {}}}
    assert classify_prestart(missing) == "NOT_RUNNABLE"
    with pytest.raises(Exception):
        build_request("pytest:selected-symbols-v1", missing, [{"selector": "tests/test_a.py::test_a"}])


def test_launch_seam_can_only_receive_token_argv_and_shell_false(tmp_path: Path):
    from tools.execution_adapters import build_request, invoke_request

    request = build_request("pytest:selected-symbols-v1", _module(tmp_path, "pytest:selected-symbols-v1", ".venv/python"), [{"selector": "tests/test_a.py::test_a"}])
    calls = []
    assert invoke_request(request, lambda *args, **kwargs: calls.append((args, kwargs)) or "result") == "result"
    assert calls == [((request.argv,), {"cwd": request.cwd, "timeout": request.timeout_seconds, "shell": False})]


def test_gradle_builder_translates_junit_hash_selector_to_gradle_dot_selector(tmp_path: Path):
    """Gradle's --tests syntax is Class.method, never JUnit's Class#method."""
    from tools.execution_adapters import GRADLE, build_request

    request = build_request(
        GRADLE,
        _module(tmp_path, GRADLE, "gradlew"),
        [{"selector": "pkg.SampleTest#works"}],
    )

    assert request.selectors == ("pkg.SampleTest#works",)
    assert request.argv[-2:] == ("--tests", "pkg.SampleTest.works")
    assert "--rerun-tasks" in request.argv
    from tools.run_tests import _request_report_path
    assert _request_report_path(request) == tmp_path.resolve() / "build/test-results/test"
    from dataclasses import replace
    assert _request_report_path(replace(request, build_profile="", argv=tuple("-Pprofile=" if part == "-Pprofile=default" else part for part in request.argv))) is None


def test_many_to_many_case_links_execute_each_exact_symbol_once(tmp_path: Path):
    """Several reviewed cases may map to one generated symbol without rerunning it."""
    from tools.execution_adapters import PYTEST, build_request

    request = build_request(
        PYTEST,
        _module(tmp_path, PYTEST, ".venv/python"),
        [
            {"selector": "tests/test_a.py::test_shared"},
            {"selector": "tests/test_a.py::test_shared"},
        ],
    )

    assert request.selectors == ("tests/test_a.py::test_shared",)
    assert request.argv.count("tests/test_a.py::test_shared") == 1


def test_runner_receipt_keeps_closed_request_and_report_digest_evidence(tmp_path: Path):
    """A V5 result must expose the exact safe launch facts that were executed."""
    from tools.execution_adapters import PYTEST, build_request, request_digest
    from tools.run_tests import _report

    request = build_request(
        PYTEST,
        _module(tmp_path, PYTEST, ".venv/python"),
        [{"selector": "tests/test_a.py::test_a"}],
    )
    report = _report(
        "NOT_RUNNABLE",
        tmp_path,
        "python",
        {"document_id": "TCDOC-run", "revision": 1, "source_digest": "sha256:" + "a" * 64, "effective_bundle_receipt_digest": "sha256:" + "d" * 64},
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        diagnostics=[{"path": "/request", "code": "RUNNER_REQUEST", "message": "blocked"}],
        request=request,
        report_evidence=(),
    )

    assert report["execution"] == {
        "adapter_id": PYTEST,
        "executable_path": request.executable,
        "cwd": request.cwd,
        "run_id": None,
        "attempt_id": None,
        "baseline_digest": None,
        "generated_delta_digest": None,
        "build_profile": "default",
        "typed_parameters": {},
        "argv": list(request.argv),
        "selectors": list(request.selectors),
        "timeout_seconds": request.timeout_seconds,
        "report_paths": list(request.report_paths),
        "request_digest": request_digest(request),
        "report_evidence": [], "artifact_evidence": [],
    }
    assert report["execution"]["request_digest"] == request_digest(request)


def test_request_digest_binds_every_closed_launch_fact(tmp_path: Path):
    """Any executable launch change must invalidate the request digest."""
    from dataclasses import replace
    from tools.execution_adapters import PYTEST, build_request, request_digest

    request = build_request(PYTEST, _module(tmp_path, PYTEST, ".venv/python"), [{"selector": "tests/test_a.py::test_a"}])
    assert request_digest(request) != request_digest(replace(request, timeout_seconds=request.timeout_seconds + 1))
    assert request_digest(request).startswith("sha256:")


def test_legacy_v3_entrypoint_is_process_free(tmp_path: Path, monkeypatch):
    """The unbound legacy API cannot bypass V5's run authorization gate."""
    from tools import run_tests

    monkeypatch.setattr(run_tests, "run_subprocess", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy process launch")))
    result = run_tests.run_tests_v3(
        tmp_path, "python",
        {"document_id": "TCDOC-legacy", "revision": 1},
        {}, {},
    )

    assert result["verdict"] == "NOT_RUNNABLE"
    assert result["diagnostics"][0]["code"] == "RUNNER_LEGACY_DISABLED"
