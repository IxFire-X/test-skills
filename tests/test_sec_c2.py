"""SEC-C2: two classes, each with a test that lists its inputs itself.

K-a: a path that comes from outside — a flag, a field of a file on disk — reaches ``open/stat/resolve`` only after one function of
``output_guard`` judged its text (and, where the command has a project or a run, its place).  K-v: the package writes only below
the run of the command (``<project>/.pilot-runs/<hex>/``) and only through ``confined_output``; a ``--run`` that is no run name
writes nothing anywhere.

The registries below are exact: a new path flag, a new reader of a path field or a new way to write that is not listed fails here.
"""
from __future__ import annotations

import ast
import builtins
import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"

# --- K-a: the path flags --------------------------------------------------------------------------------------------------

_PATHISH = re.compile(r"(?<!pro)file|path|dir|root|out|candidate|edits|answers|approval|automation|cases|docs|reference|project|artifact|"
                      r"requirements|grouping|header|evidence|schema|run$|input")
# module -> {flag: how the value is judged}.  "parser": the text only (``JsonArgumentParser`` refuses a share/device value);
# "guard": ``output_guard`` before the first I/O; "run": a run name, judged as a name; "maintainer": the root of the package's own
# checkout, given by the person who runs a maintainer command, never by an agent.
PATH_FLAGS = {
    "api.py": {"--run-root": "guard"},
    "autotest_hints.py": {"--automation": "guard"},
    "build_context.py": {"--project": "parser", "--docs": "guard"},
    "ci_gate.py": {"--root": "maintainer"},
    "contract_check.py": {"--root": "maintainer"},
    "doctor.py": {"--root": "maintainer"},
    "exec_commands.py": {"--run": "run"},
    "init_skillsrc.py": {"--project": "parser", "--answers": "guard", "--replacement-approval": "guard",
                         "--immutable-evidence-dir": "guard", "--output": "confined"},
    "ready.py": {"--project": "parser", "--docs": "guard"},
    "release_manifest.py": {"--root": "maintainer"},
    "render_contract_docs.py": {"--root": "maintainer"},
    "review_edits.py": {"--candidate": "guard", "--edits": "guard"},
    "review_view.py": {"--project": "parser", "--run": "run", "--candidate": "guard"},
    "run_pipeline.py": {"--cases": "guard", "--reference": "guard", "--new-run": "run", "--run": "run",
                        "--project": "parser", "--docs": "guard"},
    "step_size.py": {"--project": "parser", "--files": "guard", "--run": "run"},
    "validate_artifact.py": {"schema_path": "guard", "artifact_path": "guard"},
    "zephyr_template_export.py": {"--project": "parser", "--run": "run", "--cases": "guard"},
}


