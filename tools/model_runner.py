"""Model process runner (opt-in, contract amendments 2026-10-07, A1).

The driver may start a model CLI process itself: each invocation is a new process
without history, in a temporary working directory outside the project, with the whole
role input on stdin (SKILL, task instructions, answer schema, input files) and without
write or shell tools.  Credentials stay with the CLI.

Presets are closed (``claude``, ``codex``).  A project file (``.skillsrc``
``review_runner``) selects only the preset, models per role, ``max_parallel`` and
``timeout_seconds``; a custom command template is a launch flag only.

A process runs detached under a small shim (``python -m tools.model_runner shim <dir>``)
that holds an exclusive lock on ``<dir>/lock`` while it lives and writes ``result.json``
atomically when the CLI ends (or is killed at the timeout).  The driver never waits for
the model: it launches, returns ``wait``, and collects finished results on the next call.
A result without a live shim is collected once; a dead shim without a result is a lost
process (transport failure).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "mutation-triage")
PRESETS: dict[str, dict[str, Any]] = {
    "claude": {
        "cli": "claude",
        # --tools "" disables every built-in tool (no write, no shell); the session is not persisted.
        "argv": ["{cli}", "-p", "--output-format", "json", "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--model", "{model}"],
        "schema_argv": ["--json-schema", "{schema_json}"],
        "output": "claude-json",
        "user_settings_loaded": True,  # without --bare (needs an API key) the CLI reads ~/.claude settings and CLAUDE.md
    },
    "codex": {
        "cli": "codex",
        "argv": ["{cli}", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "--json", "--model", "{model}",
                 "--output-last-message", "{last_message}", "-"],
        "schema_argv": ["--output-schema", "{schema_path}"],
        "output": "codex-jsonl",
        "user_settings_loaded": True,  # ~/.codex configuration is read
    },
}
DEFAULT_TIMEOUT = 1800
DEFAULT_PARALLEL = 4
RATE_LIMIT_PAUSE = 60       # the first pause after a rate or usage limit, seconds
RATE_LIMIT_MAX_PAUSE = 1800  # the pause doubles up to this
RATE_LIMIT_MAX_WAITS = 8     # pauses per task before a limit counts as a failed try (about 2 hours in all)
# API limits (429, overload) and the messages of a Claude Code subscription ("You've hit your limit",
# "Claude AI usage limit reached", weekly / 5-hour limits, "out of usage credits") — review 2.1 item 7.
_RATE_LIMIT = re.compile(
    r"rate.?limit|\b429\b|overloaded|too many requests|usage limit|hit your (?:[\w-]+ )*limit|(?:weekly|daily|monthly|session|[0-9]+-hour) limit"
    r"|out of (?:usage )?credits|limit reached|quota exceeded|resource.?exhausted", re.I)


def is_rate_limited(message: str) -> bool:
    """Whether a CLI error is a rate or usage limit (a pause, not a failed try)."""
    return bool(_RATE_LIMIT.search(message or ""))


def rate_limit_pause(wait: int) -> int:
    """The pause before the ``wait``-th relaunch after a limit: doubling from ``RATE_LIMIT_PAUSE`` up to ``RATE_LIMIT_MAX_PAUSE``."""
    return int(min(RATE_LIMIT_PAUSE * 2 ** max(0, wait - 1), RATE_LIMIT_MAX_PAUSE))


_SECRET = re.compile(r"(?i)(?:sk-[A-Za-z0-9_-]{8,}|api[_-]?key=\S+|token=\S+|bearer\s+\S+)")


class RunnerError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RunnerConfig:
    """What the run uses: a preset (and optionally a custom template from the launch flag)."""

    preset: str
    models: Mapping[str, str] = field(default_factory=dict)
    max_parallel: int = DEFAULT_PARALLEL
    timeout_seconds: int = DEFAULT_TIMEOUT
    command: tuple[str, ...] | None = None  # launch flag only
    cli: str | None = None                  # launch flag only: the CLI executable for the preset

    def to_json(self) -> dict[str, Any]:
        return {"preset": self.preset, "models": dict(self.models), "max_parallel": self.max_parallel, "timeout_seconds": self.timeout_seconds,
                "command": None if self.command is None else list(self.command), "cli": self.cli}

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "RunnerConfig":
        return cls(preset=str(value["preset"]), models=dict(value.get("models") or {}), max_parallel=int(value.get("max_parallel") or DEFAULT_PARALLEL),
                   timeout_seconds=int(value.get("timeout_seconds") or DEFAULT_TIMEOUT),
                   command=None if value.get("command") is None else tuple(str(item) for item in value["command"]), cli=value.get("cli"))

    def model_for(self, role: str, default: str | None) -> str | None:
        return self.models.get(role) or default


def configure(skillsrc: Mapping[str, Any] | None, *, preset: str | None = None, command: Sequence[str] | None = None,
              cli: str | None = None) -> RunnerConfig:
    """The run's runner from ``.skillsrc`` ``review_runner`` and launch flags (a template only from a flag)."""
    section = skillsrc.get("review_runner") if isinstance(skillsrc, Mapping) else None
    section = dict(section) if isinstance(section, Mapping) else {}
    unknown = set(section) - {"preset", "models", "max_parallel", "timeout_seconds"}
    if unknown:
        raise RunnerError("RUNNER_CONFIG", f".skillsrc review_runner has fields a project file may not set: {', '.join(sorted(unknown))}")
    chosen = preset or section.get("preset") or "claude"
    if chosen not in PRESETS:
        raise RunnerError("RUNNER_CONFIG", f"unknown runner preset {chosen!r}: {', '.join(PRESETS)}")
    models = dict(section.get("models") or {})
    if set(models) - set(ROLES):
        raise RunnerError("RUNNER_CONFIG", "review_runner.models names unknown roles")
    if command is not None and (not command or not all(isinstance(item, str) and item for item in command)):
        raise RunnerError("RUNNER_CONFIG", "--review-runner-command is a non-empty JSON array of strings")
    return RunnerConfig(preset=chosen, models=models, max_parallel=int(section.get("max_parallel") or DEFAULT_PARALLEL),
                        timeout_seconds=int(section.get("timeout_seconds") or DEFAULT_TIMEOUT), command=None if command is None else tuple(command), cli=cli)


