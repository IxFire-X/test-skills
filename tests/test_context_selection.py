from pathlib import Path

import pytest

from tools.project_inventory import ContextSelectionError, build_inventory, select_context_batches


def test_context_selection_accepts_only_eligible_ids_and_batches_without_truncation(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    module.mkdir(parents=True)
    (module / "a.py").write_text("a" * 11, encoding="utf-8")
    (module / "b.py").write_text("b" * 11, encoding="utf-8")
    (module / ".env").write_text("SECRET=never-copy\n", encoding="utf-8")
    inventory = build_inventory(project, module)
    ids = {item["project_path"]: item["opaque_id"] for item in inventory["files"]}
    batches = select_context_batches(inventory, project, [ids["service/a.py"], ids["service/b.py"]], byte_budget=11)

    assert [batch["byte_count"] for batch in batches] == [11, 11]
    assert [batch["files"][0]["bytes"] for batch in batches] == [b"a" * 11, b"b" * 11]
    assert all(batch["receipt"]["byte_set_digest"].startswith("sha256:") for batch in batches)
    with pytest.raises(ContextSelectionError, match="eligible opaque file ID"):
        select_context_batches(inventory, project, ["not-a-real-id"], byte_budget=100)
    with pytest.raises(ContextSelectionError, match="eligible opaque file ID"):
        select_context_batches(inventory, project, ["file-secret"], byte_budget=100)


def test_context_selection_detects_digest_drift_before_bytes_are_read(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    module.mkdir(parents=True)
    source = module / "a.py"
    source.write_text("original", encoding="utf-8")
    inventory = build_inventory(project, module)
    file_id = inventory["files"][0]["opaque_id"]
    source.write_text("changed", encoding="utf-8")

    with pytest.raises(ContextSelectionError, match="digest drift"):
        select_context_batches(inventory, project, [file_id], byte_budget=100)


def test_skillsrc_stays_in_the_inventory_but_is_never_model_context(tmp_path: Path) -> None:
    """.skillsrc is the package's own configuration: the inventory and the baseline keep it, no model task reads it,
    and its edit does not change the confirmed model input (contract revision 2026-10-20)."""
    from tools.run_pipeline import is_model_context_noise

    project = tmp_path / "project"
    module = project / "service"
    module.mkdir(parents=True)
    (project / ".skillsrc").write_text('{"version":"5"}\n', encoding="utf-8")
    (module / "a.py").write_text("value = 1\n", encoding="utf-8")
    (module / "pyproject.toml").write_text("[project]\nname = 'a'\n", encoding="utf-8")
    inventory = build_inventory(project, module)
    assert ".skillsrc" in {item["project_path"] for item in inventory["files"]}
    source_id = next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == "service/a.py")

    batches = select_context_batches(inventory, project, [source_id], byte_budget=1024)

    assert [item["project_path"] for item in batches[0]["files"]] == ["service/a.py", "service/pyproject.toml"]  # build manifests stay
    assert is_model_context_noise(".skillsrc") and is_model_context_noise("service/.skillsrc")
    assert select_context_batches(inventory, project, [], byte_budget=1024)[0]["files"][0]["project_path"] == "service/pyproject.toml"


def test_scan_stops_when_nothing_is_left_for_the_model(tmp_path: Path, monkeypatch) -> None:
    """Without .skillsrc in the context a module with no code, manifest or document gives an empty selection: scan says so."""
    import json
    import shutil

    from tests.test_review_fixes_driver import ANSWERS, ROOT
    from tools import pipeline_driver as driver
    from tools import run_pipeline

    project = tmp_path / "project"
    shutil.copytree(ROOT / "tests" / "fixtures" / "projects" / "python-pytest", project)
    (project / "docs").mkdir()
    shutil.copyfile(ANSWERS / "requirements.md", project / "docs" / "feature.md")
    monkeypatch.setattr(run_pipeline, "select_context_batches", lambda *args, **kwargs: [], raising=False)
    import tools.project_inventory as inventory_module

    monkeypatch.setattr(inventory_module, "select_context_batches", lambda *args, **kwargs: [])
    done = driver.start_run(project, {"profile": "cases-only-v1", "docs": ["docs/feature.md"], "subject": "x", "model_id": "m"})
    assert done["action"] == "done" and done["result"]["reason"] == "CONTEXT_EMPTY", done
    assert "--target" in json.dumps(done["result"], ensure_ascii=False) or "paths" in json.dumps(done["result"], ensure_ascii=False)
