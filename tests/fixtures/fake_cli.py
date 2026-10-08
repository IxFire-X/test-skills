"""A fake model CLI for the process runner tests (claude ``-p --output-format json`` shape).

    python fake_cli.py --script garbage,ok [--state <dir>]

Reads the whole role input from stdin.  ``--script`` lists the behaviour of successive
calls with the same input (the last one repeats): ``ok`` answers, ``garbage`` prints
non-JSON, ``prose`` returns a result without a JSON object, ``nonzero`` exits 3,
``ratelimit`` reports an API 429, ``sublimit``/``sublimit-text`` a subscription limit (JSON / plain text), ``timeout`` sleeps for an hour, ``nosession`` answers
without a session id, and ``fixed-session`` answers with the same session id every time.

Answers: a compact review part gets a clean answer (every area checked, every lint
suspicion rejected), a triage task gets EQUIVALENT for every group, any other task gets
the recorded answer of the live step5 run ``9340016c`` for its stage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _part_text(prompt: str) -> str | None:
    match = re.search(r"### Файл [^\n]+\.input\.md\n\n(.*)\n\nОтвет — только один JSON-объект", prompt, re.S)
    return match.group(1) if match else None


def answer(prompt: str) -> dict:
    text = _part_text(prompt)
    if text is not None and text.lstrip().startswith("# Разбор выживших мутантов"):
        groups = re.findall(r"^### \[(MUT-\d{4})\]", text, re.M)
        rows = []
        for group in groups:
            line = next(line for line in text.splitlines() if line.startswith(f"[{group}:L") and "] >" in line)
            rows.append({"group_id": group, "decision": "EQUIVALENT", "refs": [line[1:line.index("]")]], "rationale": "Поведение не меняется."})
        return {"groups": rows}
    if text is not None:
        from tests.review_scaling_helpers import clean_compact_answer

        return clean_compact_answer(text)
    from tests.live_step5 import outputs

    recorded = outputs("9340016c")
    if "context-marker-draft.json" in prompt:
        draft = json.loads(re.search(r"### Файл context-marker-draft\.json\n\n(.*?)\n\n### Файл", prompt, re.S).group(1))
        return {**recorded["context-marker"], "artifacts": {**recorded["context-marker"]["artifacts"], "analytics_documentation": draft["artifacts"]["analytics_documentation"]}}
    if "### Файл generator-" in prompt:
        return next(value for label, value in recorded.items() if label.startswith("tc-generator."))
    if "### Файл automation-r1-brief.json" in prompt:
        return next(value for label, value in recorded.items() if label.startswith("tc-to-autotest."))
    raise SystemExit("fake cli: unknown task")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", default="ok")
    parser.add_argument("--state", default=os.environ.get("FAKE_CLI_STATE"))
    args, _rest = parser.parse_known_args()
    prompt = sys.stdin.buffer.read().decode("utf-8")
    steps = args.script.split(",")
    mode = steps[0]
    if args.state:
        state = Path(args.state)
        state.mkdir(parents=True, exist_ok=True)
        counter = state / hashlib.sha256((_part_text(prompt) or prompt).encode("utf-8")).hexdigest()[:16]  # tries of one part share the counter
        calls = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(calls + 1))
        mode = steps[min(calls, len(steps) - 1)]
    session = "fixed-session" if mode == "fixed-session" else str(uuid.uuid4())
    if mode == "timeout":
        time.sleep(3600)
    if mode == "garbage":
        print("<<< not json >>>")
        return 0
    if mode == "nonzero":
        print("fatal: something broke", file=sys.stderr)
        return 3
    if mode == "ratelimit":
        print(json.dumps({"type": "result", "is_error": True, "api_error_status": 429, "result": "API Error: 429 rate_limit_error", "session_id": session}))
        return 1
    if mode == "sublimit":  # a Claude Code subscription limit: no HTTP status in the result
        print(json.dumps({"type": "result", "is_error": True, "result": "You've hit your limit · resets 5pm (Europe/Moscow)", "session_id": session}))
        return 1
    if mode == "sublimit-text":  # the same as plain text on stderr
        print("Claude AI usage limit reached|1760000000", file=sys.stderr)
        return 1
    result = "Вот мой ответ без JSON." if mode == "prose" else json.dumps(answer(prompt), ensure_ascii=False)
    payload = {"type": "result", "subtype": "success", "is_error": False, "result": result, "stop_reason": "end_turn",
               "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(result) // 4, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
               "modelUsage": {"fake-model-1": {}}}
    if mode != "nosession":
        payload["session_id"] = session
    sys.stdout.buffer.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
