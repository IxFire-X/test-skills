#!/usr/bin/env python3
"""wrap.py — a test-skills model wrapper for an in-house CLI, called by the package as preset `codex`.

The package (``--runner process --review-runner-preset codex --review-runner-cli /abs/path/wrap.py``) runs:

    wrap.py exec --skip-git-repo-check --sandbox read-only --ephemeral --json [--model M]
            --output-last-message <file> - [--output-schema <schema.json>]

with the whole task on stdin, and ``wrap.py --version``.  The in-house CLI is described in ``wrap.json`` beside this
file (or ``$WRAP_CONFIG``), without code changes:

    {"command": ["inhouse-cli", "chat", "--model", "{model}", "--input", "{prompt_file}"],
     "prompt_via": "stdin" | "file" | "arg",   # how the task reaches the CLI
     "answer_from": "stdout" | "file",         # where the CLI leaves its answer
     "answer_file": "{tmp}/answer.txt",        # with answer_from = file
     "timeout_seconds": 1700, "extra_env": {"NAME": "value"}, "version": "inhouse-cli 1.2",
     "tmp_dir": ".tmp",                        # where the per-call temp directory goes (relative: to wrap.json)
     "sandbox_args": ["--no-tools"],           # the CLI's own "no tools / read-only" flags, if it has them
     "env_passthrough": ["GIGACODE_*", "HTTPS_PROXY"]}  # environment the CLI needs beyond the base list (S6)

Placeholders: {model}, {prompt_file}, {prompt} (prompt_via = arg; Linux caps one argument at 128 KiB), {answer_file},
{schema_file}, {tmp}.  ``sandbox_args`` replace an item "{sandbox_args}" of command, else go at its end.
``extra_env`` must not carry secrets (names with TOKEN, SECRET, PASSWORD, PASSWD, API_KEY, APIKEY, AWS_*, AZURE_*,
GOOGLE_APPLICATION_CREDENTIALS): the CLI's credentials belong in the CLI's own config, not in wrap.json.
The CLI does not inherit the whole environment (S6): it gets PATH, HOME, JAVA_HOME, LANG, LANGUAGE, LC_*, TMP*, TEMP,
PYTHONUTF8, PYTHONIOENCODING (a Python CLI on Windows needs them for UTF-8 output) and
the Windows minimum (SYSTEMROOT, SYSTEMDRIVE, WINDIR, COMSPEC, PATHEXT, USERPROFILE, APPDATA, LOCALAPPDATA, HOMEDRIVE,
HOMEPATH), the names and prefixes of ``env_passthrough`` (a name, or a prefix of 3+ characters ending in ``*``; secret-like
names are refused as in ``extra_env``), then ``extra_env``.
``wrap.py exec --verbose …`` prints the passed names (not values) to stderr in one line.  The answer's JSON object (the whole text, a ```json block or the outer braces) goes to
--output-last-message; stdout gets codex `--json` events (thread.started, item.completed, turn.completed with usage —
the CLI's own when its stdout is JSON with `usage`, else an estimate by bytes, marked "estimated").  A CLI failure
prints an `error` event (with its text, so rate limits are recognised) and exits non-zero.

``wrap.py --selftest [--pack <test-skills copy>]`` checks the wrapper with the fake CLI beside it in three modes and
one real review part from the replay fixtures through the package's validate_answer.  Python 3.11+, stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARG_LIMIT = 120_000  # Linux MAX_ARG_STRLEN is 128 KiB per argument
BASE_ENV = {"PATH", "HOME", "JAVA_HOME", "LANG", "LANGUAGE", "TEMP", "PYTHONUTF8", "PYTHONIOENCODING", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT",
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH"}
BASE_PREFIXES = ("LC_", "TMP")
PASSTHROUGH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\*?$")
SECRET_ENV = re.compile(r"(?i)TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|^AWS_|^AZURE_|^GOOGLE_APPLICATION_CREDENTIALS$")


def config_path() -> Path:
    return Path(os.environ.get("WRAP_CONFIG") or HERE / "wrap.json")


def load_config(path: Path | None = None) -> dict:
    path = path or config_path()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value.get("command"), list) or not value["command"]:
        raise SystemExit("wrap.json: command must be a non-empty list")
    if value.setdefault("prompt_via", "stdin") not in {"stdin", "file", "arg"}:
        raise SystemExit("wrap.json: prompt_via must be stdin, file or arg")
    if value.setdefault("answer_from", "stdout") not in {"stdout", "file"}:
        raise SystemExit("wrap.json: answer_from must be stdout or file")
    value.setdefault("answer_file", "{tmp}/answer.txt")
    value.setdefault("timeout_seconds", 1700)
    value.setdefault("extra_env", {})
    if not isinstance(value["extra_env"], dict):
        raise SystemExit("wrap.json: extra_env must be an object")
    secrets = sorted(str(name) for name in value["extra_env"] if SECRET_ENV.search(str(name)))
    if secrets:
        raise SystemExit(f"wrap.json: extra_env carries secret-like names ({', '.join(secrets)}) — secrets go to the CLI's own config, "
                         "not to wrap.json")
    passthrough = value.setdefault("env_passthrough", [])
    if not isinstance(passthrough, list) or not all(isinstance(item, str) and PASSTHROUGH.match(item) for item in passthrough):
        raise SystemExit("wrap.json: env_passthrough is a list of variable names or prefixes ending in * (GIGACODE_*, HTTPS_PROXY)")
    refused = sorted(item for item in passthrough if SECRET_ENV.search(item.rstrip("*")) or (item.endswith("*") and len(item) - 1 < 3))
    if refused:
        raise SystemExit(f"wrap.json: env_passthrough refuses secret-like names and prefixes shorter than 3 characters ({', '.join(refused)}) — "
                         "keys and tokens belong in the CLI's own config, not in the environment wrap.py passes")
    sandbox = value.setdefault("sandbox_args", [])
    if not isinstance(sandbox, list) or not all(isinstance(item, str) for item in sandbox):
        raise SystemExit("wrap.json: sandbox_args must be a list of strings")
    if value.get("tmp_dir"):
        tmp_dir = Path(os.path.expanduser(str(value["tmp_dir"])))
        value["tmp_dir"] = str(tmp_dir if tmp_dir.is_absolute() else (path.parent / tmp_dir).resolve())
    return value


def child_env(config: dict, environ: dict | None = None) -> dict:
    """The CLI's environment (S6): the base list, the env_passthrough names and prefixes, then extra_env."""
    environ = dict(os.environ if environ is None else environ)
    names = {item.upper() for item in config.get("env_passthrough") or [] if not item.endswith("*")}
    prefixes = tuple(item[:-1].upper() for item in config.get("env_passthrough") or [] if item.endswith("*"))
    env = {key: value for key, value in environ.items()
           if key.upper() in BASE_ENV or key.upper() in names or key.upper().startswith(BASE_PREFIXES + prefixes)}
    return {**env, **{str(key): str(value) for key, value in (config.get("extra_env") or {}).items()}}


