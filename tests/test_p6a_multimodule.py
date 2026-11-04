"""P6a (bugs B-2, B-8, B-16 of the review of 02.11): a multi-module Maven project reached from its root.

Before: the requirements document of docs/ in the repository root was outside the inventory of every module
(TASK_DOC_OUTSIDE_CONTEXT, whose fix named a root module that does not exist), and the module id was the hex of its folder
name (``root--617069``)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools import run_pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "maven-multimodule"
DOC = "docs/feature.md"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "fx-multi"
    shutil.copytree(FIXTURE, project)
    return project


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, *, module: str | None, docs: list[str] | None = (DOC,), expect: int = 0) -> dict:
    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    args = ["scan", "--project", str(project), "--profile", "cases-only-v1", "--new-run"]
    for doc in docs or ():
        args += ["--docs", doc]
    if module is not None:
        args += ["--module", module]
    assert run_pipeline.main(args) == expect, printed[-1]
    return json.loads(json.dumps(printed[-1], ensure_ascii=False))


def _inventory(payload: dict) -> dict:
    [path] = (Path(payload["run_root"]) / "inventories").glob("*.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _context_paths(payload: dict) -> set[str]:
    run_root = Path(payload["run_root"])
    by_id = {item["opaque_id"]: item["project_path"] for item in _inventory(payload)["files"]}
    selected: set[str] = set()
    for path in (run_root / "context-selections" / payload["attempt_id"]).glob("*.json"):
        text = path.read_text(encoding="utf-8")
        selected |= {project_path for opaque, project_path in by_id.items() if opaque in text}
    return selected


# --- B-8: the id of a module is the path of its folder ---------------------------------------------------------

@pytest.mark.parametrize(("root", "module_id"), [
    (".", "root"), ("api", "api"), ("services/core", "services/core"), ("Core_2-b", "Core_2-b"),
    ("my.service", "root--6d792e73657276696365"),  # a dot is the separator of the field modules.<id>.stack.language
    ("модуль", "root--" + "модуль".encode().hex()),
    ("root", "root--" + b"root".hex()),  # a folder named root is not the project folder
    ("services/my.core", "root--" + b"services".hex() + "--" + b"my.core".hex()),
])
def test_the_module_id_is_the_folder_path_and_hex_only_for_names_it_cannot_carry(root: str, module_id: str) -> None:
    from tools.module_ids import module_id_for

    assert module_id_for(root) == module_id


def test_discovery_names_the_modules_by_their_folders(tmp_path: Path) -> None:
    from tools.discover_project import discover_project

    modules = discover_project(_project(tmp_path))["modules"]
    assert [(row["id"], row["root"]) for row in modules] == [("api", "api"), ("core", "core")]


def test_an_old_skillsrc_keeps_its_hex_ids_and_is_not_rewritten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.discover_project import discover_project

    project = _project(tmp_path)
    first = _scan(project, monkeypatch, module="api")
    skillsrc = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
    for module in skillsrc["modules"]:
        module["id"] = "root--" + module["root"].encode().hex()  # what the package wrote before P6a
    old_text = json.dumps(skillsrc, ensure_ascii=False, indent=2)
    (project / ".skillsrc").write_bytes(old_text.encode("utf-8"))
    assert {row["id"] for row in discover_project(project)["modules"]} == {"root--617069", "root--636f7265"}

    for given in ("api", "root--617069"):  # the new id and the old one both name the module of the old file
        scan = _scan(project, monkeypatch, module=given)
        assert scan["scope"]["module"] == "api" and scan["run_id"] != first["run_id"]
    assert (project / ".skillsrc").read_bytes() == old_text.encode("utf-8")


def test_the_legacy_id_is_accepted_with_a_warning_that_names_the_new_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    plain = _scan(project, monkeypatch, module="api")
    assert plain["scope"]["module"] == "api" and not [w for w in plain["warnings"] if "MODULE_ID_LEGACY" in str(w)]
    legacy = _scan(project, monkeypatch, module="root--617069")
    assert legacy["scope"]["module"] == "api"
    [warning] = [w for w in legacy["warnings"] if "MODULE_ID_LEGACY" in str(w)]
    assert "--module api" in warning and "root--617069" in warning


def test_a_wrong_module_lists_the_current_ids(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    stop = _scan(project, monkeypatch, module="web", expect=2)
    assert stop["reason"] == "MODULE_UNKNOWN" and "api" in stop["detail"] and "core" in stop["detail"] and "root--" not in stop["detail"]


def test_ready_takes_the_new_and_the_old_id(tmp_path: Path) -> None:
    from tools.ready import check

    project = _project(tmp_path)
    for given, legacy in (("api", False), ("root--617069", True)):
        report = check(project, [DOC], profile="cases-only-v1", module=given)
        row = next(item for item in report["checks"] if item["id"] == "module")
        assert row["level"] == ("WARN" if legacy else "OK") and "api" in row["message"], row
        assert ("MODULE_ID_LEGACY" in row["message"]) is legacy and (not legacy or "--module api" in row["fix"]), row
        assert next(item for item in report["checks"] if item["id"] == f"docs:{DOC}")["level"] == "OK"


# --- B-2, B-16: the document of the root of the repository for any module --------------------------------------

def test_a_document_of_the_repository_root_is_read_for_a_submodule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, module="api")
    assert scan["context_summary"]["task_documents"] == [DOC]
    assert {row["project_path"] for row in _inventory(scan)["files"]} >= {DOC, "pom.xml", "api/pom.xml"}
    context = _context_paths(scan)
    assert DOC in context and "api/src/main/java/demo/api/Greeter.java" in context and not any(p.startswith("core/") for p in context)
    # the document is the task, not code: it is not among the code files
    assert scan["context_summary"]["code_files"] == len(context) - 1


def test_the_run_binds_the_outside_document_and_status_sees_no_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.project_inventory import read_execution_baseline

    project = _project(tmp_path)
    scan = _scan(project, monkeypatch, module="api")
    run_root = Path(scan["run_root"])
    [baseline_path] = (run_root / "baselines").glob("*.json")
    assert DOC in {row["project_path"] for row in read_execution_baseline(baseline_path)["inputs"]}

    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    assert run_pipeline.main(["status", "--project", str(project), "--run", scan["run_id"]]) == 0
    assert not printed[-1].get("drift"), printed[-1]

    (project / DOC).write_text("# Приветствие\n\nR01. Другое требование.\n", encoding="utf-8")  # the document changed after the scan
    printed.clear()
    assert run_pipeline.main(["status", "--project", str(project), "--run", scan["run_id"]]) == 0
    assert DOC in json.dumps(printed[-1]["drift"], ensure_ascii=False), printed[-1]


def test_a_document_outside_the_project_still_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    (tmp_path / "outside.md").write_text("# Чужой документ\n", encoding="utf-8")
    stop = _scan(project, monkeypatch, module="api", docs=["../outside.md"], expect=2)
    assert stop["status"] == "error" and "outside.md" in json.dumps(stop, ensure_ascii=False)


def test_a_secret_document_outside_the_module_stops_with_its_rule_and_a_fix_without_the_old_advice(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    (project / DOC).write_text('# Требования\n\npassword="secret123456"\n', encoding="utf-8")
    stop = _scan(project, monkeypatch, module="api", expect=2)
    assert stop["reason"] == "TASK_DOC_OUTSIDE_CONTEXT" and "token-assignment-v1" in stop["detail"] and DOC in stop["detail"]
    assert "сузьте" not in stop["fix"] and "корневой --module" not in stop["fix"]


def test_a_document_in_an_excluded_folder_names_the_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    (project / "node_modules").mkdir()
    (project / "node_modules" / "req.md").write_text("# Требования\n", encoding="utf-8")
    stop = _scan(project, monkeypatch, module="api", docs=["node_modules/req.md"], expect=2)
    assert stop["reason"] == "TASK_DOC_OUTSIDE_CONTEXT" and "dependency-directory-v1" in stop["detail"], stop


def test_ready_passes_a_document_of_the_root_for_a_submodule(tmp_path: Path) -> None:
    from tools.ready import check

    report = check(_project(tmp_path), [DOC], profile="cases-only-v1", module="api")
    assert next(item for item in report["checks"] if item["id"] == f"docs:{DOC}")["level"] == "OK"
    assert next(item for item in report["checks"] if item["id"] == "scan")["level"] in {"OK", "WARN"}
    # the pack row is the manifest of the package under test: it is regenerated at release, not by this test
    assert not [item for item in report["checks"] if item["level"] == "STOP" and item["block"] == "cases" and item["id"] != "pack"], report["checks"]


def test_ready_stops_on_a_secret_document_outside_the_module_with_its_rule(tmp_path: Path) -> None:
    from tools.ready import check

    project = _project(tmp_path)
    (project / DOC).write_text('# Требования\n\npassword="secret123456"\n', encoding="utf-8")
    row = next(item for item in check(project, [DOC], profile="cases-only-v1", module="api")["checks"] if item["id"] == f"docs:{DOC}")
    assert row["level"] == "STOP" and "token-assignment-v1" in row["message"] and "сузьте" not in row["fix"], row