def _path_flags() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(TOOLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                flags = [item.value for item in node.args if isinstance(item, ast.Constant) and isinstance(item.value, str)]
                typed = any(keyword.arg == "type" and ast.unparse(keyword.value) == "Path" for keyword in node.keywords)
                if typed or any(_PATHISH.search(flag) for flag in flags):
                    found.setdefault(path.name, set()).update(flags)
    return found


def test_the_path_flags_of_every_parser_are_the_registered_ones() -> None:
    assert _path_flags() == {name: set(flags) for name, flags in PATH_FLAGS.items()}


# the file flags of the commands of ``tools.api``, read by ``_read_inputs`` (``output_guard.input_file``) or judged by their text and
# anchor (``--project``, ``--run-root``: ``output_guard.existing_run``)
_API_FILE_FLAGS = {"--document"}


def test_the_file_flags_of_the_api_commands_are_the_registered_ones() -> None:
    found: set[str] = set()
    for node in ast.walk(ast.parse((TOOLS / "api.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Tuple) and len(node.elts) >= 2 and isinstance(node.elts[0], ast.Constant)                 and isinstance(node.elts[0].value, str) and node.elts[0].value.startswith("--")                 and isinstance(node.elts[1], ast.Name) and node.elts[1].id in {"Path", "_json"}:
            found.add(node.elts[0].value)
    assert found == _API_FILE_FLAGS


# --- K-a: the path fields read from files -----------------------------------------------------------------------------------

# module -> the fields of a file on disk (a receipt, an answer of the model, ``zephyr-import.json``) that name a file.  A reader of
# such a field outside this table fails the test below.  ``project_path`` of an inventory row or of a context receipt names a file of
# the scanned project: the row is bound by its digest to the journal, and a reader that opens it goes through ``output_guard.project_file``
# (``batch_assembly._context_documents``, ``step_size``) or through the inventory (``confined_files``); ``template_path`` is the field of
# ``zephyr-import.json`` that no journal event binds: ``output_guard.project_file`` before the digest is taken.
PATH_FIELDS = {
    "batch_assembly.py": {"project_path"}, "cases_import.py": {"project_path"}, "existing_tests.py": {"project_path"},
    "generated_delta.py": {"project_path"}, "orchestrate_test_case_revision.py": {"project_path"}, "pilot_state.py": {"project_path"},
    "project_inventory.py": {"project_path"}, "ready.py": {"project_path"}, "run_pipeline.py": {"project_path"},
    "run_report.py": {"template_path"}, "scan_rules.py": {"project_path"}, "step_size.py": {"project_path"},
}
_FIELD_NAMES = {"template_path", "project_path"}


def test_a_path_field_of_a_file_is_read_only_where_the_registry_says() -> None:
    readers: dict[str, set[str]] = {}
    for path in sorted(TOOLS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            key = None
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                key = node.slice.value
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args                     and isinstance(node.args[0], ast.Constant):
                key = node.args[0].value
            if key in _FIELD_NAMES:
                readers.setdefault(path.name, set()).add(key)
    assert readers == PATH_FIELDS


# --- K-a: hostile values, recorded I/O --------------------------------------------------------------------------------------

# The host name is unique: ``host`` alone is in ``/opt/hostedtoolcache`` of a CI runner, and argparse looks there for its messages.  The
# text rules (share, device, NUL, ``..``) are the same on every OS; "a place outside the project" is not: ``C:\вне\x`` is a drive path
# on Windows and a plain relative name inside the project on POSIX, so there it is ``/вне/x``.  ``..`` is no marker: a path of the runner has it.
OUTSIDE = r"C:\вне\x" if os.name == "nt" else "/вне/x"
HOSTILE = [r"\\evil-host-c2\x", "//evil-host-c2/x", r"\\?\UNC\h\x", r"\??\UNC\h\x", "/??/UNC/h/x", r"..\..\вне\x", OUTSIDE]
_MARKERS = ("evil-host-c2", "UNC", "??", "вне")
# a run given as a path into another project: the way out differs by OS; as a name it is no run on any
FOREIGN_RUN = os.sep.join(["..", "..", "other", ".pilot-runs", "{hex}"])
RUN_AS_ABSOLUTE = (r"C:\x\.pilot-runs" + "\\" if os.name == "nt" else "/x/.pilot-runs/") + "0" * 32


@contextlib.contextmanager
def recorded(monkeypatch: pytest.MonkeyPatch):
    """Every ``os.stat/lstat/open/realpath``, ``open`` and ``Path.resolve`` on a path with a hostile marker is written down."""
    calls: list[tuple[str, str]] = []

    def watch(owner, name: str, label: str) -> None:
        original = getattr(owner, name)

        def inner(*args, **kwargs):
            for item in args[:1]:
                text = os.fspath(item) if isinstance(item, (str, os.PathLike)) else ""
                if any(marker in text for marker in _MARKERS):
                    calls.append((label, text))
            return original(*args, **kwargs)

        monkeypatch.setattr(owner, name, inner)

    with monkeypatch.context() as patch:
        monkeypatch = patch  # noqa: F841 - the patches below end with this context
        for owner, name, label in ((os, "stat", "os.stat"), (os, "lstat", "os.lstat"), (builtins, "open", "open"), (io, "open", "io.open"),
                                   (os.path, "realpath", "realpath")):
            watch(owner, name, label)
        original_resolve = Path.resolve

        def resolve(self, *args, **kwargs):
            if any(marker in str(self) for marker in _MARKERS):
                calls.append(("Path.resolve", str(self)))
            return original_resolve(self, *args, **kwargs)

        patch.setattr(Path, "resolve", resolve)
        yield calls


def _project_with_run(tmp_path: Path):
    from tests.test_self_review import _run

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    return tmp_path, run_root, attempt_id


@pytest.mark.parametrize("value", HOSTILE)
def test_h1_a_template_path_of_zephyr_import_json_is_judged_before_it_is_opened(tmp_path, monkeypatch, value) -> None:
    from tools.run_report import _zephyr_import

    _project, run_root, _attempt = _project_with_run(tmp_path)
    (run_root / "zephyr-import.json").write_text(json.dumps({"template_path": value, "template_digest": "sha256:" + "0" * 64,
                                                             "columns_added": [], "rows": 0}), encoding="utf-8")
    with recorded(monkeypatch) as calls:
        assert _zephyr_import(run_root) is None
    assert calls == []


@pytest.mark.parametrize("value", HOSTILE)
def test_h2_a_generated_file_path_of_the_automation_is_judged_before_it_is_sized(tmp_path, monkeypatch, value) -> None:
    from tools.run_tests import _runner_input_size

    artifact = {"artifacts": {"generated_files": [{"path": value}]}}
    with recorded(monkeypatch) as calls:
        _runner_input_size(tmp_path, artifact, True)
    assert calls == []


@pytest.mark.parametrize("value", [*HOSTILE, "x", RUN_AS_ABSOLUTE])
def test_h4_a_run_of_step_size_is_a_run_name_not_a_path(tmp_path, monkeypatch, capsys, value) -> None:
    from tools import step_size

    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    with recorded(monkeypatch) as calls, contextlib.suppress(SystemExit):  # a share or a device is refused by the parser itself
        step_size.main(["--project", str(tmp_path), "--run", value, "--files", "a.txt"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out.get("status") in {"error", "invalid"} and calls == []


@pytest.mark.parametrize("value", HOSTILE)
def test_h12_the_candidate_of_review_view_is_judged_before_it_is_read(tmp_path, monkeypatch, capsys, value) -> None:
    from tools import review_view

    _project, run_root, _attempt = _project_with_run(tmp_path)
    with recorded(monkeypatch) as calls:
        try:
            review_view.main(["--project", str(tmp_path), "--run", run_root.name, "--candidate", value])
        except SystemExit:
            pass  # a share or a device is refused by the parser itself
    assert calls == []


@pytest.mark.parametrize("value", HOSTILE)
def test_h12_the_candidate_and_the_edits_of_review_edits_are_judged_before_they_are_read(monkeypatch, capsys, value) -> None:
    from tools import review_edits

    for argv in (["--candidate", value, "--edits", "e.json"], ["--candidate", "c.json", "--edits", value]):
        with recorded(monkeypatch) as calls:
            try:
                review_edits.main(argv)
            except SystemExit:
                pass
        assert calls == [], argv


@pytest.mark.parametrize("value", HOSTILE)
def test_h12_the_automation_of_autotest_hints_is_judged_before_it_is_read(monkeypatch, capsys, value) -> None:
    from tools import autotest_hints

    with recorded(monkeypatch) as calls:
        try:
            autotest_hints.main(["--automation", value])
        except SystemExit:
            pass
    assert calls == []


@pytest.mark.parametrize("value", HOSTILE)
def test_h12_validate_artifact_judges_both_paths_before_it_opens_them(tmp_path, monkeypatch, value) -> None:
    from tools import validate_artifact

    schema = ROOT / "schemas" / "event.schema.json"
    for schema_path, artifact_path in ((str(schema), value), (value, str(tmp_path / "a.json"))):
        with recorded(monkeypatch) as calls:
            validate_artifact.validate(schema_path, artifact_path)
        assert calls == [], (schema_path, artifact_path)


@pytest.mark.parametrize("flag", ["--answers", "--replacement-approval"])
@pytest.mark.parametrize("value", HOSTILE)
def test_h12_init_skillsrc_judges_the_answers_files_before_it_reads_them(tmp_path, monkeypatch, capsys, flag, value) -> None:
    from tools import init_skillsrc

    monkeypatch.setattr(sys, "argv", ["init_skillsrc", "--project", str(tmp_path), flag, value])
    with recorded(monkeypatch) as calls:
        try:
            init_skillsrc.main()
        except SystemExit:
            pass
    assert calls == []


@pytest.mark.parametrize("value", HOSTILE)
def test_h12_the_immutable_evidence_directory_is_judged_before_it_is_touched(tmp_path, monkeypatch, value) -> None:
    from tools import init_skillsrc

    with recorded(monkeypatch) as calls, pytest.raises(init_skillsrc.InitError):
        init_skillsrc._preserve_immutable_evidence(tmp_path, Path(value), b"x")
    assert calls == []


@pytest.mark.parametrize("value", [r"\??\UNC\h\x", "/??/UNC/h/x", r"\\?\UNC\h\x", r"\\evil-host-c2\x", "//evil-host-c2/x"])
def test_the_confined_output_refuses_a_device_or_a_share_by_its_text(tmp_path, value) -> None:
    from tools.confined_output import OutputConfinementError, _relative_output

    with pytest.raises(OutputConfinementError):
        _relative_output(tmp_path, value)


def test_the_confined_output_takes_a_local_path_below_docs_to_do(tmp_path) -> None:
    from tools.confined_output import acquire_confined_output

    with acquire_confined_output(tmp_path, "docs/to_do/out/skillsrc.json") as target:
        assert target.destination.name == "skillsrc.json" and target.destination.parent.name == "out"


# --- K-a: the working copy has the helper that the registry names -----------------------------------------------------------

def test_the_guard_of_the_registry_exists() -> None:
    from tools import output_guard

    for name in ("local_path_text", "_plain", "_inside", "project_path", "project_file", "input_file", "existing_run"):
        assert callable(getattr(output_guard, name))


# --- K-v: where the package writes ------------------------------------------------------------------------------------------

_OS_WRITE = {"link", "replace", "rename", "symlink", "remove", "unlink", "mkdir", "makedirs", "rmdir", "removedirs", "truncate", "utime",
             "chmod", "mkstemp", "mkdtemp"}
_SHUTIL_WRITE = {"copyfile", "copy", "copy2", "copytree", "move", "rmtree"}
_PATH_WRITE = {"write_bytes", "write_text", "mkdir", "touch", "unlink", "rmdir", "symlink_to", "hardlink_to"}


def _write_sites() -> dict[str, int]:
    """``module -> number of calls that can create, change or remove a file`` outside ``confined_output.py``."""
    found: dict[str, int] = {}
    for path in sorted(TOOLS.glob("*.py")):
        if path.name == "confined_output.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else function.id if isinstance(function, ast.Name) else None
            base = function.value.id if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name) else None
            hit = name in {"NamedTemporaryFile", "TemporaryDirectory"} \
                or (base == "os" and name in _OS_WRITE) or (base == "shutil" and name in _SHUTIL_WRITE) \
                or (isinstance(function, ast.Attribute) and base not in {"os", "shutil"} and name in _PATH_WRITE)
            if name == "open" and base != "os":
                mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else None
                mode = next((k.value.value for k in node.keywords if k.arg == "mode" and isinstance(k.value, ast.Constant)), mode)
                hit = isinstance(mode, str) and any(char in mode for char in "wax+")
            if name == "open" and base == "os":
                hit = "O_CREAT" in ast.unparse(node) or "O_WRONLY" in ast.unparse(node) or "O_RDWR" in ast.unparse(node)
            if hit:
                found[path.name] = found.get(path.name, 0) + 1
    return found


# module -> (number of write calls, why none of them is a write of the run of a command).  A module that is not here writes nothing
# outside ``confined_output``; a new call, or a module, fails the test and has to be argued here.
WRITES_OUTSIDE_CONFINED_OUTPUT = {
    "pilot_state.py": (2, "rmdir of the empty directory of an attempt step the same code made, below the run"),
    "project_inventory.py": (4, "the inventory's own probes of the project: unlink/mkdir of its guard files inside the scanned module"),
    "publish_test_case_bundle.py": (6, "the bundle of the run: <run>/published only (orchestrate.published_dir asks output_guard.run_output_directory at every entry; a link out is refused), hard link install"),
    "render_contract_docs.py": (1, "maintainer command, the package's own documents"),
    "release_manifest.py": (2, "maintainer command, release/manifest.json of the package checkout"),
    "init_skillsrc.py": (3, "the .skillsrc of the project and its evidence copies, the project given by --project (confined target)"),
}


def test_a_write_outside_confined_output_is_in_the_registry_with_its_reason() -> None:
    sites = _write_sites()
    assert {name: count for name, count in sites.items()} == {name: count for name, (count, _why) in WRITES_OUTSIDE_CONFINED_OUTPUT.items()}


# --- K-v: a --run that is no run name writes nothing ----------------------------------------------------------------------

def _tree(root: Path) -> dict[str, int]:
    return {str(path.relative_to(root)): path.stat().st_size for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("name", [FOREIGN_RUN, "..", "other", "{abs}"])
@pytest.mark.parametrize("command", ["status", "finish", "report"])
def test_h5_a_refused_run_coordinate_writes_nothing_into_another_run(tmp_path, capsys, command, name) -> None:
    from tests.test_self_review import _run
    from tools import run_pipeline

    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    _run_root, _attempt = _run(other, "cases-only-v1", flag=False)
    victim = other / ".pilot-runs" / _run_root.name
    value = name.format(hex=_run_root.name, abs=str(victim))
    before = _tree(other)
    with contextlib.suppress(SystemExit):
        run_pipeline.main([command, "--project", str(project), "--run", value])
    capsys.readouterr()
    assert _tree(other) == before and not (victim / "refusals.jsonl").exists()


@pytest.mark.parametrize("name", [FOREIGN_RUN, "{abs}"])
def test_h4_h5_step_size_with_a_foreign_run_reads_and_writes_nothing_there(tmp_path, capsys, name) -> None:
    from tests.test_self_review import _run
    from tools import step_size

    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    (project / "a.txt").write_text("a", encoding="utf-8")
    _run_root, _attempt = _run(other, "cases-only-v1", flag=False)
    victim = other / ".pilot-runs" / _run_root.name
    before = _tree(other)
    with contextlib.suppress(SystemExit):
        step_size.main(["--project", str(project), "--run", name.format(hex=_run_root.name, abs=str(victim)), "--files", "a.txt"])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out.get("status") in {"error", "invalid"} and _tree(other) == before


def test_h6_the_import_csv_is_written_only_below_the_published_directory_of_the_run() -> None:
    """The install of the import CSV goes through ``confined_output`` with the run as the root; a ``--cases`` that lies elsewhere in
    the project is refused (``ZEPHYR_EXPORT_CASES``)."""
    source = (TOOLS / "zephyr_template_export.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)} | {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "NamedTemporaryFile" not in names and "link" not in names and "confined_output" in source
