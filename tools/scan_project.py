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
  4. Генерация <source_code_and_diff>.
  5. Генерация заготовки <analytics_documentation>.
  6. Создание/обновление .skillsrc внутри --project.
  7. JSON-отчёт.

Использование:
    python tools/scan_project.py --project InvenTree-master \
        --target src/backend/InvenTree/part/api.py
    python tools/scan_project.py --project /path/to/java-project \
        --target src/main/java/com/example/Foo.java \
        --output docs/to_do/analytics-foo.md

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
    from .json_cli import JsonArgumentParser
    from .stack_catalog import match_marker, normalize_build_tool
else:  # direct CLI execution
    from json_cli import JsonArgumentParser
    from stack_catalog import match_marker, normalize_build_tool

# ---------------------------------------------------------------------------
# Конфигурация распознавания манифестов
# ---------------------------------------------------------------------------

# Имена файлов-манифестов сборки (ищутся рекурсивно относительно --project).
# Порядок важен: для одного проекта может быть несколько манифестов; выбираем
# «ближайший» к target либо первый найденный.
PYTHON_MANIFESTS = [
    "pyproject.toml",          # современный стандарт (PEP 621)
    "requirements.txt",        # классика pip
    "requirements-dev.txt",    # dev-зависимости (часто тут pytest)
    "setup.py",                # legacy
    "Pipfile",                 # pipenv
]
JAVA_MANIFESTS = ["pom.xml", "build.gradle", "build.gradle.kts"]
JS_MANIFESTS = ["package.json"]
GO_MANIFESTS = ["go.mod"]

# Маркеры фреймворка приложения в манифестах зависимостей.
# (подстрока в нижнем регистре → framework). Порядок = приоритет.
PYTHON_FRAMEWORK_MARKERS = [
    ("django", "django"),                    # Django + DRF (djangorestframework)
    ("rest_framework", "django"),            # DRF alias
    ("djangorestframework", "django"),
    ("fastapi", "fastapi"),
    ("flask", "flask"),
    ("aiohttp", "aiohttp"),
    ("tornado", "tornado"),
]
JAVA_FRAMEWORK_MARKERS = [
    ("spring-boot-starter", "spring-boot"),
    ("org.springframework.boot", "spring-boot"),
    ("quarkus", "quarkus"),
    ("micronaut", "micronaut"),
]
JS_FRAMEWORK_MARKERS = [
    ("\"express\"", "express"),
    ("\"next\"", "nextjs"),
    ("\"nuxt\"", "nuxt"),
    ("\"@nestjs/core\"", "nestjs"),
    ("\"fastify\"", "fastify"),
]
GO_FRAMEWORK_MARKERS = [
    ("github.com/gin-gonic/gin", "gin"),
    ("github.com/labstack/echo", "echo"),
    ("github.com/gofiber/fiber", "fiber"),
    ("github.com/gorilla/mux", "gorilla-mux"),
]

# Маркеры тестового фреймворка (в dev-зависимостях).
PYTHON_TEST_MARKERS = [
    ("pytest", "pytest"),
    ("nose", "nose"),
]
JAVA_TEST_MARKERS = [
    ("junit-jupiter", "junit5"),
    ("junit:junit", "junit4"),
    ("org.testng", "testng"),
]
JS_TEST_MARKERS = [
    ("\"jest\"", "jest"),
    ("\"mocha\"", "mocha"),
    ("\"vitest\"", "vitest"),
]
GO_TEST_MARKERS = []  # стандартный testing + go test — один вариант "go-testing"

# Допустимые значения build_tool по skillsrc.schema.json (для записи в .skillsrc).
# uv/conda и пр. маппятся в ближайшее валидное.
BUILD_TOOL_NORMALIZE = {
    "uv": "pip",
    "conda": "pip",
    "pipenv": "pip",
    "setuptools": "pip",
    "kotlin": "gradle",
}


# ---------------------------------------------------------------------------
# 1. Парсер аргументов
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        description=("Детерминированный сканер стека проекта (Опора 2). "
                     "Определяет стек по манифестам сборки, извлекает релевантный код, "
                     "генерирует <source_code_and_diff> + <analytics_documentation> и "
                     "обновляет .skillsrc. Выводит JSON по контракту "
                     "schemas/scan-project-output.schema.json."),
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