def build_command(config: dict, values: dict) -> list[str]:
    sandbox = list(config.get("sandbox_args") or [])
    command = [part for item in config["command"] for part in (sandbox if item == "{sandbox_args}" else [item.format(**values)])]
    return command if "{sandbox_args}" in config["command"] else [*command, *sandbox]


def option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def extract_json(text: str) -> str | None:
    """The answer's JSON object as text, found as the package finds it (whole text, ```json block, outer braces)."""
    text = (text or "").strip()
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if fence:
        candidates.append(fence.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for candidate in candidates:
        try:
            if isinstance(json.loads(candidate), dict):
                return candidate.strip()
        except ValueError:
            continue
    return None


def usage_of(stdout: str, prompt: str, answer: str) -> dict:
    """The CLI's usage when its stdout is a JSON object (or JSONL) with `usage`; else an estimate by bytes."""
    for line in [stdout, *stdout.splitlines()]:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        usage = value.get("usage") if isinstance(value, dict) else None
        if isinstance(usage, dict):
            names = {"input_tokens": ("input_tokens", "prompt_tokens"), "output_tokens": ("output_tokens", "completion_tokens"),
                     "cached_input_tokens": ("cached_input_tokens", "cached_tokens")}
            return {key: int(next((usage[name] for name in options if isinstance(usage.get(name), int)), 0)) for key, options in names.items()}
    return {"input_tokens": len(prompt.encode("utf-8")) // 4, "output_tokens": len(answer.encode("utf-8")) // 4, "estimated": True}


def event(value: dict) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def answer_text_of(stdout: str) -> str:
    """The model text in the CLI's stdout: a JSON envelope's text field when it has one, else the stdout itself."""
    try:
        value = json.loads(stdout)
    except ValueError:
        return stdout
    if isinstance(value, dict):
        for key in ("result", "text", "content", "output", "answer"):
            if isinstance(value.get(key), str):
                return value[key]
        choices = value.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            if isinstance(message.get("content"), str):
                return message["content"]
    return stdout


def run_exec(argv: list[str], prompt: str, config: dict) -> int:
    model = option(argv, "--model") or ""
    last = option(argv, "--output-last-message")
    schema = option(argv, "--output-schema") or ""
    event({"type": "thread.started", "thread_id": f"wrap-{os.getpid()}-{int(time.time())}"})
    if not last:
        event({"type": "error", "message": "wrap: --output-last-message is missing"})
        return 2
    if config.get("tmp_dir"):
        Path(config["tmp_dir"]).mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = Path(tempfile.mkdtemp(prefix="wrap-", dir=config.get("tmp_dir") or None))
    try:
        prompt_file = tmp / "prompt.txt"
        prompt_file.write_bytes(prompt.encode("utf-8"))
        values = {"model": model, "prompt_file": str(prompt_file), "schema_file": schema, "tmp": str(tmp), "prompt": prompt}
        values["answer_file"] = config["answer_file"].format(**{key: value for key, value in values.items() if key != "prompt"})
        if config["prompt_via"] == "arg" and len(prompt.encode("utf-8")) > ARG_LIMIT:
            event({"type": "error", "message": f"wrap: the task is {len(prompt.encode('utf-8'))} bytes, too long for one argument (prompt_via=arg); use stdin or file"})
            return 2
        command = build_command(config, values)
        env = child_env(config)
        if "--verbose" in argv:
            sys.stderr.write("wrap: environment passed to the CLI: " + ", ".join(sorted(env)) + "\n")
        try:
            done = subprocess.run(command, input=prompt.encode("utf-8") if config["prompt_via"] == "stdin" else None, capture_output=True, env=env,
                                  cwd=str(tmp), timeout=int(config["timeout_seconds"]), check=False)
        except subprocess.TimeoutExpired:
            event({"type": "error", "message": f"wrap: the CLI exceeded {config['timeout_seconds']} s"})
            return 124
        except OSError as error:
            event({"type": "error", "message": f"wrap: the CLI did not start: {error}"})
            return 127
        stdout, stderr = done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")
        if done.returncode != 0:
            event({"type": "error", "message": f"wrap: the CLI exited {done.returncode}: {(stderr or stdout)[-600:]}"})
            sys.stderr.write(stderr[-4000:])
            return done.returncode
        if config["answer_from"] == "file":
            path = Path(values["answer_file"])
            raw = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        else:
            raw = answer_text_of(stdout)
        if not raw.strip():
            event({"type": "error", "message": "wrap: the CLI returned no answer"})
            return 3
        found = extract_json(raw)
        text = found if found is not None else raw  # without a JSON object the package rejects it and retries with the reason
        if found is None:
            sys.stderr.write("wrap: no JSON object in the answer; passed on as it is\n")
        Path(last).write_bytes(text.encode("utf-8"))
        event({"type": "item.completed", "item": {"type": "agent_message", "text": text}})
        event({"type": "turn.completed", "usage": usage_of(stdout, prompt, text)})
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    if argv[:1] == ["--version"]:
        config = load_config()
        print(config.get("version") or f"wrap 1.0 ({Path(config['command'][0]).name})")
        return 0
    if argv[:1] == ["--selftest"]:
        sys.path.insert(0, str(HERE))
        from selftest import selftest

        return selftest(HERE, option(argv, "--pack"))
    if argv[:1] != ["exec"]:
        print("usage: wrap.py exec … --output-last-message FILE - | --version | --selftest [--pack DIR]", file=sys.stderr)
        return 2
    return run_exec(argv, sys.stdin.buffer.read().decode("utf-8", "replace"), load_config())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
