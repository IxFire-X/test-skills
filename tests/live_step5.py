"""Replay of the real step5-java-demo driver runs of 2026-10-06.

``tests/fixtures/live-step5-20261006`` keeps the project and, per run, the real
answers of the context-marker, the generator and every reviewer part, together
with the review state those answers were bound to (``*.json.gz``).

A copied journal cannot be continued elsewhere: it binds the absolute project
path.  ``Replay`` therefore starts the driver again on a copy of the project with
the settings of the recorded run and answers every task with the recorded
answers.  A reviewer answer is assembled per ``scope_id``: coverage rows come from
the recorded answers, findings, corrections and required checks from the
recorded part that began with the same scope.  When the controller packs scopes
into other parts, the answers stay real; only their grouping changes.
"""
from __future__ import annotations

import contextlib
import gzip
import io
import json
import shutil
from pathlib import Path
from typing import Any, Callable, Mapping

from tools import pipeline_driver as driver

LIVE = Path(__file__).resolve().parent / "fixtures" / "live-step5-20261006"
RUNS = {
    "2c10d733": "2c10d733ab8e4b81b49108f41bb0531c",  # cases-only, ACCEPTED
    "3e852e76": "3e852e7609ef4d678b4289b071df348b",  # one mechanical correction -> 66 check parts
    "9340016c": "9340016cb730422982469a3b41959de8",  # local-pilot PASS without corrections
    "d1834358": "d1834358cd6045fca2a3dfd57785efdc",  # UNCHECKED -> blocked -> DRIVER_FAILURE
}
DOCS = "docs/requirements/student-controller.md"


def load(path: Path) -> Any:
    """Read one fixture JSON file, gzip-compressed or plain."""
    path = Path(path)
    if not path.name.endswith(".gz") and not path.exists():
        path = path.with_name(path.name + ".gz")
    data = gzip.decompress(path.read_bytes()) if path.name.endswith(".gz") else path.read_bytes()
    if path.name.removesuffix(".gz").endswith(".jsonl"):
        return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    return json.loads(data.decode("utf-8"))


def run_dir(run: str) -> Path:
    return LIVE / "runs" / RUNS.get(run, run)


def outputs(run: str) -> dict[str, Any]:
    """Recorded answers keyed by task label (``context-marker``, ``review.canonical.part-000001``...)."""
    result = {}
    for path in sorted((run_dir(run) / "driver" / "outputs").glob("*.json.gz")):
        result[path.name.split(".", 1)[1].removesuffix(".json.gz")] = load(path)
    return result


def review_state(run: str, name: str) -> Any:
    """One review-state receipt of the run's single attempt (``review-plan-canonical`` ...)."""
    [attempt] = [path for path in (run_dir(run) / "review-state").iterdir() if path.is_dir()]
    return load(attempt / f"{name}.json.gz")


def ledger(run: str, review_key: str = "canonical") -> dict[str, Any]:
    [path] = (run_dir(run) / "reviewer-session-ledgers").glob(f"*/{review_key}.json.gz")
    return load(path)


def copy_project(target: Path) -> Path:
    shutil.copytree(LIVE / "project", target)
    return target


