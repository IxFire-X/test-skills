"""The stages of a run and the one rule that names the next step from the facts of ``status`` (V1).

Pure: a dict of facts in, a dict out — no read, no write, no path, no flag.  The answer is a hint: nothing here runs a step."""
from __future__ import annotations

from typing import Any, Mapping

STAGES = ("scan", "marker", "scope_pause", "generator", "review", "automation", "automation_review", "materialize", "human_run",
          "closure", "done", "drift", "left", "unknown")
ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")
# The verb is the command of the step by its one name (flags and paths are gone): ``prepare`` and ``submit`` for every stage that asks a model.
VERBS = ("scan", "prepare", "submit", "materialize", "ingest", "finish", "report", None)
_ROLE_OF_STATE = {"marker": "context-marker", "generator": "tc-generator", "review": "tc-reviewer", "automation": "tc-to-autotest",
                  "automation_review": "autotest-reviewer"}
_STATE_OF_ROLE = {role: state for state, role in _ROLE_OF_STATE.items()}

# the next step the facts of a run name (``tools.run_facts``): (state, verb); None — the attempt is closed
SCAN = ("scan", "scan")
MARKER = ("marker", "prepare")
GENERATOR = ("generator", "prepare")
REVIEW = ("review", "prepare")
REVIEW_ANSWER = ("review", "submit")
AUTOMATION = ("automation", "prepare")
AUTOMATION_REVIEW = ("automation_review", "prepare")
MATERIALIZE = ("materialize", "materialize")
HUMAN_RUN = ("human_run", "ingest")
CLOSURE = ("closure", "finish")
DRIFT = ("drift", "scan")
UNKNOWN = ("unknown", None)


def answer_of(stage_instance_id: str) -> tuple[str, str | None]:
    """The step of a request of the model that waits for its answer: the state of its role and ``submit``; a role the table does not
    know is ``UNKNOWN``."""
    state = _STATE_OF_ROLE.get(str(stage_instance_id).split(":", 1)[0])
    return (state, "submit") if state else UNKNOWN


# one fixed text for each cause of a way out — the state alone does not say which; a changed file of the project comes first (it also
# breaks the evidence the status reads back)
_RECOVERY = {
    "files": "файлы проекта изменились после scan: верните их — тогда прогон продолжится; или начните новый прогон тем же scan с --new-run (drift.command)",
    "run": "файлы прогона не прошли проверку (evidence_errors): они повреждены или изменились; продолжать нельзя — начните новый прогон тем же scan с --new-run",
    "unknown": "стадия не распознана: ничего не выполняй, покажи человеку этот вывод status",
}


def next_action(facts: Mapping[str, Any]) -> dict[str, Any]:
    """``{state, waiting_for, next: {verb, stage}}`` (+ ``recovery`` for a way out) of the facts of a run.

    A left run and a closed run (``next_step`` is None) win over a drift of the project: the project may change after them."""
    step = facts.get("next_step")
    recovery = None
    if facts.get("attempt_id") is None:
        state, verb = SCAN
    elif facts.get("left"):
        state, verb = "left", "scan"
    elif step is None:
        state, verb = "done", "report"
    elif facts.get("stop_reason") == "EVIDENCE_UNVERIFIED" or facts.get("drift") or tuple(step) == DRIFT:
        state, verb, recovery = "drift", "scan", _RECOVERY["files" if facts.get("drift") else "run"]
    elif facts.get("scan_cut"):
        state, verb = SCAN
    elif facts.get("scope_pause") and facts.get("after_marker"):
        state, verb = "scope_pause", "submit" if facts.get("scope_answer") else "prepare"
    elif tuple(step) == UNKNOWN:
        state, verb, recovery = "unknown", None, _RECOVERY["unknown"]
    else:
        state, verb = step
    # drift and unknown wait for the human: to bring the files back or to start anew is his decision, never the agent's
    waiting = ("model" if verb == "submit" and state != "scope_pause" else
               "human" if state in ("human_run", "drift", "unknown") or (state == "scope_pause" and not facts.get("scope_answer")) else "agent")
    return {"state": state, "waiting_for": waiting, "next": {"verb": verb, "stage": "scope" if state == "scope_pause" else _ROLE_OF_STATE.get(state)},
            **({"recovery": recovery} if recovery else {})}
