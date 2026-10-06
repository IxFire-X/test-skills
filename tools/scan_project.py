#!/usr/bin/env python3
"""
scan_project.py — детерминированный сканер стека проекта (Опора 2, ROADMAP Шаг 4).

Единственный источник правды о том, КАКОЙ стек у проекта и КАКОЙ код релевантен
для генерации автотестов. В отличие от LLM-угадывания стека (которое может
галлюцинировать «spring-boot» для python-проекта), этот скрипт определяет стек
по ФАКТИЧЕСКИМ манифестам сборки и читает только релевантные файлы.

Принципы (наследуются от run_tests.py):
  - Стек определяется по манифестам, не по расширениям файлов и не по LLM.
  - НЕ читается весь проект. Только: target-файл + models/serializers/urls/
    conftest того же модуля.
  - Зависимости: только стандартная библиотека Python. Ничего ставить не нужно.
  - Честность > удобства. Если манифестов нет — status:error, а не молчаливый
    успех с пустым стеком. Именно это не даёт остальным инструментам строить
    тесты на фантазиях.
  - Вывод: JSON по контракту schemas/scan-project-output.schema.json в stdout.

Что делает (по порядку):
  1. Парсер аргументов (--project, --target, --output).
  2. Определение стека по манифестам сборки.
  3. Извлечение релевантного кода (target + сопутствующие модули).
  4. Генерация <source_code_and_diff> (ручки и исходники).
  5. Техническая пометка стека. Истории/требования НЕ извлекаются из эндпоинтов.
  6. .skillsrc не создаётся и не обновляется (read-only).
  7. JSON-отчёт.

Использование:
    python tools/scan_project.py --project /path/to/app \
        --target src/mod/api.py
    python tools/scan_project.py --project /path/to/java-project \
        --target src/main/java/com/example/Foo.java \
        --output docs/to_do/scan.md

Exit codes (для встраивания в CI):
    0 — success ИЛИ partial (отчёт честный)
    1 — error (манифестов нет / target не существует)
"""

from __future__ import annotations

import argparse
import json
import ntpath
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

if __package__:
    from .confined_output import atomic_write_confined_bytes
    from .json_cli import JsonArgumentParser
    from .stack_catalog import confined_files
else:  # direct CLI execution
    from confined_output import atomic_write_confined_bytes
    from json_cli import JsonArgumentParser
    from stack_catalog import confined_files

# ---------------------------------------------------------------------------
# Конфигурация распознавания манифестов
# ---------------------------------------------------------------------------

# Имена файлов-манифестов и marker tables импортируются из stack_catalog.
# Порядок важен: для одного проекта может быть несколько манифестов; выбираем
# «ближайший» к target либо первый найденный.

# Маркеры фреймворка приложения в манифестах зависимостей.
# (подстрока в нижнем регистре → framework). Порядок = приоритет.

# Маркеры тестового фреймворка (в dev-зависимостях).

# Допустимые значения build_tool по skillsrc.schema.json (для записи в .skillsrc).
# uv/conda и пр. маппятся в ближайшее валидное.

# ---------------------------------------------------------------------------
# 1. Парсер аргументов
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description=("Read-only stack scanner. Detects the stack from manifests, "
                     "extracts nearby source/handles, never writes .skillsrc, "
                     "never invents business analytics from endpoints."),
    )
    parser.add_argument("--project", required=True,
                        help="Путь к корню целевого проекта (с манифестами сборки)")
    parser.add_argument("--target", required=True,
                        help="Целевой модуль/файл (например, part/api.py)")
    parser.add_argument("--output",
                        help="Куда записать результат (по умолчанию — stdout)")
    return parser


# ---------------------------------------------------------------------------
# 2. Определение стека по манифестам
# ---------------------------------------------------------------------------

def _read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _confined_path(project_dir: str, candidate: str) -> str | None:
    """Return a real path below project_dir, or None without reading an escape."""
    root = os.path.realpath(project_dir)
    resolved = os.path.realpath(candidate)
    try:
        return resolved if os.path.commonpath([root, resolved]) == root else None
    except ValueError:
        return None


def _valid_target_reference(target: str) -> bool:
    """Reject portable absolute, drive-relative, UNC, and traversal target input."""
    normalized = target.replace("\\", "/")
    drive, _tail = ntpath.splitdrive(target)
    return not (
        not target or os.path.isabs(target) or normalized.startswith("/")
        or target.startswith(("\\\\", "//")) or bool(drive)
        or ".." in [part for part in normalized.split("/") if part]
    )


