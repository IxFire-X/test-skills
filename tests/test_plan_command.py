"""P2 (item 9): ``tools.api plan`` = ``publish-generator-input --kind header`` + ``plan-batches`` + ``publish-generator-input --kind plan``
in one command — the same events and files, nothing published twice, the pause after the marker kept."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_batch_assembly import _header
from tests.test_scope_pause import GAP, _api, _marker_rows, _paused_run


def _files(root: Path, receipts: list[dict], header_digest: str | None) -> dict[str, Path]:
    tmp = root / "tmp"
    tmp.mkdir(exist_ok=True)
    grouping = {"context_receipt_digest": receipts[0]["digest"], **({"header_digest": header_digest} if header_digest else {})}
    for name, value in (("req.json", _marker_rows()), ("grouping.json", grouping), ("context.json", receipts[0]), ("marker.json", {}), ("header.json", _header())):
        (tmp / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return {name: tmp / name for name in ("req.json", "grouping.json", "context.json", "marker.json", "header.json")}


def _plan_flags(root: Path, attempt_id: str, files: dict[str, Path], *extra: str) -> list[str]:
    return ["--requirements", str(files["req.json"]), "--grouping", str(files["grouping.json"]), "--project", str(root.parent.parent),
            "--input-file", str(files["marker.json"]), "--context-receipt", str(files["context.json"]),
            "--run-root", str(root), "--attempt", attempt_id, *extra]


def _plan_argv(root: Path, attempt_id: str, files: dict[str, Path], *extra: str) -> list[str]:
    return ["plan", "--header", str(files["header.json"]), *_plan_flags(root, attempt_id, files, *extra),
            "--out-plan", str(root / "tmp" / "plan.json"), "--out-published", str(root / "tmp" / "published.json")]


def _shape(root: Path) -> list[tuple]:
    rows = [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    return [(row["event_type"], row.get("stage_instance_id"), row.get("batch_id")) for row in rows]


def test_plan_gives_the_events_and_the_plan_of_the_three_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    old_root, old_attempt, _b, old_receipts = _paused_run(tmp_path / "old", warnings=[])
    files = _files(old_root, old_receipts, None)
    code, header = _api(capsys, ["publish-generator-input", "--run-root", str(old_root), "--attempt", old_attempt, "--kind", "header", "--value", str(files["header.json"])])
    assert code == 0, header
    files = _files(old_root, old_receipts, header["result"]["digest"])
    code, planned = _api(capsys, ["plan-batches", *_plan_flags(old_root, old_attempt, files), "--out", str(old_root / "tmp" / "plan.json")])
    assert code == 0, planned
    code, published = _api(capsys, ["publish-generator-input", "--run-root", str(old_root), "--attempt", old_attempt, "--kind", "plan", "--value", str(old_root / "tmp" / "plan.json")])
    assert code == 0, published

    root, attempt_id, _b, receipts = _paused_run(tmp_path / "new", warnings=[])
    files = _files(root, receipts, None)  # no header_digest in the grouping: the code puts the published one
    code, both = _api(capsys, _plan_argv(root, attempt_id, files))
    assert code == 0 and both["status"] == "ok", both
    assert _shape(root) == _shape(old_root)
    plan = json.loads((root / "tmp" / "plan.json").read_bytes())
    old_plan = json.loads((old_root / "tmp" / "plan.json").read_bytes())
    summary = json.loads((root / "tmp" / "published.json").read_bytes())
    assert plan["header_digest"] == summary["header"]["digest"] == header["result"]["digest"] and summary["plan"]["digest"] == plan["digest"]
    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [batch["owned_source_requirement_ids"] for batch in old_plan["batches"]]
    assert plan["source_requirements"] == old_plan["source_requirements"]
    assert set(summary["plan"]) == set(published["result"]) and summary["plan"]["kind"] == "plan"  # the result of the old publish, key for key
    assert set(summary["header"]) == set(header["result"])
    assert ("notes" in both) == ("notes" in planned) and both.get("notes") == planned.get("notes")  # what plan-batches says about the split


def test_plan_again_publishes_nothing_twice(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _b, receipts = _paused_run(tmp_path, warnings=[])
    files = _files(root, receipts, None)
    assert _api(capsys, _plan_argv(root, attempt_id, files))[0] == 0
    before = (root / "events.jsonl").read_bytes()
    code, again = _api(capsys, _plan_argv(root, attempt_id, files))
    assert code == 0 and (root / "events.jsonl").read_bytes() == before, again


def test_a_header_digest_of_the_grouping_that_is_not_the_headers_is_refused_with_the_way_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _b, receipts = _paused_run(tmp_path, warnings=[])
    files = _files(root, receipts, "sha256:" + "c" * 64)
    before = (root / "events.jsonl").read_bytes()
    code, refused = _api(capsys, _plan_argv(root, attempt_id, files))
    assert code == 2 and refused["code"] == "GENERATOR_INPUT_HEADER" and "удали" in refused["fix"], refused
    assert (root / "events.jsonl").read_bytes() == before  # nothing published


def test_the_pause_after_the_marker_stops_plan_before_it_publishes_anything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, attempt_id, _b, receipts = _paused_run(tmp_path, warnings=[GAP])
    files = _files(root, receipts, None)
    before = (root / "events.jsonl").read_bytes()
    code, refused = _api(capsys, _plan_argv(root, attempt_id, files))
    assert code == 2 and refused["code"] == "SCOPE_PENDING", refused
    assert (root / "events.jsonl").read_bytes() == before
    code, answered = _api(capsys, _plan_argv(root, attempt_id, files, "--continue"))
    assert code == 0, answered
