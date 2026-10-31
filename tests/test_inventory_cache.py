"""FaT-AU4 (audit F4): within one command the inventory of a module is built once — scan used to build it again for every
unfinished run of the same request.  No cache between commands: a changed file is still a drift."""

from __future__ import annotations

import collections
from pathlib import Path

import pytest


def _write(project: Path, relative: str, text: str) -> None:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _two_unfinished_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan
    from tools import pilot_state

    project = _python_project(tmp_path, runtime=False)
    code, first = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, first
    _write(project, "services/api/src/app.py", "X = 2\n")  # the first run drifts: a second scan of the request may start
    code, second = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0 and second["run_id"] != first["run_id"], second
    _write(project, "services/api/src/app.py", "X = 3\n")  # now both drift
    request = pilot_state.read_run(Path(second["run_root"]))["manifest"]["scan_request"]
    return project, request


def _counted(monkeypatch: pytest.MonkeyPatch) -> collections.Counter:
    from tools import project_inventory

    counts: collections.Counter = collections.Counter()
    real = project_inventory._build_inventory
    monkeypatch.setattr(project_inventory, "_build_inventory", lambda *args, **kwargs: counts.update(["built"]) or real(*args, **kwargs))
    return counts


def test_two_unfinished_runs_build_the_inventory_once_and_both_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.project_inventory import inventory_cache
    from tools.run_pipeline import _open_run_of

    project, request = _two_unfinished_runs(tmp_path, monkeypatch)
    counts = _counted(monkeypatch)
    with inventory_cache():
        live, abandoned = _open_run_of(project, request)
    assert counts["built"] == 1, counts
    assert live is None and len(abandoned) == 2 and all("дрейф" in why for why in abandoned), abandoned


def test_without_the_block_each_run_builds_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_pipeline import _open_run_of

    project, request = _two_unfinished_runs(tmp_path, monkeypatch)
    counts = _counted(monkeypatch)
    _live, abandoned = _open_run_of(project, request)
    assert counts["built"] == 2 and len(abandoned) == 2, (counts, abandoned)


def test_a_file_changed_inside_the_block_builds_afresh(tmp_path: Path) -> None:
    """The key is the (path, mtime, size) of the project's files: an edit between two calls is seen at once."""
    from tests.test_first_run import _python_project
    from tools.project_inventory import build_inventory, inventory_cache

    project = _python_project(tmp_path, runtime=False)
    module = project / "services" / "api"
    with inventory_cache():
        before = build_inventory(project, module)
        again = build_inventory(project, module)
        before["files"].append({"changed": "a caller edits its copy"})
        _write(project, "services/api/src/app.py", "X = 22\n")
        after = build_inventory(project, module)
    assert {row["project_path"]: row for row in after["files"]}["services/api/src/app.py"] != \
        {row["project_path"]: row for row in again["files"]}["services/api/src/app.py"]
    assert {"changed": "a caller edits its copy"} not in again["files"]  # each caller gets its own copy


def test_the_scan_builds_the_inventory_of_its_new_run_outside_the_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cache covers only the comparison with unfinished runs: the scan then writes project files (.skillsrc, the cases
    document of --cases), and a same-size rewrite within one timestamp tick keeps the stamp — the new run's inventory and
    its freeze are built afresh, so they bind the same bytes (FaT-AU4 fix; scan --cases was INVENTORY_INVALID)."""
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan
    from tools import project_inventory

    project = _python_project(tmp_path, runtime=False)
    cached: list[bool] = []
    real = project_inventory.build_inventory
    monkeypatch.setattr(project_inventory, "build_inventory",
                        lambda *args, **kwargs: cached.append(project_inventory._INVENTORY_CACHE.get() is not None) or real(*args, **kwargs))
    monkeypatch.setattr("tools.run_pipeline.build_inventory", project_inventory.build_inventory, raising=False)
    code, payload = _scan(project, monkeypatch, accept_self_review=True)
    assert code == 0, payload
    assert cached and not any(cached), cached
