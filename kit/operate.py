#!/usr/bin/env python3
"""operate.py — the person's tool around a host-mode test-skills run (the agent of the company's CLI drives the run itself).

    python3 operate.py init --project <project> --docs <spec> [--docs …] [--subject "<тема>"] [--module ID] [--target T]
        [--profile local-pilot-v1|cases-only-v1] [--model M] [--host-cli gigacode] [--id-pattern RE] [--no-mutation] [--online]
        [--tokens-per-byte 0.24] [--model-context 239144] [--task-input-bytes N] [--yes] [--yes-skillsrc] [--yes-chmod]
        [--no-pip] [--dry-run]
    python3 operate.py report --project <project> [--run <run_id>] [--only 7|8] [--yes] [--dry-run]

The kit lives inside the project (<home>, by default the folder of operate.py: <project>/.gigacode/skills/test-skills).
init, once, before the agent's session: 0 the package against test-skills-*.files.sha256 and doctor; 1 preflight (chmod
+x of mvnw/gradlew only after a confirmation); the project's .skillsrc (created by the package's own init_skillsrc,
then step 4: requirements.id_pattern, mutation, offline builds, limits.task_input_bytes, execution.mode human — a diff
and a confirmation first); the first task measured without a model in a throwaway run (removed by its id); then the
ready first message for the agent (<home>/agent-message.txt).  report, after the run: 7 <home>/report/ with the
figures (the result comes from the driver's `status`), 8 the Zephyr CSV.

operate.py opens no network connection, installs nothing, starts no model CLI and no project build: it runs only the
package's local commands (doctor, init_skillsrc, the driver's `next` without a model and `status`), preflight.py and git
(ls-files, check-ignore, status).  The agent never calls operate.py.  Everything goes to <home>/operate.log (200-line
tails, secret-looking values masked).  --yes confirms the operator's questions but never the edit of the project's
.skillsrc (--yes-skillsrc) nor chmod +x of the project's files (--yes-chmod).  Python 3.11+, stdlib only.
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
TOKENS_PER_BYTE = 0.24   # DeepSeek-V4.1-Flash through gigacode, internal run 2026-10-15: 0.2391
MODEL_CONTEXT = 239_144  # its hard prompt limit, tokens
HOST_TASK_INPUT_BYTES = 700_000  # a subagent's window also holds the CLI's system prompt, tools, the role's SKILL and the answer
PROFILES = ("local-pilot-v1", "cases-only-v1")
HOST_CLI = "gigacode"
LAUNCHER = "ts.py"  # the package's launcher of next|submit|status|answer|doctor (H1)
# The package's own .skillsrc parser: the modules as the driver sees them (id, root, test).
MODULES_CODE = ("import json, sys\nfrom pathlib import Path\nfrom tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc\n"
                "try:\n    modules = normalize_skillsrc(load_skillsrc(Path(sys.argv[1])))['modules']\n"
                "except SkillsrcError as error:\n"
                "    sys.exit(f'{error.code}: {error} {json.dumps(error.details, ensure_ascii=False)[:400]}')\n"
                "print(json.dumps([{'id': m['id'], 'root': m.get('root', '.'), 'test': m.get('test') or {}} for m in modules], ensure_ascii=False))")
ASK_KNOWN_KEYS = {"action", "task_id", "question", "options", "run_id", "attempt_id", "stage", "skillsrc_question_id"}
WRAPPER_ADAPTER, SYSTEM_MAVEN = "maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1"
RUN_ID = re.compile(r"[0-9a-f]{32}")


GIT_TIMEOUT = 30


def run_git(args: list[str], cwd: Path, *, text: bool = False) -> subprocess.CompletedProcess:
    """git on the project without its hooks into this process (C9): no fsmonitor, no GIT_* from the environment, no prompt,
    no optional locks, no stdin, a timeout.  A failure to start or a timeout is an ordinary non-zero result."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"})
    argv = ["git", "-c", "core.fsmonitor=false", "-C", str(cwd), *args]
    try:
        return subprocess.run(argv, capture_output=True, stdin=subprocess.DEVNULL, env=env, timeout=GIT_TIMEOUT, check=False,
                              **({"text": True, "encoding": "utf-8", "errors": "replace"} if text else {}))
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(argv, 1, "" if text else b"", str(error) if text else str(error).encode())


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def project_files(project: Path, pattern: str = "*") -> list[Path]:
    """The project's files matching ``pattern`` (a file-name glob); symlinks and junctions are neither entered nor listed,
    so the walk never leaves the project tree."""
    found = []
    for directory, dirnames, filenames in os.walk(project):
        base = Path(directory)
        dirnames[:] = [name for name in dirnames if not _is_link(base / name)]
        found += [base / name for name in filenames if fnmatch.fnmatch(name, pattern) and not _is_link(base / name)]
    return sorted(found)


def doc_inside(project: Path, doc: str) -> Path | None:
    """The document of --docs when it lies inside the project (absolute, or relative to the project) with no symlink or
    junction on its way; None otherwise — it is never read."""
    root = Path(project).resolve()
    raw = Path(doc).expanduser()
    target = raw if raw.is_absolute() else root / raw
    try:
        relative = Path(os.path.abspath(target)).relative_to(root)
        resolved = target.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return None
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(current)):
            return None
    return resolved


