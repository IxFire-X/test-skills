"""What ``scan`` computes (v0.8, C2): the inputs and documents of the request, the choice of the run, its context and baseline, and
from the facts of a run its ``state`` and ``next`` (``_durable_status``, for scan and status).  Nothing here prints or decides an exit
code: the command ``scan`` is ``run_pipeline``.  ``HostStop`` and the printing of a value into a command for a human live here, the
lowest module of the commands."""

from __future__ import annotations

import sys
from pathlib import Path

import difflib
import hashlib
import json
import os
import re
import uuid
from typing import Any, Mapping, Sequence

from tools import execution_gate
from tools.existing_tests import own_tests
from tools.fixes import fix_for
from tools.run_facts import durable_status_facts as _durable_status_facts
from tools.project_inventory import (
    ContextSelectionError,
    InventoryError,
    context_filter,
    select_context_batches,
    skillsrc_exclude,
    skillsrc_include,
    skipped_summary,
    template_refs,
)

ROOT = Path(__file__).resolve().parents[1]

_SCAN_CONTEXT_BYTE_BUDGET = 256 * 1024  # default of .skillsrc limits.context_batch_bytes


class HostStop(RuntimeError):
    def __init__(self, code: str, message: str = "", fix: str | None = None):
        super().__init__(message or code)
        self.code = code
        self.fix = fix or fix_for(code, message)  # the table has a way out for the codes without a fix at their raise site (for the cause, when the message tells it)


# the text a human pastes into cmd.exe, PowerShell or sh.  One form for the three: a plain token as it is; a token with a space, a bracket
# or a backslash in double quotes (the same in all three); any other token is not printed.  What quotes cannot hold: ``"`` closes them, ``%``
# and ``!`` are expanded by cmd.exe inside them, ``^`` escapes there, ``$`` and the back quote are expanded by sh and PowerShell inside them, and
# ``& | < > ; ' *`` and the line breaks are not worth the doubt for a path; a backslash at the end of a token escapes the closing quote.  The
# dashes, « », № and ``?`` of a Russian heading are plain text inside double quotes to all three (“ ” „ ‘ ’ are quotes to PowerShell).
_BARE_TOKEN = re.compile(r"[A-Za-z0-9_./:=,#+\-]+")
_QUOTABLE_MARKS = " _./:=,#@+-\\()[]{}~—–«»№?"


def command_token(token: str) -> str:
    """The token as it is printed - plain, or in double quotes - or ``HostStop COMMAND_NOT_PRINTABLE`` naming what cannot be printed."""
    if _BARE_TOKEN.fullmatch(token):
        return token
    if all(char.isalnum() or char in _QUOTABLE_MARKS for char in token) and not token.endswith("\\") and "\\\\" not in token:
        return '"' + token + '"'
    bad = sorted({char for char in token if not (char.isalnum() or char in _QUOTABLE_MARKS)} | ({"\\"} if token.endswith("\\") or "\\\\" in token else set()))
    shown = " ".join(repr(char)[1:-1] if not char.isprintable() else char for char in bad)
    raise HostStop("COMMAND_NOT_PRINTABLE", f"в команде для человека есть символ(ы) {shown}: cmd.exe, PowerShell или sh прочтут их как подстановку, экранирование или кавычку "
                   f"(в «{token[:80]}»)",
                   f"переименуйте или переместите проект (или интерпретатор, модуль, документ) в каталог, в имени которого нет {shown}, и начните прогон заново "
                   "(scan --new-run): путь входит в напечатанную команду; для --docs файл#Раздел — раздел без этих символов в заголовке "
                   "или документ целиком")


def printed(value: object) -> str:
    """A value the code puts into a command the agent runs as it is: in double quotes, once ``command_token`` said it prints."""
    command_token(str(value))
    return '"' + str(value) + '"'


def printed_or(value: object, template: str) -> str:
    """``command_token`` for a value in a text that is only advice: the template when no shell takes the value as it is (the advice is not worth a refusal)."""
    try:
        return command_token(str(value))
    except HostStop:
        return template


def _digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _slug(target: str | None, docs: list[str] | None) -> str:
    if target:
        stem = Path(str(target).replace("\\", "/")).stem
        return re.sub(r"[^A-Za-z0-9._-]+", "-", stem) or "feature"
    if docs and len(docs) == 1:
        return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(docs[0]).stem).strip("-") or "project"
    return "project"


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(path.is_symlink() or attributes & 0x400)


def _confined(project: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    resolved.relative_to(project.resolve())
    return resolved


def _durable_status(project: Path, run_root: Path, *, stage: str, status: str = "ok", **extra: Any) -> dict[str, Any]:
    """The facts of the run and, from them, its ``state``, who is ``waiting_for`` and the ``next`` verb (``tools.stages``)."""
    from tools.pilot_state import read_run
    from tools.stages import next_action

    result = _durable_status_facts(project, run_root, stage=stage, status=status, **extra)
    cut = left = scope = None
    if result.get("attempt_id") is not None:
        try:  # the scan request of the run, to see a scan that was cut
            request = read_run(Path(run_root))["manifest"].get("scan_request")
        except (KeyError, OSError, TypeError, ValueError):
            request = None
        cut = _scan_cut(Path(run_root), request if isinstance(request, Mapping) else None)
        left = _left_mark(Path(run_root)) is not None  # a left run goes nowhere; the plain scan of its request starts anew
        if result.get("after_marker"):
            scope = _scope_pause(Path(run_root), str(result["attempt_id"]))
        if cut is not None and not left:  # nothing goes on from half a context
            result["scan_incomplete"] = f"scan оборван: записано {cut}; повтори тот же scan (scan_command) — он достроит недостающее в этом прогоне"
        if (left or cut is not None) and isinstance(request, Mapping):  # after /clear the flags of the request are nowhere else
            result["scan_command"] = _scan_command(project, request, extra=("--new-run",) if left else ())  # a left run: the command finish printed
        if left:
            result["attempt_note"] = "прогон оставлен до ревью кейсов: попытка в журнале не закрыта, итога нет — следующий шаг scan_command"
        note = _scope_mismatch(Path(run_root), str(result["attempt_id"]))
        if note:
            result["scope_warning"] = {"code": "SCOPE_DECISION_MISMATCH", "detail": note}
    result.update(next_action({**result, "scan_cut": cut, "left": left, "scope_pause": scope is not None,
                                 "scope_answer": Path((scope or {}).get("answer_slot", "")).is_file()}))
    result.update(scope or {})
    bare = False  # the stage of next is the role of the table, no step id: prepare and submit refuse it, so no command is printed
    if result["state"] in ("marker", "generator", "review", "automation", "automation_review"):  # the id of the step (with the revision), the slot and the size: from the steps of the stage
        from tools.stage_steps import stage_status

        table = result["next"]
        try:
            result.update(stage_status(Path(run_root), {"marker": "context-marker", "generator": "tc-generator", "review": "tc-reviewer", "automation": "tc-to-autotest",
                                                                  "automation_review": "autotest-reviewer"}[result["state"]]))
        except (OSError, KeyError, TypeError, ValueError):  # a plan that cannot be made leaves the verb of the table
            pass
        bare = result["next"] is table
    for gone in ("next_step", "after_marker", "last_confirmed_stage"):  # the state says it
        result.pop(gone, None)
    if not bare and (command := _next_command(project, Path(run_root), result["next"], result.get("abandon"))):
        result["next_command"] = command
    return result


def _next_command(project: Path, run_root: Path, step: Mapping[str, Any], abandon: str | None = None) -> str | None:
    """The command of ``next`` to copy as it is: prepare and submit live in ``tools.api``, finish and report in this module."""
    verb, stage = step.get("verb"), step.get("stage")
    if verb in ("prepare", "submit") and stage:
        return f"{printed(sys.executable)} -m tools.api {verb} --run-root {printed(run_root)} --stage {printed(stage)}"
    if verb in ("finish", "report"):
        return f"{printed(sys.executable)} -m tools.run_pipeline {verb} --project {printed(project)} --run {printed(run_root.name)}" + (f" --abandon {abandon}" if abandon and verb == "finish" else "")
    return None


def _scope_mismatch(run_root: Path, attempt_id: str) -> str | None:
    """Once the plan is published the scope is its ``out_of_scope``; a ``scope-decision.json`` that says another thing is named.  Only reads."""
    from tools import scope_pause

    try:
        published = scope_pause.published_scope(run_root, attempt_id)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return published["note"] if published is not None and published["mismatch"] else None


def _scope_pause(run_root: Path, attempt_id: str) -> dict[str, Any] | None:
    """After the marker the plan of the batches waits for the human's answer on the gaps of the marker.  Only reads; ``None`` until the
    marker has gaps, the human has not answered and the run keeps its pause."""
    from tools import scope_pause

    try:
        scope = scope_pause.scope_state(run_root, attempt_id)
    except scope_pause.ScopeRefused as error:  # a decision file that is not the human's answer: fail closed, say so
        return {"scope_warning": {"code": error.code, "detail": str(error)}}
    except (OSError, KeyError, TypeError, ValueError):
        return None
    if not scope["pending"]:
        return None
    from tools.stage_steps import slot

    return {**scope_pause.gap_summary(scope["gaps"]), "answer_slot": str(slot(run_root, "scope"))}


def _read_skillsrc_binding(path: Path) -> tuple[dict[str, Any], str]:
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    data = path.read_bytes()
    return parse_skillsrc_bytes(data, project=path.parent), _digest_bytes(data)


def _local_input(raw: str, flag: str) -> None:
    """A path of a scan flag on a share or a device is refused by its text, before anything resolves or opens it."""
    from tools.output_guard import local_path_text

    if not local_path_text(str(raw)):
        raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is not a local path: {raw}",
                       fix=f"укажите {flag} путём внутри проекта, не UNC и не путём устройства")


