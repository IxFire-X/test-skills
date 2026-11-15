"""Fix8: a repeated init and ``ready`` keep what the person wrote in .skillsrc (limits, review, paths.exclude/include, …).

Keys that detection does not derive are the person's: they are carried into the replacement proposal as they are and
are no drift.  A derived key that really went stale still gives a proposal — and the person's keys stay in it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_first_run import _write
from tools.init_skillsrc import ensure_skillsrc
from tools.ready import check_skillsrc

ROOT = Path(__file__).resolve().parents[1]
HUMAN_TOP = {"limits": {"context_batch_bytes": 1000000}, "review": {"self": False}, "disposition_policy": "cleanup"}
HUMAN_PATHS = {"exclude": ["src/generated/**"], "include": ["src/vendored/Keep.py"]}


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    _write(project, "src/app.py", "X = 1\n")
    _write(project, "pyproject.toml", "[project]\nname = 'api'\n")
    _write(project, "tests/test_app.py", "import pytest\n\n\ndef test_x():\n    assert True\n")
    return project


def _configured(tmp_path: Path) -> Path:
    """A project whose .skillsrc is what init wrote, plus the keys a person adds by hand."""
    project = _project(tmp_path)
    assert ensure_skillsrc(project, {"module:root:test.framework": "pytest"}, write=True)["status"] == "created"
    path = project / ".skillsrc"
    document = json.loads(path.read_text(encoding="utf-8"))
    document.update(HUMAN_TOP)
    document["modules"][0]["paths"].update(HUMAN_PATHS)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return project


def _stale(project: Path) -> None:
    """A derived key that no longer matches the project: the test block of the module."""
    path = project / ".skillsrc"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["modules"][0]["test"]["interpreter"] = "tools/other/python.exe"
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def _init_cli(project: Path, *args: str) -> tuple[int, dict]:
    done = subprocess.run([sys.executable, "-m", "tools.init_skillsrc", "--project", str(project), *args], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return done.returncode, json.loads(done.stdout)


def test_init_without_drift_asks_nothing_for_a_file_with_the_persons_keys(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    code, report = _init_cli(project)
    assert report["status"] == "unchanged" and code == 0, report


def test_ready_has_no_drift_warning_for_a_file_with_the_persons_keys(tmp_path: Path) -> None:
    rows = check_skillsrc(_configured(tmp_path))
    assert [(row["level"], row["message"]) for row in rows] == [("OK", ".skillsrc валиден и совпадает с проектом")], rows


def test_a_stale_derived_key_still_gives_a_proposal_with_the_persons_keys_in_it(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    _stale(project)
    receipt = ensure_skillsrc(project, {}, write=False)
    assert receipt["status"] == "needs_input", receipt
    assert [row["path"] for row in receipt["proposal"]["structural_diff"]] == ["/modules/0/test/interpreter"], receipt["proposal"]


def test_the_approved_replacement_keeps_the_persons_keys(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    _stale(project)
    proposal = ensure_skillsrc(project, {}, write=False)["proposal"]
    (project / "evidence").mkdir()
    approval = {"approval_id": "fix8", "original_digest": proposal["original_digest"], "proposed_digest": proposal["proposed_digest"]}
    receipt = ensure_skillsrc(project, {}, write=True, replacement_approval=approval, immutable_evidence_dir=project / "evidence")
    assert receipt["status"] == "updated", receipt
    document = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    assert {key: document[key] for key in HUMAN_TOP} == HUMAN_TOP
    assert {key: document["modules"][0]["paths"][key] for key in HUMAN_PATHS} == HUMAN_PATHS
    assert document["modules"][0]["test"]["interpreter"] != "tools/other/python.exe"


def test_a_module_key_of_the_person_is_kept_in_the_proposal(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    path = project / ".skillsrc"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["modules"][0]["feature_sources"] = {"requirements": ["docs"]}
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    assert ensure_skillsrc(project, {}, write=False)["status"] == "unchanged"


def test_a_project_name_the_person_changed_is_no_drift(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    path = project / ".skillsrc"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["project"]["name"] = "Мой проект"
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert ensure_skillsrc(project, {}, write=False)["status"] == "unchanged"


def test_the_proposal_names_the_kept_keys_without_their_values(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    _stale(project)
    receipt = ensure_skillsrc(project, {}, write=False)
    line = next(item for item in receipt["warnings"] if item.startswith("сохранены ключи человека"))
    for path in ("/limits", "/review", "/disposition_policy", "/modules/0/paths/exclude", "/modules/0/paths/include"):
        assert path in line
    assert "context_batch_bytes" not in line and "generated" not in line


def test_ready_shows_the_kept_paths_beside_the_diff_without_values(tmp_path: Path) -> None:
    project = _configured(tmp_path)
    _stale(project)
    row = check_skillsrc(project)[0]
    assert row["level"] == "WARN" and "/limits" in row["details"]["diff"]["preserved"], row
    assert "/modules/0/paths/exclude" in row["details"]["diff"]["preserved"]
    assert "context_batch_bytes" not in json.dumps(row, ensure_ascii=False) and "generated" not in json.dumps(row, ensure_ascii=False)


def test_a_v2_file_keeps_its_old_way_nothing_of_the_persons_goes_over(tmp_path: Path) -> None:
    project = _project(tmp_path)
    document = {"project": {"name": "legacy", "language": "python", "build_tool": "pip"}, "paths": {"source": "src", "tests": "tests", "exclude": ["gen/**"]},
                "test": {"framework": "pytest"}, "sdd": {"requirements": "docs"}}
    (project / ".skillsrc").write_text(json.dumps(document), encoding="utf-8")
    answers = {"module:root:test.framework": "pytest"}
    proposal = ensure_skillsrc(project, answers, write=False)["proposal"]
    approval = {"approval_id": "fix8", "original_digest": proposal["original_digest"], "proposed_digest": proposal["proposed_digest"]}
    (project / "evidence").mkdir()
    receipt = ensure_skillsrc(project, answers, write=True, replacement_approval=approval, immutable_evidence_dir=project / "evidence")
    assert receipt["status"] == "updated" and not any("сохранены" in item for item in receipt.get("warnings", [])), receipt
    module = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))["modules"][0]
    assert "feature_sources" not in module and "exclude" not in module["paths"]
