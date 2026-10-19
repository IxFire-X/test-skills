#!/usr/bin/env python3
"""operate.py — the internal test-skills run by RUN.md, step by step, without a model operator.

    python3 operate.py --home ~/ts --project <project> --docs <spec> [--docs …] --subject "<тема>"
        --models glm-5.3,… --cli-command '<CLI command with {model}>' --prompt-via stdin|file|arg
        --answer-from stdout|file [--answer-file …] [--sandbox-args '<CLI no-tools flags>'] [--runner-dir DIR]
        [--module ID] [--target T] [--review-input-bytes 100000] [--max-tasks 2] [--tokens-per-byte 0.24] [--model-context 239144]
        [--no-mutation] [--online] [--from-step N] [--only 0,2,3] [--yes] [--yes-skillsrc] [--yes-chmod]
        [--yes-send] [--yes-exec] [--no-pip] [--dry-run]

preflight.py, wrap/ and bench/ are taken from beside operate.py itself; --home is only where the package (test-skills/ and
its test-skills-*.files.sha256), the runner directory, the state and the report live.  The package runs under
<home>/test-skills/.venv's Python when that venv exists, else under the Python that runs operate.py.  The model CLI
wrapper is always launched by that interpreter ([python, wrap.py, …] — no script is executed by its shebang).

Steps are the sections of RUN.md: 0 kit check (the test-skills folder against test-skills-*.files.sha256, doctor),
1 preflight, 2 wrap.json + selftest + one live call, 3 model benchmark and models by role, 4 .skillsrc
(review_runner, requirements.id_pattern, mutation, modules[].test.offline unless --online), 5a cases-only trial, 5 full local-pilot-v1 run (ask_user answered
here; regenerate-after-gate and BUILD_FAILED_BEFORE_TESTS stop with the runner output tail), 7 <home>/report/ with the
figures, 8 Zephyr CSV.  Every step prints its commands, exit codes and main lines; everything goes to
<home>/operate.log.  A failing step stops with a hint; --from-step continues.

With --home inside the project everything stays there: the runner directory defaults to <home>/.runner and the
wrapper's temp files go to <home>/.tmp (preflight prints the linter excludes for .pilot-runs and <home>).

operate.py itself opens no network connection and installs nothing (a missing pip dependency stops step 0 with the
command to run).  The processes it starts do what the package does: the model CLI (via wrap.py) sends the tasks —
parts of the specification and of the project's code — to the model endpoint configured in that CLI; the project's
Maven/Gradle build and PIT run the project's and the generated tests, offline (local repository only) unless --online.  See USAGE.md «Права, сеть и исполнение» and RUN.md «Права и сеть».
--yes confirms the operator's questions but never the edit of the project's .skillsrc (--yes-skillsrc) nor chmod +x
of the project's files (--yes-chmod).  Python 3.11+, stdlib only; Linux (a Windows branch exists only for checks).
"""
from __future__ import annotations

import argparse
import datetime
import difflib
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

STEPS = ("0", "1", "2", "3", "4", "5a", "5", "7", "8")
TITLES = {"0": "Проверка комплекта", "1": "Preflight проекта", "2": "Обёртка CLI", "3": "Бенчмарк моделей", "4": ".skillsrc проекта",
          "5a": "Пробный прогон (cases-only-v1)", "5": "Полный прогон (local-pilot-v1)", "7": "Что принести (<home>/report)", "8": "Экспорт Zephyr"}
ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "mutation-triage")
REVIEW_ROLES = ("tc-reviewer", "autotest-reviewer")
PROBE = "test-skills-probe: проверка обёртки. Ответь только JSON-объектом {\"ok\": true} без пояснений.\n"
STATUS_SECONDS = 60
HERE = Path(__file__).resolve().parent
TOKENS_PER_BYTE = 0.24   # DeepSeek-V4.1-Flash through gigacode, internal run 2026-10-15: 0.2391
MODEL_CONTEXT = 239_144  # its hard prompt limit, tokens
# The package's own .skillsrc parser: the modules as the driver sees them (id, root, test).
MODULES_CODE = ("import json, sys\nfrom pathlib import Path\nfrom tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc\n"
                "try:\n    modules = normalize_skillsrc(load_skillsrc(Path(sys.argv[1])))['modules']\n"
                "except SkillsrcError as error:\n"
                "    sys.exit(f'{error.code}: {error} {json.dumps(error.details, ensure_ascii=False)[:400]}')\n"
                "print(json.dumps([{'id': m['id'], 'root': m.get('root', '.'), 'test': m.get('test') or {}} for m in modules], ensure_ascii=False))")
CODEX_ARGV_CODE = "import json\nfrom tools.model_runner import PRESETS\nprint(json.dumps(PRESETS['codex']['argv']))"
# The driver's stops (ask_user) operate.py knows: label → (what is being decided, what to show, the flag that answers it).
# The payload is shown as it comes (question, options, then any other list or value it carries) — no field names are
# assumed.  A flag answers only a stop with a single option; --yes never answers these.  Other questions are asked as is.
ASK_STOPS: dict[str, tuple[str, str, str | None]] = {
    "confirm-context": ("отправка выбранных файлов проекта в модель", "payload", "yes-send"),
    "approve-generated-tests": ("исполнение сгенерированных тестов сборкой проекта", "payload", "yes-exec"),  # also -apply/-repair
    "regenerate-after-gate": ("перегенерация тестов после гейта сборки", "runner-tail", None),
}
ASK_KNOWN_KEYS = {"action", "task_id", "question", "options", "run_id", "attempt_id", "stage", "skillsrc_question_id"}
WRAPPER_ADAPTER, SYSTEM_MAVEN ="maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1"


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


