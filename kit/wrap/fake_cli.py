#!/usr/bin/env python3
"""A fake in-house model CLI for wrap.py's selftest: answers a review part with run d's recorded answer.

    fake_cli.py --mode stdin|file|arg [--prompt-file F] [--prompt TEXT] [--answer-file A] [--model M]
                [--envelope] [--fence] [--fail]

The task comes on stdin, from a file or as an argument; the answer goes to stdout or to a file.  ``--envelope`` prints
{"result": …, "usage": {…}} (a JSON CLI), ``--fence`` wraps the answer in prose and a ```json block, ``--fail`` prints a
rate-limit error and exits 3.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bench"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("stdin", "file", "arg"), required=True)
    parser.add_argument("--prompt-file")
    parser.add_argument("--prompt")
    parser.add_argument("--answer-file")
    parser.add_argument("--model")
    parser.add_argument("--envelope", action="store_true")
    parser.add_argument("--fence", action="store_true")
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    if args.fail:
        print("Error: 429 Too Many Requests — rate limit exceeded", file=sys.stderr)
        return 3
    prompt = {"stdin": lambda: sys.stdin.buffer.read().decode("utf-8"), "file": lambda: Path(args.prompt_file).read_text(encoding="utf-8"),
              "arg": lambda: args.prompt or ""}[args.mode]()
    found = re.search(r"review\.(canonical|r1)\.(part-\d{6})", prompt)
    if not found and "test-skills-probe" in prompt:  # operate.py step 2: one live call
        sys.stdout.write(json.dumps({"ok": True, "probe": "test-skills-probe", "model": args.model}))
        return 0
    if not found:
        print("fake: no review part in the task", file=sys.stderr)
        return 4
    import replay

    answer = json.dumps(replay.reference(found.group(1), found.group(2)), ensure_ascii=False)
    if args.model == "prose-model":  # a weak model: no JSON object at all
        answer = "Я проверил часть, замечаний нет."
    elif args.model == "lazy-model":  # a model that drops every finding: valid shape, nothing found
        value = json.loads(answer)
        value["findings"] = []
        answer = json.dumps(value, ensure_ascii=False)
    text = f"Вот ответ по части:\n```json\n{answer}\n```\nГотово." if args.fence else answer
    if args.envelope:
        text = json.dumps({"result": text, "model": args.model, "usage": {"prompt_tokens": len(prompt) // 3, "completion_tokens": len(answer) // 3}}, ensure_ascii=False)
    if args.answer_file:
        Path(args.answer_file).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
