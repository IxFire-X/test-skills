"""Package Б of the full review (handoff/v08/evidence-plan.md): the evidence of a human run reaches the final record.

E1: one test failed and another was skipped (the stand: a test without its variable is SKIPPED) — the ingest wrote FAIL as
authoritative, the evidence check awaited UNKNOWN, and the finalization stopped on «authoritative execution trace cannot be built»."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from tests import test_project_native_pytest as native
from tests.test_automation_revision_budget import automation
from tests.p4_flow import run_the_build

TWO_TESTS = """import pytest


def test_selected() -> None:
    assert False, "the product answers otherwise"


def test_skipped() -> None:
    pytest.skip("BASE_URL is not set")
"""


def _two_tests(document: dict, **options) -> dict:
    """The automation of the fixture with a second test bound to the same step: one fails, one is skipped."""
    artifact = automation(document, **options)
    body = artifact["artifacts"]
    body["generated_files"][0].update(content=TWO_TESTS, content_digest="sha256:" + hashlib.sha256(TWO_TESTS.encode("utf-8")).hexdigest())
    body["generated_symbols"].append({"file_id": "FILE-products", "symbol_id": "SYMBOL-skipped",
                                      "locator": {"kind": "python_module_function", "function_name": "test_skipped"}})
    body["implementation_relations"] = [{**row, "symbol_id": symbol} for row in body["implementation_relations"] for symbol in ("SYMBOL-products", "SYMBOL-skipped")]
    return artifact


def test_a_failed_test_beside_a_skipped_one_is_a_final_fail(tmp_path, capsys, monkeypatch) -> None:
    from tests.test_exec_prepares import _project

    monkeypatch.setattr(native, "automation", _two_tests)
    project, run_root, _attempt_id = _project(tmp_path, generated_content=TWO_TESTS)
    code, output = run_the_build(project, run_root, capsys)
    assert code == 1 and output["verdict"] == "FAIL" and output["finalization"] == "complete", output  # 1: a test failed


@pytest.mark.parametrize("refusal", ["finalization", "delta"])
def test_the_ingest_names_the_refusal_of_the_dispositions(tmp_path, capsys, monkeypatch, refusal) -> None:
    """F1: the refusal of the dispositions keeps its words — once lost under «durable execution closure evidence is unavailable»."""
    from tests.test_exec_prepares import _project
    from tools import finalize_attempt
    from tools.generated_delta import GeneratedDeltaError

    def refuse(*_args, **_kwargs):
        if refusal == "delta":
            raise GeneratedDeltaError("MATERIALIZATION_CONFLICT", "delta digest does not verify")
        raise finalize_attempt.FinalizationError("generated delta digest does not verify")

    monkeypatch.setattr(finalize_attempt, "decide_dispositions", refuse)
    project, run_root, _attempt_id = _project(tmp_path)
    code, output = run_the_build(project, run_root, capsys)
    assert code == 2 and output["message"].endswith("delta digest does not verify"), output
    assert ("MATERIALIZATION_CONFLICT" in output["message"]) == (refusal == "delta")


def test_a_damaged_reviewer_ledger_is_no_run_before_the_review(tmp_path, capsys) -> None:
    """F2: a ledger that was written and does not read is a review that began — LEFT_BEFORE_REVIEW does not leave the run."""
    from tests.test_f1 import _pipeline
    from tests.test_review_requested import _generated_run
    from tests.test_review_two_commands import _open

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    ledger = next((run["run_root"] / "reviewer-session-ledgers" / run["attempt_id"]).glob("*.json"))
    ledger.write_bytes(b"{")
    code, refused = _pipeline(capsys, ["finish", "--project", str(run["project"]), "--run", run["run_root"].name, "--abandon", "LEFT_BEFORE_REVIEW"])
    assert code == 2 and refused["reason"] == "ABANDON_NOT_NEEDED", refused


# J3: a reader of the earlier runs (existing_tests) holds the lock of each one for its read only, not to the end of the process,
# and waits for a short command of another process as any command does
_READER = """
import sys, time
from pathlib import Path
from tools import existing_tests
project, flags = Path(sys.argv[1]), Path(sys.argv[2])
print("unread", existing_tests.own_files(project, {"files": []})[1], flush=True)
(flags / "read").write_text("x")
end = time.time() + 60
while time.time() < end and not (flags / "release").exists():
    time.sleep(0.02)
"""


def test_a_reader_of_the_old_runs_leaves_them_free(tmp_path) -> None:
    from tests.test_run_lock import _STATUS, _answer, _run, _spawn, _wait_for

    root = _run(tmp_path)
    (root / "materialization-ownership").mkdir()
    flags = tmp_path / "flags"
    flags.mkdir()
    reader = _spawn(_READER, tmp_path, flags)
    _wait_for(flags / "read")
    answer = _answer(_spawn(_STATUS, tmp_path, root.name, flags, 0.3))
    (flags / "release").write_text("x")
    assert reader.wait(timeout=60) == 0
    assert answer["status"] == "ok", answer


def test_a_reader_of_the_old_runs_waits_for_a_short_holder_and_reads(tmp_path) -> None:
    from tests.test_run_lock import _HOLDER, _run, _spawn, _wait_for

    root = _run(tmp_path)
    (root / "materialization-ownership").mkdir()
    flags = tmp_path / "flags"
    flags.mkdir()
    holder = _spawn(_HOLDER, root, flags)
    _wait_for(flags / "held")
    reader = _spawn(_READER, tmp_path, flags)
    time.sleep(1)
    assert not (flags / "read").exists()
    (flags / "release").write_text("x")
    out, _err = reader.communicate(timeout=60)
    assert holder.wait(timeout=60) == 0
    assert "unread []" in out, out


def test_the_approved_words_of_e2() -> None:
    """E2: the texts approved by the user («Утверждаю», texts-approval-20261127 п. 7), word for word."""
    from tools.fixes import FIXES

    root = Path(__file__).resolve().parents[1]
    assert FIXES["INGEST_REPORT_OUTSIDE_MODULE"] == ("отчёты читаются только там, куда их пишет команда из materialize (report_location, без ссылок): "
                                                    "перенесите XML туда и повторите ingest без --reports")
    assert ("ingest читает их только оттуда, и агенту путь называть не нужно. Сборка положила их в другое место — перенесите их туда: "
            "отчёт из другой папки модуля или из `<run>/incoming/` прогон не закроет.") in (root / "HOW-TO-USE-MODE-B.md").read_text(encoding="utf-8")
    assert ("`python -m tools.run_pipeline ingest --project <abs> --module <ID> --run <id>` (отчёты — там, куда их пишет напечатанная команда)"
            in (root / "USAGE.md").read_text(encoding="utf-8"))