def _module_covering_target(modules: list[dict], target_rel: str) -> dict | None:
    target = (target_rel or "").replace("\\", "/").strip("/")
    scored: list[tuple[int, dict]] = []
    for module in modules:
        root = (module.get("root") or ".").replace("\\", "/").strip("/") or "."
        if root == ".":
            scored.append((0, module))
            continue
        if target == root or target.startswith(root + "/"):
            scored.append((len(root), module))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best = scored[0][0]
    winners = [module for score, module in scored if score == best]
    return winners[0] if len(winners) == 1 else None


def detect_stack(project_dir: str, target_rel: str) -> dict:
    """Resolve stack from discover_project, the single detector."""
    if __package__:
        from .discover_project import discover_project
    else:
        from discover_project import discover_project
    errors: list[str] = []
    warnings: list[str] = []
    report = discover_project(Path(project_dir))
    if report.get("status") == "error":
        return {
            "status": "error",
            "stack": {"language": "unknown", "detection": {"manifest": None, "evidence": report.get("errors") or []}},
            "errors": list(report.get("errors") or ["discovery failed"]),
            "warnings": warnings,
        }
    modules = list(report.get("modules") or [])
    resolved_abs, resolve_warnings = resolve_target(project_dir, target_rel)
    warnings.extend(resolve_warnings)
    lookup = target_rel.replace("\\", "/")
    if resolved_abs:
        lookup = os.path.relpath(resolved_abs, project_dir).replace("\\", "/")
    module = _module_covering_target(modules, lookup)
    if module is None and len(modules) == 1:
        module = modules[0]
    if module is None:
        warnings.append("target did not match exactly one discovered module")
        return {
            "status": "error",
            "stack": {"language": "unknown", "detection": {"manifest": None, "evidence": []}},
            "errors": errors + ["ambiguous or missing module for target"],
            "warnings": warnings,
        }
    stack_info = dict(module.get("stack") or {})
    language = stack_info.get("language") or "unknown"
    framework = stack_info.get("framework")
    test_framework = (module.get("test") or {}).get("framework")
    build_tool = stack_info.get("build_tool")
    detected = (module.get("detected_from") or [None])[0]
    evidence = list(module.get("detected_from") or [])
    if report.get("status") == "needs_input":
        warnings.append("discovery needs_input; not guessing an unproven framework")
    stack = {
        "language": language,
        "framework": framework,
        "test_framework": test_framework,
        "build_tool": build_tool,
        "detection": {"manifest": detected, "evidence": evidence},
    }
    status = "success" if language != "unknown" and framework and test_framework else "partial" if language != "unknown" else "error"
    if language == "unknown":
        errors.append("module language is unresolved")
        status = "error"
    return {"status": status, "stack": stack, "errors": errors, "warnings": warnings}


# ---------------------------------------------------------------------------
# 3. Извлечение релевантного кода
# ---------------------------------------------------------------------------

# Сопутствующие модули, которые тащим рядом с target (по языку).
# Для python — фиксированный набор имён в пакете.
# Для java — файлы того же пакета с тем же «корнем» имени и ролевым суффиксом
# (Service/Controller/Repository/Entity/DTO/...), см. _java_companions.
COMPANION_FILES = {
    "python": ["models.py", "serializers.py", "urls.py", "conftest.py"],
    "java": ["_dynamic_"],  # маркер: вычисляется отдельно по пакету
    "typescript": [],
    "go": [],
}


def _java_companions(project_dir: str, target_abs: str) -> list[str]:
    """
    Находит java-компаньонов target: файлы того же пакета (директории) с тем же
    «корнем» имени и ролевым суффиксом. TransferService.java → TransferController.java,
    TransferRepository.java, TransferRequest.java, TransferResponse.java и т.п.
    Тест-классы (*Test.java, *Tests.java) в контекст не тащим.
    """
    companions: list[str] = []
    target_dir = os.path.dirname(target_abs)
    base = os.path.basename(target_abs)
    stem = re.sub(
        r"(Service|Controller|Repository|Resource|Entity|Dto|DTO|Request|Response|"
        r"Mapper|Facade|Manager|Helper|Util|Config|Properties|Command|Query)$",
        "", os.path.splitext(base)[0],
    )
    role_re = re.compile(
        r"(Service|Controller|Repository|Resource|Entity|Dto|DTO|Request|Response|"
        r"Mapper|Facade|Manager|Helper|Config|Properties|Command|Query)$",
    )
    try:
        names = sorted(os.listdir(target_dir))
    except OSError:
        return companions
    for fn in names:
        if not fn.endswith(".java") or fn == base:
            continue
        fstem = os.path.splitext(fn)[0]
        if fstem.endswith(("Test", "Tests")) or fn.endswith("Test.java"):
            continue
        # «родственник»: общий корень имени + ролевой суффикс
        if stem and fstem.startswith(stem) and role_re.search(fstem):
            candidate = _confined_path(project_dir, os.path.join(target_dir, fn))
            if candidate and os.path.isfile(candidate):
                rel = os.path.relpath(candidate, project_dir)
                companions.append(rel.replace("\\", "/"))
    return companions


