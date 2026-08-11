"""Shared, deterministic build-stack marker catalog."""

from __future__ import annotations

IGNORED_DIR_NAMES = frozenset({
    ".git", ".idea", ".tools", ".venv", "venv", "__pycache__",
    "node_modules", "target", "build", "dist", ".pytest_cache", ".ruff_cache",
})

MANIFEST_LANGUAGES = {
    "pyproject.toml": "python", "requirements.txt": "python",
    "requirements-dev.txt": "python", "setup.py": "python", "Pipfile": "python",
    "pom.xml": "java", "build.gradle": "java", "build.gradle.kts": "java",
    "package.json": "typescript", "go.mod": "go",
}
WORKSPACE_MANIFEST_NAMES = frozenset({"settings.gradle", "settings.gradle.kts", "go.work"})
PYTHON_MANIFESTS = ["pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "Pipfile"]
JAVA_MANIFESTS = ["pom.xml", "build.gradle", "build.gradle.kts"]
JS_MANIFESTS = ["package.json"]
GO_MANIFESTS = ["go.mod"]
PYTHON_FRAMEWORK_MARKERS = [("django", "django"), ("rest_framework", "django"), ("djangorestframework", "django"), ("fastapi", "fastapi"), ("flask", "flask"), ("aiohttp", "aiohttp"), ("tornado", "tornado")]
JAVA_FRAMEWORK_MARKERS = [("spring-boot-starter", "spring-boot"), ("org.springframework.boot", "spring-boot"), ("quarkus", "quarkus"), ("micronaut", "micronaut")]
JS_FRAMEWORK_MARKERS = [('"express"', "express"), ('"next"', "nextjs"), ('"nuxt"', "nuxt"), ('"@nestjs/core"', "nestjs"), ('"fastify"', "fastify")]
GO_FRAMEWORK_MARKERS = [("github.com/gin-gonic/gin", "gin"), ("github.com/labstack/echo", "echo"), ("github.com/gofiber/fiber", "fiber"), ("github.com/gorilla/mux", "gorilla-mux")]
PYTHON_TEST_MARKERS = [("pytest", "pytest"), ("nose", "nose")]
JAVA_TEST_MARKERS = [("junit-jupiter", "junit5"), ("junit:junit", "junit4"), ("org.testng", "testng")]
JS_TEST_MARKERS = [('"jest"', "jest"), ('"mocha"', "mocha"), ('"vitest"', "vitest")]
GO_TEST_MARKERS: list[tuple[str, str]] = []
BUILD_TOOL_NORMALIZE = {"uv": "pip", "conda": "pip", "pipenv": "pip", "setuptools": "pip", "kotlin": "gradle"}


def match_marker(text: str, markers: list[tuple[str, str]]) -> str | None:
    lowered = text.lower()
    return next((value for needle, value in markers if needle in lowered), None)


def manifest_language(name: str) -> str | None:
    return MANIFEST_LANGUAGES.get(name)


def normalize_build_tool(value: str) -> str:
    return BUILD_TOOL_NORMALIZE.get(value, value)
