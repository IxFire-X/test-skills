"""``wrap.py --selftest``: the wrapper with the fake CLI in its three modes, its error paths and a real review part.

A real part: the smallest base part of the case review of the live Petclinic run, composed as the driver composes it;
the fake CLI answers with run d's recorded answer; the wrapper's output must pass the checks of a real submit
(answer schema and ``review_compact.validate_answer``).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROWS: list[tuple[str, str, str]] = []


def note(ok: bool, check: str, detail: str = "") -> None:
    ROWS.append(("ок" if ok else "стоп", check, detail))


def call(here: Path, config: dict, prompt: str, work: Path, name: str) -> tuple[int, list[dict], str | None, str]:
    path = work / f"{name}.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    last = work / f"{name}.last.txt"
    argv = [sys.executable, str(here / "wrap.py"), "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "--json",
            "--model", "fake-model", "--output-last-message", str(last), "-"]
    done = subprocess.run(argv, input=prompt.encode("utf-8"), capture_output=True, env={**os.environ, "WRAP_CONFIG": str(path)}, timeout=600, check=False)
    events = []
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return done.returncode, events, last.read_text(encoding="utf-8") if last.is_file() else None, done.stderr.decode("utf-8", "replace")


def local(here: Path, config: dict) -> dict:
    """An example config with this machine's Python and the fake CLI's absolute path (the CLI runs in a temp directory)."""
    command = [sys.executable if item == "python3" else str(here / "fake_cli.py") if item == "fake_cli.py" else item for item in config["command"]]
    return {**config, "command": command}


def selftest(here: Path, pack: str | None) -> int:
    sys.path.insert(0, str(here.parent / "bench"))
    import replay

    pack_root = replay.use_pack(pack or os.environ.get("TEST_SKILLS_PACK") or here.parent / "test-skills")
    version = subprocess.run([sys.executable, str(here / "wrap.py"), "--version"], capture_output=True, text=True, timeout=60, check=False)
    note(version.returncode == 0 and bool(version.stdout.strip()), "--version", version.stdout.strip())
    part_id, size = min(replay.base_parts("canonical"), key=lambda row: row[1])
    with tempfile.TemporaryDirectory(prefix="wrap-selftest-") as temp:
        work = Path(temp)
        prompt = replay.task_input(pack_root, "canonical", part_id, work / "inputs")
        note(True, "задача части ревью", f"canonical {part_id}: часть {size} Б, вход модели {len(prompt.encode('utf-8'))} Б")
        short = f"### Файл replay.review.canonical.{part_id}.input.md\n\nОтвет — только один JSON-объект по схеме."
        for name in ("stdin-stdout", "file-file", "arg-stdout"):
            config = local(here, json.loads((here / "examples" / f"{name}.json").read_text(encoding="utf-8")))
            text = short if config["prompt_via"] == "arg" else prompt
            code, events, last, stderr = call(here, config, text, work, name)
            kinds = [event.get("type") for event in events]
            answer = None
            try:
                answer = json.loads(last) if last else None
            except ValueError:
                pass
            rejected = replay.check("canonical", part_id, answer) if answer is not None else ["NO_ANSWER"]
            note(code == 0 and kinds[:1] == ["thread.started"] and "turn.completed" in kinds and not rejected, f"режим {name}",
                 f"код {code}, события {kinds}, проверки submit: {rejected or 'приняты'}" + (f", stderr: {stderr[-200:]}" if code else ""))
        failing = local(here, {**json.loads((here / "examples" / "stdin-stdout.json").read_text(encoding="utf-8"))})
        failing["command"] = [*failing["command"], "--fail"]
        code, events, last, _stderr = call(here, failing, prompt, work, "fail")
        message = next((event.get("message", "") for event in events if event.get("type") == "error"), "")
        note(code != 0 and "rate limit" in message.lower() and last is None, "ошибка CLI → событие error и код ≠ 0", f"код {code}: {message[:120]}")
        too_long = local(here, json.loads((here / "examples" / "arg-stdout.json").read_text(encoding="utf-8")))
        code, events, _last, _stderr = call(here, too_long, prompt, work, "arg-too-long")
        note(code != 0 and any("too long" in event.get("message", "") for event in events), "длинная задача аргументом → понятная ошибка", f"код {code}")
    width = max(len(row[1]) for row in ROWS)
    for status, check, detail in ROWS:
        print(f"{status:5} {check:{width}}  {detail}")
    failed = sum(row[0] == "стоп" for row in ROWS)
    print(f"\nИтог: {'ок' if not failed else f'стоп {failed}'}")
    return 1 if failed else 0
