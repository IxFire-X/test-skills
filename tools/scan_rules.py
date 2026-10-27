"""Which directories and file names a scan leaves out, and the summary of what it left out (FaT-2).

One source for the inventory (``project_inventory``) and the module search (``stack_catalog``,
``discover_project``, ``init_skillsrc``).  A directory is judged by its name *and* place: names of build output,
dependencies and secrets that are also ordinary package names (``build``, ``coverage``, ``credentials``…) are left
out only outside a ``src`` tree; inside ``src/…`` they are code, and the content rules decide.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any, Iterable, Mapping, Sequence

VCS_DIRS = frozenset({".git", ".hg", ".svn"})
CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
DEPENDENCY_DIRS = frozenset({"node_modules", ".venv", "venv", "site-packages", ".tox"})
# Tools and CLI agents: settings, skills, worktrees — never project code.
TOOLING_DIRS = frozenset({".idea", ".vscode", ".tools", ".worktrees", ".gigacode", ".claude", ".codex", ".qwen", ".gemini"})
# Build output and dependencies whose names are also package names: left out only outside src/.
DUAL_DIRS = frozenset({"build", "target", "dist", "out", "coverage", "htmlcov", "generated", "generated-sources", "test-results", "vendor"})
SECRET_DIRS = frozenset({"secrets", ".secrets", "credentials", ".credentials"})
SOURCE_SUFFIXES = frozenset({
    ".java", ".kt", ".kts", ".scala", ".groovy", ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".cs", ".rb", ".php",
    ".swift", ".c", ".cc", ".cpp", ".h", ".hpp",
})
# Only these name rules can be lifted by `paths.include`; files inside still pass every file rule.
INCLUDABLE_RULES = frozenset({"output-directory-v1", "dependency-directory-v1", "secret-directory-v1"})
# The content rules of project_inventory (token_signature_rule): a source file that fell out by them is worth a look.
CONTENT_RULES = frozenset({"token-assignment-v1", "github-token-v1", "openai-key-v1", "service-token-v1", "aws-key-v1",
                           "google-key-v1", "jwt-v1", "url-credentials-v1", "private-key-pem-v1", "password-assignment-v1"})
_SUMMARY_PATHS = 10


def _in_src(parents: Sequence[str]) -> bool:
    return any(part.casefold() == "src" for part in parents)


def directory_reason(parents: Sequence[str], name: str) -> tuple[str, str] | None:
    """``(reason_code, scanner_rule_id)`` when the directory ``name`` under ``parents`` is left out."""
    folded = name.casefold()
    if folded in VCS_DIRS:
        return "VCS", "vcs-directory-v1"
    if folded in CACHE_DIRS:
        return "BUILD_OUTPUT", "output-directory-v1"
    if folded in DEPENDENCY_DIRS:
        return "DEPENDENCY", "dependency-directory-v1"
    if folded in TOOLING_DIRS:
        return "EXCLUDED_DIRECTORY", "tooling-directory-v1"
    if _in_src(parents):
        return None
    if folded in DUAL_DIRS:
        return ("DEPENDENCY", "dependency-directory-v1") if folded == "vendor" else ("BUILD_OUTPUT", "output-directory-v1")
    if folded in SECRET_DIRS:
        return "SECRET_SUSPECTED", "secret-directory-v1"
    return None


def path_reason(parts: Sequence[str]) -> tuple[str, str] | None:
    """The first directory of ``parts`` (all of them directories) that is left out, judged in its place."""
    for index, part in enumerate(parts):
        reason = directory_reason(parts[:index], part)
        if reason is not None:
            return reason
    return None


def is_source(name: str) -> bool:
    suffix = "." + name.rsplit(".", 1)[-1].casefold() if "." in name else ""
    return suffix in SOURCE_SUFFIXES


def include_mode(project_path: str, include: Iterable[str]) -> str | None:
    """``"full"`` when a glob covers the path (it or a parent); ``"through"`` when a glob only leads inside it.

    A directory included "through" is a passage: only its children on the way of a glob are walked, a file right in it
    only when a glob covers that file.
    """
    parts = project_path.strip("/").split("/")
    through = False
    for pattern in (item.strip("/") for item in include):
        bare = pattern[:-3] if pattern.endswith("/**") else pattern
        if any(fnmatchcase("/".join(parts[:depth]), bare) for depth in range(1, len(parts) + 1)):
            return "full"
        if bare.startswith("/".join(parts) + "/"):
            through = True
    return "through" if through else None


def exclusion_summary(exclusions: Iterable[Mapping[str, Any]], module: str = ".") -> dict[str, Any]:
    """Counts by reason and rule, up to ten paths each, and the exclusions a person should look at."""
    prefix = "" if module in {"", "."} else module.rstrip("/") + "/"
    by_reason: dict[str, dict[str, Any]] = {}
    suspicious: dict[str, list[str]] = {}
    for row in sorted(exclusions, key=lambda item: str(item.get("project_path"))):
        reason, rule, path = str(row.get("reason_code")), str(row.get("scanner_rule_id")), str(row.get("project_path"))
        entry = by_reason.setdefault(reason, {"count": 0, "rules": {}, "paths": []})
        entry["count"] += 1
        entry["rules"][rule] = entry["rules"].get(rule, 0) + 1
        if len(entry["paths"]) < _SUMMARY_PATHS:
            entry["paths"].append(path)
        kind = _suspicious_kind(path, rule, prefix)
        if kind is not None and len(suspicious.setdefault(kind, [])) < _SUMMARY_PATHS:
            suspicious[kind].append(path)
    return {"total": sum(entry["count"] for entry in by_reason.values()), "by_reason": by_reason, "suspicious": suspicious,
            "hint": "лишнее исключение возвращает .skillsrc paths.include модуля (glob от корня проекта); "
                    "файлы с секретами и бинарные include не возвращает"}


def _suspicious_kind(path: str, rule: str, prefix: str) -> str | None:
    name = path.rsplit("/", 1)[-1].casefold()
    if rule in {"output-directory-v1", "dependency-directory-v1"} and name in DUAL_DIRS:
        inside = path[len(prefix):] if path.startswith(prefix) else path
        return None if "/" not in inside else "dual_name_outside_module_root"  # target/ at the module root is expected
    if rule == "secret-filename-v1":
        return "secret_filename"
    if rule in CONTENT_RULES:
        return "source_secret_content" if is_source(name) else None
    return None