def _find_manifests(project_dir: str, names: list[str]) -> list[str]:
    """Рекурсивно ищет файлы с заданными именами, возвращает относительные пути."""
    found = []
    for root, _dirs, files in os.walk(project_dir):
        # пропускаем тяжёлые/нерелевантные деревья
        parts = os.path.relpath(root, project_dir).split(os.sep)
        if any(p in {".git", "__pycache__", "node_modules", ".venv", "venv",
                     "target", "build", "dist", ".idea", ".tools"}
               for p in parts):
            continue
        for fn in files:
            if fn in names:
                candidate = _confined_path(project_dir, os.path.join(root, fn))
                if candidate and os.path.isfile(candidate):
                    rel = os.path.relpath(candidate, project_dir)
                    found.append(rel)
    return found


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


def _extract_dep_names(text: str) -> set[str]:
    """
    Извлекает множество имён пакетов из requirements-файла/setup.py.
    Формат: 'Django>=4.2' → 'django', 'djangorestframework==3.14' → 'djangorestframework'.
    """
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        # обрезаем git-ссылки, -e, markers
        line = re.split(r"[;@\s]", line, 1)[0]
        m = re.match(r"^([A-Za-z0-9_.-]+)", line)
        if m:
            names.add(m.group(1).lower())
    return names


def _match_markers(names_or_text: str, markers: list[tuple[str, str]]) -> str | None:
    """Возвращает framework по первому совпавшему маркеру (по подстроке)."""
    return match_marker(names_or_text, markers)


