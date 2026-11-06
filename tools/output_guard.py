"""Where the package writes and reads a path the agent names: ``--out``, ``--output-dir`` and the input files.

A path from the command line or from a file is input, not a boundary.  ``--out`` names one file in ``<run>/tmp/`` of an existing run;
``--output-dir`` names ``<run>/published`` of that run or a directory below it.  An input file is inside the project (or, for a command
without a run, inside the run it lies in), with no link or junction on the way.  Every path is judged by its text first — no NUL, no UNC
or device path, no ``..`` — so a path that would reach the network is refused before anything opens it.  Nothing is created here.  Every
entry point of the package that takes ``--out`` or ``--output-dir`` calls this module; ``tests/test_output_guard.py`` reads the sources
and fails for one that does not.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path, PureWindowsPath

OUT_CODE = "API_OUT_OUTSIDE_RUN"
DIR_CODE = "OUTPUT_DIR_OUTSIDE_RUN"
IN_CODE = "INPUT_OUTSIDE_PROJECT"
OUT_FIX = "добавь --run-root <run> (существующий прогон) и пиши файл в <run>/tmp/<имя>.json"
DIR_FIX = "каталог публикации — <run>/published (или ниже) прогона из --run-root; повтори с таким --output-dir"
IN_FIX = ("передай --run-root <run> (или --project <проект>) и файл внутри этого проекта; без них — файл внутри прогона "
          "<project>/.pilot-runs/<run_id>/ (свои файлы агент кладёт в <run>/tmp/); путь — обычный файл без ссылок и junction, без «..», не UNC")


class OutputRefused(ValueError):
    def __init__(self, code: str, message: str, fix: str) -> None:
        self.code, self.fix = code, fix
        super().__init__(message)


def local_path_text(raw: str) -> bool:
    """A path that names a place on this machine by its text alone — no NUL, no UNC or device prefix: nothing that would reach the
    network.  Judged before anything opens, resolves or stats the path."""
    return "\x00" not in raw and not raw.replace("/", "\\").startswith("\\\\")


def _plain(raw: str, *, relative: bool = False) -> bool:
    """``local_path_text`` and no ``..``; a *relative* one also no root and no drive."""
    windows = PureWindowsPath(raw)
    if not local_path_text(raw) or ".." in windows.parts:
        return False
    return not relative or not (windows.drive or windows.root or raw.startswith("/"))


def _inside(root: Path, candidate: Path) -> Path:
    """*candidate* (absolute, judged by its text) below *root*, no link or junction from *root* down, a regular file."""
    base = Path(os.path.abspath(root))
    try:
        relative = candidate.relative_to(base)
    except ValueError:
        try:
            base = base.resolve()
            relative = candidate.relative_to(base)
        except (OSError, ValueError):
            raise OutputRefused(IN_CODE, f"{candidate}: the file is outside {root}", IN_FIX) from None
    current, details = base, None
    for part in relative.parts:
        current = current / part
        details = os.lstat(current)
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
            raise OutputRefused(IN_CODE, f"{candidate}: a link or junction on the way", IN_FIX)
    if details is None or not stat.S_ISREG(details.st_mode):
        raise OutputRefused(IN_CODE, f"{candidate}: not a regular file", IN_FIX)
    return current


def project_path(project: Path, value: str) -> Path:
    """A ``project_path`` (relative to the project) judged by its text alone — nothing is opened."""
    if not _plain(str(value), relative=True):
        raise OutputRefused(IN_CODE, f"{value!r}: a project path is relative, inside the project, without .., a drive or UNC", IN_FIX)
    return Path(project) / value


def project_file(project: Path, value: str) -> Path:
    """The file a ``project_path`` names: by its text, then inside the project with no link on the way."""
    return _inside(Path(project), Path(os.path.abspath(project_path(project, value))))


def input_file(value: str | Path, project: Path | None, *, base: Path | None = None) -> Path:
    """The file a path of the command line names (relative to *base*, else the working directory): by its text, then inside
    *project* — or, without one, inside the run of a project it lies in (``<project>/.pilot-runs/<run_id>/``)."""
    raw = str(value)
    if not _plain(raw):
        raise OutputRefused(IN_CODE, f"{raw}: not a local path (UNC, device, NUL or ..)", IN_FIX)
    candidate = Path(os.path.abspath(Path(base) / raw if base is not None else raw))
    if project is not None:
        return _inside(project, candidate)
    parts = candidate.parts
    runs = [index for index in range(1, len(parts) - 2) if parts[index].lower() == ".pilot-runs"]
    if not runs:
        raise OutputRefused(IN_CODE, f"{raw}: without --run-root the file lies in a run (<project>/.pilot-runs/<run_id>/)", IN_FIX)
    try:
        _project, run = existing_run(Path(*parts[: runs[-1] + 2]))
    except OutputRefused:
        raise OutputRefused(IN_CODE, f"{raw}: without --run-root the file lies in an existing run", IN_FIX) from None
    return _inside(run, candidate)


def existing_run(run_root: object) -> tuple[Path, Path]:
    """``(project, run)`` of a run that exists: ``<project>/.pilot-runs/<id>`` with its ``run-manifest.json``."""
    from tools import pilot_state

    if not local_path_text(str(run_root)):
        raise OutputRefused(OUT_CODE, "--run-root must name an existing run: <project>/.pilot-runs/<run_id>/", OUT_FIX)
    try:
        project, run = pilot_state._run_root(Path(str(run_root)))
    except (ValueError, OSError, TypeError):
        raise OutputRefused(OUT_CODE, "--run-root must name an existing run: <project>/.pilot-runs/<run_id>/", OUT_FIX) from None
    if not (run / "run-manifest.json").is_file():
        raise OutputRefused(OUT_CODE, "--run-root must name an existing run: no run-manifest.json there", OUT_FIX)
    return project, run


def out_file(path: str | Path, run_root: object) -> Path:
    """The file ``--out`` names: directly in ``<run>/tmp/``, the run given by ``--run-root``."""
    if run_root is None:
        raise OutputRefused(OUT_CODE, "--out must name a file under <run>/tmp/: --run-root is missing", OUT_FIX)
    _project, run = existing_run(run_root)
    if not local_path_text(str(path)):
        raise OutputRefused(OUT_CODE, "--out must name a file under <run>/tmp/ of the --run-root run", OUT_FIX)
    target = Path(path).resolve()
    if target.parent != run / "tmp" or not target.name:
        raise OutputRefused(OUT_CODE, "--out must name a file under <run>/tmp/ of the --run-root run", OUT_FIX)
    return target


def run_output_directory(path: str | Path, run_root: object) -> Path:
    """The directory ``--output-dir`` names: ``<run>/published`` of the run ``run_root`` names, or below it, links resolved."""
    _project, run = existing_run(run_root)
    published = run / "published"
    directory = Path(path).resolve() if local_path_text(str(path)) else None
    if directory is None or (directory != published and published not in directory.parents):
        raise OutputRefused(DIR_CODE, f"--output-dir must be {published} or below it", DIR_FIX)
    return directory