# --------------------------------------------------------------------------------------
# one invocation
# --------------------------------------------------------------------------------------

def compose_input(skill_path: Path, instructions: str, schema: Mapping[str, Any], inputs: Sequence[Path]) -> str:
    """Everything the role may read, as one stdin text (no file access needed)."""
    parts = [Path(skill_path).read_text(encoding="utf-8"), "", "## Задача", "", instructions, "",
             "## Схема ответа", "", "```json", json.dumps(schema, ensure_ascii=False, indent=1), "```", "", "## Входные данные"]
    for path in inputs:
        parts += ["", f"### Файл {Path(path).name}", "", Path(path).read_text(encoding="utf-8", errors="replace")]
    parts += ["", "Ответ — только один JSON-объект по схеме, без пояснений и без обрамления."]
    return "\n".join(parts) + "\n"


def _self_contained(schema: Mapping[str, Any]) -> bool:
    return '"$ref"' not in json.dumps(schema) or all(ref.startswith("#") for ref in re.findall(r'"\$ref":\s*"([^"]+)"', json.dumps(schema)))


def build_argv(config: RunnerConfig, *, model: str | None, schema: Mapping[str, Any], directory: Path) -> list[str]:
    preset = PRESETS[config.preset]
    template = list(config.command) if config.command is not None else list(preset["argv"])
    schema_path = directory / "schema.json"
    directory.mkdir(parents=True, exist_ok=True)
    schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8", newline="\n")
    values = {"cli": config.cli or shutil.which(preset["cli"]) or preset["cli"], "model": model or "", "schema_path": str(schema_path),
              "schema_json": json.dumps(schema, ensure_ascii=False, separators=(",", ":")), "last_message": str(directory / "last-message.txt")}
    argv = [item.format(**values) if "{" in item else item for item in template]
    if config.command is None and _self_contained(schema):
        argv += [item.format(**values) for item in preset["schema_argv"]]
    if not model and config.command is None:
        index = argv.index("--model")
        del argv[index:index + 2]
    return argv


