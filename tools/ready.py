"""Readiness of a project for a first run: every check at once, each problem with the command that fixes it (FaT-R).

``python -m tools.ready --project <abs> --docs <path> [--docs …] [--profile …] [--module <ID>]`` only reads.  It never
stops at the first problem: Python and the package dependencies, the pack itself (doctor), git, the requirement
documents, ``.skillsrc``, the module, the scan with its exclusions and — as a separate block that cases do not need — the
build (wrapper, system ``mvn``, JDK, the module interpreter).  The JSON report goes to stdout, a table to stderr; the exit
code is 0 when cases can start.

Only the standard library at module level: ready must run where ``jsonschema`` or ``yaml`` is missing, so every part of
the package is imported inside the check that needs it.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

PACK = Path(__file__).resolve().parents[1]
CASES, EXECUTION = "cases", "execution"
_LABELS = {"OK": "OK", "WARN": "ВНИМАНИЕ", "STOP": "СТОП"}


def _row(check_id: str, block: str, level: str, message: str, fix: str | None = None, details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"id": check_id, "block": block, "level": level, "message": message}
    if fix:
        row["fix"] = fix
    if details:
        row["details"] = dict(details)
    return row


def pip_command() -> str:
    return f'"{sys.executable}" -m pip install --no-cache-dir --require-hashes -r "{PACK / "requirements.txt"}"'


def check_python() -> dict[str, Any]:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 11):
        return _row("python", CASES, "OK", f"Python {version} ({sys.executable})")
    return _row("python", CASES, "STOP", f"Python {version} старше 3.11", "запустите пакет интерпретатором Python 3.11–3.13")


def check_deps() -> dict[str, Any]:
    missing = []
    for name in ("jsonschema", "yaml"):
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    if not missing:
        return _row("deps", CASES, "OK", "зависимости пакета установлены")
    return _row("deps", CASES, "STOP", f"у этого Python ({sys.executable}) нет: {', '.join(missing)}", pip_command())


def check_pack() -> dict[str, Any]:
    try:
        from tools.doctor import inspect_environment

        report = inspect_environment(PACK)
    except Exception as error:  # the doctor itself is broken: say so, keep checking
        return _row("pack", CASES, "STOP", f"doctor не запустился: {type(error).__name__}: {error}", "сверьте папку пакета с поставкой")
    if report.get("status") == "PASS":
        return _row("pack", CASES, "OK", "пакет цел (doctor PASS)")
    missing = report.get("integrity", {}).get("missing") or []
    return _row("pack", CASES, "STOP", "doctor: " + "; ".join([*map(str, missing), *map(str, report.get("messages") or [])]),
                "сверьте папку пакета с поставкой (файлы и SHA-256)", {"doctor": report})


def check_git(project: Path) -> dict[str, Any]:
    """Exactly the call of the pipeline (pilot_state._project_state_snapshot) when the project has .git."""
    project = Path(project).resolve()
    if not (project / ".git").exists():
        return _row("git", CASES, "OK", "не git-проект: git не нужен")
    try:
        completed = subprocess.run(["git", "-c", f"safe.directory={project.as_posix()}", "-C", str(project),
                                    "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as error:
        return _row("git", CASES, "STOP", f"в проекте есть .git, но git не запускается: {error}", "установите git и добавьте его в PATH")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["код " + str(completed.returncode)]
        return _row("git", CASES, "STOP", f"git status проекта не прошёл: {detail[0]}",
                    f'git -c safe.directory="{project.as_posix()}" -C "{project}" status — исправьте ошибку, которую он покажет')
    return _row("git", CASES, "OK", "git status проекта работает")


def check_docs(project: Path, docs: Sequence[str]) -> list[dict[str, Any]]:
    rows = []
    root = Path(project).resolve()
    if not docs:
        return [_row("docs", CASES, "STOP", "не указан ни один документ требований", "добавьте --docs <путь внутри проекта>")]
    for raw in docs:
        candidate = Path(raw)
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        check_id = f"docs:{raw}"
        try:
            path.relative_to(root)
        except ValueError:
            rows.append(_row(check_id, CASES, "STOP", f"{raw} вне проекта", "положите документ внутрь проекта и укажите путь от его корня"))
            continue
        if not path.is_file():
            rows.append(_row(check_id, CASES, "STOP", f"{raw} не найден", "проверьте путь от корня проекта"))
            continue
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            rows.append(_row(check_id, CASES, "STOP", f"{raw} не в UTF-8", "сохраните документ в кодировке UTF-8"))
            continue
        rows.append(_row(check_id, CASES, "OK", f"{raw} — {path.stat().st_size} Б"))
    return rows


def _init_command(project: Path) -> str:
    return f'"{sys.executable}" -m tools.init_skillsrc --project "{project}"'


def check_skillsrc(project: Path) -> list[dict[str, Any]]:
    """Validity of .skillsrc and its drift from the project, through the existing init flow (no write)."""
    from tools.init_skillsrc import ensure_skillsrc

    receipt = ensure_skillsrc(Path(project), {}, write=False)
    status = receipt.get("status")
    if not (Path(project) / ".skillsrc").is_file():
        return [_row("skillsrc", CASES, "OK", ".skillsrc нет — первый scan создаст его по проекту")] if status in {"preview", "created"} else \
            [_row("skillsrc", CASES, "STOP", f".skillsrc не создаётся: {status} {receipt.get('errors') or receipt.get('questions')}", _init_command(project))]
    if status in {"unchanged", "preview"}:
        return [_row("skillsrc", CASES, "OK", ".skillsrc валиден и совпадает с проектом")]
    proposal = receipt.get("proposal")
    if status == "needs_input" and isinstance(proposal, Mapping):
        diff = proposal.get("structural_diff")
        approval = {"approval_id": "ready-" + str(proposal["proposed_digest"]).removeprefix("sha256:")[:12],
                    "original_digest": proposal["original_digest"], "proposed_digest": proposal["proposed_digest"]}
        fix = ("сохраните approval в файл и подтвердите замену: " + _init_command(project)
               + ' --write --replacement-approval <файл approval> --immutable-evidence-dir <каталог для копии прежнего .skillsrc>')
        changed = {str(row.get("path")) for row in diff or [] if isinstance(row, Mapping)}
        before, after = _test_change(project)
        switch = any(path.endswith("/test/adapter_id") for path in changed) and (after or {}).get("adapter_id") == "maven:selected-symbols-v1"
        diff = {"paths": diff, "test_before": before, "test_after": after}
        check_id, block = ("execution:skillsrc-switch", EXECUTION) if switch else ("skillsrc", CASES)
        message = ("в .skillsrc записан wrapper, которого нет в проекте; предлагается системный mvn" if switch
                   else ".skillsrc расходится с проектом; предлагается замена")
        return [_row(check_id, block, "WARN", message, fix, {"diff": diff, "approval": approval})]
    if status == "needs_input":
        # The scan takes an existing valid .skillsrc as it is; a discovery question only says detection is unsure.
        return [_row("skillsrc", CASES, "OK", ".skillsrc валиден; scan возьмёт его как есть (обнаружение по проекту не однозначно)",
                     None, {"questions": receipt.get("questions")})]
    if status == "conflict":
        return [_row("skillsrc", CASES, "STOP", f".skillsrc требует ответа ({status})", _init_command(project) + " --answers <файл ответов>",
                     {"questions": receipt.get("questions")})]
    return [_row("skillsrc", CASES, "STOP", f".skillsrc невалиден: {', '.join(map(str, receipt.get('errors') or [status]))}", _init_command(project))]


def _test_change(project: Path) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """The ``test`` section of the first module now and as the project detection would write it (values for the diff)."""
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    try:
        existing = normalize_skillsrc(load_skillsrc(Path(project) / ".skillsrc"))["modules"]
        proposed = proposed_modules(project)
    except Exception:  # the diff values are a courtesy; the structural diff above is the binding part
        return None, None
    by_id = {str(module.get("id")): module for module in proposed}
    for module in existing:
        if str(module.get("id")) in by_id and module.get("test") != by_id[str(module.get("id"))].get("test"):
            return module.get("test"), by_id[str(module.get("id"))].get("test")
    return None, None


def _modules(project: Path) -> list[dict[str, Any]]:
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    path = Path(project) / ".skillsrc"
    if path.is_file():
        return list(normalize_skillsrc(load_skillsrc(path))["modules"])
    return proposed_modules(project)


def proposed_modules(project: Path) -> list[dict[str, Any]]:
    """The modules the first scan would write into a missing .skillsrc."""
    from tools.discover_project import discover_project
    from tools.init_skillsrc import compile_skillsrc
    from tools.skillsrc_manifest import normalize_skillsrc

    root = Path(project).resolve()
    return list(normalize_skillsrc(compile_skillsrc(discover_project(root), {}, root))["modules"])


def select_module(modules: Sequence[Mapping[str, Any]], module_id: str | None) -> tuple[Mapping[str, Any] | None, str | None]:
    if module_id is not None:
        found = [module for module in modules if module.get("id") == module_id]
        return (found[0], None) if found else (None, f"модуля {module_id} нет; есть: {', '.join(str(m.get('id')) for m in modules)}")
    if len(modules) == 1:
        return modules[0], None
    return None, f"модулей несколько: {', '.join(str(m.get('id')) for m in modules)}" if modules else "модулей нет"


def check_module(project: Path, module_id: str | None) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    module, problem = select_module(_modules(project), module_id)
    if module is None:
        return _row("module", CASES, "STOP", str(problem), "укажите --module <ID> из списка"), None
    return _row("module", CASES, "OK", f"модуль {module.get('id')} ({module.get('root')})"), module


def check_scan(project: Path, module: Mapping[str, Any]) -> tuple[dict[str, Any], Mapping[str, Any]]:
    from tools.project_inventory import build_inventory
    from tools.scan_rules import exclusion_summary

    root = Path(project).resolve()
    module_root = (root / str(module.get("root") or ".")).resolve()
    try:
        pack = PACK if PACK.resolve().is_relative_to(root) else None
    except ValueError:
        pack = None
    started = time.monotonic()
    inventory = build_inventory(root, module_root, skill_pack_root=pack, generated_roots=(root / ".pilot-runs",))
    seconds = round(time.monotonic() - started, 2)
    summary = exclusion_summary(inventory["exclusions"], module_root.relative_to(root).as_posix() or ".")
    details = {"files": len(inventory["files"]), "bytes": sum(int(item["size"]) for item in inventory["files"]), "seconds": seconds,
               "exclusion_summary": summary}
    message = f"скан: {details['files']} файлов, {details['bytes']} Б, {seconds} с; исключено {summary['total']}"
    if summary["suspicious"]:
        return _row("scan", CASES, "WARN", message + "; есть подозрительные исключения", summary["hint"], details), inventory
    return _row("scan", CASES, "OK", message, None, details), inventory


def check_task_documents(rows: list[dict[str, Any]], project: Path, docs: Sequence[str], inventory: Mapping[str, Any]) -> None:
    """FaT-3: a document of --docs outside the module's inventory never reaches the model — the same stop as scan."""
    from tools.run_pipeline import TASK_DOC_FIX, _exclusion_note

    root = Path(project).resolve()
    paths = {str(item["project_path"]) for item in inventory["files"]}
    for raw in docs:
        row = next((item for item in rows if item["id"] == f"docs:{raw}"), None)
        if row is None or row["level"] != "OK":
            continue
        candidate = Path(raw)
        relative = (candidate if candidate.is_absolute() else root / candidate).resolve().relative_to(root).as_posix()
        if relative not in paths:
            row.update(level="STOP", fix=TASK_DOC_FIX,
                       message=f"{raw}: документ задачи не входит в inventory выбранного модуля{_exclusion_note(inventory, relative)} — модель его не получит")