def detect_stack(project_dir: str, target_rel: str) -> dict:
    """
    Определяет стек по манифестам сборки.

    Возвращает dict: {status, stack, errors, warnings}.
    stack = {language, framework, test_framework, build_tool, detection}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # --- Python -----------------------------------------------------------
    py_manifests = _find_manifests(project_dir, PYTHON_MANIFESTS)
    if py_manifests:
        # выбираем «ближайший» к target манифест runtime-зависимостей
        runtime_candidates = [m for m in py_manifests
                              if os.path.basename(m) in {"requirements.txt", "pyproject.toml", "setup.py", "Pipfile"}]
        dev_candidates = [m for m in py_manifests
                          if "dev" in os.path.basename(m).lower()]
        runtime_manifest = _pick_nearest(runtime_candidates, target_rel) or (runtime_candidates[0] if runtime_candidates else None)
        dev_manifest = _pick_nearest(dev_candidates, target_rel) or (dev_candidates[0] if dev_candidates else None)

        framework = None
        test_framework = None
        evidence = []

        if runtime_manifest:
            text = _read_text(os.path.join(project_dir, runtime_manifest)) or ""
            names = _extract_dep_names(text)
            joined = " ".join(sorted(names))
            # pyproject.toml declares dependencies in TOML arrays, which are not
            # requirements-style lines; search the original manifest as well.
            framework = (_match_markers(joined, PYTHON_FRAMEWORK_MARKERS)
                         or _match_markers(text, PYTHON_FRAMEWORK_MARKERS))
            evidence.append(f"runtime deps из {runtime_manifest.replace(os.sep, '/')} ({len(names)} пакетов)")

        if dev_manifest:
            text = _read_text(os.path.join(project_dir, dev_manifest)) or ""
            names = _extract_dep_names(text)
            joined = " ".join(sorted(names))
            tf = (_match_markers(joined, PYTHON_TEST_MARKERS)
                  or _match_markers(text, PYTHON_TEST_MARKERS))
            if tf:
                test_framework = tf
                evidence.append(f"test deps из {dev_manifest.replace(os.sep, '/')}")
            # фреймворк может жить в dev-зависимостях тоже
            if not framework:
                framework = _match_markers(joined, PYTHON_FRAMEWORK_MARKERS)

        build_tool = _python_build_tool(runtime_manifest, py_manifests)

        stack = {
            "language": "python",
            "framework": framework,
            "test_framework": test_framework,
            "build_tool": build_tool,
            "detection": {
                "manifest": runtime_manifest,
                "evidence": evidence,
            },
        }
        status = "success" if (framework and test_framework) else "partial"
        if not framework:
            warnings.append("Фреймворк приложения не определён по манифестам (python найден).")
        if not test_framework:
            warnings.append("Тестовый фреймворк не найден в dev-зависимостях.")
        return {"status": status, "stack": stack, "errors": errors, "warnings": warnings}

    # --- Java -------------------------------------------------------------
    java_manifests = _find_manifests(project_dir, JAVA_MANIFESTS)
    if java_manifests:
        manifest = _pick_nearest(java_manifests, target_rel) or java_manifests[0]
        text = _read_text(os.path.join(project_dir, manifest)) or ""
        framework = _match_markers(text, JAVA_FRAMEWORK_MARKERS)
        test_framework = _match_markers(text, JAVA_TEST_MARKERS) or "junit5"
        build_tool = "maven" if manifest.endswith("pom.xml") else "gradle"
        stack = {
            "language": "java",
            "framework": framework,
            "test_framework": test_framework,
            "build_tool": build_tool,
            "detection": {
                "manifest": manifest,
                "evidence": [f"сборочный манифест {manifest}"],
            },
        }
        status = "success" if framework else "partial"
        if not framework:
            warnings.append("Java-фреймворк не определён (spring-boot/quarkus/... не найден в манифесте).")
        return {"status": status, "stack": stack, "errors": errors, "warnings": warnings}

    # --- JavaScript / TypeScript -----------------------------------------
    js_manifests = _find_manifests(project_dir, JS_MANIFESTS)
    if js_manifests:
        manifest = _pick_nearest(js_manifests, target_rel) or js_manifests[0]
        text = _read_text(os.path.join(project_dir, manifest)) or ""
        # package.json — JavaScript/TypeScript share the portable runtime category.
        framework = _match_markers(text, JS_FRAMEWORK_MARKERS)
        test_framework = _match_markers(text, JS_TEST_MARKERS) or "jest"
        stack = {
            "language": "typescript",  # typescript как umbrella
            "framework": framework,
            "test_framework": test_framework,
            "build_tool": "npm",
            "detection": {
                "manifest": manifest,
                "evidence": [f"package.json: {manifest}"],
            },
        }
        status = "success" if framework else "partial"
        if not framework:
            warnings.append("JS/TS-фреймворк не определён (express/next/nest/...).")
        return {"status": status, "stack": stack, "errors": errors, "warnings": warnings}

    # --- Go ---------------------------------------------------------------
    go_manifests = _find_manifests(project_dir, GO_MANIFESTS)
    if go_manifests:
        manifest = _pick_nearest(go_manifests, target_rel) or go_manifests[0]
        text = _read_text(os.path.join(project_dir, manifest)) or ""
        framework = _match_markers(text, GO_FRAMEWORK_MARKERS)
        stack = {
            "language": "go",
            "framework": framework,
            "test_framework": "go-testing",
            "build_tool": "go-mod",
            "detection": {
                "manifest": manifest,
                "evidence": [f"go.mod: {manifest}"],
            },
        }
        status = "success" if framework else "partial"
        if not framework:
            warnings.append("Go-фреймворк не определён (gin/echo/...); используется net/http.")
        return {"status": status, "stack": stack, "errors": errors, "warnings": warnings}

    # --- Ничего не найдено ------------------------------------------------
    errors.append("Манифесты сборки не найдены (нет pyproject/requirements/pom.xml/package.json/go.mod).")
    return {
        "status": "error",
        "stack": {"language": "unknown", "detection": {"manifest": None, "evidence": []}},
        "errors": errors,
        "warnings": warnings,
    }


def _pick_nearest(candidates: list[str], target_rel: str) -> str | None:
    """Выбирает манифест с наибольшим совпадением пути с target (общий родитель)."""
    if not candidates:
        return None
    target_parts = target_rel.replace("\\", "/").split("/")
    best, best_score = None, -1
    for c in candidates:
        cparts = c.replace("\\", "/").split("/")
        # длина общего префикса директорий
        score = 0
        for a, b in zip(cparts[:-1], target_parts[:-1]):
            if a == b:
                score += 1
            else:
                break
        if score > best_score:
            best, best_score = c, score
    return best


def _python_build_tool(runtime_manifest: str | None, all_manifests: list[str]) -> str:
    """Определяет build_tool для python с нормализацией под enum skillsrc."""
    names = {os.path.basename(m) for m in all_manifests}
    if "pyproject.toml" in names:
        # poetry/pdm/uv объявляют себя в pyproject; без парсинга [tool.*] считаем pip
        return "pip"
    if "Pipfile" in names:
        return "pip"
    return "pip"


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


def _java_imported_sources(project_dir: str, target_abs: str) -> list[str]:
    """Resolve direct project-local Java imports without leaving project_dir."""
    source = _read_text(target_abs) or ""
    mask = _strip_java_comments_and_text_blocks(source)
    imported_types = sorted(set(re.findall(r"^\s*import\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)\s*;", mask, re.MULTILINE)))
    resolved: list[str] = []
    for imported in imported_types:
        suffix = imported.replace(".", "/") + ".java"
        for rel in sorted(_find_files_by_suffix(project_dir, suffix)):
            candidate = _confined_path(project_dir, os.path.join(project_dir, rel))
            if not candidate:
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
    for root, _dirs, files in os.walk(project_dir):
        parts = os.path.relpath(root, project_dir).split(os.sep)
        if any(p in {".git", "__pycache__", "node_modules", ".venv", "venv",
                     "target", "build", "dist", ".idea", ".tools"}
               for p in parts):
            continue
        for fn in files:
            rel = os.path.relpath(os.path.join(root, fn), project_dir).replace("\\", "/")
            if rel.endswith(suffix) and _confined_path(project_dir, os.path.join(root, fn)):
                found.append(rel)
    return found


def resolve_target(project_dir: str, target_rel: str) -> tuple[str | None, list[str]]:
    """
    Разрешает target в реальный путь и возвращает (abs_path, warnings).
    Ищет по прямому пути, затем по путевому суффиксу (напр. target='part/api.py' →
    src/backend/InvenTree/part/api.py), затем рекурсивно по имени файла (fallback).
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
    if suffix_matches:
        best = min(suffix_matches, key=len)
        warnings.append(f"target задан как '{target_rel}', найден по пути: {best}")
        candidate = _confined_path(project_dir, os.path.join(project_dir, best))
        if candidate and os.path.isfile(candidate):
            return candidate, warnings

    # 3. Fallback: по имени файла (target мог быть задан коротко, напр. 'api.py')
    basename = os.path.basename(target_rel)
    matches = _find_manifests(project_dir, [basename])  # переиспользуем поиск по имени
    if matches:
        warnings.append(f"target задан как '{target_rel}', найден по имени файла: {matches[0]}")
        candidate = _confined_path(project_dir, os.path.join(project_dir, matches[0]))
        if candidate and os.path.isfile(candidate):
            return candidate, warnings

    warnings.append(f"target '{target_rel}' не найден в проекте.")
    return None, warnings


