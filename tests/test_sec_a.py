"""SEC-A: the paths and the commands the agent names stay where they belong, in both deliveries.

А1 — ``--output-dir`` is ``<run>/published`` or below it. А2 — a path the package reads is checked before it is opened: no UNC or device
path, no ``..``, inside the project, no link on the way. А3 — every value the code puts into a command the agent runs as it is passes
``command_token`` (``COMMAND_NOT_PRINTABLE``; scan refuses before the run is written). А4 — ``--out`` is written through confined_output."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_output_guard import _api, _link, _run
from tools.output_guard import OutputRefused
from tools.run_pipeline import HostStop

pytestmark = pytest.mark.contract

UNC = "\\\\pg1-no-such-host.invalid\\share\\x.md"


def _outside(tmp_path: Path) -> Path:
    """A JSON file next to the project, outside it."""
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    return outside


# ---- А1: --output-dir --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [".", "test-cases", ".claude", ".github/workflows", ".tools/test-skills", "{run}", "{run}/tmp", "{other}/published"])
def test_output_dir_outside_the_published_directory_of_the_run_is_refused(tmp_path: Path, name: str) -> None:
    from tools.output_guard import run_output_directory

    project, run_root, _attempt = _run(tmp_path)
    path = name.format(run=run_root.relative_to(project).as_posix(), other=".pilot-runs/" + "f" * 32)
    with pytest.raises(OutputRefused) as refused:
        run_output_directory(project / path, run_root)
    assert refused.value.code == "OUTPUT_DIR_OUTSIDE_RUN" and "<run>/published" in refused.value.fix


def test_output_dir_in_the_published_directory_of_the_run_is_taken(tmp_path: Path) -> None:
    from tools.output_guard import run_output_directory

    _project, run_root, _attempt = _run(tmp_path)
    for name in ("published", "published/r2"):
        assert run_output_directory(run_root / name, run_root) == (run_root / name).resolve()


# ---- А2: the paths the package reads ----------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["../outside.md", "{outside}", UNC, "//pg1-no-such-host.invalid/share/x.md", "docs/../../outside.md", "C:x.md", "/x.md"])
def test_a_context_file_outside_the_project_is_refused_before_it_is_read(tmp_path: Path, value: str) -> None:
    from tools.batch_assembly import _context_documents

    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# Чужой\n", encoding="utf-8")
    receipt = {"files": [{"project_path": value.format(outside=outside), "content_digest": "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest()}]}
    with pytest.raises(OutputRefused) as refused:
        _context_documents(project, receipt)
    assert refused.value.code == "INPUT_OUTSIDE_PROJECT" and refused.value.fix


def test_a_context_file_through_a_link_out_of_the_project_is_refused(tmp_path: Path) -> None:
    from tools.batch_assembly import _context_documents

    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# Чужой\n", encoding="utf-8")
    _link(project / "docs" / "linked.md", outside)
    receipt = {"files": [{"project_path": "docs/linked.md", "content_digest": "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest()}]}
    with pytest.raises(OutputRefused):
        _context_documents(project, receipt)


def test_step_size_refuses_a_file_outside_the_project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import step_size

    project = tmp_path / "project"
    project.mkdir()
    _outside(tmp_path)
    assert step_size.main(["--project", str(project), "--files", "../outside.json"]) == 2
    printed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert printed["code"] == "INPUT_OUTSIDE_PROJECT", printed


@pytest.mark.parametrize("value", ["{outside}", "{project}/../outside.json"])  # a share: refused while the arguments are read (D2)
def test_an_api_json_input_outside_the_project_of_the_run_is_refused(tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]) -> None:
    project, run_root, attempt_id = _run(tmp_path / "p")
    path = value.format(outside=_outside(tmp_path), project=project)
    for argv in (["validate-canonical", "--run-root", str(run_root), "--document", path],
                 ["model-response", "--run-root", str(run_root), "--attempt", attempt_id, "--stage", "context-marker:baseline", "--artifact", path]):
        code, refused = _api(argv, capsys)
        assert code == 2 and refused["code"] == "INPUT_OUTSIDE_PROJECT" and refused["fix"], (argv[0], refused)


def test_an_api_json_input_without_a_run_is_read_only_inside_a_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project, run_root, _attempt = _run(tmp_path / "p")
    inside = run_root / "tmp" / "document.json"
    inside.parent.mkdir(exist_ok=True)
    inside.write_text("{}", encoding="utf-8")
    code, refused = _api(["relations-digest", "--relations", str(_outside(tmp_path))], capsys)
    assert code == 2 and refused["code"] == "INPUT_OUTSIDE_PROJECT", refused
    code, line = _api(["relations-digest", "--relations", str(inside)], capsys)
    assert code == 0, line


# ---- А3: the values in the commands the agent runs as they are --------------------------------------------------

@pytest.mark.parametrize("token", ["docs/ТЗ.md#Раздел 3 — Требования", "#Шаг 1 – вход", "#Модуль «Оплата»", "ТЗ №5.md", "#Что нового?"])
def test_russian_typography_of_a_heading_prints_in_double_quotes(token: str) -> None:
    from tools.run_pipeline import command_token

    assert command_token(token) == f'"{token}"'


@pytest.mark.parametrize("token", ["#Модуль “Оплата”", "#Важно!"])
def test_a_typographic_quote_or_an_exclamation_mark_is_not_printed(token: str) -> None:
    from tools.run_pipeline import command_token

    with pytest.raises(HostStop) as refused:
        command_token(token)
    assert refused.value.code == "COMMAND_NOT_PRINTABLE" and "#Раздел" in refused.value.fix


def _builders():
    from tools import ready, run_pipeline, scope_pause

    return {
        "scan": lambda bad: run_pipeline._scan_command(Path("project"), {"docs": [bad]}),
        "fix": lambda bad: run_pipeline._exact_fix("python -m tools.run_pipeline status --project <project>", Path(bad), Path("run"), "a" * 32),
        "self-review": lambda bad: ready._self_review_scan(Path(bad), [], "cases-only-v1", None),
    }


@pytest.mark.parametrize("name", ["scan", "fix", "self-review"])
def test_a_value_that_cannot_be_printed_is_refused_in_a_command_the_agent_runs(name: str) -> None:
    with pytest.raises(HostStop) as refused:
        _builders()[name]("a$b")
    assert refused.value.code == "COMMAND_NOT_PRINTABLE"


def test_scan_refuses_a_document_whose_path_cannot_be_printed_before_the_run_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_documents_only import _documents_project, _run as _main
    from tools import run_pipeline

    project = _documents_project(tmp_path)
    (project / "docs" / "a$b.md").write_text("# Требования\n\nИмя владельца обязательно.\n", encoding="utf-8")
    code, refused = _main(run_pipeline, capsys, "scan", "--project", str(project), "--profile", "cases-only-v1", "--docs", "docs/a$b.md")
    assert code == 2 and "COMMAND_NOT_PRINTABLE" in json.dumps(refused), refused
    assert not (project / ".pilot-runs").exists() and not (project / ".skillsrc").exists()


# ---- D2: a share path is refused before anything touches it -----------------------------------------------------

SHARE = r"\\pg1-no-such-host.invalid\share"


def _share_touches(monkeypatch: pytest.MonkeyPatch, call) -> list[str]:
    """Run *call* with Path.resolve and Path.stat watching for a share path: the paths they were asked about (none must reach the network)."""
    import pathlib

    touched: list[str] = []
    real_resolve, real_stat = pathlib.Path.resolve, pathlib.Path.stat

    def watch(real):
        def method(self, *args, **kwargs):
            if str(self).replace("/", "\\").startswith(("\\\\", "\\??\\")):
                touched.append(str(self))
                raise OSError("a share path was touched")
            return real(self, *args, **kwargs)
        return method

    with monkeypatch.context() as patched:
        patched.setattr(pathlib.Path, "resolve", watch(real_resolve))
        patched.setattr(pathlib.Path, "stat", watch(real_stat))
        call()
    return touched


def test_scan_refuses_a_document_on_a_share_before_it_touches_it(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_documents_only import _documents_project
    from tools import run_pipeline

    project = _documents_project(tmp_path)
    stopped: list = []

    def call() -> None:
        try:
            run_pipeline.main(["scan", "--project", str(project), "--profile", "cases-only-v1", "--docs", SHARE + r"\x.md"])
        except SystemExit as stop:
            stopped.append(stop.code)

    touched = _share_touches(monkeypatch, call)
    assert touched == [] and stopped == [2] and "путь UNC" in capsys.readouterr().out and not (project / ".pilot-runs").exists(), touched


def test_a_document_path_on_a_share_from_a_file_is_refused_by_scan_before_it_touches_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second layer, for a path that does not come from the command line (the request of a run, a reference)."""
    from tools.run_pipeline import _docs_entries

    refused: list = []

    def call() -> None:
        try:
            _docs_entries(tmp_path, [SHARE + r"\x.md#Раздел"])
        except HostStop as stop:
            refused.append((stop.code, stop.fix))

    assert _share_touches(monkeypatch, call) == [] and refused and refused[0][0] == "SNAPSHOT_DOCS" and "UNC" in refused[0][1], refused


