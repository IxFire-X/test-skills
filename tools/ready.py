"""Readiness of a project for a first run: every check at once, each problem with the command that fixes it.

``python -m tools.ready --project <abs> --docs <path> [--docs …] [--profile …] [--module <ID>]`` only reads: it starts
no process.  It never stops at the first problem: Python and the package dependencies, the pack itself (doctor), git, the requirement
documents, ``.skillsrc``, the module, the scan with its exclusions and — as a separate block that cases do not need — the
build (wrapper, system ``mvn``, JDK, the module interpreter, the project's style checks).  The JSON
report goes to stdout, a table to stderr; the exit code is 0 when cases can start.

Only the standard library at module level, so a broken pack is reported by a check and not by a traceback: every part of
the package is imported inside the check that needs it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.json_cli import JsonArgumentParser

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


def check_python() -> dict[str, Any]:
    version = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info >= (3, 11):
        return _row("python", CASES, "OK", f"Python {version} ({sys.executable})")
    return _row("python", CASES, "STOP", f"Python {version} старше 3.11", "запустите пакет интерпретатором Python 3.11–3.13")


def check_deps() -> dict[str, Any]:
    """The package needs no third-party software: only its own schema validator has to import."""
    try:
        from tools.schema_engine import Draft202012Validator

        if Draft202012Validator({"type": "string"}).is_valid("x"):
            return _row("deps", CASES, "OK", "собственный валидатор схем импортируется; стороннее ПО не нужно")
        problem = "валидатор вернул неверный ответ"
    except Exception as error:
        problem = f"{type(error).__name__}: {error}"
    return _row("deps", CASES, "STOP", f"не импортируется собственный валидатор схем (tools/schema_engine.py): {problem}",
                "сверьте папку пакета с поставкой")


def check_pack() -> dict[str, Any]:
    try:
        from tools.doctor import inspect_environment

        report = inspect_environment(PACK)
    except Exception as error:  # the doctor itself is broken: say so, keep checking
        return _row("pack", CASES, "STOP", f"doctor не запустился: {type(error).__name__}: {error}", "сверьте папку пакета с поставкой")
    if report.get("status") == "PASS":
        return _row("pack", CASES, "OK", "пакет цел (doctor PASS)")
    missing = report.get("integrity", {}).get("missing") or []
    crlf = report.get("crlf")  # only the line endings differ — the fix is the .gitattributes line, not a new copy of the package
    return _row("pack", CASES, "STOP", "doctor: " + "; ".join([*map(str, missing), *map(str, report.get("messages") or [])]),
                crlf["fix"] if isinstance(crlf, dict) else "сверьте папку пакета с поставкой (файлы и SHA-256)", {"doctor": report})


def _git_row(project: Path, profile: str) -> dict[str, Any]:
    """The ``git`` row.  No profile runs ``git``: ``git status`` can run a clean filter or a hook of the project's own configuration.
    The row stays because the table of ``ready`` has always had it; it says only what the package does."""
    return _row("git", CASES, "OK", "git не нужен: git не вызывается, проект сравнивается по описи файлов")


def check_docs(project: Path, docs: Sequence[str]) -> list[dict[str, Any]]:
    rows = []
    root = Path(project).resolve()
    if not docs:
        return [_row("docs", CASES, "STOP", "не указан ни один документ требований", "добавьте --docs <путь внутри проекта>")]
    from tools.doc_sections import resolve_spec
    from tools.output_guard import local_path_text

    for raw in docs:
        if not local_path_text(raw):  # a share or a device: by its text, never resolved
            rows.append(_row(f"docs:{raw}", CASES, "STOP", f"{raw}: не локальный путь (UNC или устройство)", "положите документ внутрь проекта и укажите путь от его корня"))
            continue
        spec, heading = resolve_spec(root, raw)  # path#Section
        candidate = Path(spec)
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        check_id = f"docs:{raw}"
        try:
            path.relative_to(root)
        except ValueError:
            rows.append(_row(check_id, CASES, "STOP", f"{raw} вне проекта", "положите документ внутрь проекта и укажите путь от его корня"))
            continue
        if path.is_dir():  # scan opens it (build_context.openspec_dir_files); ready says so below if it cannot
            rows.append(_row(check_id, CASES, "OK", f"{raw} — папка: scan берёт файлы OpenSpec под ней"))
            continue
        if not path.is_file():
            rows.append(_row(check_id, CASES, "STOP", f"{raw} не найден", "проверьте путь от корня проекта"))
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            rows.append(_row(check_id, CASES, "STOP", f"{raw} не в UTF-8", "сохраните документ в кодировке UTF-8"))
            continue
        if heading is not None:
            from tools.doc_sections import SectionMissing, find_section

            try:
                section = find_section(text, heading, spec)
            except SectionMissing as error:
                message, _, fix = str(error).partition("; fix: ")
                rows.append(_row(check_id, CASES, "STOP", message.removeprefix("TASK_DOC_SECTION_MISSING: "), fix or "укажите заголовок файла"))
                continue
            rows.append(_row(check_id, CASES, "OK", f"{raw} — раздел, строки {section.first}–{section.last} из {path.stat().st_size} Б файла"))
            continue
        rows.append(_row(check_id, CASES, "OK", f"{raw} — {path.stat().st_size} Б"))
    return rows


def _init_command(project: Path) -> str:
    from tools.run_pipeline import printed

    return f"{printed(sys.executable)} -m tools.init_skillsrc --project {printed(project)}"


_LEGACY_KEYS = {"limits": "limits", "review": "review", "service_url_env": "service_url_env", "include": "paths.include",
                "projection": "projection"}
_LEGACY_KEY_LINE = re.compile(r"^(?P<indent>[ \t-]*)(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:(?P<rest>.*)$")


def legacy_yaml_keys(project: Path) -> list[str] | None:
    """The lines of an older YAML ``.skillsrc`` that carry settings worth moving into the JSON one (``limits``, ``review``,
    ``service_url_env``, ``paths.include``, ``projection``); None when the file is missing or is a JSON object.  YAML is not
    parsed: the lines are read as text, so nothing here can execute or fail on the content."""
    path = Path(project) / ".skillsrc"
    try:
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    if not text.strip() or text.lstrip().startswith(("{", "[")):  # a JSON array is no YAML either (invalid_shape, with its fix)
        return None
    from tools.skillsrc_manifest import json_with_comments

    if json_with_comments(text):  # a file that begins with // is JSON with comments, not YAML
        return None
    lines, found, depth = text.splitlines(), [], None
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if depth is not None:  # the body of the key that was found: deeper lines, comments and blanks stay out of it
            if stripped and not stripped.startswith("#") and len(line) - len(line.lstrip()) > depth:
                found.append(f"  {line.rstrip()[:200]}")
                continue
            depth = None
        match = _LEGACY_KEY_LINE.match(line)
        if match and match["key"] in _LEGACY_KEYS:
            found.append(f"{_LEGACY_KEYS[match['key']]} (строка {number}): {line.strip()[:200]}")
            depth = len(match["indent"].replace("-", " "))
    return found[:60]


def check_run_root(project: Path) -> list[dict[str, Any]]:
    """A ``.pilot-runs`` that is a link (a junction, a symlink) is a STOP row with the fix of scan's refusal; no row when it is not."""
    from tools.fixes import fix_for
    from tools.pilot_state import run_root_is_unsafe

    if not run_root_is_unsafe(project):
        return []
    return [_row("run_root", CASES, "STOP", ".pilot-runs в проекте — ссылка (junction или симлинк): прогоны по ссылкам не пишутся", fix_for("RUN_ROOT_UNSAFE"))]


