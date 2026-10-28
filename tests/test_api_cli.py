"""FaT-10: thin commands over the functions the Skills tell the agent to call — one command, one function, JSON out."""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted([*ROOT.glob("skills/*/SKILL.md"), *ROOT.glob("skills/*/references/*.md"), ROOT / "USAGE.md", ROOT / "QUICKSTART.md"])
NAMED = re.compile(r"python -m tools\.([a-z_]+)(?: ([a-z][a-z-]+)(?![\w/.-]))?")
SUBCOMMAND = re.compile(r"(?<![\w.])tools\.(api|orchestrate_test_case_revision|run_pipeline|context_marker_parts) ([a-z][a-z-]+)")


def _named_commands() -> list[tuple[str, str | None]]:
    found = set()
    for path in DOCS:
        text = path.read_text(encoding="utf-8")
        for module, sub in [*NAMED.findall(text), *SUBCOMMAND.findall(text)]:
            found.add((module, sub or None))
    return sorted(found, key=lambda item: (item[0], item[1] or ""))


def _api(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    from tools import api

    code = api.main(argv)
    out = capsys.readouterr().out
    assert out.count("\n") == 1, out
    return code, json.loads(out)


def _plain(value):
    from tools.api import plain

    return json.loads(json.dumps(plain(value), ensure_ascii=False))


# --------------------------------------------------------------------------------------
# (1)–(2) every command the docs name exists and answers JSON; the docs import nothing
# --------------------------------------------------------------------------------------

def test_the_docs_name_the_api_commands() -> None:
    named = _named_commands()
    assert ("api", "model-request") in named and ("api", "model-response") in named and ("api", "read-effective") in named, named
    assert ("orchestrate_test_case_revision", "open-session") in named and ("orchestrate_test_case_revision", "select") in named, named


@pytest.mark.parametrize("module,sub", _named_commands())
def test_a_named_command_exists_and_answers_json(module: str, sub: str | None) -> None:
    argv = [sys.executable, "-m", f"tools.{module}", *([sub] if sub else []), "--no-such-flag"]
    result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
                            env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"})
    payload = json.loads(result.stdout)
    assert result.returncode == 2 and payload["status"] == "error", (module, sub, result.stdout, result.stderr)
    assert "invalid choice" not in result.stdout, (module, sub, result.stdout)


def test_the_docs_tell_no_python_imports_of_the_package() -> None:
    call = re.compile(r"from tools|import tools|tools\.[a-z_]+\.[a-z_]+\(|\b(?:pilot_state|batch_assembly|canonical_document|automation_validation)\.[a-z_]+")
    offenders = [f"{path.relative_to(ROOT)}: {match.group(0)}" for path in DOCS for match in call.finditer(path.read_text(encoding="utf-8"))]
    assert offenders == []


def test_every_api_command_is_registered_and_answers_json_on_bad_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    from tools import api

    assert set(api.COMMANDS) >= {
        "model-request", "model-response", "read-model-artifact", "read-context", "plan-batches", "with-digest",
        "validate-fragment", "assemble", "publish-artifact", "document-digest", "validate-canonical", "read-ledger",
        "read-effective", "open-automation-boundary", "read-receipt", "automation-digest", "relations-digest",
        "validate-automation", "runner-compat",
    }
    for name in api.COMMANDS:
        with pytest.raises(SystemExit) as stop:
            api.main([name, "--no-such-flag"])
        payload = json.loads(capsys.readouterr().out)
        assert stop.value.code == 2 and payload["status"] == "error", (name, payload)


# --------------------------------------------------------------------------------------
# (3)–(4) the command returns exactly what the function returns, refusals included
# --------------------------------------------------------------------------------------

def _marker_run(tmp_path: Path):
    from tests.test_marker_parts import _run

    tmp_path.mkdir(parents=True, exist_ok=True)
    root, attempt_id, baseline, receipts = _run(tmp_path, files=1)
    digests = [baseline["requirements"]["digest"], baseline["inventory_digest"], receipts[0]["digest"]]
    return root, attempt_id, digests


def _marker_value() -> dict:
    from tests.test_marker_parts import _marker

    return _marker(["src/part0.py — наблюдение «ёлка» ∑"], ["пробел — нет ожидаемого результата"])


def _reversed(value):
    if isinstance(value, dict):
        return {key: _reversed(value[key]) for key in reversed(list(value))}
    if isinstance(value, list):
        return [_reversed(item) for item in value]
    return value


def test_model_request_and_response_match_the_direct_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    direct_root, direct_attempt, direct_digests = _marker_run(tmp_path / "direct")
    pilot_state.publish_model_request(direct_root, direct_attempt, "context-marker:baseline", model_id="m", invocation_id="inv",
                                      input_digests=direct_digests)
    direct = pilot_state.publish_model_stage_artifact(direct_root, direct_attempt, "context-marker:baseline", _marker_value())

    root, attempt_id, digests = _marker_run(tmp_path / "command")
    seen: list = []
    real = pilot_state.publish_model_request
    monkeypatch.setattr(pilot_state, "publish_model_request", lambda *a, **k: seen.append(real(*a, **k)) or seen[-1])
    code, request = _api(["model-request", "--run-root", str(root), "--attempt", attempt_id, "--stage", "context-marker:baseline",
                          "--model-id", "m", "--invocation-id", "inv", *sum((["--input-digest", item] for item in digests), [])], capsys)
    assert code == 0 and request["status"] == "ok" and request["command"] == "model-request", request
    assert request["result"] == _plain(seen[0])
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(_reversed(_marker_value()), ensure_ascii=False, indent=3), encoding="utf-8")
    code, response = _api(["model-response", "--run-root", str(root), "--attempt", attempt_id, "--stage", "context-marker:baseline",
                           "--artifact", str(answer)], capsys)
    assert code == 0, response
    assert response["result"]["content_digest"] == direct["content_digest"]
    assert set(response["result"]) == set(_plain(direct))
    code, read = _api(["read-model-artifact", "--run-root", str(root), "--attempt", attempt_id, "--stage", "context-marker:baseline",
                       "--digest", direct["content_digest"]], capsys)
    assert code == 0 and read["result"] == _plain(pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", direct["content_digest"]))


def _error_of(call) -> dict:
    try:
        call()
    except Exception as error:  # the command must report the same failure
        return {"code": getattr(error, "code", type(error).__name__), "message": str(error)}
    raise AssertionError("the direct call did not fail")


def test_a_refused_request_and_a_second_response_fail_like_the_function(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import pilot_state

    direct_root, direct_attempt, direct_digests = _marker_run(tmp_path / "direct")
    expected = _error_of(lambda: pilot_state.publish_model_request(direct_root, direct_attempt, "context-marker:baseline", model_id="m",
                                                                   invocation_id="inv", input_digests=list(reversed(direct_digests))))
    root, attempt_id, digests = _marker_run(tmp_path / "command")
    before = pilot_state.derive_state(root)
    code, refused = _api(["model-request", "--run-root", str(root), "--attempt", attempt_id, "--stage", "context-marker:baseline",
                          "--model-id", "m", "--invocation-id", "inv", *sum((["--input-digest", item] for item in reversed(digests)), [])], capsys)
    assert code == 2 and refused["status"] == "error" and {"code": refused["code"], "message": refused["message"]} == expected, refused
    assert pilot_state.derive_state(root) == before

    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="inv", input_digests=digests)
    pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", _marker_value())
    from tests.test_marker_parts import _marker

    other = _marker(["src/part0.py — другой ответ"])
    expected = _error_of(lambda: pilot_state.publish_model_stage_artifact(root, attempt_id, "context-marker:baseline", other))
    answer = tmp_path / "answer.json"
    answer.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    code, repeated = _api(["model-response", "--run-root", str(root), "--attempt", attempt_id, "--stage", "context-marker:baseline",
                           "--artifact", str(answer)], capsys)
    assert code == 2 and {"code": repeated["code"], "message": repeated["message"]} == expected, repeated


def test_a_non_strict_answer_is_refused_before_the_function(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import pilot_state

    root, attempt_id, digests = _marker_run(tmp_path)
    pilot_state.publish_model_request(root, attempt_id, "context-marker:baseline", model_id="m", invocation_id="inv", input_digests=digests)
    before = pilot_state.derive_state(root)
    answer = tmp_path / "answer.json"
    answer.write_text('{"stage": "context-marker", "stage": "x"}', encoding="utf-8")
    with pytest.raises(SystemExit) as stop:
        _api(["model-response", "--run-root", str(root), "--attempt", attempt_id, "--stage", "context-marker:baseline", "--artifact", str(answer)], capsys)
    assert stop.value.code == 2 and json.loads(capsys.readouterr().out)["status"] == "error"
    assert pilot_state.derive_state(root) == before


def test_publish_artifact_passes_the_file_bytes_as_they_are(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import hashlib

    root, attempt_id, _digests = _marker_run(tmp_path)
    data = '{"б": 1,  "a": [2, 1]}\r\n'.encode("utf-8")
    source = tmp_path / "plan.json"
    source.write_bytes(data)
    code, published = _api(["publish-artifact", "--run-root", str(root), "--attempt", attempt_id, "--name", "batch-plan.json", "--file", str(source)], capsys)
    assert code == 0 and published["result"]["digest"] == "sha256:" + hashlib.sha256(data).hexdigest(), published
    assert (root / published["result"]["path"]).read_bytes() == data


def test_the_automation_readers_match_the_direct_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _project
    from tools import pilot_state

    _project_path, run_root, attempt_id, values = _project(tmp_path)
    code, effective = _api(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id], capsys)
    assert code == 0 and effective["result"] == _plain(pilot_state.read_effective_canonical(run_root, attempt_id))
    code, boundary = _api(["read-receipt", "--run-root", str(run_root), "--attempt", attempt_id, "--kind", "automation-review-boundary-r1",
                           "--event", "ARTIFACT_READ_BACK"], capsys)
    assert code == 0 and boundary["result"] == _plain(pilot_state.read_attempt_receipt(run_root, attempt_id, "automation-review-boundary-r1", "ARTIFACT_READ_BACK"))
    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps({"automation_revision": 3}), encoding="utf-8")
    expected = _error_of(lambda: pilot_state.open_automation_review_boundary(run_root, attempt_id, {"automation_revision": 3}))
    code, refused = _api(["open-automation-boundary", "--run-root", str(run_root), "--attempt", attempt_id, "--facts", str(facts)], capsys)
    assert code == 2 and {"code": refused["code"], "message": refused["message"]} == expected, refused

    from tools.automation_validation import automation_sha256
    from tools.canonical_document import document_sha256

    document = tmp_path / "document.json"
    document.write_text(json.dumps(values["canonical_document"], ensure_ascii=False), encoding="utf-8")
    automation = tmp_path / "automation.json"
    automation.write_text(json.dumps(values["automation_artifact"], ensure_ascii=False), encoding="utf-8")
    assert _api(["document-digest", "--document", str(document)], capsys)[1]["result"] == document_sha256(values["canonical_document"])
    assert _api(["automation-digest", "--automation", str(automation)], capsys)[1]["result"] == automation_sha256(values["automation_artifact"])


# --------------------------------------------------------------------------------------
# (5) validators: diagnostics mean invalid, exit 1, the list unchanged
# --------------------------------------------------------------------------------------

def test_a_validator_with_diagnostics_is_invalid_with_the_same_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import validate_canonical_document

    document = automated_document()
    path = tmp_path / "document.json"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    code, valid = _api(["validate-canonical", "--document", str(path)], capsys)
    assert code == 0 and valid["status"] == "ok" and valid["result"] == [], valid
    document["schema_version"] = "9.9.9"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    code, invalid = _api(["validate-canonical", "--document", str(path)], capsys)
    assert code == 1 and invalid["status"] == "invalid" and invalid["result"] == _plain(validate_canonical_document(document)), invalid


# --------------------------------------------------------------------------------------
# (6) --out stays in the run and never makes drift
# --------------------------------------------------------------------------------------

def test_out_writes_the_result_into_the_run_and_prints_a_short_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _project
    from tools import pilot_state

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    out = run_root / "tmp" / "effective.json"
    code, line = _api(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id, "--out", str(out)], capsys)
    written = out.read_bytes()
    assert code == 0 and set(line) == {"status", "command", "out", "bytes", "sha256"} and line["bytes"] == len(written), line
    assert json.loads(written) == _plain(pilot_state.read_effective_canonical(run_root, attempt_id))
    outside = tmp_path / "project" / "src" / "effective.json"
    code, refused = _api(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id, "--out", str(outside)], capsys)
    assert code == 2 and refused["code"] == "API_OUT_OUTSIDE_RUN" and not outside.exists(), refused


