"""tails4: the tails of the code review of the fourth stack."""
from __future__ import annotations

import json
import sys

import pytest


def test_init_tells_why_a_skillsrc_held_by_the_os_is_not_written(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A read-only file or one held by another program (the Windows desktop case) gets the words and the advice, not a bare write_error."""
    from tests.test_skillsrc_human_keys import _project
    from tools import init_skillsrc

    def held(*_args, **_kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(init_skillsrc, "atomic_write_confined_bytes_in_project", held)
    project = _project(tmp_path)
    (project / "answers.json").write_text('{"module:root:test.framework": "pytest"}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["init_skillsrc", "--project", str(project), "--answers", str(project / "answers.json"), "--write"])
    assert init_skillsrc.main() == 1
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["errors"] == ["write_error"] and receipt["status"] == "error", receipt
    assert "Access is denied" in receipt["message"] and "только для чтения" in receipt["fix"] and "закройте" in receipt["fix"], receipt


def test_a_receipt_outside_the_run_root_is_refused_as_outside_not_as_a_link(tmp_path) -> None:
    """The text of the refusal is the core's: a path outside the run is no symlink."""
    import shutil

    from tests.test_v06_fix15 import READERS, _frozen
    from tools.project_inventory import InventoryError

    root, frozen = _frozen(tmp_path)
    reader, folder, key = READERS[0]
    name = frozen[key]["digest"].removeprefix("sha256:") + ".json"
    shutil.copytree(root / folder, tmp_path / "outside")
    with pytest.raises(InventoryError) as refused:
        reader(tmp_path, root, tmp_path / "outside" / name)
    assert "below exact confined root" in str(refused.value) and "symlink" not in str(refused.value)


def test_an_answer_outside_the_run_is_not_turned_into_files(tmp_path) -> None:
    """The file of a run is read through the core: bytes with the right digest that lie outside the run give no files of code and tables."""
    import hashlib

    from tests.test_o2a_automation_text import _artifact, _prepared
    from tools.step_size import automation_text_rows

    run = _prepared(tmp_path)
    outside = tmp_path / "answer.json"
    outside.write_bytes(json.dumps(_artifact(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest = "sha256:" + hashlib.sha256(outside.read_bytes()).hexdigest()
    assert automation_text_rows(tmp_path, run["root"], [outside], digest, False) is None
