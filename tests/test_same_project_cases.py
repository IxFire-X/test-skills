"""pm4: what «the same project by its composition» (pm3) leaves for the plan, the batches and the review — the true reason in the scan and in ``prepare`` (they are
bound to the context receipts, which hold the whole inventory), the inputs of a marker compared before its answer is read, and the own tests that hide only what is a test."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.test_marker_reuse import _fresh, _request, _source as _marker_source
from tests.test_reuse_cases import REVIEW, _new, _source
from tools import pilot_state

BUILD_FILE = "target/classes.txt"


def _build_directory(project: Path) -> None:
    (project / BUILD_FILE).parent.mkdir(parents=True, exist_ok=True)
    (project / BUILD_FILE).write_text("x", encoding="utf-8")


def _own_context(run_root: Path, attempt_id: str, project: Path) -> None:
    """The context a scan of the project as it is now publishes: the same files as the source's (the one the fixture holds)."""
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    attempt = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(project, run_root, run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(project, run_root, run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    held = [row["opaque_id"] for row in inventory["files"] if row["project_path"] == "src/evidence.py"]
    pilot_state.publish_context_selection(run_root, attempt_id, select_context_batches(inventory, project, held, byte_budget=64 * 1024)[0]["receipt"])


def _first_stage(src: dict) -> str:
    return f"tc-generator:{json.loads(Path(src['plan']).read_bytes())['batches'][0]['batch_id']}"


def test_the_scan_says_the_cases_wait_for_the_receipts_when_only_a_build_directory_differs(tmp_path: Path) -> None:
    src = _source(tmp_path)
    _build_directory(src["project"])
    run_root, attempt_id = _new(src["project"])
    _own_context(run_root, attempt_id, src["project"])
    offered = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert offered["run_id"] == src["run_root"].name  # the marker is taken (pm3)
    assert offered["cases"]["reused"] is False and "квитанци" in offered["cases"]["reason"] and "tests" not in offered["cases"]["reason"]


def test_prepare_gives_the_reason_the_scan_gives(tmp_path: Path) -> None:
    src = _source(tmp_path)
    _build_directory(src["project"])
    run_root, attempt_id = _new(src["project"])
    _own_context(run_root, attempt_id, src["project"])
    with pytest.raises(pilot_state.ModelReuseRefused, match="квитанци"):
        pilot_state.check_reuse(run_root, attempt_id, _first_stage(src), src["run_root"].name)


def test_a_changed_file_is_still_the_input_inventory(tmp_path: Path) -> None:
    src = _source(tmp_path)
    (src["project"] / "src" / "added.py").write_text("x = 1\n", encoding="utf-8")
    _build_directory(src["project"])
    run_root, attempt_id = _new(src["project"])
    with pytest.raises(pilot_state.ModelReuseRefused) as refused:
        pilot_state.check_reuse(run_root, attempt_id, REVIEW, src["run_root"].name)
    assert "входы иные: inventory" in str(refused.value) and "квитанци" not in str(refused.value)


def test_a_changed_file_of_the_context_is_a_difference_of_the_marker_not_an_invalid_source(tmp_path: Path) -> None:
    from tests.test_phase2_scan import _skillsrc
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    (tmp_path / ".skillsrc").write_bytes(_skillsrc())
    source_root, _attempt = _marker_source(tmp_path)
    document = json.loads(json.dumps(parse_skillsrc_bytes(_skillsrc())))
    document["modules"][0]["paths"] = {**document["modules"][0].get("paths", {}), "exclude": ["docs/**"]}
    (tmp_path / ".skillsrc").write_bytes(_payload(document))  # the .skillsrc is a file of the context: the marker of the source no longer reads against the files
    run_root, attempt_id, inputs = _fresh(tmp_path)
    with pytest.raises(pilot_state.ModelReuseRefused, match="входы иные"):
        _request(run_root, attempt_id, inputs, source_root.name)


def _planted(monkeypatch: pytest.MonkeyPatch, path: str, *contents: bytes) -> None:
    """A run planted whole, whose receipt names both versions of ``path`` as written by the package."""
    monkeypatch.setattr(pilot_state, "written_files", lambda run_root: {path: {"sha256:" + hashlib.sha256(content).hexdigest() for content in contents}})


def test_a_planted_receipt_does_not_hide_the_change_of_a_file_that_is_no_test(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_bytes(b"x = 1\n")
    source_root, _attempt = _marker_source(tmp_path)
    (tmp_path / "src" / "app.py").write_bytes(b"x = 2\n")
    (source_root / "materialization-ownership").mkdir(exist_ok=True)
    _planted(monkeypatch, "src/app.py", b"x = 1\n", b"x = 2\n")
    run_root, attempt_id, inputs = _fresh(tmp_path)
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] is None
    with pytest.raises(pilot_state.ModelReuseRefused, match="входы иные"):
        _request(run_root, attempt_id, inputs, source_root.name)


def test_a_planted_receipt_does_not_hide_a_test_outside_the_test_root_of_the_skillsrc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app_test.py").write_bytes(b"x = 1\n")  # a test by its kind, but not under the tests root of the .skillsrc (tests/)
    source_root, _attempt = _marker_source(tmp_path)
    (tmp_path / "src" / "app_test.py").write_bytes(b"x = 2\n")
    (source_root / "materialization-ownership").mkdir(exist_ok=True)
    _planted(monkeypatch, "src/app_test.py", b"x = 1\n", b"x = 2\n")
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] is None


def test_the_test_roots_are_those_of_the_skillsrc_the_inventory_holds(tmp_path: Path) -> None:
    """Guard and red: a ``.skillsrc`` changed since the scan names no root (by a run the drift guard of the context stops earlier, so this is the function itself)."""
    document = {"schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
                "modules": [{"id": "fixture", "root": ".", "stack": {"language": "python", "build_tool": "pip"}, "paths": {"source": ["src"], "tests": ["src"]}, "detected_from": ["pyproject.toml"]}]}
    raw = json.dumps(document).encode("utf-8")
    (tmp_path / ".skillsrc").write_bytes(raw)
    row = {"project_path": ".skillsrc", "content_digest": "sha256:" + hashlib.sha256(raw).hexdigest()}
    assert pilot_state._test_roots(tmp_path, {"module": ".", "files": [row]}) == ("src/",)
    assert pilot_state._test_roots(tmp_path, {"module": ".", "files": [{**row, "content_digest": "sha256:" + "0" * 64}]}) == ()


def test_a_nested_skillsrc_does_not_widen_the_test_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the ``.skillsrc`` of the project root names the test roots; one in a directory that sorts before it (a file an agent may write) names none."""
    nested = {"schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
              "modules": [{"id": "fixture", "root": ".", "stack": {"language": "python", "build_tool": "pip"}, "paths": {"source": ["src"], "tests": ["src"]}, "detected_from": ["pyproject.toml"]}]}
    (tmp_path / "-x").mkdir()
    (tmp_path / "-x" / ".skillsrc").write_text(json.dumps(nested), encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app_test.py").write_bytes(b"x = 1\n")
    source_root, _attempt = _marker_source(tmp_path)
    (tmp_path / "src" / "app_test.py").write_bytes(b"x = 2\n")
    (source_root / "materialization-ownership").mkdir(exist_ok=True)
    _planted(monkeypatch, "src/app_test.py", b"x = 1\n", b"x = 2\n")
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    assert pilot_state.marker_reuse_suggestion(run_root, attempt_id)["run_id"] is None