def _missing_path_fix(project: Path, missing: Path, flag: str) -> str:
    """The way out for a path that is not there: the names of its nearest existing folder that are close to it (at most three)."""
    root, parent = project.resolve(), missing.parent
    while parent != root and not parent.is_dir():
        parent = parent.parent
    try:
        names = sorted(entry.name for entry in os.scandir(parent))
    except OSError:
        names = []
    near = [(parent / name).relative_to(root).as_posix() for name in difflib.get_close_matches(missing.name, names, n=3)]
    return ("такого файла или папки в проекте нет: проверьте имя" + (f"; ближайшие: {', '.join(near)}" if near else "")
            + ("; новая область OpenSpec ещё не существует — назовите только папку изменения openspec/changes/<имя>" if flag == "--docs" else ""))


def _input_files(project: Path, paths: Sequence[str] | None, flag: str, *, outside_fix: str | None = None) -> list[tuple[str, bytes]]:
    """The files a scan flag names (``--docs``, ``--reference``, ``--cases``): inside the project, no reparse point, each
    path once. A path outside the project is the refusal ``SNAPSHOT_DOCS`` with *outside_fix*."""
    files: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    for raw in paths or []:
        _local_input(raw, flag)
        candidate = Path(raw)
        try:
            confined = _confined(project, candidate if candidate.is_absolute() else project / candidate)
        except ValueError:
            raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is outside the project: {raw}",
                           fix=outside_fix or f"укажите {flag} путём внутри проекта") from None
        if not os.path.lexists(confined):  # no such path: a typo, or an area a change only adds — not the link the table's fix is about
            raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path does not exist: {raw}", fix=_missing_path_fix(project, confined, flag))
        if _is_reparse(confined) or not confined.is_file():
            raise HostStop("SNAPSHOT_DOCS", f"{flag.removeprefix('--')} path is missing or a reparse point: {raw}")
        rel = confined.relative_to(project.resolve()).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        files.append((rel, confined.read_bytes()))
    return files


def _is_zephyr_csv(data: bytes) -> bool:
    """The file is a Zephyr Scale test-case CSV — the shared reader knows its columns (rows may still be wrong)."""
    from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv

    try:
        parse_zephyr_csv(data)
    except ZephyrCsvError as error:
        return error.code == "ZEPHYR_CSV_ROWS"
    return True


UNMARKED_BASELINE = 'в базе области {area} нет размеченных требований: кейсы будут только по изменению, регресс области не построится'  # the warning of scan and the row of ready


def expand_docs(project: Path, docs: list[str] | None) -> list[str]:
    """``--docs`` with each folder replaced by the files it stands for (``build_context.openspec_dir_files``), once at the start of a scan;
    every reader of the request then sees files.  The walk is of the one named folder: no link, no reparse point (checked before it is
    entered or listed), inside the project; the bytes are read later by ``_input_files`` and every file must be of the inventory."""
    from tools.build_context import docs_over_limit, openspec_dir_files

    root, expanded, sized, named = project.resolve(), [], {}, []
    for raw in docs or []:
        _local_input(raw, "--docs")  # a share or a device is never looked at
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else project / candidate
        try:
            confined = _confined(project, path)  # first: a path outside the project is not looked at (no type, no size), _input_files refuses it
        except ValueError:
            expanded.append(raw)
            continue
        if not path.is_dir():  # a file (maybe path#Section): _docs_entries reads it
            expanded.append(raw)
            if "#" not in raw and path.is_file() and not _is_reparse(path):  # a section is counted later, by what it holds; a link is refused by _input_files
                named.append(confined)
            continue
        found: list[str] = []
        files = None
        folder = confined.relative_to(root).as_posix()
        if openspec_dir_files(folder, []) is not None and not _is_reparse(path):  # the rule of the folder and the link first: no walk of what is no folder of OpenSpec
            for current, dirs, names in os.walk(confined, followlinks=False):
                dirs[:] = sorted(name for name in dirs if not _is_reparse(Path(current) / name))
                found += [(Path(current) / name).relative_to(root).as_posix() for name in sorted(names) if not _is_reparse(Path(current) / name)]
            files = openspec_dir_files(folder, found)
        if not files:
            raise HostStop("SNAPSHOT_DOCS", f"в папке {raw} нет файлов OpenSpec для прогона (или это не папка, которую код раскрывает)",
                           fix="папкой в --docs можно назвать openspec/specs, openspec/specs/<область>, openspec/changes/<имя> или openspec/changes/archive/<имя>; остальное — файлами")
        sized.update((str(root / name), (root / name).stat().st_size) for name in files)
        expanded += files
    if sized:  # the total is for the files a folder brings, with the files named beside it: a large document without a folder has its own stop (CONTEXT_TASK_DOCUMENT_TOO_LARGE)
        rest = [item.stat().st_size for item in dict.fromkeys(named) if str(item) not in sized]
        if docs_over_limit(list(sized.values()), rest):
            raise HostStop("NEED_DOCS_LIMIT", f"документы --docs: {len(sized) + len(rest)} файл(ов), {sum(sized.values()) + sum(rest)} байт — предел 256 КиБ на файл папки и 1 МиБ на все")
    return list(dict.fromkeys(expanded))


def _docs_entries(project: Path, docs: list[str] | None) -> list[dict[str, str]]:
    from tools.doc_sections import resolve_spec

    entries: list[dict[str, str]] = []
    sections: dict[str, list[str] | None] = {}  # --docs path#Section — the sections of the file, their headings by path (None: the whole file)
    files: list[tuple[str, bytes]] = []
    for raw in docs or []:
        _local_input(raw, "--docs")  # before resolve_spec looks for a file named with its #
        path, heading = resolve_spec(project, raw)
        for rel, data in _input_files(project, [path], "--docs"):
            if rel in sections:
                if (sections[rel] is None) != (heading is None):
                    raise HostStop("TASK_DOC_SECTION_CONFLICT", f"{rel} указан целиком и разделом",
                                   fix="файл — целиком (--docs путь) или разделами (--docs путь#Раздел, по одному на раздел), не вместе")
                if heading is not None and heading not in (sections[rel] or []):
                    sections[rel].append(heading)  # type: ignore[union-attr]
                continue
            sections[rel] = None if heading is None else [heading]
            files.append((rel, data))
    for rel, data in files:
        if _is_zephyr_csv(data):  # an export of cases is not a requirements document
            raise HostStop("DOCS_ZEPHYR_CSV", f"{rel} — выгрузка тест-кейсов Zephyr, а не документ требований",
                           fix=f"подайте её через scan --reference {printed_or(rel, '<файл>')} (эталон для сравнения), а в --docs — документ требований")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"DOCS_ENCODING: docs file is not UTF-8: {rel}; fix: сохрани документ в UTF-8 и повтори scan") from error
        entry = {"path": rel, "sha256": _digest_bytes(data), "content": text}
        if sections[rel] is not None:
            entry.update(_section_entry(rel, text, list(sections[rel] or [])))
            entry["sha256"] = _digest_bytes(entry["content"].encode("utf-8"))  # what the code's inventory is built from
        entries.append(entry)
    entries.sort(key=lambda row: row["path"])
    from tools.build_context import openspec_selected_changes

    if len(named := sorted(openspec_selected_changes([row["path"] for row in entries]))) > 1:  # one change per run; before any run exists
        raise HostStop("OPENSPEC_SOURCE_CONFLICT", "в --docs названы файлы нескольких живых изменений OpenSpec: " + ", ".join(named))
    return entries


