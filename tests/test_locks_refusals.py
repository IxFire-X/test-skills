"""The second external review, items 3 and 2 (handoff/v08/locks-refusals-plan.md): a look at the earlier runs keeps none of their locks
after the look; a refusal is written under the lock of its run, the event first; a command that only reads writes no refusal."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from tests.test_run_lock import _HOLDER, _STATUS, _answer, _run, _spawn, _wait_for
from tools.json_cli import record_refusal

# the look of a command at an earlier run, then it lives on (the flag ``release`` ends it)
_LOOK = """
import sys, time
from pathlib import Path
project, flags, look = Path(sys.argv[1]).resolve(), Path(sys.argv[2]), sys.argv[3]
root = next(path for path in (project / ".pilot-runs").iterdir() if path.is_dir())
if look == "scan":
    from tools.run_scan import _open_run_of
    _open_run_of(project, {"module": ".", "docs": ["docs/a.md"]})
elif look == "assemble":
    from tools.assemble_sections import where
    where(project, "docs/a.md")
else:
    from tools.model_inputs import _run_source
    try:
        _run_source(project, root.name, whole=False, wait=1.0)
    except ValueError:
        pass
(flags / "looked").write_text("x")
end = time.time() + 60
while time.time() < end and not (flags / "release").exists():
    time.sleep(0.02)
"""


def _look_leaves_the_run_free(tmp_path: Path, look: str) -> dict:
    root = _run(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("# A\n\n1. The page shows owners.\n", encoding="utf-8")
    flags = tmp_path / "flags"
    flags.mkdir()
    looker = _spawn(_LOOK, tmp_path, flags, look)
    _wait_for(flags / "looked")
    answer = _answer(_spawn(_STATUS, tmp_path, root.name, flags, 0.3))
    (flags / "release").write_text("x")
    looker.wait(timeout=60)
    return answer


def test_scan_looks_at_an_old_run_and_leaves_it_free(tmp_path: Path) -> None:
    assert _look_leaves_the_run_free(tmp_path, "scan")["status"] == "ok"


def test_assemble_looks_at_an_old_run_and_leaves_it_free(tmp_path: Path) -> None:
    assert _look_leaves_the_run_free(tmp_path, "assemble")["status"] == "ok"


def test_a_reuse_suggestion_looks_at_an_old_run_and_leaves_it_free(tmp_path: Path) -> None:
    assert _look_leaves_the_run_free(tmp_path, "reuse")["status"] == "ok"


_SCAN_TIMED = """
import sys, time
from pathlib import Path
from tools import run_lock
from tools.run_scan import _open_run_of
run_lock.WAIT_SECONDS = 3
start = time.monotonic()
_open_run_of(Path(sys.argv[1]).resolve(), {"module": ".", "docs": ["docs/a.md"]})
print(round(time.monotonic() - start, 2))
"""


def test_scan_does_not_wait_for_a_busy_run_of_another_request(tmp_path: Path) -> None:
    root = _run(tmp_path)  # created with no scan request: another request
    flags = tmp_path / "flags"
    flags.mkdir()
    holder = _spawn(_HOLDER, root, flags)
    _wait_for(flags / "held")
    out, _err = _spawn(_SCAN_TIMED, tmp_path).communicate(timeout=60)
    (flags / "release").write_text("x")
    holder.wait(timeout=60)
    assert float(out.strip().splitlines()[-1]) < 1.5, out


def test_status_writes_no_refusal_into_its_run(tmp_path: Path, monkeypatch) -> None:
    from tools import run_pipeline

    root = _run(tmp_path)
    monkeypatch.setattr(run_pipeline, "_ARGS", [SimpleNamespace(command="status", run=root.name, project=str(tmp_path))])
    run_pipeline._journal_refusal({"code": "EXECUTION_COORDINATES"})
    assert not (root / "refusals.jsonl").exists()


def test_a_refusal_of_a_corrupt_journal_writes_nothing(tmp_path: Path) -> None:
    root = _run(tmp_path)
    (root / "events" / "0000000001.json").write_bytes(b"x")
    record_refusal(root, "prepare", "JOURNAL_CORRUPT")
    record_refusal(root, "prepare", "INVALID_ARGUMENT")  # another code on the same journal: no event, so no line either
    assert not (root / "refusals.jsonl").exists()


def test_a_refusal_writes_its_event_and_its_line(tmp_path: Path) -> None:
    from tools import pilot_state

    root = _run(tmp_path)
    record_refusal(root, "prepare", "INVALID_ARGUMENT", "plan")
    line = (root / "refusals.jsonl").read_bytes().rstrip(b"\n")
    events = [event for event in pilot_state.derive_state(root)["events"] if event["event_type"] == "COMMAND_REFUSED"]
    assert json.loads(line)["code"] == "INVALID_ARGUMENT" and len(events) == 1


_RECORD = """
import sys
from pathlib import Path
from tools import step_size
project, run_id = Path(sys.argv[1]), sys.argv[2]
report = {key: 0 for key in ("bytes", "input_bytes", "role_bytes", "files", "tokens_est", "budget_bytes")}
step_size.record(project, run_id, "plan", {**report, "role": "plan", "fits": True, "warning": None})
"""


def test_a_step_size_waits_for_the_command_that_holds_the_run(tmp_path: Path) -> None:
    root = _run(tmp_path)
    flags = tmp_path / "flags"
    flags.mkdir()
    holder = _spawn(_HOLDER, root, flags)
    _wait_for(flags / "held")
    writer = _spawn(_RECORD, tmp_path, root.name)
    time.sleep(1)
    written_while_held = (root / "step-sizes.jsonl").exists()
    (flags / "release").write_text("x")
    assert writer.wait(timeout=60) == 0 and holder.wait(timeout=60) == 0
    assert not written_while_held and (root / "step-sizes.jsonl").exists()
