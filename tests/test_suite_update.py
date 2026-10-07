"""Wave 3 R/U/H/F: suite-update-v1 end to end on step5 with real Maven and scripted model answers."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from tests.live_step5 import DOCS
from tests.review_scaling_helpers import clean_compact_answer, part_text
from tests.test_quarantine_disposition import CONTROLLER, _maven
from tests.test_suite_manifest import GENERATED, _local, needs_java
from tools.suite_manifest import read_suite, verify

OLD, NEW = "Student Successfully Deleted!", "Student deleted"


@pytest.fixture(scope="module")
def suite_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """step5 after local-pilot-v1 --suite (12 cases, one test class), created once per module."""
    if not os.environ.get("TEST_SKILLS_JAVA_HOME"):
        pytest.skip("set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven")
    monkeypatch = pytest.MonkeyPatch()
    replay = _local(tmp_path_factory.mktemp("suite"), monkeypatch)
    _code, done = replay.drive(replay.start_with("--suite")[1])
    assert done["result"]["suite"]["status"] == "WRITTEN"
    return replay


def _copy(suite_project, tmp_path: Path):
    origin = getattr(suite_project, "origin", None) or suite_project.project
    suite_project.origin = origin
    project = tmp_path / "project"
    shutil.copytree(origin, project)
    suite_project.project = project
    return suite_project


def _replace_in(path: Path, old: str, new: str) -> None:
    raw = path.read_bytes().decode("utf-8")
    assert old in raw, (path, old)
    path.write_bytes(raw.replace(old, new).encode("utf-8"))


def _answer(task: dict) -> dict:
    stage = task["stage"]
    if task.get("review_mode") == "compact-v1":
        return clean_compact_answer(part_text(task))
    brief = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))
    if stage == "tc-generator:update":
        return {"test_cases": [json.loads(json.dumps(case, ensure_ascii=False).replace(OLD, NEW)) for case in brief["affected_cases"]]}
    if stage == "tc-to-autotest:update":
        return {"methods": [{"case_id": row["case_id"], "method_name": row["method"]["method_name"], "source": row["method"]["source"].replace(OLD, NEW)}
                            for row in brief["cases"]]}
    if stage == "tc-to-autotest:repair":
        return {"methods": [{"case_id": row["case_id"], "method_name": row["method"]["method_name"],
                             "source": row["method"]["source"].replace("textOfResponse(response)", "text(response)")} for row in brief["cases"]]}
    raise KeyError(stage)


def _drive(replay, *extra: str, limit: int = 60) -> dict:
    config = replay.config
    code, task = replay.call("next", "--project", str(replay.project), "--profile", "suite-update-v1", "--model-id", config["model_id"],
                             "--reviewer-isolation", "fresh", "--host-cli", config["host_cli"], "--host-cli-version", config["host_cli_version"], *extra)
    for _ in range(limit):
        if task.get("action") == "done":
            return task
        tasks = task["tasks"] if task.get("action") == "batch" else [task]
        for item in tasks:
            Path(item["output_path"]).write_text(json.dumps(_answer(item), ensure_ascii=False), encoding="utf-8")
            code, task = replay.submit(item)
            assert task.get("status") != "rejected", task.get("errors")
    raise AssertionError("suite-update did not finish")


@needs_java
def test_an_updated_requirement_rebuilds_only_its_cases_and_keeps_a_people_edit(suite_project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _copy(suite_project, tmp_path)
    project = replay.project
    monkeypatch.setenv("JAVA_HOME", os.environ["TEST_SKILLS_JAVA_HOME"])
    before = read_suite(project, "test-cases/")
    # A person refines the title of an unaffected case.
    canonical = project / "test-cases" / "test-cases.json"
    document = json.loads(canonical.read_text(encoding="utf-8"))
    document["test_cases"][0]["title"] += " (уточнено)"
    canonical.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    # The requirement changes the message and the developer implements it.
    _replace_in(project / DOCS, OLD, NEW)
    _replace_in(project / CONTROLLER, OLD, NEW)
    done = _drive(replay)
    result = done["result"]
    assert result["outcome"] == "UPDATED" and result["exit_code"] == 0, result
    after = read_suite(project, "test-cases/")
    changed = {row["case_id"] for row in after["cases"]} & {row["case_id"] for row in before["cases"]}
    digests = {row["case_id"]: row["case_digest"] for row in before["cases"]}
    updated = sorted(case_id for case_id in changed if next(row for row in after["cases"] if row["case_id"] == case_id)["case_digest"] != digests[case_id])
    assert updated and "TC-B1-010" in updated and document["test_cases"][0]["case_id"] not in updated
    new_document = json.loads(canonical.read_text(encoding="utf-8"))
    assert new_document["test_cases"][0]["title"].endswith("(уточнено)") and new_document["revision"] == document["revision"] + 1
    assert verify(project, after)["cases"] == [document["test_cases"][0]["case_id"]]  # still a person's edit, never overwritten
    text = (project / GENERATED).read_text(encoding="utf-8")
    assert NEW in text and OLD not in text
    assert {row["status"] for row in after["cases"]} == {"ACTIVE"} and after["last_run"]["run_id"] == result["run_id"]
    description = Path(result["paths"]["pr_description"]).read_text(encoding="utf-8")
    assert "TC-B1-010" in description and "AC-7" in description and "Карантин" not in description
    # W3-Р11: the description is built deterministically; the step5 reference is pinned (only the run ID varies).
    golden = Path(__file__).resolve().parent / "fixtures" / "suite" / "pr-description-step5-update.md"
    normalized = description.replace(result["run_id"], "<RUN>").replace(result["run_id"][:8], "<RUN8>")
    if os.environ.get("TEST_SKILLS_UPDATE_GOLDEN"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(normalized, encoding="utf-8", newline="\n")
    assert normalized == golden.read_text(encoding="utf-8")
    patch = Path(result["paths"]["patch"]).read_text(encoding="utf-8")
    assert f"-        assertThat(text(response)).as(\"ASSERT-B1-010-01-1-2\").isEqualTo(\"{OLD}\");" in patch.replace("\r", "")
    assert f"b/{GENERATED}" in patch and "b/test-cases/suite-manifest.json" in patch
    # The next impact analysis sees nothing left to do.
    from tools.suite_impact import impact

    assert impact(project)["requirements"] == {"added": [], "changed": [], "removed": [], "renamed": []}


@needs_java
def test_a_behaviour_change_without_a_requirement_goes_to_quarantine(suite_project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _copy(suite_project, tmp_path)
    project = replay.project
    monkeypatch.setenv("JAVA_HOME", os.environ["TEST_SKILLS_JAVA_HOME"])
    _replace_in(project / CONTROLLER, OLD, "Student removed")
    case_before = json.loads((project / "test-cases" / "test-cases.json").read_text(encoding="utf-8"))
    done = _drive(replay)
    result = done["result"]
    assert result["counts"]["quarantined"] == 1 and result["counts"]["questions"] == 1
    manifest = read_suite(project, "test-cases/")
    [quarantined] = [row for row in manifest["cases"] if row["status"] == "QUARANTINED"]
    assert quarantined["case_id"] == "TC-B1-010" and quarantined["quarantine"]["reason"] == "BEHAVIOR_CHANGED_WITHOUT_SPEC" and quarantined["quarantine"]["question"]
    assert json.loads((project / "test-cases" / "test-cases.json").read_text(encoding="utf-8")) == case_before  # the expectation is untouched
    description = Path(result["paths"]["pr_description"]).read_text(encoding="utf-8")
    assert "## Карантин" in description and "Черновик баг-репорта" in description and "Student removed" in description
    ordinary = _maven(project)
    assert list(ordinary.values()).count("skipped") == 1 and "failed" not in ordinary.values()
    # The product is fixed: the next update releases the quarantine.
    _replace_in(project / CONTROLLER, "Student removed", OLD)
    again = _drive(replay)["result"]
    assert again["counts"]["released"] == 1 and {row["status"] for row in read_suite(project, "test-cases/")["cases"]} == {"ACTIVE"}
    assert "test-skills quarantine" not in (project / GENERATED).read_text(encoding="utf-8")


@needs_java
def test_a_broken_owned_method_is_repaired_once_without_changing_expectations(suite_project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pipeline_driver_suite import attempt_suite  # noqa: F401  (the suite module is importable)
    from tools.suite_manifest import canonical_bytes, method_digests

    replay = _copy(suite_project, tmp_path)
    project = replay.project
    monkeypatch.setenv("JAVA_HOME", os.environ["TEST_SKILLS_JAVA_HOME"])
    # The package's own method no longer compiles (as if an older package version wrote it); the manifest records it as the package's.
    _replace_in(project / GENERATED, 'assertThat(text(response)).as("ASSERT-B1-010-01-1-2")', 'assertThat(textOfResponse(response)).as("ASSERT-B1-010-01-1-2")')
    manifest = read_suite(project, "test-cases/")
    content = (project / GENERATED).read_text(encoding="utf-8")
    from tools.suite_manifest import parse_locator

    methods = [method for case in manifest["cases"] for method in case["methods"]]
    symbols = [{"symbol_id": method["symbol_id"], "locator": parse_locator(method["locator"], "java")} for method in methods]
    digests, support = method_digests(GENERATED, manifest["files"][0]["file_id"], content, symbols)
    for case in manifest["cases"]:
        for method in case["methods"]:
            method["slice_digest"] = digests[method["symbol_id"]]
    manifest["files"][0]["file_digest"] = "sha256:" + __import__("hashlib").sha256(content.encode("utf-8")).hexdigest()
    manifest["files"][0]["support_digest"] = support
    (project / "test-cases" / "suite-manifest.json").write_bytes(canonical_bytes(manifest))
    done = _drive(replay)
    result = done["result"]
    assert result["counts"]["repaired"] == 1 and result["counts"]["quarantined"] == 0, result
    assert "textOfResponse" not in (project / GENERATED).read_text(encoding="utf-8")
    assert {row["status"] for row in read_suite(project, "test-cases/")["cases"]} == {"ACTIVE"}
    description = Path(result["paths"]["pr_description"]).read_text(encoding="utf-8")
    assert "отремонтирован" in description and "ожидания не менялись" in description


@needs_java
def test_a_method_a_person_edited_is_not_overwritten_and_becomes_a_proposal(suite_project, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """W3-Р5: the requirement changes, the case is updated, but its method was edited by a person: the method stays."""
    replay = _copy(suite_project, tmp_path)
    project = replay.project
    monkeypatch.setenv("JAVA_HOME", os.environ["TEST_SKILLS_JAVA_HOME"])
    _replace_in(project / GENERATED, '        assertThat(text(response)).as("ASSERT-B1-010-01-1-2")',
                '        // уточнено вручную: проверяем точный текст\n        assertThat(text(response)).as("ASSERT-B1-010-01-1-2")')
    edited = (project / GENERATED).read_bytes()
    _replace_in(project / DOCS, OLD, NEW)
    _replace_in(project / CONTROLLER, OLD, NEW)
    result = _drive(replay)["result"]
    assert (project / GENERATED).read_bytes() == edited  # the person's method is not overwritten
    description = Path(result["paths"]["pr_description"]).read_text(encoding="utf-8")
    assert "## Ручные правки" in description and "правил человек" in description
    manifest = read_suite(project, "test-cases/")
    assert "net.javaguides.springboot.controller.StudentControllerPipelineTest#tcB1010DeleteStudentReturnsConfirmationText" in verify(project, manifest)["methods"]
    assert not [row for row in manifest["cases"] if row["status"] == "QUARANTINED"]  # a person's method is never quarantined by the package


@needs_java
def test_a_weakened_check_stays_green_and_the_strength_drop_reaches_the_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Scenario 5 / K: a person replaces isEqualTo with isNotNull; the suite is green, the kill ratio of the case drops."""
    from tests.test_mutation_java import _triage_answer

    replay = _local(tmp_path, monkeypatch)
    replay.override = lambda task: clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else         _triage_answer(task) if task["stage"].startswith("mutation-triage:") else None
    skillsrc = replay.project / ".skillsrc"
    text = skillsrc.read_text(encoding="utf-8").replace("schema_version: 5.0.0", "schema_version: 5.2.0", 1)
    skillsrc.write_text(text + "mutation:\n  enabled: true\n  threads: 2\n", encoding="utf-8")
    _code, done = replay.drive(replay.start_with("--suite", "--mutation")[1])
    manifest = read_suite(replay.project, "test-cases/")
    before = next(row for row in manifest["cases"] if row["case_id"] == "TC-B1-001")["strength"]
    assert before and before["killed"] > 0
    source = (replay.project / GENERATED).read_text(encoding="utf-8")
    weakened = source.replace('.as("ASSERT-B1-001-01-1-3").isEqualTo(student(1, "Ramseh", "Mishra"))', '.as("ASSERT-B1-001-01-1-3").isNotNull()')
    weakened = weakened.replace('.as("ASSERT-B1-001-01-1-2").isEqualTo(APPLICATION_JSON)', '.as("ASSERT-B1-001-01-1-2").isNotEqualTo("text/xml")')
    weakened = weakened.replace('.as("ASSERT-B1-001-01-1-4")\n                .isEqualTo("{\\"id\\":1,\\"firstName\\":\\"Ramseh\\",\\"lastName\\":\\"Mishra\\"}")', '.as("ASSERT-B1-001-01-1-4").isNotNull()')
    assert weakened != source
    (replay.project / GENERATED).write_text(weakened, encoding="utf-8")
    result = _drive(replay, "--mutation")["result"]
    description = Path(result["paths"]["pr_description"]).read_text(encoding="utf-8")
    assert result["counts"]["quarantined"] == 0  # green: the weaker check passes
    assert "## Сила тестов упала" in description and "TC-B1-001" in description.split("## Сила тестов упала", 1)[1]
    assert (replay.project / GENERATED).read_text(encoding="utf-8") == weakened  # a person's edit is not undone by the package
    assert (replay.project / ".pilot-runs" / "suite-strength").is_dir()  # PIT history outside the suite
