"""FaT-H2b: the second pass and the hints of the second bug hunt (2.2, 2.7–2.9, 2.11) and discovery P2.

- P2a: spring-boot-starter-test means JUnit 5, unless JUnit 4 (vintage, junit:junit) is in the same build;
- P2b: a question of a module that was not selected, with the one host runner as its only option, answers itself
  (default:not-selected-module); a later scan of that module says the framework was taken by default;
- 2.11: status names a vanished runtime in drift.changed and still compares the files;
- 2.7–2.9: next_step and fix name the right action after a review that was not accepted;
- 2.2: an abandoned run of the same request (drift) never blocks the next scan.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_first_run import _write

STARTER = """<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>NAME</artifactId><version>1</version>
<dependencies>DEPS</dependencies></project>
"""
STARTER_TEST = "<dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>"
VINTAGE = "<dependency><groupId>org.junit.vintage</groupId><artifactId>junit-vintage-engine</artifactId><scope>test</scope></dependency>"
JUNIT4 = "<dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency>"


def _pom(name: str, *deps: str) -> str:
    return STARTER.replace("NAME", name).replace("DEPS", "".join(deps))


def _single(tmp_path: Path, *deps: str) -> Path:
    project = tmp_path / "single"
    _write(project, "pom.xml", _pom("app", *deps))
    _write(project, "src/main/java/x/App.java", "package x;\npublic class App {}\n")
    return project


@pytest.mark.parametrize(("deps", "question"), [
    ((STARTER_TEST,), False),
    ((STARTER_TEST, VINTAGE), True),
    ((STARTER_TEST, JUNIT4), True),
])
def test_starter_test_is_junit5_unless_junit4_is_there(tmp_path: Path, deps: tuple[str, ...], question: bool) -> None:
    from tools.discover_project import discover_project

    report = discover_project(_single(tmp_path, *deps))
    [module] = report["modules"]
    fields = [item["field"] for item in report["questions"]]
    if question:
        assert any(field.endswith(".test.framework") for field in fields), report
    else:
        assert module["test"]["framework"] == "junit5" and not fields, report


def _microservices(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """Two modules like spring-petclinic-microservices: customers with tests, admin-server with no test dependency."""
    from tools.discover_project import discover_project

    project = tmp_path / "micro"
    _write(project, "customers/pom.xml", _pom("customers", STARTER_TEST))
    _write(project, "customers/src/main/java/x/Owner.java", "package x;\npublic class Owner {}\n")
    _write(project, "customers/src/test/java/x/OwnerTest.java", "package x;\nclass OwnerTest {}\n")
    _write(project, "customers/docs/owners.md", "Владелец имеет имя.\n")
    _write(project, "admin/pom.xml", _pom("admin"))
    _write(project, "admin/src/main/java/x/Admin.java", "package x;\npublic class Admin {}\n")
    _write(project, "admin/docs/admin.md", "Админка показывает сервисы.\n")
    ids = {module["root"]: module["id"] for module in discover_project(project)["modules"]}
    return project, ids


def test_a_question_of_a_module_that_is_not_selected_answers_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _fake_mvn
    from tools.init_skillsrc import ensure_skillsrc

    _fake_mvn(tmp_path, monkeypatch)  # FaT-NB: with the build tool found init adds no BUILD_TOOL_NOT_FOUND note
    project, ids = _microservices(tmp_path)
    assert ensure_skillsrc(project, {}, write=False)["status"] == "needs_input"
    receipt = ensure_skillsrc(project, {}, write=True, selected_module=ids["customers"])
    assert receipt["status"] == "created" and receipt["questions"] == [], receipt
    assert receipt["warnings"] == [f"modules.{ids['admin']}.test.framework=junit5: default:not-selected-module"]
    # the module that is selected is never answered for the human
    selected_admin = tmp_path / "again"
    project2, ids2 = _microservices(selected_admin)
    blocked = ensure_skillsrc(project2, {}, write=False, selected_module=ids2["admin"])
    assert blocked["status"] == "needs_input" and [q["field"] for q in blocked["questions"]] == [f"modules.{ids2['admin']}.test.framework"]


def test_a_question_with_several_options_stays_a_question(tmp_path: Path) -> None:
    from tools.init_skillsrc import not_selected_defaults

    discovery = {"questions": [
        {"id": "module:b:stack.build_tool", "field": "modules.b.stack.build_tool",
         "options": [{"id": "gradle", "value": "gradle", "evidence": ["b/build.gradle:build-tool"]},
                     {"id": "maven", "value": "maven", "evidence": ["b/pom.xml:build-tool"]}]},
        {"id": "module:c:test.framework", "field": "modules.c.test.framework",
         "options": [{"id": "junit5", "value": "junit5", "evidence": ["c/pom.xml:marker.junit5"]}]},
    ]}
    assert not_selected_defaults(discovery, "a") == ({}, [])
    assert not_selected_defaults(discovery, None) == ({}, [])


def test_ready_and_scan_ask_only_about_the_selected_module_and_a_later_scan_names_the_default(tmp_path: Path,
                                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _fake_mvn
    from tools import run_pipeline
    from tools.ready import check

    project, ids = _microservices(tmp_path)
    _fake_mvn(tmp_path, monkeypatch)  # like the campaign: the system mvn gives each module its closed test tuple
    report = check(project, ["customers/docs/owners.md"], "cases-only-v1", ids["customers"])
    assert not [row for row in report["checks"] if row["id"] in {"skillsrc", "module"} and row["level"] == "STOP"], report["checks"]
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    def scan(module: str, docs: str) -> tuple[int, dict]:
        code = run_pipeline.cmd_scan(SimpleNamespace(project=str(project), module=module, target=None, docs=[docs],
                                                     profile="cases-only-v1", new_run=True))
        return code, json.loads(json.dumps(payloads[-1]))

    code, first = scan(ids["customers"], "customers/docs/owners.md")
    assert code == 0 and "skillsrc_defaults" not in first, first
    code, later = scan(ids["admin"], "admin/docs/admin.md")
    assert code == 0, later
    [note] = later["skillsrc_defaults"]
    assert "test.framework=junit5" in note and "по умолчанию" in note and "нет признака" in note


# --------------------------------------------------------------------------------------
# 2.11 status with a vanished runtime
# --------------------------------------------------------------------------------------

def test_status_names_a_vanished_runtime_and_still_compares_the_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan, _status

    project = _python_project(tmp_path, runtime=True)
    code, scan = _scan(project, monkeypatch)
    assert code == 0 and scan["policy_profile"] == "local-pilot-v1", scan
    (project / "services/api/.venv/Scripts/python.exe").unlink()
    _write(project, "services/api/src/app.py", "X = 2\n")
    status = _status(project, scan["run_id"], monkeypatch)
    drift = status["drift"]
    assert drift["changed"] == ["services/api/.venv/Scripts/python.exe (runtime недоступен)", "services/api/src/app.py"], drift
    assert "--new-run" in drift["command"]


def test_exec_names_a_vanished_runtime(tmp_path: Path) -> None:
    from tools.run_pipeline import HostStop, _revalidate_execution_baseline

    missing = tmp_path / "services/api/.venv/Scripts/python.exe"
    coordinates = {"manifest": {"project": str(tmp_path)}, "run_root": str(tmp_path / "run"), "module_root": str(tmp_path),
                   "attempt": {"attempt_id": "a", "baseline_digest": "sha256:" + "0" * 64, "module": "."}}
    with pytest.raises(HostStop) as stop:
        _revalidate_execution_baseline(coordinates, SimpleNamespace(executable=str(missing)))
    assert stop.value.code == "BASELINE_INCOMPLETE" and str(missing) in str(stop.value) and "новый прогон" in str(stop.value)


# --------------------------------------------------------------------------------------
# 2.7 / 2.8 / 2.9 next_step and fix by the verdict
# --------------------------------------------------------------------------------------

def test_right_after_scan_the_next_step_starts_with_the_parts_plan() -> None:
    from tools.run_pipeline import _next_step

    step = _next_step({"next_expected_artifact": "context-marker response"})
    assert step["stage"] == "context-marker" and step["skill_section"] == "3"
    assert step["action"].startswith("сначала context_marker_parts plan") and "context-marker:requirements" in step["action"]


@pytest.mark.parametrize(("verdict", "stage", "section", "needle"), [
    ("ПРИНЯТО", "exec", "5", "материализация"),
    ("ТРЕБУЕТ ДОРАБОТКИ", "finish", "6", "next_run"),
])
def test_status_after_the_static_review_follows_the_verdict(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                          verdict: str, stage: str, section: str, needle: str) -> None:
    from tests.test_exec_prepares import _project
    from tools.run_pipeline import cmd_status

    project, run_root, _attempt_id = _project(tmp_path, static_verdict=verdict)
    cmd_status(SimpleNamespace(project=str(project), run=run_root.name))
    step = json.loads(capsys.readouterr().out)["next_step"]
    assert step["stage"] == stage and step["skill_section"] == section and needle in step["action"], step
    if verdict != "ПРИНЯТО":
        assert "ревью сверх лимита" in step["action"] and "exec" not in step["action"]


def test_exec_after_a_refused_static_review_points_to_finish_not_to_another_review(tmp_path: Path,
                                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_exec_prepares import _project
    from tests.test_finish import _args
    from tools.run_pipeline import cmd_exec

    project, run_root, _attempt_id = _project(tmp_path, static_verdict="ТРЕБУЕТ ДОРАБОТКИ")
    assert cmd_exec(_args(project, run_root)) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["reason"] == "EXECUTION_NOT_PREPARED"
    assert "finish" in output["fix"] and "next_run" in output["fix"] and "снова exec" not in output["fix"] and "ПРИНЯТО" not in output["fix"]


# --------------------------------------------------------------------------------------
# 2.2 an abandoned run never blocks the second pass
# --------------------------------------------------------------------------------------

def test_the_hunters_second_pass_runs_past_an_abandoned_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """scan A → a file changes → status A: drift → its command makes B → ТРЕБУЕТ ДОРАБОТКИ → finish B → the next_run
    command works, and names A as abandoned; A itself is left as it is."""
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _rework, _run_command, _scan, _status
    from tools import pilot_state, run_pipeline

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, first
    _write(project, "services/api/src/app.py", "X = 2\n")
    drift = _status(project, first["run_id"], monkeypatch)["drift"]
    completed = _run_command(drift["command"])
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]
    second, _end = json.JSONDecoder().raw_decode(completed.stdout)
    _rework(project, second)
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    run_pipeline.cmd_finish(SimpleNamespace(project=str(project), run=second["run_id"]))
    monkeypatch.undo()
    finished = payloads[-1]
    assert finished["reason_code"] == "REWORK", finished
    events_before = len(pilot_state.derive_state(Path(first["run_root"]))["events"])
    completed = _run_command(finished["next_run"]["command"])
    assert completed.returncode == 0, completed.stdout[-2000:] + completed.stderr[-2000:]
    third, _end = json.JSONDecoder().raw_decode(completed.stdout)
    assert third["run_id"] not in {first["run_id"], second["run_id"]}
    [abandoned] = third["abandoned_runs"]
    assert abandoned.startswith(first["run_id"]) and "дрейф" in abandoned
    assert len(pilot_state.derive_state(Path(first["run_root"]))["events"]) == events_before  # A is named, never touched


def test_a_live_run_without_drift_still_blocks_and_finish_is_offered_only_when_it_closes(tmp_path: Path,
                                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _rework, _scan

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, first
    code, again = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 2 and again["reason"] == "SCAN_RUN_IN_PROGRESS" and "finish" not in again["fix"], again
    _rework(project, first)  # ТРЕБУЕТ ДОРАБОТКИ: now finish closes the attempt
    code, again = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 2 and again["reason"] == "SCAN_RUN_IN_PROGRESS" and " finish " in again["fix"], again
