"""C4: a run many tests build the same way is built once per test process and restored before each test.

A run is bound to the folder of its project (``PROJECT_MOVED`` refuses a moved one), so the copy is put back at the same path:
``<basetemp of the process>/c4-snapshots/<key>/project``.  Tests of one process run one after another, so the folder is theirs in
turn.  Every restore is checked against the tree as built — paths, sizes, ``mtime_ns`` (the report inventory of ``materialize``
keeps it) and sha256 — so a test reads exactly what a fresh build would have left, with no file of an earlier test."""
from __future__ import annotations

import copy
import hashlib
import shutil
from pathlib import Path
from typing import Any, Callable

_BUILT: dict[str, tuple[Any, dict[str, tuple]]] = {}


def tree(root: Path) -> dict[str, tuple]:
    """Every folder and file under *root*: a folder by its name, a file by size, ``mtime_ns`` and sha256."""
    found: dict[str, tuple] = {}
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            found[name] = ("dir",)
        else:
            details = path.stat()
            found[name] = (details.st_size, details.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return found


def restored(tmp_path: Path, key: str, build: Callable[[Path], Any]) -> tuple[Path, Any]:
    """The project folder of *key* as *build* left it, and a copy of what *build* returned; built on the first call of the process."""
    home = tmp_path.parent / "c4-snapshots" / key
    project, saved = home / "project", home / "saved"
    if key not in _BUILT:
        shutil.rmtree(home, ignore_errors=True)
        project.mkdir(parents=True)
        value = build(project)
        shutil.copytree(project, saved, symlinks=True)  # copy2: the times stay
        _BUILT[key] = (value, tree(project))
    else:
        shutil.rmtree(project)
        shutil.copytree(saved, project, symlinks=True)
    value, built = _BUILT[key]
    assert tree(project) == built, f"the restored run {key} is not the run as built"
    return project, copy.deepcopy(value)
