"""Where the package writes a path the agent names: ``--out`` and ``--output-dir`` (security review: unbounded paths).

A path from the command line is input, not a boundary.  ``--out`` names one file in ``<run>/tmp/`` of an existing run;
``--output-dir`` names a directory inside the project (never inside ``.git``).  Both are resolved first (links and junctions
are followed), then compared with the run or the project; nothing is created here.  Every entry point of the package that
takes one of the two flags calls this module; ``tests/test_output_guard.py`` reads the sources and fails for one that does not.
"""

from __future__ import annotations

from pathlib import Path

OUT_CODE = "API_OUT_OUTSIDE_RUN"
DIR_CODE = "OUTPUT_DIR_OUTSIDE_PROJECT"
OUT_FIX = "добавь --run-root <run> (существующий прогон) и пиши файл в <run>/tmp/<имя>.json"
DIR_FIX = "каталог публикации — внутри проекта (например <project>/test-cases/) и не внутри .git; повтори с таким --output-dir"


class OutputRefused(ValueError):
    def __init__(self, code: str, message: str, fix: str) -> None:
        self.code, self.fix = code, fix
        super().__init__(message)


def existing_run(run_root: object) -> tuple[Path, Path]:
    """``(project, run)`` of a run that exists: ``<project>/.pilot-runs/<id>`` with its ``run-manifest.json``."""
    from tools import pilot_state

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
    target = Path(path).resolve()
    if target.parent != run / "tmp" or not target.name:
        raise OutputRefused(OUT_CODE, "--out must name a file under <run>/tmp/ of the --run-root run", OUT_FIX)
    return target


def output_directory(path: str | Path, project: object) -> Path:
    """The directory ``--output-dir`` names: the project or inside it, no ``.git`` on the way, links resolved."""
    root = Path(str(project))
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise OutputRefused(DIR_CODE, "--output-dir: the project is not an existing directory", DIR_FIX) from None
    directory = Path(path).resolve()
    try:
        relative = directory.relative_to(root)
    except ValueError:
        raise OutputRefused(DIR_CODE, f"--output-dir must be inside the project {root}", DIR_FIX) from None
    if any(part.lower() == ".git" for part in relative.parts):
        raise OutputRefused(DIR_CODE, "--output-dir must not be inside .git", DIR_FIX)
    return directory


def run_output_directory(path: str | Path, run_root: object) -> Path:
    """``output_directory`` for the project of ``run_root``."""
    project, _run = existing_run(run_root)
    return output_directory(path, project)
