"""FaT-SK → FaT-SKB (hunt 3, 3.4): a tc-generator batch of an import reads the skeleton of its own cases only, so plan-batches
puts no whole skeleton into the step input of every batch — the skeleton bytes of each case join the share of its batch."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


def _plan(run: dict, receipt: dict | None, window: int | None = None):
    from tools.batch_assembly import plan_batches_for_project
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    project = run["project"]
    if window is not None:
        document = parse_skillsrc_bytes((project / ".skillsrc").read_bytes())
        document.setdefault("limits", {})["step_window_bytes"] = window
        (project / ".skillsrc").write_bytes(_payload(document))
    evidence = {"header_digest": run["header_digest"], "context_receipt_digest": run["context"]["digest"]}
    return plan_batches_for_project(run["requirements"], evidence, project, [], run["context"], receipt)


def _step_input(plan) -> int:
    return int(re.search(r"вход шага (\d+) Б", " ".join(plan.notes)).group(1))


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from tests.test_cases_import import _to_generator
    from tools import pilot_state

    run = _to_generator(tmp_path, monkeypatch)
    run["receipt_path"] = pilot_state._receipt_target(run["root"], run["attempt_id"], "imported-cases")
    run["receipt"] = json.loads(run["receipt_path"].read_bytes())
    run["requirements"] = [{key: item[key] for key in ("source_requirement_id", "display_order", "text")} | item
                           for item in run["plan"]["source_requirements"]]
    return run


def test_the_whole_skeleton_is_no_part_of_the_step_input_of_every_batch(run: dict) -> None:
    plain = _plan(run, None)
    counted = _plan(run, run["receipt"])
    assert _step_input(counted) == _step_input(plain), (plain.notes, counted.notes)
    assert any("её часть скелета импорта" in note for note in counted.notes)  # P9 adds the volume note last: not necessarily the last of all
    assert not any("скелет" in note for note in plain.notes)  # without an import the plan bytes are as before


def test_the_share_of_a_batch_includes_the_skeleton_bytes_of_its_cases(run: dict) -> None:
    from tools.batch_assembly import IMPORT_ANSWER_PER_HUMAN_BYTE, _human_bytes, canonical_bytes

    plan = _plan(run, run["receipt"], window=100_000_000)
    estimate = int(re.search(r"оценка ответа (\d+) Б", plan.notes[0]).group(1))
    cases = run["receipt"]["imported"]["cases"]
    assert estimate == sum(_human_bytes(case) * IMPORT_ANSWER_PER_HUMAN_BYTE + len(canonical_bytes(case)) for case in cases)


def test_plan_batches_takes_the_receipt_file_as_it_is(run: dict, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools import api

    files = {}
    for name, value in (("req", run["requirements"]), ("grouping", {"header_digest": run["header_digest"],
                                                                        "context_receipt_digest": run["context"]["digest"]}),
                        ("context", run["context"])):
        files[name] = tmp_path / f"{name}.json"
        files[name].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    assert api.main(["plan-batches", "--requirements", str(files["req"]), "--grouping", str(files["grouping"]),
                     "--project", str(run["project"]), "--input-file", str(files["req"]), "--context-receipt", str(files["context"]),
                     "--imported", str(run["receipt_path"])]) == 0
    assert "её часть скелета импорта" in capsys.readouterr().out


def test_step_inputs_of_a_batch_read_the_skeleton_of_that_batch(run: dict) -> None:
    from tests.test_cases_import import _generator_request, _skeleton
    from tools.pilot_state import _receipt_target
    from tools.step_size import request_files

    four = (run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"])
    assert _generator_request(run, *four, _skeleton(run))[0] == 0
    batch_id = run["plan"]["batches"][0]["batch_id"]
    files = request_files(run["root"], f"tc-generator:{batch_id}")
    [row] = [row for row in files["files"] if row["digest"] == _skeleton(run)]
    assert Path(row["path"]) == _receipt_target(run["root"], run["attempt_id"], "imported-cases-" + batch_id), files
    assert not any(row["digest"] == run["imported"]["digest"] for row in files["files"])  # the whole skeleton is no input