def _cases_scope_files(entries: list[dict[str, str]], cases: str | None) -> None:
    """An update of cases (``--cases`` with ``--docs``) reads the scope of the documents' OpenSpec change: a named archive file that is no delta, or a change without an operation, stops the scan."""
    from tools.cases_scope import ScopeSourceError, scope

    try:
        if cases:
            if any(row.get("section") and row["path"].startswith("openspec/changes/archive/") for row in entries):  # a section is no context file: its names are never judged
                raise HostStop("SNAPSHOT_DOCS", "дельта архивного изменения названа разделом (путь#Раздел)", fix="назовите файл дельты целиком, без #Раздел")
            scope(entries, [row["path"] for row in entries])
    except ScopeSourceError as error:
        raise HostStop("SNAPSHOT_DOCS", str(error), fix=scope_fix(error)) from None


def scope_fix(error: Exception) -> str:
    """The way out of a delta file the scope cannot read (``ScopeSourceError``): the fix of the scan and of ``ready``."""
    return ("в дельте нет ## ADDED/MODIFIED/REMOVED/RENAMED Requirements: допишите операции или не называйте файл" if "нет операций" in str(error)
            else "архивное изменение называют файлом его дельты openspec/changes/archive/<имя>/specs/<область>/spec.md "
            "или папкой openspec/changes/archive/<имя>; живое — папкой openspec/changes/<имя>")


def _section_entry(rel: str, text: str, headings: list[str]) -> dict[str, str]:
    """The entry of ``path#Section`` (one or several sections of the file): ``content`` is the sections at their source line numbers (the rest of the lines
    empty), ``view`` the same with the note the model reads.  The digest is that of ``content`` and the requirement digest also
    binds the headings, joined by ``SECTION_JOIN`` in the order given (``_docs_projection``); a change of the file is drift of the inventory as before."""
    from tools.doc_sections import SECTION_JOIN, SectionMissing, find_section, view

    try:
        found = [find_section(text, heading, rel) for heading in headings]
    except SectionMissing as error:
        message, _, fix = str(error).partition("; fix: ")
        raise HostStop("TASK_DOC_SECTION_MISSING", message.removeprefix("TASK_DOC_SECTION_MISSING: "), fix=fix or None) from None
    return {"content": view(text, found, rel, note=False), "view": view(text, found, rel), "section": SECTION_JOIN.join(item.heading for item in found),
            "lines": ", ".join(f"{item.first}-{item.last}" for item in sorted(found, key=lambda item: item.first))}


def _reference_cases(project: Path, reference: str | None) -> tuple[str, bytes, list[dict[str, Any]]] | None:
    """``scan --reference <csv>`` read by the shared Zephyr reader; a reader refusal is the scan's refusal."""
    if not reference:
        return None
    from tools.zephyr_csv import ZephyrCsvError, parse_zephyr_csv

    from tools.zephyr_csv import require_human_csv

    rel, data = _input_files(project, [reference], "--reference")[0]
    try:
        cases = parse_zephyr_csv(data)
    except ZephyrCsvError as error:
        raise HostStop(error.code, f"{rel}: {error}", fix="--reference — экспорт тест-кейсов Zephyr Scale в CSV") from error
    try:
        require_human_csv(cases)  # the v4 Test Data holds the machine model, not the human data of a case
    except ZephyrCsvError as error:
        raise HostStop(error.code, f"{rel}: {error}", fix="выгрузите кейсы в профиле v5 (по умолчанию) или экспортом из Zephyr — "
                                                        "в Test Data должен быть текст человека") from error
    return rel, data, cases


def _docs_projection(snapshot: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"path": row["path"], "sha256": row["sha256"], **({"section": row["section"]} if "section" in row else {})} for row in snapshot]


def _execution_skipped(project: Path, args: Any) -> dict[str, str] | None:
    """``{"reason", "fix"}`` when the module of a local-pilot request cannot be built and tested by the package."""
    from tools.init_skillsrc import InitError, NeedsInput
    from tools.ready import execution_facts, proposed_modules, select_module
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc

    try:
        path = project / ".skillsrc"
        modules = normalize_skillsrc(load_skillsrc(path))["modules"] if path.is_file() else proposed_modules(project, getattr(args, "module", None))
    except (NeedsInput, InitError, SkillsrcError, OSError, ValueError, KeyError, TypeError):
        return None  # .skillsrc and discovery problems are reported by the scan itself
    module, _problem = select_module(modules, getattr(args, "module", None))
    if module is None:
        return None
    if (module.get("stack") or {}).get("language") == "documents":
        # a project of documents only has nothing to build — local-pilot is refused, not lowered
        raise HostStop("NO_BUILD_MODULE", "в проекте только документы, сборки и тестов нет: local-pilot-v1 недоступен",
                       "повтори scan с --profile cases-only-v1 — кейсы по документам без автотестов")
    if (module.get("paths") or {}).get("context") and not module.get("service_url_env"):
        # the tests call an application the package does not start; without the stand's address — cases only
        return {"reason": "модуль только с тестами: приложение поднимает человек, адрес стенда не объявлен",
                "fix": "укажите в .skillsrc у модуля service_url_env: <ИМЯ> — переменную окружения с адресом поднятого стенда"}
    facts = execution_facts(project, module)
    if not facts["ready"]:
        return {"reason": str(facts["reason"]), "fix": str(facts["fix"])}
    # the same baseline blockers the freeze would meet, known before the run is created.
    blockers = _baseline_blockers(project, module)
    if not blockers:
        return None
    return {"reason": "; ".join(f"{row['file']}: {row['reason']}" for row in blockers), "fix": "; ".join(row["fix"] for row in blockers)}


def _baseline_blockers(project: Path, module: Mapping[str, Any]) -> list[dict[str, str]]:
    from tools.project_inventory import InventoryError, baseline_blockers, build_inventory

    root = Path(project).resolve()
    module_root = (root / str(module.get("root") or ".")).resolve()
    try:
        inventory = build_inventory(root, module_root, skill_pack_root=_scan_skill_pack_root(root), generated_roots=(root / ".pilot-runs",))
    except (InventoryError, OSError, ValueError):
        return []  # the scan itself reports an inventory problem
    return baseline_blockers(root, inventory, module, "local-pilot-v1")


def _scan_disposition_policy(project: Path, args: Any) -> str | None:
    """``cleanup`` when this scan asks for the old FAIL cleaning; the flag beats `.skillsrc`, read only here."""
    flag = getattr(args, "disposition_policy", None)
    if flag is not None:
        return "cleanup" if flag == "cleanup" else None
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc

    try:
        document = load_skillsrc(project / ".skillsrc")  # .skillsrc is a JSON object
    except (OSError, SkillsrcError):
        return None
    return "cleanup" if document.get("disposition_policy") == "cleanup" else None


def _init_skillsrc_run(project: Path, args: Any, scan_request: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], dict[str, Any]]:
    from tools.init_skillsrc import ensure_skillsrc
    from tools.pilot_state import create_lowered_run, create_run
    from tools.skillsrc_manifest import skillsrc_review_self as _skillsrc_review_self

    if (project / ".skillsrc").is_file():
        _read_skillsrc_binding(project / ".skillsrc")  # an input that is refused is refused before the run exists — no orphan run without MODULE_SELECTED
    profile = getattr(args, "profile", "local-pilot-v1")
    skipped = _execution_skipped(project, args) if profile == "local-pilot-v1" else None
    if skipped is not None:
        # no build facts — the cases still run; the run says it is cases-only and what to fix for execution.
        profile = "cases-only-v1"
        args.profile = profile
    authorization = {"request_id": f"scan-{uuid.uuid4().hex[:12]}", "execution_requested": profile == "local-pilot-v1"}
    if getattr(args, "accept_self_review", False) or _skillsrc_review_self(project):  # the flag, or .skillsrc review.self: true
        authorization["accept_self_review"] = True
    if _scan_disposition_policy(project, args) == "cleanup":
        authorization["disposition_policy"] = "cleanup"
    if scan_request is not None:
        from tools.pilot_state import create_scan_run

        run = create_scan_run(project, profile, authorization, scan_request, skipped)
    else:
        run = create_run(project, profile, authorization) if skipped is None else create_lowered_run(project, authorization, skipped)
    if (project / ".skillsrc").is_file():
        receipt = {"status": "unchanged"}
    else:
        # ``--target`` is C-lite context scope, not an instruction to mutate .skillsrc.
        receipt = ensure_skillsrc(project, {}, write=True, selected_module=getattr(args, "module", None))
    return run, receipt


