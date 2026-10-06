"""Controller-owned safe project inventory, execution baseline, and C-lite batches.

This module deliberately accepts controller-resolved roots and opaque identifiers only.
It never serializes source bytes into inventory/baseline receipts and never gives a
caller an opportunity to nominate an arbitrary filesystem path for model context.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.stack_catalog import MANIFEST_LANGUAGES, is_ambiguous_output_dir_name, is_source_package_dir
from tools.schema_validation import StrictJsonError, loads_json_strict, schema_diagnostics


ROOT = Path(__file__).resolve().parents[1]


class InventoryError(ValueError):
    """The controller could not build a safe inventory."""


class ContextSelectionError(ValueError):
    """A requested context batch is not safely selectable."""


_VCS_DIRS = frozenset({".git", ".hg", ".svn"})
_DEPENDENCY_DIRS = frozenset({".tox", ".venv", "node_modules", "site-packages", "vendor", "venv"})
_BUILD_OUTPUT_DIRS = frozenset({
    ".gradle", ".kotlin", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "build", "coverage", "dist",
    "generated", "generated-sources", "htmlcov", "target", "test-results",
})
_IDE_DIRS = frozenset({".idea", ".vscode"})
_SECRET_DIRS = frozenset({".credentials", ".secrets", "credentials", "secrets"})
_BINARY_SUFFIXES = frozenset({
    ".7z", ".a", ".bin", ".class", ".dll", ".dylib", ".exe", ".gif", ".gz", ".ico", ".jar", ".jpeg",
    ".jpg", ".lock", ".mp3", ".mp4", ".o", ".pdf", ".png", ".pyc", ".so", ".tar", ".war", ".webp", ".zip",
})
_GENERATED_SUFFIXES = frozenset({".coverage", ".map", ".min.js"})
# Only unmistakable secret containers are excluded by name. Words such as
# "password" or "token" in a file name say nothing about its content.
_PRIVATE_SUFFIXES = frozenset({".kdbx", ".key", ".p12", ".pfx", ".pem"})
_PRIVATE_NAME = re.compile(r"id_[a-z0-9_-]+(?:\.pub)?", re.I)
_DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc"})
# coverage.py / .coveragerc / coverage.yml are source and configuration, not output.
_COVERAGE_OUTPUT_SUFFIXES = frozenset({".xml", ".json", ".lcov", ".info", ".out", ".txt", ".html", ".db", ".dat", ".ec"})
_CONTEXT_GAP_REASON = "FILE_EXCEEDS_BYTE_BUDGET"
DEFAULT_CONTEXT_LIMITS = {"context_batch_bytes": 256 * 1024, "docs_file_bytes": 256 * 1024, "docs_total_bytes": 1024 * 1024}
_SPECIFICATION_DATA_SUFFIXES = frozenset({".yaml", ".yml", ".json"})
_DOCUMENT_DIRS = frozenset({"docs", "doc", "openspec"})
_GIT_TIMEOUT_SECONDS = 30
# Legacy output scrubber: intentionally broad, because over-redacting a runner log is
# harmless. Inventory decisions use _CONTENT_SIGNATURES below instead.
_TOKEN_SIGNATURES = (
    ("token-assignment-v1", re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{8,}")),
    ("github-token-v1", re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b")),
    ("openai-key-v1", re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}\b")),
    ("service-token-v1", re.compile(rb"\b(?:glpat[-_]|sk_live_|rk_live_|xox[bpars]-|hf_|npm_|pypi-)[A-Za-z0-9_-]{8,}")),
    ("aws-key-v1", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-key-v1", re.compile(rb"\bAIza[A-Za-z0-9_-]{20,}")),
    ("jwt-v1", re.compile(rb"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")),
    ("url-credentials-v1", re.compile(rb"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")),
)
_PRIVATE_KEY_BEGIN = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
_PRIVATE_KEY_END = re.compile(rb"-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")
# Inventory content rules. Every pattern is confined to one line and is either a
# vendor token format or the assignment of a quoted literal of at least 16
# characters. Identifiers, function calls, placeholders and prose do not match.
_CONTENT_SIGNATURES = (
    ("private-key-block-v1", _PRIVATE_KEY_BEGIN),
    ("aws-key-v1", re.compile(rb"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github-token-v1", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})\b")),
    ("openai-key-v1", re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("service-token-v1", re.compile(
        rb"\b(?:glpat-[A-Za-z0-9_-]{20,}|[sr]k_live_[A-Za-z0-9]{16,}|xox[abprs]-[A-Za-z0-9-]{10,}"
        rb"|hf_[A-Za-z0-9]{30,}|npm_[A-Za-z0-9]{36}|pypi-AgE[A-Za-z0-9_-]{20,})"
    )),
    ("google-key-v1", re.compile(rb"\bAIza[A-Za-z0-9_-]{35}")),
    ("jwt-v1", re.compile(rb"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url-credentials-v1", re.compile(rb"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@$<{]+:[^/\s@$<{]+@")),
    ("token-assignment-v1", re.compile(
        rb"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd|secret|token)"
        rb"[\"']?[ \t]*[:=][ \t]*(?:\"[A-Za-z0-9_./+=!@#%^&*~-]{16,}\"|'[A-Za-z0-9_./+=!@#%^&*~-]{16,}')"
    )),
)
_CLOSED_MANIFESTS = frozenset({
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "pipfile", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties",
    "libs.versions.toml", "maven.config", "jvm.config", "extensions.xml", "package.json", "go.mod", "go.work",
})
_PARENT_BUILD_PATHS = frozenset({
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "Pipfile", "pom.xml",
    "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties",
    "gradle/libs.versions.toml", ".mvn/maven.config", ".mvn/jvm.config", ".mvn/extensions.xml",
    "package.json", "go.mod", "go.work",
})
_CLOSED_ADAPTER_IDS = frozenset({
    "pytest:selected-symbols-v1", "maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1", "gradle-wrapper:selected-symbols-v1",
})
_EXECUTION_SOURCE_SUFFIXES = frozenset({
    ".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".cs",
})
_EXECUTION_CONFIG_SUFFIXES = frozenset({".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg"})


def _canonical_bytes(value: Mapping[str, Any] | list[Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: Mapping[str, Any] | list[Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def build_skillsrc_authority_receipt(
    skillsrc_bytes: bytes,
    *,
    acceptance_kind: str = "existing-valid",
    approval_digest: str | None = None,
) -> dict[str, Any]:
    """Create the minimal accepted authority receipt embedded in a frozen baseline."""
    if acceptance_kind not in {"existing-valid", "automatic-create", "confirmed-replacement"}:
        raise InventoryError("skillsrc authority kind is invalid")
    if acceptance_kind == "confirmed-replacement":
        if not isinstance(approval_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", approval_digest):
            raise InventoryError("confirmed replacement requires approval evidence")
    elif approval_digest is not None:
        raise InventoryError("approval evidence is valid only for a confirmed replacement")
    receipt: dict[str, Any] = {
        "schema_version": "1.0.0",
        "acceptance_kind": acceptance_kind,
        "skillsrc_digest": "sha256:" + hashlib.sha256(skillsrc_bytes).hexdigest(),
    }
    if approval_digest is not None:
        receipt["approval_digest"] = approval_digest
    receipt["digest"] = _digest(receipt)
    return receipt


def _validate_receipt_schema(value: Mapping[str, Any], schema_name: str, error_type: type[ValueError]) -> None:
    errors = schema_diagnostics(dict(value), ROOT / "schemas" / schema_name, ROOT)
    if errors:
        raise error_type(f"{schema_name} is invalid: {errors[0]['code']}")


def _content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def module_runtime_path(project_root: Path, relative_path: str) -> Path:
    """Keep module-local invocation paths, including a standard POSIX venv link."""
    project = Path(project_root).resolve()
    raw = Path(relative_path)
    text = str(relative_path).replace("\\", "/")
    if raw.is_absolute() or not text or text.startswith(("/", "//")) or ":" in text or any(part in {"", ".", ".."} for part in text.split("/")):
        raise InventoryError("runtime path is not a safe relative path")
    candidate = project
    for part in text.split("/"):
        candidate /= part
        if _is_reparse(candidate):
            if candidate != project / text or os.name == "nt" or not candidate.is_symlink():
                raise InventoryError("runtime path contains a symlink or reparse point")
            # Only the interpreter link of a local venv may name a host runtime.
            config = candidate.parent.parent / "pyvenv.cfg"
            if candidate.parent.name != "bin" or not re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", candidate.name) or _is_reparse(config):
                raise InventoryError("runtime path contains a symlink or reparse point outside a venv")
            try:
                with config.open("rb") as stream:
                    config_bytes = stream.read(65537)
                if len(config_bytes) > 65536:
                    raise ValueError("venv configuration is too large")
                settings = dict(line.split("=", 1) for line in config_bytes.decode("utf-8").splitlines() if "=" in line)
                settings = {key.strip(): value.strip() for key, value in settings.items()}
                runtime_home = Path(settings["home"])
                resolved = candidate.resolve(strict=True)
                if not runtime_home.is_absolute() or resolved.parent != runtime_home.resolve(strict=True) or not re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", resolved.name) or not resolved.is_file():
                    raise ValueError("venv interpreter does not match its home")
            except (KeyError, OSError, UnicodeError, ValueError) as error:
                raise InventoryError("runtime symlink is not bound to a valid local venv") from error
            return candidate
    try:
        candidate.resolve().relative_to(project)
    except (OSError, ValueError) as error:
        raise InventoryError("runtime path escapes project") from error
    if _is_reparse(candidate) or not candidate.is_file():
        raise InventoryError("runtime path is not an exact regular file")
    return candidate


def system_maven_path(executable: str) -> Path:
    """Resolve the one closed host tool; its launcher is the identity boundary."""
    if not isinstance(executable, str) or not executable or "\0" in executable:
        raise InventoryError("system Maven executable is missing")
    raw = Path(executable)
    if executable != "mvn" and not raw.is_absolute():
        raise InventoryError("system Maven requires mvn or an absolute launcher path")
    found = shutil.which("mvn") if executable == "mvn" else executable
    if not found:
        raise InventoryError("system Maven is unavailable on PATH")
    candidate = Path(found).absolute()
    if candidate.name.casefold() not in ({"mvn.cmd"} if os.name == "nt" else {"mvn"}):
        raise InventoryError("system Maven path must name the closed launcher")
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as error:
        raise InventoryError("system Maven launcher is unavailable") from error
    if not candidate.is_file() or (os.name != "nt" and not os.access(candidate, os.X_OK)):
        raise InventoryError("system Maven launcher is unavailable or not executable")
    return candidate.resolve()


def runtime_identity(project_root: Path, relative_path: str, *, adapter_id: str | None = None) -> str:
    """Bind runtime bytes, and the configuration/target of a POSIX venv link."""
    candidate = system_maven_path(relative_path) if adapter_id == "maven:selected-symbols-v1" else module_runtime_path(project_root, relative_path)
    content = _content_digest(candidate)
    if adapter_id == "maven:selected-symbols-v1":
        return _digest({"path": str(candidate), "launcher": content})
    if candidate.is_symlink():
        return _digest({"runtime": content, "target": str(candidate.resolve(strict=True)), "venv": _content_digest(candidate.parent.parent / "pyvenv.cfg")})
    return content


def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return True
    return path.is_symlink() or bool(getattr(details, "st_file_attributes", 0) & 0x400)


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve(strict=False).relative_to(project_root).as_posix()


def _safe_rel(project_root: Path, path: Path) -> str | None:
    try:
        return _relative(project_root, path)
    except (OSError, ValueError):
        return None


def _lexical_rel(project_root: Path, path: Path) -> str | None:
    try:
        return path.absolute().relative_to(project_root.absolute()).as_posix()
    except ValueError:
        return None


def _opaque_id(project_identity: str, module: str, project_path: str) -> str:
    value = f"pilot-inventory-file-v1\0{project_identity}\0{module}\0{project_path}".encode("utf-8")
    return "file-" + hashlib.sha256(value).hexdigest()[:24]


def project_identity(project_root: Path) -> str:
    normalized = os.path.normcase(str(project_root))
    return "project-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


_project_identity = project_identity


def _language(path: Path) -> str:
    suffix = path.suffix.casefold()
    return {
        ".py": "python", ".java": "java", ".kt": "kotlin", ".js": "javascript", ".jsx": "javascript",
        ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rb": "ruby", ".cs": "csharp",
        ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".xml": "xml",
    }.get(suffix, MANIFEST_LANGUAGES.get(path.name, "text"))


def _kind(project_path: str, path: Path) -> str:
    name = path.name.casefold()
    parts = {part.casefold() for part in Path(project_path).parts}
    if name == ".skillsrc" or name in _CLOSED_MANIFESTS or path.suffix.casefold() in {".json", ".toml", ".yaml", ".yml", ".xml", ".ini", ".cfg"}:
        return "config"
    if "test" in name or {"tests", "test", "spec", "specs"} & parts:
        return "test"
    if {"fixture", "fixtures", "resource", "resources"} & parts:
        return "fixture"
    if path.suffix.casefold() in {".py", ".java", ".kt", ".js", ".jsx", ".ts", ".tsx", ".go", ".rb", ".cs"}:
        return "source"
    return "resource"


def token_signature_rule(contents: bytes) -> str | None:
    """Return the legacy output-scrubbing rule for a credential-like byte sequence."""
    # Shell ${NAME:+word} substitutes word; it does not assign word to NAME.
    # Keep word visible, including any real assignment or service token inside it.
    assignments = re.sub(rb"(\$\{[A-Za-z_][A-Za-z0-9_]*):\+", rb"\1 ", contents)
    return next((rule_id for rule_id, signature in _TOKEN_SIGNATURES if signature.search(assignments if rule_id == "token-assignment-v1" else contents)), None)


def content_secret_rule(contents: bytes) -> str | None:
    """Return the high-confidence inventory rule matched by file bytes, if any."""
    return next((rule_id for rule_id, signature in _CONTENT_SIGNATURES if signature.search(contents)), None)


def redact_document(contents: bytes) -> tuple[bytes, list[dict[str, Any]]]:
    """Mask every secret-bearing line of a document, keeping its line structure.

    Returns the masked bytes and a value-free receipt: ``[{"line", "scanner_rule_id"}]``.
    A private-key block is masked through its END marker (or the end of the file).
    """
    if content_secret_rule(contents) is None:
        return contents, []
    masked: list[bytes] = []
    redactions: list[dict[str, Any]] = []
    in_key_block = False
    for number, line in enumerate(contents.splitlines(keepends=True), start=1):
        body = line.rstrip(b"\r\n")
        ending = line[len(body):]
        if in_key_block:
            rule = "private-key-block-v1"
            in_key_block = _PRIVATE_KEY_END.search(body) is None
        else:
            rule = content_secret_rule(body)
            if rule == "private-key-block-v1":
                in_key_block = _PRIVATE_KEY_END.search(body) is None
        if rule is None:
            masked.append(line)
            continue
        masked.append(f"[REDACTED:{rule}]".encode("ascii") + ending)
        redactions.append({"line": number, "scanner_rule_id": rule})
    return b"".join(masked), redactions


def redact_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Text variant of redact_document for already-decoded requirement documents."""
    masked, redactions = redact_document(text.encode("utf-8"))
    return (masked.decode("utf-8"), redactions) if redactions else (text, [])


