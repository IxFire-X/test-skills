"""FaT-SK (hunt 3, 3.4): the skeleton of an import is the last input of every tc-generator batch, so plan-batches counts its
bytes in the step input of every batch — a large skeleton splits the plan or says plainly that it does not fit."""
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


def test_the_skeleton_receipt_counts_in_the_step_input_by_its_file_bytes(run: dict) -> None:
    plain = _plan(run, None)
    counted = _plan(run, run["receipt"])
    skeleton = run["receipt_path"].stat().st_size
    # the receipt file is its canonical JSON and a closing newline
    assert _step_input(counted) - _step_input(plain) == skeleton - 1, (plain.notes, counted.notes)
    assert f"скелет импорта {skeleton - 1} Б" in counted.notes[-1]
    assert not any("скелет" in note for note in plain.notes)  # without the skeleton the plan bytes are as before


def test_a_large_skeleton_splits_the_plan_or_says_it_does_not_fit(run: dict) -> None:
    from tools.batch_assembly import WINDOW_SHARE

    skeleton = run["receipt_path"].stat().st_size - 1
    wide = _plan(run, run["receipt"], window=100_000_000)
    step, answer = _step_input(wide), int(re.search(r"оценка ответа (\d+) Б", wide.notes[0]).group(1))
    # without the skeleton in the step input the answer would fit (cap + skeleton >= answer); with it the plan splits
    window = int((step + answer - skeleton // 2) / WINDOW_SHARE)
    split = _plan(run, run["receipt"], window=window)
    assert len(split["batches"]) == 2, split.notes
    # the skeleton leaves no room for any answer: one batch and the plain reason, not «fits»
    window = int((step - skeleton // 2) / WINDOW_SHARE)
    tight = _plan(run, run["receipt"], window=window)
    assert len(tight["batches"]) == 1 and "не оставляет места ответу" in tight.notes[0], tight.notes


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
    assert f"скелет импорта {run['receipt_path'].stat().st_size - 1} Б" in capsys.readouterr().out


def test_step_inputs_of_a_batch_read_the_whole_skeleton_receipt(run: dict) -> None:
    from tests.test_cases_import import _generator_request
    from tools.step_size import request_files

    four = (run["marker"], run["context"]["digest"], run["plan_digest"], run["header_digest"])
    assert _generator_request(run, *four, run["imported"]["digest"])[0] == 0
    batch_id = run["plan"]["batches"][0]["batch_id"]
    files = request_files(run["root"], f"tc-generator:{batch_id}")
    [row] = [row for row in files["files"] if row["digest"] == run["imported"]["digest"]]
    assert Path(row["path"]) == run["receipt_path"] and row["bytes"] == run["receipt_path"].stat().st_size, files
