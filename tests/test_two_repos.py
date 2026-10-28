"""FaT-2R: the application in a neighbouring directory of the project root, the tests in their own module.

The neighbour is read only, for the model context (``paths.context``); the package never builds or starts it.  exec checks
the stand the person brought up before it writes anything (``service_url_env``)."""
from __future__ import annotations

import http.server
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

POM = "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>{}</artifactId><version>1</version></project>"


def _stand(tmp_path: Path) -> Path:
    """The two-repos stand: ``app/`` (sources) and ``autotests/`` (pom, empty test root)."""
    app = tmp_path / "app"
    (app / "src" / "main" / "java" / "org" / "example").mkdir(parents=True)
    (app / "pom.xml").write_text(POM.format("app"), encoding="utf-8")
    (app / "src" / "main" / "java" / "org" / "example" / "OwnerController.java").write_text(
        "package org.example;\npublic class OwnerController {}\n", encoding="utf-8")
    tests = tmp_path / "autotests"
    (tests / "src" / "test" / "java").mkdir(parents=True)
    (tests / "pom.xml").write_text(POM.format("autotests"), encoding="utf-8")
    (tests / "src" / "test" / "java" / ".gitkeep").write_text("", encoding="utf-8")
    return tmp_path


def _questions(report: dict) -> dict:
    return {question["id"]: question for question in report.get("questions") or []}


def test_a_tests_only_module_is_asked_for_its_context_root_and_the_answer_is_written(tmp_path: Path) -> None:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    root = _stand(tmp_path)
    report = ensure_skillsrc(root, {}, False)
    questions = _questions(report)
    context = [key for key in questions if key.endswith(":paths.context")]
    assert report["status"] == "needs_input" and len(context) == 1, report
    assert not [key for key in questions if key.endswith(":paths.source")]  # the old question that answers could not close
    assert [option["id"] for option in questions[context[0]]["options"]] == ["app"]
    answers = {key: question["options"][0]["id"] for key, question in questions.items()}
    assert ensure_skillsrc(root, answers, True)["status"] == "created"
    modules = {module["root"]: module for module in normalize_skillsrc(load_skillsrc(root / ".skillsrc"))["modules"]}
    assert modules["autotests"]["paths"]["context"] == ["app"]
    assert not [key for key in _questions(ensure_skillsrc(root, {}, False)) if key.endswith((":paths.context", ":paths.source"))]


@pytest.mark.parametrize("answer", ["../outside", "autotests", "autotests/src", "missing", "/abs", ""])
def test_a_context_root_outside_the_project_inside_the_module_or_missing_is_refused(tmp_path: Path, answer: str) -> None:
    from tools.init_skillsrc import ensure_skillsrc

    root = _stand(tmp_path / "stand")
    (tmp_path / "outside").mkdir()
    questions = _questions(ensure_skillsrc(root, {}, False))
    answers = {key: question["options"][0]["id"] for key, question in questions.items()}
    answers[next(key for key in questions if key.endswith(":paths.context"))] = answer
    report = ensure_skillsrc(root, answers, True)
    assert report["status"] == "error" and report["errors"] == ["context_root_invalid"] and not (root / ".skillsrc").exists()


