"""v0.8, hybrid C (rnd/20 §8–§10, v08/hybrid-marker-plan.md): a marker step reads the map of its context batch — every file by its path,
the code as its declarations — and opens whole the files it needs; a step asked before has no map on the disk and read the files whole."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.test_marker_parts import _publish_requirements, _run
from tools import pilot_state, step_size
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
        "H\n## Generated/Strings.swift (10 B, generated)\n\n## db/schema.sql (25 B)\n\n## src/Owner.java\n1: class Owner {\n2: void touch() {\n\n"
        "## src/consts.py (6 B)\n")  # review 0f424788 item 8: a generated file is named too — the model opens it if a requirement needs it


def _obliges(text: str) -> bool:
    return {"open", "whole", "before"} <= set(text.casefold().replace(",", " ").replace(":", " ").split())


def test_the_map_and_the_role_oblige_to_open_the_files_before_writing(tmp_path: Path) -> None:  # A/B run H2 read the map alone
    root, _baseline, _receipts = _asked(tmp_path)
    [row] = [row for row in stage_inputs(root, PART)["files"] if row.get("basis") == CODE_MAP_VIEW]
    role = next(line for line in (Path(__file__).resolve().parents[1] / "skills" / "context-marker" / "SKILL.md").read_text(encoding="utf-8").splitlines()
                if "code-map.md" in line)
    assert _obliges(Path(row["path"]).read_text(encoding="utf-8").split("\n\n")[1]) and _obliges(role)


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
    assert part["bytes"]["batch"] == sum(len(code_map_bytes(root.parent.parent, root, attempt_id, digest)) for digest in part["input_digests"][1:-1])


def test_the_plan_of_a_part_asked_before_the_map_counts_its_files(tmp_path: Path) -> None:
    root, _baseline, receipts = _asked(tmp_path)
    code_map_path(root, receipts[0]["digest"]).unlink()
    assert next(row for row in plan(root)["parts"] if row["stage"] == PART)["bytes"]["batch"] == int(receipts[0]["byte_count"])


GENERATED = "# Code generated by openapi-generator. DO NOT EDIT.\n"


def test_a_batch_of_generated_files_is_a_map_of_their_paths_and_the_plan_counts_it(tmp_path: Path) -> None:  # review 0f424788 items 3, 8
    for index in range(2):
        (tmp_path / "src" / "generated").mkdir(parents=True, exist_ok=True)  # build/ and generated/ are left out of the scan only outside src/
        (tmp_path / "src" / "generated" / f"dto{index}.py").write_text(GENERATED + "X = 1\n" * 12000, encoding="utf-8")
    root, attempt_id, baseline, receipts = _run(tmp_path, files=0)
    answer = _publish_requirements(root, attempt_id, baseline)
    receipt = next(row for row in receipts if all("generated" in Path(item["project_path"]).parts for item in row["files"]))
    stage = f"context-marker:batch-{int(receipt['batch_index']):03d}"
    text = code_map_bytes(root.parent.parent, root, attempt_id, receipt["digest"]).decode("utf-8")
    assert [line for line in text.splitlines() if line.startswith("## ")] == [
        f"## {item['project_path']} ({item['size']} B, generated)" for item in receipt["files"]] and "X = 1" not in text
    assert next(part for part in plan(root)["parts"] if part["stage"] == stage)["bytes"]["batch"] == len(text.encode("utf-8"))
    pilot_state.publish_model_request(root, attempt_id, stage, model_id="model-context", invocation_id="part-1",
                                      input_digests=[baseline["inventory_digest"], receipt["digest"], answer["content_digest"]])
    files = stage_inputs(root, stage)["files"]
    assert [row["bytes"] for row in files if row.get("basis") == CODE_MAP_VIEW] == [len(text.encode("utf-8"))]
    assert _code(files) == []  # the map, not the files whole


def test_an_empty_map_counts_the_files_of_its_receipt_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # review 0f424788 item 3
    """No file of the receipt reads back (the map is b""): ``stage_inputs`` gives the files whole, so the plan counts their bytes, not 0."""
    root, attempt_id, baseline, receipts = _run(tmp_path)
    _publish_requirements(root, attempt_id, baseline)
    monkeypatch.setattr(step_size, "code_map_bytes", lambda *_args: b"")
    assert next(part for part in plan(root)["parts"] if part["stage"] == PART)["bytes"]["batch"] == int(receipts[0]["byte_count"]) > 0