def _resolve_scan_module(project: Path, module_id: str | None, skillsrc_document: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    """Select one exact existing module without constructing an execution adapter."""
    from tools.skillsrc_manifest import normalize_skillsrc, resolve_module_root, select_module

    from tools.skillsrc_manifest import SkillsrcError

    normalized = normalize_skillsrc(skillsrc_document)
    try:
        module = select_module(normalized, module_id)
    except SkillsrcError as error:
        if error.code != "module_unknown":
            raise
        # --module takes the id of .skillsrc, or the folder of that module (the id of a plain folder is its path)
        known = ", ".join(f"{row['id']} (root {row.get('root')})" for row in normalized["modules"])
        ids = [str(row["id"]) for row in normalized["modules"]]
        raise HostStop("MODULE_UNKNOWN", f"модуля {module_id} нет в .skillsrc: --module — id модуля или путь его папки; модули: {known}",
                       f"--module {printed_or(ids[0], '<id>')}" if len(ids) == 1 else "--module <id>: " + " | ".join(ids)) from error
    return resolve_module_root(project, module), module


def _scan_skill_pack_root(project: Path) -> Path | None:
    try:
        ROOT.resolve().relative_to(project.resolve())
    except ValueError:
        return None
    return ROOT


def _context_scope_ids(inventory: Mapping[str, Any], project: Path, module_root: Path, target: str | None) -> list[str]:
    files = inventory.get("files")
    if not isinstance(files, list):
        raise HostStop("INVENTORY_INVALID", "inventory files are invalid")
    if target is None:
        return [str(item["opaque_id"]) for item in files if isinstance(item, Mapping) and isinstance(item.get("opaque_id"), str)]
    raw = str(target).replace("\\", "/").strip()
    candidate = Path(raw)
    if not raw or candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts) or ":" in raw:
        raise HostStop("SOURCE_TARGET_INVALID", "target must stay inside selected module")
    target_path = module_root / Path(*candidate.parts)
    if _is_reparse(target_path) or not target_path.exists():
        raise HostStop("SOURCE_TARGET_INVALID", "target is missing or a reparse point")
    try:
        target_rel = target_path.resolve().relative_to(project.resolve()).as_posix()
        target_path.resolve().relative_to(module_root.resolve())
    except ValueError as error:
        raise HostStop("SOURCE_TARGET_INVALID", "target escapes selected module") from error
    prefix = target_rel + "/"
    selected = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and isinstance(item.get("opaque_id"), str)
        and isinstance(item.get("project_path"), str)
        and (item["project_path"] == target_rel or item["project_path"].startswith(prefix))
    ]
    if not selected:
        raise HostStop("SOURCE_TARGET_INVALID", "target contains no eligible inventory file")
    return selected


def _filtered_context(inventory: Mapping[str, Any], project: Path, module_root: Path, selected_ids: list[str],
                      task_ids: list[str], budget: int, own: Mapping[str, str]) -> tuple[list[str], list[dict[str, Any]], dict[str, Any]]:
    """Assets, user excludes, large non-sources and the tests of earlier runs (``own``) stay out of the model context, always listed (scan and ready); then the templates ``--target`` cut off that the code names."""
    rules = {"excludes": skillsrc_exclude(project, module_root), "task_ids": task_ids, "includes": skillsrc_include(project, module_root), "own": own}
    kept, skipped = context_filter(inventory, project, selected_ids, byte_budget=budget, **rules)
    found = template_refs(inventory, project, kept, set(selected_ids), budget)
    if found["items"]:  # the templates come with the same rules as the code
        kept, skipped = context_filter(inventory, project, [*selected_ids, *(item["opaque_id"] for item in found["items"])], byte_budget=budget, **rules)
    came = [item for item in found["items"] if item["opaque_id"] in set(kept)]
    return kept, sorted([*skipped, *found["over"]], key=lambda row: row["project_path"]), {
        "ids": [item["opaque_id"] for item in came], "paths": [item["project_path"] for item in came], "bytes": sum(int(item["size"]) for item in came), "unfound": found["unfound"]}


def _without_cases_csv(ids: list[str], inventory: Mapping[str, Any], *paths: str | None) -> list[str]:
    """The CSV of ``--cases`` (the model gets its skeleton) and of ``--reference`` (a hint) is never context of a model; the inventory keeps it."""
    named = {str(item["opaque_id"]) for item in inventory["files"] if item["project_path"] in paths and item["project_path"]}
    return [opaque for opaque in ids if opaque not in named]


def context_preview(inventory: Mapping[str, Any], project: Path, module_root: Path, docs: list[str] | None,
                    target: str | None = None, cases: str | None = None) -> tuple[list[str], list[dict[str, Any]], int, dict[str, Any]]:
    """What the scan would hand the model, without a run: ``(ids, skipped, batch budget, the templates the code names)``; only reads."""
    task_ids, other_task_ids, _summary, _warnings = _task_document_scope(inventory, project, docs, module_root=module_root, cases=bool(cases))
    selected = [opaque for opaque in _context_scope_ids(inventory, project, module_root, target) if opaque not in other_task_ids]
    selected += [opaque for opaque in task_ids if opaque not in selected]
    selected = _without_cases_csv(selected, inventory, cases and relative_to_project(project.resolve(), cases))
    budget = _scan_context_budget(inventory, project)
    kept, skipped, templates = _filtered_context(inventory, project, module_root, selected, task_ids, budget, own_tests(project, inventory)[0])
    return kept, skipped, budget, templates


def _write_section_views(project: Path, run_root: Path, docs: list[str] | None) -> list[dict[str, str]]:
    """``<run>/doc-sections/<name>.md`` — the view of each ``path#Section`` (the lines of the section at their source
    numbers); the rows tell the agent which file the model reads instead of the document."""
    from tools.confined_output import atomic_write_confined_bytes_at_root
    from tools.doc_sections import SECTION_JOIN, view_name

    rows = []
    for entry in _docs_entries(project, docs):
        if "section" not in entry:
            continue
        name = view_name(entry["path"], entry["section"].split(SECTION_JOIN))
        target = run_root / "doc-sections" / name
        atomic_write_confined_bytes_at_root(project, run_root, target, entry["view"].encode("utf-8"))
        rows.append({"path": entry["path"], "section": entry["section"], "lines": entry["lines"],
                     "view": target.relative_to(project).as_posix()})
    return rows


def _scan_context_budget(inventory: Mapping[str, Any], project: Path | None = None) -> int:
    """The byte budget of one context batch: ``.skillsrc limits.context_batch_bytes``, else 256 KiB."""
    del inventory
    path = None if project is None else project / ".skillsrc"
    if path is not None and path.is_file():
        from tools.skillsrc_manifest import load_skillsrc

        value = (load_skillsrc(path).get("limits") or {}).get("context_batch_bytes")
        from tools.step_size import whole_limit

        value = whole_limit(value)
        if value is not None and value > 0:
            return value
    return _SCAN_CONTEXT_BYTE_BUDGET


