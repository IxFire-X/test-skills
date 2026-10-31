"""FaT-AU2 (audit C2): publish writes its candidate receipt to --out, and a repeated publish of the same candidate is idempotent.

After /clear the stdout of publish is gone; the receipt is needed by open-session and select (--candidate-receipt).
Publishing the same bytes again gives the same receipt and writes nothing; another candidate is refused as before.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest


def _published(tmp_path: Path) -> tuple[Path, str, dict, Path]:
    """A run whose candidate r1 is already published once (by the reviewer protocol fixture) into run/bundle."""
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run

    run_root, attempt_id = _run(tmp_path, "cases-only-v1", flag=False)
    document = automated_document()
    _reviewer_protocol_inputs(document, run_root, attempt_id, bundle_root=run_root / "bundle")
    return run_root, attempt_id, document, run_root / "bundle"


def _publish(run_root: Path, attempt_id: str, document: dict, output_dir: Path, capsys: pytest.CaptureFixture[str],
             out: Path | None = None) -> tuple[int, dict]:
    from tools.orchestrate_test_case_revision import main

    candidate = run_root / "tmp" / "candidate-again.json"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    argv = ["publish", "--candidate", str(candidate), "--output-dir", str(output_dir), "--run-root", str(run_root), "--attempt-id", attempt_id]
    code = main(argv + (["--out", str(out)] if out is not None else []))
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def _assembly_events(run_root: Path) -> list[dict]:
    from tools.pilot_state import derive_state

    return [event for event in derive_state(run_root)["events"] if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"]


def test_a_lost_stdout_is_recovered_by_publishing_the_same_candidate_again_with_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.orchestrate_test_case_revision import _load_receipt

    run_root, attempt_id, document, bundle = _published(tmp_path)
    capsys.readouterr()
    events = _assembly_events(run_root)
    out = run_root / "tmp" / "candidate-receipt.json"
    code, printed = _publish(run_root, attempt_id, document, bundle, capsys, out)
    assert code == 0 and printed["status"] == "published" and printed["idempotent"] is True
    assert json.loads(out.read_text(encoding="utf-8")) == printed["candidate_bundle_receipt"]
    assert printed["out"]["out"] == str(out.resolve())
    _load_receipt(out)  # the file is what open-session and select take as --candidate-receipt
    assert _assembly_events(run_root) == events, "nothing is written to the journal"


def test_another_candidate_is_refused_as_before_and_nothing_is_written(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_root, attempt_id, document, bundle = _published(tmp_path)
    capsys.readouterr()
    events = _assembly_events(run_root)
    before = sorted(path.name for path in bundle.iterdir())
    other = copy.deepcopy(document)
    other["test_cases"][0]["title"] += " (иначе)"
    code, printed = _publish(run_root, attempt_id, other, run_root / "bundle-other", capsys)
    assert code == 2 and printed["status"] == "error" and "already published" in printed["message"]
    assert _assembly_events(run_root) == events and sorted(path.name for path in bundle.iterdir()) == before
    assert not (run_root / "bundle-other").exists(), "refused before any bundle file is written"


def test_out_outside_the_run_tmp_is_refused_before_publishing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run_root, attempt_id, document, bundle = _published(tmp_path)
    capsys.readouterr()
    code, printed = _publish(run_root, attempt_id, document, bundle, capsys, tmp_path / "receipt.json")
    assert code == 2 and "--out must name a file under" in printed["message"] and not (tmp_path / "receipt.json").exists()