class Replay:
    """Drive a fresh run of the driver with the recorded answers of one live run."""

    def __init__(self, run: str, tmp_path: Path, *, profile: str | None = None,
                 review: Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
                 override: Callable[[Mapping[str, Any]], Any] | None = None) -> None:
        self.run = RUNS.get(run, run)
        self.config = load(run_dir(run) / "driver" / "config.json")
        self.profile = profile or self.config["profile"]
        self.recorded = outputs(run)
        self.project = copy_project(tmp_path / "project")
        self.review = review
        self.override = override  # answers a task itself when it returns anything but None
        self.review_mode: str | None = "pairs"  # the mode of the recorded answers; None = driver default
        self.used: set[str] = set()
        self.log: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- answers

    def recorded_parts(self, review_key: str) -> list[tuple[str, dict[str, Any]]]:
        return [(label, value) for label, value in self.recorded.items() if label.startswith(f"review.{review_key}.")]

    def review_answer(self, task: Mapping[str, Any]) -> dict[str, Any]:
        envelope = json.loads(Path(task["inputs"][0]).read_text(encoding="utf-8"))
        ids = [scope["scope_id"] for scope in envelope["scopes"]]
        parts = self.recorded_parts(task["review_key"])
        rows: dict[str, Any] = {}
        for _label, part in parts:
            for row in part["coverage"]:
                rows.setdefault(row["scope_id"], row)
        missing = [scope_id for scope_id in ids if scope_id not in rows]
        if missing:
            raise KeyError(f"no recorded coverage for {missing}")
        answer = {"coverage": [rows[scope_id] for scope_id in ids], "findings": [], "corrections": [], "required_checks": []}
        for label, part in parts:
            if label not in self.used and part["coverage"][0]["scope_id"] in ids:
                self.used.add(label)
                for key in ("findings", "required_checks"):
                    answer[key].extend(part[key])
        # A correction goes to the part whose evidence holds its path (the controller requires it).
        pointers = [item["pointer"] for scope in envelope["scopes"] for item in scope["inputs"] if item["pointer"] and item["start"] is None]
        for _label, part in parts:
            for correction in part["corrections"]:
                if correction["id"] not in self.used and any(correction["path"] == pointer or correction["path"].startswith(pointer + "/") for pointer in pointers):
                    self.used.add(correction["id"])
                    answer["corrections"].append(correction)
        if self.review is not None:
            answer = self.review(dict(task), envelope, answer)
        return answer

    def answer(self, task: Mapping[str, Any]) -> Any:
        if self.override is not None:
            value = self.override(task)
            if value is not None:
                return value
        stage = task["stage"]
        if stage == "context-marker:baseline":
            return self.recorded["context-marker"]
        if stage.startswith("tc-generator:"):
            return next(value for label, value in self.recorded.items() if label.startswith("tc-generator."))
        if stage.startswith(("tc-reviewer:", "autotest-reviewer:")):
            return self.review_answer(task)
        if stage.startswith("tc-to-autotest:"):
            return next(value for label, value in self.recorded.items() if label.startswith("tc-to-autotest."))
        raise KeyError(f"no recorded answer for {stage}")

    # ---------------------------------------------------------------- driving

    def call(self, *argv: str) -> tuple[int, dict[str, Any]]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = driver.main(list(argv))
        payload = json.loads(buffer.getvalue())
        self.log.append({"argv": argv, "code": code, "payload": payload})
        return code, payload

    def start(self) -> tuple[int, dict[str, Any]]:
        return self.start_with()

    def start_with(self, *extra: str) -> tuple[int, dict[str, Any]]:
        """Start like the recorded run; ``extra`` flags come last and override (e.g. ``--reviewer-isolation none``).

        The recorded runs reviewed in ``pairs``, so that mode is pinned unless ``review_mode``
        is set to another mode, or to ``None`` for the driver default.
        """
        config = self.config
        mode = () if self.review_mode is None else ("--review-mode", self.review_mode)
        return self.call(
            "next", "--project", str(self.project), "--profile", self.profile, "--docs", DOCS,
            "--subject", config["subject"], "--model-id", config["model_id"], "--reviewer-isolation", "fresh",
            "--host-cli", config["host_cli"], "--host-cli-version", config["host_cli_version"],
            "--document-id", f"TCDOC-step5-java-demo-{self.run[:8]}", *mode, *extra,
        )

    def run_root(self, payload: Mapping[str, Any]) -> Path:
        return self.project / ".pilot-runs" / str(payload["run_id"])

    def submit(self, task: Mapping[str, Any], *extra: str) -> tuple[int, dict[str, Any]]:
        return self.call("submit", "--project", str(self.project), "--run", str(task["run_id"]), "--task-id", str(task["task_id"]), *extra)

    def next(self, run_id: str) -> tuple[int, dict[str, Any]]:
        return self.call("next", "--project", str(self.project), "--run", run_id)

    def drive(self, payload: Mapping[str, Any] | None = None, *, until: Callable[[Mapping[str, Any]], bool] | None = None,
              answers: Mapping[str, str] | None = None, limit: int = 120) -> tuple[int, dict[str, Any]]:
        code, task = (0, dict(payload)) if payload is not None else self.start()
        for _ in range(limit):
            if task.get("action") not in {"llm", "ask_user"} or (until is not None and until(task)):
                return code, task
            if task["action"] == "ask_user":
                label = task["task_id"].split(".ask.", 1)[1]
                code, task = self.submit(task, "--answer", (answers or {})[label])
                continue
            Path(task["output_path"]).write_text(json.dumps(self.answer(task), ensure_ascii=False), encoding="utf-8")
            code, task = self.submit(task)
            if task.get("status") == "rejected":
                raise AssertionError(task.get("errors"))
        raise AssertionError("the driver did not finish")

    def tasks(self, prefix: str) -> list[dict[str, Any]]:
        """Every distinct task the driver issued whose stage starts with ``prefix``."""
        seen: dict[str, dict[str, Any]] = {}
        for row in self.log:
            payload = row["payload"]
            if payload.get("action") == "llm" and str(payload.get("stage", "")).startswith(prefix):
                seen.setdefault(payload["task_id"], payload)
        return list(seen.values())
