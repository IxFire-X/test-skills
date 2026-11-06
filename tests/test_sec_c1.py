"""SEC-C1 (class K-b): a command line that the agent or a person runs as it is is built from checked tokens only.

The rule: every value put into such a line goes through ``printed()`` / ``command_token()`` - a value no shell takes as it is stops
(``COMMAND_NOT_PRINTABLE``) - or is an identifier the code itself made (a hex id, a stage name).  A value that comes from the disk (the
manifest of the run, ``.skillsrc``) is checked again when it is printed.

Two tests hold the class, not the places: the first reads every f-string of ``tools/*.py`` that makes a command and fails on any new
value that is neither checked nor named below with its reason; the second calls the producers with values no shell takes as they are."""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from tools.run_pipeline import HostStop

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
NASTY = ['a&b', 'a;b', 'a$(x)', 'a`b`', "a'b", 'a"b', "a%b%", "a^b", "a!b", "a(b)"]

# ---- the static test: every value in a command f-string ------------------------------------------------------------------------------

COMMAND = re.compile(r"-m tools\.|tools\.api |tools\.run_pipeline |python -m ")
FLAG = re.compile(r"(?:^|\s)--[a-z][a-z-]*[ =]\"?$")
CHECKING = {"printed", "command_token", "printed_or"}
# identifiers the code makes itself or takes from a closed vocabulary: a 32-hex run id, a hex attempt id, a stage name, a digest, an enum value
IDENTIFIERS = {"attempt_id": "hex id of the attempt", "stage": "name from the closed list of stages", "stage_instance_id": "stage name",
               "expected": "stage name chosen by the chain of the automation", "kind": "name from the closed list of kinds",
               "instance": "stage name", "digest": "sha256 digest from the journal", "root.name": "run directory: 32 hex, checked by existing_run",
               "path.name": "run directory: 32 hex"}
# (file, expression) -> why this value is not a command argument or is checked on the line before
SPECIFIC = {
    ("api.py", "project"): "the text of a refusal that names the flag the person typed, not a command to run",
    ("module_ids.py", "given"): "MODULE_ID_LEGACY is raised only when the value is the old hex form of the id (legacy_warning)",
    ("module_ids.py", "current"): "id made by module_id_for: [A-Za-z0-9_/-] or hex",
    ("ready.py", "target"): "the text of the STOP row for the --target the person typed, not a command to run",
    ("ready.py", "profile"): "a choice of the parser (cases-only-v1 | local-pilot-v1)",
    ("review_legacy.py", "new"): "a literal of the table _RENAMED",
    ("finalize_attempt.py", "code"): "an abandon code of a closed table",
    ("skillsrc_manifest.py", "python"): "set from printed() or the literal template on the lines above",
    ("skillsrc_manifest.py", "where"): "set from printed() or the literal template on the lines above",
}