def _scan_execution_baseline(
    project: Path,
    module_root: Path,
    module: Mapping[str, Any],
    inventory: Mapping[str, Any],
    args: Any,
) -> Mapping[str, Any]:
    from tools.project_inventory import (
        InventoryError,
        build_execution_baseline,
        build_skillsrc_authority_receipt,
        runtime_identity,
        system_maven_path,
    )

    files = inventory.get("files")
    if not isinstance(files, list):
        raise InventoryError("inventory files are invalid")
    skillsrc = next((item for item in files if item.get("project_path") == ".skillsrc"), None)
    if not isinstance(skillsrc, Mapping):
        raise InventoryError("execution baseline lacks .skillsrc")
    module_path = str(inventory.get("module"))
    prefix = "" if module_path == "." else module_path.rstrip("/") + "/"
    # a task document outside the module is a proved dependency file of the baseline, not a parent build manifest
    outside_docs = {row["path"] for row in _docs_entries(project, getattr(args, "docs", None))} if prefix else set()
    dependency_ids = [
        str(item["opaque_id"]) for item in files
        if isinstance(item, Mapping) and item.get("origin") != "context" and item.get("project_path") in outside_docs
        and not str(item.get("project_path", "")).startswith(prefix)
    ]
    parent_ids = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and item.get("opaque_id") != skillsrc.get("opaque_id")
        and item.get("origin") != "context"
        and prefix
        and not str(item.get("project_path", "")).startswith(prefix)
        and item.get("opaque_id") not in dependency_ids
    ]
    context_ids = [str(item["opaque_id"]) for item in files if isinstance(item, Mapping) and item.get("origin") == "context"]
    execution_ids = [
        str(item["opaque_id"])
        for item in files
        if isinstance(item, Mapping)
        and item.get("opaque_id") != skillsrc.get("opaque_id")
        and item.get("opaque_id") not in parent_ids
        and item.get("opaque_id") not in dependency_ids
        and item.get("opaque_id") not in context_ids
    ]
    policy_profile = getattr(args, "profile", "local-pilot-v1")
    runtime_facts: dict[str, Any] = {}
    if policy_profile == "local-pilot-v1":
        test = module.get("test")
        if not isinstance(test, Mapping):
            raise InventoryError("selected module test configuration is invalid")
        adapter_id = str(test.get("adapter_id"))
        runtime_facts.update(adapter_id=adapter_id, build_profile=str(test.get("build_profile")), adapter_parameters=dict(test.get("adapter_parameters") or {}))
        if adapter_id == "maven:selected-symbols-v1":
            executable = str(system_maven_path(test.get("executable")))
            runtime_facts.update(executable_path=executable, executable_identity=runtime_identity(module_root, executable, adapter_id=adapter_id))
        else:
            interpreter = str(test["interpreter"]) if isinstance(test.get("interpreter"), str) else None
            wrapper = str(test["wrapper"]) if isinstance(test.get("wrapper"), str) else None
            runtime_facts.update(interpreter_path=interpreter, wrapper_path=wrapper,
                interpreter_identity=runtime_identity(module_root, interpreter) if interpreter is not None else None,
                wrapper_identity=runtime_identity(module_root, wrapper) if wrapper is not None else None)
    requirement = {
        "module_id": module.get("id"),
        "selected_target": getattr(args, "target", None),
        "docs": _docs_projection(_docs_entries(project, getattr(args, "docs", None))),
        # the CSV of --cases is the requirement document of an import; its bytes bind the requirement digest
        **({"cases": [{"path": rel, "sha256": _digest_bytes(data)} for rel, data in _input_files(project, [args.cases], "--cases")]}
           if getattr(args, "cases", None) else {}),
    }
    requirement_digest = _digest_bytes(
        json.dumps(requirement, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return build_execution_baseline(
        inventory,
        project_root=project,
        requirements={"requirement_id": f"scan-{_slug(getattr(args, 'target', None), getattr(args, 'docs', None))[:48]}", "digest": requirement_digest},
        skillsrc_file_id=str(skillsrc["opaque_id"]),
        skillsrc_authority=build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes()),
        execution_file_ids=execution_ids,
        parent_build_file_ids=parent_ids,
        proved_dependency_file_ids=dependency_ids,
        context_file_ids=context_ids,
        policy_profile=policy_profile,
        **runtime_facts,
    )


def _scan_request(args: Any) -> dict[str, Any]:
    """The scan flags as given (the requested profile before any lowering) — kept in the run manifest."""
    cases = getattr(args, "cases", None)
    return {
        "module": getattr(args, "module", None), "docs": [str(doc) for doc in (getattr(args, "docs", None) or [])],
        "target": getattr(args, "target", None), "profile": getattr(args, "profile", "local-pilot-v1"),
        "accept_self_review": bool(getattr(args, "accept_self_review", False)),
        "disposition_policy": getattr(args, "disposition_policy", None),
        **({"no_pause": True} if getattr(args, "no_pause", False) else {}),  # a run without the pause after the marker
        **({"reference": str(args.reference)} if getattr(args, "reference", None) else {}),
        **({"cases": str(cases)} if cases else {}),
    }


def relative_to_project(project: Path, raw: Any) -> str:
    """A path as the person typed it (relative or absolute), as the path inside the project with posix separators — the form the context and the inventory know."""
    from tools.output_guard import local_path_text

    if not local_path_text(str(raw)):  # a share or a device is never looked at: not even by resolve()
        return str(raw).replace("\\", "/")
    path = Path(str(raw))
    try:
        return (path if path.is_absolute() else project / path).resolve().relative_to(project).as_posix()
    except (OSError, ValueError):
        return str(raw).replace("\\", "/")


def _request_key(project: Path, request: Mapping[str, Any]) -> tuple[Any, ...]:
    """The same request: the module (resolved by .skillsrc when not given), the --docs paths, the requested profile, the
    --reference and the --cases CSV; a request without --cases (and every journal of an earlier version) has ``None``."""
    module = request.get("module")
    if module is None:
        try:
            from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

            modules = normalize_skillsrc(load_skillsrc(project / ".skillsrc"))["modules"]
            module = modules[0].get("id") if len(modules) == 1 else None
        except Exception:  # an unreadable .skillsrc: the scan itself will say so
            module = None
    docs = [relative_to_project(project, raw) for raw in request.get("docs") or []]
    cases = relative_to_project(project, request["cases"]) if request.get("cases") else None
    return module, tuple(sorted(docs)), request.get("profile"), request.get("reference"), cases


def _open_run_of(project: Path, request: Mapping[str, Any], skip: Sequence[Path] = ()) -> tuple[Path | None, list[str]]:
    """An unfinished run of the same request — its scan selected the context (CONTEXT_SELECTED) and its last
    attempt has no ATTEMPT_TERMINAL.  A run whose scan failed earlier never blocks; runs without ``scan_request``
    (created by an earlier version) are never compared.

    ``(the live run that blocks, the abandoned ones)``.  An unfinished run whose baseline check finds
    drift or fails is abandoned: it cannot go on, so it never blocks; the scan names it and leaves it as it is."""
    from tools import pilot_state as pilot

    runs = project / ".pilot-runs"
    abandoned: list[str] = []
    if not runs.is_dir():
        return None, abandoned
    key = _request_key(project, request)
    for root in sorted(path for path in runs.iterdir() if path.is_dir()):
        if root in skip:  # a cut run the scan could not finish - the caller named it
            continue
        try:
            stored = pilot.read_run(root)["manifest"].get("scan_request")
            if not isinstance(stored, Mapping) or _request_key(project, stored) != key:
                continue
            state = pilot.derive_state(root)
            if not state["attempts"]:
                continue
            attempt = state["attempts"][-1]
            selected = any(event["event_type"] == "CONTEXT_SELECTED" and event.get("attempt_id") == attempt["attempt_id"]
                           for event in state["events"])
            if not selected and attempt.get("state") != "TERMINAL":  # a scan cut before its first context batch
                abandoned.append(f"{root.name} (scan оборван до первой пачки контекста)")
            if selected and attempt.get("state") != "TERMINAL":
                why = _left_mark(root) or _abandoned(project, root, attempt)
                if why is None:
                    return root, abandoned
                abandoned.append(f"{root.name} ({why})")
        except (OSError, KeyError, TypeError, ValueError):
            continue
    return None, abandoned


def _abandoned(project: Path, root: Path, attempt: Mapping[str, Any]) -> str | None:
    """Why an unfinished run cannot go on — the same baseline check as materialize/ingest and status; None when it can."""
    module_root = project if attempt.get("module") == "." else project.joinpath(*str(attempt.get("module")).split("/"))
    try:
        result = _baseline_check(project, root, attempt, module_root)
    except Exception:  # an unreadable baseline: the run cannot resume
        return "сверка baseline не прошла"
    if result.get("status") == "UNCHANGED":
        return None
    changed = result.get("changed") or []
    return f"дрейф: {len(changed)} файл(ов)" if changed else "сверка baseline не прошла"


def _run_in_progress(project: Path, root: Path, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from tools.finalize_attempt import finish_closes
    from tools.pilot_state import derive_state

    python = f"{printed(sys.executable)} -m tools.run_pipeline"
    try:
        facts = _durable_status(project, root, stage="status")
        where = {key: facts[key] for key in ("state", "waiting_for", "next")}
    except (HostStop, OSError, KeyError, TypeError, ValueError):
        where = {}
    try:
        closes = finish_closes(root, str(derive_state(root)["attempts"][-1]["attempt_id"]))
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        closes = False
    # finish only where it closes the attempt (rework, a refused automation), never as a dead end
    finish = f"закрыть его без продолжения: {python} finish --project {printed(project)} --run {root.name}; " if closes else ""
    return {
        "status": "error", "stage": "scan", "reason": "SCAN_RUN_IN_PROGRESS", "run_id": root.name, "run_root": str(root),
        **where,
        "message": "для этого проекта, документа и профиля уже есть незавершённый прогон; новый не создан",
        "fix": (f"продолжите его: {python} status --project {printed(project)} --run {root.name}; "
                f"{finish}нужен ещё один прогон рядом или этот не закрыть — "
                + (_scan_command(project, request, extra=("--new-run",)) if request is not None else "повторите этот scan с флагом --new-run")),
    }


def _scan_command(project: Path, request: Mapping[str, Any], *, extra: Sequence[str] = ()) -> str:
    """The scan of the same request, its flags as given — printed for the human; the code never runs it.  Every value through
    ``printed`` (``COMMAND_NOT_PRINTABLE`` for one that cmd.exe, PowerShell or sh would read otherwise)."""
    parts = [f"{printed(sys.executable)} -m tools.run_pipeline scan --project {printed(project)}"]
    if request.get("module"):
        parts.append(f"--module {printed(request['module'])}")
    parts += [f"--docs {printed(doc)}" for doc in request.get("docs") or []]
    if request.get("target"):
        parts.append(f"--target {printed(request['target'])}")
    parts.append(f"--profile {command_token(request.get('profile') or 'local-pilot-v1')}")
    if request.get("accept_self_review"):
        parts.append("--accept-self-review")
    if request.get("no_pause"):
        parts.append("--no-pause")
    if request.get("disposition_policy"):
        parts.append(f"--disposition-policy {command_token(request['disposition_policy'])}")
    if request.get("reference"):
        parts.append(f"--reference {printed(request['reference'])}")
    if request.get("cases"):  # the import of the same CSV, never a run of cases from code
        parts.append(f"--cases {printed(request['cases'])}")
    return " ".join([*parts, *extra])


def _baseline_check(project: Path, root: Path, attempt: Mapping[str, Any], module_root: Path, request: Any = None) -> Mapping[str, Any]:
    """The one baseline check of materialize/ingest (with the request) and status (from the baseline alone); only reads."""
    from tools.project_inventory import (
        baseline_external_input_paths,
        build_inventory,
        runtime_identity,
        validate_execution_baseline,
    )
    from tools.pilot_state import frozen_baseline, read_attempt_receipt

    baseline = frozen_baseline(root, attempt)
    try:
        durable_delta = read_attempt_receipt(root, str(attempt["attempt_id"]), "generated-delta", "ARTIFACT_READ_BACK")["record"]["delta"]
    except (KeyError, OSError, TypeError, ValueError):
        if request is not None:
            raise
        durable_delta = {"files": []}
    module_prefix = "" if attempt["module"] == "." else str(attempt["module"]).rstrip("/") + "/"
    generated_paths = {module_prefix + str(row["path"]) for row in durable_delta["files"] if isinstance(row, Mapping)}
    current = build_inventory(
        project, module_root, skill_pack_root=ROOT, generated_roots=(root.parent,),
        proved_dependency_files=baseline_external_input_paths(project, baseline),
    )
    current["files"] = [row for row in current["files"] if row.get("project_path") not in generated_paths]
    body = {key: value for key, value in current.items() if key != "digest"}
    current["digest"] = "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if request is not None:
        # materialize/ingest: always against local-pilot-v1 with the runtime identity — never the baseline's own profile
        policy, adapter_id, build_profile, parameters = "local-pilot-v1", request.adapter_id, request.build_profile, dict(request.typed_parameters)
        executable = request.executable if request.adapter_id == "maven:selected-symbols-v1" else None
    else:
        # status: the baseline's own profile, so that a cases-only attempt also names its changed files
        policy, adapter_id, build_profile = str(baseline.get("policy_profile")), baseline.get("adapter_id"), baseline.get("build_profile")
        parameters = baseline.get("adapter_parameters") if policy == "local-pilot-v1" else None
        executable = baseline.get("executable_path") if adapter_id == "maven:selected-symbols-v1" else None
    runtime = executable or str(baseline.get("interpreter_path") or baseline.get("wrapper_path"))
    vanished = None
    try:
        identity = runtime_identity(module_root, runtime, adapter_id=baseline.get("adapter_id")) if policy == "local-pilot-v1" else None
    except (OSError, ValueError):
        if request is not None:
            raise
        # status names the vanished runtime and still compares the files against the scan
        vanished = (runtime if adapter_id == "maven:selected-symbols-v1" else module_prefix + runtime) + " (runtime недоступен)"
        identity_key = ("interpreter_identity" if baseline.get("adapter_id") == "pytest:selected-symbols-v1"
                        else "executable_identity" if baseline.get("adapter_id") == "maven:selected-symbols-v1" else "wrapper_identity")
        identity = baseline.get(identity_key)
    result = validate_execution_baseline(
        baseline, current, requirements=baseline["requirements"], adapter_id=adapter_id, build_profile=build_profile,
        adapter_parameters=parameters, runtime_identity_value=identity, policy_profile=policy, project_root=project,
    )
    if vanished is None:
        return result
    return {**result, "status": "DECLARED_INPUT_DRIFT", "changed": [vanished, *(result.get("changed") or [])]}


def check_cases(project: Path, cases: str) -> tuple[str, int]:
    """``(path in the project, number of cases)`` of the Zephyr CSV of ``--cases`` — only reads; a refusal is ``HostStop`` or ``CasesImportError`` (scan and ready)."""
    from tools.cases_import import CasesImportError, read_cases

    [(rel, data)] = _input_files(project, [cases], "--cases",
                                 outside_fix="CSV кейсов должен лежать внутри проекта: укажите --cases путём от корня проекта")
    from tools.cases_import import password_cells, secret_cell
    from tools.project_inventory import _secret_rule

    cases_read = read_cases(data)
    passwords = password_cells(cases_read)  # a plain password in a cell, by row and column, never the value
    if passwords:
        raise CasesImportError("IMPORT_CSV_SECRET", f"{rel}: " + "; ".join(passwords[:5]))
    secret = _secret_rule(project / rel, data, rel)  # the rule the inventory would leave the CSV out by
    if secret is not None:
        cell = secret_cell(cases_read, secret[0])  # and the cell it is in, as for a password
        raise CasesImportError("IMPORT_CSV_SECRET", f"{rel}: " + (f"{cell}: " if cell else "")
                               + f"правило секретов {secret[0]} ({secret[1]}) — файл не войдёт в опись")
    return rel, len(cases_read)


def _scan_profile(args: Any) -> None:
    """No ``--profile`` is what the package can do; ``local-pilot-v1`` asked of a package without its execution files
    is refused before any run exists."""
    profile, refused = execution_gate.resolve_profile(getattr(args, "profile", None))
    if refused:
        raise HostStop("PROFILE_NOT_IN_PACKAGE", refused)
    args.profile = profile


def _module_warnings(module: Mapping[str, Any], given: str | None) -> list[str]:
    """The warnings of the selected module: ``--module`` in the old hex form, a derived level of the autotests."""
    from tools.module_ids import legacy_warning
    from tools.skillsrc_manifest import level_derived_note

    note = level_derived_note(module) if execution_gate.execution_available() else None  # no execution in the delivery: no level to speak of
    found = [legacy_warning(module, given), f"TEST_LEVEL_DERIVED: {note[0]}; fix: {note[1]}" if note else None]
    return [item for item in found if item]


def _skillsrc_preflight(project: Path) -> None:
    """``.skillsrc`` is read before a run is created — a directory, a link, an unreadable or a foreign file is a refusal
    with its fix and no run behind it (the run_id line of a scan means a run exists)."""
    from tools.skillsrc_manifest import load_skillsrc, skillsrc_file_problem

    path = project / ".skillsrc"
    problem = skillsrc_file_problem(path)
    if problem is not None:
        raise HostStop("SKILLSRC_UNREADABLE", problem)
    if path.is_file():
        load_skillsrc(path)


_SCHEMA_CAUSE = re.compile(r"schema validation failed: [a-z0-9.-]+\.schema\.json")


def _inventory_invalid_detail(error: BaseException) -> str:
    """The cause of INVENTORY_INVALID — the kind of error and the schema or the module it names, never a text of the project."""
    from tools.pilot_state import ModulePathError

    if isinstance(error, InventoryError):
        return str(error)
    if isinstance(error, OSError):
        return "scan input is unreadable"
    if isinstance(error, ModulePathError):
        return f"scan input or receipt is invalid: {error}"
    if isinstance(error, ValueError) and _SCHEMA_CAUSE.fullmatch(str(error)):
        return f"scan input or receipt is invalid: {error}"
    return "scan input or receipt is invalid"


def _scan_context_plan(project: Path, args: Any, run_root: Path, frozen_inventory: Mapping[str, Any], module_root: Path,
                       task_info: tuple[list[str], set[str], dict[str, Any]]) -> dict[str, Any]:
    """The context batches of the scan from the frozen inventory and the request (one function for a scan and for the repeat
    that finishes it); ``task_info`` is the result of ``_task_document_scope`` — its summary is filled in place.  Writes only the
    views of the document sections of the run."""
    task_ids, other_task_ids, context_summary = task_info
    selected_ids = _context_scope_ids(frozen_inventory, project, module_root, getattr(args, "target", None))
    # the task document of --docs always goes to the model; the other tasks of to_do do not.
    selected_ids = [opaque for opaque in selected_ids if opaque not in other_task_ids]
    selected_ids += [opaque for opaque in task_ids if opaque not in selected_ids]
    context_summary["document_sections"] = _write_section_views(project, run_root, getattr(args, "docs", None))
    by_id = {str(item["opaque_id"]): str(item["project_path"]) for item in frozen_inventory["files"]}
    reference = _reference_cases(project, getattr(args, "reference", None))
    selected_ids = _without_cases_csv(selected_ids, frozen_inventory, getattr(args, "cases", None), reference and reference[0])
    context_summary["code_files"] = sum(1 for opaque in selected_ids if opaque not in task_ids and "to_do" not in by_id[opaque].split("/"))
    budget = _scan_context_budget(frozen_inventory, project)
    own, unread = own_tests(project, frozen_inventory)
    selected_ids, skipped, templates = _filtered_context(frozen_inventory, project, module_root, selected_ids, task_ids, budget, own)
    context_skipped = skipped_summary(skipped)
    if templates["paths"]:
        context_summary["resource_files"] = {"files": len(templates["paths"]), "bytes": templates["bytes"], "paths": templates["paths"][:10]}
    batches = select_context_batches(
        frozen_inventory,
        project,
        selected_ids,
        byte_budget=budget,
        skipped=skipped,
        front=templates["ids"],
    )
    if reference is not None:
        context_summary["reference_cases"] = len(reference[2])
    if not batches:  # the closed manifest .skillsrc is always in the context, so this is not an honest run: nothing to cut or finish
        raise ContextSelectionError("the scan selected no context batch")
    return {"batches": batches, "context_skipped": context_skipped, "reference": reference, "own_runs_unread": unread,
            "own_files": sum(row["reason"] == "CONTEXT_OWN_TEST" for row in skipped)}  # the files hidden, not the files that are own


def _publish_context(run_root: Path, attempt_id: str, plan: Mapping[str, Any]) -> None:
    """The receipts of the plan the attempt has no event for, in batch order, then the reference receipt (all idempotent)."""
    from tools import pilot_state as pilot

    have = {str(event["artifact_digest"]) for event in pilot.derive_state(run_root)["events"]
            if event.get("attempt_id") == attempt_id and event["event_type"] == "CONTEXT_SELECTED"}
    for batch in plan["batches"]:
        if str(batch["receipt"]["digest"]) not in have:
            pilot.publish_context_selection(run_root, attempt_id, batch["receipt"])
    reference = plan["reference"]
    if reference is not None:  # a receipt for the review hint; never context or generator input
        pilot.publish_reference_cases(run_root, attempt_id, *reference)


def _scan_payload(project: Path, run_root: Path, args: Any, module: Mapping[str, Any], scope: Mapping[str, Any],
                  frozen_inventory: Mapping[str, Any], exclusion_receipt: Mapping[str, Any], baseline_digest: str, attempt_id: str,
                  plan: Mapping[str, Any], context_summary: Mapping[str, Any], context_warnings: Sequence[str],
                  abandoned: Sequence[str], **extra: Any) -> dict[str, Any]:
    """The answer of a scan that made (or finished) the run."""
    context_skipped = plan["context_skipped"]
    unread = plan["own_runs_unread"]
    if getattr(args, "cases_read", None):
        context_warnings = [*context_warnings, f"CSV {args.cases}: кейсов {args.cases_read}"]
    if unread:  # the human must see why the input is larger than it was meant to be (a moved project, a damaged run), and which runs
        context_warnings = [*context_warnings, f"OWN_TESTS_UNREAD: прошлых прогонов не прочитано: {len(unread)} ({', '.join(unread[:5])}{f' и ещё {len(unread) - 5}' if len(unread) > 5 else ''}; папка проекта перенесена или скопирована, "
                            "прогон повреждён или занят другой командой); это не ошибка, работу можно продолжать — тесты, записанные ими в проект, остаются во входе модели, вход больше"]
    return _durable_status(
        project,
        run_root,
        stage="scan",
        status="ok",
        module_id=module.get("id"),
        scope=scope,
        selected_target=getattr(args, "target", None),
        inventory_digest=frozen_inventory["digest"],
        exclusion_digest=exclusion_receipt["digest"],
        baseline_digest=baseline_digest,
        inventory_file_count=len(frozen_inventory["files"]),
        exclusion_count=len(exclusion_receipt["exclusions"]),
        context_batch_count=len(plan["batches"]),
        context_summary=context_summary,
        **({"context_skipped": context_skipped} if context_skipped["files"] else {}),
        **({"own_files": plan["own_files"]} if plan["own_files"] else {}),
        **({"own_runs_unread": len(unread)} if unread else {}),
        **({"warnings": list(context_warnings)} if context_warnings else {}),
        **_skillsrc_defaults(project, module),
        **({"abandoned_runs": list(abandoned)} if abandoned else {}),
        **extra,
        reuse=_marker_reuse(run_root, attempt_id),
    )


def _scan_cut(run_root: Path, request: Mapping[str, Any] | None = None) -> str | None:
    """Why the scan of the run stopped before it was done - the context batches written against the count the
    receipts carry, or the reference receipt of ``--reference`` - else None.  Only reads."""
    from tools import pilot_state as pilot

    try:
        state = pilot.derive_state(run_root)
        attempt_id = str(state["attempts"][-1]["attempt_id"])
        if state["attempts"][-1].get("state") == "TERMINAL":
            return None
        unfinished = pilot.context_incomplete(run_root, attempt_id, state)
        if unfinished is not None:
            return f"{unfinished[0]} из {unfinished[1]} пачек контекста"
        if isinstance(request, Mapping) and request.get("reference") and pilot.read_reference_cases(run_root, attempt_id) is None:
            return "нет квитанции --reference"
    except (OSError, KeyError, IndexError, TypeError, ValueError):
        return None
    return None


def _marker_reuse(run_root: Path, attempt_id: str) -> dict[str, Any]:
    """The scan suggests an earlier accepted run whose context marker has the very same inputs; the agent decides."""
    from tools.pilot_state import marker_reuse_suggestion

    try:
        return marker_reuse_suggestion(run_root, attempt_id)
    except (ValueError, OSError, KeyError, TypeError) as error:  # a suggestion never stops the scan
        return {"run_id": None, "reason": f"поиск не удался: {error}"}


def _skillsrc_defaults(project: Path, module: Mapping[str, Any]) -> dict[str, Any]:
    """A test framework the build files of the module do not show was taken by default — say so."""
    from tools.discover_project import module_test_evidenced

    framework = (module.get("test") or {}).get("framework") if isinstance(module.get("test"), Mapping) else None
    if framework is None or module_test_evidenced(project, module):
        return {}
    return {"skillsrc_defaults": [
        f"{module.get('id')}: test.framework={framework} указан в .skillsrc, а в сборке модуля нет признака тестового "
        f"фреймворка; проверьте, что тесты модуля на {framework}, иначе исправьте .skillsrc"]}


TASK_DOC_FIX = ("документ задачи должен лежать внутри проекта, вне исключённых папок (.git, build, node_modules и т. п.), "
                "быть текстом без секретов и не лежать в самом пакете: перенесите его или уберите названную причину")


def task_doc_fix(path: str) -> str:
    return TASK_DOC_FIX + ("; файл взят из папки в --docs? назовите области файлами, без этого" if path.startswith("openspec/") else "")


def _under_to_do(project_path: str) -> bool:
    return "to_do" in project_path.split("/")[:-1]


def _outside_task_files(project: Path, module_root: Path, docs: list[str] | None) -> list[Path]:
    """The task documents of --docs that lie in the project outside the module (the root of a multi-module project):
    they join the inventory as parent files, bound by its digest, and are read as documents only.  One the rules of the
    inventory leave out stops the scan with its rule."""
    from tools.project_inventory import outside_task_documents

    files, problems = outside_task_documents(project, module_root, [row["path"] for row in _docs_entries(project, docs)],
                                             skill_pack_root=_scan_skill_pack_root(project))
    for path, problem in problems.items():
        raise HostStop("TASK_DOC_OUTSIDE_CONTEXT", f"документ задачи {path} не может войти в inventory: {problem}", task_doc_fix(path))
    return files


def _task_document_scope(inventory: Mapping[str, Any], project: Path, docs: list[str] | None, module_root: Path | None = None,
                         cases: bool = False) -> tuple[list[str], set[str], dict[str, Any], list[str]]:
    """``(ids of the task documents, ids of other tasks' files in to_do, summary, warnings)``.

    A document of --docs must be a file of the module's inventory (its bytes are bound by the inventory digest);
    otherwise the scan stops with TASK_DOC_OUTSIDE_CONTEXT — cases without their requirements are no cases.
    """
    files = [item for item in inventory.get("files", []) if isinstance(item, Mapping)]
    by_path = {str(item["project_path"]): item for item in files}
    to_do = [item for item in files if _under_to_do(str(item["project_path"]))]
    from tools.build_context import (RequirementConflict, openspec_applied, openspec_change_only, openspec_unmarked_areas, openspec_context_only, openspec_gone_names, openspec_live_changes,
                                     openspec_partial_archives, openspec_selected_changes, openspec_unnamed_archive_areas, openspec_unnamed_areas, openspec_unnamed_baseline,
                                     openspec_unselected)
    from tools.scan_rules import include_mode

    if not docs:
        excludes = skillsrc_exclude(project, module_root) if module_root else ()  # the filter of the context leaves these out: the model does not get them
        if len(live := sorted(openspec_live_changes([path for path in by_path if include_mode(path, excludes) != "full"]))) > 1:  # no answer of the model can compose two
            raise HostStop("OPENSPEC_SOURCE_CONFLICT", "в проекте несколько живых изменений OpenSpec, а --docs не задан: " + ", ".join(live),
                           fix="повторите scan с --docs: папка одного изменения (openspec/changes/<имя>) и папки или spec.md его областей (openspec/specs/<область>) — --new-run не нужен")
        warnings = []
        if to_do:
            size = sum(int(item.get("size") or 0) for item in to_do)
            warnings.append(f"модели уйдут все документы из to_do — {len(to_do)} файл(ов), {size} байт; укажите --docs документа задачи")
        return [], set(), {"task_documents": [], "other_tasks_skipped": 0}, warnings
    entries = _docs_entries(project, docs)
    task_paths = [row["path"] for row in entries]
    task_ids: list[str] = []
    for path in task_paths:
        item = by_path.get(path)
        if item is None:
            raise HostStop("TASK_DOC_OUTSIDE_CONTEXT", f"документ задачи {path} не входит в inventory выбранного модуля"
                           + _exclusion_note(inventory, path), task_doc_fix(path))
        task_ids.append(str(item["opaque_id"]))
    others = {str(item["opaque_id"]) for item in to_do if str(item["project_path"]) not in task_paths}
    task_count = len(others)
    base = openspec_change_only(task_paths, list(by_path), bool(cases))
    if unnamed := [path for path in openspec_unnamed_areas(task_paths, list(by_path)) if path not in base]:  # a change of an area whose spec.md is no requirements of the run
        raise HostStop("OPENSPEC_SOURCE_CONFLICT", "изменение OpenSpec меняет область, чей spec.md не назван в --docs: " + ", ".join(unnamed),
                       fix="назовите spec.md области изменения: " + " ".join(f"--docs {printed(path)}" for path in unnamed))
    composed = entries
    if base:  # change only (25.11): the spec.md of its areas is the base, the other spec.md and the proposal give the neighbours — as the marker composes them
        excludes = skillsrc_exclude(project, module_root) if module_root else ()
        areas = [path for path in by_path if openspec_unnamed_baseline(path, task_paths) and path not in base and include_mode(path, excludes) != "full"]
        proposals = [path for name in sorted(openspec_selected_changes(task_paths)) if (path := f"openspec/changes/{name}/proposal.md") in by_path]
        context = []
        for path in [*areas, *proposals]:  # files the person did not name: one that does not read gives no neighbours, never a stop
            try:
                context += _docs_entries(project, [path])
            except (HostStop, ValueError):
                continue
        composed = entries + [{**row, "base": True} for row in _docs_entries(project, base)] + [{**row, "context": True} for row in context]
    try:  # what the delta says, composed with the specification: a real difference stops the scan here, not after the step of the model
        applied, removed = openspec_applied(composed)
    except RequirementConflict as conflict:
        raise HostStop(conflict.code, str(conflict)) from conflict
    if cases and (partial := openspec_partial_archives(task_paths, list(by_path))):  # an update of cases judges the whole archived change: no delta is left out silently
        raise HostStop("OPENSPEC_SOURCE_CONFLICT", "названы не все дельты архивного изменения: " + ", ".join(partial),
                       fix="назовите архивное изменение папкой: " + " ".join(f"--docs {printed('openspec/changes/archive/' + name)}" for name in partial))
    if cases and (unnamed := openspec_unnamed_archive_areas(task_paths, list(by_path))):  # the cases of an area whose spec.md is not named would stay unjudged
        raise HostStop("OPENSPEC_SOURCE_CONFLICT", "архивное изменение меняет область, чей spec.md не назван в --docs: " + ", ".join(unnamed),
                       fix="назовите spec.md области изменения: " + " ".join(f"--docs {printed(path)}" for path in unnamed))
    gone, touched = openspec_gone_names(entries) if cases else ([], 0)
    if gone and len(gone) == touched:  # no name of the delta is a requirement now: no row would be judged, and no answer of the model can change that
        raise HostStop("OPENSPEC_SOURCE_CONFLICT", "имена дельты не найдены среди нынешних требований: " + ", ".join(gone[:5]),
                       fix="назовите в --docs spec.md области, где эти требования есть сейчас, или дельту, которая их меняет")
    skipped = openspec_unselected(list(by_path), task_paths)  # the other live changes and the archive: not selected by --docs, no context
    others |= {str(by_path[path]["opaque_id"]) for path in skipped}
    warnings = [f"{path} — OpenSpec контекст (proposal/design/tasks), не требования: строк требований из него нет" for path in task_paths if openspec_context_only(path)]
    warnings += [f"имена дельты не найдены среди нынешних требований, их кейсы не судятся: {', '.join(gone[:5])}"] * bool(gone)
    warnings += [f"изменение {name}: уже вписано в спецификацию, требований: {count}; они берутся из изменения, как обычно" for name, count in sorted(applied.items())]
    warnings += removed  # a REMOVED whose name the specification lacks: gone already, or a name that does not match
    warnings += [UNMARKED_BASELINE.format(area=area) for area in openspec_unmarked_areas([row for row in entries if not row.get("section")])]  # a prose spec.md: no regression of the area
    if skipped:
        names = sorted({path.split("/")[2] for path in skipped if not path.startswith("openspec/changes/archive/")})
        archive = sum(path.startswith("openspec/changes/archive/") for path in skipped)
        size = sum(int(by_path[path].get("size") or 0) for path in skipped)
        warnings.append("OpenSpec: не применяются и в модель не идут: " + ", ".join(names + ([f"архив ({archive} файл(ов))"] if archive else []))
                        + f" — {len(skipped)} файл(ов), {size} байт; изменение выбирают файлы в --docs")
    return task_ids, others, {"task_documents": task_paths, "other_tasks_skipped": task_count}, warnings


def _exclusion_note(inventory: Mapping[str, Any], path: str) -> str:
    for row in inventory.get("exclusions", []):
        excluded = str(row.get("project_path"))
        if path == excluded or path.startswith(excluded + "/"):
            return f" (исключён правилом {row.get('scanner_rule_id')}: {excluded})"
    return " (он вне корня модуля)"


def _left_mark(root: Path) -> str | None:
    """Why the scan skips this run — only when the mark names the run's last attempt (read confined)."""
    from tools.pilot_state import left_attempt

    try:
        return "оставлен до ревью кейсов (finish --abandon LEFT_BEFORE_REVIEW)" if left_attempt(root) else None
    except (OSError, ValueError):
        return None


# Public names for run_pipeline (the command scan and status) and the modules that take them: the same objects under names
# without an underscore (P1).
baseline_check = _baseline_check
digest_bytes = _digest_bytes
durable_status = _durable_status
read_skillsrc_binding = _read_skillsrc_binding
docs_entries = _docs_entries
cases_scope_files = _cases_scope_files
reference_cases = _reference_cases
init_skillsrc_run = _init_skillsrc_run
resolve_scan_module = _resolve_scan_module
scan_skill_pack_root = _scan_skill_pack_root
scan_execution_baseline = _scan_execution_baseline
scan_request = _scan_request
open_run_of = _open_run_of
run_in_progress = _run_in_progress
scan_command = _scan_command
scan_profile = _scan_profile
module_warnings = _module_warnings
skillsrc_preflight = _skillsrc_preflight
inventory_invalid_detail = _inventory_invalid_detail
scan_context_plan = _scan_context_plan
publish_context = _publish_context
scan_payload = _scan_payload
scan_cut = _scan_cut
outside_task_files = _outside_task_files
task_document_scope = _task_document_scope
scan_baseline_blockers = _baseline_blockers
exclusion_note = _exclusion_note
scan_context_budget = _scan_context_budget
