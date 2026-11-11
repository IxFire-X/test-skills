"""CO1 + MB1 + R1: each delivery profile walks its whole path, from ``ready`` to ``report``, on a copy of the profile.

The gate runs the full development package, so a command of the delivery that calls what the profile does not carry passes it.  Here
every command runs in a copy of the files of the profile (``pack_registry.profile_paths``); only the answers of the model are written
by the test (the marker, the fragments, the verdicts) and the run of the tests is the human's (``human_run``).

- ``cases-only``: ``ready``, ``scan``, the marker (``prepare`` / ``submit``), the pause on a
  gap, ``publish-generator-input``, ``plan``, the answers of the generator by batches (``validate-fragment``, ``prior-capabilities``),
  ``assemble``, ``validate-canonical``, ``review-open``, ``review-select``, ``finish``, ``report`` — no module of mode B is there (CO1:
  the first ``model-response`` refused ``MODE_B_NOT_IN_PACKAGE``);
- ``mode-b``: the same cases part, then the review of the automation by the commands of the text (``references/mode-b.md`` §5) —
  ``read-effective``, ``model-response tc-to-autotest``, ``automation-boundary-facts``, ``open-automation-boundary``, ``review-requested``,
  the reviewer's request and answer, ``materialize``, the human run, ``ingest``, ``report`` — no ``review_legacy`` is there (MB1: no
  ``review-requested``).
"""
from __future__ import annotations

import hashlib
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


# ---- the front half of a path: ready → scan → the marker → the plan of the batches --------------------------------------

def _scan(pack: Path, *argv: str) -> tuple[int, dict]:
    """``scan`` prints its JSON and then ``run_id: <id>`` as the last line."""
    done = subprocess.run([sys.executable, "-m", "tools.run_pipeline", "scan", *argv], cwd=pack, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    assert "Traceback" not in done.stderr, done.stderr
    return done.returncode, json.loads(done.stdout.rsplit("\nrun_id: ", 1)[0])


def _tree(root: Path) -> dict[str, tuple[int, int]]:
    return {path.relative_to(root).as_posix(): (path.stat().st_size, path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}


def _writes_stay_in_bounds(pack: Path, run: dict, *allowed: str) -> None:
    """Since the last snapshot the commands left the copy of the profile as it was and wrote into the project only the run
    (``.pilot-runs/``), ``.skillsrc`` and the files in ``allowed`` (the tests ``materialize`` named)."""
    def changed(root: Path, before: dict) -> list[str]:
        after = _tree(root)
        return sorted(key for key in {**before, **after} if before.get(key) != after.get(key))

    outside = [key for key in changed(run["project"], run["project_before"]) if not key.startswith(".pilot-runs/") and key != ".skillsrc" and key not in allowed]
    assert changed(pack, run["pack_before"]) == [] and outside == [], outside


def _status(pack: Path, project: Path, run_id: str, state: str, verb: str, waiting: str = "agent") -> dict:
    """``status`` of the run says its state, who waits and the one verb of the next step."""
    code, status = _cli(pack, "tools.run_pipeline", "status", "--project", str(project), "--run", run_id)
    assert code == 0, status
    assert (status["state"], status["waiting_for"], status["next"]["verb"]) == (state, waiting, verb), status
    return status


def _skillsrc(project: Path, window: int | None) -> None:
    from tests.test_project_native_pytest import MODULE_PYTHON

    limits = {"limits": {"step_window_bytes": window}} if window is not None else {}
    (project / ".skillsrc").write_text(json.dumps({
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "fixture"}, **limits,
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "python-pytest", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "test": {"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": MODULE_PYTHON, "build_profile": "default", "adapter_parameters": {}},
            "detected_from": ["pyproject.toml"],
        }],
    }, ensure_ascii=False), encoding="utf-8")


