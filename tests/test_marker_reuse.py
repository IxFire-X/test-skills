"""FaT-CR: the context marker of an earlier accepted run with the very same inputs is reused through the same two commands.

Code finds and checks the source and the agent decides; the reused answer passes every check of a fresh one, and a
reuse is not a model call.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _source(project: Path) -> tuple[Path, str]:
    """A run whose canonical review is accepted: its context marker is a reuse source."""
    from tests.test_automation_revision_budget import automated_document
    from tests.test_generated_delta import _reviewer_protocol_inputs
    from tests.test_self_review import _run

    run_root, attempt_id = _run(project, "cases-only-v1", flag=False)
    _reviewer_protocol_inputs(automated_document(), run_root, attempt_id, bundle_root=run_root / "bundle")
    return run_root, attempt_id


def _fresh(project: Path, *, changed: bool = False) -> tuple[Path, str, list[str]]:
    """A new run of the same project up to its marker request: its inputs, as a scan of the same project gives them.

    ``changed``: a file was added to the project since the source run — its inventory, so its marker inputs, differ."""
    if changed:
        (project / "tests" / "added_since.py").write_text("x = 1\n", encoding="utf-8")
    from tests.test_self_review import _run
    from tools.pilot_state import derive_state, publish_context_selection
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt, select_context_batches

    run_root, attempt_id = _run(project, "cases-only-v1", flag=False)
    attempt = next(row for row in derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    baseline = read_execution_baseline(run_root / "baselines" / (attempt["baseline_digest"].removeprefix("sha256:") + ".json"))
    inventory = read_inventory_receipt(run_root / "inventories" / (baseline["inventory_digest"].removeprefix("sha256:") + ".json"))
    context = publish_context_selection(run_root, attempt_id, select_context_batches(inventory, project, [], byte_budget=64 * 1024)[0]["receipt"])
    return run_root, attempt_id, [baseline["requirements"]["digest"], baseline["inventory_digest"], context["digest"]]


def _marker(run_root: Path, attempt_id: str) -> tuple[dict, str]:
    from tools.pilot_state import derive_state, read_model_stage_artifact

    event = next(row for row in derive_state(run_root)["events"] if row["event_type"] == "MODEL_RESPONSE_RECEIVED"
                 and row.get("stage_instance_id") == "context-marker:baseline")
    stored = read_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", event["artifact_digest"])
    return dict(stored["artifact"]), str(event["artifact_digest"])


def _request(run_root: Path, attempt_id: str, inputs: list[str], reuse_from: str | None):
    from tools.pilot_state import publish_model_request

    return publish_model_request(run_root, attempt_id, "context-marker:baseline", model_id="reuse", invocation_id=f"reuse-{attempt_id}",
                                 input_digests=inputs, reuse_from=reuse_from)


def test_the_marker_of_an_accepted_run_with_the_same_inputs_is_reused_unchanged(tmp_path: Path) -> None:
    from tools.pilot_state import publish_model_stage_artifact, skill_pack_digest

    source_root, source_attempt = _source(tmp_path)
    marker, digest = _marker(source_root, source_attempt)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    request = _request(run_root, attempt_id, inputs, source_root.name)
    assert request["reuse"] == {"run_id": source_root.name, "attempt_id": source_attempt, "response_digest": digest,
                                          "request_digest": request["reuse"]["request_digest"]}
    assert request["skill_pack_digest"] == skill_pack_digest() is not None
    published = publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", marker)
    assert published["content_digest"] == digest


def test_a_changed_answer_to_a_reuse_request_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    from tools.pilot_state import ModelReuseMismatch, derive_state, publish_model_stage_artifact

    source_root, source_attempt = _source(tmp_path)
    marker, digest = _marker(source_root, source_attempt)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    _request(run_root, attempt_id, inputs, source_root.name)
    marker["warnings"] = ["иначе"]
    with pytest.raises(ModelReuseMismatch) as refused:
        publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", marker)
    assert refused.value.code == "MODEL_REUSE_MISMATCH" and digest in str(refused.value)
    assert not [row for row in derive_state(run_root)["events"] if row["event_type"] == "MODEL_RESPONSE_RECEIVED"]


@pytest.mark.parametrize("case", ["unknown run", "the same run", "review not accepted", "other inputs", "other package", "drift"])
def test_a_source_is_refused_unless_every_condition_holds(tmp_path: Path, case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    if case == "review not accepted":  # the marker reached the generator, the review of the cases did not accept them
        monkeypatch.setattr(pilot_state, "_review_verdict", lambda *args: "ТРЕБУЕТ ДОРАБОТКИ")
    source_root, _attempt = _source(tmp_path)
    run_root, attempt_id, inputs = _fresh(tmp_path, changed=case == "other inputs")
    source = {"unknown run": "0" * 32, "the same run": run_root.name}.get(case, source_root.name)
    if case == "other package":  # any change of the package (its skill_pack_digest) cancels the reuse
        monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: "sha256:" + "e" * 64)
    if case == "drift":
        monkeypatch.setattr(pilot_state, "_has_drifted", lambda root, attempt: True)
    with pytest.raises(pilot_state.ModelReuseRefused) as refused:
        _request(run_root, attempt_id, inputs, source)
    assert refused.value.code == "MODEL_REUSE_REFUSED"
    expected = {"unknown run": "нет прогона", "the same run": "тот же прогон", "review not accepted": "ревью кейсов не принято",
                "other inputs": "входы иные", "other package": "пакет иной", "drift": "дрейф"}[case]
    assert expected in str(refused.value), str(refused.value)


def test_reuse_is_for_the_context_marker_only(tmp_path: Path) -> None:
    from tools.pilot_state import publish_model_request

    source_root, _attempt = _source(tmp_path)
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    with pytest.raises(ValueError):
        publish_model_request(run_root, attempt_id, "tc-reviewer:canonical", model_id="reuse", invocation_id="x",
                              input_digests=["sha256:" + "1" * 64, "sha256:" + "2" * 64], reuse_from=source_root.name)


def test_a_fresh_marker_request_records_its_role_and_no_reuse(tmp_path: Path) -> None:
    from tools.pilot_state import skill_pack_digest

    run_root, attempt_id, inputs = _fresh(tmp_path)
    record = _request(run_root, attempt_id, inputs, None)
    assert record["skill_pack_digest"] == skill_pack_digest() and "reuse" not in record and record["schema_version"] == "1.1.0"


def test_a_source_recorded_without_the_package_digest_is_not_offered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run from before FaT-CR (no skill_pack_digest in its marker request) falls out, honestly."""
    from tools import pilot_state

    monkeypatch.setattr(pilot_state, "skill_pack_digest", lambda: None)
    source_root, _attempt = _source(tmp_path)
    monkeypatch.undo()
    run_root, attempt_id, _inputs = _fresh(tmp_path)
    reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert reuse["run_id"] is None and reuse["source_run_id"] == source_root.name and reuse["reason"].startswith("пакет иной")