def set_child(lines: list[str], key: str, child: str, value: str) -> list[str]:
    """Set ``key.child`` to the YAML scalar ``value``, keeping the block's other children."""
    found = _top_block(lines, key)
    if found is None:
        return [*lines, f"{key}:", f"  {child}: {value}"]
    start, end = found
    if lines[start].split(":", 1)[1].strip():  # flow form `key: {…}` — rewritten as a block with this child only
        return [*lines[:start], f"{key}:", f"  {child}: {value}", *lines[end:]]
    body = lines[start + 1:end]
    indent = next((re.match(r"^(\s+)", line).group(1) for line in body if line.strip()), "  ")
    for offset, line in enumerate(body):
        if re.match(rf"^{indent}{re.escape(child)}\s*:", line):
            return [*lines[:start + 1 + offset], f"{indent}{child}: {value}", *lines[start + 2 + offset:]]
    return [*lines[:start + 1], f"{indent}{child}: {value}", *lines[start + 1:]]


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
        notes.append("ни у одного модуля нет блока test — пакет создаст его при первом прогоне; шаг 4 повторится перед полным прогоном")
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
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.dry = args.dry_run
        self.home = Path(args.home).expanduser().resolve()
        self.pack = self.home / "test-skills"
        self.py = pack_python(self.pack)
        self.wrap_dir = HERE / "wrap"
        self.report = self.home / "report"
        self.project = Path(args.project).expanduser().resolve()
        self.models = [item.strip() for item in args.models.split(",") if item.strip()]
        self.default_model = args.default_model or self.models[0]
        self.in_project = self.home.is_relative_to(self.project)
        default_runner = self.home / (".runner" if self.in_project else "runner")
        self.runner_dir = Path(args.runner_dir).expanduser().resolve() if args.runner_dir else default_runner
        self.tmp_dir = self.home / ".tmp" if self.in_project else None
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
        if self.dry or getattr(self.args, flag.replace("-", "_")):
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

    def mark_first_call(self, when: datetime.datetime | None = None) -> None:
        """The first answered model call (S10 KPI); later calls do not move it."""
        log = self.home / "operate.log"
        if FIRST_CALL not in log.read_text(encoding="utf-8", errors="replace"):
            self.log(f"{FIRST_CALL} {(when or datetime.datetime.now()).isoformat(timespec='milliseconds')}")

    def mark_first_call_of(self, run_id: str) -> None:
        """Without init or step 2 the first model answer of the run is the KPI mark (from the driver's log)."""
        epochs = [float(event["epoch"]) for event in self._log_events(run_id) if event.get("event") == "runner_collected" and event.get("epoch")]
        if epochs:
            self.mark_first_call(datetime.datetime.fromtimestamp(min(epochs)))

    def command(self, step: str, *extra: str) -> str:
        """The ready command to continue from ``step``: this run's own arguments without --from-step/--only/--run/--answer/--dry-run."""
        argv, skip = [], {"--from-step", "--only", "--run", "--answer"}
        items = iter(sys.argv[1:])
        for item in items:
            if item in skip:
                next(items, None)
            elif item != "--dry-run" and not any(item.startswith(flag + "=") for flag in skip):
                argv.append(item)
        return shlex.join([sys.executable, str(Path(sys.argv[0]).resolve()), *argv, "--from-step", step, *extra])

    # -- commands
    def run(self, argv: list[Any], *, cwd: Path | None = None, stdin: str | None = None, echo: bool = False, timeout: int | None = None) -> Result:
        argv = [str(item) for item in argv]
        self.say(f"$ {shlex.join(argv)}" + (f"   (в {cwd})" if cwd else ""))
        if self.dry:
            return Result(0)
        self.log(f"--- запуск {datetime.datetime.now().isoformat(timespec='seconds')}")
        try:
            if echo:
                process = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if stdin is not None else None, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, env=self.env, text=True, encoding="utf-8", errors="replace")
                if stdin is not None:
                    process.stdin.write(stdin)
                    process.stdin.close()
                out = []
                for line in process.stdout:
                    print("  " + line.rstrip(), flush=True)
                    out.append(line)
                stderr = process.stderr.read()
                code = process.wait()
                done = Result(code, "".join(out), stderr)
            else:
                completed = subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, env=self.env, text=True, encoding="utf-8",
                                           errors="replace", timeout=timeout, check=False)
                done = Result(completed.returncode, completed.stdout, completed.stderr)
        except FileNotFoundError as error:
            raise Stop(f"команда не запустилась: {error}", "проверить путь к программе и PATH")
        except subprocess.TimeoutExpired:
            raise Stop(f"команда не уложилась в {timeout} с", "смотреть operate.log")
        self.log("stdout:\n" + done.stdout + "\nstderr:\n" + done.stderr)
        self.say(f"  код выхода: {done.code}")
        return done

    def pack_json(self, code: str, *args: Any) -> Any:
        """A read-only question to the package (its own parser or presets) under its interpreter; runs in --dry-run too."""
        argv = [self.py, "-c", code, *[str(item) for item in args]]
        self.log(f"$ {shlex.join(argv)}   (в {self.pack})")
        try:
            done = subprocess.run(argv, cwd=self.pack, capture_output=True, env=self.env, text=True, encoding="utf-8", errors="replace",
                                  timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise Stop(f"пакет не ответил: {error}", "сначала шаг 0 (комплект и интерпретатор)")
        self.log("stdout:\n" + done.stdout + "\nstderr:\n" + done.stderr)
        if done.returncode != 0:
            raise Stop(f"пакет не ответил (код {done.returncode}): {' | '.join(_main_lines(done.stderr, 2))}", "сначала шаг 0; ошибку — в отчёт (п. 7)")
        return json.loads(done.stdout)

    def runner_command(self) -> list[str]:
        """The driver's --review-runner-command: the package's codex preset argv with [python, wrap.py] in place of {cli} —
        the wrapper is never executed by its shebang (execve of scripts is forbidden on some hosts)."""
        argv = self.pack_json(CODEX_ARGV_CODE)
        return [self.py, str(self.wrap_dir / "wrap.py"), *[item for item in argv if item != "{cli}"]]

    def skillsrc_modules(self) -> list[dict[str, Any]] | None:
        """The modules of the project's .skillsrc by the package's parser; None without .skillsrc."""
        skillsrc = self.project / ".skillsrc"
        if not skillsrc.is_file():
            return None
        try:
            return self.pack_json(MODULES_CODE, skillsrc)
        except Stop as stop:
            raise Stop(f".skillsrc проекта не читается пакетом: {stop.message}", "исправить .skillsrc (ошибка выше) и повторить шаг")

    def check_module(self) -> None:
        """--module against the .skillsrc modules: several modules need it (the driver stops with module_required otherwise)."""
        modules = self.skillsrc_modules()
        if modules is None:
            if self.args.module:
                self.say(f"  в проекте ещё нет .skillsrc — --module {self.args.module} проверит драйвер")
            return
        ids = [module["id"] for module in modules]
        listed = ", ".join(f"{module['id']} ({module['root']})" for module in modules)
        if self.args.module and self.args.module not in ids:
            raise Stop(f"--module {self.args.module}: такого модуля в .skillsrc нет; есть: {listed}", "передать --module <id> из списка")
        if not self.args.module and len(ids) > 1:
            raise Stop(f"в .skillsrc модулей {len(ids)}, а --module не задан: {listed}", "передать --module <id> из списка (корень модуля — в скобках)")
        if len(ids) > 1 and not self.args.target:
            self.say("  внимание: модулей несколько, а --target не задан — разметка контекста получит весь модуль; RUN.md: --target обязателен")

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
        if self.in_project:
            self.say(f"  комплект внутри проекта ({self.home.relative_to(self.project)}): каталог раннера {self.runner_dir}, "
                     f"временные файлы обёртки {self.tmp_dir}")
        if not self.dry:
            rows = [line.split(None, 1) for line in files.read_text(encoding="utf-8").splitlines() if line.strip()]
            listed = {name.lstrip("*").strip() for _digest, name in rows}
            bad = [name for digest, name in ((digest, name.lstrip("*").strip()) for digest, name in rows)
                   if not (self.home / name).is_file() or _sha256(self.home / name) != digest]
            extra = sorted(path.relative_to(self.home).as_posix() for path in self.pack.rglob("*")
                           if path.is_file() and "__pycache__" not in path.parts and path.relative_to(self.home).as_posix() not in listed)
            if bad:
                raise Stop(f"файлы пакета отличаются от {files.name} ({len(bad)}): {', '.join(bad[:5])}",
                           f"удалить {self.pack} и скопировать папку test-skills из комплекта заново")
            code = [name for name in extra if name.endswith(".py")]
            if code:
                raise Stop(f"в пакете лишние .py, которых нет в {files.name} ({len(code)}): {', '.join(code[:5])}",
                           f"удалить {self.pack} и скопировать папку test-skills из комплекта заново")
            self.say(f"  файлы пакета: {len(rows)} совпадают" + (f"; лишних файлов {len(extra)}: {', '.join(extra[:5])}" if extra else ""))
        for path in (self.wrap_dir / "wrap.py", HERE / "preflight.py", HERE / "bench" / "bench.py"):
            if not path.is_file():
                raise Stop(f"рядом с operate.py нет {path.relative_to(HERE)}", f"скопировать комплект целиком: operate.py, preflight.py, wrap/, bench/ — в одну папку ({HERE})")
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
                       "operate.py сам ничего не ставит; после установки — --from-step 0")
        doctor = self.run([self.py, "-m", "tools.doctor", "--root", "."], cwd=self.pack)
        if self.dry:
            return
        report = _json_tail(doctor.stdout) or {}
        self.say(f"  doctor: {report.get('status')}")
        if doctor.code != 0 or report.get("status") != "PASS":
            raise Stop(f"doctor пакета: {report.get('status') or 'нет JSON'} (код {doctor.code})", "удалить распакованный test-skills, повторить шаг 0; ошибку — в отчёт")

    # -- step 1
    def _docs_args(self) -> list[str]:
        return [item for doc in self.args.docs for item in ("--docs", doc)]

    def step_1(self, again: bool = False) -> None:
        if not os.environ.get("JAVA_HOME"):
            self.say("  внимание: JAVA_HOME не задан (export JAVA_HOME=<JDK 17+>)")
        report = self.home / "preflight.json"
        inside = [self.home.relative_to(self.project).as_posix()] if self.in_project else []
        inside += [self.runner_dir.relative_to(self.project).as_posix()] if self.runner_dir.is_relative_to(self.project) and not \
            self.runner_dir.is_relative_to(self.home) else []
        inside += [HERE.relative_to(self.project).as_posix()] if HERE.is_relative_to(self.project) and not HERE.is_relative_to(self.home) else []
        done = self.run([self.py, HERE / "preflight.py", "--pack", self.pack, "--project", self.project, *self._docs_args(),
                         *[item for path in inside for item in ("--exclude-dir", path)], "--json", report])
        if self.dry:
            self.say("  (при «стоп» только из-за неисполняемого mvnw/gradlew — chmod +x с подтверждением и повтор preflight)")
            self.state["mutation"] = not self.args.no_mutation
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
            self.say("  файлы выше уходят в модель вместе с кодом модуля: значения должны быть тестовыми, иначе — исключить до шага 5a")
        if stops:
            fixable = [re.match(r"chmod \+x (.+?)(?:\s{2}|$)", row["hint"]) for row in stops]
            if all(fixable) and not again:
                paths = [Path(match.group(1)) for match in fixable]
                if self.confirm("Сделать исполняемыми файлы проекта " + ", ".join(str(path) for path in paths) + "?", "yes-chmod"):
                    for path in paths:
                        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                        self.say(f"  chmod +x {path}")
                    return self.step_1(again=True)
            raise Stop(f"preflight: «стоп» {len(stops)}", "исправить по подсказкам выше (линтер — строка excludes из подсказки, на ветке прогона) и запустить --from-step 1")
        pit_missing = [row for row in warns if "--mutation" in row["hint"]]
        self.state["mutation"] = not self.args.no_mutation and not pit_missing
        if pit_missing and not self.args.no_mutation:
            self.say(f"  мутации выключены: в ~/.m2 нет {', '.join(row['check'].split(': ', 1)[-1] for row in pit_missing)}")
        self.say(f"  мутации: {'да' if self.state['mutation'] else 'нет'}")

    # -- step 2
    def passthrough(self) -> list[str]:
        """--env-passthrough: the variables the model CLI needs beyond wrap.py's base list (names, or prefixes ending in *)."""
        items = [item.strip() for item in (self.args.env_passthrough or "").split(",") if item.strip()]
        bad = [item for item in items if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\*?", item)]
        if bad:
            raise Stop(f"--env-passthrough: не имя и не префикс со * в конце: {', '.join(bad)}", self.command(getattr(self, "current_step", "2")) +
                       " --env-passthrough 'GIGACODE_*,HTTPS_PROXY'")
        return items

    def wrap_config(self) -> dict[str, Any]:
        command = shlex.split(self.args.cli_command)
        try:  # extra_env and sandbox_args set by hand in the current wrap.json stay (wrap.py rejects secrets in extra_env)
            current = json.loads((self.wrap_dir / "wrap.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = {}
        sandbox = shlex.split(self.args.sandbox_args) if self.args.sandbox_args is not None else list(current.get("sandbox_args") or [])
        config: dict[str, Any] = {"command": command, "prompt_via": self.args.prompt_via, "answer_from": self.args.answer_from,
                                  "timeout_seconds": self.args.cli_timeout, "extra_env": dict(current.get("extra_env") or {}),
                                  "sandbox_args": sandbox,
                                  "env_passthrough": self.passthrough() if self.args.env_passthrough is not None else list(current.get("env_passthrough") or [])}
        if self.tmp_dir:
            config["tmp_dir"] = str(self.tmp_dir)
        if self.args.answer_file:
            config["answer_file"] = self.args.answer_file
        if self.args.cli_version:
            config["version"] = self.args.cli_version
        return config

    def step_2(self) -> None:
        config = self.wrap_config()
        if not any("{model}" in item for item in config["command"]):
            self.say("  внимание: в --cli-command нет {model} — модель по ролям до CLI не дойдёт")
        if config["prompt_via"] == "arg":
            self.say("  внимание: prompt_via=arg — части ревью 126–552 КБ не пройдут одним аргументом (Linux: 128 КиБ); лучше stdin или file")
        if config["answer_from"] == "file" and "answer_file" not in config:
            self.say("  answer_file по умолчанию: {tmp}/answer.txt")
        if not config["sandbox_args"]:
            self.say("  внимание: --sandbox-args не задан — если у CLI есть флаг «без инструментов / read-only», передать его: "
                     "иначе модель внутри CLI может сама читать и менять файлы")
        path = self.wrap_dir / "wrap.json"
        self.action(f"записать {path}: {json.dumps(config, ensure_ascii=False)}")
        if not self.dry:
            if path.is_file() and not (self.wrap_dir / "wrap.json.orig").exists():
                shutil.copy2(path, self.wrap_dir / "wrap.json.orig")
            path.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        version = self.run([self.py, self.wrap_dir / "wrap.py", "--version"])
        if not self.dry:
            if version.code != 0:
                raise Stop("wrap.py --version не прошёл", " ".join(_main_lines(version.stderr, 2)) or "проверить wrap.json")
            self.state["host_cli_version"] = version.stdout.strip() or "unknown"
            self.say(f"  версия CLI: {self.state['host_cli_version']}")
        selftest = self.run([self.py, self.wrap_dir / "wrap.py", "--selftest", "--pack", self.pack], cwd=self.wrap_dir)
        if not self.dry:
            for line in _main_lines(selftest.stdout, 3):
                self.say("  " + line)
            if selftest.code != 0:
                raise Stop("селфтест обёртки не прошёл", "смотреть строки «стоп» в operate.log; обёртка или Python окружения")
        # the live call goes through the very command the driver will launch (the package's codex argv, interpreter first)
        answer = self.home / "probe-answer.txt"
        values = {"model": self.default_model, "last_message": str(answer)}
        probe = self.run([item.format(**values) if "{" in item else item for item in self.runner_command()], stdin=PROBE,
                         timeout=int(self.args.cli_timeout) + 60)
        if self.dry:
            return
        events = [_json_tail(line) for line in probe.stdout.splitlines()]
        errors = [event.get("message") for event in events if isinstance(event, dict) and event.get("type") == "error"]
        if probe.code != 0:
            raise Stop(f"живой вызов CLI ({self.default_model}) не прошёл: код {probe.code}" + (f": {errors[0][:300]}" if errors else ""),
                       "проверить --cli-command (запустить его руками с тем же вводом), --prompt-via/--answer-from, доступ к модели")
        text = answer.read_text(encoding="utf-8") if answer.is_file() else ""
        self.say(f"  ответ модели {self.default_model}: {text[:300]}")
        if not isinstance(_json_tail(text), dict):
            raise Stop("CLI ответил не JSON-объектом", "модель или обёртка теряет JSON: проверить --answer-from и формат вывода CLI")
        self.mark_first_call()
        usage = next((event.get("usage") for event in events if isinstance(event, dict) and event.get("type") == "turn.completed"), None)
        if usage:
            self.say(f"  токены: {json.dumps(usage, ensure_ascii=False)}")

    # -- step 3
    def step_3(self) -> None:
        out = self.home / f"bench-{datetime.date.today():%m%d}"
        done = self.run([self.py, HERE / "bench" / "bench.py", "--pack", self.pack, "--wrap-config", self.wrap_dir / "wrap.json",
                         "--models", ",".join(self.models), "--out", out], echo=True)
        proposal = {role: self.default_model for role in ROLES}
        if self.dry:
            self.say("  (таблица по bench-*.json; tc-reviewer/autotest-reviewer — по рекомендации бенча, остальные роли — модель по умолчанию; подтверждение y/n)")
            return
        if done.code != 0:
            raise Stop(f"бенчмарк упал (код {done.code})", " ".join(_main_lines(done.stderr, 2)) or "смотреть operate.log")
        data = json.loads(Path(str(out) + ".json").read_text(encoding="utf-8"))
        self.say("")
        self.say(f"  {'модель':24} {'роль':18} {'с 1-й попытки':>14} {'доля':>6} {'Б/токен':>8} {'время, с':>9}")
        for model in self.models:
            for role in REVIEW_ROLES:
                rows = [row for row in data["rows"] if row["model"] == model and row["role"] == role]
                if not rows:
                    continue
                first = sum(bool(row["accepted"] and row["accepted_try"] == 1) for row in rows)
                shares = [row["overlap"] for row in rows if row["overlap"] is not None]
                ratios = [row["bytes_per_input_token"] for row in rows if row["bytes_per_input_token"]]
                self.say(f"  {model:24} {role:18} {f'{first}/{len(rows)}':>14} {(sum(shares) / len(shares) if shares else 0):6.2f} "
                         f"{(sum(ratios) / len(ratios) if ratios else 0):8.2f} {sum(row['seconds'] for row in rows):9.1f}")
        for role in REVIEW_ROLES:
            chosen = data["recommendation"].get(role)
            if chosen:
                proposal[role] = chosen
            else:
                self.say(f"  внимание: для {role} ни одна модель не прошла схему с первой попытки на всех частях — предлагаю {self.default_model}; "
                         "лучше меньший --review-input-bytes или другая модель")
        for item in self.args.role_model:
            role, _, model = item.partition("=")
            if role not in ROLES or not model:
                raise Stop(f"--role-model {item}: роль одна из {', '.join(ROLES)}", "формат --role-model tc-reviewer=<модель>")
            proposal[role] = model
        self.say("  Предложение по ролям (правило RUN.md: схема с первой попытки, наибольшая доля совпадений с Sonnet):")
        for role in ROLES:
            self.say(f"    {role:18} {proposal[role]}")
        while not self.confirm("Принять модели по ролям?"):
            line = self.ask("Замены через запятую (роль=модель), пусто — остановиться: ")
            if not line:
                raise Stop("модели по ролям не подтверждены", "запустить --from-step 3 и подтвердить, или передать --role-model роль=модель")
            for item in line.split(","):
                role, _, model = item.strip().partition("=")
                if role in ROLES and model:
                    proposal[role] = model
                else:
                    self.say(f"  пропущено: {item.strip()} (роль одна из {', '.join(ROLES)})")
            for role in ROLES:
                self.say(f"    {role:18} {proposal[role]}")
        self.state["models"] = proposal

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
                    raise Stop(f"спека не читается: {path}", "--docs — путь к спеке внутри проекта (или абсолютный)")
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
            raise Stop(f"шаблон ID не компилируется: {error}", "передать --id-pattern '<регулярное выражение>'")
        if compiled.match(""):
            raise Stop("шаблон ID совпадает с пустой строкой", "шаблон должен описывать сам ID, например [OPVX]\\d\\d\\.")
        return pattern

    def _skillsrc_passthrough(self) -> list[str]:
        """review_runner.env_passthrough for the package's own CLI launch — only a package that knows the field (S6)."""
        if self.args.env_passthrough is None:
            return []
        schema = self.pack / "schemas" / "skillsrc.schema.json"
        if not (schema.is_file() and "env_passthrough" in schema.read_text(encoding="utf-8")):
            self.say("  --env-passthrough: записан только в wrap/wrap.json — эта версия пакета не знает review_runner.env_passthrough")
            return []
        return ["  env_passthrough:", *[f"    - {_q(item)}" for item in self.passthrough()]]

    def step_4(self) -> None:
        models = self.state.get("models") or {role: self.default_model for role in ROLES}
        if not self.state.get("models"):
            self.say(f"  модели по ролям не выбраны (шаг 3 не проходил) — все роли: {self.default_model}")
        skillsrc = self.project / ".skillsrc"
        if not skillsrc.is_file() and not self.dry:
            self.say("  в проекте нет .skillsrc — пакет создаст его при пробном прогоне; шаг 4 повторится перед полным прогоном")
            self.state["skillsrc_deferred"] = True
            return
        pattern = self._id_pattern()
        before = skillsrc.read_text(encoding="utf-8") if skillsrc.is_file() else ""
        lines = before.splitlines()
        lines = set_block(lines, "review_runner", ["review_runner:", "  preset: codex", "  models:",
                                                   *[f"    {role}: {_q(models[role])}" for role in ROLES],
                                                   f"  max_parallel: {self.args.max_parallel}", f"  timeout_seconds: {self.args.runner_timeout}",
                                                   *self._skillsrc_passthrough()])
        if pattern:
            lines = set_child(lines, "requirements", "id_pattern", _q(pattern))
        if self.state.get("mutation", not self.args.no_mutation):
            if _top_block(lines, "mutation") is None:
                lines = [*lines, "mutation:", "  enabled: true", "  threads: 2", "  timeout_seconds: 3600"]
            else:
                lines = set_child(lines, "mutation", "enabled", "true")
        # S2a: builds only from the local repository (Maven -o / Gradle --offline); --online leaves the network to the build
        schema = self.pack / "schemas" / "skillsrc.schema.json"
        if schema.is_file() and '"offline"' not in schema.read_text(encoding="utf-8"):
            self.say("  внимание: эта версия пакета не знает test.offline (нужен пакет с S2a) — сборка пойдёт онлайн; обновить пакет")
        else:
            lines, notes = set_modules_test(lines, "offline", "false" if self.args.online else "true", add=not self.args.online)
            for note in notes:
                self.say(f"  внимание: test.offline: {note}")
            self.say("  сборка: " + ("онлайн (--online)" if self.args.online else
                                     "offline — только ~/.m2; нет артефакта → BUILD_FAILED_BEFORE_TESTS / MUTATION_TOOL_MISSING"))
        # preflight «mvnw нет, есть mvn в PATH»: the wrapper adapter cannot run there — offer the system Maven adapter
        modules = self.skillsrc_modules() if skillsrc.is_file() else None
        if modules and shutil.which("mvn"):
            no_wrapper = [module["id"] for module in modules if module["test"].get("adapter_id") == WRAPPER_ADAPTER and not any(
                (base / str(module["test"].get("wrapper") or "mvnw")).is_file() for base in (self.project / module["root"], self.project))]
            if no_wrapper:
                lines, edited = set_system_maven(lines, no_wrapper)
                self.say(f"  обёртки Maven нет, mvn есть в PATH ({shutil.which('mvn')}): модули {', '.join(edited or no_wrapper)} — "
                         f"adapter_id {SYSTEM_MAVEN}, executable: mvn" + ("" if edited else " (не удалось найти строки — поправить руками)"))
        after = "\n".join(lines) + "\n"
        if after == before:
            self.say("  .skillsrc уже такой — без изменений")
            self.state.pop("skillsrc_deferred", None)
            self.check_module()
            return
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), ".skillsrc", ".skillsrc (новый)", lineterm="", n=1):
            self.say("  " + line)
        if not self.confirm(f"Записать {skillsrc}?", "yes-skillsrc"):
            raise Stop(".skillsrc не записан", "поправить руками по RUN.md п. 4 и запустить --from-step 5a "
                       "(или повторить --from-step 4 и ответить y; без терминала — флаг --yes-skillsrc)")
        self.action(f"копия прежнего .skillsrc → {self.home}/skillsrc.bak-<время>; запись {skillsrc}")
        if not self.dry:
            shutil.copy2(skillsrc, self.home / f"skillsrc.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
            skillsrc.write_text(after, encoding="utf-8")
        self.state.pop("skillsrc_deferred", None)
        if not self.dry:
            self.check_module()

    # -- steps 5a and 5
    def driver_argv(self, profile: str, extra: list[str]) -> list[str]:
        """The run's start as ``next --review-runner process``: the driver scans and returns the first task without
        launching a model, so its size is checked first (``resume_argv`` then drives the same run with ``run --run``)."""
        host_cli = Path(shlex.split(self.args.cli_command)[0]).name
        command = self.runner_command() if self.pack.is_dir() or not self.dry else [self.py, str(self.wrap_dir / "wrap.py"), "exec", "…"]
        return [self.py, "-m", "tools.pipeline_driver", "next", "--project", str(self.project), "--review-runner", "process", "--profile", profile,
                *self._docs_args(), "--subject", self.args.subject, "--model-id", self.default_model, "--host-cli", host_cli,
                "--host-cli-version", self.state.get("host_cli_version") or self.args.cli_version or "unknown", "--reviewer-isolation", "fresh",
                "--review-runner-preset", "codex", "--review-runner-command", json.dumps(command, ensure_ascii=False),
                "--runner-dir", str(self.runner_dir), "--review-input-bytes", str(self.args.review_input_bytes), "--max-tasks", str(self.args.max_tasks),
                *(["--module", self.args.module] if self.args.module else []), *(["--target", self.args.target] if self.args.target else []),
                *(["--execution-review", self.args.execution_review] if self.args.execution_review else []),
                *(["--task-input-bytes", str(self.task_input_bytes())] if self.driver_knows("--task-input-bytes") else []),
                *(["--confirm-context", self.args.confirm_context] if self.args.confirm_context else []), *extra]

    def resume_argv(self, run_id: str, answers: list[str]) -> list[str]:
        return [self.py, "-m", "tools.pipeline_driver", "run", "--project", str(self.project), "--runner", "process", "--run", run_id,
                "--max-tasks", str(self.args.max_tasks), *[item for answer in answers for item in ("--answer", answer)]]

    def task_input_bytes(self) -> int:
        """The driver's task input budget: --task-input-bytes, else 0.8 of the model window in bytes (P1b stops above it)."""
        return int(self.args.task_input_bytes or self.args.model_context / self.args.tokens_per_byte * 0.8)

    def driver_knows(self, flag: str) -> bool:
        path = self.pack / "tools" / "pipeline_driver.py"
        return path.is_file() and f'"{flag}"' in path.read_text(encoding="utf-8", errors="replace")

    def check_task_size(self, payload: dict[str, Any]) -> None:
        """Before the first model call: the task's input (its files, SKILL and schema — what goes to stdin) in tokens
        against the model's context; over it — stop with the --target hint instead of sending it."""
        tasks = payload.get("tasks") if payload.get("action") == "batch" else [payload]
        rows = []
        for task in tasks or []:
            size = int(task.get("input_bytes") or 0) + sum(Path(task[key]).stat().st_size for key in ("skill_path", "schema_path")
                                                           if task.get(key) and Path(task[key]).is_file())
            rows.append((str(task.get("task_id")), size))
        if not rows:
            return
        tpb, context = self.args.tokens_per_byte, self.args.model_context
        budget = int(context / tpb)
        for task_id, size in rows:
            tokens = int(size * tpb)
            self.say(f"  {'первая задача модели' if len(rows) == 1 else f'задача модели (первые {len(rows)} — частями)'} {task_id}: вход ≈ {size:,} Б ≈ {tokens:,} токенов ({tpb} ток/Б); окно {context:,} токенов "
                     f"≈ {budget:,} Б".replace(",", " "))
            if tokens > context:
                raise Stop(f"задача {task_id} не влезет в окно модели: {tokens / context:.2f}× (≈ {tokens:,} > {context:,} токенов); модель не вызывалась"
                           .replace(",", " "),
                           "сузить вход: --target <каталог фичи> (обязателен для проектов больше одного модуля / 1 МБ), --module, paths модуля в "
                           f".skillsrc; другая модель — --model-context/--tokens-per-byte. Заведённый прогон {payload.get('run_id')} не продолжать "
                           f"(можно удалить из {self.project / '.pilot-runs'})")

    def _driver_dirs(self) -> set[Path]:
        return set((self.project / ".pilot-runs").glob("*.driver")) if (self.project / ".pilot-runs").is_dir() else set()

    def status_line(self, started: float, before: set[Path], run_id: str | None) -> None:
        if run_id:
            driver = self.project / ".pilot-runs" / f"{run_id}.driver"
        else:
            fresh = sorted(self._driver_dirs() - before, key=lambda path: path.stat().st_mtime)
            driver = fresh[-1] if fresh else None
        events = []
        if driver is not None and (driver / "driver-log.jsonl").is_file():
            for line in (driver / "driver-log.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
                event = _json_tail(line)
                if isinstance(event, dict) and float(event.get("epoch") or 0) >= started - 1:
                    events.append(event)
        kinds = [event.get("event") for event in events]
        last = next((event.get("task_id") for event in reversed(events) if event.get("task_id")), "—")
        minutes, seconds = divmod(int(time.time() - started), 60)
        self.say(f"  [{minutes:3d}:{seconds:02d}] запущено {kinds.count('runner_launched')}, собрано {kinds.count('runner_collected')}, "
                 f"отказов {kinds.count('runner_failed')}, пауз по лимиту {kinds.count('runner_rate_limited')}; последняя задача: {last}")

    def run_watched(self, argv: list[str], label: str, run_id: str | None) -> tuple[int, Any]:
        self.say(f"$ {shlex.join(argv)}   (в {self.pack})")
        if self.dry:
            return 0, None
        out, err = self.home / f"{label}.stdout.json", self.home / f"{label}.stderr.txt"
        started, before = time.time(), self._driver_dirs()
        with out.open("wb") as stdout, err.open("wb") as stderr:
            process = subprocess.Popen(argv, cwd=self.pack, stdout=stdout, stderr=stderr, env=self.env)
            due = started + STATUS_SECONDS
            try:
                while process.poll() is None:
                    time.sleep(2)
                    if time.time() >= due:
                        self.status_line(started, before, run_id)
                        due += STATUS_SECONDS
            except KeyboardInterrupt:
                process.terminate()
                raise Stop("прогон прерван (Ctrl+C)", "продолжить тот же прогон: operate.py … --from-step <шаг> --run <run_id> (run_id — в "
                           f"{self.project}/.pilot-runs); каталог {self.runner_dir} не трогать")
        text = out.read_text(encoding="utf-8", errors="replace")
        self.log(f"stdout ({out}):\n{text}\nstderr:\n{err.read_text(encoding='utf-8', errors='replace')[-20000:]}")
        self.say(f"  код выхода: {process.returncode}; время {int(time.time() - started) // 60} мин")
        payload = _json_tail(text)
        if isinstance(payload, dict) and payload.get("action") == "error" and payload.get("code") == "DRIVER_INPUT":
            self._runner_dir_refused(str(payload.get("message")))
        if not isinstance(payload, dict):
            self._runner_dir_refused(err.read_text(encoding="utf-8", errors="replace")[-4000:])
            # `run --runner process` reports a DriverError as a traceback, not as JSON: its last line names the code
            raised = re.findall(r"^tools\.pipeline_driver\.DriverError: ([A-Z_]+): (.*)$", err.read_text(encoding="utf-8", errors="replace"), re.M)
            if raised:
                code, message = raised[-1][0], raised[-1][1].strip()
                step = getattr(self, "current_step", "5")
                if code == "TASK_INPUT_TOO_LARGE":
                    raise Stop(f"драйвер: {code}: {message[:300]} — модель не вызывалась; сузить вход новым прогоном",
                               self.command(step, "--target", "<каталог фичи внутри модуля>"))
                raise Stop(f"драйвер: {code}: {message[:400]}", f"смотреть {err}; код и message — в отчёт (п. 7)")
            tail = _main_lines(err.read_text(encoding="utf-8", errors="replace"), 3)
            raise Stop(f"драйвер не вернул JSON (код {process.returncode}): {' | '.join(tail)}", f"смотреть {err}; текст ошибки — в отчёт (п. 7)")
        return process.returncode, payload

    def _runner_dir_refused(self, text: str) -> None:
        """A package before S2b refuses a runner directory inside the project (as a JSON error or a traceback)."""
        if "--runner-dir must be outside the project" in text:
            raise Stop(f"эта версия пакета не принимает каталог раннера внутри проекта: {self.runner_dir}",
                       "обновить пакет до версии, где --runner-dir внутри проекта разрешён (S2b), или задать --runner-dir вне проекта "
                       f"(если политика это позволяет) и запустить тот же шаг заново; пустой прогон, который драйвер успел завести в "
                       f"{self.project / '.pilot-runs'}, можно удалить")

    def _runner_output_tail(self, run_id: str, lines: int = 30) -> None:
        outputs = sorted((self.project / ".pilot-runs" / run_id / "artifacts").glob("*/runner-output.txt"), key=lambda path: path.stat().st_mtime)
        if not outputs:
            self.say("  runner-output.txt не найден")
            return
        self.say(f"  хвост {outputs[-1]}:")
        for line in outputs[-1].read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
            self.say("    " + line[:300])

    def show_payload(self, payload: dict[str, Any], limit: int = 30) -> None:
        """Whatever else the stop carries besides question/options (file lists, digests …), as it comes."""
        for key, value in payload.items():
            if key in ASK_KNOWN_KEYS or value in (None, "", [], {}):
                continue
            if isinstance(value, list):
                self.say(f"  {key}: {len(value)}")
                for item in value[:limit]:
                    if isinstance(item, dict) and "size" in item and "path" in item:  # confirm-context: path, content_digest, size
                        self.say(f"    {item['path']}  {int(item['size']):,} Б".replace(",", " "))
                    elif isinstance(item, dict) and "path" in item:  # approve-generated-tests: path, content_digest
                        rest = {name: field for name, field in item.items() if name != "path"}
                        self.say(f"    {item['path']}  " + "  ".join(str(field) for field in rest.values())[:160])
                    else:
                        self.say("    " + (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))[:200])
                if len(value) > limit:
                    self.say(f"    … ещё {len(value) - limit} (полностью — {self.home / 'operate.log'})")
                    self.log(json.dumps(value[limit:], ensure_ascii=False))
            else:
                self.say(f"  {key}: " + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))[:300])
        sized = [row for value in payload.values() if isinstance(value, list) for row in value if isinstance(row, dict) and "size" in row]
        if sized:
            self.say(f"  итого: {len(sized)} файл(ов), {sum(int(row['size']) for row in sized):,} Б".replace(",", " "))

    def answer_ask(self, payload: dict[str, Any], name: str, run_id: str, step: str) -> str:
        """The answer to a driver stop: from its flag (one option only), else from the terminal; none — a stop whose hint
        is one ready command."""
        kind = next((key for key in ASK_STOPS if key in name), None)
        what, show, flag = ASK_STOPS.get(kind, ("вопрос драйвера", "payload", None))
        question = str(payload.get("question") or "")
        if any(isinstance(value, list) and value for key, value in payload.items() if key not in ASK_KNOWN_KEYS) and len(question) > 400:
            question = question[:400] + " … (полностью — в operate.log)"  # the list is shown below, item by item
            self.log(f"вопрос {name}: {payload.get('question')}")
        self.say(f"  остановка «{what}» ({name}): {question}")
        options = [option for option in payload.get("options") or [] if isinstance(option, dict)]
        for option in options:
            self.say("    - " + (" — ".join(str(option[key]) for key in ("value", "label", "description") if option.get(key)) or json.dumps(option, ensure_ascii=False)))
        if show == "runner-tail":
            self._runner_output_tail(run_id)
            raise Stop("тесты не прошли гейт сборки — перегенерацию сразу не делать; линтер/сборка проекта в хвосте выше — исправить "
                       "окружение (п. 1) и начать новый прогон, ошибки компиляции самих тестов — перегенерировать:",
                       self.command(step, "--run", run_id, "--answer", f"{name}=<значение из списка выше>"))
        self.show_payload(payload)
        given = dict(item.split("=", 1) for item in self.args.answer if "=" in item).get(name)
        if given is not None:  # e.g. init's --answer confirm-context=<digest>: valid only for the very same list
            if not options or given in {str(option.get("value")) for option in options}:
                self.say(f"  ответ {given} (--answer {name}=…)")
                return given
            self.say(f"  --answer {name}=… не совпадает с этой остановкой (список изменился) — нужен новый ответ")
        if flag and (self.dry or getattr(self.args, flag.replace("-", "_"))):
            digest = payload.get("approval_digest") or payload.get("confirmation_digest")  # S3/S5: exactly the digest of the shown list
            if len(options) == 1 and options[0].get("value") and (digest is None or options[0]["value"] == digest):
                self.say(f"  ответ {options[0]['value']} (--{flag})")
                return str(options[0]["value"])
            self.say(f"  --{flag} отвечает только остановку с одним вариантом, здесь их {len(options)} — нужен ответ человека")
        value = self.ask("Ответ (значение; пусто — остановиться): ")
        if value:
            return value
        sole = options[0].get("value") if len(options) == 1 else None
        raise Stop(f"прогон ждёт ответа: {what}",
                   self.command(step, "--run", run_id, f"--{flag}") if flag and sole else
                   self.command(step, "--run", run_id, "--answer", f"{name}=<значение>"))

    def drive(self, profile: str, extra: list[str], label: str) -> tuple[int, dict[str, Any] | None]:
        run_id, self.args.run = self.args.run, None  # --run resumes the first driven step only
        starting = not run_id  # until the first model task is sized, the run goes by `next`/`submit` (no process launched)
        if run_id:
            argv = self.resume_argv(run_id, self.args.answer)
        else:
            if not self.dry or self.pack.is_dir():
                self.check_module()
            argv = self.driver_argv(profile, extra)
        while True:
            code, payload = self.run_watched(argv, label, run_id)
            if self.dry:
                self.say(f"  (первая задача модели: вход × {self.args.tokens_per_byte} ток/Б против окна {self.args.model_context} — больше → стоп "
                         "с подсказкой --target; иначе тот же прогон: … run --project <проект> --runner process --run <run_id>)")
                self.say("  (ask_user — показать вопрос и всё, что несёт остановка; confirm-context отвечает --yes-send, approve-generated-tests — "
                         "--yes-exec (только при одном варианте, --yes не отвечает); иначе ответ с терминала или стоп с готовой командой "
                         "… --from-step <шаг> --run <run_id> --answer <метка>=<значение>)")
                self.say("  (ask regenerate-after-gate или BUILD_FAILED_BEFORE_TESTS — хвост runner-output.txt и стоп)")
                return 0, None
            run_id = payload.get("run_id") or run_id
            self.state[f"{label}_run_id"] = run_id
            self.save_state()
            action = payload.get("action")
            if starting and action in {"llm", "batch", "wait"} and run_id:  # nothing launched yet
                if action != "wait":
                    self.check_task_size(payload)
                starting = False
                argv = self.resume_argv(run_id, [])
                continue
            if action == "done":
                if not run_id:
                    result = payload.get("result") or {}
                    raise Stop(f"прогон не заведён: {result.get('reason_code') or result.get('status') or 'нет run_id'} "
                               f"{str(result.get('message') or result.get('reason') or '')[:300]}".rstrip(),
                               f"смотреть {self.home / (label + '.stdout.json')}; reason_code — в отчёт (п. 7)")
                return code, payload
            if action == "ask_user":
                task_id = str(payload.get("task_id") or "")
                name = task_id.split(".ask.", 1)[1] if ".ask." in task_id else task_id
                value = self.answer_ask(payload, name, run_id, "5" if label == "run" else "5a")
                argv = [self.py, "-m", "tools.pipeline_driver", "submit", "--project", str(self.project), "--run", run_id, "--task-id", task_id,
                        "--answer", value] if starting else self.resume_argv(run_id, [f"{name}={value}"])
                continue
            if action == "error" and "module_required" in json.dumps(payload, ensure_ascii=False):
                modules = self.skillsrc_modules() or []
                raise Stop("драйвер требует модуль: в .skillsrc их несколько, а --module не задан ("
                           + ", ".join(f"{module['id']} ({module['root']})" for module in modules) + ")", "передать --module <id> из списка")
            if action == "error":
                hint = "DRIVER_FAILURE — дефект пакета: код и message в отчёт (п. 7)" if payload.get("code") == "DRIVER_FAILURE" else \
                    "код и message — в отчёт (п. 7); RUNNER_* — смотреть stderr.txt и last-message.txt в каталоге раннера"
                raise Stop(f"драйвер: {payload.get('code')}: {str(payload.get('message'))[:400]}", hint)
            raise Stop(f"неожиданный ответ драйвера: action={action}, код {code}", f"смотреть {self.home / (label + '.stdout.json')}")

    def _log_events(self, run_id: str) -> list[dict[str, Any]]:
        path = self.project / ".pilot-runs" / f"{run_id}.driver" / "driver-log.jsonl"
        if not path.is_file():
            return []
        return [event for event in (_json_tail(line) for line in path.read_text(encoding="utf-8", errors="replace").splitlines()) if isinstance(event, dict)]

    def step_5a(self) -> None:
        code, payload = self.drive("cases-only-v1", [], "trial")
        if self.dry:
            return
        result = payload["result"]
        self.mark_first_call_of(payload["run_id"])
        events = self._log_events(payload["run_id"])
        failed: dict[str, int] = {}
        for event in events:
            if event.get("event") == "runner_failed":
                key = str(event.get("reason") or event.get("failure_class"))
                failed[key] = failed.get(key, 0) + 1
        collected = [event for event in events if event.get("event") == "runner_collected"]
        parts = {re.search(r"review\.canonical\.(part-\d+)", str(event.get("task_id"))).group(1) for event in collected
                 if re.search(r"review\.canonical\.(part-\d+)", str(event.get("task_id")))}
        cases = _count_cases(result, self.project)
        self.say(f"  итог: completion {result.get('completion')}, код {code} (у cases-only-v1 код 1 — норма), run_id {payload['run_id']}")
        self.say(f"  задач модели: запущено {sum(event.get('event') == 'runner_launched' for event in events)}, собрано {len(collected)}, "
                 f"повторов {sum(int(event.get('try') or 1) > 1 for event in collected)}; частей ревью кейсов {len(parts)}; кейсов {cases[0] if cases else '—'}")
        self.say("  отказы раннера: " + (", ".join(f"{key} ×{value}" for key, value in sorted(failed.items())) or "нет"))
        if result.get("completion") != "COMPLETE":
            raise Stop(f"пробный прогон не завершён: {result.get('completion')} {result.get('reason_code')}", "reason_code — в отчёт; RUN.md п. 6")

    def step_5(self) -> None:
        extra = ["--analyst-report", "--suite", *(["--mutation"] if self.state.get("mutation", not self.args.no_mutation) else [])]
        code, payload = self.drive("local-pilot-v1", extra, "run")
        if self.dry:
            return
        result = payload["result"]
        self.mark_first_call_of(payload["run_id"])
        (self.home / "run-result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        self.state["run_id"] = payload["run_id"]
        strength = result.get("test_strength") or {}
        self.say(f"  итог: completion {result.get('completion')}, verification {result.get('verification')}, accepted {result.get('accepted')}, "
                 f"reason {result.get('reason_code')}, код {code}")
        self.say(f"  набор: {json.dumps(result.get('suite'), ensure_ascii=False)}; мутации: {strength.get('status')} {strength.get('score')}")
        if result.get("reason_code") == "BUILD_FAILED_BEFORE_TESTS":
            failure = result.get("build_failure") or {}
            self.say("  " + str(failure.get("message")))
            for line in failure.get("output_tail") or []:
                self.say("    " + line)
            raise Stop("сборка проекта упала до тестов", "исправить окружение проекта (п. 1) и начать новый прогон: --from-step 5")
        if result.get("completion") != "COMPLETE":
            raise Stop(f"прогон не завершён: {result.get('completion')} {result.get('reason_code')}", "reason_code — в отчёт (шаг 7 соберёт его): --from-step 7")
        if result.get("verification") == "FAIL":
            self.say("  verification FAIL — ожидаемо при дефекте продукта: quarantine.md (вопрос аналитику и черновик баг-репорта)")

    # -- step 7
    def _run_payload(self) -> dict[str, Any]:
        path = self.home / "run-result.json"
        if self.dry and not path.is_file():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise Stop(f"нет итога прогона {path}", "сначала шаг 5 (полный прогон)")

    def step_7(self) -> None:
        payload = self._run_payload()
        self.action(f"собрать {self.report}: driver-log.jsonl, config.json (без секретов), run-result.json, errors.txt, runner-output-errors.txt, "
                    "test-strength.md, summary.md (без кода, спеки, кейсов, ответов моделей, stdin.txt)")
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
                  for event in events if event.get("event") in {"error", "runner_failed", "runner_rate_limited", "triage_failed"}]
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
            figures += _first_call_figures((self.home / "operate.log").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
        table = ["| Показатель | Значение |", "| --- | --- |", *[f"| {name} | {value} |" for name, value in figures]]
        (self.report / "summary.md").write_text(f"# Внутренний прогон {run_id}\n\n" + "\n".join(table) + "\n", encoding="utf-8")
        copied.append("summary.md")
        self.say(f"  в {self.report}: {', '.join(copied)}")
        width = max(len(name) for name, _value in figures)
        for name, value in figures:
            self.say(f"  {name:<{width}}  {value}")

    # -- step 8
    def step_8(self) -> None:
        payload = self._run_payload()
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

    # -- loop
    def main(self) -> int:
        self.log(f"\n===== operate.py {datetime.datetime.now().isoformat(timespec='seconds')} {shlex.join(sys.argv[1:])}")
        steps = STEPS[STEPS.index(self.args.from_step):]
        if self.args.only:
            wanted = [item.strip() for item in self.args.only.split(",")]
            steps = tuple(step for step in STEPS if step in wanted)
        for step in steps:
            if step == "5" and self.state.get("skillsrc_deferred") and not self.dry:
                if not self.run_step("4"):
                    return 1
            if not self.run_step(step):
                return 1
        self.say("\nГотово." + ("" if self.dry else f" Журнал: {self.home / 'operate.log'}"))
        return 0

    def run_step(self, step: str) -> bool:
        self.current_step = step
        self.say(f"\n=== Шаг {step}. {TITLES[step]} ===")
        try:
            getattr(self, "step_" + step)()
        except Stop as stop:
            self.say(f"СТОП на шаге {step}: {stop.message}")
            self.log(f"stop_at {datetime.datetime.now().isoformat(timespec='milliseconds')}")
            if stop.hint:
                self.say(f"Что делать: {stop.hint}")
            if not stop.hint.startswith(shlex.quote(sys.executable)):
                self.say(f"Продолжить после исправления: {self.command(step)}")
            self.save_state()
            return False
        self.save_state()
        return True


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
            checks = sum(len(expectation.get("assertions") or []) for case in cases for step in case.get("steps") or []
                         for expectation in step.get("expectations") or [])
            return len(cases), checks
    return None


FIRST_CALL = "first_model_call"
TEXT_SUFFIXES = {".java", ".kt", ".kts", ".groovy", ".scala", ".py", ".js", ".ts", ".tsx", ".jsx", ".xml", ".yml", ".yaml", ".properties",
                 ".json", ".sql", ".html", ".css", ".md", ".txt", ".csv", ".gradle", ".conf", ".jsp", ".ftl", ".vm"}
CODE_SUFFIXES = {".java", ".kt", ".kts", ".groovy", ".scala", ".py", ".js", ".ts", ".tsx", ".jsx"}
GENERIC_DIRS = {"src", "main", "java", "kotlin", "scala", "groovy", "test", "tests", "resources", "org", "com", "net", "io", "app", "springframework",
                "samples", "static", "templates", "python", "source", "sources", "spec", "docs", "requirements", "файл", "файлы", "требования"}
SKIP_DIRS = {".git", ".pilot-runs", "target", "build", "node_modules", ".gradle", ".idea", ".vscode", ".mvn", "__pycache__"}


# ---------------------------------------------------------------- init: the first launch (S10)

class Init(Operator):
    """``operate.py init``: steps 0–2 and the size of the first model task in one go, then one ready command.

    Writes nothing to the project: the measuring run it starts (``next``, no model) is removed by its id afterwards.
    Every problem is one line «что сделать» with a ready command; the first blocking one stops."""

    def __init__(self, args: argparse.Namespace, init_argv: list[str]) -> None:
        super().__init__(args)
        self.init_argv = init_argv
        self.started = time.time()
        self.decisions: list[str] = []
        self.later: list[str] = []
        self.facts: dict[str, Any] = {}

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
        self.say(f"init: проект {self.project}, спека {', '.join(self.args.docs)}, тема: {self.args.subject}")
        for title, part in (("комплект и интерпретатор", self.step_0), ("модуль и сборка", self.init_module), ("CLI модели", self.init_cli),
                            ("цель контекста (--target)", self.init_target), (".skillsrc проекта (шаг 4)", self.init_skillsrc),
                            ("размер первой задачи", self.init_measure),
                            ("проверки проекта", self.init_preflight)):
            self.say(f"\n=== init: {title} ===")
            try:
                part()
            except Stop as stop:
                self.say(f"СТОП на шаге init: {stop.message}")
                self.log(f"stop_at {datetime.datetime.now().isoformat(timespec='milliseconds')}")
                self.say(f"Что делать: {stop.hint}")
                self.save_init()
                return 1
        self.finish()
        return 0

    def save_init(self) -> None:
        self.facts.update({"decisions": self.decisions, "later": self.later, "seconds": int(time.time() - self.started)})
        (self.home / "init.json").write_text(json.dumps(self.facts, ensure_ascii=False, indent=1), encoding="utf-8")

    # -- module and build
    def init_module(self) -> None:
        modules = self.skillsrc_modules()
        if modules is None:
            done = subprocess.run([self.py, "-m", "tools.init_skillsrc", "--project", str(self.project)], cwd=self.pack, capture_output=True,
                                  env=self.env, text=True, encoding="utf-8", errors="replace", timeout=300, check=False)
            self.log("init_skillsrc (без записи):\n" + done.stdout + done.stderr)
            proposal = _json_tail(done.stdout) or {}
            ids = [str(item) for item in proposal.get("module_ids") or []]
            questions = [str(item.get("field") or item.get("id")) for item in proposal.get("questions") or [] if isinstance(item, dict)]
            self.say(f"  .skillsrc нет — его создаст пакет при первом прогоне; модули по разбору пакета: {', '.join(ids) or '—'}")
            if questions:
                self.decisions.append("ответ на вопросы пакета при создании .skillsrc: " + ", ".join(questions))
                self.facts["skillsrc_questions"] = questions
            modules = [{"id": item, "root": ".", "test": {}} for item in ids] or [{"id": "root", "root": ".", "test": {}}]
            self.facts["skillsrc"] = "будет создан"
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
        self.args.module = module["id"] if len(modules) > 1 else self.args.module
        self.module = module
        root = self.project / str(module.get("root") or ".")
        wrapper = next((name for name in ("mvnw", "gradlew") if (root / name).is_file() or (self.project / name).is_file()), None)
        build = wrapper or ("mvn в PATH" if shutil.which("mvn") else None)
        self.say(f"  модуль {module['id']} ({module.get('root') or '.'}); сборка: {build or 'не найдена'}")
        if not build:
            self.later.append("сборка: нет mvnw/gradlew и mvn в PATH — нужна до полного прогона (шаг 5)")
        m2 = Path.home() / ".m2" / "repository"
        if not m2.is_dir():
            self.later.append(f"нет {m2}: сборка пойдёт online (--online) или собрать проект один раз с доступом к репозиторию")
        self.facts.update({"module": module["id"], "module_root": module.get("root") or ".", "build": build})

    # -- CLI
    def init_cli(self) -> None:
        command = shlex.split(self.args.cli_command)
        if not any("{model}" in item for item in command):
            raise Stop("в --cli-command нет {model}", "добавьте {model} в --cli-command: " + self.init_command(cli_command=self.args.cli_command + " --model {model}"))
        if any(mark in item for item in command for mark in ("{prompt_file}", "{answer_file}")):
            forms = [("file" if any("{prompt_file}" in item for item in command) else "stdin",
                      "file" if any("{answer_file}" in item for item in command) else "stdout", command)]
        elif any("{prompt}" in item for item in command):
            forms = [("arg", "stdout", command)]
        else:
            forms = [("stdin", "stdout", command), ("arg", "stdout", [*command, "{prompt}"])]
        last = ""
        for prompt_via, answer_from, argv in forms:
            self.args.prompt_via, self.args.answer_from = prompt_via, answer_from
            config = {**self.wrap_config(), "command": argv}
            ok, last = self._probe(config)
            self.say(f"  форма {prompt_via} → {answer_from}: {'ответ JSON' if ok else last[:200]}")
            if ok:
                path = self.wrap_dir / "wrap.json"
                if path.is_file() and not (self.wrap_dir / "wrap.json.orig").exists():
                    shutil.copy2(path, self.wrap_dir / "wrap.json.orig")
                path.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
                self.mark_first_call()
                if argv is not command:  # the arg form appended {prompt}
                    self.args.cli_command += " {prompt}"
                self.facts["cli_form"] = f"{prompt_via}/{answer_from}"
                self.facts["first_model_call_seconds"] = int(time.time() - self.started)
                if prompt_via == "arg":
                    self.later.append("CLI принимает задачу только аргументом: задачи больше 128 КиБ не пройдут — нужен stdin или файл")
                if not config["sandbox_args"]:
                    self.later.append("--sandbox-args не задан: если у CLI есть режим «без инструментов / read-only» — передать его")
                return
        raise Stop(f"CLI не ответил JSON ни в одной форме ({len(forms)}): {last[:300]}",
                   "проверьте вызов руками: echo '{\"ok\": true}' | " + shlex.join([self.py, str(self.wrap_dir / "wrap.py"), "exec", "--json", "--model",
                                                                                  self.default_model, "--output-last-message", str(self.home / "w.txt"), "-"]))

    def _probe(self, config: dict[str, Any]) -> tuple[bool, str]:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="init-cli-") as temp:
            path, answer = Path(temp) / "wrap.json", Path(temp) / "answer.txt"
            path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
            values = {"model": self.default_model, "last_message": str(answer)}
            argv = [item.format(**values) if "{" in item else item for item in self.runner_command()]
            self.log(f"$ WRAP_CONFIG={path} {shlex.join(argv)}")
            try:
                done = subprocess.run(argv, input=PROBE, capture_output=True, env={**self.env, "WRAP_CONFIG": str(path)}, text=True,
                                      encoding="utf-8", errors="replace", timeout=int(self.args.cli_timeout) + 60, check=False)
            except subprocess.TimeoutExpired:
                return False, f"нет ответа за {self.args.cli_timeout} с"
            self.log("stdout:\n" + done.stdout + "\nstderr:\n" + done.stderr)
            text = answer.read_text(encoding="utf-8") if answer.is_file() else ""
            if done.returncode == 0 and isinstance(_json_tail(text), dict):
                self.say(f"  ответ модели {self.default_model}: {text.strip()[:200]}")
                return True, ""
            errors = [event.get("message") for event in (_json_tail(line) for line in done.stdout.splitlines())
                      if isinstance(event, dict) and event.get("type") == "error"]
            return False, f"код {done.returncode}: " + (errors[0] if errors else (text.strip() or done.stderr.strip())[:300] or "пустой ответ")

    # -- target
    def _module_files(self) -> list[tuple[str, int]]:
        """(path relative to the module root, bytes) of the module's text files — git's list when there is one."""
        root = self.project / str(self.module.get("root") or ".")
        listed = subprocess.run(["git", "-C", str(root), "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True, check=False)
        names = [name for name in listed.stdout.decode("utf-8", "replace").split("\0") if name] if listed.returncode == 0 else \
            [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
        rows = []
        for name in names:
            parts = name.split("/")
            if any(part in SKIP_DIRS for part in parts[:-1]) or Path(name).suffix.lower() not in TEXT_SUFFIXES:
                continue
            path = root / name
            if self.home.resolve() in path.resolve().parents or not path.is_file():
                continue
            size = path.stat().st_size
            if size < 1_000_000:
                rows.append((name, size))
        return rows

    def init_target(self) -> None:
        budget = int(self.args.model_context / self.args.tokens_per_byte)
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
        self.say(f"  текстовых файлов модуля {len(files)}, {total:,} Б ≈ {int(total * self.args.tokens_per_byte):,} токенов; окно ≈ {budget:,} Б"
                 .replace(",", " "))
        if total <= budget * 0.8:
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
        limit = int(budget * 0.6)
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
            raise Stop(f"модуль {total:,} Б больше окна модели, а каталога исходников меньше {limit:,} Б не нашлось".replace(",", " "),
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
        raise Stop(f"модуль ({total:,} Б) не влезет в окно модели целиком — выберите каталог фичи из списка выше".replace(",", " "),
                   self.init_command(target=ranked[0]))

    # -- step 4 before the measurement: .skillsrc is itself in the context, so its final form is what gets confirmed
    def init_skillsrc(self) -> None:
        try:
            self.step_4()
        except Stop as stop:
            if ".skillsrc не записан" in stop.message:
                raise Stop(".skillsrc не записан (diff выше): без терминала — подтверждение флагом", self.init_command() + " --yes-skillsrc") from None
            raise

    # -- the first task, measured without a model
    def _git_status(self) -> str:
        done = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=False)
        return done.stdout if done.returncode == 0 else ""

    def init_measure(self) -> None:
        before, skillsrc_existed = self._git_status(), (self.project / ".skillsrc").is_file()
        runs_existed = (self.project / ".pilot-runs").is_dir()
        code, payload = self.run_watched(self.driver_argv("cases-only-v1", []), "init-measure", None)
        run_id = payload.get("run_id")
        try:
            if payload.get("action") == "ask_user" and str(payload.get("task_id") or "").endswith(".ask.confirm-context") and run_id:
                payload = self._measure_past_confirmation(payload, run_id)
            action = payload.get("action")
            if action in {"llm", "batch"}:
                try:
                    self.check_task_size(payload)
                except Stop as stop:
                    self.decisions.append("--target")
                    raise Stop(stop.message, self.init_command(target="<каталог фичи внутри модуля, уже чем сейчас>")) from None
            elif action == "ask_user":
                name = str(payload.get("task_id") or "").split(".ask.", 1)[-1]
                self.say(f"  драйвер до первой задачи спрашивает «{name}»: {payload.get('question')}")
                self.show_payload(payload)
                if not (name.startswith("skillsrc.") and self.facts.get("skillsrc_questions")):  # the same question init_skillsrc listed
                    self.decisions.append(f"ответ на вопрос драйвера «{name}» (спросит пробный прогон)")
                self.facts["first_stop"] = name
            elif action == "error" or (action == "done" and not run_id):
                result = payload.get("result") or {}
                raise Stop(f"драйвер не завёл прогон: {payload.get('code') or result.get('reason') or result.get('reason_code')}: "
                           f"{str(payload.get('message') or result.get('message') or '')[:300]}",
                           self.init_command(target="<каталог фичи>") if "TARGET" in json.dumps(payload) else f"смотреть {self.home / 'init-measure.stdout.json'}")
            else:
                self.say(f"  драйвер ответил {action} — размер замерит шаг 5a до вызова модели")
            self.facts["first_task"] = {key: payload.get(key) for key in ("task_id", "input_bytes") if payload.get(key)}
        finally:
            self._remove_measuring_run(run_id, runs_existed, skillsrc_existed, before)

    def _measure_past_confirmation(self, payload: dict[str, Any], run_id: str) -> dict[str, Any]:
        """S5 in the measuring run: show what the run will send and ask the person here (y/n; --yes-send answers).  Yes —
        the digest answers the measuring run (to reach and size the first task) and goes to the ready command as
        ``--answer confirm-context=<digest>``: the real run with the same files does not ask again, with other files it does.
        No — a stop: narrow the input."""
        rows = [row for key in ("documents", "files") for row in payload.get(key) or [] if isinstance(row, dict)]
        total = sum(int(row.get("size") or 0) for row in rows)
        self.say(f"  в модель уйдут {len(payload.get('documents') or [])} документ(ов) требований и {len(payload.get('files') or [])} файл(ов) "
                 f"проекта, {total:,} Б ≈ {int(total * self.args.tokens_per_byte):,} токенов".replace(",", " "))
        listing = Path(str(payload.get("listing_path") or ""))
        if listing.is_file():
            shutil.copy2(listing, self.home / "context-files.txt")
            self.say(f"  полный список с размерами: {self.home / 'context-files.txt'}")
        self.facts["context"] = {"files": len(payload.get("files") or []), "documents": len(payload.get("documents") or []), "bytes": total}
        digest = str(payload.get("confirmation_digest") or "")
        if not self.confirm(f"Отправлять эти файлы в модель (список выше и в {self.home / 'context-files.txt'})?", "yes-send"):
            self.decisions.append("отправка в модель: не подтверждена")
            raise Stop("отправка файлов в модель не подтверждена — сузить вход и повторить init",
                       self.init_command(target="<каталог фичи внутри модуля>") + "  (или исключить файлы: paths модуля в .skillsrc, .gitignore)")
        self.facts["confirmation_digest"] = digest
        self.log(f"init: отправка подтверждена человеком ({digest}); ответ — в замерный прогон {run_id} и в готовую команду")
        _code, after = self.run_watched([self.py, "-m", "tools.pipeline_driver", "submit", "--project", str(self.project), "--run", run_id,
                                         "--task-id", str(payload["task_id"]), "--answer", digest], "init-measure", run_id)
        return after

    def _remove_measuring_run(self, run_id: str | None, runs_existed: bool, skillsrc_existed: bool, before: str) -> None:
        runs = self.project / ".pilot-runs"
        if run_id and re.fullmatch(r"[0-9a-f]{32}", str(run_id)):
            for path in (runs / run_id, runs / f"{run_id}.driver", self.runner_dir / run_id):
                if path.is_dir():
                    shutil.rmtree(path)
        if not runs_existed and runs.is_dir() and not any(runs.iterdir()):
            runs.rmdir()
        if not skillsrc_existed and (self.project / ".skillsrc").is_file():
            (self.project / ".skillsrc").unlink()
        after = self._git_status()
        self.say("  замерный прогон удалён; git status проекта " + ("как был" if after == before else "ИЗМЕНИЛСЯ:"))
        if after != before:
            for line in sorted(set(after.splitlines()) ^ set(before.splitlines()))[:10]:
                self.say("    " + line)

    # -- quiet preflight
    def init_preflight(self) -> None:
        report = self.home / "preflight.json"
        argv = [self.py, HERE / "preflight.py", "--pack", self.pack, "--project", self.project, *self._docs_args(), "--json", report]
        if self.in_project:
            argv += ["--exclude-dir", self.home.relative_to(self.project).as_posix()]
        done = subprocess.run([str(item) for item in argv], capture_output=True, env=self.env, text=True, encoding="utf-8", errors="replace",
                              timeout=900, check=False)
        self.log("preflight:\n" + done.stdout + done.stderr)
        try:
            rows = json.loads(report.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.later.append(f"preflight не записал отчёт (код {done.returncode}) — запустить шаг 1")
            return
        sensitive = [row for row in rows if row["check"].startswith("ПДн/секреты") and row["status"] != "ок"]
        for row in sensitive:
            self.say(f"  [{row['status']}] {row['check']}: {row['finding']}")
        if sensitive:
            self.decisions.append("отправка в модель файлов с ПДн/секретами из списка выше (или исключить их: .gitignore / paths модуля)")
        rest = [row for row in rows if row["status"] in {"стоп", "внимание"} and row not in sensitive]
        for row in rest:
            self.later.append(f"{row['check']}: {row['finding'][:120]}")
        self.say(f"  до первого прогона мешает: {'ПДн/секреты — решение ниже' if sensitive else 'ничего'}; до полного прогона — {len(rest)} пунктов (ниже)")

    # -- the result
    def finish(self) -> None:
        self.state["host_cli_version"] = self.args.cli_version or "unknown"
        self.save_state()
        argv = ["--home", str(self.home), "--project", str(self.project), *self._docs_args(), "--subject", self.args.subject,
                "--models", self.default_model, "--cli-command", self.args.cli_command, "--prompt-via", self.args.prompt_via,
                "--answer-from", self.args.answer_from, *(["--module", self.args.module] if self.args.module else []),
                *(["--target", self.args.target] if self.args.target else []),
                *(["--cli-version", self.args.cli_version] if self.args.cli_version else []),
                *(["--answer", f"confirm-context={self.facts['confirmation_digest']}"] if self.facts.get("confirmation_digest") else []),
                "--only", "5a"]
        command = shlex.join([sys.executable, str(Path(sys.argv[0]).resolve()), *argv])
        self.facts["command"] = command
        self.save_init()
        kpi = dict(_first_call_figures((self.home / "operate.log").read_text(encoding="utf-8", errors="replace")))
        self.say("\n=== init: готово ===")
        self.say(f"Запуск (шаги 0–2 и 4 init уже сделал): {command}")
        asks = [] if self.facts.get("confirmation_digest") else [
            "отправка в модель — остановка confirm-context покажет документы и файлы с размерами (список — context-files.txt); "
            "подтверждение — ввод дайджеста или --yes-send"]
        self.say("Что ещё спросит прогон: " + ("; ".join(f"№{index} {text}" for index, text in enumerate([*self.decisions, *asks], 1)) or
                                              "ничего до первого ответа модели"))
        if self.later:
            self.say("До полного прогона (шаг 5), не сейчас:")
            for line in self.later:
                self.say(f"  - {line}")
        self.say(f"init: {int(time.time() - self.started) // 60} мин {int(time.time() - self.started) % 60} с; до первого вызова модели — "
                 f"{kpi.get('Время до первого вызова модели', '—')}, остановок до него — {kpi.get('Остановок до первого вызова модели', '—')}; "
                 f"решений впереди — {len(self.decisions) + len(asks)}")


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


def build_init_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="operate.py init", description="Первый запуск: комплект, модуль, CLI модели, цель контекста и размер "
                                                                         "первой задачи — и одна готовая команда прогона.")
    parser.add_argument("--project", required=True, help="корень проекта")
    parser.add_argument("--docs", action="append", required=True, help="спека (путь внутри проекта); можно несколько")
    parser.add_argument("--cli-command", required=True, help="команда CLI модели одной строкой, с {model}")
    parser.add_argument("--model", required=True, help="модель, как её называет CLI (одна; список — у --models шага 3)")
    parser.add_argument("--home", help="каталог пакета (там test-skills/); по умолчанию — папка operate.py или её родитель, где лежит test-skills/")
    parser.add_argument("--subject", help="тема прогона (по умолчанию — первый заголовок спеки)")
    parser.add_argument("--module", help="id модуля, если init не выбрал его сам")
    parser.add_argument("--target", help="каталог фичи внутри модуля, если init не угадал его сам")
    parser.add_argument("--cli-version", help="строка версии CLI для квитанций")
    parser.add_argument("--sandbox-args", help="флаги CLI «без инструментов / read-only»")
    parser.add_argument("--env-passthrough", help="переменные окружения для CLI сверх базового списка wrap.py (GIGACODE_*,HTTPS_PROXY)")
    parser.add_argument("--yes-send", action="store_true", help="подтвердить отправку показанного списка файлов в модель без вопроса")
    parser.add_argument("--yes-skillsrc", action="store_true", help="записать .skillsrc проекта (шаг 4) без вопроса; diff печатается")
    parser.add_argument("--yes", action="store_true", help="подтверждать вопросы оператора (шаблон ID требований); .skillsrc и отправку — нет")
    parser.add_argument("--runner-dir", help="каталог процессов модели")
    parser.add_argument("--tokens-per-byte", type=float, default=TOKENS_PER_BYTE)
    parser.add_argument("--model-context", type=int, default=MODEL_CONTEXT)
    return parser


def init_main(argv: list[str]) -> int:
    options = build_init_parser().parse_args(argv)
    home = options.home or next((str(path) for path in (HERE, HERE.parent) if (path / "test-skills").is_dir()), "~/ts")
    project = Path(options.project).expanduser().resolve()
    subject = options.subject or _subject(options.docs, project)
    main_argv = ["--home", home, "--project", str(project), *[item for doc in options.docs for item in ("--docs", doc)], "--subject", subject,
                 "--models", options.model, "--cli-command", options.cli_command, "--tokens-per-byte", str(options.tokens_per_byte),
                 "--model-context", str(options.model_context)]
    main_argv += [flag for flag, on in (("--yes-send", options.yes_send), ("--yes-skillsrc", options.yes_skillsrc), ("--yes", options.yes)) if on]
    for key in ("module", "target", "cli_version", "sandbox_args", "env_passthrough", "runner_dir"):
        if getattr(options, key) is not None:
            main_argv += [f"--{key.replace('_', '-')}", getattr(options, key)]
    return Init(build_parser().parse_args(main_argv), argv).run_init()


def _first_call_figures(log: str) -> list[tuple[str, str]]:
    """S10 KPI from operate.log: time from the first operate.py start (init or step 0) to the first answered model call,
    and the stops before it."""
    started = re.search(r"^===== operate\.py (\S+)", log, re.M)
    call = re.search(rf"^{FIRST_CALL} (\S+)", log, re.M)
    if not started:
        return []
    if not call:
        return [("Время до первого вызова модели", "— (вызова не было)"),
                ("Остановок до первого вызова модели", str(len(re.findall(r"^СТОП на шаге", log, re.M))))]
    first = datetime.datetime.fromisoformat(call.group(1))
    seconds = int((first - datetime.datetime.fromisoformat(started.group(1))).total_seconds())
    stamped = [datetime.datetime.fromisoformat(item) for item in re.findall(r"^stop_at (\S+)", log, re.M)]  # the mark may be written late
    stops = sum(item < first for item in stamped) if stamped else len(re.findall(r"^СТОП на шаге", log[started.start():call.start()], re.M))
    return [("Время до первого вызова модели", f"{seconds // 60} мин {seconds % 60} с"), ("Остановок до первого вызова модели", str(stops))]


def _figures(result: dict[str, Any], events: list[dict[str, Any]], run_root: Path, project: Path) -> list[tuple[str, str]]:
    cases = _count_cases(result, project)
    collected = {str(event.get("task_id")) for event in events if event.get("event") == "runner_collected"}
    canonical = {match.group(1) for task in collected if (match := re.search(r"review\.canonical\.(part-\d+)", task))}
    automation = {match.group(0) for task in collected if (match := re.search(r"review\.r\d+\.part-\d+", task))}
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
        ("Проверок (assertions)", str(cases[1]) if cases else "—"),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Внутренний прогон test-skills по RUN.md, шаг за шагом (шаги: " + ", ".join(STEPS) + ").")
    parser.add_argument("--home", default="~/ts", help="каталог пакета, раннера и отчёта (там test-skills/ и test-skills-*.files.sha256); "
                                                       "preflight.py, wrap/, bench/ берутся рядом с operate.py; может лежать внутри проекта")
    parser.add_argument("--project", required=True, help="корень проекта")
    parser.add_argument("--docs", action="append", required=True, help="спека (путь внутри проекта); можно несколько")
    parser.add_argument("--subject", required=True, help="тема прогона по-русски")
    parser.add_argument("--models", required=True, help="модели через запятую, как их называет CLI; первая — по умолчанию")
    parser.add_argument("--default-model", help="модель для ролей без рекомендации бенча (по умолчанию первая из --models)")
    parser.add_argument("--role-model", action="append", default=[], help="роль=модель, поверх предложения бенча")
    parser.add_argument("--cli-command", required=True, help="команда CLI одной строкой, с {model} (и {prompt_file}/{answer_file}/{tmp} по режиму)")
    parser.add_argument("--prompt-via", choices=("stdin", "file", "arg"), default="stdin")
    parser.add_argument("--answer-from", choices=("stdout", "file"), default="stdout")
    parser.add_argument("--answer-file", help="файл ответа CLI при --answer-from file (можно с {tmp})")
    parser.add_argument("--sandbox-args", help="флаги CLI «без инструментов / read-only» одной строкой → sandbox_args в wrap.json")
    parser.add_argument("--cli-version", help="строка версии CLI для квитанций (иначе — wrap.py --version)")
    parser.add_argument("--cli-timeout", type=int, default=1700, help="тайм-аут одного вызова CLI в обёртке, с")
    parser.add_argument("--id-pattern", help="шаблон ID требований (иначе — вывести из спеки и подтвердить)")
    parser.add_argument("--runner-dir", help="каталог процессов модели (по умолчанию <home>/runner, а при --home внутри проекта — "
                                             "<home>/.runner; драйвер пишет предупреждение RUNNER_DIR_INSIDE_PROJECT)")
    parser.add_argument("--module", help="id модуля из .skillsrc (pipeline_driver --module); обязателен, если модулей несколько")
    parser.add_argument("--target", help="цель прогона для драйвера (pipeline_driver --target): каталог фичи; обязателен для проектов "
                                         "больше одного модуля / 1 МБ — иначе разметка получит весь модуль")
    parser.add_argument("--tokens-per-byte", type=float, default=TOKENS_PER_BYTE, help="токенов на байт входа задачи (оценка перед "
                                                                                       "первым вызовом модели; по умолчанию 0.24)")
    parser.add_argument("--model-context", type=int, default=MODEL_CONTEXT, help="окно модели, токенов (по умолчанию 239144): "
                                                                                 "первая задача больше — стоп до вызова модели")
    parser.add_argument("--review-input-bytes", type=int, default=100_000)
    parser.add_argument("--max-tasks", type=int, default=2)
    parser.add_argument("--max-parallel", type=int, default=2, help="review_runner.max_parallel в .skillsrc")
    parser.add_argument("--runner-timeout", type=int, default=3600, help="review_runner.timeout_seconds в .skillsrc")
    parser.add_argument("--no-mutation", action="store_true", help="без PIT")
    parser.add_argument("--online", action="store_true", help="не писать modules[].test.offline: true в .skillsrc (сборка может ходить "
                                                              "в репозитории артефактов); по умолчанию — offline")
    parser.add_argument("--from-step", choices=STEPS, default="0")
    parser.add_argument("--only", help="только эти шаги, через запятую (например 7,8)")
    parser.add_argument("--run", help="run_id прерванного или ждущего прогона — продолжить его (шаг 5)")
    parser.add_argument("--answer", action="append", default=[], help="метка=значение для продолжения с --run")
    parser.add_argument("--yes", action="store_true", help="подтверждать вопросы оператора без вопросов (ответы ask_user всё равно вводятся; "
                                                           "правку .skillsrc и chmod +x файлов проекта не подтверждает)")
    parser.add_argument("--yes-skillsrc", action="store_true", help="записать .skillsrc проекта без вопроса (diff всё равно печатается)")
    parser.add_argument("--yes-chmod", action="store_true", help="chmod +x mvnw/gradlew проекта без вопроса")
    parser.add_argument("--execution-review", choices=("human", "none"), help="одобрение сгенерированных тестов перед запуском "
                        "(pipeline_driver --execution-review; пакет с S3): по умолчанию — как у пакета (human, остановка "
                        "approve-generated-tests); none — без остановки, для повторов на учебном проекте")
    parser.add_argument("--env-passthrough", help="переменные окружения для CLI модели сверх базового списка wrap.py, через запятую "
                        "(имя или префикс со * в конце: GIGACODE_*,HTTPS_PROXY) → env_passthrough в wrap/wrap.json и, если пакет "
                        "это знает, в .skillsrc review_runner")
    parser.add_argument("--confirm-context", choices=("yes", "no"), help="подтверждение отправки файлов в модель (pipeline_driver "
                        "--confirm-context; пакет с S5): по умолчанию — как у пакета (yes, остановка confirm-context до первой задачи "
                        "модели); no — без остановки, для повторов на учебном проекте")
    parser.add_argument("--task-input-bytes", type=int, help="порог входа одной задачи модели у драйвера (pipeline_driver "
                        "--task-input-bytes; пакет с P1): по умолчанию 0,8 окна модели в байтах (--model-context / --tokens-per-byte); "
                        "больше — стоп TASK_INPUT_TOO_LARGE до вызова модели")
    parser.add_argument("--yes-send", action="store_true", help="подтвердить остановку confirm-context (отправка показанных файлов "
                                                                "в модель), если у неё один вариант; --yes её не подтверждает")
    parser.add_argument("--yes-exec", action="store_true", help="подтвердить остановку approve-generated-tests (исполнение показанных "
                                                                "сгенерированных тестов), если у неё один вариант; --yes её не подтверждает")
    parser.add_argument("--no-pip", action="store_true", help="не предлагать команду pip: зависимости ставятся средствами окружения")
    parser.add_argument("--dry-run", action="store_true", help="только напечатать команды и действия")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["init"]:
        return init_main(argv[1:])
    args = build_parser().parse_args(argv)
    return Operator(args).main()


if __name__ == "__main__":
    raise SystemExit(main())