def _declare(root: Path, context: list[str], service: str | None = None) -> None:
    modules = [
        {"id": "app", "root": "app", "stack": {"language": "java", "build_tool": "maven"}, "paths": {"source": ["src/main/java"]},
         "detected_from": ["app/pom.xml"]},
        {"id": "autotests", "root": "autotests", "stack": {"language": "java", "build_tool": "maven"},
         "paths": {"tests": ["src/test/java"], "context": context}, "detected_from": ["autotests/pom.xml"],
         **({"service_url_env": service} if service else {})},
    ]
    (root / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "two-repos"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": modules}), encoding="utf-8")


def test_the_inventory_reads_the_context_root_with_the_same_secret_rules(tmp_path: Path) -> None:
    from tools.project_inventory import build_inventory

    root = _stand(tmp_path)
    (root / "app" / "src" / "main" / "resources").mkdir(parents=True)
    (root / "app" / "src" / "main" / "resources" / "application.properties").write_text(
        "spring.datasource.password=s3cr3t-XYZ-not-real\n", encoding="utf-8")
    (root / "app" / ".env").write_text("TOKEN=abc\n", encoding="utf-8")
    _declare(root, ["app"])
    inventory = build_inventory(root, root / "autotests")
    files = {row["project_path"]: row for row in inventory["files"]}
    assert files["app/src/main/java/org/example/OwnerController.java"]["origin"] == "context"
    assert files["app/src/main/java/org/example/OwnerController.java"]["kind"] == "source"
    assert "origin" not in files["autotests/pom.xml"]
    excluded = {row["project_path"]: row["reason_code"] for row in inventory["exclusions"]}
    assert excluded["app/src/main/resources/application.properties"] == "SECRET_SUSPECTED"
    assert "app/.env" in excluded and "app/.env" not in files


def test_a_context_root_that_is_a_link_is_refused_by_the_inventory(tmp_path: Path) -> None:
    from tools.project_inventory import InventoryError, build_inventory

    root = _stand(tmp_path / "stand")
    try:
        os.symlink(root / "app", root / "app-link", target_is_directory=True)
    except OSError as error:  # pragma: no cover - a Windows host without the symlink privilege
        pytest.skip(f"no symlink here: {error}")
    _declare(root, ["app-link"])
    with pytest.raises(InventoryError, match="CONTEXT_ROOT_INVALID"):
        build_inventory(root, root / "autotests")


def test_a_change_of_the_neighbour_after_the_scan_is_baseline_drift_named_by_path(tmp_path: Path) -> None:
    from tools.project_inventory import build_inventory

    root = _stand(tmp_path)
    _declare(root, ["app"])
    before = build_inventory(root, root / "autotests")
    source = root / "app" / "src" / "main" / "java" / "org" / "example" / "OwnerController.java"
    source.write_text("package org.example;\npublic class OwnerController { int changed; }\n", encoding="utf-8")
    after = build_inventory(root, root / "autotests")
    changed = {row["project_path"] for row in after["files"]} & {row["project_path"] for row in before["files"]}
    differ = sorted(path for path in changed
                    if next(r for r in after["files"] if r["project_path"] == path)["content_digest"]
                    != next(r for r in before["files"] if r["project_path"] == path)["content_digest"])
    assert differ == ["app/src/main/java/org/example/OwnerController.java"] and before["digest"] != after["digest"]


def test_without_a_stand_address_a_local_pilot_request_is_lowered_to_cases(tmp_path: Path) -> None:
    from tools.run_pipeline import _execution_skipped

    root = _stand(tmp_path)
    _declare(root, ["app"])
    skipped = _execution_skipped(root, SimpleNamespace(module="autotests"))
    assert skipped is not None and "service_url_env" in skipped["fix"], skipped


def _local(port: int, path: str) -> str:
    """A loopback address of the test stand (built, so the package keeps no plain http literal)."""
    from urllib.parse import urlunsplit

    return urlunsplit(("http", f"127.0.0.1:{port}", path, "", ""))


class _Answer(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:  # noqa: N802 - the http.server name
        self.send_response(404)  # any answer of the stand counts: it is up
        self.end_headers()

    def log_message(self, *args) -> None:  # silence
        return


def test_exec_checks_the_stand_before_any_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_pipeline import _service_stop

    root = _stand(tmp_path)
    _declare(root, ["app"], service="STAND_URL")
    monkeypatch.delenv("STAND_URL", raising=False)
    assert _service_stop(root, "autotests")["reason"] == "SERVICE_URL_MISSING"
    monkeypatch.setenv("STAND_URL", "ftp://stand")
    assert _service_stop(root, "autotests")["reason"] == "SERVICE_URL_INVALID"
    for port in ("99999", "abc"):  # a port urllib cannot read: a refusal with the address without the port, no traceback
        monkeypatch.setenv("STAND_URL", _local(9, "/api").replace(":9/", f":{port}/"))
        stop = _service_stop(root, "autotests")
        assert stop["code"] == "SERVICE_URL_INVALID" and stop["address"] == _local(9, "/api").replace(":9/", "/"), stop
    server = http.server.HTTPServer(("127.0.0.1", 0), _Answer)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("STAND_URL", _local(port, "/api"))
        assert _service_stop(root, "autotests") is None  # up: the tests run and fail or pass as usual
        # no proxy: the Java tests do not use HTTP_PROXY, and a proxy's own 502/403 is not the stand
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
            monkeypatch.setenv(name, _local(9, "/"))
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        assert _service_stop(root, "autotests") is None
        for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy"):
            monkeypatch.delenv(name, raising=False)
    finally:
        server.shutdown()
        server.server_close()
    from urllib.parse import urlunsplit

    # nobody listens on the port any more; the address shown keeps no userinfo, query or fragment
    monkeypatch.setenv("STAND_URL", urlunsplit(("http", f"tester:pa55word@127.0.0.1:{port}", "/api", "token=t0ken", "frag")))
    stop = _service_stop(root, "autotests")
    shown = json.dumps(stop, ensure_ascii=False)
    assert stop["code"] == "SERVICE_UNREACHABLE" and "STAND_URL" in stop["fix"] and stop["address"] == _local(port, "/api"), stop
    assert not [secret for secret in ("tester", "pa55word", "t0ken", "frag") if secret in shown]
    assert _service_stop(root, "app") is None  # a module without a stand is not checked


def _stand_project(tmp_path: Path) -> tuple[Path, Path, str]:
    """``tests.test_finish._project`` with ``service_url_env`` in the module before the scan (the baseline binds .skillsrc)."""
    import shutil

    import tests.test_finish as finish
    from tests.test_project_native_pytest import _module_python, _reviewed_inputs

    project = tmp_path / "project"
    shutil.copytree(Path(finish.__file__).parent / "fixtures" / "projects" / "python-pytest", project)
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]}, "service_url_env": "STAND_URL",
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": finish.MODULE_PYTHON,
                     "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(project)
    _document, _artifact, _review, _evidence, run_root, attempt_id, _authorization, _delta = _reviewed_inputs(project)
    return project, run_root, attempt_id


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_an_unreachable_stand_changes_nothing_and_exec_after_it_is_up_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    """End to end through cmd_exec: the stop leaves every byte of the project and the run as it was, the attempt open;
    once the stand answers, the same exec goes on to the run of the tests."""
    import tests.test_finish as finish
    from tools.pilot_state import read_terminal_result
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id = _stand_project(tmp_path)
    monkeypatch.setenv("STAND_URL", _local(9, "/"))  # the discard port: nobody answers
    before = _snapshot(project)
    code = cmd_exec(finish._args(project, run_root))
    answer = json.loads(capsys.readouterr().out)
    assert code != 0 and answer["code"] == "SERVICE_UNREACHABLE" and answer["fix"] and answer["address"] == _local(9, "/"), answer
    assert _snapshot(project) == before
    with pytest.raises(KeyError):
        read_terminal_result(run_root, attempt_id)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("STAND_URL", _local(server.server_address[1], "/"))
        code = cmd_exec(finish._args(project, run_root))
    finally:
        server.shutdown()
        server.server_close()
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["verdict"] == "PASS" and output["finalization"] == "complete", output
    assert read_terminal_result(run_root, attempt_id)["verification"] == "PASS"


