"""V4a (P8b, first part): mode B without git — the project is compared by its inventory, no process is started to look at it.

``git status`` can run a clean filter or a hook of the project's own configuration, so a delivery of mode B must not run it.  What the
old scenario ``dirty-git-preservation`` proved — the user's own changes are not touched by a run — is proved by the inventory the run
froze at its start: the same inventory, rebuilt at the end and without the files the pipeline owns (the generated delta), has the same
digest.  Files outside the inventory (secrets, binaries, ``.git`` itself) are not seen by this proof."""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[1]
DEV_TOOLS = {"ci_gate.py", "release_manifest.py"}  # development tools, no delivery carries them


def _no_process(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def refuse(*args, **kwargs):
        calls.append(str(args[0] if args else kwargs))
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    return calls


def test_the_package_has_no_git_module_and_no_call_of_git() -> None:
    assert not (ROOT / "tools" / "exec_git.py").exists()
    offenders = []
    for path in sorted((ROOT / "tools").glob("*.py")):
        if path.name in DEV_TOOLS:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, (ast.List, ast.Tuple)) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == "git" and len(node.elts) > 1:
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
    from tools import execution_gate

    assert "exec_git" not in execution_gate.EXEC_MODULES


@pytest.mark.parametrize("profile", ["cases-only-v1", "local-pilot-v1"])
@pytest.mark.parametrize("with_git_directory", [False, True])
def test_the_git_row_of_ready_starts_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str, with_git_directory: bool) -> None:
    from tools import ready

    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True) if with_git_directory else project.mkdir()
    calls = _no_process(monkeypatch)
    row = ready._git_row(project, profile)
    assert row["id"] == "git" and row["level"] == "OK" and "git" in row["message"] and calls == []


def test_the_project_state_of_a_run_starts_nothing_and_names_only_the_absence_of_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    plain, with_git = tmp_path / "plain", tmp_path / "with-git"
    plain.mkdir()
    (with_git / ".git").mkdir(parents=True)
    calls = _no_process(monkeypatch)
    assert pilot_state._project_state_for("cases-only-v1", plain) is None and pilot_state._project_state_for("cases-only-v1", with_git) is None
    assert pilot_state._project_state_for("local-pilot-v1", plain)["kind"] == "GIT_DIRECTORY"
    assert pilot_state._project_state_for("local-pilot-v1", with_git) is None  # "not taken": the tree is compared by the inventory at the end
    assert calls == []


def test_the_manifest_schema_knows_no_git_tree_and_the_receipt_schema_knows_the_project_tree() -> None:
    manifest = json.loads((ROOT / "schemas" / "run-manifest.schema.json").read_text(encoding="utf-8"))
    kinds = [row.get("properties", {}).get("kind", {}).get("const") for row in manifest["properties"]["project_state"]["oneOf"]]
    assert "GIT_TREE" not in kinds and "GIT_DIRECTORY" in kinds
    receipt = json.loads((ROOT / "schemas" / "scenario-observation-receipt.schema.json").read_text(encoding="utf-8"))
    text = json.dumps(receipt)
    assert "dirty-git-preservation" not in text and "DIRTY_GIT_TREE" not in text and "RUN_PRESERVING_DIRTY_TREE" not in text and "GIT_TREE" not in text
    assert "tree-preservation" in text and "PROJECT_TREE" in text and "RUN_PRESERVING_TREE" in text
    for name in ("release-eval-run.schema.json",):
        assert "tree-preservation" in (ROOT / "schemas" / name).read_text(encoding="utf-8")


def test_the_suite_and_the_evaluator_name_the_new_scenario_and_the_old_name_is_gone() -> None:
    suite = json.loads((ROOT / "evals" / "scenarios" / "pilot-critical.json").read_text(encoding="utf-8"))
    assert "tree-preservation" in suite["critical_scenarios"] and "dirty-git-preservation" not in suite["critical_scenarios"]
    assert suite["scenario_contracts"]["tree-preservation"]["check_id"] == "tree-preserved"
    from evals import release_eval

    assert "tree-preservation" in release_eval._OBSERVATION_SCENARIOS and "dirty-git-preservation" not in release_eval._OBSERVATION_SCENARIOS
    assert release_eval._load_scenario_suite(ROOT / "evals" / "scenarios" / "pilot-critical.json")  # the sealed digest is the file's own