def _is_document(path: Path, relative_parts: Iterable[str] = ()) -> bool:
    """Documents and specifications are masked line by line instead of being excluded.

    Prose formats, Gherkin features, OpenAPI/AsyncAPI descriptions and structured files
    kept in a documentation or OpenSpec directory routinely carry example tokens.
    """
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if name in _CLOSED_MANIFESTS:
        return False
    if suffix in _DOCUMENT_SUFFIXES or suffix == ".feature":
        return True
    if suffix not in _SPECIFICATION_DATA_SUFFIXES:
        return False
    return name.startswith(("openapi", "swagger", "asyncapi")) or bool(_DOCUMENT_DIRS & {part.casefold() for part in relative_parts})


def _secret_name_rule(path: Path, relative_parts: Iterable[str] = ()) -> tuple[str, str] | None:
    """Exclude by name only explicit secret containers (decision 8)."""
    name = path.name.casefold()
    if name.startswith(".env"):
        return "filename-dotenv-v1", "dotenv"
    if path.suffix.casefold() in _PRIVATE_SUFFIXES or _PRIVATE_NAME.fullmatch(name):
        return "private-extension-v1", "private-material"
    if _SECRET_DIRS & {part.casefold() for part in relative_parts}:
        return "secret-directory-v1", "credential-material"
    return None


def _secret_rule(path: Path, contents: bytes, relative_parts: Iterable[str] = ()) -> tuple[str, str] | None:
    named = _secret_name_rule(path, relative_parts)
    if named is not None:
        return named
    rule_id = content_secret_rule(contents)
    if rule_id is not None:
        return rule_id, "token-signature"
    return None