def execution_facts(project: Path, module: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the module can be built and tested by the package: ``{"ready", "reason", "fix"}`` (only reads)."""
    root = Path(project).resolve()
    module_root = root / str(module.get("root") or ".")
    test = module.get("test") if isinstance(module.get("test"), Mapping) else None
    language = (module.get("stack") or {}).get("language") if isinstance(module.get("stack"), Mapping) else None
    problems: list[tuple[str, str]] = []
    if test is None:
        problems.append(("у модуля нет тестового адаптера в .skillsrc", _init_command(project)))
    else:
        adapter = str(test.get("adapter_id"))
        if adapter in {"maven-wrapper:selected-symbols-v1", "gradle-wrapper:selected-symbols-v1"}:
            wrapper = module_root / str(test.get("wrapper") or "")
            usable = wrapper.is_file() and (os.name == "nt" or os.access(wrapper, os.X_OK))
            if not usable:
                state = "не исполняемый" if wrapper.is_file() else "нет"
                if adapter.startswith("gradle"):
                    problems.append((f"{test.get('wrapper')}: {state}; системный gradle не поддерживается, нужен wrapper",
                                     "добавьте gradle wrapper (gradlew) в проект или сделайте его исполняемым (chmod +x gradlew); агент wrapper не создаёт"))
                elif shutil.which("mvn"):
                    problems.append((f"{test.get('wrapper')}: {state}, но системный mvn есть",
                                     "переключите .skillsrc на системный mvn (проверка execution:skillsrc-switch)"))
                else:
                    problems.append((f"{test.get('wrapper')}: {state}, системного mvn нет",
                                     "установите Maven (mvn в PATH) или добавьте mvnw в проект (chmod +x mvnw); агент его не создаёт"))
        elif adapter == "maven:selected-symbols-v1":
            from tools.project_inventory import InventoryError, system_maven_path

            try:
                system_maven_path(str(test.get("executable")))
            except (InventoryError, OSError) as error:
                problems.append((f"системный mvn недоступен: {error}", "установите Maven и добавьте mvn в PATH"))
        elif adapter == "pytest:selected-symbols-v1":
            interpreter = module_root / str(test.get("interpreter") or "")
            if not interpreter.is_file():
                problems.append((f"интерпретатор модуля {test.get('interpreter')} не найден",
                                 f'создайте окружение модуля: python -m venv "{module_root / ".venv"}" и установите его тестовые зависимости'))
    if language == "java" and not (os.environ.get("JAVA_HOME") or shutil.which("java")):
        problems.append(("JDK не найден (нет JAVA_HOME и java в PATH)", "установите JDK и задайте JAVA_HOME"))
    if not problems:
        return {"ready": True, "reason": None, "fix": None}
    return {"ready": False, "reason": "; ".join(reason for reason, _fix in problems), "fix": "; ".join(fix for _reason, fix in problems)}


def _self_review_scan(project: Path, docs: Sequence[str], profile: str, module: str | None) -> str:
    """FaT-4b: the full scan command of this very request plus the flag — an agent runs a fix verbatim."""
    parts = [f'python -m tools.run_pipeline scan --project "{project}"', *(f'--docs "{doc}"' for doc in docs),
             f"--profile {profile}", *([f'--module "{module}"'] if module else []), "--accept-self-review"]
    return " ".join(parts)


def check(project: Path, docs: Sequence[str], profile: str = "local-pilot-v1", module: str | None = None) -> dict[str, Any]:
    project = Path(project).resolve()
    rows: list[dict[str, Any]] = [check_python()]
    deps = check_deps()
    rows.append(deps)
    rows.append(check_git(project))
    rows.extend(check_docs(project, docs))
    if deps["level"] != "OK":
        skipped = "пропущено до установки зависимостей пакета"
        rows.extend(_row(name, CASES, "WARN", skipped, deps.get("fix")) for name in ("pack", "skillsrc", "module", "scan"))
    else:
        rows.append(check_pack())
        for name, run in (("skillsrc", lambda: check_skillsrc(project)),):
            try:
                rows.extend(run())
            except Exception as error:
                rows.append(_row(name, CASES, "STOP", f"{type(error).__name__}: {error}", _init_command(project)))
        try:
            module_row, selected = check_module(project, module)
        except Exception as error:
            module_row, selected = _row("module", CASES, "STOP", f".skillsrc не читается: {type(error).__name__}: {error}", _init_command(project)), None
        rows.append(module_row)
        if selected is not None:
            try:
                scan_row, inventory = check_scan(project, selected)
                rows.append(scan_row)
                check_task_documents(rows, project, docs, inventory)
            except Exception as error:
                rows.append(_row("scan", CASES, "STOP", f"скан не прошёл: {type(error).__name__}: {error}", "исправьте причину и повторите ready"))
            if profile == "local-pilot-v1":
                facts = execution_facts(project, selected)
                rows.append(_row("execution:build", EXECUTION, "OK" if facts["ready"] else "WARN",
                                 "сборка и тесты модуля готовы" if facts["ready"] else
                                 f"сборка не готова: {facts['reason']}; без неё прогон пойдёт как cases-only-v1", facts["fix"]))
    # FaT-4b: a hint, never a STOP — whether the CLI opens a fresh reviewer is known only to the agent.
    rows.append(_row("review:isolation", CASES, "OK", "ревьюер кейсов нужен в свежем контексте (отдельный subagent); "
                     "если CLI этого не умеет — спросите человека о прогоне с пометкой SELF",
                     _self_review_scan(project, docs, profile, module)))
    blocked = any(row["level"] == "STOP" and row["block"] == CASES for row in rows)
    return {"status": "BLOCKED" if blocked else "READY", "project": str(project), "profile": profile, "checks": rows}


def _table(report: Mapping[str, Any]) -> str:
    lines = [f"Готовность: {'можно начинать кейсы' if report['status'] == 'READY' else 'есть СТОП'} — {report['project']}"]
    for block, title in ((CASES, "Для кейсов"), (EXECUTION, "Нужно только для исполнения")):
        rows = [row for row in report["checks"] if row["block"] == block]
        if rows:
            lines.append(f"[{title}]")
            for row in rows:
                lines.append(f"  {_LABELS[row['level']]:<9} {row['id']}: {row['message']}")
                if row.get("fix") and row["level"] != "OK":
                    lines.append(f"            исправить: {row['fix']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Readiness of a project for a run: every problem at once, with fixes.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--docs", action="append", default=[])
    parser.add_argument("--profile", choices=("cases-only-v1", "local-pilot-v1"), default="local-pilot-v1")
    parser.add_argument("--module")
    args = parser.parse_args(argv)
    report = check(Path(args.project), args.docs, args.profile, args.module)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    print(_table(report), file=sys.stderr)
    return 0 if report["status"] == "READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