def _secret_filter(project_dir: str, absolute_path: str) -> bool:
    """True when the inventory secret filter rejects a file; failing closed."""
    try:
        if __package__:
            from .project_inventory import file_secret_rule
        else:  # direct CLI execution: the package root is the parent of tools/
            pack_root = str(Path(__file__).resolve().parents[1])
            if pack_root not in sys.path:
                sys.path.insert(0, pack_root)
            from tools.project_inventory import file_secret_rule
    except ImportError:
        return True
    return file_secret_rule(Path(project_dir), Path(absolute_path)) is not None


def _java_source_index(project_dir: str) -> dict[str, list[str]]:
    """Index project Java files by simple file name with one confined walk."""
    root = Path(project_dir)
    index: dict[str, list[str]] = {}
    for path in confined_files(root):
        if path.suffix == ".java":
            index.setdefault(path.name, []).append(path.relative_to(root).as_posix())
    return index


def _java_imported_sources(project_dir: str, target_abs: str) -> list[str]:
    """Resolve direct project-local Java imports without leaving project_dir."""
    source = _read_text(target_abs) or ""
    mask = _strip_java_comments_and_text_blocks(source)
    imported_types = sorted(set(re.findall(r"^\s*import\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*;", mask, re.MULTILINE)))
    resolved: list[str] = []
    if not imported_types:
        return resolved
    # One walk for all imports instead of one repository walk per import.
    index = _java_source_index(project_dir)
    for imported in imported_types:
        suffix = imported.replace(".", "/") + ".java"
        for rel in sorted(rel for rel in index.get(suffix.rsplit("/", 1)[-1], []) if rel == suffix or rel.endswith("/" + suffix)):
            candidate = _confined_path(project_dir, os.path.join(project_dir, rel))
            if not candidate or _secret_filter(project_dir, candidate):
                continue
            text = _read_text(candidate) or ""
            package = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
            if package and package.group(1) + "." + os.path.splitext(os.path.basename(rel))[0] == imported:
                resolved.append(rel.replace("\\", "/"))
                break
    return resolved


def _find_files_by_suffix(project_dir: str, suffix: str) -> list[str]:
    """Ищет файлы, чей относительный путь (forward-slash) заканчивается на suffix."""
    suffix = suffix.replace("\\", "/").lstrip("./")
    found: list[str] = []
    root = Path(project_dir)
    for path in confined_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.endswith(suffix):
            found.append(rel)
    return found


def resolve_target(project_dir: str, target_rel: str) -> tuple[str | None, list[str]]:
    """
    Разрешает target в реальный путь и возвращает (abs_path, warnings).
    Ищет по прямому пути, затем по единственному путевому суффиксу.
    """
    warnings: list[str] = []
    if not _valid_target_reference(target_rel):
        return None, [f"invalid target outside project root: {target_rel}"]
    target_rel = target_rel.replace("\\", "/")

    # 1. Прямой путь
    direct = os.path.join(project_dir, target_rel)
    direct_confined = _confined_path(project_dir, direct)
    if direct_confined and os.path.isfile(direct_confined):
        return direct_confined, warnings
    if os.path.lexists(direct) and not direct_confined:
        return None, [f"invalid target outside project root: {target_rel}"]

    # 2. Поиск по суффиксу пути (точнее, чем по имени файла — учитывает модуль)
    suffix_matches = _find_files_by_suffix(project_dir, target_rel)
    if len(suffix_matches) == 1:
        match = suffix_matches[0]
        warnings.append(f"target задан как '{target_rel}', найден по пути: {match}")
        candidate = _confined_path(project_dir, os.path.join(project_dir, match))
        if candidate and os.path.isfile(candidate):
            return candidate, warnings
    if len(suffix_matches) > 1:
        warnings.append(f"ambiguous target suffix '{target_rel}': {', '.join(sorted(suffix_matches))}")
        return None, warnings

    warnings.append(f"target '{target_rel}' не найден в проекте.")
    return None, warnings