def file_secret_rule(project_root: Path, path: Path) -> str | None:
    """Apply the inventory secret filter to one project file; unreadable is unsafe."""
    relative = _lexical_rel(Path(project_root), Path(path))
    parts = Path(relative).parts[:-1] if relative else ()
    named = _secret_name_rule(Path(path), parts)
    if named is not None:
        return named[0]
    try:
        contents = Path(path).read_bytes()
    except OSError:
        return "readability-v1"
    return content_secret_rule(contents)


_CONTENT_FACTS: dict[tuple[str, bool], tuple[tuple[int, int, int], dict[str, Any]]] = {}
_CONTENT_FACTS_LIMIT = 50_000
_CONTENT_SETTLE_NS = 50_000_000


def _content_facts(path: Path, is_document: bool) -> dict[str, Any]:
    """Size, digest and secret findings of one file, re-read only when the file changed.

    One ``exec`` builds the inventory several times around the test process;
    unchanged files are recognised by size, mtime and inode.  A file modified
    within the last moments is always read again, because a rewrite inside the
    timestamp granularity could keep the same signature.
    """
    import time

    key = (os.path.normcase(str(path)), is_document)
    details = os.stat(path)
    signature = (details.st_size, details.st_mtime_ns, details.st_ino)
    cached = _CONTENT_FACTS.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    observed_ns = time.time_ns()
    contents = path.read_bytes()
    facts: dict[str, Any] = {"binary": b"\0" in contents[:8192], "secret_rule": None, "redactions": [], "size": len(contents),
                             "content_digest": "sha256:" + hashlib.sha256(contents).hexdigest()}
    if not facts["binary"]:
        if is_document:
            _masked, facts["redactions"] = redact_document(contents)
        else:
            facts["secret_rule"] = content_secret_rule(contents)
    after = os.stat(path)
    if (after.st_size, after.st_mtime_ns, after.st_ino) == signature and signature[1] <= observed_ns - _CONTENT_SETTLE_NS:
        if len(_CONTENT_FACTS) >= _CONTENT_FACTS_LIMIT:
            _CONTENT_FACTS.clear()
        _CONTENT_FACTS[key] = (signature, facts)
    else:
        _CONTENT_FACTS.pop(key, None)
    return facts


def _is_binary_or_generated(path: Path) -> bool:
    name = path.name.casefold()
    return (
        path.suffix.casefold() in _BINARY_SUFFIXES or name.endswith(".min.js") or name.endswith(".generated.py")
        or name.endswith("generated.java") or name.startswith("generated_")
    )


def _output_file_rule(path: Path) -> tuple[str, str] | None:
    """Recognize run by-products that would otherwise look like baseline drift."""
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if suffix == ".log":
        return "log-file-v1", "log-file"
    if name == ".coverage" or name.startswith(".coverage.") or name == "lcov.info" or (name.startswith("coverage") and suffix in _COVERAGE_OUTPUT_SUFFIXES):
        return "coverage-output-v1", "coverage-output"
    return None


def _directory_reason(name: str, path: Path | None = None, project_root: Path | None = None) -> tuple[str, str] | None:
    folded = name.casefold()
    if folded in _VCS_DIRS:
        return "VCS", "vcs-directory-v1"
    if folded in _DEPENDENCY_DIRS:
        return "DEPENDENCY", "dependency-directory-v1"
    if folded in _IDE_DIRS:
        return "EXCLUDED_DIRECTORY", "ide-directory-v1"
    if folded in _BUILD_OUTPUT_DIRS or folded.startswith("coverage"):
        # build/generated/coverage are also ordinary package names (M32).
        ambiguous = is_ambiguous_output_dir_name(folded) or folded.startswith("coverage")
        if ambiguous and path is not None and is_source_package_dir(path, project_root):
            return None
        return "BUILD_OUTPUT", "output-directory-v1"
    if folded in _SECRET_DIRS:
        return "SECRET_SUSPECTED", "secret-directory-v1"
    return None