@pytest.mark.parametrize("name", ["state.json", "model-stage-artifacts/x.json", "tmp/../state.json", "tmp"])
def test_out_outside_the_run_tmp_directory_is_refused_and_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str) -> None:
    from tests.test_finish import _project

    _project_path, run_root, attempt_id, _values = _project(tmp_path)
    before = {path: path.read_bytes() for path in run_root.rglob("*") if path.is_file()}
    code, refused = _api(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id, "--out", str(run_root / name)], capsys)
    assert code == 2 and refused["code"] == "API_OUT_OUTSIDE_RUN", refused
    assert {path: path.read_bytes() for path in run_root.rglob("*") if path.is_file()} == before


def test_a_file_written_by_out_makes_no_drift_for_status(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan, _status
    from tests.test_automation_revision_budget import automated_document

    project = _python_project(tmp_path, runtime=True)
    code, scan = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, scan
    run_root = project / ".pilot-runs" / scan["run_id"]
    source = run_root / "tmp" / "document.json"
    source.parent.mkdir()
    source.write_text(json.dumps(automated_document(), ensure_ascii=False), encoding="utf-8")
    code, _line = _api(["document-digest", "--document", str(source), "--out", str(run_root / "tmp" / "digest.json")], capsys)
    assert code == 0 and (run_root / "tmp" / "digest.json").is_file()
    assert "drift" not in _status(project, scan["run_id"], monkeypatch)


def test_a_file_written_by_out_makes_no_drift_for_exec(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_finish import _args, _project
    from tools.run_pipeline import cmd_exec

    project, run_root, attempt_id, _values = _project(tmp_path)
    code, _line = _api(["read-effective", "--run-root", str(run_root), "--attempt", attempt_id, "--out", str(run_root / "tmp" / "effective.json")], capsys)
    assert code == 0
    code = cmd_exec(_args(project, run_root))
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and output["verdict"] == "PASS", output


def test_results_are_plain_json(tmp_path: Path) -> None:
    from tools.api import plain

    value = plain({"bytes": b"ab", "pair": ("a", 1), "path": Path("x") / "y", "map": {("a", "b"): {"k": 1}}})
    assert value["bytes"] == {"bytes": 2, "sha256": "sha256:fb8e20fc2e4c3f248c60c39bd652f3c1347298bb977b8b4d5903b85055620603"}
    assert value["pair"] == ["a", 1] and value["path"] == str(Path("x") / "y")
    assert value["map"] == [{"key": ["a", "b"], "value": {"k": 1}}]


# --------------------------------------------------------------------------------------
# (7) the no-driver boundary: one package function per command, no process, no next step
# --------------------------------------------------------------------------------------

def test_each_command_calls_exactly_one_package_function() -> None:
    source = (ROOT / "tools" / "api.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    modules = {alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "tools"
               for alias in node.names}
    assert {"pilot_state", "batch_assembly", "canonical_document", "automation_validation", "run_tests"} <= modules
    handlers = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name.startswith("_cmd_")]
    from tools import api

    assert len(handlers) == len(api.COMMANDS)
    for handler in handlers:
        calls = [node for node in ast.walk(handler) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and isinstance(node.func.value, ast.Name) and node.func.value.id in modules]
        assert len(calls) == 1, handler.name
    assert "subprocess" not in source and "os.system" not in source and "next_step" not in source


def test_with_digest_is_public_and_the_old_name_is_the_same_function() -> None:
    from tools import batch_assembly

    assert batch_assembly._with_digest is batch_assembly.with_digest