def command_digest(config: RunnerConfig, *, model: str | None, schema: Mapping[str, Any]) -> str:
    """Digest of the command before expansion (template, preset, model, schema digest): no secrets, no per-try paths.

    The part boundary records it before the process starts; the process receipt repeats it.
    """
    template = list(config.command) if config.command is not None else [*PRESETS[config.preset]["argv"], *PRESETS[config.preset]["schema_argv"]]
    value = {"preset": config.preset, "template": [_SECRET.sub("<redacted>", item) for item in template], "model": model,
             "schema": "sha256:" + hashlib.sha256(json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()}
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def launch(directory: Path, argv: Sequence[str], stdin_text: str, *, timeout: int, digest: str, environment: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Start the detached shim for one invocation; it outlives the driver command."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "stdin.txt").write_text(stdin_text, encoding="utf-8", newline="\n")
    spec = {"argv": list(argv), "timeout": int(timeout), "launched_at": time.time(), "command_digest": digest}
    (directory / "spec.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8", newline="\n")
    env = dict(os.environ if environment is None else environment)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    # The launch token reaches only the shim (its environment); the runner directory keeps its digest.
    # result.json must carry the token: a file a host wrote into the directory cannot (review 2.1 item 8).
    token = secrets.token_hex(32)
    env[LAUNCH_ENV] = token
    _write_atomic(directory / "launch.json", {"launch_digest": _token_digest(token), "launched_at": spec["launched_at"]})
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": str(directory), "env": env, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "DETACHED_PROCESS", 0x00000008) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen([sys.executable, "-m", "tools.model_runner", "shim", str(directory)], **kwargs)
    return {"pid": process.pid, "directory": str(directory)}


LAUNCH_ENV = "TEST_SKILLS_RUNNER_LAUNCH"


def _token_digest(token: str) -> str:
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def bound_to_launch(directory: Path, result: Mapping[str, Any]) -> str | None:
    """Why ``result.json`` is not the record of the process the driver launched, or None.

    The shim alone knows the launch token (its environment); the result carries it and the
    digest of the stdout it captured.  This binds the result to the launch against a mistaken
    or careless host; a host that deliberately rewrites both ``launch.json`` and the result
    can still forge it — DRIVER_PROCESS is not proof against the machine's own user (A1.6).
    """
    try:
        launch = json.loads((Path(directory) / "launch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "no launch record"
    token = result.get("launch_token")
    if not isinstance(token, str) or _token_digest(token) != launch.get("launch_digest"):
        return "the result does not carry the token of this launch"
    data = (Path(directory) / "stdout.txt").read_bytes() if (Path(directory) / "stdout.txt").is_file() else b""
    if "sha256:" + hashlib.sha256(data).hexdigest() != result.get("stdout_sha256") or len(data) != result.get("stdout_bytes"):
        return "stdout.txt is not the output the process captured"
    return None


def _lock(path: Path, *, blocking: bool) -> Any:
    """An exclusive lock on one byte of ``path``; None when it is held by another process."""
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return handle
    except OSError:
        handle.close()
        return None


def _write_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def shim(directory: Path) -> int:
    """Run the CLI once with the lock held, then publish ``result.json``."""
    directory = Path(directory)
    held = _lock(directory / "lock", blocking=False)
    if held is None:
        return 2  # another shim owns this invocation
    spec = json.loads((directory / "spec.json").read_text(encoding="utf-8"))
    token = os.environ.pop(LAUNCH_ENV, None)  # never passed on to the CLI
    workdir = Path(tempfile.mkdtemp(prefix="test-skills-model-"))  # outside the project: no project CLAUDE.md
    started = time.time()
    timed_out = False
    exit_code: int | None = None
    error = None
    with open(directory / "stdin.txt", "rb") as stdin, open(directory / "stdout.txt", "wb") as stdout, open(directory / "stderr.txt", "wb") as stderr:
        try:
            kwargs: dict[str, Any] = {"stdin": stdin, "stdout": stdout, "stderr": stderr, "cwd": str(workdir)}
            if os.name != "nt":
                kwargs["start_new_session"] = True
            process = subprocess.Popen(spec["argv"], **kwargs)
            try:
                exit_code = process.wait(timeout=int(spec["timeout"]))
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_tree(process)
                exit_code = process.wait()
        except OSError as failure:
            error = f"{type(failure).__name__}: {failure}"
    shutil.rmtree(workdir, ignore_errors=True)
    data = (directory / "stdout.txt").read_bytes()
    _write_atomic(directory / "result.json", {"exit_code": exit_code, "timed_out": timed_out, "error": error, "started_at": started, "finished_at": time.time(),
                                              "stdout_sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "stdout_bytes": len(data),
                                              "launch_token": token})
    held.close()
    return 0


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        import signal

        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()


def state(directory: Path) -> str:
    """``done`` (result written), ``running`` (shim holds the lock) or ``lost`` (no shim and no result)."""
    directory = Path(directory)
    if (directory / "result.json").is_file():
        return "done"
    if not (directory / "spec.json").is_file():
        return "lost"
    probe = _lock(directory / "lock", blocking=False)
    if probe is None:
        return "running"
    probe.close()
    # A shim that has not taken its lock yet: give it a short grace period after launch.
    launched = json.loads((directory / "spec.json").read_text(encoding="utf-8")).get("launched_at", 0)
    return "running" if time.time() - float(launched) < 30 else "lost"


# --------------------------------------------------------------------------------------
# reading the result
# --------------------------------------------------------------------------------------

@dataclass
class Outcome:
    """What one finished invocation produced: an answer, or a classified failure, and its evidence."""

    answer: Any = None
    failure: str | None = None         # TRANSPORT or CONTENT
    reason: str | None = None
    rate_limited: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)


def extract_json(text: str) -> Any:
    """The JSON object of a model answer: the whole text, a fenced block, or the outermost object."""
    text = (text or "").strip()
    candidates = [text]
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if fence:
        candidates.append(fence.group(1))
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("no JSON object in the answer")


def _tokens(usage: Mapping[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    keys = {"input_tokens": "input", "output_tokens": "output", "cache_read_input_tokens": "cache_read", "cache_creation_input_tokens": "cache_creation",
            "cached_input_tokens": "cache_read"}
    tokens = {name: 0 for name in ("input", "output", "cache_read", "cache_creation")}
    for key, name in keys.items():
        if isinstance(usage.get(key), int):
            tokens[name] += int(usage[key])
    tokens["total"] = sum(tokens.values())
    return tokens


def read_outcome(directory: Path, *, preset: str, configured_model: str | None) -> Outcome:
    directory = Path(directory)
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    stdout = (directory / "stdout.txt").read_text(encoding="utf-8", errors="replace") if (directory / "stdout.txt").is_file() else ""
    stderr = (directory / "stderr.txt").read_text(encoding="utf-8", errors="replace") if (directory / "stderr.txt").is_file() else ""
    spec = json.loads((directory / "spec.json").read_text(encoding="utf-8"))
    evidence: dict[str, Any] = {
        "command_digest": spec["command_digest"], "cli": Path(spec["argv"][0]).name, "started_at": result["started_at"], "finished_at": result["finished_at"],
        "exit_code": result["exit_code"], "timed_out": result["timed_out"], "stdout_sha256": result["stdout_sha256"], "stdout_bytes": result["stdout_bytes"],
        "session_id": None, "model": configured_model, "model_source": "configured", "tokens": _tokens(None),
        "user_settings_loaded": bool(PRESETS[preset]["user_settings_loaded"]),
    }
    outcome = Outcome(evidence=evidence)
    unbound = bound_to_launch(directory, result)
    if unbound:
        outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_RESULT_UNBOUND: {unbound}"
        return outcome
    if result.get("error"):
        outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_LAUNCH_FAILED: {result['error']}"
        return outcome
    if result["timed_out"]:
        outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_TIMEOUT: the process exceeded {spec['timeout']} s"
        return outcome
    text = None
    try:
        if PRESETS[preset]["output"] == "claude-json":
            payload = json.loads(stdout)
            if not isinstance(payload, dict):
                raise ValueError("not an object")
            evidence["session_id"] = payload.get("session_id")
            evidence["tokens"] = _tokens(payload.get("usage"))
            models = sorted((payload.get("modelUsage") or {}).keys())
            if models:
                evidence.update(model=models[0] if len(models) == 1 else ",".join(models), model_source="cli_output")
            if payload.get("is_error"):
                message = str(payload.get("result") or payload.get("api_error_status") or "error")
                outcome.rate_limited = is_rate_limited(message) or payload.get("api_error_status") == 429
                outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_CLI_ERROR: {message[:300]}"
                return outcome
            if payload.get("stop_reason") == "max_tokens":
                outcome.failure, outcome.reason = "TRANSPORT", "RUNNER_OUTPUT_TRUNCATED: the answer did not fit the process output (max_tokens)"
                return outcome
            text = payload.get("structured_output")
            text = json.dumps(text, ensure_ascii=False) if isinstance(text, (dict, list)) else payload.get("result")
        else:
            for line in stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "thread.started":
                    evidence["session_id"] = event.get("thread_id")
                elif event.get("type") == "turn.completed":
                    evidence["tokens"] = _tokens(event.get("usage"))
                elif event.get("type") == "item.completed" and (event.get("item") or {}).get("type") == "agent_message":
                    text = event["item"].get("text")
                elif event.get("type") in {"error", "turn.failed"}:
                    message = json.dumps(event, ensure_ascii=False)[:300]
                    outcome.rate_limited = is_rate_limited(message)
                    outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_CLI_ERROR: {message}"
                    return outcome
            last = directory / "last-message.txt"
            if last.is_file():
                text = last.read_text(encoding="utf-8", errors="replace")
            evidence["model_source"] = "configured"  # codex --json events do not name the model
    except (ValueError, json.JSONDecodeError):
        outcome.rate_limited = is_rate_limited(stdout + stderr)
        if result["exit_code"] != 0:
            outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_EXIT_{result['exit_code']}: {(stderr or stdout)[-300:].strip()}"
        else:
            outcome.failure, outcome.reason = "TRANSPORT", "RUNNER_OUTPUT_NOT_JSON: the CLI output is not its JSON format" + (" (rate limit)" if outcome.rate_limited else "")
        return outcome
    if result["exit_code"] != 0:
        outcome.rate_limited = is_rate_limited(stdout + stderr)
        outcome.failure, outcome.reason = "TRANSPORT", f"RUNNER_EXIT_{result['exit_code']}: {(stderr or stdout)[-300:].strip()}"
        return outcome
    if not text:
        outcome.failure, outcome.reason = "TRANSPORT", "RUNNER_OUTPUT_EMPTY: the CLI returned no answer text"
        return outcome
    try:
        outcome.answer = extract_json(text)
    except ValueError:
        outcome.failure, outcome.reason = "TRANSPORT", "RUNNER_ANSWER_NOT_JSON: the answer text holds no complete JSON object (truncated or prose)"
    return outcome


def cli_version(config: RunnerConfig) -> str:
    """``<name> <version>`` of the CLI (best effort, never fails)."""
    if config.command is not None:
        return f"custom {Path(config.command[0]).name}"
    preset = PRESETS[config.preset]
    executable = config.cli or shutil.which(preset["cli"])
    if not executable:
        return f"{preset['cli']} unavailable"
    try:
        completed = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=60, check=False)
        return f"{preset['cli']} {(completed.stdout or completed.stderr).strip().splitlines()[0][:80]}"
    except (OSError, subprocess.SubprocessError, IndexError):
        return f"{preset['cli']} unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Model process runner shim.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("shim")
    run.add_argument("directory")
    args = parser.parse_args(argv)
    return shim(Path(args.directory))


if __name__ == "__main__":
    raise SystemExit(main())