def pack_python(pack: Path) -> str:
    """The package's venv interpreter if there is one (where jsonschema/pyyaml are), else the current one."""
    for candidate in (pack / ".venv" / "Scripts" / "python.exe", pack / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return str(candidate)
    return sys.executable


class Stop(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message, self.hint = message, hint


class Result:
    def __init__(self, code: int, stdout: str = "", stderr: str = "") -> None:
        self.code, self.stdout, self.stderr = code, stdout, stderr


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_tail(text: str) -> Any:
    """The JSON value of a command's stdout: the whole text, else its last JSON line."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    for line in reversed(text.strip().splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    return None


def _n(value: float) -> str:
    """A whole number with thin groups: 404 945."""
    return f"{int(value):,}".replace(",", " ")


def _q(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _main_lines(text: str, count: int = 5) -> list[str]:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return [line[:200] for line in lines[-count:]]


# ---------------------------------------------------------------- .skillsrc (line edits, no YAML library)

def _top_block(lines: list[str], key: str) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        if re.match(rf"^{re.escape(key)}\s*:", line):
            end = index + 1
            while end < len(lines) and (lines[end].startswith((" ", "\t")) or re.match(r"^-(\s|$)", lines[end]) or not lines[end].strip()):
                end += 1
            while end > index + 1 and not lines[end - 1].strip():
                end -= 1
            return index, end
    return None


def set_block(lines: list[str], key: str, block: list[str]) -> list[str]:
    """Replace the top-level ``key`` block (or append it)."""
    found = _top_block(lines, key)
    if found is None:
        return [*lines, *block]
    start, end = found
    return [*lines[:start], *block, *lines[end:]]


def remove_block(lines: list[str], key: str) -> list[str]:
    """Drop the top-level ``key`` block (nothing when there is none)."""
    found = _top_block(lines, key)
    if found is None:
        return lines
    start, end = found
    return [*lines[:start], *lines[end:]]


def _flow_children(value: str) -> list[str] | None:
    """``{a: 1, b: x}`` as block lines ``a: 1`` …; None for anything nested or quoted with commas."""
    inner = value.strip()
    if not (inner.startswith("{") and inner.endswith("}")) or any(mark in inner[1:-1] for mark in "{}[]\"'"):
        return None
    items = [item.strip() for item in inner[1:-1].split(",") if item.strip()]
    if not all(re.fullmatch(r"[A-Za-z_][\w-]*\s*:\s*\S.*", item) for item in items):
        return None
    return [re.sub(r"\s*:\s*", ": ", item, count=1) for item in items]


def set_child(lines: list[str], key: str, child: str, value: str) -> list[str]:
    """Set ``key.child`` to the YAML scalar ``value``, keeping the block's other children (a simple flow form
    ``key: {a: 1}`` becomes a block with its children kept)."""
    found = _top_block(lines, key)
    if found is None:
        return [*lines, f"{key}:", f"  {child}: {value}"]
    start, end = found
    flow = lines[start].split(":", 1)[1].strip()
    if flow:
        children = _flow_children(flow) or []
        kept = [item for item in children if not item.startswith(f"{child}:")]
        return [*lines[:start], f"{key}:", *[f"  {item}" for item in kept], f"  {child}: {value}", *lines[end:]]
    body = lines[start + 1:end]
    indent = next((re.match(r"^(\s+)", line).group(1) for line in body if line.strip()), "  ")
    for offset, line in enumerate(body):
        if re.match(rf"^{indent}{re.escape(child)}\s*:", line):
            return [*lines[:start + 1 + offset], f"{indent}{child}: {value}", *lines[start + 2 + offset:]]
    return [*lines[:start + 1], f"{indent}{child}: {value}", *lines[start + 1:]]


def get_child(lines: list[str], key: str, child: str) -> str | None:
    """The scalar ``key.child`` as written (block or simple flow form), else None."""
    found = _top_block(lines, key)
    if found is None:
        return None
    start, end = found
    flow = lines[start].split(":", 1)[1].strip()
    rows = _flow_children(flow) or [] if flow else [line.strip() for line in lines[start + 1:end]]
    for row in rows:
        match = re.match(rf"^{re.escape(child)}\s*:\s*(.*?)\s*(?:#.*)?$", row)
        if match:
            return match.group(1).strip("'\"")
    return None


def set_modules_test(lines: list[str], child: str, value: str, add: bool = True) -> tuple[list[str], list[str]]:
    """Set ``child`` in the ``test:`` block of every item of the top-level ``modules:`` list (``add`` False: only where the
    key is already there).  Returns the lines and the notes on modules it could not edit (no ``test:`` block, flow form)."""
    found = _top_block(lines, "modules")
    if found is None:
        return lines, ["в .skillsrc нет modules"]
    start, end = found
    lines, notes, index = list(lines), [], start + 1
    while index < end:
        test = re.match(r"^(\s*)(?:-\s+)?test\s*:\s*(.*)$", lines[index])
        if not test:
            index += 1
            continue
        indent = len(lines[index]) - len(lines[index].lstrip(" -")) if lines[index].lstrip().startswith("-") else len(test.group(1))
        if test.group(2).strip():
            notes.append(f"test в строке {index + 1} записан одной строкой ({test.group(2).strip()[:40]}) — {child} не добавлен")
            index += 1
            continue
        body_end = index + 1
        while body_end < end and (not lines[body_end].strip() or len(lines[body_end]) - len(lines[body_end].lstrip(" ")) > indent):
            body_end += 1
        body = [row for row in range(index + 1, body_end) if lines[row].strip()]
        child_indent = " " * (len(lines[body[0]]) - len(lines[body[0]].lstrip())) if body else " " * (indent + 2)
        existing = next((row for row in body if re.match(rf"^{child_indent}{re.escape(child)}\s*:", lines[row])), None)
        if existing is not None:
            lines[existing] = f"{child_indent}{child}: {value}"
        elif add:
            lines.insert(index + 1, f"{child_indent}{child}: {value}")
            end += 1
            body_end += 1
        index = body_end
    if not any(re.match(r"^\s*(?:-\s+)?test\s*:", line) for line in lines[start:end]):
        notes.append("ни у одного модуля нет блока test — пакет создаст его при первом прогоне; повторить init перед полным прогоном")
    return lines, notes


def set_system_maven(lines: list[str], module_ids: list[str]) -> tuple[list[str], list[str]]:
    """In the listed modules' ``test:`` blocks: ``adapter_id: maven-wrapper:…`` → ``maven:selected-symbols-v1`` and
    ``wrapper: …`` → ``executable: mvn`` (system Maven from PATH).  Returns the lines and the ids it edited."""
    found = _top_block(lines, "modules")
    if found is None:
        return lines, []
    start, end = found
    lines, edited = list(lines), []
    dash = next((len(line) - len(line.lstrip(" ")) for line in lines[start + 1:end] if re.match(r"^\s*-(\s|$)", line)), None)
    if dash is None:
        return lines, []
    heads = [index for index in range(start + 1, end) if re.match(rf"^ {{{dash}}}-(\s|$)", lines[index])]
    for head, tail in zip(heads, [*heads[1:], end]):
        ids = [match.group(1) for line in lines[head:tail] if (match := re.match(r"^\s*(?:-\s+)?id\s*:\s*['\"]?([^'\"\s#]+)", line))]
        if not ids or ids[0] not in module_ids:
            continue
        for index in range(head, tail):
            adapter = re.match(rf"^(\s*)adapter_id\s*:\s*['\"]?{re.escape(WRAPPER_ADAPTER)}['\"]?\s*$", lines[index])
            if adapter:
                lines[index] = f"{adapter.group(1)}adapter_id: {SYSTEM_MAVEN}"
                edited.append(ids[0])
            wrapper = re.match(r"^(\s*)wrapper\s*:", lines[index])
            if wrapper:
                lines[index] = f"{wrapper.group(1)}executable: mvn"
    return lines, list(dict.fromkeys(edited))


def drop_modules(lines: list[str], module_ids: list[str]) -> tuple[list[str], list[str]]:
    """Remove the listed items of the top-level ``modules:`` list.  Returns the lines and the ids it removed."""
    found = _top_block(lines, "modules")
    if found is None:
        return lines, []
    start, end = found
    dash = next((len(line) - len(line.lstrip(" ")) for line in lines[start + 1:end] if re.match(r"^\s*-(\s|$)", line)), None)
    if dash is None:
        return lines, []
    heads = [index for index in range(start + 1, end) if re.match(rf"^ {{{dash}}}-(\s|$)", lines[index])]
    keep, removed = list(lines[:start + 1]), []
    keep += lines[start + 1:heads[0]] if heads else lines[start + 1:end]
    for head, tail in zip(heads, [*heads[1:], end]):
        ids = [match.group(1) for line in lines[head:tail] if (match := re.match(r"^\s*(?:-\s+)?id\s*:\s*['\"]?([^'\"\s#]+)", line))]
        if ids and ids[0] in module_ids:
            removed.append(ids[0])
            continue
        keep += lines[head:tail]
    return [*keep, *lines[end:]], removed


_ID_LINE = re.compile(r"^[ \t>*#-]*(?:\*\*)?([A-Za-zА-Яа-яЁё]{1,12})([-_]?)(\d{1,4})((?:[.-]\d{1,4})*)(\.?)(?=[\s*:)\]])", re.M)


def derive_id_pattern(texts: list[str]) -> tuple[str | None, list[str]]:
    """A requirement ID pattern from IDs that open lines of the specification (O01., REQ-12, AC-3.1 …)."""
    found = [match.groups() for text in texts for match in _ID_LINE.finditer(text)]
    if len(found) < 3:
        return None, []
    shapes: dict[tuple, list[tuple]] = {}
    for letters, sep, digits, rest, dot in found:
        shape = (len(letters) == 1, sep, len(digits), re.sub(r"\d+", "N", rest), dot)
        shapes.setdefault(shape, []).append((letters, sep, digits, rest, dot))
    shape, rows = max(shapes.items(), key=lambda item: len(item[1]))
    if len(rows) < 3:
        return None, []
    prefixes = sorted({row[0] for row in rows})
    head = ("[" + "".join(prefixes) + "]" if len(prefixes) > 1 else re.escape(prefixes[0])) if shape[0] else \
        ("(?:" + "|".join(re.escape(item) for item in prefixes) + ")" if len(prefixes) > 1 else re.escape(prefixes[0]))
    width = shape[2]
    digits = "\\d" * width if width <= 3 else f"\\d{{{width}}}"
    rest = re.sub(r"N", r"\\d+", re.sub(r"([.-])", r"\\\1", shape[3]))
    pattern = head + re.escape(shape[1]) + digits + rest + ("\\." if shape[4] else "")
    examples = list(dict.fromkeys("".join(row) for row in rows))
    return pattern, examples


# ---------------------------------------------------------------- the operator

class Operator:
    """What init and report share: the kit's place, the package and its interpreter, the log and the questions."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dry = args.dry_run
        self.home = Path(args.home).expanduser().resolve()
        self.pack = self.home / "test-skills"
        self.py = pack_python(self.pack)
        self.report = self.home / "report"
        self.project = Path(args.project).expanduser().resolve()
        self.in_project = self.home.is_relative_to(self.project) and self.home != self.project
        self.home.mkdir(parents=True, exist_ok=True)
        self.log_file = (self.home / "operate.log").open("a", encoding="utf-8")
        self.state_path = self.home / "operate-state.json"
        try:
            self.state: dict[str, Any] = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

    # -- output
    def log(self, text: str) -> None:
        self.log_file.write(text.rstrip("\n") + "\n")
        self.log_file.flush()

    def say(self, text: str = "") -> None:
        print(text, flush=True)
        self.log(text)

    def action(self, text: str) -> None:
        self.say(("• (dry-run) " if self.dry else "• ") + text)

    def save_state(self) -> None:
        if not self.dry:
            self.state_path.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")

    def confirm(self, question: str, flag: str = "yes") -> bool:
        """y/n; ``flag`` is the option that answers it: --yes for the operator's own questions, --yes-skillsrc and
        --yes-chmod for changes to the project's files (--yes does not answer those)."""
        if self.dry or getattr(self.args, flag.replace("-", "_"), False):
            self.say(f"{question} → да ({'dry-run' if self.dry else '--' + flag})")
            return True
        try:
            answer = input(f"{question} [y/n] ").strip().lower()
        except EOFError:
            answer = ""
        self.log(f"{question} [y/n] {answer}")
        return answer in {"y", "yes", "д", "да"}

    def ask(self, question: str) -> str:
        try:
            answer = input(question).strip()
        except EOFError:  # no terminal: the caller stops with the command to continue
            answer = ""
        self.log(f"{question}{answer}")
        return answer

    # -- commands
    def run(self, argv: list[Any], *, cwd: Path | None = None, timeout: int | None = None) -> Result:
        argv = [str(item) for item in argv]
        self.say(f"$ {shlex.join(argv)}" + (f"   (в {cwd})" if cwd else ""))
        if self.dry:
            return Result(0)
        self.log(f"--- запуск {datetime.datetime.now().isoformat(timespec='seconds')}")
        try:
            completed = subprocess.run(argv, cwd=cwd, capture_output=True, env=self.env, text=True, encoding="utf-8",
                                       errors="replace", timeout=timeout, check=False)
        except FileNotFoundError as error:
            raise Stop(f"команда не запустилась: {error}", "проверить путь к программе и PATH")
        except subprocess.TimeoutExpired:
            raise Stop(f"команда не уложилась в {timeout} с", "смотреть operate.log")
        done = Result(completed.returncode, completed.stdout, completed.stderr)
        self.log("stdout:\n" + log_tail(done.stdout) + "\nstderr:\n" + log_tail(done.stderr))
        self.say(f"  код выхода: {done.code}")
        return done

    def pack_json(self, code: str, *args: Any) -> Any:
        """A read-only question to the package (its own parser) under its interpreter; runs in --dry-run too."""
        argv = [self.py, "-c", code, *[str(item) for item in args]]
        self.log(f"$ {shlex.join(argv)}   (в {self.pack})")
        try:
            done = subprocess.run(argv, cwd=self.pack, capture_output=True, env=self.env, text=True, encoding="utf-8", errors="replace",
                                  timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Stop(f"пакет не ответил: {error}", "сначала проверка комплекта (init, шаг 0)")
        self.log("stdout:\n" + log_tail(done.stdout) + "\nstderr:\n" + log_tail(done.stderr))
        if done.returncode != 0:
            raise Stop(f"пакет не ответил (код {done.returncode}): {' | '.join(_main_lines(done.stderr, 2))}", "ошибку — в отчёт (RUN.md, «Что принести»)")
        return json.loads(done.stdout)

    def driver(self, argv: list[str], label: str, timeout: int = 900) -> dict[str, Any]:
        """One local driver command (``next`` without a model, ``status``); its JSON answer.  Nothing is launched by it."""
        self.say(f"$ {shlex.join(argv)}   (в {self.pack})")
        if self.dry:
            return {}
        out, err = self.home / f"{label}.stdout.json", self.home / f"{label}.stderr.txt"
        started = time.time()
        try:
            with out.open("wb") as stdout, err.open("wb") as stderr:
                code = subprocess.run(argv, cwd=self.pack, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, env=self.env,
                                      timeout=timeout, check=False).returncode
        except subprocess.TimeoutExpired:
            raise Stop(f"драйвер не ответил за {timeout} с", f"смотреть {err}")
        text = out.read_text(encoding="utf-8", errors="replace")
        self.log(f"stdout ({out}):\n{log_tail(text)}\nstderr:\n{log_tail(err.read_text(encoding='utf-8', errors='replace'))}")
        self.say(f"  код выхода: {code}; {int(time.time() - started)} с")
        payload = _json_tail(text)
        if not isinstance(payload, dict):
            raised = re.findall(r"DriverError: ([A-Z_]+): (.*)$", err.read_text(encoding="utf-8", errors="replace"), re.M)
            if raised:
                raise Stop(f"драйвер: {raised[-1][0]}: {raised[-1][1].strip()[:400]}", f"смотреть {err}; код и message — в отчёт")
            tail = _main_lines(err.read_text(encoding="utf-8", errors="replace"), 3)
            raise Stop(f"драйвер не вернул JSON (код {code}): {' | '.join(tail)}", f"смотреть {err}; текст ошибки — в отчёт")
        return payload


# ---------------------------------------------------------------- init: before the agent's session

TEXT_SUFFIXES = {".java", ".kt", ".kts", ".groovy", ".scala", ".py", ".js", ".ts", ".tsx", ".jsx", ".xml", ".yml", ".yaml", ".properties",
                 ".json", ".sql", ".html", ".css", ".md", ".txt", ".csv", ".gradle", ".conf", ".jsp", ".ftl", ".vm"}
CODE_SUFFIXES = {".java", ".kt", ".kts", ".groovy", ".scala", ".py", ".js", ".ts", ".tsx", ".jsx"}
GENERIC_DIRS = {"src", "main", "java", "kotlin", "scala", "groovy", "test", "tests", "resources", "org", "com", "net", "io", "app", "springframework",
                "samples", "static", "templates", "python", "source", "sources", "spec", "docs", "requirements", "файл", "файлы", "требования"}
SKIP_DIRS = {".git", ".pilot-runs", "target", "build", "node_modules", ".gradle", ".idea", ".vscode", ".mvn", "__pycache__"}


class Init(Operator):
    """``operate.py init``: the kit and the project prepared once, then the first message for the agent.

    Writes to the project only .skillsrc (after a diff and a confirmation) and chmod +x of mvnw/gradlew (after a
    confirmation); the measuring run it starts (``next``, no model) is removed by its id.  Every problem is one line
    «что делать» with a ready command; the first blocking one stops."""

    def __init__(self, args: argparse.Namespace, init_argv: list[str]) -> None:
        super().__init__(args)
        self.init_argv = init_argv
        self.started = time.time()
        self.decisions: list[str] = []
        self.later: list[str] = []
        self.facts: dict[str, Any] = {"profile": args.profile}
        self.module: dict[str, Any] = {"id": args.module or "root", "root": "."}
        self.mutation = False

    def init_command(self, **changes: str | None) -> str:
        """``operate.py init`` with this call's arguments, ``changes`` replacing (or, None, dropping) options."""
        argv, items = [], iter(self.init_argv)
        for item in items:
            key = item.split("=", 1)[0]
            if key[2:].replace("-", "_") in changes:
                if "=" not in item:
                    next(items, None)
                continue
            argv.append(item)
        for key, value in changes.items():
            if value is not None:
                argv += [f"--{key.replace('_', '-')}", value]
        return shlex.join([sys.executable, str(Path(sys.argv[0]).resolve()), "init", *argv])

    def run_init(self) -> int:
        self.log(f"\n===== operate.py {datetime.datetime.now().isoformat(timespec='seconds')} init {shlex.join(self.init_argv)}")
        self.say(f"init: проект {self.project}, спека {', '.join(self.args.docs)}, тема: {self.args.subject}, профиль {self.args.profile}")
        for title, part in (("место комплекта", self.init_home), ("комплект и интерпретатор (шаг 0)", self.step_0),
                            ("проверки проекта (шаг 1, preflight)", self.step_1), (".skillsrc проекта", self.ensure_skillsrc),
                            ("модуль и сборка", self.init_module), ("цель контекста (--target)", self.init_target),
                            (".skillsrc проекта (шаг 4)", self.step_4), ("размер первой задачи", self.init_measure)):
            self.say(f"\n=== init: {title} ===")
            try:
                part()
            except Stop as stop:
                self.say(f"СТОП на шаге init: {stop.message}")
                self.say(f"Что делать: {stop.hint or 'исправить сказанное и повторить: ' + self.init_command()}")
                self.save_init()
                return 1
        self.finish()
        return 0

    def save_init(self) -> None:
        self.facts.update({"decisions": self.decisions, "later": self.later, "seconds": int(time.time() - self.started)})
        if not self.dry:
            (self.home / "init.json").write_text(json.dumps(self.facts, ensure_ascii=False, indent=1), encoding="utf-8")

    def _docs_args(self) -> list[str]:
        return [item for doc in self.args.docs for item in ("--docs", doc)]

    # -- the kit's place: inside the project (nothing is written outside it), untracked and ignored by git
    def init_home(self) -> None:
        if not self.in_project:
            raise Stop(f"комплект {self.home} лежит вне проекта {self.project}: писать вне проекта нельзя",
                       f"скопировать комплект в {self.project / '.gigacode' / 'skills' / 'test-skills'} и запустить init оттуда")
        relative = self.home.relative_to(self.project).as_posix()
        self.say(f"  комплект в проекте: {relative}")
        tracked = run_git(["ls-files", "--", relative], self.project, text=True)
        if tracked.returncode == 0 and tracked.stdout.strip():
            raise Stop(f"файлы комплекта отслеживаются git ({len(tracked.stdout.splitlines())}): {relative} — они попали бы в коммит и в "
                       "инвентарь пакета", f"на ветке прогона: git rm -r --cached {shlex.quote(relative)} (файлы остаются на диске) и повторить init")
        ignored = run_git(["check-ignore", "-q", relative + "/"], self.project, text=True)
        if ignored.returncode == 1:  # 0 — ignored, 128 — no git repository (the inventory then has no git list either)
            raise Stop(f"{relative}/ не исключён в git: инвентарь пакета (git ls-files с .gitignore) взял бы файлы комплекта в контекст модели",
                       f"добавить строку /{relative}/ в {self.project / '.git' / 'info' / 'exclude'} (или в .gitignore ветки прогона) и повторить init")
        if ignored.returncode not in (0, 1):
            self.say("  git-репозитория нет — исключение комплекта проверит список файлов замера")

    # -- step 0
    def step_0(self) -> None:
        if not self.pack.is_dir():
            raise Stop(f"в {self.home} нет папки test-skills", f"скопировать комплект целиком (папку kit/ из ветки) в {self.home}")
        listings = sorted(self.home.glob("test-skills-*.files.sha256"), key=lambda path: path.stat().st_mtime)
        if not listings:
            raise Stop(f"в {self.home} нет test-skills-*.files.sha256", "скопировать комплект целиком: без списка сумм пакет не проверить")
        files = listings[-1]
        if len(listings) > 1:
            self.say(f"  внимание: списков сумм несколько, беру самый новый: {files.name}")
        self.action(f"сверить файлы {self.pack} по {files.name}")
        if not self.dry:
            rows = [line.split(None, 1) for line in files.read_text(encoding="utf-8").splitlines() if line.strip()]
            listed = {name.lstrip("*").strip() for _digest, name in rows}
            bad = [name for digest, name in ((digest, name.lstrip("*").strip()) for digest, name in rows)
                   if not (self.home / name).is_file() or _sha256(self.home / name) != digest]
            extra = sorted(path.relative_to(self.home).as_posix() for path in self.pack.rglob("*")
                           if path.is_file() and "__pycache__" not in path.parts and ".venv" not in path.parts
                           and path.relative_to(self.home).as_posix() not in listed)
            if bad:
                raise Stop(f"файлы пакета отличаются от {files.name} ({len(bad)}): {', '.join(bad[:5])}",
                           f"удалить {self.pack} и скопировать папку test-skills из комплекта заново")
            code = [name for name in extra if name.endswith(".py")]
            if code:
                raise Stop(f"в пакете лишние .py, которых нет в {files.name} ({len(code)}): {', '.join(code[:5])}",
                           f"удалить {self.pack} и скопировать папку test-skills из комплекта заново")
            self.say(f"  файлы пакета: {len(rows)} совпадают" + (f"; лишних файлов {len(extra)}: {', '.join(extra[:5])}" if extra else ""))
        if not (HERE / "preflight.py").is_file():
            raise Stop("рядом с operate.py нет preflight.py", f"скопировать комплект целиком в одну папку ({HERE})")
        if sys.version_info < (3, 11):
            raise Stop(f"Python {sys.version.split()[0]} < 3.11", "запускать operate.py через python3.11 или новее")
        self.say(f"  Python {sys.version.split()[0]}: ок; интерпретатор пакета: {self.py}" + ("" if self.py != sys.executable else
                 f" (venv {self.pack / '.venv'} нет — текущий)"))
        modules = self.run([self.py, "-c", "import sys, jsonschema, yaml; sys.exit(sys.version_info < (3, 11))"])
        if modules.code != 0:
            requirements = self.pack / "requirements.txt"
            install = shlex.join([self.py, "-m", "pip", "install", "--require-hashes", "-r", str(requirements)])
            raise Stop(f"у интерпретатора {self.py} нет jsonschema/pyyaml (или он старше 3.11) — запустите через интерпретатор пакета "
                       f"({self.pack / '.venv'}) или поставьте зависимости: "
                       + ("пакетами ОС python3-jsonschema и python3-yaml (--no-pip)" if self.args.no_pip else install),
                       "operate.py сам ничего не ставит; после установки — повторить init")
        doctor = self.run([self.py, "-m", "tools.doctor", "--root", "."], cwd=self.pack)
        if self.dry:
            return
        report = _json_tail(doctor.stdout) or {}
        self.say(f"  doctor: {report.get('status')}")
        if doctor.code != 0 or report.get("status") != "PASS":
            raise Stop(f"doctor пакета: {report.get('status') or 'нет JSON'} (код {doctor.code})", "скопировать test-skills из комплекта заново и повторить init; ошибку — в отчёт")

    # -- step 1
    def step_1(self, again: bool = False) -> None:
        if not os.environ.get("JAVA_HOME"):
            self.say("  внимание: JAVA_HOME не задан (export JAVA_HOME=<JDK 17+>)")
        report = self.home / "preflight.json"
        inside = [self.home.relative_to(self.project).as_posix()]
        inside += [HERE.relative_to(self.project).as_posix()] if HERE.is_relative_to(self.project) and not HERE.is_relative_to(self.home) else []
        done = self.run([self.py, HERE / "preflight.py", "--pack", self.pack, "--project", self.project, *self._docs_args(),
                         *[item for path in inside for item in ("--exclude-dir", path)], "--json", report], timeout=900)
        if self.dry:
            self.say("  (при «стоп» только из-за неисполняемого mvnw/gradlew — chmod +x с подтверждением и повтор preflight)")
            self.mutation = not self.args.no_mutation
            return
        try:
            rows = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Stop(f"preflight не записал {report.name} (код {done.code})", "смотреть operate.log: " + " ".join(_main_lines(done.stderr or done.stdout, 2)))
        stops = [row for row in rows if row["status"] == "стоп"]
        warns = [row for row in rows if row["status"] == "внимание"]
        for line in _main_lines(done.stdout, 1):
            self.say("  " + line)
        sensitive = [row for row in [*stops, *warns] if row["check"].startswith("ПДн/секреты")]
        for row in [*[row for row in [*stops, *warns] if row not in sensitive][:8], *sensitive]:
            self.say(f"  [{row['status']}] {row['check']}: {row['finding']}" + (f" → {row['hint']}" if row["hint"] else ""))
        if sensitive:
            self.say("  файлы выше уходят в модель вместе с кодом модуля: значения должны быть тестовыми, иначе — исключить до прогона")
            self.decisions.append("отправка в модель файлов с ПДн/секретами из списка preflight (или исключить их: .gitignore / paths модуля)")
        if stops:
            fixable = [re.match(r"chmod \+x (.+?)(?:\s{2}|$)", row["hint"]) for row in stops]
            if all(fixable) and not again:
                paths = [Path(match.group(1)) for match in fixable]
                if self.confirm("Сделать исполняемыми файлы проекта " + ", ".join(str(path) for path in paths) + "?", "yes-chmod"):
                    for path in paths:
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        self.say(f"  chmod +x {path}")
                    return self.step_1(again=True)
            blocking = [row for row in stops if not row["check"].startswith("ПДн/секреты")]
            if blocking and self.args.profile == "cases-only-v1":  # nothing is built in cases-only: the stops wait for the full run
                self.later += [f"{row['check']}: {row['finding'][:120]}" for row in blocking]
                self.say(f"  «стоп» {len(blocking)} — для полного прогона; профиль cases-only-v1 сборку не запускает")
            elif blocking:
                raise Stop(f"preflight: «стоп» {len(blocking)}", "исправить по подсказкам выше (линтер — строка excludes из подсказки, на ветке прогона) "
                           "и повторить: " + self.init_command())
        pit_missing = [row for row in warns if "--mutation" in row["hint"]]
        self.mutation = not self.args.no_mutation and not pit_missing
        if pit_missing and not self.args.no_mutation:
            self.facts["mutation_off"] = "в ~/.m2 нет " + ", ".join(row["check"].split(": ", 1)[-1] for row in pit_missing)
            self.say(f"  мутации выключены: {self.facts['mutation_off']}")
        self.say(f"  мутации: {'да' if self.mutation else 'нет'}")

    # -- the project's .skillsrc: the package's own proposal when there is none
    def skillsrc_modules(self) -> list[dict[str, Any]] | None:
        """The modules of the project's .skillsrc by the package's parser; None without .skillsrc."""
        skillsrc = self.project / ".skillsrc"
        if not skillsrc.is_file():
            return None
        try:
            return self.pack_json(MODULES_CODE, skillsrc)
        except Stop as stop:
            raise Stop(f".skillsrc проекта не читается пакетом: {stop.message}", "исправить .skillsrc (ошибка выше) и повторить init")

    def ensure_skillsrc(self) -> None:
        if (self.project / ".skillsrc").is_file():
            self.say("  .skillsrc есть")
            return
        argv = [self.py, "-m", "tools.init_skillsrc", "--project", str(self.project)]
        preview = self.run(argv, cwd=self.pack, timeout=300)
        if self.dry:
            self.say("  (.skillsrc нет: пакет предложит его — показ модулей, подтверждение --yes-skillsrc, запись init_skillsrc --write)")
            return
        receipt = _json_tail(preview.stdout) or {}
        (self.home / "init-skillsrc.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
        status = receipt.get("status")
        if status == "needs_input":
            questions = [str(item.get("id") or item.get("field")) for item in receipt.get("questions") or [] if isinstance(item, dict)]
            raise Stop(f"у пакета вопросы для .skillsrc: {', '.join(questions)} (варианты — {self.home / 'init-skillsrc.json'})",
                       "ответы — файлом JSON {вопрос: значение}: " + shlex.join([*argv, "--answers", "<answers.json>", "--write"])
                       + f" (в {self.pack}); затем повторить init")
        if status != "preview":
            raise Stop(f"пакет не предложил .skillsrc: {status} {receipt.get('errors') or ''}", f"смотреть {self.home / 'init-skillsrc.json'}")
        modules = receipt.get("modules") or []
        self.say("  .skillsrc нет — пакет предлагает модули: " + (", ".join(f"{item.get('id')} ({item.get('root', '.')})" for item in modules
                                                                     if isinstance(item, dict)) or "—"))
        if not self.confirm(f"Создать {self.project / '.skillsrc'} по предложению пакета (дальше — правки шага 4 с diff)?", "yes-skillsrc"):
            raise Stop(".skillsrc не создан", self.init_command() + " --yes-skillsrc")
        written = self.run([*argv, "--write"], cwd=self.pack, timeout=300)
        if (_json_tail(written.stdout) or {}).get("status") != "created":
            raise Stop(f"пакет не записал .skillsrc (код {written.code})", "смотреть operate.log")
        self.facts["skillsrc"] = "создан init"

    # -- module and build
    def init_module(self) -> None:
        modules = self.skillsrc_modules() or [{"id": self.args.module or "root", "root": ".", "test": {}}]  # none: dry-run only
        kit = [module for module in modules if self.kit_module(module)]
        if kit:  # the package's discovery walks the disk: the kit folder itself looks like a Python module to it
            self.say(f"  модуль {', '.join(module['id'] for module in kit)} — это папка комплекта, не код проекта: шаг 4 уберёт его из .skillsrc")
            modules = [module for module in modules if module not in kit] or modules
        ids = [module["id"] for module in modules]
        if self.args.module:
            if self.args.module not in ids:
                raise Stop(f"модуля {self.args.module} нет; есть: {', '.join(ids)}", self.init_command(module="<id из списка>"))
            module = next(item for item in modules if item["id"] == self.args.module)
        elif len(modules) == 1:
            module = modules[0]
        else:
            docs = [(Path(doc) if Path(doc).is_absolute() else self.project / doc).resolve() for doc in self.args.docs]
            owning = [item for item in modules if item["root"] not in (".", "") and
                      any(doc.is_relative_to((self.project / item["root"]).resolve()) for doc in docs)]
            if len(owning) != 1:
                raise Stop("модулей несколько, спека ни в одном из них: " + ", ".join(f"{item['id']} ({item['root']})" for item in modules),
                           self.init_command(module="<id из списка>"))
            module = owning[0]
            self.say(f"  модуль по месту спеки: {module['id']}")
        self.args.module = module["id"] if len(modules) > 1 or kit else self.args.module
        self.module = module
        root = self.project / str(module.get("root") or ".")
        wrapper = next((name for name in ("mvnw", "gradlew") if (root / name).is_file() or (self.project / name).is_file()), None)
        build = wrapper or ("mvn в PATH" if shutil.which("mvn") else None)
        self.say(f"  модуль {module['id']} ({module.get('root') or '.'}); сборка: {build or 'не найдена'}")
        if not build:
            self.later.append("сборка: нет mvnw/gradlew и mvn в PATH — нужна, когда агент дойдёт до сборки (human_exec)")
        m2 = Path.home() / ".m2" / "repository"
        if not m2.is_dir():
            self.later.append(f"нет {m2}: сборка пойдёт online (--online) или собрать проект один раз с доступом к репозиторию")
        self.facts.update({"module": module["id"], "module_root": module.get("root") or ".", "build": build})

    def kit_module(self, module: dict[str, Any]) -> bool:
        root = (self.project / str(module.get("root") or ".")).resolve()
        return root == self.home or self.home in root.parents

    # -- target
    def _module_files(self) -> list[tuple[str, int]]:
        """(path relative to the module root, bytes) of the module's text files — git's list when there is one."""
        root = self.project / str(self.module.get("root") or ".")
        listed = run_git(["ls-files", "-co", "--exclude-standard", "-z"], root)
        names = [name for name in listed.stdout.decode("utf-8", "replace").split("\0") if name] if listed.returncode == 0 else \
            [path.relative_to(root).as_posix() for path in project_files(root) if path.is_file()]
        rows = []
        for name in names:
            parts = name.split("/")
            if any(part in SKIP_DIRS for part in parts[:-1]) or Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            path = root / name
            if self.home in path.resolve().parents or not path.is_file():
                continue
            size = path.stat().st_size
            if size < 1_000_000:
                rows.append((name, size))
        return rows

    def task_input_bytes(self) -> int:
        """The host's task input budget: --task-input-bytes, else 700 000 B or 0.8 of the window in bytes, the smaller."""
        return int(self.args.task_input_bytes or min(HOST_TASK_INPUT_BYTES, self.args.model_context / self.args.tokens_per_byte * 0.8))

    def init_target(self) -> None:
        budget = self.task_input_bytes()
        if self.args.target:
            self.say(f"  --target {self.args.target} (задан)")
            self.facts["target"] = self.args.target
            return
        paths = self.module.get("paths") or {}
        sources = [str(item).rstrip("/") for item in (paths.get("source") or ["src/main", "src"])]
        tests = [str(item).rstrip("/") for item in (paths.get("tests") or ["src/test"])]
        inside = lambda name, roots: any(name == root or name.startswith(root + "/") for root in roots)  # noqa: E731
        files = [(name, size) for name, size in self._module_files()
                 if inside(name, [*sources, *(paths.get("resources") or [])]) and not inside(name, tests)]
        total = sum(size for _name, size in files)
        self.say(f"  текстовых файлов модуля {len(files)}: {_n(total)} Б ≈ {_n(total * self.args.tokens_per_byte)} токенов; порог задачи {_n(budget)} Б")
        if total <= budget:
            self.say("  весь модуль влезает — --target не нужен")
            return
        sizes: dict[str, int] = {}
        resources = [str(item).rstrip("/") for item in (paths.get("resources") or ["src/main/resources"])]
        code: set[str] = set()  # a --target must hold code; its size is everything under it (what the scan would take)
        for name, size in files:
            parts = name.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                key = "/".join(parts[:depth])
                sizes[key] = sizes.get(key, 0) + size
                if Path(name).suffix.lower() in CODE_SUFFIXES:
                    code.add(key)
        limit = int(budget * 0.75)
        fitting = [key for key, size in sizes.items() if key in code and inside(key, sources) and not inside(key, resources) and size <= limit
                   and sizes.get(key.rsplit("/", 1)[0] if "/" in key else "", limit + 1) > limit]
        words = set()
        for doc in self.args.docs:
            path = Path(doc) if Path(doc).is_absolute() else self.project / doc
            text = (path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "") + " " + doc
            words |= {word.lower() for word in re.findall(r"[A-Za-zА-Яа-яЁё]{4,}", text)}
        words -= GENERIC_DIRS

        def score(key: str) -> int:
            parts = [part for part in key.lower().split("/")[-2:] if part not in GENERIC_DIRS and len(part) >= 3]
            return sum(1 for part in parts for word in words if part.startswith(word[:5]) or word.startswith(part[:5]))
        ranked = sorted(fitting, key=lambda key: (-score(key), -sizes[key]))[:5]
        if not ranked:
            raise Stop(f"модуль {total:,} Б больше порога задачи, а каталога исходников меньше {limit:,} Б не нашлось".replace(",", " "),
                       self.init_command(target="<каталог фичи внутри модуля>"))
        best = [key for key in ranked if score(key) == score(ranked[0])]
        if score(ranked[0]) > 0 and len(best) == 1:
            self.args.target = ranked[0]
            self.say(f"  --target угадан по словам спеки: {ranked[0]} ({sizes[ranked[0]]:,} Б); другой — {self.init_command(target='<каталог>')}"
                     .replace(",", " "))
            self.facts["target"] = self.args.target
            return
        for key in ranked:
            self.say(f"    {key}  ({sizes[key]:,} Б ≈ {int(sizes[key] * self.args.tokens_per_byte):,} токенов)".replace(",", " "))
        self.decisions.append("--target")
        raise Stop(f"модуль ({total:,} Б) не влезет в задачу целиком — выберите каталог фичи из списка выше".replace(",", " "),
                   self.init_command(target=ranked[0]))

    # -- step 4
    def _id_pattern(self) -> str | None:
        if self.args.id_pattern:
            pattern, source = self.args.id_pattern, "--id-pattern"
            examples: list[str] = []
        else:
            texts = []
            for doc in self.args.docs:
                path = Path(doc) if Path(doc).is_absolute() else self.project / doc
                if self.dry and not path.is_file():
                    continue
                try:
                    texts.append(path.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    raise Stop(f"спека не читается: {path}", "--docs — путь к спеке внутри проекта")
            pattern, examples = derive_id_pattern(texts)
            source = "выведен из спеки"
        if pattern:
            self.say(f"  шаблон ID требований ({source}): {pattern}" + (f"; найдено {len(examples)}: {', '.join(examples[:6])}" if examples else ""))
            if self.confirm("Записать этот шаблон в requirements.id_pattern?"):
                return self._checked(pattern)
        else:
            self.say("  шаблон ID в спеке не найден (строки, начинающиеся с ID вида O01. / REQ-12)")
        if self.dry or self.args.yes:
            return None
        line = self.ask("Свой шаблон (регулярное выражение ID в начале строки), пусто — без id_pattern: ")
        return self._checked(line) if line else None

    def _checked(self, pattern: str) -> str:
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise Stop(f"шаблон ID не компилируется: {error}", self.init_command(id_pattern="<регулярное выражение>"))
        if compiled.match(""):
            raise Stop("шаблон ID совпадает с пустой строкой", "шаблон должен описывать сам ID, например [OPVX]\\d\\d\\.: "
                       + self.init_command(id_pattern="<регулярное выражение>"))
        return pattern

    def step_4(self) -> None:
        """The project's .skillsrc for the host: id_pattern, mutation, offline builds, the system Maven where there is no
        wrapper, the host's task budget and execution by the person; review_runner goes (no process runner in this build)."""
        skillsrc = self.project / ".skillsrc"
        before = skillsrc.read_text(encoding="utf-8") if skillsrc.is_file() else ""
        if not before and not self.dry:
            raise Stop(f"нет {skillsrc}", "повторить init (пакет предложит .skillsrc)")
        pattern = self._id_pattern()
        lines = before.splitlines()
        if _top_block(lines, "review_runner") is not None:
            lines = remove_block(lines, "review_runner")
            self.say("  review_runner убирается: в хост-сборке процессного раннера нет (с ним драйвер ответит RUNNER_UNAVAILABLE)")
        kit = [module["id"] for module in (self.skillsrc_modules() if skillsrc.is_file() else None) or [] if self.kit_module(module)]
        if kit:
            lines, removed = drop_modules(lines, kit)
            self.say(f"  модуль папки комплекта убирается: {', '.join(removed or kit)}" + ("" if removed else " (не удалось найти строки — поправить руками)"))
        if pattern:
            lines = set_child(lines, "requirements", "id_pattern", _q(pattern))
        if self.mutation:
            if _top_block(lines, "mutation") is None:
                lines = [*lines, "mutation:", "  enabled: true", "  threads: 2", "  timeout_seconds: 3600"]
            else:
                lines = set_child(lines, "mutation", "enabled", "true")
        # S2a: builds only from the local repository (Maven -o / Gradle --offline); --online leaves the network to the build
        lines, notes = set_modules_test(lines, "offline", "false" if self.args.online else "true", add=not self.args.online)
        for note in notes:
            self.say(f"  внимание: test.offline: {note}")
        self.say("  сборка: " + ("онлайн (--online)" if self.args.online else
                                 "offline — только ~/.m2; нет артефакта → BUILD_FAILED_BEFORE_TESTS / MUTATION_TOOL_MISSING"))
        lines = set_child(lines, "limits", "task_input_bytes", str(self.task_input_bytes()))
        lines = set_child(lines, "execution", "mode", "human")
        # preflight «mvnw нет, есть mvn в PATH»: the wrapper adapter cannot run there — offer the system Maven adapter
        modules = self.skillsrc_modules() if skillsrc.is_file() else None
        if modules and shutil.which("mvn"):
            no_wrapper = [module["id"] for module in modules if module["test"].get("adapter_id") == WRAPPER_ADAPTER and not any(
                (base / str(module["test"].get("wrapper") or "mvnw")).is_file() for base in (self.project / module["root"], self.project))]
            if no_wrapper:
                lines, edited = set_system_maven(lines, no_wrapper)
                self.say(f"  обёртки Maven нет, mvn есть в PATH ({shutil.which('mvn')}): модули {', '.join(edited or no_wrapper)} — "
                         f"adapter_id {SYSTEM_MAVEN}, executable: mvn" + ("" if edited else " (не удалось найти строки — поправить руками)"))
        self.facts["mutation"] = get_child(lines, "mutation", "enabled") == "true" and self.mutation
        after = "\n".join(lines) + "\n"
        if after == before:
            self.say("  .skillsrc уже такой — без изменений")
            return
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), ".skillsrc", ".skillsrc (новый)", lineterm="", n=1):
            self.say("  " + line)
        if not self.confirm(f"Записать {skillsrc}?", "yes-skillsrc"):
            raise Stop(".skillsrc не записан (diff выше): без терминала — подтверждение флагом", self.init_command() + " --yes-skillsrc")
        self.action(f"копия прежнего .skillsrc → {self.home}/skillsrc.bak-<время>; запись {skillsrc}")
        if not self.dry:
            shutil.copy2(skillsrc, self.home / f"skillsrc.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
            skillsrc.write_text(after, encoding="utf-8")

    # -- the first task, measured without a model; confirm-context is left to the person in the agent's session
    def _git_status(self) -> str:
        done = run_git(["status", "--porcelain", "--untracked-files=all"], self.project, text=True)
        return done.stdout if done.returncode == 0 else ""

    def _runs(self) -> list[str]:
        runs = self.project / ".pilot-runs"
        return sorted(path.name for path in runs.iterdir()) if runs.is_dir() else []

    def measure_argv(self) -> list[str]:
        return [self.py, "-m", "tools.pipeline_driver", "next", "--project", str(self.project), "--profile", "cases-only-v1",
                *self._docs_args(), "--subject", self.args.subject, *(["--module", self.args.module] if self.args.module else []),
                *(["--target", self.args.target] if self.args.target else []), "--host-cli", self.args.host_cli,
                "--reviewer-isolation", "fresh", "--task-input-bytes", str(self.task_input_bytes())]

    def init_measure(self) -> None:
        before, runs_before = self._git_status(), self._runs()
        payload = self.driver(self.measure_argv(), "init-measure")
        if self.dry:
            self.say(f"  (confirm-context: документы и файлы с размерами против порога {_n(self.task_input_bytes())} Б — больше → стоп с --target; "
                     "init за человека не отвечает, прогон удаляется по run_id)")
            return
        run_id = str(payload.get("run_id") or "")
        try:
            action, task_id = payload.get("action"), str(payload.get("task_id") or "")
            if action == "ask_user" and task_id.endswith(".ask.confirm-context"):
                self.size_context(payload)
            elif action in {"llm", "batch"}:  # context.confirm off in .skillsrc: the task itself
                self.size_tasks(payload)
            elif action == "ask_user":
                name = task_id.split(".ask.", 1)[-1]
                self.say(f"  драйвер до первой задачи спрашивает «{name}»: {str(payload.get('question'))[:400]}")
                self.decisions.append(f"ответ на вопрос драйвера «{name}» (в сессии агента)")
                self.facts["first_stop"] = name
            elif action == "error" or (action == "done" and not run_id):
                result = payload.get("result") or {}
                code = payload.get("code") or result.get("reason") or result.get("reason_code")
                if code == "TASK_INPUT_TOO_LARGE":
                    self.decisions.append("--target")
                    raise Stop(f"драйвер: {code} — первая задача больше порога", self.init_command(target="<каталог фичи внутри модуля>"))
                raise Stop(f"драйвер не завёл прогон: {code}: {str(payload.get('message') or result.get('message') or '')[:300]}",
                           self.init_command(target="<каталог фичи>") if "TARGET" in json.dumps(payload) else f"смотреть {self.home / 'init-measure.stdout.json'}")
            else:
                self.say(f"  драйвер ответил {action} — размер первой задачи увидит агент")
        finally:
            self._remove_measuring_run(run_id, runs_before, before)

    def size_context(self, payload: dict[str, Any]) -> None:
        """S5 list of the measuring run: what the run will send; under <home> — a stop (the kit must stay out of the
        context); over the task budget — a stop with --target.  The digest is not answered here."""
        rows = [row for key in ("documents", "files") for row in payload.get(key) or [] if isinstance(row, dict)]
        total = sum(int(row.get("size") or 0) for row in rows)
        relative = self.home.relative_to(self.project).as_posix()
        leaked = [str(row.get("path")) for row in rows if str(row.get("path")).replace("\\", "/").startswith(relative + "/")]
        if leaked:
            raise Stop(f"в список для модели попали файлы комплекта ({len(leaked)}): {', '.join(leaked[:3])}",
                       f"исключить /{relative}/ в .git/info/exclude (или .gitignore) и повторить init")
        listing = Path(str(payload.get("listing_path") or ""))
        if listing.is_file():
            shutil.copy2(listing, self.home / "context-files.txt")
        self.facts["context"] = {"files": len(payload.get("files") or []), "documents": len(payload.get("documents") or []), "bytes": total}
        self.check_size("контекст разметки (документы + файлы проекта)", total)
        self.say(f"  в модель уйдут {len(payload.get('documents') or [])} документ(ов) и {len(payload.get('files') or [])} файл(ов); "
                 f"полный список — {self.home / 'context-files.txt'}")
        self.decisions.append("подтверждение отправки этих файлов (confirm-context) — в сессии агента, ответом агенту")

    def size_tasks(self, payload: dict[str, Any]) -> None:
        tasks = payload.get("tasks") if payload.get("action") == "batch" else [payload]
        for task in tasks or []:
            size = int(task.get("input_bytes") or 0) + sum(Path(task[key]).stat().st_size for key in ("skill_path", "schema_path")
                                                           if task.get(key) and Path(task[key]).is_file())
            self.check_size(f"задача {task.get('task_id')}", size)

    def check_size(self, what: str, size: int) -> None:
        tpb, context, budget = self.args.tokens_per_byte, self.args.model_context, self.task_input_bytes()
        self.say(f"  {what}: ≈ {_n(size)} Б ≈ {_n(size * tpb)} токенов ({tpb} ток/Б); порог задачи {_n(budget)} Б, окно {_n(context)} токенов")
        self.facts["first_task_bytes"] = size
        if size > budget:
            self.decisions.append("--target")
            raise Stop(f"{what} больше порога задачи: {size / budget:.2f}× — модель не вызывалась", self.init_command(target="<каталог фичи внутри модуля, уже чем сейчас>"))

    def _remove_measuring_run(self, run_id: str, runs_before: list[str], before: str) -> None:
        """Only the measuring run's own two directories under <project>/.pilot-runs, by its id; nothing else."""
        runs = self.project / ".pilot-runs"
        if RUN_ID.fullmatch(run_id) and run_id not in runs_before:
            for path in (runs / run_id, runs / f"{run_id}.driver"):
                if path.is_dir() and path.resolve().parent == runs.resolve():
                    shutil.rmtree(path)
        if not runs_before and runs.is_dir() and not any(runs.iterdir()):
            runs.rmdir()
        left = sorted(set(self._runs()) - set(runs_before))
        after = self._git_status()
        self.say("  замерный прогон удалён; .pilot-runs " + ("как был" if not left else f"ИЗМЕНИЛСЯ: {', '.join(left)}")
                 + "; git status проекта " + ("как был" if after == before else "ИЗМЕНИЛСЯ:"))
        for line in sorted(set(after.splitlines()) ^ set(before.splitlines()))[:10]:
            self.say("    " + line)

    # -- the first message for the agent
    def agent_command(self, *args: str) -> str:
        return shlex.join([self.py, str(self.pack / LAUNCHER), *args])

    def first_next(self) -> str:
        profile = self.args.profile
        extra = ["--analyst-report", "--suite", *(["--mutation"] if self.facts.get("mutation") else [])] if profile == "local-pilot-v1" else []
        return self.agent_command("next", "--project", str(self.project), "--profile", profile, *self._docs_args(), "--subject", self.args.subject,
                                  *(["--module", self.args.module] if self.args.module else []),
                                  *(["--target", self.args.target] if self.args.target else []),
                                  "--host-cli", self.args.host_cli, *(["--model-id", self.args.model] if self.args.model else []),
                                  *extra, "--compact")

    def agent_message(self) -> str:
        project, skill = str(self.project), self.pack / "skills" / "orchestrate" / "SKILL.md"
        if self.args.profile != "local-pilot-v1":
            mutation = "нет (профиль cases-only-v1: только кейсы и их ревью)"
        elif self.facts.get("mutation"):
            mutation = "да (--mutation)"
        else:
            mutation = "нет: " + (self.facts.get("mutation_off") or ("--no-mutation" if self.args.no_mutation else "mutation.enabled не записан в .skillsrc"))
        return "\n".join([
            f"Задача: прогон test-skills на проекте {project}, тема «{self.args.subject}».",
            f"1. Прочитай {skill} целиком и следуй ему — это главная инструкция.",
            f"2. Запускай только команды пакета, ровно в таком виде (без cd, без другого python, без других программ):",
            f"   {self.agent_command()} next|submit|status|answer|doctor …",
            "3. Первая команда:",
            f"   {self.first_next()}",
            "4. После перерыва или в новой сессии:",
            f"   {self.agent_command('status', '--project', project, '--run', '<run_id>')}",
            f"   {self.agent_command('next', '--project', project, '--run', '<run_id>', '--compact')}",
            "Правила:",
            "- human_exec: сборку, тесты и мутации сам не запускай — покажи мне команды по порядку и жди путь к отчёту;",
            "- ask_user (confirm-context, approve-generated-tests и другие, кроме reviewer-isolation): покажи вопрос и список, "
            "отвечай только тем, что напишу я;",
            "- задачу модели выполняй по SKILL.md: при субагентах входы задач читает субагент (свежий на задачу), сам их не читай; "
            "без субагентов — запасной путь SKILL.md;",
            "- reviewer-isolation отвечаешь сам: fresh — только если задачи выполняют субагенты, иначе none;",
            "- другие файлы проекта не меняй и ничего, кроме команд из п. 2, не запускай.",
            f"Проект: {project}; модуль: {self.args.module or self.module.get('id')}; спека: {', '.join(self.args.docs)}; "
            f"--target: {self.args.target or '— (весь модуль)'}; профиль: {self.args.profile}; мутации: {mutation}.",
        ])

    def finish(self) -> None:
        message = self.agent_message()
        self.facts.update({"agent_message": str(self.home / "agent-message.txt"), "first_next": self.first_next()})
        if not self.dry:
            (self.home / "agent-message.txt").write_text(message + "\n", encoding="utf-8")
        self.save_init()
        self.save_state()
        self.say("\n=== init: готово ===")
        self.say(f"Первое сообщение агенту (копия — {self.home / 'agent-message.txt'}): запустите gigacode в папке проекта и вставьте его.")
        self.say("-" * 72)
        self.say(message)
        self.say("-" * 72)
        self.say("Разрешать «всегда» в CLI — только next/submit/status; answer и python3 целиком — никогда (RUN.md, «Разрешения»).")
        self.say("Что ещё спросит прогон: " + ("; ".join(f"№{index} {text}" for index, text in enumerate(self.decisions, 1)) or "ничего до первой задачи модели"))
        if self.later:
            self.say("До сборки (human_exec), не сейчас:")
            for line in self.later:
                self.say(f"  - {line}")
        seconds = int(time.time() - self.started)
        self.say(f"init: {seconds // 60} мин {seconds % 60} с; решений впереди — {len(self.decisions)}")
        self.say("После прогона: " + shlex.join([sys.executable, str(Path(sys.argv[0]).resolve()), "report", "--project", str(self.project),
                                                  "--run", "<run_id>"]))


# ---------------------------------------------------------------- report: after the agent's run

class Report(Operator):
    """``operate.py report``: 7 <home>/report/ and 8 the Zephyr CSV of a finished run.  The result comes from the
    driver's ``status`` (read-only); a run that is not finished is a stop — report never advances it."""

    def run_report(self) -> int:
        self.log(f"\n===== operate.py {datetime.datetime.now().isoformat(timespec='seconds')} report {shlex.join(sys.argv[2:])}")
        steps = [item.strip() for item in (self.args.only or "7,8").split(",") if item.strip()]
        for step, title, part in (("", "итог прогона", self.load_result), ("7", "Что принести (<home>/report)", self.step_7),
                                  ("8", "Экспорт Zephyr", self.step_8)):
            if step and step not in steps:
                continue
            self.say(f"\n=== {'Шаг ' + step + '. ' if step else ''}{title} ===")
            try:
                part()
            except Stop as stop:
                self.say(f"СТОП{' на шаге ' + step if step else ''}: {stop.message}")
                if stop.hint:
                    self.say(f"Что делать: {stop.hint}")
                return 1
        self.say("\nГотово." + ("" if self.dry else f" Журнал: {self.home / 'operate.log'}"))
        return 0

    def run_id(self) -> str:
        if self.args.run:
            if not RUN_ID.fullmatch(self.args.run):
                raise Stop(f"--run {self.args.run}: не run_id (32 шестнадцатеричных символа)", "run_id — из вывода агента или имя каталога в .pilot-runs")
            return self.args.run
        drivers = sorted((self.project / ".pilot-runs").glob("*.driver"), key=lambda path: path.stat().st_mtime) \
            if (self.project / ".pilot-runs").is_dir() else []
        if not drivers:
            raise Stop(f"в {self.project / '.pilot-runs'} нет прогонов", "передать --run <run_id>")
        run_id = drivers[-1].name[:-len(".driver")]
        if not self.confirm(f"Последний прогон — {run_id}. Отчёт по нему?"):
            raise Stop("прогон не выбран", "передать --run <run_id>")
        return run_id

    def load_result(self) -> None:
        run_id = self.run_id() if not self.dry or self.args.run else "<run_id>"
        payload = self.driver([self.py, "-m", "tools.pipeline_driver", "status", "--project", str(self.project), "--run", run_id], "report-status")
        if self.dry:
            self.say("  (итог — поле result ответа status; нет его — стоп «прогон не закончен», прогон не продвигается)")
            self.payload = {}
            return
        if payload.get("action") == "error":
            raise Stop(f"драйвер: {payload.get('code')}: {str(payload.get('message'))[:300]}", "проверить --project и --run")
        if not isinstance(payload.get("result"), dict):
            raise Stop(f"прогон {run_id} не закончен (attempt_state {payload.get('attempt_state') or '—'}) или пакет не отдаёт итог в status",
                       "дождаться конца прогона в сессии агента (done) и повторить report")
        self.payload = {"action": "done", "run_id": run_id, "result": payload["result"]}
        (self.home / "run-result.json").write_text(json.dumps(self.payload, ensure_ascii=False, indent=1), encoding="utf-8")
        result = payload["result"]
        self.say(f"  {run_id}: completion {result.get('completion')}, verification {result.get('verification')}, reason {result.get('reason_code') or '—'}")

    def step_7(self) -> None:
        payload = self.payload
        self.action(f"собрать {self.report}: driver-log.jsonl, config.json (без секретов), run-result.json, errors.txt, runner-output-errors.txt, "
                    "test-strength.md, summary.md (без кода, спеки, кейсов, ответов моделей)")
        if self.dry:
            return
        result, run_id = payload["result"], payload["run_id"]
        paths = result.get("paths") or {}
        driver = Path(paths.get("driver_dir") or self.project / ".pilot-runs" / f"{run_id}.driver")
        run_root = Path(paths.get("run_root") or self.project / ".pilot-runs" / run_id)
        self.report.mkdir(parents=True, exist_ok=True)
        copied = []
        if (driver / "driver-log.jsonl").is_file():
            shutil.copy2(driver / "driver-log.jsonl", self.report / "driver-log.jsonl")
            copied.append("driver-log.jsonl")
        if (driver / "config.json").is_file():
            config = json.loads((driver / "config.json").read_text(encoding="utf-8"))
            (self.report / "config.json").write_text(json.dumps(_redact(config), ensure_ascii=False, indent=1), encoding="utf-8")
            copied.append("config.json")
        (self.report / "run-result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        copied.append("run-result.json")
        events = _read_jsonl(driver / "driver-log.jsonl")
        errors = [f"{event.get('at')} {event.get('event')} {event.get('task_id') or ''} {event.get('code') or event.get('failure_class') or ''} "
                  f"{str(event.get('reason') or event.get('message') or '')[:300]}".rstrip()
                  for event in events if event.get("event") in {"error", "triage_failed"}]
        if result.get("reason_code"):
            errors.append(f"result reason_code {result['reason_code']}")
        (self.report / "errors.txt").write_text("\n".join(errors) + "\n", encoding="utf-8")
        copied.append(f"errors.txt ({len(errors)})")
        outputs = sorted(run_root.glob("artifacts/*/runner-output.txt"), key=lambda path: path.stat().st_mtime)
        if outputs:
            lines = [line[:300] for line in outputs[-1].read_text(encoding="utf-8", errors="replace").splitlines()
                     if line.startswith("[ERROR]") or "BUILD FAILURE" in line or re.match(r"^\[(?:INFO|WARNING|ERROR)\] Tests run:", line)]
            (self.report / "runner-output-errors.txt").write_text("\n".join(lines[-200:]) + "\n", encoding="utf-8")
            copied.append(f"runner-output-errors.txt ({len(lines[-200:])})")
        strength_md = paths.get("test_strength_markdown")
        if strength_md and Path(strength_md).is_file():
            shutil.copy2(strength_md, self.report / "test-strength.md")
            copied.append("test-strength.md")
        figures = _figures(result, events, run_root, self.project)
        try:
            mode = json.loads((driver / "config.json").read_text(encoding="utf-8")).get("execution_mode") or "auto"
        except (OSError, ValueError):
            mode = "—"
        gates = result.get("gates_disabled_by") or payload.get("gates_disabled_by")
        if gates:
            figures.append(("Гейты", "выключены конфигом проекта (" + str(gates) + "): " + ", ".join(
                str(item) for item in (result.get("gates_disabled") or payload.get("gates_disabled") or [])) + " — ВНИМАНИЕ"))
        figures.append(("Режим исполнения (execution_mode)", f"{mode} — " + ("сборку и тесты выполнял человек по напечатанным командам"
                                                                             if mode == "human" else "сборку и тесты запускал пакет")))
        try:
            init = json.loads((self.home / "init.json").read_text(encoding="utf-8"))
            figures.append(("init", f"{int(init.get('seconds') or 0) // 60} мин {int(init.get('seconds') or 0) % 60} с, "
                                    f"решений впереди {len(init.get('decisions') or [])}"))
        except (OSError, ValueError):
            pass
        table = ["| Показатель | Значение |", "| --- | --- |", *[f"| {name} | {value} |" for name, value in figures]]
        (self.report / "summary.md").write_text(f"# Внутренний прогон {run_id}\n\n" + "\n".join(table) + "\n", encoding="utf-8")
        copied.append("summary.md")
        self.say(f"  в {self.report}: {', '.join(copied)}")
        width = max(len(name) for name, _value in figures)
        for name, value in figures:
            self.say(f"  {name:<{width}}  {value}")

    def step_8(self) -> None:
        payload = self.payload
        if self.dry:
            self.action("путь к test-cases/test-cases.zephyr-scale.csv и первые 3 строки")
            return
        paths = payload["result"].get("paths") or {}
        candidates = [Path(paths["suite_dir"]) / "test-cases.zephyr-scale.csv"] if paths.get("suite_dir") else []
        candidates.append(self.project / "test-cases" / "test-cases.zephyr-scale.csv")
        if paths.get("candidate_bundle"):
            candidates += sorted(Path(paths["candidate_bundle"]).glob("*.zephyr-scale.csv"), reverse=True)
        csv_path = next((path for path in candidates if path.is_file()), None)
        if csv_path is None:
            raise Stop("CSV для Zephyr не найден", "набор не записан (suite) — смотреть suite в run-result.json")
        self.say(f"  CSV: {csv_path}")
        with csv_path.open(encoding="utf-8-sig", errors="replace") as handle:
            for _ in range(3):
                line = handle.readline()
                if not line:
                    break
                print("    " + line.rstrip()[:200], flush=True)
        self.log("(первые строки CSV в журнал не пишутся)")
        self.say("  Импорт в Zephyr — CSV (не XML); приоритеты High/Normal/Low; поля АС, Вид тестирования, Команда, Приоритет теста — проверить в первой строке.")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [row for row in (_json_tail(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines()) if isinstance(row, dict)]


def _redact(value: Any, key: str = "") -> Any:
    if re.search(r"(?i)secret|token|password|passwd|api[_-]?key|credential|extra_env|^env$", key):
        return "***"
    if isinstance(value, dict):
        return {name: _redact(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    return value


def _count_cases(result: dict[str, Any], project: Path) -> tuple[int, int] | None:
    """(cases, checks) of the run's suite or newest candidate document."""
    cases = _case_list(result, project)
    if cases is None:
        return None
    checks = sum(len(expectation.get("assertions") or []) for case in cases for step in case.get("steps") or []
                 for expectation in step.get("expectations") or [])
    return len(cases), checks


def _case_list(result: dict[str, Any], project: Path) -> list[dict[str, Any]] | None:
    """The cases of the run's suite or newest candidate document."""
    paths = result.get("paths") or {}
    candidates = [Path(paths["suite_dir"]) / "test-cases.json"] if paths.get("suite_dir") else []
    if paths.get("candidate_bundle"):
        candidates += sorted(Path(paths["candidate_bundle"]).glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    candidates.append(project / "test-cases" / "test-cases.json")
    for path in candidates:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        cases = document.get("test_cases") if isinstance(document, dict) else None
        if isinstance(cases, list):
            return [case for case in cases if isinstance(case, dict)]
    return None


PRIORITY_LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")


def _design_figures(result: dict[str, Any], project: Path) -> list[tuple[str, str]]:
    """D1: design coverage and the priority spread from the run result (``design_coverage``); a run without the
    design stage has no coverage, and its priorities were chosen by the model — counted from the case document."""
    covered = result.get("design_coverage")
    if not isinstance(covered, dict):
        cases = _case_list(result, project)
        spread = (", ".join(f"{level} {sum(case.get('priority') == level for case in cases)}" for level in PRIORITY_LEVELS)
                  + " (выбраны моделью)") if cases else "—"
        return [("Покрытие дизайна", "— (стадии тест-дизайна в прогоне не было: .skillsrc design.enabled: false)"), ("Приоритеты", spread)]
    spread = _priority_text(covered) + " (назначены кодом по дизайну)"
    degenerate = [row.get("message") for row in result.get("warnings") or [] if isinstance(row, dict) and row.get("code") == "PRIORITY_DEGENERATE"]
    if degenerate:
        spread += " — ВНИМАНИЕ: один уровень больше 70 % кейсов"
    return [("Покрытие дизайна", _coverage_text(covered)), ("Приоритеты", spread)]


def _coverage_text(covered: dict[str, Any]) -> str:
    """One line of a ``design_coverage`` (run result or suite manifest): covered/total per element kind, the uncovered."""
    def share(name: str, label: str) -> str:
        row = covered.get(name) or {}
        ratio = row.get("ratio")
        return f"{label} {row.get('covered', '—')}/{row.get('total', '—')}" + (f" ({ratio:.0%})" if isinstance(ratio, (int, float)) else "")

    uncovered = covered.get("uncovered") or []
    text = ", ".join(share(name, label) for name, label in (("partitions", "разбиения"), ("boundaries", "границы"), ("rules", "правила"),
                                                             ("combinations", "комбинации CB"),
                                                             ("negative_partitions", "невалидные разбиения негативными кейсами"))
                     if name in covered)  # combinations: package with D1b
    return text + f"; не покрыто с причиной: {len(uncovered)}" + (" (" + ", ".join(str(row.get("ref")) for row in uncovered[:10])
                                                                  + (", …" if len(uncovered) > 10 else "") + ")" if uncovered else "")


def _priority_text(covered: dict[str, Any]) -> str:
    priorities = covered.get("priorities") or {}
    return ", ".join(f"{level} {priorities.get(level, 0)}" for level in PRIORITY_LEVELS)


# C10: what operate.log keeps of a command's output — the last lines, secret-looking values masked.
LOG_TAIL_LINES = 200
SECRET_IN_TEXT = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}"
                            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
                            r"|(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"
                            r"|(?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|password|passwd)[\"']?\s*[=:]\s*[\"']?[^\s\"',;]{6,})")


def log_tail(text: str, lines: int = LOG_TAIL_LINES) -> str:
    """The last ``lines`` lines of a command's output with secret-looking values masked (C10)."""
    rows = (text or "").splitlines()
    head = f"[… {len(rows) - lines} строк выше опущено]\n" if len(rows) > lines else ""
    return head + SECRET_IN_TEXT.sub("***", "\n".join(rows[-lines:]))


def _figures(result: dict[str, Any], events: list[dict[str, Any]], run_root: Path, project: Path) -> list[tuple[str, str]]:
    cases = _count_cases(result, project)
    issued = {str(event.get("task_id")) for event in events if event.get("event") in {"task_issued", "task_reissued", "runner_collected"}}
    issued |= {str(task) for event in events if event.get("event") == "batch_issued" for task in event.get("task_ids") or []}
    canonical = {match.group(1) for task in issued if (match := re.search(r"review\.canonical\.(part-\d+)", task))}
    automation = {match.group(0) for task in issued if (match := re.search(r"review\.r\d+\.part-\d+", task))}
    tests = failed = skipped = 0
    for report in run_root.glob(f"artifacts/{result.get('attempt_id') or '*'}/reports/*.xml"):
        try:
            root = ET.parse(report).getroot()
        except ET.ParseError:
            continue
        for suite in [root] if root.tag == "testsuite" else root.iter("testsuite"):
            tests += int(suite.get("tests") or 0)
            failed += int(suite.get("failures") or 0) + int(suite.get("errors") or 0)
            skipped += int(suite.get("skipped") or 0)
    strength = result.get("test_strength") or {}
    suite = result.get("suite") or {}
    epochs = [float(event["epoch"]) for event in events if isinstance(event.get("epoch"), (int, float))]
    duration = int(max(epochs) - min(epochs)) if epochs else 0
    return [
        ("Итог", f"completion {result.get('completion')}, verification {result.get('verification')}, accepted {result.get('accepted')}"
                 + (f", reason {result['reason_code']}" if result.get("reason_code") else "")),
        ("Кейсов", str(cases[0]) if cases else "—"),
        *_design_figures(result, project),
        ("Проверок (assertions)", str(cases[1]) if cases else "—"),
        ("Задач модели", str(len(issued)) if issued else "—"),
        ("Частей ревью кейсов", str(len(canonical))),
        ("Частей ревью автотестов", str(len(automation))),
        ("Методов автотестов", str(tests) if tests else "—"),
        ("Прошло / упало / пропущено", f"{tests - failed - skipped} / {failed} / {skipped}" if tests else "—"),
        ("В карантине", str(suite.get("quarantined", "—"))),
        ("Мутанты убито / выжило / без покрытия", f"{strength.get('killed', '—')} / {strength.get('survived', '—')} / {strength.get('no_coverage', '—')}"
                                                  + (f" (test strength {strength.get('score')})" if strength.get("score") is not None else "")),
        ("Разбор выживших", json.dumps((result.get("strength_triage") or {}).get("decisions") or {}, ensure_ascii=False) or "—"),
        ("Вопросов аналитику", str(result.get("analyst_questions", "—"))),
        ("Время прогона", f"{duration // 3600} ч {duration % 3600 // 60} мин" if duration else "—"),
    ]


def _subject(docs: list[str], project: Path) -> str:
    """The first heading of the first specification (or its file name)."""
    path = Path(docs[0]) if Path(docs[0]).is_absolute() else project / docs[0]
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            heading = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", line)
            if heading:
                return heading.group(1)[:120]
    except OSError:
        pass
    return path.stem


def _default_home() -> str:
    return next((str(path) for path in (HERE, HERE.parent) if (path / "test-skills").is_dir()), str(HERE))


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="operate.py init", description="Подготовка до сессии агента: комплект, preflight, .skillsrc, "
                                                                         "размер первой задачи — и готовое первое сообщение агенту CLI.")
    parser.add_argument("--project", required=True, help="корень проекта")
    parser.add_argument("--docs", action="append", required=True, help="спека (путь внутри проекта); можно несколько")
    parser.add_argument("--home", help="каталог комплекта внутри проекта (там test-skills/); по умолчанию — папка operate.py")
    parser.add_argument("--subject", help="тема прогона (по умолчанию — первый заголовок спеки)")
    parser.add_argument("--module", help="id модуля, если init не выбрал его сам")
    parser.add_argument("--target", help="каталог фичи внутри модуля, если init не угадал его сам")
    parser.add_argument("--profile", choices=PROFILES, default="local-pilot-v1",
                        help="профиль прогона в сообщении агенту: local-pilot-v1 (по умолчанию: кейсы, автотесты, их прогон человеком) "
                             "или cases-only-v1 (быстрая первая проверка: только кейсы и их ревью)")
    parser.add_argument("--model", help="имя модели CLI для квитанций прогона (--model-id драйвера); по умолчанию не передаётся")
    parser.add_argument("--host-cli", default=HOST_CLI, help=f"имя CLI агента для квитанций (по умолчанию {HOST_CLI})")
    parser.add_argument("--id-pattern", help="шаблон ID требований (иначе — вывести из спеки и подтвердить)")
    parser.add_argument("--no-mutation", action="store_true", help="без PIT")
    parser.add_argument("--online", action="store_true", help="не писать modules[].test.offline: true в .skillsrc (сборка может ходить "
                                                              "в репозитории артефактов); по умолчанию — offline")
    parser.add_argument("--tokens-per-byte", type=float, default=TOKENS_PER_BYTE, help="токенов на байт входа (по умолчанию 0.24)")
    parser.add_argument("--model-context", type=int, default=MODEL_CONTEXT, help="окно модели, токенов (по умолчанию 239144)")
    parser.add_argument("--task-input-bytes", type=int, help="порог входа одной задачи → .skillsrc limits.task_input_bytes; по умолчанию "
                                                             "700000 или 0,8 окна в байтах, что меньше")
    parser.add_argument("--yes", action="store_true", help="подтверждать вопросы оператора (шаблон ID требований); .skillsrc и chmod — нет")
    parser.add_argument("--yes-skillsrc", action="store_true", help="записать .skillsrc проекта без вопроса; diff печатается")
    parser.add_argument("--yes-chmod", action="store_true", help="chmod +x mvnw/gradlew проекта без вопроса")
    parser.add_argument("--no-pip", action="store_true", help="не предлагать команду pip: зависимости ставятся средствами окружения")
    parser.add_argument("--dry-run", action="store_true", help="только напечатать команды и действия")
    return parser


def build_report_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="operate.py report", description="После прогона: 7 — <home>/report/ с цифрами, 8 — CSV для Zephyr.")
    parser.add_argument("--project", required=True, help="корень проекта")
    parser.add_argument("--home", help="каталог комплекта (там test-skills/); по умолчанию — папка operate.py")
    parser.add_argument("--run", help="run_id прогона (по умолчанию — последний в .pilot-runs, с подтверждением)")
    parser.add_argument("--only", help="только эти шаги: 7, 8 или 7,8 (по умолчанию оба)")
    parser.add_argument("--yes", action="store_true", help="подтверждать вопросы оператора (выбор последнего прогона)")
    parser.add_argument("--dry-run", action="store_true", help="только напечатать команды и действия")
    return parser


def init_main(argv: list[str]) -> int:
    options = build_init_parser().parse_args(argv)
    options.home = options.home or _default_home()
    project = Path(options.project).expanduser().resolve()
    outside = [doc for doc in options.docs if doc_inside(project, doc) is None]
    if outside:
        kept, items = [], iter(argv)
        for item in items:
            if item == "--docs" or item.startswith("--docs="):
                if item == "--docs":
                    next(items, None)
                continue
            kept.append(item)
        print(f"СТОП на шаге init: --docs — только внутри проекта {project} (без ссылок на пути): {', '.join(outside)}", flush=True)
        print("Что делать: " + shlex.join([sys.executable, str(Path(sys.argv[0]).resolve()), "init", *kept, "--docs", "<спека внутри проекта>"]), flush=True)
        return 1
    options.subject = options.subject or _subject(options.docs, project)
    return Init(options, argv).run_init()


def report_main(argv: list[str]) -> int:
    options = build_report_parser().parse_args(argv)
    options.home = options.home or _default_home()
    return Report(options).run_report()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["init"]:
        return init_main(argv[1:])
    if argv[:1] == ["report"]:
        return report_main(argv[1:])
    print("operate.py init … — подготовка до сессии агента; operate.py report … — отчёт и Zephyr после прогона (RUN.md)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
