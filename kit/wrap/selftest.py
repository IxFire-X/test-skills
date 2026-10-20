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


def note(ok: bool, check: str, detail: str = "", soft: bool = False) -> None:
    ROWS.append(("ок" if ok else "внимание" if soft else "стоп", check, detail))


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


def note_sandbox(here: Path) -> None:
    """The working wrap.json: its own extra_env passes the secret check, and the CLI runs without tools if it can."""
    path = Path(os.environ.get("WRAP_CONFIG") or here / "wrap.json")
    sys.path.insert(0, str(here))
    from wrap import load_config

    try:
        config = load_config(path)
    except (OSError, ValueError, SystemExit) as error:
        note(False, f"рабочий {path.name}", str(error)[:200])
        return
    note(True, f"рабочий {path.name}", f"extra_env: {', '.join(config['extra_env']) or 'пусто'}; tmp_dir: {config.get('tmp_dir') or 'системный temp'}")
    note(bool(config["sandbox_args"]), "режим CLI без инструментов (sandbox_args)", " ".join(config["sandbox_args"]) or
         "не задан: если у CLI есть флаг «без инструментов / read-only» — указать его в sandbox_args, иначе модель в CLI может читать "
         "и менять файлы сама", soft=True)


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
        in_tmp = local(here, json.loads((here / "examples" / "stdin-stdout.json").read_text(encoding="utf-8")))
        in_tmp["tmp_dir"] = "own-tmp"  # relative to the config file: work/own-tmp
        code, _events, last, stderr = call(here, in_tmp, prompt, work, "tmp-dir")
        left = sorted(path.name for path in (work / "own-tmp").iterdir()) if (work / "own-tmp").is_dir() else None
        note(code == 0 and last is not None and left == [], "tmp_dir: временный каталог вызова — внутри него и убран",
             f"код {code}, каталог {'создан, пуст' if left == [] else left}" + (f", stderr: {stderr[-200:]}" if code else ""))
        secret = local(here, json.loads((here / "examples" / "stdin-stdout.json").read_text(encoding="utf-8")))
        secret["extra_env"] = {"LLM_ENDPOINT": "http://127.0.0.1:1", "LLM_API_KEY": "x"}
        code, _events, last, stderr = call(here, secret, prompt, work, "secret-env")
        note(code != 0 and last is None and "LLM_API_KEY" in stderr and "LLM_ENDPOINT" not in stderr,
             "extra_env с секретом → отказ конфигурации", f"код {code}: {stderr.strip()[-160:]}")
        # S6: the CLI gets the base list and env_passthrough only — a token in the wrapper's environment does not reach it
        os.environ["FOO_TOKEN"], os.environ["UNLISTED_VAR"] = "selftest-not-a-secret", "x"
        try:
            seen = {}
            for name, passthrough in (("env-base", []), ("env-passthrough", ["FOO_*"])):
                dump = work / f"{name}.json"
                config = local(here, json.loads((here / "examples" / "stdin-stdout.json").read_text(encoding="utf-8")))
                config["command"] = [*config["command"], "--dump-env", str(dump)]
                config["env_passthrough"] = passthrough
                code, _events, _last, stderr = call(here, config, prompt, work, name)
                seen[name] = set(json.loads(dump.read_text(encoding="utf-8"))) if dump.is_file() else None
            base, passed = seen["env-base"] or set(), seen["env-passthrough"] or set()
            note(seen["env-base"] is not None and "FOO_TOKEN" not in base and "UNLISTED_VAR" not in base and any(key.upper() == "PATH" for key in base),
                 "окружение CLI — белый список: FOO_TOKEN и прочее не доходят", f"передано: {len(base)} имён")
            note("FOO_TOKEN" in passed and "UNLISTED_VAR" not in passed, "env_passthrough [\"FOO_*\"] пропускает FOO_TOKEN, и только его",
                 f"передано: {len(passed)} имён")
            bad = local(here, json.loads((here / "examples" / "stdin-stdout.json").read_text(encoding="utf-8")))
            bad["env_passthrough"] = ["FOO*BAR"]
            code, _events, last, stderr = call(here, bad, prompt, work, "env-bad")
            note(code != 0 and last is None and "env_passthrough" in stderr, "env_passthrough с * не в конце → отказ конфигурации", f"код {code}")
            secret = local(here, json.loads((here / "examples" / "stdin-stdout.json").read_text(encoding="utf-8")))
            secret["env_passthrough"] = ["FOO_TOKEN", "AW*"]
            code, _events, last, stderr = call(here, secret, prompt, work, "env-secret")
            note(code != 0 and last is None and "FOO_TOKEN" in stderr and "AW*" in stderr,
                 "env_passthrough с секретоподобным именем или префиксом короче 3 символов → отказ", f"код {code}")
        finally:
            os.environ.pop("FOO_TOKEN", None)
            os.environ.pop("UNLISTED_VAR", None)
    note_sandbox(here)
    width = max(len(row[1]) for row in ROWS)
    for status, check, detail in ROWS:
        print(f"{status:8} {check:{width}}  {detail}")
    failed = sum(row[0] == "стоп" for row in ROWS)
    warned = sum(row[0] == "внимание" for row in ROWS)
    print(f"\nИтог: {'ок' if not failed else f'стоп {failed}'}" + (f", внимание {warned}" if warned else ""))
    return 1 if failed else 0