def test_the_scan_suggestion_names_the_source_and_the_commands(tmp_path: Path) -> None:
    from tools.pilot_state import marker_reuse_suggestion

    source_root, _attempt = _source(tmp_path)
    marker, digest = _marker(source_root, _attempt)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    reuse = marker_reuse_suggestion(run_root, attempt_id)
    assert reuse["run_id"] == source_root.name
    [stage] = reuse["stages"]
    assert stage["stage"] == "context-marker:baseline" and stage["response_digest"] == digest
    assert Path(stage["artifact"]).is_file() and "--reuse-from " + source_root.name in stage["commands"][0]
    assert all("--input-digest " + item in stage["commands"][0] for item in inputs) and stage["artifact"] in stage["commands"][1]


def test_the_scan_suggestion_says_which_input_differs(tmp_path: Path) -> None:
    from tools.pilot_state import marker_reuse_suggestion

    source_root, _attempt = _source(tmp_path)
    run_root, attempt_id, inputs = _fresh(tmp_path, changed=True)
    reuse = marker_reuse_suggestion(run_root, attempt_id)
    assert reuse["run_id"] is None and reuse["source_run_id"] == source_root.name and reuse["reason"] == "входы иные"
    names = [row["input"] for row in reuse["differences"]]
    assert names[0] == "inventory", names
    assert reuse["differences"][0]["given"] == inputs[1] and reuse["differences"][0]["source"] != inputs[1]


def test_the_scan_suggestion_without_an_earlier_run_is_null_with_a_reason(tmp_path: Path) -> None:
    from tools.pilot_state import marker_reuse_suggestion

    run_root, attempt_id, _inputs = _fresh(tmp_path)
    assert marker_reuse_suggestion(run_root, attempt_id) == {"run_id": None, "reason": "нет прошлого прогона с разметкой"}


def test_a_reuse_is_not_a_model_call_in_the_report(tmp_path: Path) -> None:
    from tools.pilot_state import publish_model_stage_artifact
    from tools.run_report import write_report

    source_root, source_attempt = _source(tmp_path)
    marker, _digest = _marker(source_root, source_attempt)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    _request(run_root, attempt_id, inputs, source_root.name)
    publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", marker)
    write_report(tmp_path, run_root.name)
    report = json.loads((run_root / "report.json").read_text(encoding="utf-8"))
    [call] = report["model_calls"]
    assert call["by"] == "reuse" and call["reused_from"] == source_root.name and call["tokens_est"] is None
    assert report["counts"]["model_calls"] == 0 and report["counts"]["reused_calls"] == 1
    assert "повтор " + source_root.name[:8] in (run_root / "report.md").read_text(encoding="utf-8")