def extract_companions(project_dir: str, target_abs: str, language: str) -> list[str]:
    """
    Находит сопутствующие модули того же пакета/директории.
    Для python: models.py / serializers.py / urls.py / conftest.py рядом с target.
    """
    companions: list[str] = []
    if language == "java":
        companions = _java_companions(project_dir, target_abs)
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


def _java_mapping_endpoints(text: str) -> list[str]:
    """Extract mapping strings only when the annotation token occurs in Java code."""
    mask = _strip_java_comments_and_text_blocks(text)
    annotations = re.compile(r"@(?P<name>Get|Post|Put|Delete|Patch|Request)Mapping\s*\(|@(?P<jax>Path)\s*\(", re.IGNORECASE)
    literal = re.compile(r'"((?:\\.|[^"\\])*)"')
    named_path = re.compile(r"(?:^|,)\s*(?:path|value)\s*=", re.DOTALL)
    endpoints: list[str] = []
    for match in annotations.finditer(mask):
        opening = mask.find("(", match.start(), match.end())
        depth = 0
        closing = None
        for index in range(opening, len(mask)):
            if mask[index] == "(":
                depth += 1
            elif mask[index] == ")":
                depth -= 1
                if depth == 0:
                    closing = index
                    break
        if closing is None:
            continue
        arguments = text[opening + 1:closing]
        arguments_mask = mask[opening + 1:closing]
        values: list[str] = []
        if match.group("jax"):
            positional = arguments.lstrip()
            if positional.startswith("{"):
                end = positional.find("}")
                values = literal.findall(positional[:end + 1]) if end >= 0 else []
            elif positional.startswith('"'):
                direct = literal.match(positional)
                values = [direct.group(1)] if direct else []
        else:
            named_assignments = list(named_path.finditer(arguments_mask))
            if named_assignments:
                for assignment in named_assignments:
                    start = assignment.end()
                    while start < len(arguments) and arguments[start].isspace():
                        start += 1
                    if start < len(arguments) and arguments[start] == "{":
                        end = arguments_mask.find("}", start)
                        if end >= 0:
                            values.extend(literal.findall(arguments[start:end + 1]))
                    elif start < len(arguments) and arguments[start] == '"':
                        direct = literal.match(arguments[start:])
                        if direct:
                            values.append(direct.group(1))
            else:
                positional = arguments.lstrip()
                if positional.startswith("{"):
                    end = positional.find("}")
                    values = literal.findall(positional[:end + 1]) if end >= 0 else []
                elif positional.startswith('"'):
                    direct = literal.match(positional)
                    values = [direct.group(1)] if direct else []
        for value in values:
            if value not in endpoints:
                endpoints.append(value)
    return endpoints


