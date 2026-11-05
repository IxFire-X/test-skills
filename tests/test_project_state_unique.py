"""V4a (was FaT-RL): the manifest of a run carries no list of dirty paths any more.

The paths of ``git status`` were unique by code because the schema's ``uniqueItems`` was quadratic on a large dirty tree (Petclinic: 1190
paths, 3.4 s for one read of the manifest).  With the project compared by its inventory (``test_p8b_git``) the state is ``null`` or the
sealed ``GIT_DIRECTORY``, and the old ``GIT_TREE`` is not a state of a manifest: the schema refuses it and so does the read of the run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_schema_knows_only_null_and_the_directory_marker() -> None:
    schema = json.loads((ROOT / "schemas" / "run-manifest.schema.json").read_text(encoding="utf-8"))
    rows = schema["properties"]["project_state"]["oneOf"]
    assert [row.get("type") if "properties" not in row else row["properties"]["kind"]["const"] for row in rows] == ["null", "GIT_DIRECTORY"]


def _with_project_state(tmp_path: Path, state) -> Path:
    from tests.test_reviewer_protocol import _new_run
    from tools import pilot_state

    run_root, _attempt_id = _new_run(tmp_path)
    path = run_root / "run-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("digest")
    manifest["project_state"] = state
    path.write_bytes(pilot_state._canonical_bytes(pilot_state._sealed(manifest)))
    return run_root


def test_a_run_with_the_directory_marker_or_no_state_is_read(tmp_path: Path) -> None:
    from tools import pilot_state

    marker = pilot_state._sealed({"kind": "GIT_DIRECTORY", "path": ".git", "entries": []})
    for number, state in enumerate((None, marker)):
        run_root = _with_project_state(tmp_path / str(number), state)
        assert pilot_state.read_run(run_root)["manifest"]["project_state"] == state


def test_a_run_with_the_old_git_tree_state_is_refused(tmp_path: Path) -> None:
    from tools import pilot_state

    state = pilot_state._sealed({"kind": "GIT_TREE", "status": ["?? a.txt"], "files": [{"path": "a.txt", "digest": "sha256:" + "1" * 64}]})
    run_root = _with_project_state(tmp_path, state)
    with pytest.raises(ValueError):
        pilot_state.read_run(run_root)
