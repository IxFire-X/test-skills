"""FaT-RL: the dirty paths of a run manifest are unique by code — the schema's uniqueItems was quadratic (Petclinic: 1190
paths, 3.4 s for one read of the manifest)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_the_schema_has_no_quadratic_uniqueness_on_the_dirty_paths() -> None:
    schema = json.loads((ROOT / "schemas" / "run-manifest.schema.json").read_text(encoding="utf-8"))
    tree = next(row for row in schema["properties"]["project_state"]["oneOf"] if row.get("properties", {}).get("kind", {}).get("const") == "GIT_TREE")
    assert "uniqueItems" not in tree["properties"]["files"] and "uniqueItems" not in tree["properties"]["status"]


def _with_project_state(tmp_path: Path, files: list[dict], status: list[str]) -> Path:
    from tests.test_reviewer_protocol import _new_run
    from tools import pilot_state

    run_root, _attempt_id = _new_run(tmp_path)
    path = run_root / "run-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.pop("digest")
    manifest["project_state"] = pilot_state._sealed({"kind": "GIT_TREE", "status": status, "files": files})
    path.write_bytes(pilot_state._canonical_bytes(pilot_state._sealed(manifest)))
    return run_root


ONE = {"path": "a.txt", "digest": "sha256:" + "1" * 64}
TWO = {"path": "b.txt", "digest": "sha256:" + "2" * 64}


def test_unique_dirty_paths_are_read(tmp_path: Path) -> None:
    from tools.pilot_state import read_run

    run_root = _with_project_state(tmp_path, [ONE, TWO], ["?? a.txt", "?? b.txt"])
    assert [row["path"] for row in read_run(run_root)["manifest"]["project_state"]["files"]] == ["a.txt", "b.txt"]


@pytest.mark.parametrize(("files", "status"), [
    ([ONE, ONE], ["?? a.txt", "?? b.txt"]),
    ([ONE, {**ONE, "digest": "sha256:" + "3" * 64}], ["?? a.txt", " M a.txt"]),  # one path twice with two digests
    ([ONE, TWO], ["?? a.txt", "?? a.txt"]),
])
def test_a_duplicate_dirty_path_or_status_is_refused(tmp_path: Path, files: list, status: list) -> None:
    from tools.pilot_state import read_run

    run_root = _with_project_state(tmp_path, files, status)
    with pytest.raises(ValueError, match="invalid run boundary"):
        read_run(run_root)
