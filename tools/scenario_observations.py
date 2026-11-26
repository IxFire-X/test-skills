"""The observations of the scenarios and of the state of the project (v0.8, the cut of the core): what ``finalize_attempt`` publishes
after a run (the tree of the project, the retry of a terminal attempt) and reads back at a later ``finish``.

Either delivery carries this module.  The bodies are the ones of ``pilot_state`` before the cut as they were (``handoff/v08/moves/v1a.tsv``);
what they read of the journal they take by its public names, and ``pilot_state`` still serves their names to its callers (the facade of
the cut).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.confined_output import read_confined_bytes
from tools.pilot_state import (  # the names of the journal these bodies read (П1 of the cut of v0.8)
    DIGEST as _DIGEST,
    append_event,
    canonical_bytes as _canonical_bytes,
    check_sealed_value as _check_sealed,
    derive_state,
    frozen_baseline,
    publish_artifact_value as _publish,
    read_artifact as _read_artifact,
    read_attempt_receipt,
    read_closure_artifact_if_present,
    read_model_request_with_state as _read_model_request_with_state,
    read_model_stage_artifact_with_state as _read_model_stage_artifact_with_state,
    read_run,
    read_terminal_result,
    run_root_of as _run_root,
    sealed as _sealed,
)


def _scenario_observation_target(root: Path, attempt_id: str, scenario_id: str) -> Path:
    allowed = {
        "fresh-non-git", "tree-preservation", "waiting-for-model-resume",
        "one-user-question-resume", "terminal-idempotent-retry",
    }
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id) or scenario_id not in allowed:
        raise ValueError("invalid scenario observation identity")
    return root / "scenario-observations" / f"{attempt_id}.{scenario_id}.json"


def _project_tree_state(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """The user's tree as the inventory sees it — the number of files and the digest of their ``(path, content digest)`` pairs."""
    pairs = sorted((str(row["project_path"]), str(row["content_digest"])) for row in rows)
    return _sealed({"kind": "PROJECT_TREE", "files": len(pairs),
                    "tree_digest": "sha256:" + hashlib.sha256(json.dumps(pairs, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()})


def _owned_project_paths(root: Path, attempt: Mapping[str, Any]) -> set[str]:
    """The project paths the pipeline owns in the attempt: the files of its generated delta (relative to the module, so prefixed here)."""
    try:
        delta = read_attempt_receipt(root, str(attempt["attempt_id"]), "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
    except (KeyError, OSError, TypeError, ValueError):
        return set()
    prefix = "" if attempt.get("module") == "." else str(attempt.get("module")).rstrip("/") + "/"
    return {prefix + str(row["path"]) for row in delta.get("files", ()) if isinstance(row, Mapping)}


def _project_tree_states(project: Path, root: Path, attempt: Mapping[str, Any], inventory: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(before, after)`` — the frozen inventory of the attempt, and the inventory rebuilt now, both without the files the pipeline
    owns.  The same rebuild as ``run_pipeline._baseline_check`` (the same arguments), so what ``status`` calls a drift is what is compared."""
    from tools.project_inventory import baseline_external_input_paths, build_inventory

    owned = _owned_project_paths(root, attempt)
    baseline = frozen_baseline(root, attempt)
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    current = build_inventory(project, module_root, skill_pack_root=Path(__file__).resolve().parents[1], generated_roots=(root.parent,),
                              proved_dependency_files=baseline_external_input_paths(project, baseline))
    keep = lambda row: row.get("project_path") not in owned  # noqa: E731
    return (_project_tree_state(row for row in inventory["files"] if keep(row)),
            _project_tree_state(row for row in current["files"] if keep(row)))


def _terminal_retry_snapshot(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """Seal the terminal bytes and event counts a retry is forbidden to change."""
    terminal = read_terminal_result(run_root, attempt_id)
    closure = [
        read_closure_artifact_if_present(run_root, attempt_id, kind)
        for kind in ("pre_finalization_trace", "finalization_receipt", "terminal_trace")
    ]
    if any(not isinstance(row, Mapping) for row in closure):
        raise ValueError("terminal retry closure is incomplete")
    state = derive_state(run_root)
    attempt_events = [
        row for row in state["events"] if row.get("attempt_id") == attempt_id
    ]
    return _sealed({
        "kind": "TERMINAL",
        "terminal_digest": terminal["digest"],
        "terminal_event_count": sum(
            row.get("event_type") == "ATTEMPT_TERMINAL" for row in attempt_events
        ),
        "execution_start_count": sum(
            row.get("event_type") == "EXECUTION_STARTED" for row in attempt_events
        ),
        "terminal_artifact_digests": sorted([
            terminal["digest"], *(str(row["digest"]) for row in closure if row is not None),
        ]),
    })


def _resume_observation_events(
    state: Mapping[str, Any], attempt_id: str,
) -> tuple[str, list[Mapping[str, Any]]] | None:
    attempt_events = [
        row for row in state["events"] if row.get("attempt_id") == attempt_id
    ]
    terminal_events = [
        row for row in attempt_events if row.get("event_type") == "ATTEMPT_TERMINAL"
    ]
    if len(terminal_events) != 1:
        return None
    terminal_event = terminal_events[0]
    user_waits = [
        row for row in attempt_events
        if row.get("event_type") == "WAITING_FOR_INPUT"
        and row["seq"] < terminal_event["seq"]
    ]
    if user_waits:
        scenario_id, waiting_event = "one-user-question-resume", user_waits[0]
    else:
        model_waits = [
            row for row in attempt_events
            if row.get("event_type") == "WAITING_FOR_MODEL"
            and row["seq"] < terminal_event["seq"]
        ]
        if not model_waits:
            return None
        scenario_id, waiting_event = "waiting-for-model-resume", model_waits[0]
    requests = [
        row for row in attempt_events
        if row.get("event_type") == "MODEL_REQUESTED"
        and waiting_event["seq"] < row["seq"] < terminal_event["seq"]
    ]
    if not requests:
        return None
    request_event = requests[0]
    responses = [
        row for row in attempt_events
        if row.get("event_type") == "MODEL_RESPONSE_RECEIVED"
        and row.get("stage_instance_id") == request_event.get("stage_instance_id")
        and request_event["seq"] < row["seq"] < terminal_event["seq"]
    ]
    if not responses:
        return None
    return scenario_id, [waiting_event, request_event, responses[0], terminal_event]


def _validate_scenario_observation(
    root: Path,
    attempt: Mapping[str, Any],
    scenario_id: str,
    observation: Mapping[str, Any],
) -> tuple[list[Mapping[str, Any]], Mapping[str, Any] | None]:
    expected = {
        "fresh-non-git": ("NON_GIT_PROJECT", "RUN_PIPELINE"),
        "tree-preservation": ("PROJECT_TREE", "RUN_PRESERVING_TREE"),
        "waiting-for-model-resume": ("MODEL_WAITING", "RESUME_MODEL_ATTEMPT"),
        "one-user-question-resume": ("USER_QUESTION_PENDING", "RESUME_AFTER_USER_ANSWER"),
        "terminal-idempotent-retry": ("TERMINAL_ATTEMPT", "RETRY_TERMINAL_ATTEMPT"),
    }[scenario_id]
    terminal = read_terminal_result(root, str(attempt["attempt_id"]))
    before = _check_sealed(observation["before"], "scenario observation before")
    after = _check_sealed(observation["after"], "scenario observation after")
    precondition, action = observation["precondition"], observation["action"]
    if (
        observation.get("run_id") != attempt.get("run_id")
        or observation.get("attempt_id") != attempt.get("attempt_id")
        or observation.get("policy_profile") != attempt.get("policy_profile")
        or observation.get("scenario_id") != scenario_id
        or (precondition.get("kind"), precondition.get("snapshot_digest")) != (expected[0], before["digest"])
        or (action.get("kind"), action.get("before_digest"), action.get("after_digest"))
        != (expected[1], before["digest"], after["digest"])
        or observation.get("result") != {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]}
        or terminal["digest"] not in observation.get("receipt_digests", ())
    ):
        raise ValueError("scenario observation binding is invalid")
    project, root = _run_root(root)
    state = derive_state(root)
    indexed = {
        row["digest"]: (index, row)
        for index, row in enumerate(state["events"])
    }
    try:
        selected = [indexed[digest] for digest in observation["event_digests"]]
    except KeyError as error:
        raise ValueError("scenario observation journal binding is invalid") from error
    if (
        [index for index, _row in selected] != sorted(index for index, _row in selected)
        or any(
            row.get("run_id") != attempt.get("run_id")
            or row.get("attempt_id") not in {None, attempt.get("attempt_id")}
            for _index, row in selected
        )
    ):
        raise ValueError("scenario observation journal binding is invalid")
    selected_events = [row for _index, row in selected]
    artifact_binding = [
        row for row in state["events"]
        if row.get("event_type") in {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}
        and row.get("artifact_digest") == observation.get("digest")
        and row.get("run_id") == attempt.get("run_id")
        and row.get("attempt_id") is None
    ]
    if (
        [row.get("event_type") for row in artifact_binding]
        != ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
        or not selected
        or selected[-1][0] >= artifact_binding[0].get("seq", -1)
    ):
        raise ValueError("scenario observation publication binding is invalid")
    binding_event: Mapping[str, Any] | None = None
    if scenario_id in {"fresh-non-git", "tree-preservation"}:
        from tools.project_inventory import read_execution_baseline, read_inventory_receipt

        manifest = read_run(root)["manifest"]
        initial = manifest.get("project_state")
        baseline = read_execution_baseline(
            project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
        )
        inventory = read_inventory_receipt(
            project, root, root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json")
        )
        selected_valid = (
            [row.get("event_type") for row in selected_events]
            == ["RUN_CREATED", "SNAPSHOT_BOUND", "ATTEMPT_TERMINAL"]
            and selected_events[0].get("artifact_digest") == manifest.get("digest")
            and selected_events[1].get("artifact_digest") == attempt.get("baseline_digest")
            and selected_events[2].get("artifact_digest") == terminal.get("digest")
            and observation.get("receipt_digests") == [inventory["digest"], terminal["digest"]]
        )
        if scenario_id == "fresh-non-git":
            valid = (
                isinstance(initial, Mapping)
                and initial.get("kind") == "GIT_DIRECTORY"
                and before == initial
                and after == _terminal_retry_snapshot(root, str(attempt["attempt_id"]))
            )
        else:
            # no state in the manifest — the "before" is the frozen inventory of the attempt (without the files the pipeline owns)
            owned = _owned_project_paths(root, attempt)
            valid = (
                initial is None
                and attempt.get("policy_profile") == "local-pilot-v1"
                and before == after == _project_tree_state(row for row in inventory["files"] if row.get("project_path") not in owned)
            )
        if not selected_valid or not valid:
            raise ValueError("project state observation is invalid")
    elif scenario_id in {"waiting-for-model-resume", "one-user-question-resume"}:
        derived = _resume_observation_events(state, str(attempt["attempt_id"]))
        if derived is None:
            raise ValueError("resume observation lifecycle is invalid")
        derived_scenario, derived_events = derived
        waiting_event, request_event, response_event, terminal_event = derived_events
        stage_instance_id = str(request_event.get("stage_instance_id", ""))
        request_digest = str(request_event.get("artifact_digest", ""))
        response_digest = str(response_event.get("artifact_digest", ""))
        _read_model_request_with_state(
            project, root, state, str(attempt["attempt_id"]), stage_instance_id, request_digest,
        )
        _read_model_stage_artifact_with_state(
            project, root, state, str(attempt["attempt_id"]), stage_instance_id, response_digest,
        )
        selected_valid = (
            derived_scenario == scenario_id
            and [row["digest"] for row in selected_events]
            == [row["digest"] for row in derived_events]
            and terminal_event.get("artifact_digest") == terminal.get("digest")
            and observation.get("receipt_digests")
            == [request_digest, response_digest, terminal["digest"]]
        )
        if scenario_id == "waiting-for-model-resume":
            expected_before = _sealed({
                "kind": "EVENT", "event_digest": waiting_event["digest"],
            })
            expected_after = _terminal_retry_snapshot(root, str(attempt["attempt_id"]))
        else:
            waiting_receipt = read_attempt_receipt(
                root, str(attempt["attempt_id"]), "structured-result", "WAITING_FOR_MODEL",
            )
            question_digest = str(waiting_event.get("artifact_digest", ""))
            model_waits = [
                row for row in state["events"]
                if row.get("attempt_id") == attempt.get("attempt_id")
                and row.get("event_type") == "WAITING_FOR_MODEL"
                and row.get("artifact_digest") == question_digest
                and row["seq"] < waiting_event["seq"]
            ]
            expected_before = _sealed({
                "kind": "USER_INTERACTION",
                "question_digests": [question_digest],
                "answer_digests": [],
            })
            expected_after = _sealed({
                "kind": "USER_INTERACTION",
                "question_digests": [question_digest],
                "answer_digests": [request_digest],
            })
            selected_valid = (
                selected_valid
                and question_digest == waiting_receipt["digest"]
                and len(model_waits) == 1
            )
        if not selected_valid or before != expected_before or after != expected_after:
            raise ValueError("resume observation is invalid")
    elif scenario_id == "terminal-idempotent-retry":
        terminal_events = [
            row for row in state["events"]
            if row.get("attempt_id") == attempt.get("attempt_id")
            and row.get("event_type") == "ATTEMPT_TERMINAL"
        ]
        current_snapshot = _terminal_retry_snapshot(root, str(attempt["attempt_id"]))
        retry_binding = [
            row for row in state["events"]
            if row.get("event_type") == "TERMINAL_RETRY_OBSERVED"
            and row.get("artifact_digest") == observation.get("digest")
            and row.get("run_id") == attempt.get("run_id")
            and row.get("attempt_id") == attempt.get("attempt_id")
        ]
        post_terminal = (
            len(terminal_events) == 1
            and [row.get("event_type") for row in artifact_binding]
            == ["ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"]
            and len(retry_binding) == 1
            and terminal_events[0]["seq"] < artifact_binding[0]["seq"]
            < artifact_binding[1]["seq"] < retry_binding[0]["seq"]
        )
        later_execution = any(
            row.get("attempt_id") == attempt.get("attempt_id")
            and row.get("event_type") == "EXECUTION_STARTED"
            and row.get("seq", 0) > terminal_events[0]["seq"]
            for row in state["events"]
        ) if terminal_events else True
        if (
            len(terminal_events) != 1
            or later_execution
            or not post_terminal
            or [row.get("digest") for row in selected_events] != [terminal_events[0]["digest"]]
            or before != after
            or before != current_snapshot
            or observation.get("receipt_digests") != current_snapshot["terminal_artifact_digests"]
        ):
            raise ValueError("terminal retry observation is invalid")
        binding_event = retry_binding[0]
    return selected_events, binding_event


def read_scenario_observation(
    run_root: Path,
    attempt_id: str,
    scenario_id: str,
    expected_digest: str,
) -> Mapping[str, Any]:
    """Read one controller-owned scenario receipt from its fixed run location."""
    if not isinstance(expected_digest, str) or _DIGEST.fullmatch(expected_digest) is None:
        raise ValueError("scenario observation digest is invalid")
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        raise ValueError("scenario observation attempt is invalid")
    target = _scenario_observation_target(root, attempt_id, scenario_id)
    observation = _read_artifact(project, root, target, "scenario observation")
    if observation.get("digest") != expected_digest:
        raise ValueError("scenario observation digest mismatch")
    events, binding_event = _validate_scenario_observation(
        root, attempt, scenario_id, observation,
    )
    data = _canonical_bytes(observation)
    if read_confined_bytes(project, root, target) != data:
        raise ValueError("scenario observation read-back mismatch")
    return {
        "path": str(target.relative_to(root)).replace("\\", "/"),
        "bytes": data,
        "digest": observation["digest"],
        "record": observation,
        "events": events,
        "binding_event": binding_event,
        "verified": True,
    }


def _publish_derived_scenario_observation(
    run_root: Path,
    attempt_id: str,
    scenario_id: str,
    value: Mapping[str, Any],
) -> Mapping[str, Any]:
    project, root = _run_root(run_root)
    target = _scenario_observation_target(root, attempt_id, scenario_id)
    record, created, identity = _publish(
        project, root, target, value, "scenario observation", return_created=True,
    )
    append_event(
        root, "ARTIFACT_PUBLISHED", actor="controller",
        artifact_digest=str(record["digest"]),
    )
    append_event(
        root, "ARTIFACT_READ_BACK", actor="controller",
        artifact_digest=str(record["digest"]),
    )
    loaded = dict(read_scenario_observation(
        root, attempt_id, scenario_id, str(record["digest"]),
    ))
    loaded.update({"created": created, "installed_identity": identity})
    return loaded


def publish_project_state_observation(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Any] | None:
    """Derive project-state qualification evidence after a successful terminal action."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        return None
    terminal = read_terminal_result(root, attempt_id)
    if terminal.get("accepted") is not True or terminal.get("verification") != "PASS":
        return None
    manifest = read_run(root)["manifest"]
    before = manifest.get("project_state")
    if before is None:  # no state — the tree of a local-pilot run is compared by the inventory (below); a cases-only run takes none
        if attempt.get("policy_profile") != "local-pilot-v1":
            return None
    elif not isinstance(before, Mapping) or before.get("kind") != "GIT_DIRECTORY":
        return None
    from tools.project_inventory import read_execution_baseline, read_inventory_receipt

    baseline = read_execution_baseline(
        project, root, root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json")
    )
    inventory = read_inventory_receipt(
        project, root, root / "inventories" / (str(baseline["inventory_digest"]).removeprefix("sha256:") + ".json")
    )
    selected = [
        next(row for row in state["events"] if row.get("event_type") == "RUN_CREATED"),
        next(
            row for row in state["events"]
            if row.get("event_type") == "SNAPSHOT_BOUND"
            and row.get("artifact_digest") == attempt["baseline_digest"]
        ),
        next(
            row for row in state["events"]
            if row.get("event_type") == "ATTEMPT_TERMINAL"
            and row.get("attempt_id") == attempt_id
        ),
    ]
    if isinstance(before, Mapping) and before.get("kind") == "GIT_DIRECTORY":
        scenario_id, precondition, action = "fresh-non-git", "NON_GIT_PROJECT", "RUN_PIPELINE"
        after = _terminal_retry_snapshot(root, attempt_id)
    elif before is None:
        scenario_id, precondition, action = "tree-preservation", "PROJECT_TREE", "RUN_PRESERVING_TREE"
        before, after = _project_tree_states(project, root, attempt, inventory)
        if after != before:  # a foreign file changed, went or came during the run: no evidence, not a wrong evidence
            return None
    else:
        return None
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"], "scenario_id": scenario_id,
        "precondition": {"kind": precondition, "snapshot_digest": before["digest"]},
        "action": {
            "kind": action, "before_digest": before["digest"],
            "after_digest": after["digest"],
        },
        "before": dict(before), "after": after,
        "event_digests": [row["digest"] for row in selected],
        "receipt_digests": [inventory["digest"], terminal["digest"]],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    return _publish_derived_scenario_observation(root, attempt_id, scenario_id, value)


def publish_resume_observations(
    run_root: Path,
    attempt_id: str,
) -> Mapping[str, Mapping[str, Any]]:
    """Derive one completed wait/resume qualification observation from durable events."""
    project, root = _run_root(run_root)
    state = derive_state(root)
    attempt = next((row for row in state["attempts"] if row["attempt_id"] == attempt_id), None)
    if attempt is None or attempt.get("state") != "TERMINAL":
        return {}
    terminal = read_terminal_result(root, attempt_id)
    if terminal.get("accepted") is not True or terminal.get("verification") != "PASS":
        return {}
    derived = _resume_observation_events(state, attempt_id)
    if derived is None:
        return {}
    scenario_id, selected = derived
    waiting_event, request_event, response_event, _terminal_event = selected
    stage_instance_id = str(request_event.get("stage_instance_id", ""))
    request_digest = str(request_event.get("artifact_digest", ""))
    response_digest = str(response_event.get("artifact_digest", ""))
    _read_model_request_with_state(project, root, state, attempt_id, stage_instance_id, request_digest)
    _read_model_stage_artifact_with_state(project, root, state, attempt_id, stage_instance_id, response_digest)
    if scenario_id == "waiting-for-model-resume":
        precondition, action = "MODEL_WAITING", "RESUME_MODEL_ATTEMPT"
        before = _sealed({"kind": "EVENT", "event_digest": waiting_event["digest"]})
        after = _terminal_retry_snapshot(root, attempt_id)
    else:
        precondition, action = "USER_QUESTION_PENDING", "RESUME_AFTER_USER_ANSWER"
        waiting_receipt = read_attempt_receipt(
            root, attempt_id, "structured-result", "WAITING_FOR_MODEL",
        )
        question_digest = str(waiting_event.get("artifact_digest", ""))
        if question_digest != waiting_receipt["digest"]:
            return {}
        before = _sealed({
            "kind": "USER_INTERACTION",
            "question_digests": [question_digest],
            "answer_digests": [],
        })
        after = _sealed({
            "kind": "USER_INTERACTION",
            "question_digests": [question_digest],
            "answer_digests": [request_digest],
        })
    value = {
        "schema_version": "1.0.0", "kind": "scenario-observation",
        "run_id": attempt["run_id"], "attempt_id": attempt_id,
        "policy_profile": attempt["policy_profile"], "scenario_id": scenario_id,
        "precondition": {"kind": precondition, "snapshot_digest": before["digest"]},
        "action": {
            "kind": action, "before_digest": before["digest"],
            "after_digest": after["digest"],
        },
        "before": before, "after": after,
        "event_digests": [row["digest"] for row in selected],
        "receipt_digests": [request_digest, response_digest, terminal["digest"]],
        "result": {"kind": "TERMINAL_RESULT", "digest": terminal["digest"]},
    }
    published = _publish_derived_scenario_observation(root, attempt_id, scenario_id, value)
    return {scenario_id: published}


scenario_observation_target = _scenario_observation_target


terminal_retry_snapshot = _terminal_retry_snapshot