def _callee(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return getattr(node.func, "id", None) or getattr(node.func, "attr", None)
    return None


def _unchecked_values() -> list[str]:
    found = []
    for path in sorted((ROOT / "tools").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.JoinedStr):
                continue
            before, command = "", False
            for index, part in enumerate(node.values):
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    before += part.value
                    command = command or bool(COMMAND.search(before))
                    continue
                expr = ast.unparse(part.value)  # type: ignore[attr-defined]
                following = node.values[index + 1] if index + 1 < len(node.values) else None
                launcher = isinstance(following, ast.Constant) and bool(re.match(r"\"? -m ", str(following.value)))  # {sys.executable} -m tools...
                if (command or launcher or FLAG.search(before[-60:])) and _callee(part.value) not in CHECKING                         and expr not in IDENTIFIERS and (path.name, expr) not in SPECIFIC:  # type: ignore[attr-defined]
                    found.append(f"{path.name}:{node.lineno}: {{{expr[:60]}}}")
                before += "{}"
    return found


def test_every_value_in_a_command_line_is_checked_or_an_identifier_the_code_made() -> None:
    assert _unchecked_values() == []


def test_the_table_of_identifiers_and_exceptions_has_no_dead_row() -> None:
    """A row that no f-string uses any more is removed: the table only shrinks."""
    used = set()
    for path in sorted((ROOT / "tools").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.JoinedStr):
                used.update((path.name, ast.unparse(part.value)) for part in node.values if isinstance(part, ast.FormattedValue))
    assert [key for key in SPECIFIC if key not in used] == []
    assert [key for key in IDENTIFIERS if key not in {expr for _file, expr in used}] == []


# ---- the dynamic tests: the producers with values no shell takes as they are ---------------------------------------------------------


def test_command_token_is_the_white_list_the_schema_of_a_module_id_says() -> None:
    from tools.run_pipeline import command_token

    schema = json.loads((ROOT / "schemas" / "skillsrc.schema.json").read_text(encoding="utf-8"))
    pattern = re.compile(schema["$defs"]["module"]["properties"]["id"]["pattern"])
    for fine in ("api", "services/core", "root--617069", "m", "a.b-c_d"):
        assert pattern.search(fine) and command_token(fine) == fine
    for bad in (*NASTY, "a b", "-x", "", "a\nb", "а"):
        assert pattern.search(bad) is None, bad


def test_a_module_id_that_no_shell_takes_is_refused_by_the_skillsrc() -> None:
    from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes

    def skillsrc(module_id: str) -> bytes:
        return json.dumps({
            "schema_version": "5.0.0", "version": "3.0", "project": {"name": "x"},
            "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
            "modules": [{"id": module_id, "root": ".", "stack": {"language": "python", "build_tool": "pip"},
                         "paths": {"source": ["src"], "tests": ["tests"]}, "detected_from": ["pyproject.toml"]}]}).encode("utf-8")

    parse_skillsrc_bytes(skillsrc("api"))
    for bad in NASTY:
        with pytest.raises(SkillsrcError):
            parse_skillsrc_bytes(skillsrc(bad))


def test_the_command_of_review_open_is_quoted_and_a_directory_no_shell_takes_stops_before_anything_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_review_canaries import _fake_seed
    from tests.test_review_requested import _generated_run
    from tools import review_canaries
    from tools.orchestrate_test_case_revision import review_open_step
    from tools.pilot_state import read_attempt_receipt

    monkeypatch.setattr(review_canaries, "seed", _fake_seed)
    run = _generated_run(tmp_path)

    def opened(directory: Path) -> dict:
        return review_open_step(run["run_root"], run["attempt_id"], files={}, output_dir=directory, model_id="model-reviewer",
                                invocation_id="reviewer-subagent-1", fresh_context=True)

    with pytest.raises(HostStop) as stopped:
        opened(run["project"] / "bundles & more")
    assert stopped.value.code == "COMMAND_NOT_PRINTABLE"
    with pytest.raises((KeyError, ValueError)):  # nothing was opened: no boundary of a reviewer session
        read_attempt_receipt(run["run_root"], run["attempt_id"], "reviewer-session-boundary", "ARTIFACT_READ_BACK")
    directory = run["project"] / "bundles two"
    result = opened(directory)
    select = result["next_step"].split("затем ", 1)[1]
    assert f'--output-dir "{directory}"' in select and "--run-root " in select and select.endswith("<файл ответа ревьюера>")
    assert f'--output-dir "{directory}"' in result["for_human"]


def test_ingest_before_materialize_in_a_project_no_shell_takes_stops_instead_of_printing_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_exec_prepares import _project
    from tests.test_p4_ingest import _args
    from tools import exec_commands

    project, run_root, _attempt_id = _project(tmp_path / "100%done")
    with pytest.raises(HostStop) as stopped:
        exec_commands.cmd_ingest(_args(project, run_root))
    assert stopped.value.code == "COMMAND_NOT_PRINTABLE"
    assert "100%done" not in capsys.readouterr().out


def test_a_reuse_source_whose_artifact_no_shell_takes_is_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_marker_reuse import _fresh, _marker, _source
    from tools import pilot_state

    source_root, source_attempt = _source(tmp_path)
    _marker(source_root, source_attempt)
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] == source_root.name  # the source is there to be offered
    original = pilot_state._model_stage_artifact_target

    def moved(*args, **kwargs):  # the answer file of the source lies in a directory no shell takes as it is - for the suggestion only
        found = Path(original(*args, **kwargs))
        if sys._getframe(1).f_code.co_name != "marker_reuse_suggestion":
            return found
        copy = tmp_path / "a & b" / found.name
        copy.parent.mkdir(exist_ok=True)
        shutil.copyfile(found, copy)
        return copy

    with monkeypatch.context() as patched:
        patched.setattr(pilot_state, "_model_stage_artifact_target", moved)
        reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert "a & b" not in json.dumps(reuse, ensure_ascii=False), reuse


def test_the_venv_fix_of_ready_prints_the_directory_quoted_or_as_a_template(tmp_path: Path) -> None:
    from tools import ready
    from tools.run_pipeline import command_token

    module = {"id": "m", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
              "test": {"adapter_id": "pytest:selected-symbols-v1", "interpreter": ".venv/bin/python", "build_profile": "default", "adapter_parameters": {}}}
    for name, shown in (("fine dir", True), ("a & b", False)):
        project = tmp_path / name
        project.mkdir()
        fix = ready.execution_facts(project, module)["fix"]
        assert "python -m venv" in fix
        assert ("a & b" in fix) is False and (not shown or command_token(str(project / ".venv")) in fix)
        assert shown or "<папка модуля>/.venv" in fix
