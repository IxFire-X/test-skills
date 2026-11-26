"""C4: a run many tests build the same way is built once per test process and restored before each test.

A run is bound to the folder of its project (``PROJECT_MOVED`` refuses a moved one), so the copy is put back at the same path:
``<basetemp of the process>/c4-snapshots/<key>/project``.  Tests of one process run one after another, so the folder is theirs in
turn.  Every restore is checked against the tree as built — paths, sizes, ``mtime_ns`` (the report inventory of ``materialize``
keeps it) and sha256 — so a test reads exactly what a fresh build would have left, with no file of an earlier test.

The folder lives only as long as the test that took it.  So the snapshot is given only to the test itself — its own ``tmp_path``,
once per key.  A subfolder, a folder of a wider fixture (``tmp_path_factory``) and a second run of the same key in one test get a
fresh build in ``<folder>/project``, as before the snapshots.  A test that patches what the build reads calls the builder itself
(nothing here can see that)."""
from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

_BUILT: dict[str, tuple[Path, Any, dict[str, tuple]]] = {}
_HOLDER: dict[str, str] = {}  # key -> the test that holds its folder now


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


def _own_test(tmp_path: Path) -> str | None:
    """The node id of the running test when *tmp_path* is that test's own ``tmp_path`` (pytest names it after the test), else None."""
    node = os.environ.get("PYTEST_CURRENT_TEST", "").rsplit(" (", 1)[0]
    own = re.sub(r"[\W]", "_", node.rsplit("::", 1)[-1])[:30]
    return node if node and re.fullmatch(re.escape(own) + r"\d+", tmp_path.name) else None


def restored(tmp_path: Path, key: str, build: Callable[[Path], Any]) -> tuple[Path, Any]:
    """The project folder of *key* as *build* left it, and a copy of what *build* returned; built on the first call of the process."""
    test = _own_test(tmp_path)
    if test is None or _HOLDER.get(key) == test:  # the folder would outlive the test, or the test holds it already
        (tmp_path / "project").mkdir(parents=True)
        return tmp_path / "project", build(tmp_path / "project")
    _HOLDER[key] = test
    if key not in _BUILT:
        home = tmp_path.parent / "c4-snapshots" / key
        shutil.rmtree(home, ignore_errors=True)
        (home / "project").mkdir(parents=True)
        value = build(home / "project")
        shutil.copytree(home / "project", home / "saved", symlinks=True)  # copy2: the times stay
        _BUILT[key] = (home, value, tree(home / "project"))
    else:
        home = _BUILT[key][0]
        shutil.rmtree(home / "project")
        shutil.copytree(home / "saved", home / "project", symlinks=True)
    home, value, built = _BUILT[key]
    assert tree(home / "project") == built, f"the restored run {key} is not the run as built"
    return home / "project", copy.deepcopy(value)
