"""F2 (A1-2, arch review 3): the baseline of an attempt is the file the journal froze, not any file with the right name.

``frozen_baseline(run, attempt)`` — the file ``baselines/<attempt.baseline_digest>.json`` whose own digest is that digest and on which
the journal wrote EXECUTION_BASELINE_FROZEN — the one reader of the seven that read it by name only (the import by code, status/finish drift,
the exclusions of the scan, step sizes, the marker reuse hint, the marker parts, the review inputs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_open_recovery import _main
from tests.test_review_requested import _generated_run, _journal


def _attempt(run: dict) -> dict:
    from tools import pilot_state

    return next(row for row in pilot_state.derive_state(run["run_root"])["attempts"] if row["attempt_id"] == run["attempt_id"])


def _forge(run: dict, **changes) -> Path:
    """A baseline valid by itself (schema, canonical bytes, its own digest) put under the name of the real one."""
    from tools.project_inventory import _canonical_bytes, _digest

    attempt = _attempt(run)
    path = run["run_root"] / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json")
    value = json.loads(path.read_bytes())
    value.update(changes)
    value.pop("digest", None)
    value["digest"] = _digest(value)
    path.write_bytes(_canonical_bytes(value))
    return path


def test_the_baseline_of_a_real_attempt_is_read(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.project_inventory import read_execution_baseline

    run = _generated_run(tmp_path)
    attempt = _attempt(run)
    baseline = pilot_state.frozen_baseline(run["run_root"], attempt)
    assert baseline["digest"] == attempt["baseline_digest"]
    assert baseline == read_execution_baseline(run["run_root"] / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))


def test_a_baseline_with_another_digest_under_the_right_name_is_unbound(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    attempt = _attempt(run)
    _forge(run, adapter_id="forged:adapter")
    with pytest.raises(pilot_state.BaselineUnbound) as refused:
        pilot_state.frozen_baseline(run["run_root"], attempt)
    assert refused.value.code == "BASELINE_UNBOUND" and "--new-run" in str(refused.value)


def test_a_missing_baseline_is_unbound_too(tmp_path: Path) -> None:
    from tools import pilot_state

    run = _generated_run(tmp_path)
    attempt = _attempt(run)
    (run["run_root"] / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json")).unlink()
    with pytest.raises(pilot_state.BaselineUnbound):
        pilot_state.frozen_baseline(run["run_root"], attempt)


def test_prepare_refuses_a_forged_baseline_and_writes_nothing(tmp_path: Path, capsys) -> None:
    run = _generated_run(tmp_path)
    _forge(run, adapter_id="forged:adapter")
    before = _journal(run["run_root"])
    from tools import api

    code = api.main(["prepare", "--run-root", str(run["run_root"]), "--stage", "context-marker:baseline"])
    refusal = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code != 0 and refusal.get("code") == "BASELINE_UNBOUND" or "BASELINE_UNBOUND" in json.dumps(refusal, ensure_ascii=False), refusal
    assert _journal(run["run_root"]) == before


def test_the_review_inputs_refuse_a_forged_baseline(tmp_path: Path) -> None:
    from tools import pilot_state
    from tools.orchestrate_test_case_revision import review_inputs

    run = _generated_run(tmp_path)
    _forge(run, adapter_id="forged:adapter")
    with pytest.raises(pilot_state.BaselineUnbound):
        review_inputs(run["run_root"], run["attempt_id"], output_dir=run["project"] / "bundles")


def test_status_of_an_open_run_with_a_forged_baseline_names_the_baseline_not_a_drift(tmp_path: Path, capsys) -> None:
    from tools import run_pipeline

    run = _generated_run(tmp_path)
    _forge(run, adapter_id="forged:adapter")
    result = run_pipeline._status_drift(run["project"], run["run_root"], {}, _attempt(run))
    assert "drift" not in result and result["drift_check_error"]["code"] == "BASELINE_UNBOUND", result


def test_cases_import_refuses_a_forged_baseline_before_it_publishes_anything(tmp_path: Path, monkeypatch, capsys) -> None:
    """Scenario 1 of the review: the skeleton of the import is built from the CSV the baseline's inventory binds — a forged pair
    must not let it be published."""
    from tests.test_cases_import import _project, _scan

    project = _project(tmp_path)
    _code, scan = _scan(project, monkeypatch)
    root, attempt_id = Path(scan["run_root"]), scan["attempt_id"]
    from tools.project_inventory import _canonical_bytes, _digest

    attempt = json.loads((root / "attempts" / f"{attempt_id}.json").read_bytes())
    path = root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json")
    value = json.loads(path.read_bytes())
    value["adapter_id"] = "forged:adapter"
    value.pop("digest")
    value["digest"] = _digest(value)
    path.write_bytes(_canonical_bytes(value))
    before = _journal(root)
    code, refused = _main(capsys, ["prepare", "--run-root", str(root), "--stage", "context-marker:requirements"])
    assert code == 2 and refused["code"] == "BASELINE_UNBOUND" and refused["fix"], refused
    assert _journal(root) == before and not list((root / "imported-cases").glob("*")) if (root / "imported-cases").exists() else True