def _git_listing(project_root: Path, module_root: Path) -> tuple[set[str], set[str], set[str]] | None:
    """Return git-visible files below module_root, or None to use the plain walk.

    Uses ``git ls-files -co --exclude-standard`` so ``.gitignore``, ``.git/info/exclude``
    and the user's global excludes apply. Never overrides ``safe.directory``; a missing
    executable, a refused repository, a timeout or any error falls back to the walk.
    """
    if not (project_root / ".git").exists():
        return None
    environment = {key: value for key, value in os.environ.items() if key not in {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_NAMESPACE"}}
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.quotepath=off", "ls-files", "-z", "-co", "--exclude-standard"],
            cwd=str(module_root), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=_GIT_TIMEOUT_SECONDS, env=environment, check=False,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if completed.returncode != 0 or not isinstance(completed.stdout, bytes):
        return None
    try:
        module = module_root.relative_to(project_root).as_posix()
    except ValueError:
        return None
    prefix = "" if module == "." else module + "/"
    fold = (lambda value: value.casefold()) if os.name == "nt" else (lambda value: value)
    files: set[str] = set()
    directories: set[str] = set()
    opaque: set[str] = set()
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            entry = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if entry.startswith(("/", "../")) or "/../" in entry:
            return None
        nested_repository = entry.endswith("/")
        project_path = fold(prefix + entry.rstrip("/"))
        # A submodule (gitlink) or an untracked nested repository is listed as one
        # entry; git does not describe its files, so it is walked without the filter.
        (opaque if nested_repository else files).add(project_path)
        parts = project_path.split("/")
        for index in range(1, len(parts)):
            directories.add("/".join(parts[:index]))
    return files, directories, opaque


def _exclusion(project_path: str, reason_code: str, scanner_rule_id: str, safe_label: str) -> dict[str, str]:
    return {
        "project_path": project_path,
        "reason_code": reason_code,
        "scanner_rule_id": scanner_rule_id,
        "safe_label": safe_label,
    }


def build_inventory(
    project_root: Path,
    module_root: Path,
    *,
    skill_pack_root: Path | None = None,
    generated_roots: Iterable[Path] = (),
    proved_dependency_files: Iterable[Path] = (),
) -> dict[str, Any]:
    """Return a complete metadata-only inventory for one controller-selected module."""
    declared_project = Path(project_root).absolute()
    declared_module = Path(module_root).absolute()
    if not declared_project.is_dir() or not declared_module.is_dir():
        raise InventoryError("project and module roots must exist")
    try:
        relative_module = declared_module.relative_to(declared_project)
    except ValueError as error:
        raise InventoryError("module root must be confined to project root") from error
    current = declared_project
    if _is_reparse(current):
        raise InventoryError("project or module root is a symlink or reparse point")
    for part in relative_module.parts:
        current /= part
        if _is_reparse(current):
            raise InventoryError("project or module root is a symlink or reparse point")
    project_root = declared_project.resolve()
    module_root = declared_module.resolve()
    module = relative_module.as_posix() or "."
    skill_root = Path(skill_pack_root).resolve() if skill_pack_root is not None else None
    state_roots = tuple(Path(path).resolve() for path in generated_roots)

    def excluded_root(path: Path) -> tuple[str, str, str] | None:
        if skill_root is not None and (path == skill_root or skill_root in path.parents):
            return "SKILL_PACK", "skill-pack-root-v1", "skill-pack"
        if any(path == root or root in path.parents for root in state_roots):
            return "BUILD_OUTPUT", "pipeline-state-root-v1", "pipeline-state"
        return None
    project_identity = _project_identity(project_root)
    files: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []

    def walk_error(error: OSError) -> None:
        relative = _lexical_rel(project_root, Path(error.filename)) if error.filename else None
        raise InventoryError(f"inventory directory is unreadable: {relative or module}") from error

    listing = _git_listing(project_root, module_root)
    fold = (lambda value: value.casefold()) if os.name == "nt" else (lambda value: value)
    opaque_roots: list[str] = []

    def git_ignored(project_path: str, *, directory: bool) -> bool:
        """True when git proves the path is ignored; nested repositories are not filtered."""
        if listing is None:
            return False
        git_files, git_directories, git_opaque = listing
        key = fold(project_path)
        if any(key == root or key.startswith(root + "/") for root in opaque_roots):
            return False
        if directory:
            if key in git_opaque or key in git_files:
                opaque_roots.append(key)
                return False
            return key not in git_directories
        return key not in git_files

    def file_entry(project_path: str, path: Path, contents: bytes, *, kind: str | None = None, redactions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "opaque_id": _opaque_id(project_identity, module, project_path),
            "project_path": project_path,
            "kind": kind or _kind(project_path, path),
            "language": _language(path),
            "size": len(contents),
            "content_digest": "sha256:" + hashlib.sha256(contents).hexdigest(),
        }
        if redactions:
            entry["redactions"] = redactions
        return entry

    for current, dir_names, file_names in os.walk(module_root, topdown=True, followlinks=False, onerror=walk_error):
        current_path = Path(current)
        admitted_dirs: list[str] = []
        for name in sorted(dir_names):
            candidate = current_path / name
            project_path = _safe_rel(project_root, candidate)
            if project_path is None or _is_reparse(candidate):
                lexical_path = _lexical_rel(project_root, candidate)
                if lexical_path is not None:
                    exclusions.append(_exclusion(lexical_path, "REPARSE_ESCAPE", "reparse-policy-v1", "reparse"))
                continue
            root_exclusion = excluded_root(candidate)
            if root_exclusion is not None:
                exclusions.append(_exclusion(project_path, *root_exclusion))
                continue
            excluded = _directory_reason(name, candidate, project_root)
            if excluded is not None:
                reason_code, rule = excluded
                exclusions.append(_exclusion(project_path, reason_code, rule, "excluded-directory"))
                continue
            if git_ignored(project_path, directory=True):
                exclusions.append(_exclusion(project_path, "GIT_IGNORED", "gitignore-v1", "git-ignored"))
                continue
            admitted_dirs.append(name)
        dir_names[:] = admitted_dirs
        for name in sorted(file_names):
            path = current_path / name
            project_path = _safe_rel(project_root, path)
            if project_path is None or _is_reparse(path):
                lexical_path = _lexical_rel(project_root, path)
                if lexical_path is not None:
                    exclusions.append(_exclusion(lexical_path, "REPARSE_ESCAPE", "reparse-policy-v1", "reparse"))
                continue
            root_exclusion = excluded_root(path)
            if root_exclusion is not None:
                exclusions.append(_exclusion(project_path, *root_exclusion))
                continue
            suffix = path.suffix.casefold()
            if suffix in _GENERATED_SUFFIXES or _is_binary_or_generated(path):
                exclusions.append(_exclusion(project_path, "BINARY_OR_GENERATED", "binary-generated-v1", "binary-or-generated"))
                continue
            secret = _secret_name_rule(path)
            if secret is not None:
                rule, label = secret
                exclusions.append(_exclusion(project_path, "SECRET_SUSPECTED", rule, label))
                continue
            output = _output_file_rule(path)
            if output is not None:
                exclusions.append(_exclusion(project_path, "BUILD_OUTPUT", *output))
                continue
            # The authoritative manifest is a baseline input even when a project ignores it.
            if name != ".skillsrc" and git_ignored(project_path, directory=False):
                exclusions.append(_exclusion(project_path, "GIT_IGNORED", "gitignore-v1", "git-ignored"))
                continue
            is_document = _is_document(path, Path(project_path).parts[:-1])
            try:
                facts = _content_facts(path, is_document)
            except OSError as error:
                if _kind(project_path, path) in {"source", "config"} or suffix in _DOCUMENT_SUFFIXES:
                    raise InventoryError(f"required inventory file is unreadable: {project_path}") from error
                exclusions.append(_exclusion(project_path, "UNREADABLE", "readability-v1", "unreadable"))
                continue
            if facts["binary"]:
                exclusions.append(_exclusion(project_path, "BINARY_OR_GENERATED", "binary-content-v1", "binary-or-generated"))
                continue
            # Documents and specifications stay visible: only the matching lines are
            # masked when bytes are handed out, and the receipt records which lines.
            if facts["secret_rule"] is not None:
                exclusions.append(_exclusion(project_path, "SECRET_SUSPECTED", facts["secret_rule"], "token-signature"))
                continue
            entry = file_entry(project_path, path, b"", redactions=[dict(row) for row in facts["redactions"]])
            entry["size"], entry["content_digest"] = facts["size"], facts["content_digest"]
            files.append(entry)
    for declared_dependency in proved_dependency_files:
        raw = Path(declared_dependency)
        candidate = raw if raw.is_absolute() else project_root / raw
        lexical_path = _lexical_rel(project_root, candidate)
        if lexical_path is None:
            raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: local dependency escapes project")
        probe = project_root
        for part in Path(lexical_path).parts:
            probe /= part
            if _is_reparse(probe):
                raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: local dependency is a reparse path")
        dependency_parts = Path(lexical_path).parts
        if not candidate.is_file() or excluded_root(candidate) is not None or any(
            _directory_reason(part, project_root.joinpath(*dependency_parts[:index + 1]), project_root)
            for index, part in enumerate(dependency_parts[:-1])
        ):
            raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: local dependency is not eligible")
        try:
            candidate.resolve(strict=True).relative_to(module_root)
        except ValueError:
            pass
        else:
            raise InventoryError("proved local dependency must be outside the selected module")
        if _is_binary_or_generated(candidate):
            raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: local dependency is binary or generated")
        try:
            contents = candidate.read_bytes()
        except OSError as error:
            raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: local dependency is unreadable") from error
        if b"\0" in contents[:8192] or _secret_rule(candidate, contents, dependency_parts[:-1]) is not None:
            raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: local dependency is unsafe")
        if any(item["project_path"] == lexical_path for item in files):
            raise InventoryError("proved local dependency duplicates an inventory input")
        files.append(file_entry(lexical_path, candidate, contents))
    # A nested module executes under its own root, but its parent build manifests
    # can alter resolution and therefore belong to the same declared baseline.
    ancestor = module_root.parent
    while ancestor != project_root.parent:
        for manifest_name in [".skillsrc", *sorted(_PARENT_BUILD_PATHS)]:
            path = ancestor / manifest_name
            if not path.is_file() or _is_reparse(path):
                continue
            project_path = _safe_rel(project_root, path)
            if project_path is None or any(item["project_path"] == project_path for item in files):
                continue
            try:
                contents = path.read_bytes()
            except OSError as error:
                raise InventoryError(f"required parent configuration is unreadable: {project_path}") from error
            secret = _secret_rule(path, contents, Path(project_path).parts[:-1])
            if secret is not None:
                rule, label = secret
                exclusions.append(_exclusion(project_path, "SECRET_SUSPECTED", rule, label))
                continue
            files.append(file_entry(project_path, path, contents, kind="config"))
        if ancestor == project_root:
            break
        ancestor = ancestor.parent
    files.sort(key=lambda item: item["project_path"])
    exclusions.sort(key=lambda item: (item["project_path"], item["reason_code"], item["scanner_rule_id"]))
    receipt: dict[str, Any] = {
        "schema_version": "1.0.0", "project": project_root.name, "project_identity": project_identity, "module": module,
        "files": files, "exclusions": exclusions,
    }
    receipt["digest"] = _digest(receipt)
    _validate_receipt_schema(receipt, "inventory-receipt.schema.json", InventoryError)
    _validate_receipt_schema(build_exclusion_receipt(receipt), "exclusion-receipt.schema.json", InventoryError)
    return receipt


def _inventory_index(inventory: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    expected = _digest({key: value for key, value in inventory.items() if key != "digest"})
    if inventory.get("schema_version") != "1.0.0" or inventory.get("digest") != expected:
        raise InventoryError("inventory readback is invalid")
    files = inventory.get("files")
    if not isinstance(files, list):
        raise InventoryError("inventory files are invalid")
    index = {item.get("opaque_id"): item for item in files if isinstance(item, Mapping) and isinstance(item.get("opaque_id"), str)}
    if len(index) != len(files):
        raise InventoryError("inventory opaque IDs are invalid")
    return index


def _require_file_ids(inventory: Mapping[str, Any], file_ids: Iterable[str]) -> list[Mapping[str, Any]]:
    index = _inventory_index(inventory)
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for opaque_id in file_ids:
        if not isinstance(opaque_id, str) or opaque_id in seen or opaque_id not in index:
            raise InventoryError("baseline requires eligible opaque file IDs")
        seen.add(opaque_id)
        result.append(index[opaque_id])
    return result


def _secret_exclusion_is_execution_significant(item: Mapping[str, Any], module: str) -> bool:
    """Recognize only the closed paths whose bytes can deterministically affect execution."""
    raw_path = item.get("project_path")
    if item.get("reason_code") != "SECRET_SUSPECTED" or not isinstance(raw_path, str):
        return False
    path = Path(raw_path)
    name = path.name.casefold()
    normalized = raw_path.replace("\\", "/").casefold()
    if name in _CLOSED_MANIFESTS or any(
        normalized == candidate.casefold() or normalized.endswith("/" + candidate.casefold())
        for candidate in _PARENT_BUILD_PATHS
    ):
        return True
    module_path = Path() if module == "." else Path(module)
    try:
        module_relative = path.relative_to(module_path)
    except ValueError:
        return False
    relative_parts = {part.casefold() for part in module_relative.parts[:-1]}
    if name.startswith(".env"):
        return bool(relative_parts & {"test", "tests", "fixture", "fixtures", "resource", "resources"})
    return (
        path.suffix.casefold() in _EXECUTION_SOURCE_SUFFIXES | _EXECUTION_CONFIG_SUFFIXES
        or bool(relative_parts & {"test", "tests", "fixture", "fixtures", "resource", "resources"})
    )


def _validate_adapter_parameters(value: Any) -> None:
    """Phase 2 has no typed adapter-key catalogue, so it accepts no parameters."""
    if not isinstance(value, Mapping) or dict(value):
        raise InventoryError("adapter parameters must be the closed empty object until Phase 6")


def _selected_test_root(project_root: Path, inventory: Mapping[str, Any], skillsrc_path: Path) -> str:
    """Derive the one materialization root from the authoritative selected module."""
    try:
        from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc

        modules = normalize_skillsrc(load_skillsrc(skillsrc_path))["modules"]
    except (OSError, SkillsrcError, KeyError, TypeError, ValueError) as error:
        raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: authoritative .skillsrc is invalid") from error
    selected = [item for item in modules if isinstance(item, Mapping) and item.get("root") == inventory.get("module")]
    if len(selected) != 1:
        raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: selected module is ambiguous in .skillsrc")
    paths = selected[0].get("paths")
    roots = paths.get("tests") if isinstance(paths, Mapping) else None
    if not isinstance(roots, list) or len(roots) != 1 or not isinstance(roots[0], str):
        raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: selected module requires one test root")
    test_root = roots[0]
    raw = Path(test_root)
    if raw.is_absolute() or "\\" in test_root or not test_root or any(part in {"", ".", ".."} for part in raw.parts):
        raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: selected test root is unsafe")
    try:
        (project_root / Path(str(inventory["module"]))).resolve().relative_to(project_root.resolve())
    except ValueError as error:
        raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: selected module escapes project") from error
    return test_root


def _verify_inventory_root(project_root: Path, inventory: Mapping[str, Any]) -> None:
    """Bind the controller root to every frozen eligible inventory byte sequence."""
    files = inventory.get("files")
    if not isinstance(files, list) or not files:
        raise InventoryError("inventory has no eligible files")
    for item in files:
        if not isinstance(item, Mapping) or not isinstance(item.get("project_path"), str):
            raise InventoryError("inventory file identity is invalid")
        relative = Path(item["project_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise InventoryError("inventory file path is unsafe")
        candidate = project_root / relative
        try:
            candidate.resolve().relative_to(project_root)
        except ValueError as error:
            raise InventoryError("inventory file escapes project root") from error
        if _is_reparse(candidate) or not candidate.is_file() or _content_digest(candidate) != item.get("content_digest"):
            raise InventoryError("project root does not match frozen inventory")


def build_execution_baseline(
    inventory: Mapping[str, Any], *, project_root: Path, requirements: Mapping[str, str], skillsrc_file_id: str,
    skillsrc_authority: Mapping[str, Any],
    execution_file_ids: Iterable[str], parent_build_file_ids: Iterable[str], interpreter_path: str | None = None,
    wrapper_path: str | None = None, adapter_id: str | None = None, build_profile: str | None = None, adapter_parameters: Mapping[str, Any] | None = None,
    interpreter_identity: str | None = None, wrapper_identity: str | None = None,
    proved_dependency_file_ids: Iterable[str] = (), policy_profile: str = "local-pilot-v1",
    executable_path: str | None = None, executable_identity: str | None = None,
) -> dict[str, Any]:
    """Freeze controller-declared profile-applicable inputs; context is intentionally absent."""
    if policy_profile not in {"cases-only-v1", "local-pilot-v1"}:
        raise InventoryError("baseline policy profile is invalid")
    if policy_profile == "local-pilot-v1" and any(
        isinstance(item, Mapping) and _secret_exclusion_is_execution_significant(item, str(inventory.get("module", ".")))
        for item in inventory.get("exclusions", [])
    ):
        raise InventoryError("NOT_RUNNABLE/BASELINE_INCOMPLETE: secret-suspected execution input requires a safe substitute")
    if not isinstance(requirements.get("requirement_id"), str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", requirements["requirement_id"]):
        raise InventoryError("requirements identity is unsafe")
    if not isinstance(requirements.get("digest"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", requirements["digest"]):
        raise InventoryError("requirements digest is invalid")
    if policy_profile == "cases-only-v1":
        if any(value is not None for value in (adapter_id, build_profile, adapter_parameters, interpreter_path, interpreter_identity, wrapper_path, wrapper_identity, executable_path, executable_identity)):
            raise InventoryError("cases-only baseline forbids execution facts")
    else:
        if not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,256}", value) for value in (adapter_id, build_profile)):
            raise InventoryError("execution identity is unsafe")
        if adapter_id not in _CLOSED_ADAPTER_IDS:
            raise InventoryError("adapter ID is not closed")
        if wrapper_path is not None and (not isinstance(wrapper_path, str) or not re.fullmatch(r"[A-Za-z0-9._/-]{1,256}", wrapper_path)):
            raise InventoryError("wrapper path is unsafe")
        if adapter_id == "maven:selected-symbols-v1":
            if not isinstance(executable_path, str) or str(system_maven_path(executable_path)) != executable_path:
                raise InventoryError("system Maven baseline requires the resolved absolute launcher")
            if not isinstance(executable_identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", executable_identity):
                raise InventoryError("system Maven requires an executable identity")
            if any(value is not None for value in (interpreter_path, interpreter_identity, wrapper_path, wrapper_identity)):
                raise InventoryError("system Maven forbids module-local runtime facts")
        elif adapter_id == "pytest:selected-symbols-v1":
            if not isinstance(interpreter_path, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,256}", interpreter_path):
                raise InventoryError("pytest requires an interpreter path")
            if not isinstance(interpreter_identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", interpreter_identity):
                raise InventoryError("pytest requires an interpreter identity")
            if wrapper_path is not None or wrapper_identity is not None:
                raise InventoryError("pytest forbids a wrapper identity")
        else:
            if wrapper_path is None or not isinstance(wrapper_identity, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", wrapper_identity):
                raise InventoryError("wrapper adapters require a wrapper identity")
            if interpreter_path is not None or interpreter_identity is not None:
                raise InventoryError("wrapper adapters forbid an interpreter identity")
            expected_wrappers = {
                "maven-wrapper:selected-symbols-v1": {"mvnw", "mvnw.cmd"},
                "gradle-wrapper:selected-symbols-v1": {"gradlew", "gradlew.bat"},
            }
            if Path(wrapper_path).name.casefold() not in expected_wrappers[adapter_id]:
                raise InventoryError("wrapper path must name the module-local closed wrapper")
        if adapter_id != "maven:selected-symbols-v1" and (executable_path is not None or executable_identity is not None):
            raise InventoryError("module-local adapters forbid a system executable identity")
    skillsrc_item = _require_file_ids(inventory, [skillsrc_file_id])[0]
    if Path(str(skillsrc_item["project_path"])).name != ".skillsrc":
        raise InventoryError("skillsrc file identity must bind an exact .skillsrc")
    authority = dict(skillsrc_authority)
    if authority.get("digest") != _digest({key: value for key, value in authority.items() if key != "digest"}) or authority.get("skillsrc_digest") != skillsrc_item.get("content_digest"):
        raise InventoryError("skillsrc authority receipt is invalid")
    project_root = Path(project_root).resolve()
    if project_root.name != inventory.get("project"):
        raise InventoryError("project root does not match inventory identity")
    _verify_inventory_root(project_root, inventory)
    test_root = _selected_test_root(project_root, inventory, project_root / Path(str(skillsrc_item["project_path"]))) if policy_profile == "local-pilot-v1" else None
    execution_items = _require_file_ids(inventory, execution_file_ids)
    parent_items = _require_file_ids(inventory, parent_build_file_ids)
    dependency_items = _require_file_ids(inventory, proved_dependency_file_ids)
    module_prefix = "" if inventory["module"] == "." else str(inventory["module"]) + "/"
    for item in parent_items:
        if item["project_path"].startswith(module_prefix) or Path(str(item["project_path"])).name.casefold() not in _CLOSED_MANIFESTS:
            raise InventoryError("parent build inputs must be parent build manifests")
    selected = [skillsrc_item, *execution_items, *parent_items, *dependency_items]
    inputs = [
        {"opaque_id": item["opaque_id"], "project_path": item["project_path"], "content_digest": item["content_digest"]}
        for item in selected
    ]
    if len({item["opaque_id"] for item in inputs}) != len(inputs):
        raise InventoryError("baseline inputs must be unique")
    if {item["opaque_id"] for item in inputs} != set(_inventory_index(inventory)):
        raise InventoryError("execution baseline must cover every eligible inventory input")
    if policy_profile == "local-pilot-v1":
        _validate_adapter_parameters(adapter_parameters)
    baseline: dict[str, Any] = {
        "schema_version": "1.0.0", "policy_profile": policy_profile, "project": inventory["project"], "project_identity": inventory["project_identity"], "module": inventory["module"],
        "inventory_digest": inventory["digest"],
        "requirements": {"requirement_id": requirements["requirement_id"], "digest": requirements["digest"]},
        "skillsrc_file_id": skillsrc_file_id, "skillsrc_authority": authority, "inputs": inputs,
    }
    if policy_profile == "local-pilot-v1":
        baseline.update(test_root=test_root, adapter_id=adapter_id, build_profile=build_profile, adapter_parameters=dict(adapter_parameters))
        if adapter_id == "pytest:selected-symbols-v1":
            baseline["interpreter_path"] = interpreter_path
            baseline["interpreter_identity"] = interpreter_identity
        elif adapter_id == "maven:selected-symbols-v1":
            baseline["executable_path"] = executable_path
            baseline["executable_identity"] = executable_identity
        else:
            baseline["wrapper_path"] = wrapper_path
            baseline["wrapper_identity"] = wrapper_identity
    baseline["digest"] = _digest(baseline)
    _validate_receipt_schema(baseline, "execution-baseline.schema.json", InventoryError)
    return baseline


def _validate_baseline_shape(baseline: Mapping[str, Any]) -> None:
    expected = _digest({key: value for key, value in baseline.items() if key != "digest"})
    if baseline.get("schema_version") != "1.0.0" or baseline.get("digest") != expected:
        raise InventoryError("execution baseline readback is invalid")


def validate_execution_baseline_binding(
    baseline: Mapping[str, Any], inventory: Mapping[str, Any], module_root: Path, *, policy_profile: str = "local-pilot-v1",
) -> None:
    """Prove one frozen baseline is complete, inventory-bound, and executable as declared."""
    _validate_baseline_shape(baseline)
    _validate_receipt_schema(baseline, "execution-baseline.schema.json", InventoryError)
    if baseline.get("policy_profile") != policy_profile:
        raise InventoryError("baseline policy profile does not match the authorized run")
    index = _inventory_index(inventory)
    if (
        baseline.get("project") != inventory.get("project")
        or baseline.get("project_identity") != inventory.get("project_identity")
        or baseline.get("module") != inventory.get("module")
        or baseline.get("inventory_digest") != inventory.get("digest")
    ):
        raise InventoryError("execution baseline inventory binding is invalid")
    inputs = baseline.get("inputs")
    if not isinstance(inputs, list) or {item.get("opaque_id") for item in inputs if isinstance(item, Mapping)} != set(index):
        raise InventoryError("execution baseline must cover every eligible inventory input")
    for item in inputs:
        if not isinstance(item, Mapping):
            raise InventoryError("execution baseline input is invalid")
        current = index.get(item.get("opaque_id"))
        if current is None or item.get("project_path") != current.get("project_path") or item.get("content_digest") != current.get("content_digest"):
            raise InventoryError("execution baseline input binding is invalid")
    skillsrc = index.get(baseline.get("skillsrc_file_id"))
    if skillsrc is None or Path(str(skillsrc.get("project_path"))).name != ".skillsrc":
        raise InventoryError("execution baseline skillsrc binding is invalid")
    authority = baseline.get("skillsrc_authority")
    if not isinstance(authority, Mapping) or authority.get("digest") != _digest({key: value for key, value in authority.items() if key != "digest"}) or authority.get("skillsrc_digest") != skillsrc.get("content_digest"):
        raise InventoryError("execution baseline skillsrc authority is invalid")
    project_root = Path(module_root).resolve()
    if inventory.get("module") != ".":
        for _part in Path(str(inventory["module"])).parts:
            project_root = project_root.parent
    skillsrc_path = project_root / Path(str(skillsrc["project_path"]))
    try:
        from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, normalize_skillsrc

        document = normalize_skillsrc(load_skillsrc(skillsrc_path))
    except (OSError, SkillsrcError, ValueError) as error:
        raise InventoryError("execution baseline skillsrc is not valid authoritative configuration") from error
    if policy_profile == "cases-only-v1":
        return
    if baseline.get("test_root") != _selected_test_root(project_root, inventory, skillsrc_path):
        raise InventoryError("execution baseline test root binding is invalid")
    _validate_adapter_parameters(baseline.get("adapter_parameters"))
    runtime_path = baseline.get("interpreter_path") or baseline.get("wrapper_path") or baseline.get("executable_path")
    if baseline.get("adapter_id") == "maven:selected-symbols-v1":
        selected = [item for item in document["modules"] if item.get("root") == inventory.get("module")]
        test = selected[0].get("test") if len(selected) == 1 else None
        if not isinstance(test, Mapping) or test.get("adapter_id") != baseline["adapter_id"] or str(system_maven_path(test.get("executable"))) != runtime_path:
            raise InventoryError("execution baseline system Maven path binding is invalid")
    identity_key = "interpreter_identity" if baseline.get("adapter_id") == "pytest:selected-symbols-v1" else "executable_identity" if baseline.get("adapter_id") == "maven:selected-symbols-v1" else "wrapper_identity"
    if not isinstance(runtime_path, str) or runtime_identity(_runtime_base(module_root, document, inventory), runtime_path, adapter_id=baseline.get("adapter_id")) != baseline.get(identity_key):
        raise InventoryError("execution baseline runtime binding is invalid")


def _runtime_base(module_root: Path, document: Mapping[str, Any], inventory: Mapping[str, Any]) -> Path:
    """Directory a module-local runtime path is relative to.

    A wrapper of a multi-module build lives in the build root declared by
    ``test.build_root``; without that field it is the module root as before.
    """
    selected = [item for item in document["modules"] if item.get("root") == inventory.get("module")]
    test = selected[0].get("test") if len(selected) == 1 else None
    if not isinstance(test, Mapping) or "build_root" not in test:
        return Path(module_root)
    from tools.execution_adapters import AdapterRequestError, launch_layout

    try:
        return launch_layout({**selected[0], "module_root": str(module_root)}).build_root
    except AdapterRequestError as error:
        raise InventoryError("execution baseline build root binding is invalid") from error


def _safe_immutable_target(path: Path, error_type: type[ValueError]) -> Path:
    """Reject link/reparse parent chains before publishing an immutable artifact."""
    target = Path(path).absolute()
    current = target.parent
    while True:
        if (current.exists() or current.is_symlink()) and _is_reparse(current):
            raise error_type("immutable receipt parent is a symlink or reparse point")
        if current == current.parent:
            break
        current = current.parent
    if (target.exists() or target.is_symlink()) and _is_reparse(target):
        raise error_type("immutable receipt target is a symlink or reparse point")
    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_reparse(target.parent):
        raise error_type("immutable receipt parent is a symlink or reparse point")
    return target


def _freeze_json_receipt(path: Path, receipt: Mapping[str, Any], error_type: type[ValueError]) -> dict[str, Any]:
    """Create once, cleanup a partial artifact, and prove exact JSON readback."""
    target = _safe_immutable_target(Path(path), error_type)
    payload = _canonical_bytes(dict(receipt))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as error:
        raise error_type("immutable receipt already exists") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        raw = target.read_bytes()
        readback = loads_json_strict(raw.decode("utf-8"))
        if raw != payload or readback != dict(receipt):
            raise error_type("immutable receipt readback is invalid")
        return readback
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise


def freeze_inventory_receipt(path: Path, inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Persist the controller-owned metadata inventory as an immutable receipt."""
    _inventory_index(inventory)
    _validate_receipt_schema(inventory, "inventory-receipt.schema.json", InventoryError)
    readback = _freeze_json_receipt(path, inventory, InventoryError)
    _validate_receipt_schema(readback, "inventory-receipt.schema.json", InventoryError)
    return readback


def read_inventory_receipt(path: Path) -> dict[str, Any]:
    target = Path(path)
    if _is_reparse(target) or _is_reparse(target.parent):
        raise InventoryError("inventory receipt target is a symlink or reparse point")
    try:
        raw = target.read_bytes()
        receipt = loads_json_strict(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise InventoryError("inventory receipt is unreadable") from error
    if not isinstance(receipt, dict):
        raise InventoryError("inventory receipt is invalid")
    if raw != _canonical_bytes(receipt):
        raise InventoryError("inventory receipt bytes are not canonical")
    _inventory_index(receipt)
    _validate_receipt_schema(receipt, "inventory-receipt.schema.json", InventoryError)
    return receipt


def build_exclusion_receipt(inventory: Mapping[str, Any]) -> dict[str, Any]:
    """Project safe exclusions into a separate immutable-artifact payload."""
    _inventory_index(inventory)
    exclusions = inventory.get("exclusions")
    if not isinstance(exclusions, list) or not all(isinstance(item, Mapping) and set(item) == {"project_path", "reason_code", "scanner_rule_id", "safe_label"} for item in exclusions):
        raise InventoryError("inventory exclusions are invalid")
    receipt: dict[str, Any] = {"schema_version": "1.0.0", "exclusions": [dict(item) for item in exclusions]}
    receipt["digest"] = _digest(receipt)
    _validate_receipt_schema(receipt, "exclusion-receipt.schema.json", InventoryError)
    return receipt


def _validate_exclusion_receipt(receipt: Mapping[str, Any]) -> None:
    if receipt.get("schema_version") != "1.0.0" or receipt.get("digest") != _digest({key: value for key, value in receipt.items() if key != "digest"}):
        raise InventoryError("exclusion receipt is invalid")
    exclusions = receipt.get("exclusions")
    if not isinstance(exclusions, list) or not all(isinstance(item, Mapping) and set(item) == {"project_path", "reason_code", "scanner_rule_id", "safe_label"} for item in exclusions):
        raise InventoryError("exclusion receipt is invalid")


def freeze_exclusion_receipt(path: Path, receipt: Mapping[str, Any]) -> dict[str, Any]:
    _validate_exclusion_receipt(receipt)
    _validate_receipt_schema(receipt, "exclusion-receipt.schema.json", InventoryError)
    readback = _freeze_json_receipt(path, receipt, InventoryError)
    _validate_receipt_schema(readback, "exclusion-receipt.schema.json", InventoryError)
    return readback


def read_exclusion_receipt(path: Path) -> dict[str, Any]:
    target = Path(path)
    if _is_reparse(target) or _is_reparse(target.parent):
        raise InventoryError("exclusion receipt target is a symlink or reparse point")
    try:
        raw = target.read_bytes()
        receipt = loads_json_strict(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise InventoryError("exclusion receipt is unreadable") from error
    if not isinstance(receipt, dict):
        raise InventoryError("exclusion receipt is invalid")
    if raw != _canonical_bytes(receipt):
        raise InventoryError("exclusion receipt bytes are not canonical")
    _validate_exclusion_receipt(receipt)
    _validate_receipt_schema(receipt, "exclusion-receipt.schema.json", InventoryError)
    return receipt


def freeze_execution_baseline(path: Path, baseline: Mapping[str, Any]) -> dict[str, Any]:
    """Create a baseline once, then read back identical canonical bytes."""
    _validate_baseline_shape(baseline)
    _validate_receipt_schema(baseline, "execution-baseline.schema.json", InventoryError)
    readback = _freeze_json_receipt(path, baseline, InventoryError)
    _validate_baseline_shape(readback)
    _validate_receipt_schema(readback, "execution-baseline.schema.json", InventoryError)
    return readback


def read_execution_baseline(path: Path) -> dict[str, Any]:
    target = Path(path)
    if _is_reparse(target) or _is_reparse(target.parent):
        raise InventoryError("execution baseline target is a symlink or reparse point")
    try:
        raw = target.read_bytes()
        value = loads_json_strict(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise InventoryError("execution baseline is unreadable") from error
    if not isinstance(value, dict):
        raise InventoryError("execution baseline is invalid")
    if raw != _canonical_bytes(value):
        raise InventoryError("execution baseline bytes are not canonical")
    _validate_baseline_shape(value)
    _validate_receipt_schema(value, "execution-baseline.schema.json", InventoryError)
    return value


def validate_execution_baseline(
    baseline: Mapping[str, Any], inventory: Mapping[str, Any], *, late_dependency_file_ids: Iterable[str] = (),
    requirements: Mapping[str, str] | None = None, adapter_id: str | None = None, build_profile: str | None = None,
    adapter_parameters: Mapping[str, Any] | None = None, runtime_identity_value: str | None = None,
    policy_profile: str = "local-pilot-v1",
) -> dict[str, Any]:
    """Validate a frozen baseline without adding new inputs to it."""
    _validate_baseline_shape(baseline)
    if inventory.get("project") != baseline.get("project") or inventory.get("project_identity") != baseline.get("project_identity") or inventory.get("module") != baseline.get("module"):
        return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    if requirements is not None and dict(requirements) != baseline.get("requirements"):
        return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    if adapter_id is not None and adapter_id != baseline.get("adapter_id"):
        return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    if build_profile is not None and build_profile != baseline.get("build_profile"):
        return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    if adapter_parameters is not None and dict(adapter_parameters) != baseline.get("adapter_parameters"):
        return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    if baseline.get("policy_profile") != policy_profile:
        return {"status": "NOT_RUNNABLE", "reason_code": "BASELINE_INCOMPLETE", "requires_child_attempt": True}
    if policy_profile == "local-pilot-v1":
        identity_key = "interpreter_identity" if baseline.get("adapter_id") == "pytest:selected-symbols-v1" else "executable_identity" if baseline.get("adapter_id") == "maven:selected-symbols-v1" else "wrapper_identity"
        if runtime_identity_value is None:
            return {"status": "NOT_RUNNABLE", "reason_code": "BASELINE_INCOMPLETE", "requires_child_attempt": True}
        if runtime_identity_value != baseline.get(identity_key):
            return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    late_ids = list(late_dependency_file_ids)
    if late_ids:
        try:
            _require_file_ids(inventory, late_ids)
        except InventoryError:
            pass
        return {"status": "NOT_RUNNABLE", "reason_code": "BASELINE_INCOMPLETE", "requires_child_attempt": True}
    index = _inventory_index(inventory)
    if {item.get("opaque_id") for item in baseline.get("inputs", [])} != set(index):
        return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    for item in baseline.get("inputs", []):
        current = index.get(item.get("opaque_id"))
        if current is None or current.get("content_digest") != item.get("content_digest"):
            return {"status": "DECLARED_INPUT_DRIFT", "requires_child_attempt": True}
    return {"status": "UNCHANGED", "requires_child_attempt": False}


def baseline_external_input_paths(project_root: Path, baseline: Mapping[str, Any]) -> list[Path]:
    """Recover controller-declared parent/dependency files needed to rebuild an inventory."""
    module = baseline.get("module")
    inputs = baseline.get("inputs")
    if not isinstance(module, str) or not isinstance(inputs, list):
        raise InventoryError("execution baseline external inputs are invalid")
    prefix = "" if module == "." else module.rstrip("/") + "/"
    result: list[Path] = []
    for item in inputs:
        if not isinstance(item, Mapping) or not isinstance(item.get("project_path"), str):
            raise InventoryError("execution baseline external inputs are invalid")
        project_path = str(item["project_path"])
        if Path(project_path).name == ".skillsrc" or not prefix or project_path.startswith(prefix):
            continue
        result.append(Path(project_root) / Path(project_path))
    return result


def context_limits(skillsrc_document: Mapping[str, Any] | None) -> dict[str, int]:
    """Resolve context limits from an optional ``.skillsrc`` ``limits`` object.

    Manifests written before the field existed carry no ``limits`` and get the defaults.
    """
    limits = dict(DEFAULT_CONTEXT_LIMITS)
    declared = skillsrc_document.get("limits") if isinstance(skillsrc_document, Mapping) else None
    if isinstance(declared, Mapping):
        for key in limits:
            value = declared.get(key)
            if type(value) is int and value > 0:
                limits[key] = value
    return limits


def _context_receipt(
    inventory: Mapping[str, Any], batch_index: int, files: list[dict[str, Any]], byte_count: int, byte_set: bytes,
    *, gaps: list[dict[str, Any]] | None = None, byte_budget: int | None = None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema_version": "1.0.0", "inventory_digest": inventory["digest"], "batch_index": batch_index,
        "files": [{key: item[key] for key in ("opaque_id", "project_path", "content_digest", "size")} for item in files],
        "byte_count": byte_count, "byte_set_digest": "sha256:" + hashlib.sha256(byte_set).hexdigest(),
    }
    if gaps:
        # Additive fields: present only when a file was skipped, so receipts for
        # selections without gaps keep their previous bytes and digests.
        receipt["byte_budget"] = byte_budget
        receipt["gaps"] = gaps
    receipt["digest"] = _digest(receipt)
    _validate_receipt_schema(receipt, "context-selection-receipt.schema.json", ContextSelectionError)
    return receipt


def _delivered_bytes(item: Mapping[str, Any], contents: bytes) -> bytes:
    """Return the bytes a model may see: documents with redactions are masked."""
    declared = item.get("redactions")
    if not declared:
        return contents
    masked, redactions = redact_document(contents)
    if redactions != declared:
        raise ContextSelectionError("context redaction receipt does not match file bytes")
    return masked


def select_context_batches(
    inventory: Mapping[str, Any], project_root: Path, opaque_file_ids: Iterable[str], *, byte_budget: int,
    include_closed_manifests: bool = True,
) -> list[dict[str, Any]]:
    """Read verified eligible files into bounded, non-truncated C-lite batches.

    A file larger than the budget is never truncated and never fails the whole
    selection: it is skipped and recorded as an explicit gap in the first receipt.
    """
    if type(byte_budget) is not int or byte_budget <= 0:
        raise ContextSelectionError("byte budget must be a positive integer")
    project_root = Path(project_root).resolve()
    index = _inventory_index(inventory)
    if inventory.get("project_identity") != _project_identity(project_root):
        raise ContextSelectionError("context inventory ownership failed")
    requested = list(opaque_file_ids)
    if include_closed_manifests:
        requested.extend(
            item["opaque_id"]
            for item in index.values()
            if Path(item["project_path"]).name.casefold() == ".skillsrc"
            or Path(item["project_path"]).name.casefold() in _CLOSED_MANIFESTS
        )
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for opaque_id in requested:
        item = index.get(opaque_id) if isinstance(opaque_id, str) else None
        if item is None or opaque_id in seen:
            if item is None:
                raise ContextSelectionError("context requires an eligible opaque file ID")
            continue
        seen.add(opaque_id)
        selected.append(item)
    selected.sort(key=lambda item: item["project_path"])
    groups: list[tuple[list[dict[str, Any]], int, bytes]] = []
    gaps: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_count = 0
    current_bytes = bytearray()
    for item in selected:
        candidate = project_root / Path(item["project_path"])
        try:
            resolved = candidate.resolve()
            resolved.relative_to(project_root)
        except (OSError, ValueError) as error:
            raise ContextSelectionError("context path confinement failed") from error
        if _is_reparse(candidate) or not candidate.is_file():
            raise ContextSelectionError("context path reparse status changed")
        try:
            contents = candidate.read_bytes()
        except OSError as error:
            raise ContextSelectionError("context bytes are unreadable") from error
        if "sha256:" + hashlib.sha256(contents).hexdigest() != item["content_digest"]:
            raise ContextSelectionError("context digest drift")
        contents = _delivered_bytes(item, contents)
        if len(contents) > byte_budget:
            gaps.append({
                "opaque_id": item["opaque_id"], "project_path": item["project_path"],
                "size": item["size"], "reason_code": _CONTEXT_GAP_REASON,
            })
            continue
        prepared = {**dict(item), "bytes": contents}
        if current and current_count + len(contents) > byte_budget:
            groups.append((current, current_count, bytes(current_bytes)))
            current, current_count, current_bytes = [], 0, bytearray()
        current.append(prepared)
        current_count += len(contents)
        current_bytes.extend(item["opaque_id"].encode("ascii") + b"\0" + contents)
    if current or (gaps and not groups):
        groups.append((current, current_count, bytes(current_bytes)))
    batches: list[dict[str, Any]] = []
    for index_number, (files, byte_count, byte_set) in enumerate(groups, start=1):
        receipt = _context_receipt(
            inventory, index_number, files, byte_count, byte_set,
            gaps=gaps if index_number == 1 else None, byte_budget=byte_budget,
        )
        batches.append({"files": files, "byte_count": byte_count, "receipt": receipt})
    return batches


def freeze_context_receipt(path: Path, receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Persist exactly one immutable safe receipt for an already selected byte set."""
    expected = _digest({key: value for key, value in receipt.items() if key != "digest"})
    if receipt.get("schema_version") != "1.0.0" or receipt.get("digest") != expected:
        raise ContextSelectionError("context receipt is invalid")
    _validate_receipt_schema(receipt, "context-selection-receipt.schema.json", ContextSelectionError)
    target = _safe_immutable_target(Path(path), ContextSelectionError)
    payload = _canonical_bytes(dict(receipt))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(target, flags, 0o600)
    except FileExistsError as error:
        raise ContextSelectionError("context receipt is immutable") from error
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        raw = target.read_bytes()
        readback = loads_json_strict(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, StrictJsonError) as error:
        raise ContextSelectionError("context receipt readback failed") from error
    except BaseException:
        try:
            target.unlink()
        except OSError:
            pass
        raise
    if raw != payload or readback != dict(receipt) or readback.get("digest") != expected:
        try:
            target.unlink()
        except OSError:
            pass
        raise ContextSelectionError("context receipt readback is invalid")
    _validate_receipt_schema(readback, "context-selection-receipt.schema.json", ContextSelectionError)
    return readback


def validate_context_receipt_binding(inventory: Mapping[str, Any], project_root: Path, receipt: Mapping[str, Any]) -> None:
    """Recompute an exact C-lite byte-set receipt from frozen inventory and live bytes."""
    value = dict(receipt)
    expected = _digest({key: item for key, item in value.items() if key != "digest"})
    if value.get("digest") != expected or value.get("inventory_digest") != inventory.get("digest"):
        raise ContextSelectionError("context receipt inventory binding is invalid")
    _validate_receipt_schema(value, "context-selection-receipt.schema.json", ContextSelectionError)
    index = _inventory_index(inventory)
    files = value.get("files")
    gaps = value.get("gaps", [])
    if not isinstance(files, list) or not isinstance(gaps, list) or not (files or gaps):
        raise ContextSelectionError("context receipt must bind at least one eligible file")
    byte_budget = value.get("byte_budget")
    if ("gaps" in value) != ("byte_budget" in value) or ("gaps" in value and (not gaps or type(byte_budget) is not int or byte_budget <= 0)):
        raise ContextSelectionError("context receipt gaps are invalid")
    listed = {row.get("opaque_id") for row in files if isinstance(row, Mapping)}
    gap_ids: set[str] = set()
    for gap in gaps:
        opaque_id = gap.get("opaque_id") if isinstance(gap, Mapping) else None
        item = index.get(opaque_id)
        if (
            item is None or opaque_id in gap_ids or opaque_id in listed
            or gap.get("project_path") != item.get("project_path") or gap.get("size") != item.get("size")
            or gap.get("reason_code") != _CONTEXT_GAP_REASON
            # Masking only ever replaces a line, so an unmasked size at or below the
            # budget can be a real gap only for a document that carries redactions.
            or (item["size"] <= byte_budget and not item.get("redactions"))
        ):
            raise ContextSelectionError("context receipt gap ownership is invalid")
        gap_ids.add(opaque_id)
    byte_set = bytearray()
    byte_count = 0
    seen: set[str] = set()
    project = Path(project_root).resolve()
    for row in files:
        opaque_id = row.get("opaque_id") if isinstance(row, Mapping) else None
        item = index.get(opaque_id)
        if item is None or opaque_id in seen or any(row.get(key) != item.get(key) for key in ("project_path", "content_digest", "size")):
            raise ContextSelectionError("context receipt file ownership is invalid")
        seen.add(opaque_id)
        candidate = project / Path(str(item["project_path"]))
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(project)
            contents = candidate.read_bytes()
        except (OSError, ValueError) as error:
            raise ContextSelectionError("context receipt file readback failed") from error
        if _is_reparse(candidate) or "sha256:" + hashlib.sha256(contents).hexdigest() != item["content_digest"] or len(contents) != item["size"]:
            raise ContextSelectionError("context receipt file bytes drifted")
        contents = _delivered_bytes(item, contents)
        byte_count += len(contents)
        byte_set.extend(str(opaque_id).encode("ascii") + b"\0" + contents)
    if value.get("byte_count") != byte_count or value.get("byte_set_digest") != "sha256:" + hashlib.sha256(bytes(byte_set)).hexdigest():
        raise ContextSelectionError("context receipt byte set is invalid")
