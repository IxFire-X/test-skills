"""CO1 + MB1: each delivery profile walks its own path to the end, on a copy of the profile.

The gate runs the full development package, so a command of the delivery that calls what the profile does not carry passes it.  Here
the commands run in a copy of the files of the profile (``pack_registry.profile_paths``), the run itself is prepared by the fixtures
of the development package up to the step the commands start from.

- ``cases-only``: the answers of the generator (``model-request`` / ``model-response``), ``assemble``, ``review-open``,
  ``review-select``, ``finish``, ``report`` — no module of mode B is there (CO1: the first ``model-response`` refused
  ``MODE_B_NOT_IN_PACKAGE``);
- ``mode-b``: the review of the automation by the commands of the text (``references/mode-b.md`` §5) — ``read-effective``,
  ``model-response tc-to-autotest``, ``automation-boundary-facts``, ``open-automation-boundary``, ``review-requested``, the reviewer's
  request and answer, ``materialize``, the human run, ``ingest``, ``report`` — no ``review_legacy`` is there (MB1: no ``review-requested``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_pack_profile import copy_profile

ROOT = Path(__file__).resolve().parents[1]


def _cli(pack: Path, module: str, *argv: str) -> tuple[int, dict]:
    done = subprocess.run([sys.executable, "-m", module, *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    assert "Traceback" not in done.stderr, done.stderr
    text = done.stdout.strip()
    try:
        return done.returncode, json.loads(text) if text else {}
    except json.JSONDecodeError:  # a line of notes before the answer
        return done.returncode, json.loads(text.splitlines()[-1])


def _api(pack: Path, command: str, *argv: str) -> tuple[int, dict]:
    return _cli(pack, "tools.api", command, *argv)


def _put(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    return str(path)


@pytest.fixture(scope="module")
def cases_only_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("co1") / "pack", "cases-only")


@pytest.fixture(scope="module")
def mode_b_pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("mb1") / "pack", "mode-b")


def test_the_two_profiles_leave_out_what_the_flows_below_must_not_need() -> None:
    from tools import pack_registry

    paths = [row["path"] for row in pack_registry.registry_rows(ROOT)]
    cases_only, mode_b = (set(pack_registry.profile_paths(paths, name)) for name in ("cases-only", "mode-b"))
    assert not {"tools/automation_validation.py", "tools/run_tests.py", "tools/review_legacy.py"} & cases_only
    assert "tools/review_legacy.py" not in mode_b and "tools/automation_validation.py" in mode_b and "tools/exec_commands.py" in mode_b


# ---- cases-only: scan-side answers → review → finish → report ----------------------------------------------------------

def test_cases_only_delivery_takes_the_answers_of_the_model_and_walks_to_the_report(tmp_path: Path, cases_only_pack: Path) -> None:
    from tests.test_review_requested import _generated_run
    from tests.test_review_two_commands import _bundles, _flags, _verdict

    pack = cases_only_pack
    run = _generated_run(tmp_path / "w", generated=False)
    flags, tmp = _flags(run), run["tmp"]
    fragments = [json.loads(Path(path).read_bytes()) for path in run["fragments"]]
    context = json.loads(Path(run["context"]).read_bytes())
    for kind in ("header", "plan"):
        code, published = _api(pack, "publish-generator-input", *flags, "--kind", kind, "--value", run[kind])
        assert code == 0, published
    for index, (fragment, path) in enumerate(zip(fragments, run["fragments"], strict=True)):
        stage = f"tc-generator:{fragment['batch_id']}"
        digests = [run["marker_digest"], context["digest"], fragment["plan_digest"], fragment["header_digest"]]
        if index:
            code, prior = _api(pack, "prior-capabilities", *flags, "--batch-id", fragment["batch_id"])
            assert code == 0, prior
            digests.append(prior["result"]["digest"])
        code, requested = _api(pack, "model-request", *flags, "--stage", stage, "--model-id", "model-generator", "--invocation-id", f"invoke-{index + 1}",
                               *[flag for digest in digests for flag in ("--input-digest", digest)])
        assert code == 0, requested
        code, answered = _api(pack, "model-response", *flags, "--stage", stage, "--artifact", path)
        assert code == 0, ("CO1: the answer of the model is not taken in a delivery of cases", answered)
    code, assembled = _api(pack, "assemble", "--header", run["header"], "--plan", run["plan"], *[f for path in run["fragments"] for f in ("--fragment", path)],
                           "--out-candidate", str(tmp / "candidate.json"), "--out-receipt", str(tmp / "assembly-receipt.json"), "--run-root", str(run["run_root"]))
    assert code == 0, assembled
    code, opened = _api(pack, "review-open", *flags, "--output-dir", _bundles(run), "--model-id", "model-reviewer",
                        "--invocation-id", "reviewer-subagent-1", "--fresh-context", "yes")
    assert code == 0 and opened["status"] == "ok", opened
    code, selected = _api(pack, "review-select", *flags, "--output-dir", _bundles(run), "--verdict", _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", ("CO1: the answer of the reviewer", selected)
    # the automation chain is not a step of this delivery: asking for it is a refusal with the code and the fix, not an accepted request
    # that the answer cannot follow
    code, effective = _api(pack, "read-effective", *flags)
    assert code == 0, effective
    effective = effective["result"]
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1"):
        code, refused = _api(pack, "model-request", *flags, "--stage", stage, "--model-id", "m", "--invocation-id", "i",
                             "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])
        assert code == 2 and refused["code"] == "MODEL_STAGE_NOT_IN_PROFILE" and refused["fix"], (stage, refused)
    project, name = str(run["project"]), run["run_root"].name
    for command in ("status", "finish", "report"):
        code, answer = _cli(pack, "tools.run_pipeline", command, "--project", project, "--run", name)
        assert code in (0, 1) and "MODE_B_NOT_IN_PACKAGE" not in json.dumps(answer), (command, answer)
    assert (run["run_root"] / "report.json").is_file() and (run["run_root"] / "report.md").is_file()


# ---- mode-b: the review of the automation without review_legacy → the human run → ingest → report --------------------

def _mode_b_project(tmp_path: Path) -> tuple[Path, Path, str, dict, dict]:
    """The project of the fixture with a module venv and a local-pilot run whose effective canonical is selected."""
    from tests.test_automation_revision_budget import automated_document
    from tests.test_exec_prepares import _module_python
    from tests.test_generated_delta import _effective_bundle, _published_reviewer_ledger
    from tests.test_project_native_pytest import MODULE_PYTHON, _local_attempt
    from tools.pilot_state import publish_effective_canonical
    import shutil

    project = tmp_path / "project"
    shutil.copytree(ROOT / "tests" / "fixtures" / "projects" / "python-pytest", project)
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")
    _module_python(project)
    run_root, attempt_id, _authorization, _baseline = _local_attempt(project)
    document = automated_document()
    publish_effective_canonical(run_root, attempt_id, document, _effective_bundle(document), _published_reviewer_ledger(run_root, attempt_id, document))
    return project, run_root, attempt_id, document, {}


def test_mode_b_delivery_opens_the_review_of_the_automation_and_walks_to_the_report(tmp_path: Path, mode_b_pack: Path) -> None:
    import hashlib

    from tests.p4_flow import human_run, junit  # noqa: F401
    from tests.test_automation_revision_budget import automation, review
    from tests.test_generated_delta import _effective_bundle_digest

    pack = mode_b_pack
    assert not (pack / "tools" / "review_legacy.py").exists() and (pack / "tools" / "exec_commands.py").is_file()
    project, run_root, attempt_id, document, _ = _mode_b_project(tmp_path)
    run, tmp = ("--run-root", str(run_root), "--attempt", attempt_id), run_root / "tmp"

    code, effective = _api(pack, "read-effective", *run)
    assert code == 0, effective
    effective = effective["result"]
    code, requested = _api(pack, "model-request", *run, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1",
                           "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])
    assert code == 0, requested
    content = ("import pytest\n\nfrom sample import combine\n\n\n@pytest.mark.native\n"
               "def test_selected(plugin_value: str, conftest_value: str) -> None:\n"
               "    assert combine(plugin_value, conftest_value) == \"from-project-plugin:from-conftest\"\n")
    artifact = automation(document, effective_bundle_receipt_digest=_effective_bundle_digest(document))
    artifact["artifacts"]["generated_files"][0].update(path="tests/test_generated.py", content=content,
                                                         content_digest="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
    artifact["artifacts"]["generated_symbols"][0]["locator"] = {"kind": "python_module_function", "function_name": "test_selected"}
    code, answered = _api(pack, "model-response", *run, "--stage", "tc-to-autotest:r1", "--artifact", _put(tmp / "automation.json", artifact))
    assert code == 0, answered
    statement = ("--fresh-context", "yes", "--reviewer-invocation-id", "automation-reviewer-subagent-1")
    code, facts = _api(pack, "automation-boundary-facts", *run, "--revision", "1", *statement, "--out", str(tmp / "facts.json"))
    assert code == 0, facts
    code, opened = _api(pack, "open-automation-boundary", *run, "--facts", str(tmp / "facts.json"))
    assert code == 0, opened
    automation_digest = json.loads((tmp / "facts.json").read_bytes())["automation_digest"]
    # mode-b.md §5: the review of the automation is requested by the command of the text
    code, recorded = _api(pack, "review-requested", *run, "--stage", "autotest-reviewer:r1", "--digest", automation_digest)
    assert code == 0 and recorded["result"]["event_type"] == "REVIEW_REQUESTED", ("MB1: no review-requested in mode-b", recorded)
    code, boundary = _api(pack, "read-receipt", *run, "--kind", "automation-review-boundary-r1", "--event", "ARTIFACT_READ_BACK")
    assert code == 0, boundary
    record = boundary["result"]["record"]
    code, reviewer = _api(pack, "model-request", *run, "--stage", "autotest-reviewer:r1", "--model-id", "model-automation-reviewer",
                          "--invocation-id", record["reviewer_invocation_id"], "--input-digest", record["automation_digest"], "--input-digest", record["digest"])
    assert code == 0 and reviewer["status"] == "ok", reviewer
    verdict = review(document, artifact, record, "ПРИНЯТО", session_id=record["reviewer_session_id"])
    left_to_the_code = ("automation_sha256", "reviewed_relations_sha256", "host_isolation_sha256")  # the SKILL tells the reviewer to leave them empty
    for key in left_to_the_code:
        del verdict["artifacts"]["autotest_review"][key]
    code, accepted = _api(pack, "model-response", *run, "--stage", "autotest-reviewer:r1", "--artifact", _put(tmp / "autotest-review.json", verdict))
    assert code == 0 and all(f"/artifacts/autotest_review/{key}" in accepted["result"]["filled"] for key in left_to_the_code), accepted
    # the canonical review has one way in a delivery without review_legacy: review-open / review-select
    code, refused = _api(pack, "review-requested", *run, "--stage", "tc-reviewer:canonical", "--digest", automation_digest)
    assert code == 2 and refused["code"] == "LEGACY_REVIEW_NOT_IN_PACKAGE" and "review-open" in refused["fix"] and "review-select" in refused["fix"], refused
    # materialize → the person runs the printed command → ingest → report
    shared = ("--project", str(project), "--run", run_root.name, "--module", "python-pytest")
    code, printed = _cli(pack, "tools.run_pipeline", "materialize", *shared)
    assert code == 0 and printed["stage"] == "materialize", ("MB1: the static review was not taken", printed)
    assert human_run(project, printed).returncode == 0
    code, ingested = _cli(pack, "tools.run_pipeline", "ingest", *shared)
    assert code == 0 and ingested["verdict"] == "PASS", ingested
    code, reported = _cli(pack, "tools.run_pipeline", "report", "--project", str(project), "--run", run_root.name)
    assert code == 0 and (run_root / "report.json").is_file(), reported
    code, finished = _cli(pack, "tools.run_pipeline", "finish", "--project", str(project), "--run", run_root.name)
    assert "MODE_B_NOT_IN_PACKAGE" not in json.dumps(finished), finished


# ---- the texts of a profile name only the commands the profile has ---------------------------------------------------

_NAMED = __import__("re").compile(r"tools\.(api|run_pipeline)\s+([a-z](?:[a-z-]*[a-z])?)(?![a-z{-])")

# (profile, file of the profile, "module command") → why a text may name a command its profile lacks: the text is reached only
# where the command is (mode B in a delivery of cases) or is a text of the development build
_NOT_REACHABLE = {
    ("cases-only", "tools/finalize_attempt.py", "run_pipeline materialize"): "the accepted automation exists only in a run of mode B",
    ("cases-only", "tools/pilot_state.py", "api automation-digest"): "message of the check of an automation answer (mode B)",
    ("cases-only", "tools/pilot_state.py", "api relations-digest"): "message of the check of an automation answer (mode B)",
    ("cases-only", "tools/run_pipeline.py", "run_pipeline ingest"): "an attempt with a start of execution, which a delivery of cases cannot make",
}


def _commands_of(pack: Path) -> dict[str, set[str]]:
    code = ("import json\nfrom tools import api, run_pipeline\n"
            "parser = run_pipeline.build_parser()\n"
            "choices = next(a for a in parser._actions if getattr(a, 'choices', None)).choices\n"
            "print(json.dumps({'api': sorted(api.COMMANDS), 'run_pipeline': sorted(choices)}))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    assert done.returncode == 0, done.stderr
    return {key: set(value) for key, value in json.loads(done.stdout).items()}


def _strings_of(path: Path):
    """The string constants of a module, without the docstrings (a docstring explains the code; a message reaches the person)."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


