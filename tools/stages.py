"""The stages of a run and the one rule that names the next step from the facts of ``status`` (V1).

Pure: a dict of facts in, a dict out — no read, no write, no path, no flag.  The answer is a hint: nothing here runs a step."""
from __future__ import annotations

from typing import Any, Mapping

STAGES = ("scan", "marker", "scope_pause", "generator", "review", "automation", "automation_review", "materialize", "human_run",
          "closure", "done", "drift", "left", "unknown")
ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")
# The verb is the command of the step by its one name (flags and paths are gone): ``prepare`` and ``submit`` for the stages that went over to
# them (the marker, V2), the old command of the others until their packages; V5 drops the old ones.
VERBS = ("scan", "prepare", "submit", "model-request", "model-response",
         "open-automation-boundary", "materialize", "ingest", "finish", "report", None)
_ROLE_OF_STATE = {"marker": "context-marker", "generator": "tc-generator", "review": "tc-reviewer", "automation": "tc-to-autotest",
                  "automation_review": "autotest-reviewer"}
_STATE_OF_ROLE = {role: state for state, role in _ROLE_OF_STATE.items()}

# the value of ``next_expected_artifact`` that ``_durable_status_facts`` sets -> (state, verb)
_TABLE = {
    "module selection, inventory and profile baseline": ("scan", "scan"), "authorized context": ("scan", "scan"),
    "context-marker response": ("marker", "prepare"), "context-marker parts merge": ("marker", "prepare"),
    "generator fragments": ("generator", "prepare"), "remaining fragments or assembled candidate": ("generator", "prepare"),
    "canonical reviewer package and boundary": ("review", "prepare"), "reviewer evidence or verdict": ("review", "submit"),
    "reviewer ledger and effective selection or negative closure": ("review", "submit"), "effective canonical": ("review", "submit"),
    "negative branch trace and finalization": ("closure", "finish"),
    "rework fragment": ("generator", "prepare"), "rework answered": ("closure", "finish"),
    "automation artifact": ("automation", "model-request"), "automation revision": ("automation", "model-request"),
    "static automation review": ("automation_review", "open-automation-boundary"), "automation refused": ("closure", "finish"),
    "review disposition and materialization": ("materialize", "materialize"), "materialization": ("materialize", "materialize"),
    "materialization command": ("materialize", "materialize"), "human run report": ("human_run", "ingest"),
    "execution trace and dispositions": ("closure", "finish"), "disposition receipt": ("closure", "finish"),
    "disposition and finalization": ("closure", "finish"), "pre-finalization trace": ("closure", "finish"),
    "finalization receipt": ("closure", "finish"), "terminal result and trace": ("closure", "finish"), "terminal event": ("closure", "finish"),
    "validated inputs or new scan": ("drift", "scan"),
}
# one fixed text for each cause of a way out — the state alone does not say which; a changed file of the project comes first (it also
# breaks the evidence the status reads back)
_RECOVERY = {
    "files": "файлы проекта изменились после scan: верните их — тогда прогон продолжится; или начните новый прогон тем же scan с --new-run (drift.command)",
    "run": "файлы прогона не прошли проверку (evidence_errors): они повреждены или изменились; продолжать нельзя — начните новый прогон тем же scan с --new-run",
    "unknown": "стадия не распознана: ничего не выполняй, покажи человеку этот вывод status",
}


def next_action(facts: Mapping[str, Any]) -> dict[str, Any]:
    """``{state, waiting_for, next: {verb, stage}}`` (+ ``recovery`` for a way out) of the facts of a run.

    A left run and a closed run (``next_expected_artifact`` is None) win over a drift of the project: the project may change after them."""
    expected = facts.get("next_expected_artifact")
    recovery = None
    if facts.get("attempt_id") is None:
        state, verb = "scan", "scan"
    elif facts.get("left"):
        state, verb = "left", "scan"
    elif expected is None:
        state, verb = "done", "report"
    elif facts.get("stop_reason") == "EVIDENCE_UNVERIFIED" or facts.get("drift") or _TABLE.get(str(expected), ("",))[0] == "drift":
        state, verb, recovery = "drift", "scan", _RECOVERY["files" if facts.get("drift") else "run"]
    elif facts.get("scan_cut"):
        state, verb = "scan", "scan"
    elif facts.get("scope_pause") and expected == "generator fragments":
        state, verb = "scope_pause", "submit" if facts.get("scope_answer") else "prepare"
    elif str(expected) in _TABLE:
        state, verb = _TABLE[str(expected)]
    elif str(expected).endswith(" response") and str(expected).split(":", 1)[0] in _STATE_OF_ROLE:  # a pending request of the model
        state = _STATE_OF_ROLE[str(expected).split(":", 1)[0]]
        verb = "submit" if state == "marker" else "model-response"
    else:
        state, verb, recovery = "unknown", None, _RECOVERY["unknown"]
    # drift and unknown wait for the human: to bring the files back or to start anew is his decision, never the agent's
    waiting = ("model" if verb in ("model-response", "submit") and state != "scope_pause" else
               "human" if state in ("human_run", "drift", "unknown") or (state == "scope_pause" and not facts.get("scope_answer")) else "agent")
    return {"state": state, "waiting_for": waiting, "next": {"verb": verb, "stage": "scope" if state == "scope_pause" else _ROLE_OF_STATE.get(state)},
            **({"recovery": recovery} if recovery else {})}
