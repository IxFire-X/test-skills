"""V0: the form of what ``tools.api`` answers — the keys of ``result`` of every command (a snapshot) and the envelope around it.

The snapshot ``api-result-keys.json`` is the contract of the readers (the agent's texts, the 41 test files that read ``result.*``): a key
that leaves or arrives is a change of the contract and shows in the diff of the pull request, not in a failure two packages later.
To change it on purpose: ``UPDATE_CONTRACT_SNAPSHOT=1 python -m pytest tests/contract/test_api_form.py`` writes the file and the run
fails once on purpose — read the diff, commit the file, run again."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.contract.api_driver import Observed, drive

pytestmark = pytest.mark.contract
SNAPSHOT = Path(__file__).with_name("api-result-keys.json")
# A command the fixtures of the other tests cannot drive to a success.  Each line says why; a command that becomes drivable leaves the list.
NOT_DRIVEN = {
    "open-automation-boundary": "mode B: the fixture run (tests.test_finish) has the boundary of revision 1 open already; opening another needs a run in rework",
    **{name: "old command of the review: the tests drive prepare / submit now; V4b-C removes the command and this line"
       for name in ("complete-review", "review-abort", "review-evidence", "review-open", "review-publish", "review-select", "session-start")},
}
# What a successful answer may carry besides {status, command, result}; nothing else is an answer of tools.api.
OPTIONAL_OK_KEYS = {"dry_run", "deprecated"}
OUT_KEYS = {"out", "bytes", "sha256"}
REFUSAL_KEYS = {"status", "command", "code", "message", "fix", "next"}  # error_envelope + the command; diagnostics when the refusal has a list


def test_the_commands_of_the_api_are_driven_answer_in_one_form_and_keep_the_keys_of_the_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One test, one drive: the drive builds five runs (a minute); under ``-n`` three tests would build them three times."""
    observed = drive(tmp_path, monkeypatch)
    _every_command_is_in_the_snapshot_or_named_with_a_reason(observed)
    _every_answer_is_exactly_status_command_and_result(observed)
    _the_keys_of_result_are_the_ones_of_the_snapshot(observed)


def _current(observed: Observed) -> dict:
    return {"commands": dict(sorted(observed.results.items())), "not_driven": dict(sorted(NOT_DRIVEN.items()))}


def _every_command_is_in_the_snapshot_or_named_with_a_reason(observed: Observed) -> None:
    from tools import api

    driven = {name.split(" ")[0] for name in observed.results}
    assert driven | set(NOT_DRIVEN) == set(api.COMMANDS), (
        f"not driven and not in NOT_DRIVEN: {sorted(set(api.COMMANDS) - driven - set(NOT_DRIVEN))}; "
        f"unknown commands: {sorted((driven | set(NOT_DRIVEN)) - set(api.COMMANDS))}")
    assert not driven & set(NOT_DRIVEN), f"driven now, leave NOT_DRIVEN: {sorted(driven & set(NOT_DRIVEN))}"
    assert all(isinstance(reason, str) and len(reason) > 20 for reason in NOT_DRIVEN.values())


def _the_keys_of_result_are_the_ones_of_the_snapshot(observed: Observed) -> None:
    current = _current(observed)
    if os.environ.get("UPDATE_CONTRACT_SNAPSHOT") == "1":
        SNAPSHOT.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        pytest.fail("снимок обновлён, проверь diff и закоммить (tests/contract/api-result-keys.json); с UPDATE_CONTRACT_SNAPSHOT тест не бывает зелёным")
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    problems = []
    for name in sorted(set(expected["commands"]) | set(current["commands"])):
        before, now = expected["commands"].get(name), current["commands"].get(name)
        if before != now:
            gone = sorted(set(before) - set(now)) if isinstance(before, list) and isinstance(now, list) else before
            new = sorted(set(now) - set(before)) if isinstance(before, list) and isinstance(now, list) else now
            problems.append(f"{name}: keys gone {gone}, keys new {new}")
    assert expected["not_driven"] == current["not_driven"]
    assert problems == [], "the keys of result changed: " + "; ".join(problems) + " — if on purpose: UPDATE_CONTRACT_SNAPSHOT=1, read the diff, commit the file"


def _every_answer_is_exactly_status_command_and_result(observed: Observed) -> None:
    assert observed.envelopes, "nothing was driven"
    problems = []
    for command, code, keys in observed.envelopes:
        names = set(keys)
        if code == 2:  # a refusal: the one envelope of error_envelope, plus diagnostics when the refusal carries a list
            if not REFUSAL_KEYS <= names or names - REFUSAL_KEYS - {"diagnostics"}:
                problems.append(f"{command} (refusal): {keys}")
            continue
        if not {"status", "command"} <= names:
            problems.append(f"{command}: {keys}")
            continue
        rest = names - {"status", "command"} - OPTIONAL_OK_KEYS
        if rest != {"result"} and rest != OUT_KEYS and rest != {"out"}:
            problems.append(f"{command}: {keys}")
    assert problems == []
    assert {code for _command, code, _keys in observed.envelopes} == {0, 1, 2}, "the drive must show a success, an invalid result and a refusal"