def _extract_endpoints(project_dir: str, files_rel: list[str], framework: str | None) -> list[str]:
    """
    Грубая эвристика эндпоинтов для аналитики: ищет URL-паттерны и/или HTTP-методы
    в urls.py / api.py / serializers. НЕ LLM — простые regex.
    """
    endpoints: list[str] = []
    # [^'"'\n] — запрещаем переводы строк: иначе жадный класс захватывает
    # многострочные куски кода как «эндпоинт» (мусор в аналитике).
    patterns = [
        # Django url conf
        re.compile(r"""(?:path|url|re_path|include)\(\s*['"]([^'"'\n]{1,120})['"]"""),
        # HTTP-методы рядом со строковым путём
        re.compile(r"""(?:GET|POST|PUT|PATCH|DELETE)\b[^\n]{0,80}?['"]([^'"'\n]{1,120})['"]""", re.IGNORECASE),
        # DRF @api_view / python-декораторы
        re.compile(r"""@(?:get|post|put|patch|delete|api_view)\s*\(\s*(?:.*?['"]([^'"'\n]{1,120})['"])?""", re.IGNORECASE),
        # Spring @*Mapping: @GetMapping("/..."), @RequestMapping(value = "/...")
        re.compile(r"""@(?:Get|Post|Put|Delete|Patch|Request)Mapping\s*\(\s*(?:value\s*=\s*)?['"]([^'"'\n]{1,120})['"]""", re.IGNORECASE),
        # JAX-RS: @Path("/...")
        re.compile(r"""@Path\s*\(\s*['"]([^'"'\n]{1,120})['"]""", re.IGNORECASE),
    ]
    for rel in files_rel:
        base = os.path.basename(rel)
        is_python_urls = base in {"urls.py", "api.py", "serializers.py"}
        is_java_source = base.endswith(".java")
        if not (is_python_urls or is_java_source):
            continue
        candidate = _confined_path(project_dir, os.path.join(project_dir, rel))
        if not candidate:
            continue
        text = _read_text(candidate) or ""
        if is_java_source:
            for endpoint in _java_mapping_endpoints(text):
                if endpoint not in endpoints:
                    endpoints.append(endpoint)
            continue
        for pat in patterns:
            for m in pat.finditer(text):
                val = m.group(1)
                if val and val not in endpoints:
                    endpoints.append(val)
        if len(endpoints) >= 40:
            break
    return endpoints[:40]


def _extract_models(project_dir: str, files_rel: list[str], language: str) -> list[str]:
    """Грубая эвристика моделей: class-имена из models.py (ORM)."""
    models: list[str] = []
    for rel in files_rel:
        base = os.path.basename(rel)
        candidate = _confined_path(project_dir, os.path.join(project_dir, rel))
        if not candidate:
            continue
        text = _read_text(candidate) or ""
        if language == "python":
            if base != "models.py":
                continue
            # class Foo(models.Model) / class Foo(models.ModelBase)
            for m in re.finditer(r"^\s*class\s+(\w+)\s*\(\s*(?:[\w.]*Model[^,)]*)", text, re.MULTILINE):
                name = m.group(1)
                if name not in models:
                    models.append(name)
        elif language == "java":
            # java-модель: файл *Entity.java / *Model.java ИЛИ класс с @Entity/@Table/@Document
            is_entity_file = base.endswith(("Entity.java", "Model.java"))
            has_orm_anno = bool(re.search(r"@(?:Entity|Table|Document)\b", text))
            if not (is_entity_file or has_orm_anno):
                continue
            # 1) имена классов/record с ORM-аннотацией
            for m in re.finditer(r"@(?:Entity|Table|Document)\b[\s\S]{0,300}?(?:class|record)\s+(\w+)", text):
                name = m.group(1)
                if name not in models:
                    models.append(name)
            # 2) entity-файл без аннотации над классом (lombok @Data и пр.) — берём все class/record
            if is_entity_file:
                for m in re.finditer(r"\b(?:class|record)\s+(\w+)", text):
                    name = m.group(1)
                    if name not in models:
                        models.append(name)
        if len(models) >= 40:
            break
    return models[:40]


