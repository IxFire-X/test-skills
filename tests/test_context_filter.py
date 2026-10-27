"""FaT-A: what is useless for tests does not go to the model — always visibly; inventory and baseline do not change."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_first_run import _python_project, _write
from tests.test_phase2_scan import _args
from tools import run_pipeline

MODULE = "services/api"
DOCS = "services/api/docs/feature.md"
BIG = 300 * 1024


def _project(tmp_path: Path, *, exclude: list[str] | None = None, include: list[str] | None = None) -> Path:
    project = _python_project(tmp_path, runtime=True)
    if exclude is not None or include is not None:
        from tools.init_skillsrc import _payload
        from tools.skillsrc_manifest import load_skillsrc

        document = load_skillsrc(project / ".skillsrc")
        if exclude is not None:
            document["modules"][0]["paths"]["exclude"] = exclude
        if include is not None:
            document["modules"][0]["paths"]["include"] = include
        (project / ".skillsrc").write_bytes(_payload(document))
    return project


def _scan(project: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict]:
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.profile = "cases-only-v1"
    args.docs = [DOCS]
    return run_pipeline.cmd_scan(args), payloads[-1]


def _receipts(scan: dict) -> list[dict]:
    root = Path(scan["run_root"])
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted((root / "context-selections").rglob("*.json"))]


def _selected_paths(receipts: list[dict]) -> set[str]:
    return {row["project_path"] for receipt in receipts for row in receipt["files"]}


def _skipped(receipts: list[dict]) -> dict[str, tuple[str, str]]:
    rows = [row for receipt in receipts for row in receipt.get("skipped", [])]
    return {row["project_path"]: (row["reason"], row["rule"]) for row in rows}


def test_assets_and_large_non_sources_are_skipped_visibly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, f"{MODULE}/src/main/resources/static/css/big.css", "a{color:red}\n" * (BIG // 13 + 1))
    _write(project, f"{MODULE}/src/main/resources/static/fonts/x.svg", '<svg><defs><font id="x"><glyph unicode="a"/></font></defs></svg>\n')
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    receipts = _receipts(scan)
    skipped = _skipped(receipts)
    assert skipped[f"{MODULE}/src/main/resources/static/css/big.css"][0] == "CONTEXT_TOO_LARGE"
    assert skipped[f"{MODULE}/src/main/resources/static/fonts/x.svg"] == ("CONTEXT_ASSET", "font")
    assert not set(skipped) & _selected_paths(receipts)
    assert "skipped" in receipts[0] and all("skipped" not in receipt for receipt in receipts[1:])
    assert scan["context_skipped"]["files"] == 2 and scan["context_skipped"]["by_reason"]["CONTEXT_ASSET"]["files"] == 1
    inventory = json.loads(next((Path(scan["run_root"]) / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    tracked = {row["project_path"] for row in inventory["files"]}
    assert set(skipped) <= tracked


def test_minified_code_is_skipped_but_one_line_data_stays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    line = "x" * 30 * 1024
    _write(project, f"{MODULE}/static/app.min.js", "var a=1;\n")
    _write(project, f"{MODULE}/static/bundle.js", f"var s='{line}';\n")
    _write(project, f"{MODULE}/static/small.css", "a{color:red}\n")
    _write(project, f"{MODULE}/tests/data/payload.json", json.dumps({"value": line}))
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    receipts = _receipts(scan)
    skipped = _skipped(receipts)
    # *.min.js is already left out of the inventory as generated; it never reaches the context either way.
    assert f"{MODULE}/static/app.min.js" not in _selected_paths(receipts)
    assert skipped[f"{MODULE}/static/bundle.js"] == ("CONTEXT_ASSET", "minified")
    assert {f"{MODULE}/static/small.css", f"{MODULE}/tests/data/payload.json"} <= _selected_paths(receipts)


def test_a_source_larger_than_the_batch_stops_with_a_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path)
    _write(project, f"{MODULE}/src/Big.java", "class Big {\n" + "  int a = 1;\n" * (BIG // 13 + 1) + "}\n")
    code, scan = _scan(project, monkeypatch)
    assert code == 2 and scan["reason"] == "CONTEXT_FILE_TOO_LARGE", scan
    assert f"{MODULE}/src/Big.java" in scan["detail"] and "paths.exclude" in scan["fix"]


def test_user_exclude_skips_and_beats_include(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, exclude=[f"{MODULE}/static/**"], include=[f"{MODULE}/static/**"])
    _write(project, f"{MODULE}/static/page.css", "a{color:red}\n")
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    receipts = _receipts(scan)
    assert _skipped(receipts)[f"{MODULE}/static/page.css"] == ("CONTEXT_EXCLUDED_BY_USER", f"{MODULE}/static/**")
    assert f"{MODULE}/static/page.css" not in _selected_paths(receipts)


def test_the_task_document_cannot_be_excluded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, exclude=[f"{MODULE}/docs/**"])
    code, scan = _scan(project, monkeypatch)
    assert code == 2 and scan["reason"] == "CONTEXT_EXCLUDE_TASK_DOCUMENT", scan
    assert "paths.exclude" in scan["fix"]


def test_everything_skipped_is_a_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _project(tmp_path, exclude=[f"{MODULE}/src/**", f"{MODULE}/pyproject.toml"])
    code, scan = _scan(project, monkeypatch)
    assert code == 2 and scan["reason"] == "CONTEXT_ALL_SKIPPED", scan
    assert "paths.exclude" in scan["fix"] and "--target" in scan["fix"]


def test_old_projects_keep_their_receipts_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, scan = _scan(_project(tmp_path), monkeypatch)
    assert code == 0, scan
    assert all("skipped" not in receipt for receipt in _receipts(scan))
    assert scan.get("context_skipped", {"files": 0})["files"] == 0


def test_ready_shows_the_same_skip_as_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.ready import check

    project = _project(tmp_path)
    _write(project, f"{MODULE}/static/app.min.js", "var a=1;\n")
    _write(project, f"{MODULE}/static/fonts/x.woff2", "font\n")
    report = check(project, [DOCS], profile="cases-only-v1")
    row = next(item for item in report["checks"] if item["id"] == "context:skipped")
    code, scan = _scan(project, monkeypatch)
    assert code == 0, scan
    assert row["details"]["files"] == scan["context_skipped"]["files"] and row["details"]["bytes"] == scan["context_skipped"]["bytes"]


def test_ready_stops_on_a_source_larger_than_the_batch(tmp_path: Path) -> None:
    from tools.ready import check

    project = _project(tmp_path)
    _write(project, f"{MODULE}/src/Big.java", "class Big {\n" + "  int a = 1;\n" * (BIG // 13 + 1) + "}\n")
    report = check(project, [DOCS], profile="cases-only-v1")
    stops = [item for item in report["checks"] if item["level"] == "STOP"]
    assert stops and stops[0]["id"] == "context:too-large" and "paths.exclude" in stops[0]["fix"]


def test_the_filter_reads_a_context_file_with_the_same_checks_as_the_batches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file replaced by a link after the inventory is refused before a byte outside the project is read."""
    import os

    from tools.project_inventory import ContextSelectionError, build_inventory, context_filter

    project = _project(tmp_path)
    target = _write(project, f"{MODULE}/static/page.css", "a{color:red}\n")
    inventory = build_inventory(project, project / MODULE)
    outside = _write(tmp_path, "outside/secret.css", "outside-bytes\n")
    target.unlink()
    try:
        os.symlink(outside, target)
    except OSError:
        pytest.skip("symlinks are not available here")
    reads: list[Path] = []
    original = Path.read_bytes

    def tracked(self: Path) -> bytes:
        reads.append(self.resolve())
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", tracked)
    ids = [row["opaque_id"] for row in inventory["files"]]
    with pytest.raises(ContextSelectionError):
        context_filter(inventory, project, ids, byte_budget=262144)
    assert outside.resolve() not in reads