@pytest.mark.parametrize("profile", ["cases-only", "mode-b"])
def test_the_texts_of_a_profile_name_only_commands_the_profile_has(profile: str, cases_only_pack: Path, mode_b_pack: Path) -> None:
    pack = cases_only_pack if profile == "cases-only" else mode_b_pack
    have = _commands_of(pack)
    named: dict[tuple[str, str], set[str]] = {}
    for path in sorted((pack / "tools").glob("*.py")):
        for text in _strings_of(path):
            for tool, command in _NAMED.findall(text):
                named.setdefault((path.relative_to(pack).as_posix(), f"{tool} {command}"), set()).add(text[:60])
    for path in sorted((pack / "skills").rglob("*.md")):
        for tool, command in _NAMED.findall(path.read_text(encoding="utf-8")):
            named.setdefault((path.relative_to(pack).as_posix(), f"{tool} {command}"), set()).add("")
    from tools import fixes

    for code in fixes.FIXES:
        for tool, command in _NAMED.findall(fixes.fix_for(code) or ""):
            named.setdefault(("tools/fixes.py", f"{tool} {command}"), set()).add(code)
    missing = sorted((file, name) for file, name in named if name.split()[1] not in have[name.split()[0]]
                     and (profile, file, name) not in _NOT_REACHABLE)
    assert missing == [], missing
    reached = {(profile, file, name) for file, name in named if name.split()[1] not in have[name.split()[0]]}
    assert not {key for key in _NOT_REACHABLE if key[0] == profile} - reached, "an exception that no text needs any more"


