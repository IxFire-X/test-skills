"""Human gates of a run (security fixes S3): what a person approves before the package acts on the project.

S3 — ``execution.review``: after the generated tests are written into the project and before any build, test or
mutation run, the driver stops with ``ask_user approve-generated-tests`` listing the exact files and their digests;
the answer is the digest of that list.  ``human`` is the default; ``none`` (``.skillsrc`` or ``--execution-review
none``) turns the stop off, for example for repeated runs on a known project.

S5 — ``context.confirm``: after scan and before the first model call the driver stops with ``ask_user
confirm-context`` listing every selected project file and its size; the answer is the digest of that list.  On by
default; ``context.confirm: false`` or ``--confirm-context no`` turns it off.

S9 — ``execution.mode: human``: the package starts no build, test or mutation process; the driver prints the exact
closed commands and a person runs them and hands the reports back (``next --junit``).  ``auto`` is the default.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

EXECUTION_REVIEW = ("human", "none")
DEFAULT_EXECUTION_REVIEW = "human"
CONFIRM_CONTEXT = ("yes", "no")
DEFAULT_CONFIRM_CONTEXT = True
EXECUTION_MODE = ("auto", "human")
DEFAULT_EXECUTION_MODE = "auto"


class GateError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _skillsrc(project: Path) -> Mapping[str, Any]:
    path = Path(project) / ".skillsrc"
    if not path.is_file():
        return {}
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    try:
        value = parse_skillsrc_bytes(path.read_bytes())
    except Exception:  # an unreadable .skillsrc is reported by scan; the safe default applies
        return {}
    return value if isinstance(value, Mapping) else {}


def execution_review(project: Path, flag: str | None = None) -> str:
    """``--execution-review``, else ``.skillsrc`` ``execution.review``, else ``human``."""
    if flag is not None:
        if flag not in EXECUTION_REVIEW:
            raise GateError("DRIVER_INPUT", f"--execution-review must be one of: {', '.join(EXECUTION_REVIEW)}")
        return flag
    section = _skillsrc(project).get("execution")
    value = section.get("review") if isinstance(section, Mapping) else None
    return value if value in EXECUTION_REVIEW else DEFAULT_EXECUTION_REVIEW


def execution_mode(project: Path, flag: str | None = None) -> str:
    """S9: ``--execution-mode auto|human``, else ``.skillsrc`` ``execution.mode``, else ``auto``."""
    if flag is not None:
        if flag not in EXECUTION_MODE:
            raise GateError("DRIVER_INPUT", f"--execution-mode must be one of: {', '.join(EXECUTION_MODE)}")
        return flag
    section = _skillsrc(project).get("execution")
    value = section.get("mode") if isinstance(section, Mapping) else None
    return value if value in EXECUTION_MODE else DEFAULT_EXECUTION_MODE


def confirm_context(project: Path, flag: str | None = None) -> bool:
    """S5: ``--confirm-context yes|no``, else ``.skillsrc`` ``context.confirm``, else on."""
    if flag is not None:
        if flag not in CONFIRM_CONTEXT:
            raise GateError("DRIVER_INPUT", f"--confirm-context must be one of: {', '.join(CONFIRM_CONTEXT)}")
        return flag == "yes"
    section = _skillsrc(project).get("context")
    value = section.get("confirm") if isinstance(section, Mapping) else None
    return value if isinstance(value, bool) else DEFAULT_CONFIRM_CONTEXT


def disabled_by_skillsrc(project: Path, *, review_flag: Any = None, confirm_flag: Any = None, executes: bool = True) -> list[str]:
    """S11 B6: the gates ``.skillsrc`` itself turned off (no flag decided them); named in the result and the driver log.

    A flag is the person's explicit decision for this run and is not reported; ``execution.review`` is a gate only
    where the package runs the generated tests (local-pilot-v1, suite-update-v1).
    """
    data = _skillsrc(project)
    gates = []
    context = data.get("context")
    if confirm_flag is None and isinstance(context, Mapping) and context.get("confirm") is False:
        gates.append("context.confirm")
    execution = data.get("execution")
    if executes and review_flag is None and isinstance(execution, Mapping) and execution.get("review") == "none":
        gates.append("execution.review")
    return sorted(gates)


def context_rows(receipts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every selected file once (path, content digest, size): what the model tasks may carry."""
    rows: dict[str, dict[str, Any]] = {}
    for receipt in receipts:
        for item in receipt["files"]:
            rows.setdefault(str(item["project_path"]), {"path": str(item["project_path"]), "content_digest": str(item["content_digest"]),
                                                        "size": int(item["size"])})
    return [rows[path] for path in sorted(rows)]


def context_digest(rows: Sequence[Mapping[str, Any]], requirements_digest: str) -> str:
    """Digest of what goes to the model: the requirement documents (by the digest of their snapshot) and every context file."""
    value = {"requirements_digest": str(requirements_digest), "files": sorted((dict(row) for row in rows), key=lambda row: row["path"])}
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def file_rows(files: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The approved list: project path and content digest of every file, by path."""
    return sorted(({"path": str(row["path"]), "content_digest": str(row["content_digest"])} for row in files), key=lambda row: row["path"])


def approval_digest(files: Sequence[Mapping[str, Any]]) -> str:
    """Digest of the exact list a person approves; another file or other bytes give another digest."""
    data = json.dumps(file_rows(files), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()