def render_analytics_block(stack: dict, project_dir: str, files_rel: list[str],
                           target_rel: str, warnings: list[str]) -> str:
    """Генерирует <analytics_documentation>...</analytics_documentation>."""
    module = _guess_module(target_rel)
    endpoints = _extract_endpoints(project_dir, files_rel, stack.get("framework"))
    models = _extract_models(project_dir, files_rel, stack.get("language", "unknown"))

    lines = ["<analytics_documentation>"]
    lines.append(f"  <module>{_xml_escape(module)}</module>")

    lines.append("  <endpoints>")
    for ep in endpoints:
        lines.append(f"    <endpoint>{_xml_escape(ep)}</endpoint>")
    if not endpoints:
        lines.append("    <!-- эндпоинты не извлечены (urls.py/api.py отсутствуют или пусты) -->")
    lines.append("  </endpoints>")

    lines.append("  <models>")
    for m in models:
        lines.append(f"    <model>{_xml_escape(m)}</model>")
    if not models:
        lines.append("    <!-- модели не извлечены (models.py отсутствует или без ORM-классов) -->")
    lines.append("  </models>")

    lines.append("  <stack>")
    lines.append(f"    <language>{_xml_escape(stack.get('language') or 'unknown')}</language>")
    lines.append(f"    <framework>{_xml_escape(stack.get('framework') or 'unknown')}</framework>")
    lines.append(f"    <test_framework>{_xml_escape(stack.get('test_framework') or 'unknown')}</test_framework>")
    lines.append(f"    <build_tool>{_xml_escape(stack.get('build_tool') or 'unknown')}</build_tool>")
    lines.append("  </stack>")

    if warnings:
        lines.append("  <warnings>")
        for w in warnings:
            lines.append(f"    <warning>{_xml_escape(w)}</warning>")
        lines.append("  </warnings>")

    lines.append("</analytics_documentation>")
    return "\n".join(lines)


def _guess_module(target_rel: str) -> str:
    """part/api.py → 'part'; com/example/Foo.java → 'com.example'."""
    norm = target_rel.replace("\\", "/")
    parts = norm.split("/")
    if len(parts) >= 2:
        return parts[-2]
    return os.path.splitext(parts[0])[0] if parts else "unknown"


# ---------------------------------------------------------------------------
# 7. Сборка отчёта
# ---------------------------------------------------------------------------

def _norm(path: str | None) -> str | None:
    """Нормализует разделители путей в forward-slash для кроссплатформенного вывода."""
    if not path:
        return path
    return path.replace("\\", "/")


def resolve_persistent_output_path(output: str) -> tuple[str | None, str | None]:
    """Accept only paths beneath an exact docs/to_do directory, without traversal."""
    raw = Path(output)
    if ".." in raw.parts:
        return None, "--output не должен содержать traversal '..'"
    resolved = raw.resolve()
    parts = resolved.parts
    if not any(parts[index:index + 2] == ("docs", "to_do") for index in range(len(parts) - 1)):
        return None, "--output должен находиться внутри exact docs/to_do"
    return str(resolved), None


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

    # Validate a persistent destination before attempting any write.
    output_error = None
    requested_output = None
    if args.output:
        requested_output, output_error = resolve_persistent_output_path(args.output)
        if output_error:
            errors.append(output_error)
            status = "error"

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
            out_abs = requested_output
            out_dir = os.path.dirname(out_abs)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            try:
                with open(out_abs, "w", encoding="utf-8") as f:
                    f.write(document)
                output_file = out_abs
            except OSError as e:
                errors.append(f"Не удалось записать --output: {e}")
                status = "error"
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