def extract_companions(project_dir: str, target_abs: str, language: str) -> list[str]:
    """
    Находит сопутствующие модули того же пакета/директории.
    Для python: models.py / serializers.py / urls.py / conftest.py рядом с target.
    """
    companions: list[str] = []
    if language == "java":
        companions = [
            path for path in _java_companions(project_dir, target_abs)
            if not _secret_filter(project_dir, os.path.join(project_dir, path))
        ]
        imports = _java_imported_sources(project_dir, target_abs)
        return companions + [path for path in imports if path not in companions]
    if language not in COMPANION_FILES or not COMPANION_FILES[language]:
        return companions

    target_dir = os.path.dirname(target_abs)
    # Также поднимаемся на уровень пакета (target может лежать в подpkg)
    candidate_dirs = [target_dir]
    parent = os.path.dirname(target_dir)
    if parent != target_dir:
        candidate_dirs.append(parent)

    seen = set()
    for d in candidate_dirs:
        for fn in COMPANION_FILES[language]:
            cand = _confined_path(project_dir, os.path.join(d, fn))
            if cand and os.path.isfile(cand):
                rel = os.path.relpath(cand, project_dir).replace("\\", "/")
                if rel not in seen:
                    seen.add(rel)
                    companions.append(rel)
    return companions


# ---------------------------------------------------------------------------
# 4 & 5. Генерация XML-блоков
# ---------------------------------------------------------------------------

def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))


def render_source_block(project_dir: str, files_rel: list[str]) -> str:
    """Emit a well-formed envelope while XML parsing round-trips source exactly."""
    lines = ["<source_code_and_diff>"]
    for rel in files_rel:
        abs_p = _confined_path(project_dir, os.path.join(project_dir, rel))
        if not abs_p:
            continue
        content = _read_text(abs_p) or ""
        cdata = content.replace("]]>", "]]" + "]]><![CDATA[>")
        lines.append(f'  <file path="{_xml_escape(rel)}"><![CDATA[{cdata}]]></file>')
    lines.append("</source_code_and_diff>")
    return "\n".join(lines)


def _strip_java_comments_and_text_blocks(text: str) -> str:
    """Mask non-code Java lexemes, retaining newlines and code positions."""
    result: list[str] = []
    index = 0
    state = "code"
    while index < len(text):
        if state == "code" and text.startswith("//", index):
            state = "line_comment"
            result.extend("  ")
            index += 2
        elif state == "code" and text.startswith("/*", index):
            state = "block_comment"
            result.extend("  ")
            index += 2
        elif state == "code" and text.startswith('\"\"\"', index):
            state = "text_block"
            result.extend("   ")
            index += 3
        elif state == "code" and text[index] == '"':
            state = "string"
            result.append(" ")
            index += 1
        elif state == "code" and text[index] == "'":
            state = "char"
            result.append(" ")
            index += 1
        elif state == "line_comment":
            char = text[index]
            result.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\n":
                state = "code"
        elif state == "block_comment":
            if text.startswith("*/", index):
                result.extend("  ")
                index += 2
                state = "code"
            else:
                result.append("\n" if text[index] == "\n" else " ")
                index += 1
        elif state == "text_block":
            if text[index] == "\\" and index + 1 < len(text):
                result.append(" ")
                index += 1
                escaped = text[index]
                result.append("\n" if escaped == "\n" else " ")
                index += 1
            elif text.startswith('\"\"\"', index):
                result.extend("   ")
                index += 3
                state = "code"
            else:
                result.append("\n" if text[index] == "\n" else " ")
                index += 1
        elif state in {"string", "char"}:
            char = text[index]
            result.append("\n" if char == "\n" else " ")
            index += 1
            if char == "\\" and index < len(text):
                escaped = text[index]
                result.append("\n" if escaped == "\n" else " ")
                index += 1
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                state = "code"
        else:
            result.append(text[index])
            index += 1
    return "".join(result)


def render_analytics_block(stack: dict, project_dir: str, files_rel: list[str],
                           target_rel: str, warnings: list[str]) -> str:
    """Technical stack note only. Do not invent business stories from endpoints."""
    lines = [
        "<analytics_documentation>",
        "  <note>Stories come from authorized --docs via build_context. "
        "This scanner does not invent requirements from routes or models.</note>",
        "  <stack>",
        f"    <language>{_xml_escape(stack.get('language') or 'unknown')}</language>",
        f"    <framework>{_xml_escape(stack.get('framework') or 'unknown')}</framework>",
        f"    <test_framework>{_xml_escape(stack.get('test_framework') or 'unknown')}</test_framework>",
        f"    <build_tool>{_xml_escape(stack.get('build_tool') or 'unknown')}</build_tool>",
        "  </stack>",
    ]
    if warnings:
        lines.append("  <warnings>")
        for warning in warnings:
            lines.append(f"    <warning>{_xml_escape(warning)}</warning>")
        lines.append("  </warnings>")
    lines.append("</analytics_documentation>")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Сборка отчёта
