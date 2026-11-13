"""F5 (finds 8.9, 8.10, 8.11, 8.13, 8.14 of the hunt of 05.11): the first run of a tester on a project that is not like ours.

- 8.9  a module folder with Cyrillic or a space: ``ready`` and ``scan`` agree, the schema of the attempt is linear;
- 8.10 a foreign ``.skillsrc`` in JSON: every refusal names the key or the line and what to do, ``init_skillsrc`` says the same;
- 8.11 a ``.skillsrc`` that is a directory or unreadable stops the scan before a run is created;
- 8.13 the skill pack inside the project is no module;
- 8.14 a document with a BOM and ``--docs file.md#Section``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_first_run import _write
from tests.test_phase2_scan import _skillsrc
from tools import run_pipeline
from tools.init_skillsrc import _payload
from tools.module_ids import module_id_for

ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, folder: str, *, doc_text: str = "Пользователь видит список.\n") -> tuple[Path, str, str]:
    """A project whose one module lives in ``folder``; returns (project, module id, path of the requirements document)."""
    project = tmp_path / "project"
    _write(project / folder, "src/app.py", "X = 1\n")
    _write(project / folder, "pyproject.toml", "[project]\nname = 'm'\n")
    doc = f"{folder}/docs/feature.md"
    _write(project, doc, doc_text)
    document = json.loads(_skillsrc())
    module = document["modules"][0]
    module["id"], module["root"], module["detected_from"] = module_id_for(folder), folder, [f"{folder}/pyproject.toml"]
    (project / ".skillsrc").write_bytes(_payload(document))
    return project, module["id"], doc


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch, *argv: str, expect: int | None = None) -> dict:
    printed: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", printed.append)
    code = run_pipeline.main(["scan", "--project", str(project), "--profile", "cases-only-v1", "--new-run", *argv])
    monkeypatch.undo()
    if expect is not None:
        assert code == expect, printed[-1]
    return json.loads(json.dumps(printed[-1], ensure_ascii=False))


def _runs(project: Path) -> list[str]:
    root = project / ".pilot-runs"
    return sorted(path.name for path in root.iterdir()) if root.is_dir() else []


def _ready(project: Path, docs: list[str], module: str | None = None) -> dict:
    from tools import ready

    return ready.check(project, docs, "cases-only-v1", module)


def _row(report: dict, check_id: str) -> dict:
    return next(row for row in report["checks"] if row["id"] == check_id)


# --- 8.9: the folder of a module ------------------------------------------------------------------------------------

@pytest.mark.parametrize("folder", ["сервис", "my service", "services/мой модуль"])
def test_a_module_folder_with_cyrillic_or_a_space_is_ready_and_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, folder: str) -> None:
    project, module_id, doc = _project(tmp_path, folder)
    report = _ready(project, [doc], module_id)
    assert _row(report, "module")["level"] == "OK" and report["status"] == "READY", report
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
    assert payload["status"] == "ok" and payload["scope"]["module"] == folder, payload


@pytest.mark.parametrize("value", ["..", "a/../b", "a/./b", "a//b", "/abs", "a/", "C:/x", "a\x00b", "a\nb", "", "../x"])
def test_the_code_refuses_a_module_path_the_schema_would_let_through(tmp_path: Path, value: str) -> None:
    from tools.pilot_state import module_path_problem

    (tmp_path / "a").mkdir()
    assert module_path_problem(tmp_path, value), value


def test_module_path_problem_names_a_missing_folder_and_accepts_an_existing_one(tmp_path: Path) -> None:
    from tools.pilot_state import module_path_problem

    (tmp_path / "сервис").mkdir()
    assert module_path_problem(tmp_path, "сервис") is None and module_path_problem(tmp_path, ".") is None
    assert "нет" in str(module_path_problem(tmp_path, "нет-такой"))


@pytest.mark.parametrize("value", [
    pytest.param(" " * 100_000, id="spaces"), pytest.param("a/" * 50_000, id="a-slash-ends-with-slash"),
    pytest.param("a/" * 49_999 + "a", id="a-slash-valid"), pytest.param("я" * 100_000, id="cyrillic"),
    pytest.param("a/" * 49_999 + "/", id="double-slash-at-the-end")])
def test_the_module_pattern_of_the_attempt_schema_is_linear(value: str) -> None:
    from tools.schema_validation import schema_diagnostics

    attempt = {"schema_version": "1.0.0", "run_id": "0" * 32, "attempt_id": "1" * 32, "project": "p", "module": value,
               "policy_profile": "cases-only-v1", "baseline_digest": "sha256:" + "0" * 64, "digest": "sha256:" + "0" * 64}
    started = time.monotonic()
    schema_diagnostics(attempt, ROOT / "schemas" / "attempt.schema.json", ROOT)
    assert time.monotonic() - started < 0.5


def test_the_module_pattern_takes_unicode_and_spaces_and_not_separators() -> None:
    from tools.schema_validation import schema_diagnostics

    def bad(module: str) -> bool:
        attempt = {"schema_version": "1.0.0", "run_id": "0" * 32, "attempt_id": "1" * 32, "project": "p", "module": module,
                   "policy_profile": "cases-only-v1", "baseline_digest": "sha256:" + "0" * 64, "digest": "sha256:" + "0" * 64}
        return any(row["path"] == "/module" for row in schema_diagnostics(attempt, ROOT / "schemas" / "attempt.schema.json", ROOT))

    assert not any(bad(value) for value in (".", "api", "сервис", "my service", "services/мой модуль", "a.b/c-d_e"))
    assert all(bad(value) for value in ("", "a/", "/a", "a//b", "a\\b", "C:/x", "a\x01b"))


def test_a_schema_failure_of_the_attempt_names_its_cause_and_a_fix_without_project_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    project, module_id, doc = _project(tmp_path, "svc")

    def refuse(*_a, **_k):
        raise ValueError("schema validation failed: attempt.schema.json")

    monkeypatch.setattr(pilot_state, "create_attempt", refuse)
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=2)
    assert payload["reason"] == "INVENTORY_INVALID" and "attempt.schema.json" in payload["detail"] and payload["fix"], payload

    def leak(*_a, **_k):
        raise ValueError("пароль админа: 12345 в файле docs/feature.md")

    monkeypatch.setattr(pilot_state, "create_attempt", leak)
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=2)
    assert payload["reason"] == "INVENTORY_INVALID" and "12345" not in json.dumps(payload, ensure_ascii=False) and payload["fix"], payload


def test_module_unknown_does_not_say_that_the_folder_is_no_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _module_id, doc = _project(tmp_path, "svc")
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", "nope", expect=2)
    assert payload["reason"] == "MODULE_UNKNOWN" and "не папка" not in payload["detail"], payload
    assert "svc" in payload["detail"] and payload["fix"] == "--module svc"


# --- 8.10 / 8.11: a foreign .skillsrc -----------------------------------------------------------------------------------

def _foreign(project: Path, raw: bytes) -> None:
    (project / ".skillsrc").write_bytes(raw)


def _extra_key_skillsrc() -> bytes:
    document = json.loads(_skillsrc())
    document["bogus_key"] = 1
    return json.dumps(document).encode("utf-8")


FOREIGN = {
    "empty": (b"", "пуст"),
    "crlf": (b"\r\n", "пуст"),
    "duplicate": (b'{"version": "3.0", "version": "3.0"}', "version"),
    "extra_key": (_extra_key_skillsrc(), "bogus_key"),
    "array": (b"[1]\n", "объект"),
}


@pytest.mark.parametrize("case", sorted(FOREIGN))
def test_scan_of_a_foreign_skillsrc_names_the_key_and_the_way_out_and_creates_no_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    raw, named = FOREIGN[case]
    project, _module_id, doc = _project(tmp_path, "svc")
    _foreign(project, raw)
    payload = _scan(project, monkeypatch, "--docs", doc, expect=2)
    text = json.dumps(payload, ensure_ascii=False)
    assert payload["status"] == "error" and payload["fix"] and named in text, payload
    assert "run_id" not in payload and _runs(project) == [], (payload, _runs(project))
    assert "tools.init_skillsrc" in payload["fix"] or ".skillsrc" in payload["fix"]


@pytest.mark.parametrize("case", sorted(FOREIGN))
def test_ready_and_init_skillsrc_say_the_same_for_a_foreign_skillsrc(tmp_path: Path, case: str) -> None:
    raw, named = FOREIGN[case]
    project, _module_id, doc = _project(tmp_path, "svc")
    _foreign(project, raw)
    row = _row(_ready(project, [doc]), "skillsrc")
    assert row["level"] == "STOP" and row["fix"] and named in row["message"] + row["fix"], row
    assert "--write" not in row["fix"].split("python", 1)[-1].split("-m", 1)[0]
    done = subprocess.run([sys.executable, "-m", "tools.init_skillsrc", "--project", str(project)], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"})
    out = json.loads(done.stdout)
    assert done.returncode == 2 and out["fix"] and named in out["message"] + out["fix"], out
    assert (project / ".skillsrc").read_bytes() == raw


def test_a_skillsrc_that_is_a_directory_stops_the_scan_before_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _module_id, doc = _project(tmp_path, "svc")
    (project / ".skillsrc").unlink()
    (project / ".skillsrc").mkdir()
    printed: list[str] = []
    payload = _scan(project, monkeypatch, "--docs", doc, expect=2)
    assert payload["reason"] == "SKILLSRC_UNREADABLE" and payload["fix"] and "run_id" not in payload, payload
    assert _runs(project) == [] and not printed
    row = _row(_ready(project, [doc]), "skillsrc")
    assert row["level"] == "STOP" and ".skillsrc" in row["fix"], row


@pytest.mark.skipif(os.name != "nt", reason="junction")
def test_a_skillsrc_that_is_a_junction_stops_the_scan_before_a_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, _module_id, doc = _project(tmp_path, "svc")
    (project / ".skillsrc").unlink()
    target = tmp_path / "elsewhere"
    target.mkdir()
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(project / ".skillsrc"), str(target)], capture_output=True)
    assert made.returncode == 0
    payload = _scan(project, monkeypatch, "--docs", doc, expect=2)
    assert payload["reason"] == "SKILLSRC_UNREADABLE" and _runs(project) == [], payload


# --- 8.13: the skill pack inside the project ----------------------------------------------------------------------------

def test_the_skill_pack_inside_a_project_without_skillsrc_is_no_module(tmp_path: Path) -> None:
    from tools.discover_project import discover_project

    project = tmp_path / "project"
    _write(project, "svc/pyproject.toml", "[project]\nname = 'm'\n")
    _write(project, "svc/src/app.py", "X = 1\n")
    pack = project / "tools-pack"
    for rel in ("requirements.txt", "tools/__init__.py", "contracts/pipeline.json"):
        _write(pack, rel, "{}\n" if rel.endswith(".json") else "x\n")
    _write(pack, "release/manifest.json", "{}\n")
    roots = [row["root"] for row in discover_project(project, skill_pack_root=pack)["modules"]]
    assert "tools-pack" not in roots and "svc" in roots, roots
    assert "tools-pack" in [row["root"] for row in discover_project(project)["modules"]]


# --- 8.14: a BOM in the document ---------------------------------------------------------------------------------------

def test_a_document_with_a_bom_finds_its_section_in_ready_and_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module_id, doc = _project(tmp_path, "svc", doc_text="# Заголовок\n\nТекст.\n\n## Раздел один\n\nПользователь видит список.\n")
    path = project / doc
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    spec = f"{doc}#Заголовок"
    report = _ready(project, [spec], module_id)
    assert _row(report, f"docs:{spec}")["level"] == "OK", _row(report, f"docs:{spec}")
    payload = _scan(project, monkeypatch, "--docs", spec, "--module", module_id, expect=0)
    assert payload["status"] == "ok"
    sub = f"{doc}#Раздел один"
    assert _scan(project, monkeypatch, "--docs", sub, "--module", module_id, expect=0)["status"] == "ok"
