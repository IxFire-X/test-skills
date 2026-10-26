"""Shared, deterministic build-stack marker catalog."""

from __future__ import annotations

import os
from pathlib import Path

if __package__:
    from .scan_rules import directory_reason, path_reason
else:  # direct CLI execution of discover_project / init_skillsrc
    from scan_rules import directory_reason, path_reason


# The module search and the explicit paths of .skillsrc also skip task drafts; the inventory keeps them (a task's
# requirements live in docs/to_do/<task>/ and reach the model only through the inventory).
SEARCH_ONLY_DIRS = frozenset({"to_do"})


def is_ignored_dir_name(name: str) -> bool:
    """The name alone, judged as outside a src tree (FaT-2: inside src/ use is_ignored_dir)."""
    return is_ignored_dir((), name)


def is_ignored_dir(parents: tuple[str, ...], name: str) -> bool:
    return name.casefold() in SEARCH_ONLY_DIRS or directory_reason(parents, name) is not None


def is_ignored_path(parts: tuple[str, ...]) -> bool:
    """Any directory of ``parts`` left out in its place (the inventory rules plus the search-only ones)."""
    return any(part.casefold() in SEARCH_ONLY_DIRS for part in parts) or path_reason(parts) is not None


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
    """Walk root without following links, skipping directories left out in their place (tools.scan_rules)."""
    root = Path(root)
    try:
        resolved_root = root.resolve()
    except OSError:
        return []
    found: list[Path] = []
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        try:
            parents = base.relative_to(root).parts
        except ValueError:
            parents = ()
        dirs[:] = sorted(name for name in dirs if not is_ignored_dir(parents, name) and not _is_reparse(base / name))
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
