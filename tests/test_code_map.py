"""v0.8, hybrid C (rnd/20 §8–§10, v08/hybrid-marker-plan.md): a marker step reads the map of its context batch — every file by its path,
the code as its declarations — and opens whole the files it needs; a step asked before has no map on the disk and read the files whole."""
from __future__ import annotations

import hashlib
from pathlib import Path

from tests.test_marker_parts import _publish_requirements, _run
from tools import pilot_state
from tools.code_skeleton import view_bytes
from tools.context_marker_parts import plan
from tools.step_size import CODE_MAP_VIEW, code_map_bytes, code_map_path, stage_inputs

PART = "context-marker:batch-001"


def _asked(tmp_path: Path, stage: str = PART, files: int = 3) -> tuple[Path, dict, list[dict]]:
    root, attempt_id, baseline, receipts = _run(tmp_path, files=files)
    if stage == PART:
        answer = _publish_requirements(root, attempt_id, baseline)
        inputs = [baseline["inventory_digest"], receipts[0]["digest"], answer["content_digest"]]
    else:
        inputs = [baseline["requirements"]["digest"], baseline["inventory_digest"], *(receipt["digest"] for receipt in receipts)]
    pilot_state.publish_model_request(root, attempt_id, stage, model_id="model-context", invocation_id="part-1", input_digests=inputs)
    return root, baseline, receipts


def _code(files: list[dict]) -> list[str]:
    return [row["path"] for row in files if "src" in Path(row["path"]).parts]


def _file(project: Path, relative: str, text: str) -> dict:
    path = project / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))
    return {"project_path": relative, "size": len(text.encode("utf-8")), "content_digest": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()}


def test_a_part_reads_the_map_of_its_batch_not_its_files(tmp_path: Path) -> None:
    root, _baseline, _receipts = _asked(tmp_path)
    files = stage_inputs(root, PART)["files"]
    assert ([row["basis"] for row in files if row.get("basis") == CODE_MAP_VIEW], _code(files)) == ([CODE_MAP_VIEW], [])


def test_the_map_lists_every_file_by_its_path(tmp_path: Path) -> None:
    rows = [_file(tmp_path, "src/Owner.java", "class Owner {\n    void touch() {\n    }\n}\n"), _file(tmp_path, "db/schema.sql", "CREATE TABLE t (id INT);\n"),
            _file(tmp_path, "src/consts.py", "X = 1\n"), _file(tmp_path, "Generated/Strings.swift", "let a = 1\n")]
    assert view_bytes(tmp_path, rows, "H\n", every=True).decode("utf-8") == (
        "H\n## db/schema.sql (25 B)\n\n## src/Owner.java\n1: class Owner {\n2: void touch() {\n\n## src/consts.py (6 B)\n")


def test_one_batch_reads_the_map_too(tmp_path: Path) -> None:  # one context batch: the single step context-marker:baseline
    root, _baseline, _receipts = _asked(tmp_path, "context-marker:baseline", files=1)
    files = stage_inputs(root, "context-marker:baseline")["files"]
    assert ([row["basis"] for row in files if row.get("basis") == CODE_MAP_VIEW], _code(files)) == ([CODE_MAP_VIEW], [])


def test_a_part_asked_before_the_map_read_its_files_whole(tmp_path: Path) -> None:  # the report of an old run: no map on the disk
    root, _baseline, receipts = _asked(tmp_path)
    code_map_path(root, receipts[0]["digest"]).unlink()  # asked by a package before the map
    files = stage_inputs(root, PART, write_views=False)["files"]
    assert _code(files) and not any(row.get("basis") == CODE_MAP_VIEW for row in files)


def test_the_plan_counts_the_map_of_a_part(tmp_path: Path) -> None:
    root, attempt_id, baseline, receipts = _run(tmp_path)
    _publish_requirements(root, attempt_id, baseline)
    part = next(row for row in plan(root)["parts"] if row["stage"] == PART)
    assert part["bytes"]["batch"] == len(code_map_bytes(root.parent.parent, root, attempt_id, receipts[0]["digest"]))


def test_the_plan_of_a_part_asked_before_the_map_counts_its_files(tmp_path: Path) -> None:
    root, _baseline, receipts = _asked(tmp_path)
    code_map_path(root, receipts[0]["digest"]).unlink()
    assert next(row for row in plan(root)["parts"] if row["stage"] == PART)["bytes"]["batch"] == int(receipts[0]["byte_count"])
