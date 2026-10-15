r"""E5, генераторы: прогон драйвера на копии проекта step5, живые стадии отдаёт модели.

    python evals/review-scaling/e5_drive.py start --dir D --kind cases|autotest
    python evals/review-scaling/e5_drive.py submit --dir D

``start`` копирует проект step5 в ``D/project`` и запускает драйвер с настройками живого
прогона ``9340016c`` (режим ревью — по умолчанию драйвера).  Стадии, которые здесь не
проверяются, отвечаются сами:

* ``context-marker`` — записанный ответ ``9340016c``;
* ``kind=autotest``: генератор кейсов — записанный ответ, ревью кейсов — чистый ответ
  compact (канон ``9340016c`` принят без правок).

На живой стадии (генератор и ревьюер своего ``kind``, вопрос пользователю) скрипт
останавливается и печатает задачу; её ответ пишет модель в ``output_path``, затем
``submit`` отдаёт его драйверу и ведёт дальше.  Каждый ``submit`` и каждая задача
пишутся в ``D/e5-log.jsonl`` (отказы до принятия считаются по нему).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.live_step5 import Replay  # noqa: E402
from tests.review_scaling_helpers import clean_compact_answer, part_text  # noqa: E402

LIVE = {"cases": ("tc-generator:", "tc-reviewer:"), "autotest": ("tc-to-autotest:", "autotest-reviewer:")}
JDK = Path("D:/AI-Projects/.tools/jdk-17")


def _replay(directory: Path, kind: str, *, fresh: bool) -> Replay:
    from tests.live_step5 import RUNS, load, outputs, run_dir

    profile = "cases-only-v1" if kind == "cases" else None
    if fresh:
        replay = Replay("9340016c", directory, profile=profile)
    else:  # the project copy already exists: attach to it
        replay = Replay.__new__(Replay)
        replay.run = RUNS["9340016c"]
        replay.config = load(run_dir("9340016c") / "driver" / "config.json")
        replay.profile = profile or replay.config["profile"]
        replay.recorded = outputs("9340016c")
        replay.project = directory / "project"
        replay.review, replay.used, replay.log = None, set(), []
    replay.review_mode = None  # the driver default
    replay.override = lambda task: _auto(kind, task)
    return replay


def _auto(kind: str, task: Mapping[str, Any]) -> Any:
    stage = str(task.get("stage", ""))
    if kind == "autotest" and stage.startswith("tc-reviewer:"):
        return clean_compact_answer(part_text(dict(task)))
    return None


def _live(kind: str, task: Mapping[str, Any]) -> bool:
    return task.get("action") == "ask_user" or str(task.get("stage", "")).startswith(LIVE[kind])


def _log(directory: Path, row: Mapping[str, Any]) -> None:
    with (directory / "e5-log.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"at": time.time(), **row}, ensure_ascii=False) + "\n")


def _drive(directory: Path, kind: str, replay: Replay, code: int, task: dict[str, Any]) -> int:
    for _ in range(200):
        _log(directory, {"event": "task", "code": code, "action": task.get("action"), "stage": task.get("stage"),
                         "task_id": task.get("task_id"), "status": task.get("status")})
        if task.get("action") not in {"llm", "ask_user"} or _live(kind, task):
            (directory / "state.json").write_text(json.dumps({"kind": kind, "task": task}, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return code
        Path(task["output_path"]).write_text(json.dumps(replay.answer(task), ensure_ascii=False), encoding="utf-8")
        code, task = replay.submit(task)
        _log(directory, {"event": "auto-submit", "stage": task.get("stage"), "status": task.get("status")})
        if task.get("status") == "rejected":
            raise SystemExit(f"recorded answer rejected: {task.get('errors')}")
    raise SystemExit("the driver did not stop")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--dir", type=Path, required=True)
    start.add_argument("--kind", choices=tuple(LIVE), required=True)
    again = sub.add_parser("submit")
    again.add_argument("--dir", type=Path, required=True)
    again.add_argument("--answer", help="answer to an ask_user task")
    args = parser.parse_args(argv)
    os.environ.setdefault("JAVA_HOME", str(JDK))
    directory = args.dir.resolve()
    if args.command == "start":
        directory.mkdir(parents=True, exist_ok=True)
        replay = _replay(directory, args.kind, fresh=True)
        code, task = replay.start()
        return _drive(directory, args.kind, replay, code, task)
    state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
    kind, task = state["kind"], state["task"]
    replay = _replay(directory, kind, fresh=False)
    extra = ("--answer", args.answer) if args.answer is not None else ()
    code, result = replay.submit(task, *extra)
    _log(directory, {"event": "live-submit", "stage": task.get("stage"), "task_id": task.get("task_id"), "status": result.get("status"),
                     "errors": result.get("errors")})
    if result.get("status") == "rejected":
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    return _drive(directory, kind, replay, code, result)


if __name__ == "__main__":
    raise SystemExit(main())