# ---- the stages of the automation are not stages of a run of cases (the policy of the run, in every build) --------------

@pytest.mark.parametrize("where", ["development", "cases-only", "mode-b"])
def test_a_run_of_cases_only_v1_refuses_the_stages_of_the_automation_before_any_event(tmp_path: Path, where: str,
                                                                                     cases_only_pack: Path, mode_b_pack: Path) -> None:
    from tests.journal_helpers import assert_refused_once
    from tests.test_review_requested import _generated_run, _journal

    run = _generated_run(tmp_path / "w")
    flags = ("--run-root", str(run["run_root"]), "--attempt", run["attempt_id"])
    cwd = {"development": ROOT, "cases-only": cases_only_pack, "mode-b": mode_b_pack}[where]
    digest = "sha256:" + "7" * 64
    commands = [("model-request", "--stage", stage, "--model-id", "m", "--invocation-id", "i", "--input-digest", digest)
                for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1")]
    if where != "cases-only":  # review-requested is not a command of a delivery of cases
        commands.append(("review-requested", "--stage", "autotest-reviewer:r1", "--digest", digest))
    for command, *argv in commands:
        before = _journal(run["run_root"])
        code, refused = _api(cwd, command, *flags, *argv)
        assert code == 2 and refused["code"] == "MODEL_STAGE_NOT_IN_PROFILE" and "local-pilot-v1" in refused["fix"], (where, command, refused)
        assert_refused_once(run["run_root"], before, refused["code"], command)
