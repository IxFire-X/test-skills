"""v0.6 fix4: a file of the run directory is no receipt until the journal binds it and its seal holds — the list the model reads is made of such files only;
the text of the model is escaped in the ``.md`` of a set."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tools import pilot_state, step_size


# ---- 1: a file that only names a digest binds nothing ---------------------------------------------------------------------------------------

def _header_row(run: dict, stage: str) -> tuple[dict, dict | None]:
    found = step_size.stage_inputs(run["run_root"], stage, write_views=False)
    return found, next((row for row in found["files"] if Path(row["path"]).parent.name == "generator-headers"), None)


@pytest.mark.parametrize("planted", [lambda digest: {"digest": digest}, lambda digest: {"x": {"digest": digest}}], ids=["own-digest-field", "nested-digest-field"])
def test_a_planted_file_that_names_the_digest_of_an_input_does_not_take_its_place(tmp_path: Path, capsys: pytest.CaptureFixture[str], planted) -> None:
    from tests.test_cut_model import _generator_stage, _prepare_argv
    from tests.test_review_open_recovery import _main

    run, stage = _generator_stage(tmp_path)
    assert _main(capsys, _prepare_argv(run, stage))[0] == 0
    found, header = _header_row(run, stage)
    extra = run["run_root"] / "prior-capabilities" / f"{run['attempt_id']}.planted.json"  # a place the glob of the journal reads; no event stands behind it
    extra.parent.mkdir(exist_ok=True)
    extra.write_text(json.dumps(planted(header["digest"])), encoding="utf-8")
    after, again = _header_row(run, stage)
    assert again is not None and again["path"] == header["path"] and str(extra) not in [row["path"] for row in after["files"]] and after["unresolved"] == found["unresolved"], after


# ---- 3: the ledger of the reviewer is the last revision, read by the strict reader --------------------------------------------------------------

def test_a_first_revision_of_the_ledger_that_is_gone_bad_does_not_stop_the_measure_of_the_review(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_cut_review import _evidence_verdict
    from tests.test_review_requested import _generated_run
    from tests.test_review_two_commands import _open, _select_verdict

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    assert _select_verdict(run, _evidence_verdict(run, "src/extra.py"))[0] == 0  # an evidence round: the ledger is published again, each revision under its digest
    root, attempt = run["run_root"], run["attempt_id"]
    revisions = [event for event in pilot_state.derive_state(root)["events"] if event.get("batch_id") == "reviewer-ledger-v1" and event["event_type"] == "ARTIFACT_READ_BACK"]
    assert len(revisions) > 1
    (root / "reviewer-session-ledgers" / attempt / (str(revisions[0]["artifact_digest"]).removeprefix("sha256:") + ".json")).write_text("garbage", encoding="utf-8")
    capsys.readouterr()
    code = step_size.main(["--project", str(run["project"]), "--run", root.name, "--stage", "tc-reviewer:canonical"])
    assert code == 0, capsys.readouterr().out


# ---- 4: «not reviewed» is decided by the journal, not by a file under published/ ----------------------------------------------------------------

def test_a_rejected_file_laid_beside_the_package_does_not_stop_the_unreviewed_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tests.test_closure_deadlocks import _run
    from tests.test_review_requested import _generated_run
    from tests.test_review_two_commands import _bundles, _cycle, _open
    from tools.orchestrate_test_case_revision import journal_assembly

    run = _generated_run(tmp_path)
    assert _open(run)[0] == 0
    assert _cycle("submit", run, "--abort", "REVIEW_UNUSABLE")[0] == 0
    candidate = journal_assembly(run["run_root"], run["attempt_id"])[0]
    laid = Path(_bundles(run)) / "elsewhere" / f"{candidate['document_id']}.r{candidate['revision']}.REJECTED.md"
    laid.parent.mkdir(parents=True)
    laid.write_text("> **ОТКЛОНЁН РЕВЬЮЕРОМ** (not the package's file)", encoding="utf-8")
    capsys.readouterr()
    _code, closed = _run(capsys, "finish", run["project"], run["run_root"])
    assert len(closed.get("unreviewed_files", [])) == 2, closed


# ---- 5: the text of the model in the ``.md`` of a set --------------------------------------------------------------------------------------------

def _grouped_with_parameter(name: str) -> dict:
    from tests.test_param_groups import grouped

    document = grouped()
    for requirement in document["requirements"]:
        for parameter in requirement.get("design", {}).get("parameters", []):
            if parameter["id"] == "P-first":
                parameter["name"] = name
    return document


@pytest.mark.parametrize("name", ["<img src=x onerror=alert(1)>", "[нажми](javascript:alert(1))"])
def test_the_name_of_the_parameter_of_a_group_is_no_markup_in_the_markdown(name: str) -> None:
    from tools.test_case_projections import render_markdown

    text = render_markdown(_grouped_with_parameter(name)).payload.decode("utf-8")
    assert not re.search(r"(?<!\\)<img", text) and not re.search(r"(?<!\\)\]\(javascript", text) and "PG-first-name" in text  # a sign is there only with its backslash


def test_a_harmless_name_of_the_parameter_keeps_its_bytes() -> None:
    """The ``.md`` is compared with its rendering when a set is verified: a name without markup signs must come out as before."""
    from tools.test_case_projections import render_markdown

    assert "<first_name>" in render_markdown(_grouped_with_parameter("first_name")).payload.decode("utf-8")


def test_a_json_tail_with_three_backticks_does_not_close_its_fence() -> None:
    from tools.test_case_projections import render_markdown

    document = json.loads((Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json").read_text(encoding="utf-8"))
    document["test_cases"][0]["steps"][0]["test_data"] = '\u0422\u0435\u043b\u043e \u0437\u0430\u043f\u0440\u043e\u0441\u0430.\n\n{"note":"```x```"}'
    lines = render_markdown(document).payload.decode("utf-8").splitlines()
    opened = next(index for index, line in enumerate(lines) if line.endswith("json") and set(line[:-4]) == {"`"})
    fence = lines[opened][:-4]
    assert len(fence) > 3 and lines[opened + 2] == fence, lines[opened:opened + 4]
