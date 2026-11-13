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

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted([*ROOT.glob("skills/*/SKILL.md"), *ROOT.glob("skills/*/references/*.md"), ROOT / "USAGE.md", ROOT / "README.md", ROOT / "HOW-TO-USE.md"])
NAMED = re.compile(r"python -m tools\.([a-z_]+)(?: ([a-z][a-z-]+)(?![\w/.-]))?")
SUBCOMMAND = re.compile(r"(?<![\w.])tools\.(api|orchestrate_test_case_revision|run_pipeline) ([a-z][a-z-]+)")


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
    assert not {("api", "model-request"), ("api", "model-response"), ("api", "read-effective")} & set(named), named  # V5-B1: the docs name no command of the old path
    # V4: the review goes through the one cycle — the docs name tools.api prepare/submit
    assert ("api", "prepare") in named and ("api", "submit") in named, named


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

    assert set(api.COMMANDS) == {"prepare", "submit", "validate-canonical"}  # V5-B3: the cycle of the agent and the check of a candidate
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


def test_prepare_and_submit_match_the_direct_calls(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state, stage_steps

    direct_root, direct_attempt, direct_digests = _marker_run(tmp_path / "direct")
    pilot_state.publish_model_request(direct_root, direct_attempt, "context-marker:baseline", model_id="m", invocation_id="inv",
                                      input_digests=direct_digests)
    direct = pilot_state.publish_model_stage_artifact(direct_root, direct_attempt, "context-marker:baseline", _marker_value())

    root, attempt_id, _digests = _marker_run(tmp_path / "command")
    seen: list = []
    real = pilot_state.publish_model_request
    monkeypatch.setattr(pilot_state, "publish_model_request", lambda *a, **k: seen.append(real(*a, **k)) or seen[-1])
    code, request = _api(["prepare", "--run-root", str(root), "--stage", "context-marker:baseline", "--model-id", "m", "--invocation-id", "inv"], capsys)
    assert code == 0 and request["status"] == "ok" and request["command"] == "prepare", request
    assert request["result"]["request_digest"] == seen[0]["digest"] and request["result"]["answer_slot"] == str(stage_steps.slot(root, "context-marker:baseline"))
    answer = stage_steps.slot(root, "context-marker:baseline")
    answer.write_text(json.dumps(_reversed(_marker_value()), ensure_ascii=False, indent=3), encoding="utf-8")
    code, response = _api(["submit", "--run-root", str(root), "--stage", "context-marker:baseline"], capsys)
    assert code == 0 and response["command"] == "submit", response
    assert response["result"]["content_digest"] == direct["content_digest"]
    assert set(_plain(direct)) - {"artifact"} <= set(response["result"])  # PF-1: the receipt of the record, not the body of the answer
    assert pilot_state.read_model_stage_artifact(root, attempt_id, "context-marker:baseline", direct["content_digest"])["artifact"] == _marker_value()


from tests.journal_helpers import assert_refused_once, refusal_log, state_minus_refusals


def test_a_non_strict_answer_is_refused_before_the_function(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import stage_steps

    root, attempt_id, _digests = _marker_run(tmp_path)
    assert _api(["prepare", "--run-root", str(root), "--stage", "context-marker:baseline"], capsys)[0] == 0
    before, seen = state_minus_refusals(root), refusal_log(root)
    stage_steps.slot(root, "context-marker:baseline").write_text('{"stage": "context-marker", "stage": "x"}', encoding="utf-8")
    code, refused = _api(["submit", "--run-root", str(root), "--stage", "context-marker:baseline"], capsys)
    assert code == 2 and refused["status"] == "error" and refused["code"] == "ANSWER_JSON_INVALID" and refused["fix"]  # F4b: read in main, not by argparse
    assert state_minus_refusals(root) == before
    assert_refused_once(root, seen, "ANSWER_JSON_INVALID", "submit")


def test_the_removed_digest_commands_are_gone_everywhere() -> None:
    """FaT-DG: the two old digest commands are gone."""
    from tools import api

    removed = ("with" + "-digest", "publish" + "-artifact")
    assert not set(removed) & set(api.COMMANDS)
    offenders = [str(path.relative_to(ROOT)) for path in [*DOCS, *sorted((ROOT / "tests").glob("*.py"))]
                 if any(name in path.read_text(encoding="utf-8") for name in removed)]
    assert offenders == []


# --------------------------------------------------------------------------------------
# (5) validators: diagnostics mean invalid, exit 1, the list unchanged
# --------------------------------------------------------------------------------------

def test_a_validator_with_diagnostics_is_invalid_with_the_same_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_automation_revision_budget import automated_document
    from tools.canonical_document import validate_canonical_document

    from tests.test_output_guard import _run

    document = automated_document()
    path = _run(tmp_path)[1] / "tmp" / "document.json"  # SEC-A: a command without a run reads a file in a run
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    code, valid = _api(["validate-canonical", "--document", str(path)], capsys)
    assert code == 0 and valid["status"] == "ok" and valid["result"] == [], valid
    document["schema_version"] = "9.9.9"
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    code, invalid = _api(["validate-canonical", "--document", str(path)], capsys)
    assert code == 1 and invalid["status"] == "invalid" and invalid["result"] == _plain(validate_canonical_document(document)), invalid


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
    # V5-B3: the cycle (``stage_steps``) and the check of a candidate (``canonical_document``); no module of mode B is imported by api
    assert {"stage_steps", "canonical_document"} <= modules
    assert not {"automation_validation", "run_tests", "execution_gate", "pilot_state"} & modules
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


def test_the_tester_documents_carry_the_p9_sections_and_no_word_of_the_security_review() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    how_to = (ROOT / "HOW-TO-USE.md").read_text(encoding="utf-8")
    assert "## Словарь терминов" in readme and "`SELF`" in readme  # PT: the delivery has no section about automated tests
    for term in ("run", "attempt", "revision", "accepted", "verification", "coverage"):
        assert f"({term})" in readme, term
    table = how_to.split("## 8. Частые остановки", 1)[1].split("\n## ", 1)[0]
    for code in ("MODEL_OUTPUT_TRUNCATED", "TASK_DOC_OUTSIDE_CONTEXT", "MODULE_UNKNOWN", "EXECUTION_COORDINATES",
                 "FINISH_FACT_UNDETERMINED", "ZEPHYR_CSV_COLUMNS"):
        assert f"| `{code}` |" in table, code
    assert "## Памятка: модель запуталась" in how_to and "`next`" in how_to and "--new-run" in how_to
    for text in (readme, how_to):
        assert "безопасн" not in text.lower()
