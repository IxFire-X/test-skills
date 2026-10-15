"""W2-Р12: one review part through the driver's process runner, against the subagent numbers of wave 1.

    python evals/review-scaling/run_eval.py plan --set E1 --mode compact-v1 --out <dir>
    python evals/test-strength/runner_cost.py run --dir <dir> --part part-000001 --preset claude --model sonnet [--cli <path>]
    python evals/review-scaling/run_eval.py score --dir <dir>

``run`` gives the part exactly what the driver would (``tools.model_runner.compose_input``
with the production SKILL, task instructions and answer schema), starts the CLI through
the runner's shim, checks the answer with the production validator (``run_eval.py
check``) and repeats with the rejection as feedback, at most three processes.  Tokens,
seconds, tries and the process evidence go to ``<dir>/<part>.runner.json``.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals" / "review-scaling"))


def cmd_run(args: argparse.Namespace) -> int:
    import run_eval

    from tools import model_runner

    directory = Path(args.dir).resolve()
    meta = json.loads((directory / "tasks.json").read_text(encoding="utf-8"))
    task = next(item for item in meta["tasks"] if item["part_id"] == args.part)
    schema = json.loads(Path(task["schema"]).read_text(encoding="utf-8"))
    config = model_runner.RunnerConfig(preset=args.preset, cli=args.cli, timeout_seconds=args.timeout)
    feedback, rows, tries = "", [], []
    for number in range(1, 4):
        process = directory / "runner" / f"{args.part}-try{number}"
        stdin = model_runner.compose_input(Path(task["skill"]), task["instructions"] + feedback, schema, [Path(task["input"])])
        argv = model_runner.build_argv(config, model=args.model, schema=schema, directory=process)
        model_runner.launch(process, argv, stdin, timeout=args.timeout, digest=model_runner.command_digest(config, model=args.model, schema=schema))
        while model_runner.state(process) == "running":
            time.sleep(1)
        outcome = model_runner.read_outcome(process, preset=args.preset, configured_model=args.model)
        row = {"try": number, "input_bytes": len(stdin.encode("utf-8")), "failure": outcome.failure, "reason": outcome.reason, **outcome.evidence}
        row["seconds"] = round(row["finished_at"] - row["started_at"], 1)
        if outcome.failure is None:
            Path(task["output"]).write_text(json.dumps(outcome.answer, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            rows = run_eval.diagnostics(directory, args.part)
            row["accepted"] = not rows
            row["codes"] = sorted({item["code"] for item in rows})
        tries.append(row)
        if outcome.failure is None and not rows:
            break
        feedback = ("\n\nКонтроллер отклонил предыдущий ответ: " + json.dumps(rows[:20], ensure_ascii=False)) if outcome.failure is None else \
                   f"\n\nПредыдущий процесс не дал ответа ({outcome.reason})."
    report = {"part": args.part, "preset": args.preset, "model": args.model, "accepted": bool(tries and tries[-1].get("accepted")), "tries": tries,
              "tokens": sum(row["tokens"]["total"] for row in tries), "seconds": round(sum(row["seconds"] for row in tries), 1)}
    (directory / f"{args.part}.runner.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: report[key] for key in ("part", "model", "accepted", "tokens", "seconds")} | {"tries": len(tries)}, ensure_ascii=False))
    return 0 if report["accepted"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--dir", required=True)
    run.add_argument("--part", required=True)
    run.add_argument("--preset", default="claude", choices=("claude", "codex"))
    run.add_argument("--model", default="sonnet")
    run.add_argument("--cli")
    run.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    return {"run": cmd_run}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
