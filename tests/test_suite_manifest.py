"""Wave 3 S: the living suite in the project and its manifest."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.live_step5 import DOCS, Replay
from tools.pilot_state import read_effective_canonical
from tools.requirement_identity import identify
from tools.suite_manifest import (SuiteError, build_manifest, canonical_bytes, projections, read_suite, sha256_bytes, suite_directory, validate, verify,
                                  write_suite)

JAVA_HOME = os.environ.get("TEST_SKILLS_JAVA_HOME")
needs_java = pytest.mark.skipif(not JAVA_HOME or not (Path(JAVA_HOME) / "bin").is_dir(), reason="set TEST_SKILLS_JAVA_HOME to a JDK 17+ to run Maven")
GENERATED = "src/test/java/net/javaguides/springboot/controller/StudentControllerPipelineTest.java"


@pytest.fixture(scope="module")
def step5(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The effective canonical of the 9340016c cases (a cases-only replay) and its recorded automation."""
    replay = Replay("9340016c", tmp_path_factory.mktemp("cases"), profile="cases-only-v1")
    replay.review_mode = None
    from tests.review_scaling_helpers import clean_compact_answer, part_text

    replay.override = lambda task: clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None
    _code, done = replay.drive()
    document = read_effective_canonical(replay.run_root(done), done["result"]["attempt_id"])["document"]
    answer = next(value for label, value in replay.recorded.items() if label.startswith("tc-to-autotest."))
    from tools.run_pipeline import _docs_entries

    entries = _docs_entries(replay.project, [DOCS])
    return {"document": document, "automation": {"artifacts": answer}, "entries": entries, "project": replay.project}


def _manifest(step5: dict, files: dict | None = None, **extra) -> dict:
    document = step5["document"]
    rendered = projections(document)
    generated = step5["automation"]["artifacts"]["generated_files"]
    test_files = files or {row["file_id"]: {"path": row["path"], "content": row["content"]} for row in generated}
    return build_manifest(
        suite_id="SUITE-step5-root", module_id="root", package_version="0.5.0-pilot", document=document, identities=identify(step5["entries"]),
        documents=[{"path": DOCS, "sha256": step5["entries"][0]["sha256"]}], id_pattern=None, automation=step5["automation"], test_files=test_files,
        surface=extra.pop("surface", None), suite_dir="test-cases/", suite_digests={name: sha256_bytes(data) for name, data in rendered.items()},
        source_run={"run_id": "9340016cb730422982469a3b41959de8", "attempt_id": None, "profile": "local-pilot-v1", "verification": "PASS", "accepted": True},
        history=[{"run_id": "9340016cb730422982469a3b41959de8", "profile": "local-pilot-v1", "action": "CREATED"}], **extra)