def test_a_reused_marker_goes_through_the_same_openspec_reconciliation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit D4: the source checks of a fresh answer (OpenSpec on the merged marker) run on a reused one too, not around it."""
    from tools import pilot_state

    source_root, source_attempt = _source(tmp_path)
    marker, _digest = _marker(source_root, source_attempt)
    run_root, attempt_id, inputs = _fresh(tmp_path)
    _request(run_root, attempt_id, inputs, source_root.name)
    seen: list[str] = []
    original = pilot_state._validate_context_marker_sources
    monkeypatch.setattr(pilot_state, "_validate_context_marker_sources",
                        lambda *args: seen.append(args[4]["stage_instance_id"]) or original(*args))
    pilot_state.publish_model_stage_artifact(run_root, attempt_id, "context-marker:baseline", marker)
    assert seen and set(seen) == {"context-marker:baseline"}  # on the write and on its read-back


def test_the_scan_prints_the_reuse_suggestion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_first_run import _python_project
    from tests.test_scan_guard import _scan

    code, payload = _scan(_python_project(tmp_path, runtime=False), monkeypatch)
    assert code == 0 and payload["reuse"] == {"run_id": None, "reason": "нет прошлого прогона с разметкой"}


def _parts_source(project: Path, monkeypatch: pytest.MonkeyPatch, inputs: list[str], module: str, requirements_by: str, parts: int = 2) -> str:
    """A source marked in parts (requirements, batch-001.., merged baseline), stubbed at the seam _marker_source.

    Its parts all bind the one context receipt of the fresh run, so the callers stub _marker_base to the fresh inputs."""
    from tools import pilot_state

    source_id = "f" * 32
    (project / ".pilot-runs" / source_id).mkdir(parents=True)
    (project / ".pilot-runs" / source_id / "events.jsonl").write_text("", encoding="utf-8")
    pack = pilot_state.skill_pack_digest()
    req, inv, ctx = inputs
    answer = "sha256:" + "a" * 64
    stages = {"context-marker:requirements": ({"input_digests": [req, inv], "model_id": requirements_by, "skill_pack_digest": pack}, answer)}
    for number in range(parts, 0, -1):  # recorded out of order on purpose: the order is the code's
        stages[f"context-marker:batch-{number:03d}"] = ({"input_digests": [req, inv, ctx, answer], "model_id": "m", "skill_pack_digest": pack},
                                                        f"sha256:{number:064x}")
    stages["context-marker:baseline"] = ({"input_digests": [req, inv, ctx], "model_id": "controller:context-marker-merge"}, "sha256:" + "c" * 64)
    fake = {"run_id": source_id, "root": project / ".pilot-runs" / source_id, "state": {}, "attempt": {"attempt_id": "e" * 32, "module": module},
            "stages": stages, "verdict": "ПРИНЯТО", "drift": False}
    monkeypatch.setattr(pilot_state, "_marker_source", lambda project, run_id: fake if run_id == source_id else (_ for _ in ()).throw(ValueError()))
    return source_id


@pytest.mark.parametrize("parts", [2, 11])
def test_the_commands_of_a_marker_in_parts_come_in_the_order_the_journal_accepts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parts: int) -> None:
    from tools import pilot_state

    run_root, attempt_id, inputs = _fresh(tmp_path)
    module = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)["module"]
    source_id = _parts_source(tmp_path, monkeypatch, inputs, module, "m", parts)
    monkeypatch.setattr(pilot_state, "_marker_base", lambda source: inputs)
    reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    assert reuse["run_id"] == source_id
    assert [row["stage"] for row in reuse["stages"]] == ["context-marker:requirements", *(f"context-marker:batch-{n:03d}" for n in range(1, parts + 1))]
    assert reuse["then"].endswith(f"context_marker_parts merge --run-root {run_root}")


def test_an_import_source_starts_with_cases_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import pilot_state

    run_root, attempt_id, inputs = _fresh(tmp_path)
    module = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)["module"]
    _parts_source(tmp_path, monkeypatch, inputs, module, "controller:cases-import")
    monkeypatch.setattr(pilot_state, "_marker_base", lambda source: inputs)
    reuse = pilot_state.marker_reuse_suggestion(run_root, attempt_id)
    first, *rest = reuse["stages"]
    assert first == {"stage": "context-marker:requirements", "by": "cases-import",
                     "commands": [f"python -m tools.api cases-import --run-root {run_root} --attempt {attempt_id}"]}
    assert [row["stage"] for row in rest] == ["context-marker:batch-001", "context-marker:batch-002"]
