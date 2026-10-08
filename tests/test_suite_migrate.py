"""Wave 3 G: a format-0 suite (a run's bundle without a manifest) migrates to 1.0.0 without losing data (W3-Р7)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests.live_step5 import DOCS
from tests.test_suite_manifest import GENERATED, _local, needs_java
from tools.suite import main as suite_main
from tools.suite import status
from tools.suite_manifest import SuiteError, read_suite, validate, verify
from tools.suite_migrate import detect, migrate


def _legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, edit_case: bool = True):
    replay = _local(tmp_path, monkeypatch)
    _code, done = replay.drive(replay.start()[1])
    assert done["result"]["verification"] == "PASS"
    bundle = Path(done["result"]["paths"]["candidate_bundle"])
    suite = replay.project / "test-cases"
    suite.mkdir()
    for path in bundle.glob("TCDOC-*"):
        shutil.copy2(path, suite / path.name)
    [legacy] = suite.glob("TCDOC-*.r1.json")
    document = json.loads(legacy.read_text(encoding="utf-8"))
    edited = None
    if edit_case:
        edited = document["test_cases"][2]["case_id"]
        document["test_cases"][2]["title"] += " (уточнено аналитиком)"
        legacy.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return replay, done, document, edited


@needs_java
def test_a_bundle_suite_migrates_without_losing_cases_links_or_people_edits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    replay, done, document, edited = _legacy(tmp_path, monkeypatch)
    project = replay.project
    assert detect(project, "test-cases/") == "0"
    assert status(project)["migration_needed"] is True
    before_tests = (project / GENERATED).read_bytes()
    summary = migrate(project)
    assert summary["status"] == "MIGRATED" and summary["from_format"] == "0" and summary["to_format"] == "1.0.0"
    assert summary["run_id"] == done["result"]["run_id"] and summary["requirement_keys_from"] == "scan"
    assert summary["cases_edited_by_people"] == [edited] and summary["cases_added_by_people"] == [] and summary["cases_missing_from_bundle"] == []
    manifest = read_suite(project, "test-cases/")
    assert validate(manifest) == [] and manifest["history"][-1] == {"run_id": done["result"]["run_id"], "profile": "migration", "action": "MIGRATED", "from_format": "0"}
    # No data lost: the people's document is the suite's canonical JSON, every case is linked to its methods.
    assert json.loads((project / "test-cases" / "test-cases.json").read_text(encoding="utf-8")) == document
    assert [case["case_id"] for case in manifest["cases"]] == sorted(case["case_id"] for case in document["test_cases"])
    assert all(case["methods"] for case in manifest["cases"]) and [row["path"] for row in manifest["files"]] == [GENERATED]
    assert (project / GENERATED).read_bytes() == before_tests
    # The edited case is seen as a person's edit and nothing else is.
    assert verify(project, manifest) == {"suite_files": [], "cases": [edited], "methods": [], "support": [], "missing": []}
    assert status(project)["edited"]["cases"] == [edited]
    assert migrate(project)["status"] == "CURRENT"
    legacy_files = sorted(path.name for path in (project / "test-cases").glob("TCDOC-*"))
    assert summary["legacy_files"] == legacy_files and legacy_files  # left in place for the PR to decide


@needs_java
def test_changed_documents_take_the_keys_from_the_run_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    replay, done, _document, _edited = _legacy(tmp_path, monkeypatch, edit_case=False)
    doc = replay.project / DOCS
    doc.write_bytes(doc.read_bytes() + "\n## Новое требование\nПоявилось после прогона.\n".encode("utf-8"))
    assert suite_main(["migrate", "--project", str(replay.project), "--write"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok" and payload["migration"]["requirement_keys_from"] == "run_record"
    manifest = read_suite(replay.project, "test-cases/")
    assert validate(manifest) == [] and len(manifest["requirements"]) == len(json.loads(
        (replay.project / "test-cases" / "test-cases.json").read_text(encoding="utf-8"))["source_requirements"])


def test_an_unknown_format_or_a_missing_run_stops_the_migration(tmp_path: Path) -> None:
    suite = tmp_path / "test-cases"
    suite.mkdir()
    (suite / "suite-manifest.json").write_text(json.dumps({"format_version": "9.0.0"}), encoding="utf-8")
    with pytest.raises(SuiteError) as error:
        migrate(tmp_path)
    assert error.value.code == "MIGRATION_UNKNOWN_FORMAT"
    (suite / "suite-manifest.json").unlink()
    (suite / "TCDOC-demo-1.r1.json").write_text(json.dumps({"document_id": "TCDOC-demo-1", "revision": 1, "test_cases": []}), encoding="utf-8")
    with pytest.raises(SuiteError) as error:
        migrate(tmp_path)
    assert error.value.code == "MIGRATION_SOURCE_MISSING"



@needs_java
def test_the_migration_command_writes_only_with_the_persons_consent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    """Independent review 2.3 (A5.1): the suite is written only under an authorization — the CLI previews by default,
    and `--write` is a person's consent recorded in a migration receipt."""
    replay, _done, _document, _edited = _legacy(tmp_path, monkeypatch, edit_case=False)
    project = replay.project
    before = sorted(path.name for path in (project / "test-cases").iterdir())
    assert suite_main(["migrate", "--project", str(project)]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["migration"]["status"] == "MIGRATED" and preview["written"] is False
    assert sorted(path.name for path in (project / "test-cases").iterdir()) == before and detect(project, "test-cases/") == "0"
    assert suite_main(["migrate", "--project", str(project), "--write"]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["written"] is True and detect(project, "test-cases/") == "1.0.0"
    receipt = json.loads(Path(written["receipt"]).read_text(encoding="utf-8"))
    assert receipt["authorization"] == "suite migrate --write" and receipt["migration"]["run_id"] == written["migration"]["run_id"]
    assert Path(written["receipt"]).parent == project / ".pilot-runs" / "suite-migrations"