def test_the_context_root_takes_no_part_in_the_build_of_exec(tmp_path: Path) -> None:
    """The exec request of the tests module runs in that module only: no argument and no working directory of the neighbour."""
    from tools.execution_adapters import MAVEN, build_request

    root = _stand(tmp_path)
    wrapper = root / "autotests" / "mvnw"
    wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
    wrapper.chmod(0o755)
    module = {"root": str(root / "autotests"), "paths": {"tests": ["src/test/java"], "context": ["app"]},
              "test": {"adapter_id": MAVEN, "build_profile": "default", "adapter_parameters": {}, "wrapper": "mvnw"}}
    request = build_request(MAVEN, module, [{"selector": "org.example.OwnersApiTest#createsOwner"}])
    assert Path(request.cwd) == (root / "autotests").resolve()
    assert not [item for item in (request.executable, *request.argv) if "app" in Path(str(item)).parts]


@pytest.mark.parametrize("profile", ["local-pilot-v1", "cases-only-v1"])
def test_a_scan_of_a_tests_module_with_a_neighbour_reaches_the_context_and_names_its_drift(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str) -> None:
    """FaT-2R2: the neighbour's files are context inputs of the baseline, not parent build files (run 7d546b2c…)."""
    from tests.test_phase2_scan import _args, _project
    from tools import run_pipeline
    from tools.init_skillsrc import _payload
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    project, _module = _project(tmp_path)
    app = project / "services" / "app" / "src"
    app.mkdir(parents=True)
    (app / "owners.py").write_text("OWNERS = []\n", encoding="utf-8")
    document = parse_skillsrc_bytes((project / ".skillsrc").read_bytes())
    document["modules"][0]["paths"]["context"] = ["services/app"]
    document["modules"][0]["service_url_env"] = "STAND_URL"
    (project / ".skillsrc").write_bytes(_payload(document))
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    args = _args(project)
    args.profile = profile
    assert run_pipeline.cmd_scan(args) == 0, payloads[-1:]
    scan = payloads[-1]
    run_root = Path(scan["run_root"])
    inventory = read_inventory_receipt(run_root / "inventories" / (scan["inventory_digest"].removeprefix("sha256:") + ".json"))
    baseline = read_execution_baseline(run_root / "baselines" / (scan["baseline_digest"].removeprefix("sha256:") + ".json"))
    assert scan["policy_profile"] == profile and "context-marker" in json.dumps(scan, ensure_ascii=False), scan
    assert [row["project_path"] for row in inventory["files"] if row.get("origin") == "context"] == ["services/app/src/owners.py"]
    assert "services/app/src/owners.py" in {row["project_path"] for row in baseline["inputs"]}
    assert scan["scope"]["parent_files"] == [".skillsrc"], scan["scope"]

    (app / "owners.py").write_text("OWNERS = ['changed']\n", encoding="utf-8")
    assert run_pipeline.cmd_status(SimpleNamespace(project=str(project), run=scan["run_id"])) == 0
    assert payloads[-1]["drift"]["changed"] == ["services/app/src/owners.py"], payloads[-1]


def test_a_repeated_init_keeps_the_declared_context_and_stand_address(tmp_path: Path) -> None:
    """FaT-2R2 (а): discovery cannot see paths.context or service_url_env; a repeated init proposes no replacement."""
    from tools.init_skillsrc import _payload, ensure_skillsrc
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    root = _stand(tmp_path)
    questions = _questions(ensure_skillsrc(root, {}, False))
    answers = {key: question["options"][0]["id"] for key, question in questions.items()}
    assert ensure_skillsrc(root, answers, True)["status"] == "created"
    document = parse_skillsrc_bytes((root / ".skillsrc").read_bytes())
    next(module for module in document["modules"] if module["root"] == "autotests")["service_url_env"] = "STAND_URL"
    (root / ".skillsrc").write_bytes(_payload(document))
    again = ensure_skillsrc(root, {key: value for key, value in answers.items() if key.endswith(":test.framework")}, False)
    assert again["status"] == "unchanged", again.get("proposal") or again
