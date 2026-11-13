"""V0: drives the commands of ``tools.api`` through the helpers the other tests already have and records the shape of every answer.

The helpers of ``test_review_requested`` call ``tools.api`` in a subprocess through one function, ``_cmd``; here that function runs the
command in this process and notes the shape of its ``result`` — the sorted top-level keys of an object, or the name of the JSON type.
Nothing is written to the repository: every run lives in a ``tmp_path``."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
from typing import Any

REFUSAL_KEYS = {"status", "command", "code", "message", "fix"}


def shape(value: Any) -> Any:
    """Sorted keys of an object, else the JSON type name — the form of an answer, never its content."""
    if isinstance(value, dict):
        return sorted(value)
    return {list: "array", str: "string", bool: "boolean", int: "integer", float: "number", type(None): "null"}[type(value)]


def call_in_process(argv: list[str]) -> tuple[int, dict]:
    from tools import api

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = api.main(list(argv))
    return code, json.loads(buffer.getvalue().strip().splitlines()[-1])


class Observed:
    """What the commands answered: ``results[command]`` — the shape of ``result``; ``envelopes`` — every top-level key set seen."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {}
        self.envelopes: list[tuple[str, int, list[str]]] = []
        self.refusals: list[tuple[str, dict]] = []

    def note(self, argv: list[str], code: int, payload: dict) -> None:
        command = argv[0] + (" --dry-run" if payload.get("dry_run") else "")  # a dry run answers with another object
        self.envelopes.append((command, code, sorted(payload)))
        if payload.get("status") == "error":
            self.refusals.append((command, payload))
        elif "result" in payload:
            self.results.setdefault(command, shape(payload["result"]))
        elif isinstance(payload.get("out"), str):  # --out: the result is in the file, the line holds {out, bytes, sha256}
            self.results.setdefault(command, shape(json.loads(Path(payload["out"]).read_bytes())))


@contextlib.contextmanager
def recording(monkeypatch):
    """Patch the one subprocess function of ``test_review_requested`` so that ``tools.api`` runs here and is noted."""
    from tests import test_review_requested as base

    original = base._cmd
    observed = Observed()

    def wrapper(*args: str) -> tuple[int, dict]:
        if args and args[0] == "tools.api":
            code, payload = call_in_process(list(args[1:]))
            observed.note(list(args[1:]), code, payload)
            return code, payload
        return original(*args)

    monkeypatch.setattr(base, "_cmd", wrapper)
    yield observed


def drive(tmp_path: Path, monkeypatch) -> Observed:
    """The commands reachable by the helpers of the existing tests, in four runs."""
    import shutil

    from tests import test_review_requested as rr
    from tests.test_batch_journal import _run
    from tests.test_cut_review import _evidence_verdict
    from tests.test_review_two_commands import _candidate_file, _slot, _verdict

    with recording(monkeypatch) as observed:
        # (1) a run whose marker is answered: both verbs say so
        run, _plan_digest, _header_digest, _fragments = _run(tmp_path / "generator")
        for verb in ("prepare", "submit"):
            assert rr._api(verb, "--run-root", str(run["run_root"]), "--stage", "context-marker:baseline")[0] == 0

        # (1b) the plan of the batches on a run that has none yet: prepare, the subject in the slot, submit
        fresh = rr._generated_run(tmp_path / "plan", generated=False)
        assert rr._api("prepare", "--run-root", str(fresh["run_root"]), "--stage", "plan")[0] == 0
        rr._put(fresh["run_root"] / "incoming" / "plan.json", {"subject": "Предмет"})
        code, planned = rr._api("submit", "--run-root", str(fresh["run_root"]), "--stage", "plan")
        assert code == 0, planned

        # (2) the review on the cycle (prepare, the answer in the slot, submit), then what reads its result; the assembled candidate is checked
        def review(run: dict, verb: str, *extra: str) -> tuple[int, dict]:
            flags = ("--invocation-id", "reviewer-subagent-1", "--fresh-context", "yes") if verb == "prepare" else ()
            return rr._api(verb, "--run-root", str(run["run_root"]), "--stage", "tc-reviewer:canonical", *flags, *extra)

        run = rr._generated_run(tmp_path / "review")
        _candidate_file(run)
        assert rr._api("validate-canonical", "--document", str(run["tmp"] / "candidate.json"))[0] in (0, 1)
        assert review(run, "prepare")[0] == 0
        _slot(run, _verdict(run))
        assert review(run, "submit", "--dry-run")[0] == 0
        assert review(run, "submit")[0] == 0

        # (3) a review with evidence asked for in the slot
        evidence = rr._generated_run(tmp_path / "evidence")
        assert review(evidence, "prepare")[0] == 0
        _slot(evidence, _evidence_verdict(evidence, "src/extra.py"))
        assert review(evidence, "submit")[0] == 0

        # (4) a review opened and abandoned
        run = rr._generated_run(tmp_path / "abort")
        assert review(run, "prepare")[0] == 0
        code, aborted = review(run, "submit", "--abort", "REVIEW_UNUSABLE")
        assert code == 0, aborted
        shutil.rmtree(tmp_path / "abort", ignore_errors=True)

        def direct(argv: list[str]) -> tuple[int, dict]:
            code, payload = call_in_process(argv)
            observed.note(argv, code, payload)
            return code, payload

        # (5) an import of ready cases: the requirements part answered by code
        from tests.test_cases_import import _project as csv_project
        from tests.test_cases_import import _scan

        _code, scanned = _scan(csv_project(tmp_path / "csv"), monkeypatch)
        code, imported = direct(["prepare", "--run-root", scanned["run_root"], "--stage", "context-marker:requirements"])
        assert code == 0, imported

        # (6) two answers that are not a plain success: a refusal (a stage the run has no step for) and an invalid result (no document)
        from tests.test_run_lock import _run as made_run

        (tmp_path / "refusal").mkdir()
        run_root = made_run(tmp_path / "refusal")
        assert direct(["prepare", "--run-root", str(run_root), "--stage", "no-such:stage"])[0] == 2
        assert direct(["validate-canonical", "--document", rr._put(run_root / "tmp" / "empty.json", {})])[0] == 1
    return observed