def _write(project: Path, step5: dict, manifest: dict) -> None:
    write_suite(project, "test-cases/", {**projections(step5["document"]), "manifest": canonical_bytes(manifest)})
    for row in step5["automation"]["artifacts"]["generated_files"]:
        target = project / row["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(row["content"].encode("utf-8"))


def test_manifest_links_cases_to_requirement_keys_and_method_slices(step5: dict) -> None:
    manifest = _manifest(step5)
    assert validate(manifest) == []
    assert manifest == _manifest(step5)  # deterministic
    cases = manifest["cases"]
    assert len(cases) == len(step5["document"]["test_cases"]) == 12
    keys = {row["key"] for row in manifest["requirements"]}
    for case in cases:
        assert case["automation"] == "AUTOMATED" and case["methods"] and case["status"] == "ACTIVE"
        assert case["requirement_keys"] and set(case["requirement_keys"]) <= keys
        assert set(case["requirement_text_digests"]) == set(case["requirement_keys"])
        for method in case["methods"]:
            assert method["file"] == GENERATED and method["locator"].startswith("net.javaguides.springboot.controller.StudentControllerPipelineTest#")
    assert [row["path"] for row in manifest["files"]] == [GENERATED]
    assert all(key.startswith(f"md:{DOCS}#") for key in keys)


def test_verify_sees_only_what_a_person_changed(tmp_path: Path, step5: dict) -> None:
    project = tmp_path / "project"
    manifest = _manifest(step5)
    _write(project, step5, manifest)
    clean = {"suite_files": [], "cases": [], "methods": [], "support": [], "missing": []}
    assert verify(project, manifest) == clean
    test_file = project / GENERATED
    original = test_file.read_bytes()
    # Line endings do not count as an edit of a method or of SUPPORT.
    test_file.write_bytes(original.replace(b"\n", b"\r\n"))
    assert verify(project, manifest) == clean
    text = original.decode("utf-8")
    target = next(method for case in manifest["cases"] for method in case["methods"])
    name = target["locator"].rsplit("#", 1)[1]
    start = text.index(f"void {name}(")
    body = text.index("{", start) + 1
    test_file.write_text(text[:body] + "\n        int unused = 0;" + text[body:], encoding="utf-8")
    assert verify(project, manifest)["methods"] == [target["locator"]]
    assert verify(project, manifest)["support"] == []
    test_file.write_text(text.replace("import ", "import java.util.List;\nimport ", 1), encoding="utf-8")
    edits = verify(project, manifest)
    assert edits["support"] == [GENERATED] and edits["methods"] == []
    test_file.write_bytes(original)
    canonical = project / "test-cases" / "test-cases.json"
    document = json.loads(canonical.read_text(encoding="utf-8"))
    document["test_cases"][0]["title"] += " (уточнено)"
    canonical.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    edits = verify(project, manifest)
    assert edits["cases"] == [document["test_cases"][0]["case_id"]] and edits["suite_files"] == ["test-cases/test-cases.json"]


def test_suite_files_are_never_overwritten_unless_they_match_the_manifest(tmp_path: Path, step5: dict) -> None:
    project = tmp_path / "project"
    manifest = _manifest(step5)
    payloads = {**projections(step5["document"]), "manifest": canonical_bytes(manifest)}
    first = write_suite(project, "test-cases/", payloads)
    assert write_suite(project, "test-cases/", payloads) == first  # same bytes: nothing to do
    (project / "test-cases" / "test-cases.md").write_text("ручная правка\n", encoding="utf-8")
    with pytest.raises(SuiteError) as error:
        write_suite(project, "test-cases/", payloads)
    assert error.value.code == "SUITE_FILE_CONFLICT"
    edited = sha256_bytes((project / "test-cases" / "test-cases.md").read_bytes())
    write_suite(project, "test-cases/", {"markdown": payloads["markdown"]}, replace={"markdown": edited})
    assert (project / "test-cases" / "test-cases.md").read_bytes() == payloads["markdown"]
    assert read_suite(project, "test-cases/")["suite_id"] == "SUITE-step5-root"


@pytest.mark.parametrize("value,expected", [(None, "test-cases/"), ("qa/suite", "qa/suite/"), ("qa\\suite\\", "qa/suite/")])
def test_suite_directory_defaults_and_normalizes(tmp_path: Path, value, expected: str) -> None:
    assert suite_directory(tmp_path, None if value is None else {"suite": {"path": value}}) == expected


@pytest.mark.parametrize("value", ["../outside", "/abs", "C:/abs", "a/../../b", "."])
def test_suite_directory_stays_inside_the_project(tmp_path: Path, value: str) -> None:
    with pytest.raises(SuiteError):
        suite_directory(tmp_path, {"suite": {"path": value}})


def _local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Replay:
    from tests.review_scaling_helpers import clean_compact_answer, part_text

    monkeypatch.setenv("JAVA_HOME", str(JAVA_HOME))
    monkeypatch.setenv("PATH", str(Path(JAVA_HOME) / "bin") + os.pathsep + os.environ.get("PATH", ""))
    replay = Replay("9340016c", tmp_path, override=lambda task: clean_compact_answer(part_text(task)) if task.get("review_mode") == "compact-v1" else None)
    replay.review_mode = None
    (replay.project / GENERATED).unlink()  # the fixture keeps the class retained by the live run
    return replay


@needs_java
def test_local_pilot_with_suite_writes_the_suite_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path, monkeypatch)
    code, done = replay.drive(replay.start_with("--suite")[1])
    result = done["result"]
    assert result["verification"] == "PASS" and result["schema_version"] == "1.2.0"
    assert result["suite"] == {"status": "WRITTEN", "reason_code": None, "message": None, "cases": 12, "files": 1}
    project = replay.project
    manifest = read_suite(project, "test-cases/")
    assert validate(manifest) == [] and manifest["source_run"]["run_id"] == result["run_id"]
    assert {case["last_green_run"] for case in manifest["cases"]} == {result["run_id"]}
    assert [row["path"] for row in manifest["files"]] == [GENERATED]
    assert manifest["code_surface"]["endpoints"] and verify(project, manifest)["methods"] == []
    before = {path.name: path.read_bytes() for path in (project / "test-cases").iterdir()}
    _code, again = replay.next(result["run_id"])
    assert again["result"]["suite"]["status"] == "WRITTEN"
    assert {path.name: path.read_bytes() for path in (project / "test-cases").iterdir()} == before
    # A second run does not overwrite the suite of the first one.
    (project / GENERATED).rename(project / (GENERATED + ".kept"))
    _code, second = replay.drive(replay.start_with("--suite")[1])
    assert second["result"]["suite"]["status"] == "NOT_WRITTEN" and second["result"]["suite"]["reason_code"] == "SUITE_EXISTS"
    assert {path.name: path.read_bytes() for path in (project / "test-cases").iterdir()} == before


@needs_java
def test_without_the_flag_nothing_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay = _local(tmp_path, monkeypatch)
    _code, done = replay.drive(replay.start()[1])
    assert "suite" not in done["result"] and done["result"]["schema_version"] == "1.0.0"
    assert not (replay.project / "test-cases").exists()
