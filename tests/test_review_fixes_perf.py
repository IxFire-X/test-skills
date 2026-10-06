"""Regression tests for the 2026-10-05 pipeline review: performance (P01-P09)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tools import pilot_state
from tools.pilot_state import append_event, create_run, derive_state

AUTHORIZATION = {"request_id": "request-1", "execution_requested": False}


def _digest(number: int) -> str:
    return "sha256:" + f"{number:064x}"


def _run(tmp_path: Path) -> Path:
    root = Path(create_run(tmp_path, "cases-only-v1", AUTHORIZATION)["run_root"])
    append_event(root, "MODULE_SELECTED", actor="controller", artifact_digest=pilot_state.module_selection_digest(tmp_path, "."))
    return root


# --- P01 -----------------------------------------------------------------------------------------

def test_p01_appending_an_event_validates_only_the_new_tail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    for number in range(30):
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(number))
    parsed: list[int] = []
    original = pilot_state._parse_json_bytes

    def counting(data: bytes, label: str) -> dict:
        if label == "event journal":
            parsed.append(1)
        return original(data, label)

    monkeypatch.setattr(pilot_state, "_parse_json_bytes", counting)
    append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1000))
    derive_state(root)
    # One append and one read after 32 committed events: only the new event is parsed, not 33 + 33.
    assert len(parsed) <= 3, len(parsed)


def test_p01_cached_prefix_still_detects_tampering(tmp_path: Path) -> None:
    root = _run(tmp_path)
    for number in range(5):
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(number))
    assert len(derive_state(root)["events"]) == 7

    counterpart = root / "events" / "0000000004.json"
    original = counterpart.read_bytes()
    # Same size, different bytes, written immediately (within timestamp granularity).
    counterpart.write_bytes(original.replace(b"ARTIFACT_PUBLISHED", b"ARTIFACT_PUBLISHEX"))
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)
    counterpart.write_bytes(original)
    assert len(derive_state(root)["events"]) == 7

    journal = root / "events.jsonl"
    lines = journal.read_bytes().splitlines(keepends=True)
    journal.write_bytes(b"".join(lines[:3] + lines[4:]))
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)
    journal.write_bytes(b"".join(lines))
    time.sleep(0.12)
    stamp = counterpart.stat()
    counterpart.write_bytes(original.replace(b"ARTIFACT_PUBLISHED", b"ARTIFACT_PUBLISHEX"))
    os.utime(counterpart, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))
    with pytest.raises(ValueError, match="event journal"):
        derive_state(root)


def test_p01_four_hundred_events_stay_fast(tmp_path: Path) -> None:
    root = _run(tmp_path)
    started = time.perf_counter()
    for number in range(400):
        append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(number))
    elapsed = time.perf_counter() - started
    # The reviewed code needed 62-74 s here; allow a wide margin for slow CI disks.
    assert elapsed < 30, elapsed
    assert len(derive_state(root)["events"]) == 402


# --- P02 -----------------------------------------------------------------------------------------

def test_p02_state_is_derived_once_inside_one_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _run(tmp_path)
    calls: list[int] = []
    original = pilot_state._derive_state_uncached

    def counting(project: Path, run_root: Path) -> dict:
        calls.append(1)
        return original(project, run_root)

    monkeypatch.setattr(pilot_state, "_derive_state_uncached", counting)
    with pilot_state.run_lock(root):
        first = derive_state(root)
        for _ in range(20):
            assert derive_state(root) == first
        assert len(calls) == 1
        event = append_event(root, "ARTIFACT_PUBLISHED", actor="controller", artifact_digest=_digest(1))
        assert derive_state(root)["events"][-1] == event  # a write ends the reuse
        assert len(calls) == 2
    derive_state(root)
    derive_state(root)
    assert len(calls) == 4  # separate operations never share state


def test_p02_operation_memo_does_not_leak_caller_mutations(tmp_path: Path) -> None:
    root = _run(tmp_path)
    with pilot_state.run_lock(root):
        state = derive_state(root)
        state["events"].append({"seq": 99})
        state["events"][0]["event_type"] = "CHANGED"
        fresh = derive_state(root)
        assert len(fresh["events"]) == 2 and fresh["events"][0]["event_type"] == "RUN_CREATED"


# --- P03 -----------------------------------------------------------------------------------------

def test_p03_schema_is_compiled_once_per_process_not_per_validation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import schema_validation

    compiled: list[str] = []
    original = schema_validation.Draft202012Validator.check_schema

    def counting(schema: dict) -> None:
        compiled.append(schema.get("$id", ""))
        return original(schema)

    monkeypatch.setattr(schema_validation.Draft202012Validator, "check_schema", staticmethod(counting))
    pack = Path(__file__).resolve().parents[1]
    schema = pack / "schemas" / "event.schema.json"
    reads: list[str] = []
    original_read = Path.read_bytes

    def counting_read(self: Path) -> bytes:
        if self.parent.name == "schemas":
            reads.append(self.name)
        return original_read(self)

    schema_validation.schema_diagnostics({"probe": 0}, schema, pack)  # warm
    monkeypatch.setattr(Path, "read_bytes", counting_read)
    compiled.clear()
    for number in range(200):
        schema_validation.schema_diagnostics({"probe": number}, schema, pack)
    assert compiled == [] and reads == []


def test_p03_changed_schema_file_is_recompiled(tmp_path: Path) -> None:
    from tools import schema_validation

    root = tmp_path / "pack"
    (root / "schemas").mkdir(parents=True)
    schema = root / "schemas" / "probe.schema.json"
    body = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "schemas/probe.schema.json", "type": "object", "required": ["a"]}
    schema.write_text(json.dumps(body), encoding="utf-8")
    assert schema_validation.schema_diagnostics({}, schema, root)
    body["required"] = []
    schema.write_text(json.dumps(body) + "\n", encoding="utf-8")
    assert schema_validation.schema_diagnostics({}, schema, root) == []


# --- P04 -----------------------------------------------------------------------------------------

def test_p04_manifest_with_thousands_of_untracked_files_validates_quickly() -> None:
    files = [{"path": f"src/file_{number}.py", "digest": _digest(number)} for number in range(3000)]
    state = pilot_state._sealed({"kind": "GIT_TREE", "status": [f"?? {row['path']}" for row in files], "files": files})
    manifest = pilot_state._sealed({"schema_version": "1.0.0", "run_id": "a" * 32, "project": "/tmp/project", "policy_profile": "cases-only-v1",
                                    "authorization_digest": _digest(1), "project_state": state})
    started = time.perf_counter()
    pilot_state._validate_schema("run-manifest.schema.json", manifest)
    assert time.perf_counter() - started < 3.0
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "run-manifest.schema.json").read_text(encoding="utf-8"))
    assert "uniqueItems" not in json.dumps(schema["properties"]["project_state"])


# --- P05 -----------------------------------------------------------------------------------------

def test_p05_repeated_inventory_rereads_only_changed_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.project_inventory import build_inventory

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    for number in range(20):
        (project / "src" / f"module_{number}.py").write_text(f"VALUE = {number}\n", encoding="utf-8")
    (project / "docs").mkdir()
    (project / "docs" / "feature.md").write_text("Пользователь видит карточку.\n", encoding="utf-8")
    old = time.time() - 5
    for path in project.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    first = build_inventory(project, project)

    reads: list[str] = []
    original = Path.read_bytes

    def counting(self: Path) -> bytes:
        if project in self.parents:
            reads.append(self.name)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", counting)
    assert build_inventory(project, project) == first
    assert reads == []

    changed = project / "src" / "module_3.py"
    changed.write_text("VALUE = 'changed'\n", encoding="utf-8")
    second = build_inventory(project, project)
    assert reads == ["module_3.py"]
    by_path = {row["project_path"]: row["content_digest"] for row in second["files"]}
    before = {row["project_path"]: row["content_digest"] for row in first["files"]}
    assert {path for path in by_path if by_path[path] != before[path]} == {"src/module_3.py"}


# --- P06 -----------------------------------------------------------------------------------------

def test_p06_review_plan_is_not_rebuilt_or_revalidated_for_every_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.helpers import complete_review_parts
    from tests.test_reviewer_protocol import _prepared
    from tools import review_parts

    root, attempt, _session, _package, _receipt = _prepared(tmp_path, budget=7000)
    rebuilt: list[int] = []
    validated: list[int] = []
    original_build = review_parts.build_review_plan
    original_schema = review_parts.schema_diagnostics

    def counting_build(*args: object, **kwargs: object) -> dict:
        rebuilt.append(1)
        return original_build(*args, **kwargs)

    def counting_schema(value: object, schema: Path, pack: Path) -> list:
        if Path(schema).name == "review-plan.schema.json":
            validated.append(1)
        return original_schema(value, schema, pack)

    monkeypatch.setattr(review_parts, "build_review_plan", counting_build)
    monkeypatch.setattr(review_parts, "schema_diagnostics", counting_schema)
    finished = complete_review_parts(root, attempt)

    parts = len(finished["aggregate"]["parts"]) if "parts" in finished["aggregate"] else 3
    assert finished["session"]["status"] == "COMPLETED" and parts >= 2
    assert rebuilt == [], len(rebuilt)
    # The immutable plan was validated when it was published; later parts only validate ledger additions.
    assert len(validated) <= 4, len(validated)


# --- P08 -----------------------------------------------------------------------------------------

def test_p08_identical_canonical_document_is_validated_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from copy import deepcopy

    from tests.test_requirement_traceability import canonical_fixture
    from tools import canonical_document

    document = canonical_fixture()
    document["metadata"]["project"] = "P08-уникальный-проект"
    calls: list[int] = []
    original = canonical_document._semantic_diagnostics

    def counting(value: dict) -> list:
        calls.append(1)
        return original(value)

    monkeypatch.setattr(canonical_document, "_semantic_diagnostics", counting)
    assert canonical_document.validate_canonical_document(document) == []
    for _ in range(5):
        assert canonical_document.validate_canonical_document(deepcopy(document)) == []
    assert len(calls) == 1

    broken = deepcopy(document)
    broken["test_cases"][0]["requirement_ids"] = ["CREQ-unknown"]
    first = canonical_document.validate_canonical_document(broken)
    assert first and len(calls) == 2
    first.append({"path": "", "code": "MUTATED", "message": ""})
    second = canonical_document.validate_canonical_document(deepcopy(broken))
    assert second and all(row["code"] != "MUTATED" for row in second) and len(calls) == 2


# --- T03 -----------------------------------------------------------------------------------------

def _scope(scope_id: str, kind: str, pointers: list[str]) -> dict:
    return {"scope_id": scope_id, "kind": kind, "targets": [scope_id],
            "inputs": [{"artifact_digest": "sha256:" + "b" * 64, "pointer": pointer, "start": None, "end": None, "content": pointer * 200} for pointer in pointers]}


def test_t03_content_repeated_inside_one_review_part_is_sent_once() -> None:
    from tools.review_parts import build_review_plan, part_input, review_bytes, validate_review_plan

    snapshot = {"review_kind": "tc-reviewer", "revision": 1, "snapshot_digest": "sha256:" + "a" * 64, "instructions": "Review.", "response_reserve_bytes": 100}
    scopes = [_scope("source", "source", ["/s"]), _scope("local-a", "local", ["/a", "/shared"]), _scope("local-b", "local", ["/b", "/shared"]),
              _scope("cross-a-b", "cross", ["/a", "/shared", "/b"])]
    plan = build_review_plan(snapshot, scopes, input_byte_budget=1_000_000)
    assert validate_review_plan(plan) == [] and len(plan["parts"]) == 1
    envelope = part_input(plan, plan["parts"][0])

    contents = [item.get("content") for scope in envelope["scopes"] for item in scope["inputs"]]
    assert sorted(value for value in contents if value is not None) == sorted(pointer * 200 for pointer in ("/s", "/a", "/shared", "/b"))
    # Every input still names its exact evidence; a repeated one points at the first occurrence.
    for scope, original in zip(envelope["scopes"], scopes):
        assert [(item["artifact_digest"], item["pointer"], item["start"], item["end"]) for item in scope["inputs"]] == [(item["artifact_digest"], item["pointer"], item["start"], item["end"]) for item in original["inputs"]]
        for item in scope["inputs"]:
            assert ("content" in item) != ("content_ref" in item)
    by_position = {(scope["scope_id"], index): item for scope in envelope["scopes"] for index, item in enumerate(scope["inputs"])}
    for item in by_position.values():
        if "content_ref" in item:
            target = by_position[(item["content_ref"]["scope_id"], item["content_ref"]["input"])]
            assert "content" in target and (target["pointer"], target["artifact_digest"]) == (item["pointer"], item["artifact_digest"])
    full = sum(len(item["content"].encode()) for scope in scopes for item in scope["inputs"])
    assert plan["parts"][0]["input_byte_count"] == len(review_bytes(envelope)) < full
    # The durable plan keeps complete scopes: validation never depends on the compact envelope.
    assert all("content" in item for scope in plan["parts"][0]["scopes"] for item in scope["inputs"])


def test_t03_deduplicated_envelope_packs_more_scopes_into_one_part() -> None:
    from tools.review_parts import build_review_plan

    snapshot = {"review_kind": "tc-reviewer", "revision": 1, "snapshot_digest": "sha256:" + "a" * 64, "instructions": "Review.", "response_reserve_bytes": 100}
    scopes = [_scope("source", "source", ["/s"]), _scope("local-a", "local", ["/a", "/shared"]), _scope("local-b", "local", ["/b", "/shared"]),
              _scope("cross-a-b", "cross", ["/a", "/shared", "/b"])]
    # 4 unique contents are ~4.6 KiB; with the repeats the same scopes need ~7.4 KiB.
    plan = build_review_plan(snapshot, scopes, input_byte_budget=6500)
    assert len(plan["parts"]) == 1 and plan["parts"][0]["blocked_reason"] is None


# --- T02 -----------------------------------------------------------------------------------------

def test_t02_source_requirement_text_is_not_repeated_in_its_own_provenance(tmp_path: Path) -> None:
    from tools.build_context import build_context

    body = "Пользователь видит карточку товара с названием, ценой и остатком на складе. " * 6
    document = tmp_path / "requirements.md"
    document.write_text(f"## REQ-12 Карточка товара\n{body}\n\n## REQ-13 Список\nПользователь открывает список товаров.\n", encoding="utf-8")
    rows = build_context(tmp_path, docs=[document])["artifacts"]["analytics_documentation"]["requirements"]
    assert len(rows) == 2
    for row in rows:
        assert row["text"].strip()
        assert all(row["text"] not in mark for mark in row["provenance"]), row["provenance"]
        # Provenance still identifies the file, the section and the exact source bytes.
        assert any(mark.startswith("requirements.md — ") and "sha256:" in mark for mark in row["provenance"])
        assert any(mark.startswith("requirements.md — ") and "REQ-1" in mark for mark in row["provenance"])
    assert sum(len(mark) for mark in rows[0]["provenance"]) < len(rows[0]["text"])


# --- T04 -----------------------------------------------------------------------------------------

def test_t04_token_limits_convert_to_conservative_review_byte_budgets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.review_budget import bytes_per_token, estimate_tokens, main, review_budgets

    english = "The owner opens the pet card and sees the visit history. " * 50
    russian = "Владелец открывает карточку питомца и видит историю визитов. " * 50
    assert estimate_tokens(english) >= len(english) / 4  # never fewer tokens than a real tokenizer needs
    assert estimate_tokens(russian) >= len(russian) / 2
    assert 1.0 <= bytes_per_token(russian) < bytes_per_token(english) <= 3.0

    budgets = review_budgets(200_000, 8_000, russian)
    assert budgets["input_byte_budget"] <= 200_000 * bytes_per_token(russian)
    assert budgets["response_reserve_bytes"] >= 8_000
    assert budgets["response_reserve_bytes"] < budgets["input_byte_budget"]
    assert review_budgets(200_000, 8_000, english)["input_byte_budget"] > budgets["input_byte_budget"]
    for bad in ((0, 1), (10, 10), (10, 0), (-5, 1)):
        with pytest.raises(ValueError):
            review_budgets(*bad)

    sample = tmp_path / "cases.json"
    sample.write_text(json.dumps({"text": russian}, ensure_ascii=False), encoding="utf-8")
    assert main(["--context-tokens", "200000", "--response-tokens", "8000", "--sample", str(sample)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "ok" and printed["input_byte_budget"] > printed["response_reserve_bytes"] > 0
    assert main(["--context-tokens", "10", "--response-tokens", "20"]) == 2