def _front(pack: Path, tmp_path: Path, profile: str, *, requirements: int = 1, gap: bool = False) -> dict:
    """The path of a delivery up to the answers of the generator, by the commands of the profile copy ``pack`` alone.

    A project of the fixtures, a requirements document of ``requirements`` long lines (the plan splits the batches by the size of the
    expected answer, 42 B per byte of text, so a window of 200 kB gives a batch each), ``ready``, ``scan``, the answer of the marker,
    the pause on a gap (``gap``), ``plan``.  The returned run is the shape the commands of the back half read."""
    import shutil

    from tests.test_batch_assembly import _fragment
    from tests.test_exec_prepares import _module_python

    project = tmp_path / "project"
    shutil.copytree(ROOT / "tests" / "fixtures" / "projects" / "python-pytest", project)
    (project / "docs").mkdir()
    lines = [f"REQ-{number} " + "Пользователь видит список значений. " * 60 for number in range(1, requirements + 1)]
    (project / "docs" / "requirements.md").write_text("# Требования\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    _skillsrc(project, 200_000 if requirements > 1 else None)
    if profile == "local-pilot-v1":
        _module_python(project)
    before = _tree(pack), _tree(project)
    scope = ("--project", str(project), "--docs", "docs/requirements.md", "--profile", profile)
    code, ready = _cli(pack, "tools.ready", *scope)
    assert code == 0 and ready["status"] == "READY", [row for row in ready["checks"] if row["level"] == "STOP"]
    code, scanned = _scan(pack, *scope, "--module", "python-pytest")
    assert code == 0 and (scanned["state"], scanned["waiting_for"], scanned["next"]["verb"]) == ("marker", "agent", "prepare"), scanned
    assert scanned["next"]["stage"] == "context-marker:baseline", scanned
    run_root, attempt = Path(scanned["run_root"]), scanned["attempt_id"]
    flags, tmp = ("--run-root", str(run_root), "--attempt", attempt), Path(scanned["run_root"]) / "tmp"
    code, prepared = _api(pack, "prepare", "--run-root", str(run_root), "--stage", "context-marker:baseline", "--model-id", "model-context")
    assert code == 0 and prepared["result"]["answer_slot"] == str(run_root / "incoming" / "context-marker-baseline.json"), prepared
    _status(pack, project, run_root.name, "marker", "submit", "model")
    source = [{"source_requirement_id": f"SREQ-{number:04d}", "display_order": number, "text": line, "provenance": [f"docs/requirements.md:{number + 2}"],
               "digest": "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()} for number, line in enumerate(lines, start=1)]
    warnings = ["REQ-1 сценарий (docs/requirements.md:3) — нет наблюдаемого канала — блокирует проверку списка — как увидеть список?"] if gap else []
    marker = {"schema_version": "5.0.0", "stage": "context-marker", "warnings": warnings,
              "artifacts": {"analytics_documentation": {"requirements": source}, "source_code_and_diff": {"sources": ["src/sample.py — immutable test source"]}}}
    _put(Path(prepared["result"]["answer_slot"]), marker)
    code, answered = _api(pack, "submit", "--run-root", str(run_root), "--stage", "context-marker:baseline")
    assert code == 0, answered
    header = {"schema_version": "1.1.0", "document_id": "TCDOC-fixture", "revision": 1, "parent_sha256": None, "content_locale": "ru-RU",
              "metadata": {"subject": {"kind": "generic", "name": "Фикстура"}, "documentation": ["docs/requirements.md"], "project": "fixture",
                           "author": "controller", "date": "2026-11-10"}}
    context_path = Path(scanned["evidence_path"])
    context = json.loads(context_path.read_bytes())
    plan_files = ("--header", _put(tmp / "header.json", header), "--requirements", _put(tmp / "requirements.json", source),
                  "--grouping", _put(tmp / "grouping.json", {"context_receipt_digest": context["digest"], "context_receipt_digests": [context["digest"]]}),
                  "--project", str(project), "--input-file", str(run_root / answered["result"]["path"]), "--context-receipt", str(context_path), *flags,
                  "--out-plan", str(tmp / "plan.json"), "--out-published", str(tmp / "published.json"))
    code, published = _api(pack, "publish-generator-input", *flags, "--kind", "header", "--value", str(tmp / "header.json"))
    assert code == 0, published
    if gap:  # the pause after the marker: status says so, the plan is refused until the human answers
        status = _status(pack, project, run_root.name, "scope_pause", "plan-batches", "human")
        assert status["scope_gaps"][0]["source_requirement_ids"] == ["SREQ-0001"], status
        code, refused = _api(pack, "plan", *plan_files)
        assert code == 2 and refused["code"] == "SCOPE_PENDING" and "--continue" in refused["fix"], refused
    if not gap:  # the plan is published by `tools.api plan` (SKILL §3), not named by its inner step plan-batches
        _status(pack, project, run_root.name, "generator", "plan")
    code, planned = _api(pack, "plan", *plan_files, *(["--continue"] if gap else []))
    assert code == 0, planned
    plan = json.loads((tmp / "plan.json").read_bytes())
    assert len(plan["batches"]) == requirements, planned["notes"]
    make = _automated if profile == "local-pilot-v1" else (lambda fragment: fragment)
    fragments = [_put(tmp / f"fragment-{index}.json", make(_fragment(plan, context, index))) for index in range(len(plan["batches"]))]
    return {"run_root": run_root, "attempt_id": attempt, "project": project, "tmp": tmp, "header": str(tmp / "header.json"), "plan": str(tmp / "plan.json"),
            "fragments": fragments, "context": str(context_path), "marker_digest": answered["result"]["content_digest"],
            "pack_before": before[0], "project_before": before[1]}


# ---- the cases part of a path, in both profiles: the answers of the generator → assemble → the review → the effective canonical ----

def _cases_to_effective(pack: Path, run: dict) -> dict:
    """The generator batch by batch (checked, requested, answered), ``assemble``, ``validate-canonical``, ``review-open``, ``review-select``,
    ``read-effective``: the effective canonical of the run."""
    from tests.test_review_two_commands import _bundles, _flags, _verdict

    flags, tmp = _flags(run), run["tmp"]
    context = json.loads(Path(run["context"]).read_bytes())
    plan, prior = json.loads(Path(run["plan"]).read_bytes()), []
    for index, path in enumerate(run["fragments"]):
        batch, fragment = plan["batches"][index]["batch_id"], json.loads(Path(path).read_bytes())
        stage = f"tc-generator:{batch}"
        checked = ("--fragment", path, "--plan", run["plan"], "--header", run["header"], "--context-receipt", run["context"], *flags[:2],
                   *[flag for earlier in prior for flag in ("--prior-fragment", earlier)])
        code, valid = _api(pack, "validate-fragment", *checked)
        assert code == 0, valid
        digests = [run["marker_digest"], context["digest"], fragment["plan_digest"], fragment["header_digest"]]
        if index:
            code, capabilities = _api(pack, "prior-capabilities", *flags, "--batch-id", batch)
            assert code == 0, capabilities
            digests.append(capabilities["result"]["digest"])
        code, requested = _api(pack, "model-request", *flags, "--stage", stage, "--model-id", "model-generator", "--invocation-id", f"invoke-{index + 1}",
                               *[flag for digest in digests for flag in ("--input-digest", digest)])
        assert code == 0, requested
        code, answered = _api(pack, "model-response", *flags, "--stage", stage, "--artifact", path)
        assert code == 0, ("CO1: the answer of the model is not taken in a delivery of cases", answered)
        prior.append(str(run["run_root"] / answered["result"]["path"]))
        # the last answer asks for the assembly, an earlier one for the next batch
        _status(pack, run["project"], run["run_root"].name, "generator", "assemble" if index == len(run["fragments"]) - 1 else "model-request")
    code, assembled = _api(pack, "assemble", "--header", run["header"], "--plan", run["plan"], *[f for path in run["fragments"] for f in ("--fragment", path)],
                           "--out-candidate", str(tmp / "candidate.json"), "--out-receipt", str(tmp / "assembly-receipt.json"), "--run-root", str(run["run_root"]))
    assert code == 0, assembled
    _status(pack, run["project"], run["run_root"].name, "generator", "assemble")  # the journal does not see assemble
    code, valid = _api(pack, "validate-canonical", "--document", str(tmp / "candidate.json"))
    assert code == 0, valid
    code, opened = _api(pack, "review-open", *flags, "--output-dir", _bundles(run), "--model-id", "model-reviewer",
                        "--invocation-id", "reviewer-subagent-1", "--fresh-context", "yes")
    assert code == 0 and opened["status"] == "ok", opened
    _status(pack, run["project"], run["run_root"].name, "review", "model-response", "model")  # the request of the reviewer is the one open step
    code, selected = _api(pack, "review-select", *flags, "--output-dir", _bundles(run), "--verdict", _verdict(run))
    assert code == 0 and selected["result"]["status"] == "EFFECTIVE_SELECTED", ("CO1: the answer of the reviewer", selected)
    code, effective = _api(pack, "read-effective", *flags)
    assert code == 0, effective
    return effective["result"]


# ---- cases-only: the whole path of a delivery of cases, from ready to report ------------------------------------------

def test_cases_only_delivery_walks_from_ready_to_the_report(tmp_path: Path, cases_only_pack: Path) -> None:
    from tests.test_review_two_commands import _flags

    pack = cases_only_pack
    run = _front(pack, tmp_path, "cases-only-v1", requirements=2, gap=True)
    flags = _flags(run)
    effective = _cases_to_effective(pack, run)
    # the automation chain is not a step of this delivery: asking for it is a refusal with the code and the fix, not an accepted request
    # that the answer cannot follow
    for stage in ("tc-to-autotest:r1", "autotest-reviewer:r1"):
        code, refused = _api(pack, "model-request", *flags, "--stage", stage, "--model-id", "m", "--invocation-id", "i",
                             "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])
        assert code == 2 and refused["code"] == "MODEL_STAGE_NOT_IN_PROFILE" and refused["fix"], (stage, refused)
    project, name = str(run["project"]), run["run_root"].name
    _status(pack, run["project"], name, "closure", "finish")
    for command in ("status", "finish", "report"):
        code, answer = _cli(pack, "tools.run_pipeline", command, "--project", project, "--run", name)
        assert code in (0, 1) and "MODE_B_NOT_IN_PACKAGE" not in json.dumps(answer), (command, answer)
    assert (run["run_root"] / "report.json").is_file() and (run["run_root"] / "report.md").is_file()
    _writes_stay_in_bounds(pack, run)


# ---- mode-b: the whole path, from ready to report: the cases, the review of the automation without review_legacy, the human run -----

def _automated(fragment: dict) -> dict:
    """The one case of the fragment with an automated step: a GET of the product list answered by 200."""
    from tests.test_batch_assembly import _refresh

    step = fragment["test_cases"][0]["steps"][0]
    step.update(manual_only=False, manual_reason=None, test_data="Тело запроса отсутствует.", operation={
        "kind": "http", "binding_profile": "http-binding-v1", "base_url_source": {"kind": "environment", "name": "BASE_URL", "provenance": ["test"]},
        "method": "GET", "path": "/products"})
    expectation = step["expectations"][0]
    expectation["text"] = "Отображается список доступных товаров.\n\nHTTP 200 OK"
    expectation["assertions"] = [{"assertion_id": expectation["expectation_id"].replace("EXP-", "ASSERT-") + "-1", "display_order": 1,
                                  "actual": {"kind": "http_status"}, "operator": "equals", "expected": {"kind": "literal", "value": 200}}]
    _refresh(fragment)
    return fragment


def _automation_answer(document: dict, effective: dict) -> dict:
    """The answer of ``tc-to-autotest``: one generated test whose relations name the ids of the document of the run."""
    from tests.test_automation_revision_budget import automation
    from tests.test_generated_delta import _effective_bundle_digest  # noqa: F401

    content = ("import pytest\n\nfrom sample import combine\n\n\n@pytest.mark.native\n"
               "def test_selected(plugin_value: str, conftest_value: str) -> None:\n"
               "    assert combine(plugin_value, conftest_value) == \"from-project-plugin:from-conftest\"\n")
    artifact = automation(document, effective_bundle_receipt_digest=effective["effective_bundle_receipt_digest"])
    generated = artifact["artifacts"]
    generated["generated_files"][0].update(path="tests/test_generated.py", content=content,
                                           content_digest="sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest())
    generated["generated_symbols"][0]["locator"] = {"kind": "python_module_function", "function_name": "test_selected"}
    case = document["test_cases"][0]
    step = case["steps"][0]
    expectation = step["expectations"][0]
    where = {"case_id": case["case_id"], "step_id": step["step_id"], "file_id": "FILE-products", "symbol_id": "SYMBOL-products"}
    generated["implementation_relations"] = [{"kind": "operation", **where}, {
        "kind": "assertion", **where, "expectation_id": expectation["expectation_id"], "assertion_id": expectation["assertions"][0]["assertion_id"]}]
    return artifact


def test_mode_b_delivery_walks_from_ready_to_the_report(tmp_path: Path, mode_b_pack: Path) -> None:
    from tests.p4_flow import human_run
    from tests.test_automation_revision_budget import review
    from tests.test_review_two_commands import _flags

    pack = mode_b_pack
    assert not (pack / "tools" / "review_legacy.py").exists() and (pack / "tools" / "exec_commands.py").is_file()
    front = _front(pack, tmp_path, "local-pilot-v1")
    effective = _cases_to_effective(pack, front)
    _status(pack, front["project"], front["run_root"].name, "automation", "model-request")  # after the canonical review the automation is next
    project, run_root, tmp = front["project"], front["run_root"], front["tmp"]
    run = _flags(front)
    document = json.loads((tmp / "candidate.json").read_bytes())
    code, requested = _api(pack, "model-request", *run, "--stage", "tc-to-autotest:r1", "--model-id", "model-automation", "--invocation-id", "generator-1",
                           "--input-digest", effective["document_digest"], "--input-digest", effective["effective_bundle_receipt_digest"])
    assert code == 0, requested
    artifact = _automation_answer(document, effective)
    code, answered = _api(pack, "model-response", *run, "--stage", "tc-to-autotest:r1", "--artifact", _put(tmp / "automation.json", artifact))
    assert code == 0, answered
    _status(pack, project, run_root.name, "automation_review", "open-automation-boundary")
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
    _status(pack, project, run_root.name, "materialize", "materialize")
    # the canonical review has one way in a delivery without review_legacy: review-open / review-select
    code, refused = _api(pack, "review-requested", *run, "--stage", "tc-reviewer:canonical", "--digest", automation_digest)
    assert code == 2 and refused["code"] == "LEGACY_REVIEW_NOT_IN_PACKAGE" and "review-open" in refused["fix"] and "review-select" in refused["fix"], refused
    # materialize → the person runs the printed command → ingest → report
    shared = ("--project", str(project), "--run", run_root.name, "--module", "python-pytest")
    code, printed = _cli(pack, "tools.run_pipeline", "materialize", *shared)
    assert code == 0 and printed["stage"] == "materialize", ("MB1: the static review was not taken", printed)
    _status(pack, project, run_root.name, "human_run", "ingest", "human")
    _writes_stay_in_bounds(pack, front, *printed["files_to_review"])
    assert human_run(project, printed).returncode == 0
    front["project_before"] = _tree(project)  # what the person's own run wrote is the person's, not a command of the pack
    code, ingested = _cli(pack, "tools.run_pipeline", "ingest", *shared)
    assert code == 0 and ingested["verdict"] == "PASS", ingested
    _status(pack, project, run_root.name, "done", "report")  # the ingest closes the attempt
    code, reported = _cli(pack, "tools.run_pipeline", "report", "--project", str(project), "--run", run_root.name)
    assert code == 0 and (run_root / "report.json").is_file(), reported
    code, finished = _cli(pack, "tools.run_pipeline", "finish", "--project", str(project), "--run", run_root.name)
    assert "MODE_B_NOT_IN_PACKAGE" not in json.dumps(finished), finished
    _writes_stay_in_bounds(pack, front)


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
