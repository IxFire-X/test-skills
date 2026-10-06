"""Shared, deterministic build-stack marker catalog."""

from __future__ import annotations

import os
from pathlib import Path

IGNORED_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn", ".idea", ".vscode", ".tools", ".venv", "venv", "__pycache__",
    "node_modules", "target", "build", "dist", "generated", "generated-sources",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".worktrees", ".tox", "htmlcov",
    "coverage", "site-packages", "to_do", "vendor",
    ".secrets", "secrets", ".credentials", "credentials",
    ".gradle", ".kotlin",
})
_IGNORED_DIR_NAMES_CASEFOLDED = frozenset(name.casefold() for name in IGNORED_DIR_NAMES)

# Fixed output names that are also ordinary package names. They are ignored only
# when the directory does not look like project source (see is_source_package_dir).
AMBIGUOUS_OUTPUT_DIR_NAMES = frozenset({"build", "generated", "coverage"})
# Suffixes that build, code-generation and coverage tools do not emit directly into
# their output directory. JavaScript is absent on purpose: bundles and coverage
# reports are JavaScript.
_PACKAGE_SOURCE_SUFFIXES = frozenset({".py", ".java", ".kt", ".go", ".rb", ".cs", ".scala"})
_SOURCE_TREE_SUFFIXES = _PACKAGE_SOURCE_SUFFIXES | {".ts", ".tsx"}
_SOURCE_TREE_PARENTS = frozenset({"src"})
_SOURCE_TREE_SCAN_LIMIT = 2000


def is_ignored_dir_name(name: str) -> bool:
    return name.casefold() in _IGNORED_DIR_NAMES_CASEFOLDED


def is_ambiguous_output_dir_name(name: str) -> bool:
    return name.casefold() in AMBIGUOUS_OUTPUT_DIR_NAMES


def _has_direct_source(path: Path, suffixes: frozenset[str]) -> bool:
    try:
        with os.scandir(path) as entries:
            return any(
                os.path.splitext(entry.name)[1].casefold() in suffixes and entry.is_file(follow_symlinks=False)
                for entry in entries
            )
    except OSError:
        return False


def is_source_package_dir(path: Path, root: Path | None = None) -> bool:
    """Tell a source package named build/generated/coverage from build output.

    Source evidence is deliberately narrow. Anywhere: the directory directly holds
    Python or compiled-language sources (``build/lib/pkg/mod.py`` stays output).
    Under a ``src`` tree: any nested package source counts, so a Java package
    ``src/main/java/com/acme/build`` with only sub-packages is still source.
    """
    path = Path(path)
    if _has_direct_source(path, _PACKAGE_SOURCE_SUFFIXES):
        return True
    try:
        parents = path.relative_to(root).parts[:-1] if root is not None else path.parts[:-1]
    except ValueError:
        parents = path.parts[:-1]
    if not any(part.casefold() in _SOURCE_TREE_PARENTS for part in parents):
        return False
    seen = 0
    for _current, dirs, files in os.walk(path, followlinks=False):
        dirs.sort()
        for name in files:
            seen += 1
            if os.path.splitext(name)[1].casefold() in _SOURCE_TREE_SUFFIXES:
                return True
        if seen > _SOURCE_TREE_SCAN_LIMIT:
            break
    return False


def is_ignored_dir(path: Path, root: Path | None = None) -> bool:
    """Path-aware variant of is_ignored_dir_name that keeps real source packages."""
    name = Path(path).name
    if not is_ignored_dir_name(name):
        return False
    return not (is_ambiguous_output_dir_name(name) and is_source_package_dir(Path(path), root))


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & 0x400)

MANIFEST_LANGUAGES = {
    "pyproject.toml": "python", "requirements.txt": "python",
    "requirements-dev.txt": "python", "setup.py": "python", "Pipfile": "python",
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java",
    "package.json": "typescript", "go.mod": "go",
}
WORKSPACE_MANIFEST_NAMES = frozenset({"settings.gradle", "settings.gradle.kts", "go.work"})


def confined_files(root: Path) -> list[Path]:
    """Walk root without following links, skipping ignored directories."""
    root = Path(root)
    try:
        resolved_root = root.resolve()
    except OSError:
        return []
    found: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = sorted(name for name in dirs if not is_ignored_dir(base / name, root) and not _is_reparse(base / name))
        for name in sorted(files):
            path = base / name
            if _is_reparse(path):
                continue
            try:
                path.resolve().relative_to(resolved_root)
            except (OSError, ValueError):
                continue
            found.append(path)
    return found
PYTHON_MANIFESTS = ["pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "Pipfile"]
JAVA_MANIFESTS = ["pom.xml", "build.gradle", "build.gradle.kts"]
JS_MANIFESTS = ["package.json"]
GO_MANIFESTS = ["go.mod"]
PYTHON_FRAMEWORK_MARKERS = [("django", "django"), ("rest_framework", "django"), ("djangorestframework", "django"), ("fastapi", "fastapi"), ("flask", "flask"), ("aiohttp", "aiohttp"), ("tornado", "tornado")]
JAVA_FRAMEWORK_MARKERS = [("spring-boot-starter", "spring-boot"), ("org.springframework.boot", "spring-boot"), ("quarkus", "quarkus"), ("micronaut", "micronaut")]
JS_FRAMEWORK_MARKERS = [('"express"', "express"), ('"next"', "nextjs"), ('"nuxt"', "nuxt"), ('"@nestjs/core"', "nestjs"), ('"fastify"', "fastify")]
GO_FRAMEWORK_MARKERS = [("github.com/gin-gonic/gin", "gin"), ("github.com/labstack/echo", "echo"), ("github.com/gofiber/fiber", "fiber"), ("github.com/gorilla/mux", "gorilla-mux")]
PYTHON_TEST_MARKERS = [("pytest", "pytest"), ("unittest", "unittest")]
JAVA_TEST_MARKERS = [("junit-jupiter", "junit5")]
JS_TEST_MARKERS = [('"jest"', "jest"), ('"mocha"', "mocha")]
GO_TEST_MARKERS: list[tuple[str, str]] = []
BUILD_TOOL_NORMALIZE = {"uv": "pip", "conda": "pip", "pipenv": "pip", "setuptools": "pip", "kotlin": "gradle"}


def match_marker(text: str, markers: list[tuple[str, str]]) -> str | None:
    lowered = text.lower()
    return next((value for needle, value in markers if needle in lowered), None)


def manifest_language(name: str) -> str | None:
    return MANIFEST_LANGUAGES.get(name)


def normalize_build_tool(value: str) -> str:
    return BUILD_TOOL_NORMALIZE.get(value, value)
