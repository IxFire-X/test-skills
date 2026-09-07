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


def test_context_selection_adds_scanner_proved_skillsrc_without_a_model_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    module.mkdir(parents=True)
    (project / ".skillsrc").write_text('{"version":"5"}\n', encoding="utf-8")
    (module / "a.py").write_text("value = 1\n", encoding="utf-8")
    inventory = build_inventory(project, module)
    source_id = next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == "service/a.py")

    batches = select_context_batches(inventory, project, [source_id], byte_budget=1024)

    assert [item["project_path"] for item in batches[0]["files"]] == [".skillsrc", "service/a.py"]