# ---------------------------------------------------------------------------

def _norm(path: str | None) -> str | None:
    """Нормализует разделители путей в forward-slash для кроссплатформенного вывода."""
    if not path:
        return path
    return path.replace("\\", "/")


def build_report(status: str, stack: dict, files_extracted: list[str],
                 output_file: str | None, skillsrc_updated: bool,
                 skillsrc_path: str | None, warnings: list[str],
                 errors: list[str], artifact: str | None = None) -> dict:
    # нормализуем пути в stack.detection (manifest может прийти с backslash)
    if isinstance(stack.get("detection"), dict) and stack["detection"].get("manifest"):
        stack["detection"]["manifest"] = _norm(stack["detection"]["manifest"])
    report = {
        "status": status,
        "stack": stack,
        "files_extracted": [_norm(p) for p in files_extracted],
        "output_file": _norm(output_file),
        "skillsrc_updated": skillsrc_updated,
        "skillsrc_path": _norm(skillsrc_path),
        "warnings": warnings or None,
        "errors": errors or None,
        "artifact": artifact,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
    return report


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project)
    if not os.path.isdir(project_dir):
        report = build_report(
            status="error",
            stack={"language": "unknown"},
            files_extracted=[],
            output_file=None, skillsrc_updated=False, skillsrc_path=None,
            warnings=[],
            errors=[f"--project не существует: {project_dir}"],
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1

    target_rel = args.target

    # 2. Определение стека
    detection = detect_stack(project_dir, target_rel)
    stack = detection["stack"]
    warnings: list[str] = list(detection.get("warnings") or [])
    errors: list[str] = list(detection.get("errors") or [])

    # 3. Извлечение кода (даже при partial стеке — target всё равно пробуем)
    target_abs, tgt_warnings = resolve_target(project_dir, target_rel)
    warnings.extend(tgt_warnings)
    errors.extend(item for item in tgt_warnings if item.startswith("invalid target"))

    files_extracted: list[str] = []
    if target_abs:
        target_rel_norm = os.path.relpath(target_abs, project_dir).replace("\\", "/")
        files_extracted.append(target_rel_norm)
        companions = extract_companions(project_dir, target_abs, stack.get("language", "unknown"))
        # не дублируем сам target, если он совпал с компаньоном
        for c in companions:
            if c not in files_extracted:
                files_extracted.append(c)

    # статус: error, если стек не определён ИЛИ target не найден
    status = detection["status"]
    if not target_abs:
        errors.append(f"target '{target_rel}' не существует в проекте.")
        status = "error"
    elif detection["status"] == "success" and files_extracted:
        status = "success"
    elif detection["status"] == "partial" and target_abs:
        status = "partial"

    requested_output = args.output

    # 4 & 5. Генерация блоков (только если есть что генерировать)
    output_file = None
    document = None
    source_block = ""
    analytics_block = ""
    if files_extracted:
        source_block = render_source_block(project_dir, files_extracted)
        analytics_block = render_analytics_block(
            stack, project_dir, files_extracted, target_rel, warnings
        )
        document = f"{source_block}\n\n{analytics_block}\n"
        if requested_output:
            try:
                atomic_write_confined_bytes(Path(project_dir), requested_output, document.encode("utf-8"))
            except (OSError, ValueError) as error:
                errors.append(f"Не удалось записать --output: {error}")
                status = "error"
            else:
                output_file = requested_output
        # без --output документ печатается отдельно ниже (после JSON)

    # Scanning is read-only by default. A scanner report must never silently
    # rewrite project metadata; .skillsrc can be explicitly managed elsewhere.
    skillsrc_updated = False
    skillsrc_path = None

    # 7. JSON-отчёт
    report = build_report(
        status=status, stack=stack, files_extracted=files_extracted,
        output_file=output_file, skillsrc_updated=skillsrc_updated,
        skillsrc_path=skillsrc_path, warnings=warnings, errors=errors,
        artifact=document,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0 if status != "error" else 1


if __name__ == "__main__":
    sys.exit(main())