def test_a_manifest_with_the_old_git_tree_state_is_not_a_manifest(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.schema_validation import schema_diagnostics

    state = pilot_state._sealed({"kind": "GIT_TREE", "status": ["?? a"], "files": [{"path": "a", "digest": "sha256:" + "a" * 64}]})
    manifest = {"schema_version": "1.0.0", "run_id": "r" * 32, "project": str(tmp_path), "policy_profile": "local-pilot-v1",
                "authorization_digest": "sha256:" + "b" * 64, "project_state": state}
    assert schema_diagnostics(pilot_state._sealed(manifest), ROOT / "schemas" / "run-manifest.schema.json", ROOT)


# ---- the scenario: foreign changes laid before the run stay as they were after it ------------------------------------------------


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], cwd=project, check=True, capture_output=True)


def _run_with_foreign_changes(tmp_path: Path):
    """A project with git history, then: a tracked file changed, a tracked file deleted, an untracked file added — all before the run."""
    from tests.test_durable_execution_trace import _publish_execution
    from tests.test_execution_receipt import _execution_facts

    project = tmp_path / "project"
    project.mkdir()
    for name in ("changed.txt", "deleted.txt", "kept.txt"):
        (project / name).write_text(f"{name} as committed\n", encoding="utf-8")
    _git(project, "init", "--quiet")
    _git(project, "add", ".")
    _git(project, "commit", "--quiet", "-m", "base")
    (project / "changed.txt").write_text("changed.txt edited by the user, not committed\n", encoding="utf-8")
    (project / "deleted.txt").unlink()
    (project / "added.txt").write_text("added.txt, never committed\n", encoding="utf-8")
    run_root, attempt_id, report, request, _durable = _execution_facts(tmp_path, project=project)
    _publish_execution(run_root, attempt_id, report, request=request)
    return project, run_root, attempt_id


def _finalize(run_root: Path, attempt_id: str) -> dict:
    from tools.finalize_attempt import finalize_durable_execution_attempt

    return finalize_durable_execution_attempt(run_root, attempt_id)


@pytest.mark.slow
def test_foreign_changes_laid_before_the_run_are_there_after_it_and_the_observation_proves_it(tmp_path: Path) -> None:
    from tools.pilot_state import read_scenario_observation

    project, run_root, attempt_id = _run_with_foreign_changes(tmp_path)
    expected = {name: (project / name).read_bytes() for name in ("changed.txt", "added.txt", "kept.txt")}
    finalized = _finalize(run_root, attempt_id)
    published = finalized["scenario_observation_receipts"]["tree-preservation"]
    loaded = read_scenario_observation(run_root, attempt_id, "tree-preservation", published["digest"])
    assert loaded["verified"] is True
    record = loaded["record"]
    assert {name: (project / name).read_bytes() for name in expected} == expected and not (project / "deleted.txt").exists()
    assert record["precondition"]["kind"] == "PROJECT_TREE" and record["action"]["kind"] == "RUN_PRESERVING_TREE"
    assert record["before"] == record["after"] and record["before"]["kind"] == "PROJECT_TREE" and record["before"]["files"] >= 3
    assert "dirty-git-preservation" not in finalized["scenario_observation_receipts"]

    # the same run, the same function: a foreign file that changed, went or came after the start is no evidence (nothing is published)
    from tools import pilot_state

    kept = project / "kept.txt"
    for name, damage, repair in (
        ("edit", lambda: kept.write_text("touched in the middle of the run" + chr(10), encoding="utf-8"), lambda: kept.write_bytes(expected["kept.txt"])),
        ("delete", lambda: kept.unlink(), lambda: kept.write_bytes(expected["kept.txt"])),
        ("add", lambda: (project / "intruder.txt").write_text("a new foreign file" + chr(10), encoding="utf-8"), lambda: (project / "intruder.txt").unlink()),
    ):
        damage()
        assert pilot_state.publish_project_state_observation(run_root, attempt_id) is None, name
        repair()
    assert pilot_state.publish_project_state_observation(run_root, attempt_id) is not None  # repaired: the evidence is there again