UNC_ARGV = {  # every CLI of tools/: one flag on a share, the other required ones local
    "api": ["validate-canonical", "--document", "//pg1-no-such-host.invalid/share/d.json"],
    "autotest_hints": ["--automation", SHARE + r"\a.json"],
    "build_context": ["--project", SHARE, "--docs", "x.md"],
    "ci_gate": ["--root", SHARE],
    "contract_check": ["--root", SHARE],
    "doctor": ["--root", SHARE],
    "exec_commands": ["ingest", "--project", "p", "--run", "r", "--reports", SHARE + r"\reports"],  # its commands are run_pipeline's
    "init_skillsrc": ["--project", SHARE],
    "ready": ["--project", "p", "--docs", SHARE + r"\x.md"],
    "release_manifest": ["--root", SHARE],
    "render_contract_docs": ["--root", SHARE],
    "review_edits": ["--candidate", "c.json", "--edits", "//pg1-no-such-host.invalid/share/e.json"],
    "review_legacy": ["publish", "--candidate", "c.json", "--output-dir", SHARE, "--run-root", "r", "--attempt-id", "a"],
    "review_view": ["--project", "p", "--run", "r", "--candidate", SHARE + r"\c.json"],
    "run_pipeline": ["scan", "--profile", "cases-only-v1", "--docs", "x.md", "--project", SHARE + r"\p"],
    "step_size": ["--project", "p", "--files", "a.md", SHARE + r"\b.md"],
    "validate_artifact": [SHARE + r"\schema.json", "a.json"],
    "zephyr_template_export": ["--project", "p", "--run", "r", "--cases", SHARE + r"\c.json"],
}


