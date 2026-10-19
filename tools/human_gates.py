"""Human gates of a run (security fixes S3): what a person approves before the package acts on the project.

S3 — ``execution.review``: after the generated tests are written into the project and before any build, test or
mutation run, the driver stops with ``ask_user approve-generated-tests`` listing the exact files and their digests;
the answer is the digest of that list.  ``human`` is the default; ``none`` (``.skillsrc`` or ``--execution-review
none``) turns the stop off, for example for repeated runs on a known project.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

EXECUTION_REVIEW = ("human", "none")
DEFAULT_EXECUTION_REVIEW = "human"


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


def file_rows(files: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The approved list: project path and content digest of every file, by path."""
    return sorted(({"path": str(row["path"]), "content_digest": str(row["content_digest"])} for row in files), key=lambda row: row["path"])


def approval_digest(files: Sequence[Mapping[str, Any]]) -> str:
    """Digest of the exact list a person approves; another file or other bytes give another digest."""
    data = json.dumps(file_rows(files), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()
