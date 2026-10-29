"""FaT-DOC: a project of requirement documents only (no build manifest, no source file) is one module `documents` at the root —
cases-only-v1 reaches the context-marker request; local-pilot-v1 is refused, not lowered; projects with a build are untouched.

The stand: live/verify-20261029/doc-only — docs/*.md only; ready cases-only-v1 stopped at "build manifests were not found"."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

DOC = "docs/portable-owner-lifecycle-requirements.md"
EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle"


def _documents_project(tmp_path: Path) -> Path:
    project = tmp_path / "doc-only"
    (project / "docs").mkdir(parents=True)
    (project / DOC).write_text("# Требования\n\nВладелец питомца: имя, фамилия, адрес, город, телефон из 10 цифр.\n", encoding="utf-8")
    (project / "docs" / "notes.txt").write_text("Заметки аналитика.\n", encoding="utf-8")
    return project


def test_a_project_of_documents_is_one_documents_module(tmp_path: Path) -> None:
    from tools.discover_project import discover_project

    report = discover_project(_documents_project(tmp_path))
    assert report["status"] == "ready" and report["errors"] == [] and report["questions"] == []
    [module] = report["modules"]
    assert module == {"id": "root", "root": ".", "stack": {"language": "documents"}, "readiness": "source_ready",
                      "detected_from": ["docs/notes.txt", DOC]}


@pytest.mark.parametrize("extra", ["scripts/build.py", "src/App.java", None])
def test_a_source_file_or_no_document_keeps_the_old_refusal(tmp_path: Path, extra: str | None) -> None:
    from tools.discover_project import discover_project

    project = _documents_project(tmp_path)
    if extra is None:
        shutil.rmtree(project / "docs")
        (project / "data.csv").write_text("a,b\n", encoding="utf-8")
    else:
        (project / extra).parent.mkdir(parents=True, exist_ok=True)
        (project / extra).write_text("x = 1\n", encoding="utf-8")
    assert discover_project(project)["errors"] == ["build manifests were not found"]


def _run(module, capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    capsys.readouterr()
    code = module.main(list(argv))
    out = capsys.readouterr().out
    return code, json.loads(out[out.index("{"):out.rindex("}") + 1])


def test_cases_only_reaches_the_context_marker_request(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import ready, run_pipeline

    project = _documents_project(tmp_path)
    code, report = _run(ready, capsys, "--project", str(project), "--profile", "cases-only-v1", "--docs", DOC)
    assert code == 0 and report["status"] == "READY", [row for row in report["checks"] if row["level"] != "OK"]
    code, scanned = _run(run_pipeline, capsys, "scan", "--project", str(project), "--profile", "cases-only-v1", "--docs", DOC)
    assert code == 0 and scanned["status"] == "ok" and scanned["policy_profile"] == "cases-only-v1", scanned
    assert scanned["next_expected_artifact"] == "context-marker response" and scanned["attempt_state"] == "ACTIVE"
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    [module] = normalize_skillsrc(load_skillsrc(project / ".skillsrc"))["modules"]
    assert module["stack"] == {"language": "documents"} and "test" not in module


def test_local_pilot_without_a_build_is_refused_not_lowered(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import ready, run_pipeline

    project = _documents_project(tmp_path)
    code, scanned = _run(run_pipeline, capsys, "scan", "--project", str(project), "--profile", "local-pilot-v1", "--docs", DOC)
    assert code == 2 and scanned["reason"] == "NO_BUILD_MODULE" and "--profile cases-only-v1" in scanned["fix"], scanned
    assert not (project / ".skillsrc").exists() and not (project / ".pilot-runs").exists()
    code, report = _run(ready, capsys, "--project", str(project), "--profile", "local-pilot-v1", "--docs", DOC)
    assert report["status"] == "BLOCKED"
    assert [row["id"] for row in report["checks"] if row["level"] == "STOP"] == ["execution:build"]


@pytest.mark.parametrize("fixture", ["python", "maven"])
def test_a_project_with_a_build_gets_the_same_skillsrc_and_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixture: str) -> None:
    """Build projects never reach the documents path: the same discovery, the same .skillsrc bytes, the same module."""
    from tools import discover_project as discovery
    from tools.init_skillsrc import ensure_skillsrc

    if fixture == "python":
        project = tmp_path / "python-pytest"
        shutil.copytree(Path(__file__).parent / "fixtures" / "projects" / "python-pytest", project)
    else:
        project = tmp_path / "maven"
        (project / "src/main/java/x").mkdir(parents=True)
        (project / "src/test/java/x").mkdir(parents=True)
        (project / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion><groupId>x</groupId><artifactId>m</artifactId>"
                                         "<version>1</version><dependencies><dependency><groupId>org.junit.jupiter</groupId>"
                                         "<artifactId>junit-jupiter</artifactId><version>5.10.2</version><scope>test</scope>"
                                         "</dependency></dependencies></project>", encoding="utf-8")
        (project / "src/main/java/x/App.java").write_text("package x;\nclass App {}\n", encoding="utf-8")
    (project / "docs").mkdir(exist_ok=True)
    (project / DOC).write_text("# Требования\n", encoding="utf-8")
    (project / ".skillsrc").unlink(missing_ok=True)  # discovery writes it from the project
    before = ensure_skillsrc(project, {}, write=True)
    written = (project / ".skillsrc").read_bytes()
    (project / ".skillsrc").unlink()

    def never(_root):
        raise AssertionError("a project with a build manifest took the documents path")

    monkeypatch.setattr(discovery, "_documents_module", never)
    after = ensure_skillsrc(project, {}, write=True)
    assert (project / ".skillsrc").read_bytes() == written
    assert [module["id"] for module in after.get("modules", [])] == [module["id"] for module in before.get("modules", [])]
    assert all(module["stack"].get("language") != "documents" for module in after.get("modules", []))


def test_the_orchestrate_skill_describes_the_documents_mode_as_the_code_does() -> None:
    """FaT-DS: the SKILL names the module, the profile, the refusal and the expected outcome — each one the code's own."""
    root = Path(__file__).resolve().parents[1]
    skill = (root / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")
    start = skill.index("Проект только с документами (FaT-DOC)")
    paragraph = skill[start:skill.index("\n\n", start)]
    for fact in ("`root`", "«.»", "`stack.language: documents`", "`cases-only-v1`", "`NO_BUILD_MODULE`", "`manual_only`",
                 "`coverage: MANUAL_ONLY`", "`accepted: false`"):
        assert fact in paragraph, fact
    discover = (root / "tools" / "discover_project.py").read_text(encoding="utf-8")
    pipeline = (root / "tools" / "run_pipeline.py").read_text(encoding="utf-8")
    assert '"id": "root", "root": ".", "stack": {"language": "documents"}' in discover
    assert 'HostStop("NO_BUILD_MODULE"' in pipeline