CLI_OF = {"exec_commands": "run_pipeline"}  # a module that adds its commands to another CLI


NT_SHARES = [r"\??\UNC\pg1-no-such-host.invalid\share", "/??/UNC/pg1-no-such-host.invalid/share"]  # D3: the prefix of an NT path


@pytest.mark.parametrize("share", [None, *NT_SHARES])
@pytest.mark.parametrize("name", sorted(UNC_ARGV))
def test_a_share_path_in_any_flag_of_any_cli_is_refused_when_the_arguments_are_read(name: str, share: str | None, capsys: pytest.CaptureFixture[str],
                                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib
    import inspect
    import sys

    module = importlib.import_module(f"tools.{CLI_OF.get(name, name)}")
    argv = [item.replace(SHARE, share).replace("//pg1-no-such-host.invalid/share", share) if share else item for item in UNC_ARGV[name]]
    assert share is None or any(item.startswith(share) for item in argv), argv
    stopped: list = []

    def call() -> None:
        try:
            if inspect.signature(module.main).parameters:
                module.main(argv)
            else:  # a main that reads sys.argv
                monkeypatch.setattr(sys, "argv", [name, *argv])
                module.main()
        except SystemExit as stop:
            stopped.append(stop.code)

    touched = _share_touches(monkeypatch, call)
    printed = capsys.readouterr().out
    assert touched == [] and stopped == [2] and "путь UNC или путь устройства не принимается" in printed, (touched, stopped, printed[-400:])


def test_every_cli_of_tools_builds_its_parser_from_the_json_parser() -> None:
    """The rule of the share lives in JsonArgumentParser: a new CLI with a bare argparse parser would pass by it."""
    import ast

    tools = Path(__file__).resolve().parents[1] / "tools"
    bare = []
    for path in sorted(tools.glob("*.py")):
        if path.name == "json_cli.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            named = node.func if isinstance(node, ast.Call) else node.bases[0] if isinstance(node, ast.ClassDef) and node.bases else None
            if isinstance(named, (ast.Attribute, ast.Name)) and getattr(named, "attr", getattr(named, "id", None)) == "ArgumentParser":
                bare.append(f"{path.name}:{node.lineno}")
    assert bare == [], bare
    assert set(UNC_ARGV) == {path.stem for path in tools.glob("*.py") if "add_argument(" in path.read_text(encoding="utf-8")}  # every CLI is in the table


# ---- А4: --out ---------------------------------------------------------------------------------------------------

def test_out_is_not_written_through_a_planted_link_of_its_temporary_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project, run_root, _attempt = _run(tmp_path / "p")
    outside = tmp_path / "outside.json"
    outside.write_text("keep", encoding="utf-8")
    (run_root / "tmp").mkdir(exist_ok=True)
    source = run_root / "tmp" / "document.json"
    source.write_text("{}", encoding="utf-8")
    _link(run_root / "tmp" / "digest.json.tmp", outside)
    out = run_root / "tmp" / "digest.json"
    code, line = _api(["relations-digest", "--relations", str(source), "--out", str(out), "--run-root", str(run_root)], capsys)
    assert code == 0 and outside.read_text(encoding="utf-8") == "keep" and json.loads(out.read_text(encoding="utf-8")), line
