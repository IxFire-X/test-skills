"""P8a-2 B: the two places of the package that run ``git`` — the check of ``ready`` and the snapshot of the project's dirty paths.

Moved out of ``ready`` and ``pilot_state`` unchanged.  Only ``local-pilot-v1`` asks for them (through ``execution_gate``): a run of
``cases-only-v1`` never starts ``git`` — ``git status`` can run a clean filter or a hook of the project's own configuration — and a
delivery of cases does not carry this file.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Mapping

from tools.pilot_state import _sealed
from tools.ready import CASES, _row


def check_git(project: Path) -> dict[str, Any]:
    """Exactly the call of the pipeline (pilot_state._project_state_snapshot) when the project has .git."""
    project = Path(project).resolve()
    if not (project / ".git").exists():
        return _row("git", CASES, "OK", "не git-проект: git не нужен")
    try:
        # FaT-SEC1: core.fsmonitor=false — a hook the project's .git/config names is never run
        completed = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", f"safe.directory={project.as_posix()}", "-C", str(project),
                                    "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as error:
        return _row("git", CASES, "STOP", f"в проекте есть .git, но git не запускается: {error}", "установите git и добавьте его в PATH")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or ["код " + str(completed.returncode)]
        return _row("git", CASES, "STOP", f"git status проекта не прошёл: {detail[0]}",
                    f'git -c safe.directory="{project.as_posix()}" -C "{project}" status — исправьте ошибку, которую он покажет')
    return _row("git", CASES, "OK", "git status проекта работает")


def project_state_snapshot(
    project: Path,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Seal non-Git identity or the user-owned dirty paths present at run start."""
    project = Path(project).resolve()
    if not (project / ".git").exists():
        if expected is not None and expected.get("kind") != "GIT_DIRECTORY":
            raise ValueError("project Git state changed")
        return _sealed({"kind": "GIT_DIRECTORY", "path": ".git", "entries": []})

    completed = subprocess.run(
        [
            # FaT-SEC1: core.fsmonitor=false — a hook the project's .git/config names is never run
            "git", "-c", "core.fsmonitor=false", "-c", f"safe.directory={project.as_posix()}", "-C", str(project),
            "status", "--porcelain=v1", "-z", "--untracked-files=all",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError("project Git state is unavailable")
    tokens = completed.stdout.split(b"\0")
    status_by_path: dict[str, str] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        if len(token) < 4 or token[2:3] != b" ":
            raise ValueError("project Git status is invalid")
        try:
            status = token[:2].decode("ascii")
            relative = token[3:].decode("utf-8").replace("\\", "/")
        except UnicodeDecodeError as error:
            raise ValueError("project Git status is invalid") from error
        if "R" in status or "C" in status:
            index += 1
        if relative == ".pilot-runs" or relative.startswith(".pilot-runs/"):
            continue
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or "\n" in relative or "\r" in relative:
            raise ValueError("project Git status path is unsafe")
        status_by_path[relative] = f"{status} {relative}"

    if expected is not None:
        if expected.get("kind") != "GIT_TREE":
            raise ValueError("project Git state changed")
        expected_status = list(expected.get("status", ()))
        expected_paths = [str(row.get("path")) for row in expected.get("files", ())]
        if (
            len(expected_status) != len(expected_paths)
            or any(status_by_path.get(path) != status for path, status in zip(expected_paths, expected_status))
        ):
            raise ValueError("project dirty status changed")
        selected = list(zip(expected_paths, expected_status))
    else:
        selected = sorted(status_by_path.items())
        if not selected:
            return None

    files: list[dict[str, str]] = []
    statuses: list[str] = []
    for relative, status in selected:
        target = project.joinpath(*relative.split("/"))
        try:
            target.resolve().relative_to(project)
            content = target.read_bytes() if target.is_file() else b"<missing>"
        except (OSError, ValueError) as error:
            raise ValueError("project dirty file is unavailable") from error
        statuses.append(status)
        files.append({
            "path": relative,
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
        })
    return _sealed({"kind": "GIT_TREE", "status": statuses, "files": files})