def _question_text(questions: Any) -> str:
    """The first question of the discovery the package cannot answer itself, in the words of the tester (the raw questions stay in ``details``/``fix``)."""
    first = next((item for item in questions or [] if isinstance(item, Mapping)), {})
    field = str(first.get("field", "?"))
    if field.endswith(".paths.source"):
        return f"пакет не нашёл, где лежит исходный код модуля {field.removeprefix('modules.').removesuffix('.paths.source')}. Скажите агенту папку исходников (от корня модуля)."
    if not field.endswith(".test.framework"):
        return f"пакет не смог сам определить настройку проекта ({field}). Скажите агенту, что указать."
    options = ", ".join(str(item.get("id")) for item in first.get("options") or [] if isinstance(item, Mapping))
    return f"пакет не смог сам выбрать, чем запускать тесты модуля {field.removeprefix('modules.').removesuffix('.test.framework')}. Скажите агенту вариант из списка: {options}."


def check_skillsrc(project: Path, module: str | None = None) -> list[dict[str, Any]]:
    """Validity of .skillsrc and its drift from the project, through the existing init flow (no write)."""
    from tools.fixes import fix_for
    from tools.init_skillsrc import answers_hint, ensure_skillsrc
    from tools.skillsrc_manifest import COMMENTS_FIX, COMMENTS_MESSAGE, json_with_comments, skillsrc_file_problem

    unreadable = skillsrc_file_problem(Path(project) / ".skillsrc")  # the scan's SKILLSRC_UNREADABLE, seen here first
    if unreadable is not None:
        return [_row("skillsrc", CASES, "STOP", f"SKILLSRC_UNREADABLE: {unreadable}", fix_for("SKILLSRC_UNREADABLE"))]
    try:
        commented = json_with_comments((Path(project) / ".skillsrc").read_bytes().decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        commented = False
    if commented:  # said as it is, before the file is taken for anything else
        return [_row("skillsrc", CASES, "STOP", COMMENTS_MESSAGE, COMMENTS_FIX)]
    legacy = legacy_yaml_keys(project)
    if legacy is not None:
        keys = legacy or ["в файле нет limits, review, service_url_env, paths.include, projection — переносить нечего"]
        return [_row("skillsrc", CASES, "STOP", ".skillsrc в старом формате YAML: пакет читает JSON",
                     "переименуйте .skillsrc в .skillsrc.yaml.bak, создайте новый: " + _init_command(project) + " --write; затем перенесите в него "
                     "значения этих ключей (в JSON): " + " | ".join(keys), {"legacy_yaml_keys": keys})]
    receipt = ensure_skillsrc(Path(project), {}, write=False, selected_module=module)
    status = receipt.get("status")
    if module is None and (status == "conflict" or status == "needs_input" and not (Path(project) / ".skillsrc").is_file()) and module_choice(project):
        return []  # no question about a module (the first of the list) before the module is named: the module row says so
    if not (Path(project) / ".skillsrc").is_file():
        return [_row("skillsrc", CASES, "OK", ".skillsrc нет — первый scan создаст его по проекту")] if status in {"preview", "created"} else \
            [_row("skillsrc", CASES, "STOP", ".skillsrc не создаётся: " + (_question_text(receipt.get("questions")) if status == "needs_input"
                                                                         else f"{status} {receipt.get('errors') or receipt.get('questions')}"),
                  answers_hint(Path(project), receipt.get("questions") or []) if status == "needs_input" else _init_command(project))]
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
        diff = {"paths": diff, "preserved": receipt.get("_preserved") or [], "test_before": before, "test_after": after}
        check_id, block = ("execution:skillsrc-switch", EXECUTION) if switch else ("skillsrc", CASES)
        message = ("в .skillsrc записан wrapper, которого нет в проекте; предлагается системный mvn" if switch
                   else ".skillsrc расходится с проектом; предлагается замена")
        return [_row(check_id, block, "WARN", message, fix, {"diff": diff, "approval": approval})]
    if status == "needs_input":
        # The scan takes an existing valid .skillsrc as it is; a discovery question only says detection is unsure.
        return [_row("skillsrc", CASES, "OK", ".skillsrc валиден; scan возьмёт его как есть (обнаружение по проекту не однозначно)",
                     None, {"questions": receipt.get("questions")})]
    if status == "conflict":
        return [_row("skillsrc", CASES, "STOP", f".skillsrc требует ответа ({status})", answers_hint(Path(project), receipt.get("questions") or []),
                     {"questions": receipt.get("questions")})]
    if receipt.get("_fix"):  # a foreign file — what is wrong in it (the key or the line) and the way out
        return [_row("skillsrc", CASES, "STOP", f".skillsrc невалиден: {receipt.get('_message')}", str(receipt["_fix"]))]
    return [_row("skillsrc", CASES, "STOP", f".skillsrc невалиден: {', '.join(map(str, receipt.get('errors') or [status]))}", _init_command(project) + " --write")]


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


def _modules(project: Path, selected: str | None = None) -> list[dict[str, Any]]:
    from tools.skillsrc_manifest import load_skillsrc, normalize_skillsrc

    path = Path(project) / ".skillsrc"
    if path.is_file():
        return list(normalize_skillsrc(load_skillsrc(path))["modules"])
    return proposed_modules(project, selected)


def proposed_modules(project: Path, selected: str | None = None) -> list[dict[str, Any]]:
    """The modules the first scan would write into a missing .skillsrc."""
    from tools.discover_project import discover_project
    from tools.init_skillsrc import compile_skillsrc, not_selected_defaults
    from tools.skillsrc_manifest import normalize_skillsrc

    root = Path(project).resolve()
    discovery = discover_project(root)
    return list(normalize_skillsrc(compile_skillsrc(discovery, not_selected_defaults(discovery, selected)[0], root))["modules"])


def select_module(modules: Sequence[Mapping[str, Any]], module_id: str | None) -> tuple[Mapping[str, Any] | None, str | None]:
    if module_id is not None:
        from tools.module_ids import select

        found = select(modules, module_id)  # the exact id first, then the id of the folder in its current or old form
        return (found, None) if found is not None else (None, f"модуля {module_id} нет; есть: {', '.join(str(m.get('id')) for m in modules)}")
    if len(modules) == 1:
        return modules[0], None
    return None, f"модулей несколько: {', '.join(str(m.get('id')) for m in modules)}" if modules else "модулей нет"


def module_choice(project: Path) -> str | None:
    """«назовите модуль: --module <id>; модули: …» for a project of several modules when none is named (up to 12 ids), else None."""
    from tools.discover_project import discover_project

    try:
        found = _modules(project) if (Path(project) / ".skillsrc").is_file() else discover_project(Path(project).resolve())["modules"]
    except Exception:  # the other rows say what is wrong with the project
        return None
    ids = [str(item["id"]) for item in found]
    return f"назовите модуль: --module <id>; модули: {', '.join(ids[:12])}" + (f" …и ещё {len(ids) - 12}" if len(ids) > 12 else "") if len(ids) > 1 else None


def check_module(project: Path, module_id: str | None) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    choice = module_choice(project) if module_id is None else None
    if choice:
        return _row("module", CASES, "STOP", choice, "повторите ready и scan с --module <id> из этого списка"), None
    module, problem = select_module(_modules(project, module_id), module_id)
    if module is None:
        return _row("module", CASES, "STOP", str(problem), "укажите --module <ID> из списка"), None
    from tools.module_ids import legacy_warning
    from tools.pilot_state import module_path_problem

    problem = module_path_problem(Path(project).resolve(), module.get("root"))  # what create_attempt checks, before the scan meets it
    if problem is not None:
        return _row("module", CASES, "STOP", f"модуль {module.get('id')}: {problem}",
                    "в .skillsrc у модуля root — путь его папки от корня проекта (папка есть, без «..» и ссылок); поправьте и повторите ready"), None
    legacy = legacy_warning(module, module_id)
    if legacy is not None:  # the old hex id works; the row says what to write instead
        return _row("module", CASES, "WARN", f"модуль {module.get('id')} ({module.get('root')}); {legacy}",
                    legacy.split("пишите ", 1)[1]), module
    return _row("module", CASES, "OK", f"модуль {module.get('id')} ({module.get('root')})"), module


SHARED_ROWS = {  # by stand.SHARED_DATA_DEFAULT: what a module without the key gets, and the key that says otherwise
    False: ("общая среда не отмечена (shared_data нет): кейсы пишутся как для среды, где других записей нет",
            "если кейсы проходят там, где есть чужие записи, — впишите в .skillsrc у модуля {id} \"shared_data\": true"),
    True: ("среда модуля считается общей (shared_data нет): кейсы создают записи с меткой прогона и удаляют их шагом «Уборка»",
           "если среда только ваша — впишите в .skillsrc у модуля {id} \"shared_data\": false"),
}


def check_shared_data(module: Mapping[str, Any]) -> dict[str, Any] | None:
    """One OK row for a module without ``shared_data`` (a module at a stand is shared and needs none): what the package takes and the key that says otherwise."""
    from tools.skillsrc_manifest import module_test_level
    from tools.stand import SHARED_DATA_DEFAULT

    if "shared_data" in module or (module_test_level(module) or ("",))[0] == "stand":
        return None
    said, fix = SHARED_ROWS[SHARED_DATA_DEFAULT]
    return _row("data:shared", CASES, "OK", said, fix.format(id=module.get("id")))


def check_test_level(module: Mapping[str, Any]) -> dict[str, Any]:
    """The level of the module's autotests — declared (OK) or derived (WARN with the key to write); a module without tests has none."""
    from tools.skillsrc_manifest import level_derived_note, module_test_level

    found = module_test_level(module)
    if found is None:
        return _row("test:level", CASES, "OK", "у модуля нет блока test: уровня автотестов нет")
    if (module.get("paths") or {}).get("context") and not module.get("service_url_env"):  # before the derived «stand»: it needs the variable
        return _row("test:level", CASES, "WARN", f"у модуля {module.get('id')} есть paths.context, но нет service_url_env: scan сделает только кейсы",
                    f"если нужны автотесты против стенда — впишите в .skillsrc у модуля {module.get('id')} service_url_env: <ИМЯ> — переменную "
                    "окружения с адресом поднятого стенда")
    note = level_derived_note(module)
    if note is not None:
        return _row("test:level", CASES, "WARN", f"TEST_LEVEL_DERIVED: {note[0]}", note[1])
    return _row("test:level", CASES, "OK", f"уровень автотестов: {found[0]} (задан в .skillsrc)")


def _docs_rels(root: Path, docs: Sequence[str]) -> list[str]:
    """The existing files of --docs inside the project, as paths from its root (the others are the docs rows' business).
    ``путь#Раздел`` is the file ``путь``; the heading is the docs row's business."""
    from tools.doc_sections import resolve_spec
    from tools.output_guard import local_path_text

    rels = []
    for raw in docs:
        if not local_path_text(raw):  # the docs row has refused it
            continue
        candidate = Path(resolve_spec(root, raw)[0])
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if path.is_file() and path.is_relative_to(root):
            rels.append(path.relative_to(root).as_posix())
    return rels


def check_openspec_base(project: Path, docs: Sequence[str], paths: Sequence[str] = (), cases: bool = False, excludes: Sequence[str] = ()) -> list[dict[str, Any]]:
    """An area a change of OpenSpec touches whose spec.md has no «### Requirement:» — the line scan warns with, before any model; a docs row says a document that does not read.
    A run of the change only (``paths`` — the inventory, ``excludes`` — paths.exclude of .skillsrc): the lines of its scope, its neighbours and what does not apply, as scan says them."""
    import hashlib

    from tools.build_context import RequirementConflict, openspec_applied, openspec_change_only, openspec_selected_changes, openspec_unmarked_areas, openspec_unnamed_baseline
    from tools.openspec_scope import DISPUTED, NEIGHBOURS, PARTIAL, SCOPE
    from tools.output_guard import OutputRefused, project_file
    from tools.run_scan import UNMARKED_BASELINE, expand_docs
    from tools.scan_rules import include_mode

    root = Path(project).resolve()
    try:
        files = expand_docs(root, list(docs))
    except Exception:  # the docs rows and the context row say what does not read
        return []
    named = _docs_rels(root, files)

    def read(rels: Sequence[str], **mark: bool) -> list[dict[str, Any]]:  # inside the project only, as check_size reads; no link on the way (project_file), as scan reads
        found = []
        for rel in rels:
            try:
                text = project_file(root, rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError, OutputRefused):
                continue
            found.append({"path": rel, "sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(), "content": text, **mark})
        return found

    entries = read([rel for rel in named if rel.startswith("openspec/")])
    rows = [_row("openspec:unmarked", CASES, "WARN", UNMARKED_BASELINE.format(area=area), "это не стоп; чтобы был регресс области, владелец базы размечает требования разделами `### Requirement:`")
            for area in openspec_unmarked_areas(entries)]
    base = openspec_change_only(named, list(paths), cases)
    if not base:
        return rows
    areas = [path for path in paths if openspec_unnamed_baseline(path, named) and path not in base and include_mode(path, excludes) != "full"]
    proposals = [path for name in sorted(openspec_selected_changes(named)) if (path := f"openspec/changes/{name}/proposal.md") in paths]
    try:
        _applied, lines = openspec_applied(entries + read(base, base=True) + read([*areas, *proposals], context=True))
    except RequirementConflict:  # the context row stops on it, as scan does
        return rows
    disputed = [line for line in lines if line.startswith(DISPUTED)]
    for head, row_id, level in ((SCOPE, "openspec:scope", "OK"), (NEIGHBOURS, "openspec:neighbours", "WARN"), (PARTIAL, "openspec:partial", "WARN")):
        rows += [_row(row_id, CASES, level, line, details={"disputed": disputed} if head == PARTIAL else None) for line in lines if line.startswith(head)]
    return rows


def check_size(project: Path, docs: Sequence[str], automation: bool) -> list[dict[str, Any]]:
    """The numbers of the plain documents against what one review holds (``build_context.large_document``): a line, never a stop — the plan stops a run that cannot be reviewed."""
    from tools.build_context import large_document, say
    from tools.doc_sections import resolve_spec

    root = Path(project).resolve()
    documents: dict[str, str] = {}
    named: dict[str, list[str]] = {}
    for raw in docs:
        for rel in _docs_rels(root, [raw]):
            try:
                documents[rel] = (root / rel).read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # the docs row says it
            if heading := resolve_spec(root, raw)[1]:
                named.setdefault(rel, []).append(heading)
    try:
        found = large_document(root, documents, named, automation)
    except (OSError, ValueError):  # a foreign .skillsrc (SkillsrcError is a ValueError): the skillsrc row says what is wrong
        return []
    return [_row("docs:size", CASES, "WARN", say(found[1], found[2]))] if found else []


def check_scan(project: Path, module: Mapping[str, Any], docs: Sequence[str] = ()) -> tuple[dict[str, Any], Mapping[str, Any]]:
    from tools.project_inventory import build_inventory, outside_task_documents
    from tools.scan_rules import exclusion_summary

    root = Path(project).resolve()
    module_root = (root / str(module.get("root") or ".")).resolve()
    try:
        pack = PACK if PACK.resolve().is_relative_to(root) else None
    except ValueError:
        pack = None
    started = time.monotonic()
    # a task document outside the module (the root of a multi-module project) joins the inventory, as in scan
    outside, _problems = outside_task_documents(root, module_root, _docs_rels(root, docs), skill_pack_root=pack)
    inventory = build_inventory(root, module_root, skill_pack_root=pack, generated_roots=(root / ".pilot-runs",),
                                proved_dependency_files=outside)
    seconds = round(time.monotonic() - started, 2)
    summary = exclusion_summary(inventory["exclusions"], module_root.relative_to(root).as_posix() or ".")
    details = {"files": len(inventory["files"]), "bytes": sum(int(item["size"]) for item in inventory["files"]), "seconds": seconds,
               "exclusion_summary": summary}
    message = f"скан: {details['files']} файлов, {details['bytes']} Б, {seconds} с; исключено {summary['total']}"
    if summary["suspicious"]:
        return _row("scan", CASES, "WARN", message + "; есть подозрительные исключения", summary["hint"], details), inventory
    return _row("scan", CASES, "OK", message, None, details), inventory


def check_context(project: Path, module: Mapping[str, Any], docs: Sequence[str], inventory: Mapping[str, Any],
                  target: str | None = None, cases: str | None = None) -> dict[str, Any]:
    """What will not go to the model and why — the same filter as scan; a stop the scan would meet is a STOP here."""
    from tools.cases_scope import ScopeSourceError
    from tools.project_inventory import ContextStop, skipped_summary
    from tools.run_scan import HostStop, context_preview, scope_fix

    root = Path(project).resolve()
    module_root = (root / str(module.get("root") or ".")).resolve()
    try:
        _kept, skipped, budget, templates = context_preview(inventory, root, module_root, list(docs) or None, target, cases)
    except ContextStop as stop:
        row_id = {"CONTEXT_FILE_TOO_LARGE": "context:too-large"}.get(stop.code, "context:" + stop.code.removeprefix("CONTEXT_").casefold().replace("_", "-"))
        return _row(row_id, CASES, "STOP", f"{stop.code}: {stop.detail}", stop.fix)
    except ScopeSourceError as error:  # scan stops on the same delta file
        return _row("context:scope", CASES, "STOP", f"SNAPSHOT_DOCS: {error}", scope_fix(error))
    except HostStop as stop:
        if stop.code == "SOURCE_TARGET_INVALID":  # the scan would stop on the same --target
            return _row("context:target", CASES, "STOP", f"{stop.code}: --target {target}: {stop}",
                        "укажите --target путём внутри модуля (от его корня), который существует")
        if stop.code == "OPENSPEC_SOURCE_CONFLICT":  # scan stops on it: the code and the exact fix, and no READY
            from tools.fixes import human_for

            human = human_for(stop.code, str(stop))  # the phrase of the cause first, the old text after it
            return _row("context:openspec", CASES, "STOP", f"{human} ({stop.code}: {stop})" if human else f"{stop.code}: {stop}", stop.fix)
        # a task document problem is the task-documents check's row
        return _row("context:selection", CASES, "WARN", f"контекст не посчитан: {stop.code}", stop.fix)
    summary = skipped_summary(skipped)
    reasons = ", ".join(f"{code} {value['files']} ({value['bytes']} Б)" for code, value in summary["by_reason"].items())
    message = (f"модели не пойдут {summary['files']} файлов, {summary['bytes']} Б: {reasons}" if summary["files"]
               else "в контекст модели идёт всё отобранное")
    if templates["paths"]:
        shown = ", ".join(templates["paths"][:5]) + (f" и ещё {len(templates['paths']) - 5}" if len(templates["paths"]) > 5 else "")
        message += f"; из кода --target подтянуты шаблоны: {len(templates['paths'])} файлов, {templates['bytes']} Б ({shown}) — код называет их по имени"
    if (lost := templates["unfound"]) is not None:
        return _row("context:selection", CASES, "WARN", f"{message}; код называет шаблоны ({', '.join(lost['suffixes'])}), но файл не найден по имени: таких файлов вне --target: {lost['files']} ({lost['bytes']} Б)",
                    "расширьте --target до общего родителя (например --target src/main): так шаблоны войдут целиком", summary)
    return _row("context:selection", CASES, "OK", message + f"; пачка {budget} Б", None, summary)


def check_task_documents(rows: list[dict[str, Any]], project: Path, docs: Sequence[str], inventory: Mapping[str, Any]) -> None:
    """A document of --docs outside the module's inventory never reaches the model — the same stop as scan."""
    from tools.project_inventory import outside_task_documents
    from tools.run_scan import exclusion_note as _exclusion_note, task_doc_fix

    root = Path(project).resolve()
    paths = {str(item["project_path"]) for item in inventory["files"]}
    module_root = (root / str(inventory.get("module") or ".")).resolve()
    _files, problems = outside_task_documents(root, module_root, _docs_rels(root, docs))
    for raw in docs:
        row = next((item for item in rows if item["id"] == f"docs:{raw}"), None) or _row(f"docs:{raw}", CASES, "OK", "", None)  # a file of a folder has no row of its own: one only when it stops
        if row["level"] != "OK":
            continue
        from tools.doc_sections import resolve_spec

        candidate = Path(resolve_spec(root, raw)[0])
        relative = (candidate if candidate.is_absolute() else root / candidate).resolve().relative_to(root).as_posix()
        if relative in problems:  # outside the module, left out by a rule of the inventory
            row.update(level="STOP", fix=task_doc_fix(relative), message=f"{raw}: документ задачи не может войти в inventory: {problems[relative]} — модель его не получит")
        elif relative not in paths:
            row.update(level="STOP", fix=task_doc_fix(relative),
                       message=f"{raw}: документ задачи не входит в inventory выбранного модуля{_exclusion_note(inventory, relative)} — модель его не получит")
        if row["level"] == "STOP" and row not in rows:
            rows.append(row)


def execution_facts(project: Path, module: Mapping[str, Any]) -> dict[str, Any]:
    """Whether the module can be built and tested by the package: ``{"ready", "reason", "fix"}`` (only reads)."""
    from tools.run_scan import printed_or

    root = Path(project).resolve()
    module_root = root / str(module.get("root") or ".")
    test = module.get("test") if isinstance(module.get("test"), Mapping) else None
    language = (module.get("stack") or {}).get("language") if isinstance(module.get("stack"), Mapping) else None
    problems: list[tuple[str, str]] = []
    if test is None:
        from tools.init_skillsrc import build_tool_missing

        # the reason init gave (no build tool), else the general one
        problems.append(build_tool_missing(module, root) or ("у модуля нет тестового адаптера в .skillsrc", _init_command(project)))
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
                                 f"создайте окружение модуля: python -m venv {printed_or(module_root / '.venv', '<папка модуля>/.venv')} и установите его тестовые зависимости"))
    if language == "java" and not (os.environ.get("JAVA_HOME") or shutil.which("java")):
        problems.append(("JDK не найден (нет JAVA_HOME и java в PATH)", "установите JDK и задайте JAVA_HOME"))
    if not problems:
        return {"ready": True, "reason": None, "fix": None}
    return {"ready": False, "reason": "; ".join(reason for reason, _fix in problems), "fix": "; ".join(fix for _reason, fix in problems)}


_EXCLUSIONS = {
    "nohttp": ("в maven-checkstyle-plugin, в <configuration> выполнения nohttp, допишите через запятую в <excludes>: "
               "**/{pack}/**,**/.pilot-runs/**  (например <excludes>…,**/{pack}/**,**/.pilot-runs/**</excludes>); "
               "для Gradle: nohttp {{ source.exclude(\"{pack}/**\", \".pilot-runs/**\") }}"),
    "apache-rat": "в apache-rat-plugin, в <configuration>: <excludes><exclude>{pack}/**</exclude><exclude>.pilot-runs/**</exclude></excludes>"
                  " (с RAT 0.17 параметр называется <inputExcludes>)",
    "license-mycila": "в com.mycila license-maven-plugin, в <licenseSet> (до 4.x — в <configuration>): "
                      "<excludes><exclude>{pack}/**</exclude><exclude>.pilot-runs/**</exclude></excludes>",
}


def _pack_folder(project: Path) -> str:
    try:
        return PACK.resolve().relative_to(Path(project).resolve()).parts[0]
    except (ValueError, IndexError):
        return ".tools"


def style_rows(project: Path, module: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The project's style checks (read from its build files) and what its own build needs to skip the package."""
    from tools import execution_gate

    GRADLE, MAVEN, SYSTEM_MAVEN, project_style_checks, style_notes, whole_project_checks = execution_gate.from_mode_b(
        "execution_adapters", "GRADLE", "MAVEN", "SYSTEM_MAVEN", "project_style_checks", "style_notes", "whole_project_checks",
    )

    root = Path(project).resolve()
    module_root = (root / str(module.get("root") or ".")).resolve()
    checks = project_style_checks(root, module_root)
    test = module.get("test") if isinstance(module.get("test"), Mapping) else {}
    adapter = str(test.get("adapter_id"))
    if not checks or adapter not in {MAVEN, SYSTEM_MAVEN, GRADLE}:
        return []
    executable = str(test.get("executable") or "mvn") if adapter == SYSTEM_MAVEN else str(module_root / str(test.get("wrapper") or ""))
    hint = style_notes(adapter, executable, checks, ("<сгенерированные файлы>",)).get("format_hint")
    skipped = ("при запуске выбранных тестов пакет их пропускает флагами пропуска" if adapter != GRADLE
               else "задача test Gradle от них не зависит")
    rows = [_row("execution:style", EXECUTION, "WARN", f"проект проверяет стиль: {', '.join(checks)}; {skipped}", hint)]
    whole = whole_project_checks(checks)
    if whole:
        pack = _pack_folder(root)
        rows.append(_row(
            "execution:project-checks", EXECUTION, "WARN",
            # RAT fell on a task document in commons-lang — the checks see every file outside the project's git.
            f"проверки проекта ({', '.join(whole)}) видят файлы, которых нет в git проекта: {pack}/ (окружение пакета, "
            "в нём есть ссылки http), .pilot-runs/, новые документы требований; ваша собственная сборка проекта "
            "на них упадёт, запуск тестов пакета эти проверки пропускает",
            "; ".join(_EXCLUSIONS[name].format(pack=pack) for name in whole)))
    return rows


def java_level_rows(project: Path, module: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The Java level the module's build gives its tests — the generated tests use only that API."""
    if ((module.get("stack") or {}) if isinstance(module.get("stack"), Mapping) else {}).get("language") != "java":
        return []
    from tools.discover_project import java_api_level

    level = java_api_level(Path(project).resolve() / str(module.get("root") or "."), Path(project).resolve())
    if level is None:
        return [_row("execution:java-level", EXECUTION, "OK", "уровень Java модуля в его сборке не задан (или задан во внешнем "
                     "родительском pom) — пакет не ограничивает API автотестов; сборка проверит сама")]
    return [_row("execution:java-level", EXECUTION, "OK", f"уровень Java модуля: {level['level']} ({'; '.join(level['evidence'])}); "
                 f"автотесты — только API Java {level['level']}, новее отклонит submit генератора (RUNNER_API_LEVEL)")]


def _cases_row(project: Path, cases: str) -> tuple[dict[str, Any], str | None]:
    """``--cases`` of the request: the check scan makes of the CSV (only reads, the same code); the row and the path in the project it returned (None on a stop)."""
    from tools.cases_import import CasesImportError
    from tools.run_pipeline import HostStop, check_cases

    try:
        rel, count = check_cases(project, cases)
    except (HostStop, CasesImportError) as refusal:
        from tools.fixes import human_for

        said = str(refusal) if isinstance(refusal, CasesImportError) else f"{refusal.code}: {refusal}"  # the message of a CasesImportError carries its code
        human = human_for(refusal.code, said)
        return _row("cases", CASES, "STOP", f"{human} ({said})" if human else said, refusal.fix), None
    return _row("cases", CASES, "OK", f"{rel}: кейсов {count} — scan --cases возьмёт их"), rel


def _self_review_scan(project: Path, docs: Sequence[str], profile: str, module: str | None, target: str | None = None, cases: str | None = None) -> str:
    """The full scan command of this very request plus the flag — an agent runs a fix verbatim (package python); every value
    through ``printed``."""
    from tools.run_pipeline import printed

    parts = [f"{printed(sys.executable)} -m tools.run_pipeline scan --project {printed(project)}", *(f"--docs {printed(doc)}" for doc in docs),
             f"--profile {profile}", *([f"--module {printed(module)}"] if module else []), *([f"--target {printed(target)}"] if target else []),
             *([f"--cases {printed(cases)}"] if cases else []), "--accept-self-review"]
    return " ".join(parts)


def check(project: Path, docs: Sequence[str], profile: str | None = None, module: str | None = None,
          target: str | None = None, cases: str | None = None) -> dict[str, Any]:
    from tools import execution_gate

    project = Path(project).resolve()
    requested_profile = profile or execution_gate.default_profile()  # what this package can do
    profile, refused = execution_gate.resolve_profile(profile or None)  # "" is no profile, as before; a refused profile runs as cases-only: no execution rows
    rows: list[dict[str, Any]] = [check_python()]
    from tools.run_pipeline import project_not_found

    if missing := project_not_found(project):  # one row, not the stops of docs, .skillsrc and module the absent folder would give
        from tools.fixes import human_for

        rows.append(_row("project", CASES, "STOP", f"{human_for(missing.code)} ({missing.code}: {missing})", missing.fix))
        return {"status": "BLOCKED", "project": str(project), "profile": requested_profile, "checks": rows}
    paths: list[str] = []  # the inventory of the module and its paths.exclude, for the scope of a change only
    excludes: Sequence[str] = ()
    if refused:
        rows.append(_row("profile", CASES, "STOP", refused, "запустите ready без --profile или с --profile cases-only-v1"))
    deps = check_deps()
    rows.append(deps)
    rows.append(_git_row(project, profile))
    rows.extend(check_run_root(project))
    rows.extend(check_docs(project, docs))
    cases_row, cases_rel = _cases_row(project, cases) if cases else (None, None)  # the path scan takes; no context of the cases where the CSV stops
    rows.extend([cases_row] if cases_row else [])
    from tools.run_pipeline import HostStop, expand_docs

    try:
        _self_review_scan(project, docs, profile, module, target, cases)  # the values every printed command of the run carries (the folders as typed)
        typed, docs = docs, expand_docs(project, list(docs))  # the files of a folder, as scan takes them
    except HostStop as stop:  # scan refuses the same request: said here, before anything else
        rows.append(_row("command", CASES, "STOP", f"{stop.code}: {stop}", stop.fix))
        return {"status": "BLOCKED", "project": str(project), "profile": requested_profile, "checks": rows}
    if deps["level"] != "OK":
        skipped = "пропущено до установки зависимостей пакета"
        rows.extend(_row(name, CASES, "WARN", skipped, deps.get("fix")) for name in ("pack", "skillsrc", "module", "scan"))
    else:
        rows.append(check_pack())
        for name, run in (("skillsrc", lambda: check_skillsrc(project, module)),):
            try:
                rows.extend(run())
            except Exception as error:
                rows.append(_row(name, CASES, "STOP", f"{type(error).__name__}: {error}", _init_command(project)))
        try:
            module_row, selected = check_module(project, module)
        except Exception as error:
            said = "модуль назвать нельзя, пока нет ответа на вопрос строки skillsrc" if type(error).__name__ == "NeedsInput" else f".skillsrc не читается: {type(error).__name__}: {error}"
            module_row, selected = _row("module", CASES, "STOP", said, _init_command(project)), None
        rows.append(module_row)
        if selected is not None and (shared_row := check_shared_data(selected)) is not None:
            rows.append(shared_row)
        if selected is not None:
            if execution_gate.execution_available():  # the level is a fact of mode B; the cases-only delivery has no autotests to run
                rows.append(check_test_level(selected))
            try:
                scan_row, inventory = check_scan(project, selected, docs)
                rows.append(scan_row)
                paths = [str(item["project_path"]) for item in inventory.get("files", [])]
                from tools.project_inventory import skillsrc_exclude

                excludes = skillsrc_exclude(project, (project / str(selected.get("root") or ".")).resolve())
                check_task_documents(rows, project, docs, inventory)
                rows.append(check_context(project, selected, docs, inventory, target, cases_rel))
            except Exception as error:
                rows.append(_row("scan", CASES, "STOP", f"скан не прошёл: {type(error).__name__}: {error}", "исправьте причину и повторите ready"))
            if profile == "local-pilot-v1" and ((selected.get("stack") or {}) if isinstance(selected.get("stack"), Mapping) else {}).get("language") == "documents":
                # the same refusal as scan — nothing to build, local-pilot is not lowered here
                rows.append(_row("execution:build", CASES, "STOP", "в проекте только документы, сборки и тестов нет: local-pilot-v1 недоступен",
                                 "ready и scan с --profile cases-only-v1 — кейсы по документам без автотестов"))
            elif profile == "local-pilot-v1":
                facts = execution_facts(project, selected)
                rows.append(_row("execution:build", EXECUTION, "OK" if facts["ready"] else "WARN",
                                 "сборка и тесты модуля готовы" if facts["ready"] else
                                 f"сборка не готова: {facts['reason']}; без неё прогон пойдёт как cases-only-v1", facts["fix"]))
                rows.extend(style_rows(project, selected))
                rows.extend(java_level_rows(project, selected))
                # the baseline refusals the scan would meet, the same code; cases are not blocked by them.
                from tools.run_scan import scan_baseline_blockers as _baseline_blockers

                for blocker in _baseline_blockers(project, selected):
                    rows.append(_row(f"execution:baseline:{blocker['file']}", EXECUTION, "WARN",
                                     f"{blocker['file']}: {blocker['reason']}; прогон пойдёт как cases-only-v1", blocker["fix"]))
    if profile == "local-pilot-v1":
        freeze = ("после scan не меняйте файлы проекта и .skillsrc до finish или ingest: иначе materialize и ingest "
                  "остановятся на дрейфе и предложат вернуть файлы или начать новый scan")
    else:  # PT2: the cases-only profile has no execution
        freeze = ("после scan не меняйте файлы проекта и .skillsrc до finish: иначе status покажет дрейф (drift) "
                  "и предложит вернуть файлы или начать новый scan")
    rows.append(_row("run:freeze", CASES, "OK", freeze))
    if not any(row["id"] == "context:openspec" and row["level"] == "STOP" for row in rows):  # beside the stop of a MODIFIED the warning only confuses
        rows.extend(check_openspec_base(project, typed, paths, bool(cases), excludes))
    rows.extend(check_size(project, typed, profile == "local-pilot-v1" and any(row["id"] == "execution:build" and row["level"] == "OK" for row in rows)))
    # a hint, never a STOP — whether the CLI opens a fresh reviewer is known only to the agent.
    from tools.skillsrc_manifest import skillsrc_review_self

    if skillsrc_review_self(project):  # the project has answered once, in .skillsrc
        rows.append(_row("review:isolation", CASES, "OK", "в .skillsrc review.self: true — ревью в той же сессии принимается с пометкой SELF, "
                         "вопрос не задаётся (свежий контекст всё равно лучше)"))
    else:
        # PT: the row is OK — there is nothing to fix; the scan with the SELF flag is data for the one case the human says yes to
        rows.append(_row("review:isolation", CASES, "OK", "ревьюер кейсов нужен в свежем контексте (отдельный субагент или вызов без истории генерации); если программа с моделью так не умеет — "
                         "спросите человека о прогоне с пометкой SELF; команду scan с флагом SELF (details.self_review_scan) — только после его «да»",
                         None, {"self_review_scan": _self_review_scan(project, typed, profile, module, target, cases)}))
    blocked = any(row["level"] == "STOP" and row["block"] == CASES for row in rows)
    return {"status": "BLOCKED" if blocked else "READY", "project": str(project), "profile": requested_profile, "checks": rows}


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
    parser = JsonArgumentParser(description="Readiness of a project for a run: every problem at once, with fixes.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--docs", action="append", default=[])
    parser.add_argument("--profile", choices=("cases-only-v1", "local-pilot-v1"), default=None,
                        help="P8a-1: default is what this package can do — local-pilot-v1 with its execution files, else cases-only-v1")
    parser.add_argument("--module")
    parser.add_argument("--target", help="the narrowed context of scan --target, to preview before the scan")
    parser.add_argument("--cases", help="the Zephyr CSV of scan --cases: checked as scan checks it")
    args = parser.parse_args(argv)
    report = check(Path(args.project), args.docs, args.profile, args.module, args.target, args.cases)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
    print(_table(report), file=sys.stderr)
    return 0 if report["status"] == "READY" else 2


# Public names for execution_core: the same objects under names without an underscore; a rename of the private name is then
# a rename here, in one place.


if __name__ == "__main__":
    raise SystemExit(main())
