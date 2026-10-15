#!/usr/bin/env python3
"""operate.py — the internal test-skills run by RUN.md, step by step, without a model operator.

    python3 operate.py --home ~/ts --project <project> --docs <spec> [--docs …] --subject "<тема>"
        --models glm-5.3,… --cli-command '<CLI command with {model}>' --prompt-via stdin|file|arg
        --answer-from stdout|file [--answer-file …] [--runner-dir ~/ts/runner] [--review-input-bytes 100000]
        [--max-tasks 2] [--no-mutation] [--from-step N] [--only 0,2,3] [--yes] [--dry-run]

Steps are the sections of RUN.md: 0 kit check, 1 preflight, 2 wrap.json + selftest + one live call, 3 model
benchmark and models by role, 4 .skillsrc (review_runner, requirements.id_pattern, mutation), 5a cases-only trial,
5 full local-pilot-v1 run (ask_user answered here; regenerate-after-gate and BUILD_FAILED_BEFORE_TESTS stop with the
runner output tail), 7 ~/ts/report/ with the figures, 8 Zephyr CSV.  Every step prints its commands, exit codes and
main lines; everything goes to ~/ts/operate.log.  A failing step stops with a hint; --from-step continues.
Nothing leaves the machine.  Python 3.11+, stdlib only; Linux (a Windows branch exists only for checks).
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
import zipfile
from pathlib import Path
from typing import Any

STEPS = ("0", "1", "2", "3", "4", "5a", "5", "7", "8")
TITLES = {"0": "Проверка комплекта", "1": "Preflight проекта", "2": "Обёртка CLI", "3": "Бенчмарк моделей", "4": ".skillsrc проекта",
          "5a": "Пробный прогон (cases-only-v1)", "5": "Полный прогон (local-pilot-v1)", "7": "Что принести (~/ts/report)", "8": "Экспорт Zephyr"}
ROLES = ("context-marker", "tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer", "mutation-triage")
REVIEW_ROLES = ("tc-reviewer", "autotest-reviewer")
PROBE = "test-skills-probe: проверка обёртки. Ответь только JSON-объектом {\"ok\": true} без пояснений.\n"
STATUS_SECONDS = 60
PY = sys.executable
WINDOWS = os.name == "nt"


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
            while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
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
        self.wrap_dir = self.home / "wrap"
        self.report = self.home / "report"
        self.project = Path(args.project).expanduser().resolve()
        self.models = [item.strip() for item in args.models.split(",") if item.strip()]
        self.default_model = args.default_model or self.models[0]
        self.runner_dir = Path(args.runner_dir).expanduser().resolve() if args.runner_dir else self.home / "runner"
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

    def confirm(self, question: str) -> bool:
        if self.dry or self.args.yes:
            self.say(f"{question} → да ({'dry-run' if self.dry else '--yes'})")
            return True
        answer = input(f"{question} [y/n] ").strip().lower()
        self.log(f"{question} [y/n] {answer}")
        return answer in {"y", "yes", "д", "да"}

    def ask(self, question: str) -> str:
        answer = input(question).strip()
        self.log(f"{question}{answer}")
        return answer

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

    # -- step 0
    def step_0(self) -> None:
        zips = sorted(self.home.glob("test-skills-*.zip"), key=lambda path: path.stat().st_mtime)
        if not zips and not self.pack.is_dir():
            raise Stop(f"в {self.home} нет ни test-skills-*.zip, ни распакованного test-skills", f"скопировать комплект целиком в {self.home}")
        if zips:
            archive = zips[-1]
            if len(zips) > 1:
                self.say(f"  внимание: архивов несколько, беру самый новый: {archive.name}")
            sums = archive.with_name(archive.name + ".sha256")
            self.action(f"sha256 {archive.name} против {sums.name}")
            if not self.dry:
                if not sums.is_file():
                    raise Stop(f"нет {sums.name}", "скопировать комплект целиком (архив и его .sha256)")
                expected, actual = sums.read_text(encoding="utf-8").split()[0].lower(), _sha256(archive)
                if expected != actual:
                    raise Stop(f"sha256 архива не совпадает: {actual[:12]}… вместо {expected[:12]}…", "скопировать архив заново, он повреждён")
                self.say(f"  sha256 архива: ок ({actual[:12]}…)")
            if not self.pack.is_dir():
                self.action(f"распаковать {archive.name} → {self.pack}")
                if not self.dry:
                    with zipfile.ZipFile(archive) as bundle:
                        bundle.extractall(self.home)
            files = self.home / (archive.name[:-4] + ".files.sha256")
            self.action(f"сверить файлы пакета по {files.name}")
            if not self.dry and files.is_file():
                bad = []
                rows = [line.split(None, 1) for line in files.read_text(encoding="utf-8").splitlines() if line.strip()]
                for digest, name in rows:
                    path = self.home / name.lstrip("*").strip()
                    if not path.is_file() or _sha256(path) != digest:
                        bad.append(name.strip())
                if bad:
                    raise Stop(f"файлы пакета отличаются от архива ({len(bad)}): {', '.join(bad[:5])}",
                               f"удалить {self.pack} и запустить шаг 0 заново — он распакует архив")
                self.say(f"  файлы пакета: {len(rows)} совпадают")
        if not WINDOWS:
            for path in (self.wrap_dir / "wrap.py", self.home / "preflight.py", self.home / "bench" / "bench.py"):
                self.action(f"chmod +x {path}")
                if not self.dry and path.is_file():
                    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        if sys.version_info < (3, 11):
            raise Stop(f"Python {sys.version.split()[0]} < 3.11", "запускать operate.py через python3.11 или новее")
        self.say(f"  Python {sys.version.split()[0]}: ок")
        modules = self.run([PY, "-c", "import jsonschema, yaml"])
        if modules.code != 0:
            self.say("  нет jsonschema/pyyaml — ставлю из локального зеркала pip")
            pip = self.run([PY, "-m", "pip", "install", "-r", self.pack / "requirements.txt"], echo=True)
            if pip.code != 0:
                raise Stop("pip не поставил зависимости пакета", "проверить настройку локального зеркала pip (pip config list) и повторить шаг 0")
        doctor = self.run([PY, "-m", "tools.doctor", "--root", "."], cwd=self.pack)
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
        done = self.run([PY, self.home / "preflight.py", "--pack", self.pack, "--project", self.project, *self._docs_args(), "--json", report])
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
        for row in [*stops, *warns][:8]:
            self.say(f"  [{row['status']}] {row['check']}: {row['finding']}" + (f" → {row['hint']}" if row["hint"] else ""))
        if stops:
            fixable = [re.match(r"chmod \+x (.+?)(?:\s{2}|$)", row["hint"]) for row in stops]
            if all(fixable) and not again:
                paths = [Path(match.group(1)) for match in fixable]
                if self.confirm("Сделать исполняемыми " + ", ".join(str(path) for path in paths) + "?"):
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
    def wrap_config(self) -> dict[str, Any]:
        command = shlex.split(self.args.cli_command)
        config: dict[str, Any] = {"command": command, "prompt_via": self.args.prompt_via, "answer_from": self.args.answer_from,
                                  "timeout_seconds": self.args.cli_timeout, "extra_env": {}}
        if self.args.answer_file:
            config["answer_file"] = self.args.answer_file
        if self.args.cli_version:
            config["version"] = self.args.cli_version
        return config

    def runner_cli(self) -> Path:
        return self.wrap_dir / ("wrap.cmd" if WINDOWS else "wrap.py")

    def step_2(self) -> None:
        config = self.wrap_config()
        if not any("{model}" in item for item in config["command"]):
            self.say("  внимание: в --cli-command нет {model} — модель по ролям до CLI не дойдёт")
        if config["prompt_via"] == "arg":
            self.say("  внимание: prompt_via=arg — части ревью 126–552 КБ не пройдут одним аргументом (Linux: 128 КиБ); лучше stdin или file")
        if config["answer_from"] == "file" and "answer_file" not in config:
            self.say("  answer_file по умолчанию: {tmp}/answer.txt")
        path = self.wrap_dir / "wrap.json"
        self.action(f"записать {path}: {json.dumps(config, ensure_ascii=False)}")
        if not self.dry:
            if path.is_file() and not (self.wrap_dir / "wrap.json.orig").exists():
                shutil.copy2(path, self.wrap_dir / "wrap.json.orig")
            path.write_text(json.dumps(config, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            if WINDOWS:
                self.runner_cli().write_text(f'@"{PY}" "%~dp0wrap.py" %*\r\n', encoding="utf-8")
        version = self.run([PY, self.wrap_dir / "wrap.py", "--version"])
        if not self.dry:
            if version.code != 0:
                raise Stop("wrap.py --version не прошёл", " ".join(_main_lines(version.stderr, 2)) or "проверить wrap.json")
            self.state["host_cli_version"] = version.stdout.strip() or "unknown"
            self.say(f"  версия CLI: {self.state['host_cli_version']}")
        selftest = self.run([PY, self.wrap_dir / "wrap.py", "--selftest", "--pack", self.pack], cwd=self.wrap_dir)
        if not self.dry:
            for line in _main_lines(selftest.stdout, 3):
                self.say("  " + line)
            if selftest.code != 0:
                raise Stop("селфтест обёртки не прошёл", "смотреть строки «стоп» в operate.log; обёртка или Python окружения")
        answer = self.home / "probe-answer.txt"
        probe = self.run([PY, self.wrap_dir / "wrap.py", "exec", "--skip-git-repo-check", "--json", "--model", self.default_model,
                          "--output-last-message", answer, "-"], stdin=PROBE, timeout=int(self.args.cli_timeout) + 60)
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
        usage = next((event.get("usage") for event in events if isinstance(event, dict) and event.get("type") == "turn.completed"), None)
        if usage:
            self.say(f"  токены: {json.dumps(usage, ensure_ascii=False)}")

    # -- step 3
    def step_3(self) -> None:
        out = self.home / f"bench-{datetime.date.today():%m%d}"
        done = self.run([PY, self.home / "bench" / "bench.py", "--pack", self.pack, "--wrap-config", self.wrap_dir / "wrap.json",
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
                                                   f"  max_parallel: {self.args.max_parallel}", f"  timeout_seconds: {self.args.runner_timeout}"])
        if pattern:
            lines = set_child(lines, "requirements", "id_pattern", _q(pattern))
        if self.state.get("mutation", not self.args.no_mutation):
            if _top_block(lines, "mutation") is None:
                lines = [*lines, "mutation:", "  enabled: true", "  threads: 2", "  timeout_seconds: 3600"]
            else:
                lines = set_child(lines, "mutation", "enabled", "true")
        after = "\n".join(lines) + "\n"
        if after == before:
            self.say("  .skillsrc уже такой — без изменений")
            self.state.pop("skillsrc_deferred", None)
            return
        for line in difflib.unified_diff(before.splitlines(), after.splitlines(), ".skillsrc", ".skillsrc (новый)", lineterm="", n=1):
            self.say("  " + line)
        if not self.confirm(f"Записать {skillsrc}?"):
            raise Stop(".skillsrc не записан", "поправить руками по RUN.md п. 4 и запустить --from-step 5a")
        self.action(f"копия прежнего .skillsrc → {self.home}/skillsrc.bak-<время>; запись {skillsrc}")
        if not self.dry:
            shutil.copy2(skillsrc, self.home / f"skillsrc.bak-{datetime.datetime.now():%Y%m%d-%H%M%S}")
            skillsrc.write_text(after, encoding="utf-8")
        self.state.pop("skillsrc_deferred", None)

    # -- steps 5a and 5
    def driver_argv(self, profile: str, extra: list[str]) -> list[str]:
        host_cli = Path(shlex.split(self.args.cli_command)[0]).name
        return [PY, "-m", "tools.pipeline_driver", "run", "--project", str(self.project), "--runner", "process", "--profile", profile,
                *self._docs_args(), "--subject", self.args.subject, "--model-id", self.default_model, "--host-cli", host_cli,
                "--host-cli-version", self.state.get("host_cli_version") or self.args.cli_version or "unknown", "--reviewer-isolation", "fresh",
                "--review-runner-preset", "codex", "--review-runner-cli", str(self.runner_cli()), "--runner-dir", str(self.runner_dir),
                "--review-input-bytes", str(self.args.review_input_bytes), "--max-tasks", str(self.args.max_tasks), *extra]

    def resume_argv(self, run_id: str, answers: list[str]) -> list[str]:
        return [PY, "-m", "tools.pipeline_driver", "run", "--project", str(self.project), "--runner", "process", "--run", run_id,
                *[item for answer in answers for item in ("--answer", answer)]]

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
        if not isinstance(payload, dict):
            tail = _main_lines(err.read_text(encoding="utf-8", errors="replace"), 3)
            raise Stop(f"драйвер не вернул JSON (код {process.returncode}): {' | '.join(tail)}", f"смотреть {err}; текст ошибки — в отчёт (п. 7)")
        return process.returncode, payload

    def _runner_output_tail(self, run_id: str, lines: int = 30) -> None:
        outputs = sorted((self.project / ".pilot-runs" / run_id / "artifacts").glob("*/runner-output.txt"), key=lambda path: path.stat().st_mtime)
        if not outputs:
            self.say("  runner-output.txt не найден")
            return
        self.say(f"  хвост {outputs[-1]}:")
        for line in outputs[-1].read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]:
            self.say("    " + line[:300])

    def drive(self, profile: str, extra: list[str], label: str) -> tuple[int, dict[str, Any] | None]:
        run_id, self.args.run = self.args.run, None  # --run resumes the first driven step only
        argv = self.resume_argv(run_id, self.args.answer) if run_id else self.driver_argv(profile, extra)
        while True:
            code, payload = self.run_watched(argv, label, run_id)
            if self.dry:
                self.say("  (ask_user — показать вопрос, ждать ответа, повтор: … run --project <проект> --runner process --run <run_id> --answer <метка>=<значение>)")
                self.say("  (ask regenerate-after-gate или BUILD_FAILED_BEFORE_TESTS — хвост runner-output.txt и стоп)")
                return 0, None
            run_id = payload.get("run_id") or run_id
            self.state[f"{label}_run_id"] = run_id
            self.save_state()
            action = payload.get("action")
            if action == "done":
                return code, payload
            if action == "ask_user":
                task_id = str(payload.get("task_id") or "")
                self.say(f"  драйвер спрашивает ({task_id}): {payload.get('question')}")
                for option in payload.get("options") or []:
                    self.say("    - " + (" — ".join(str(option[key]) for key in ("value", "label", "description") if option.get(key)) or json.dumps(option, ensure_ascii=False)))
                if "regenerate-after-gate" in task_id:
                    self._runner_output_tail(run_id)
                    raise Stop("тесты не прошли гейт сборки — перегенерацию сразу не делать",
                               "если в хвосте линтер/сборка проекта — исправить окружение (п. 1) и начать новый прогон; если ошибки компиляции самих тестов — "
                               f"ответить: operate.py … --from-step {'5' if label == 'run' else '5a'} --run {run_id} --answer {task_id.split('.ask.', 1)[-1]}=<значение>")
                name = task_id.split(".ask.", 1)[1] if ".ask." in task_id else task_id
                value = self.ask("Ответ (значение; пусто — остановиться): ")
                if not value:
                    raise Stop("прогон ждёт ответа", f"продолжить: operate.py … --from-step {'5' if label == 'run' else '5a'} --run {run_id} --answer {name}=<значение>")
                argv = self.resume_argv(run_id, [f"{name}={value}"])
                continue
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
        self.say(f"\n=== Шаг {step}. {TITLES[step]} ===")
        try:
            getattr(self, "step_" + step)()
        except Stop as stop:
            self.say(f"СТОП на шаге {step}: {stop.message}")
            if stop.hint:
                self.say(f"Что делать: {stop.hint}")
            self.say(f"Продолжить после исправления: python3 operate.py <те же аргументы> --from-step {step}")
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
    parser.add_argument("--home", default="~/ts", help="каталог комплекта (там test-skills-*.zip, wrap/, bench/, preflight.py)")
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
    parser.add_argument("--cli-version", help="строка версии CLI для квитанций (иначе — wrap.py --version)")
    parser.add_argument("--cli-timeout", type=int, default=1700, help="тайм-аут одного вызова CLI в обёртке, с")
    parser.add_argument("--id-pattern", help="шаблон ID требований (иначе — вывести из спеки и подтвердить)")
    parser.add_argument("--runner-dir", help="каталог процессов модели вне проекта (по умолчанию <home>/runner)")
    parser.add_argument("--review-input-bytes", type=int, default=100_000)
    parser.add_argument("--max-tasks", type=int, default=2)
    parser.add_argument("--max-parallel", type=int, default=2, help="review_runner.max_parallel в .skillsrc")
    parser.add_argument("--runner-timeout", type=int, default=3600, help="review_runner.timeout_seconds в .skillsrc")
    parser.add_argument("--no-mutation", action="store_true", help="без PIT")
    parser.add_argument("--from-step", choices=STEPS, default="0")
    parser.add_argument("--only", help="только эти шаги, через запятую (например 7,8)")
    parser.add_argument("--run", help="run_id прерванного или ждущего прогона — продолжить его (шаг 5)")
    parser.add_argument("--answer", action="append", default=[], help="метка=значение для продолжения с --run")
    parser.add_argument("--yes", action="store_true", help="подтверждать всё без вопросов (ответы ask_user всё равно вводятся)")
    parser.add_argument("--dry-run", action="store_true", help="только напечатать команды и действия")
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return Operator(args).main()


if __name__ == "__main__":
    raise SystemExit(main())
