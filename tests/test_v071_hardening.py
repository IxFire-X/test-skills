"""v0.7.1 (the security review's notes): no look at a share by resolve(), a link in the place of an assembled file is no file the code opens,
and a cut list of schema errors says where the whole list is."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_a_unc_path_in_the_journal_is_not_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import output_guard  # noqa: F401  (its import resolves a path: before the patch)
    from tools.run_pipeline import relative_to_project

    def touched(*_args: object) -> Path:
        raise AssertionError("resolve() reached for a share")

    monkeypatch.setattr(Path, "resolve", touched)
    assert relative_to_project(tmp_path, "\\\\server\\share\\spec.md") == "//server/share/spec.md"


def test_a_folder_in_the_place_of_an_assembled_file_is_refused_with_the_way_for_a_link(tmp_path: Path) -> None:
    from tools.assemble_sections import AssembleRefused, _already_there

    (tmp_path / "sections.csv").mkdir()
    with pytest.raises(AssembleRefused) as refused:
        _already_there(tmp_path, tmp_path / "sections.csv", b"x")
    assert refused.value.code == "ASSEMBLE_CONFLICT" and "человек удаляет" in refused.value.hint


def test_another_file_in_the_place_of_an_assembled_file_is_shown_to_the_human(tmp_path: Path) -> None:
    from tools.assemble_sections import AssembleRefused, _already_there

    (tmp_path / "sections.csv").write_bytes(b"other")
    with pytest.raises(AssembleRefused) as refused:
        _already_there(tmp_path, tmp_path / "sections.csv", b"x")
    assert "покажи этот путь человеку" in refused.value.hint


def test_the_same_bytes_in_the_place_of_an_assembled_file_are_taken_as_done(tmp_path: Path) -> None:
    from tools.assemble_sections import _already_there

    (tmp_path / "sections.csv").write_bytes(b"x")
    assert _already_there(tmp_path, tmp_path / "sections.csv", b"x") is True


def test_a_cut_list_of_errors_names_where_the_whole_list_is() -> None:
    from tools.validate_artifact import RETRY_TAIL_MORE, retry_prompt

    errors = [{"path": f"/items/{index}", "code": "SCHEMA_TYPE", "message": "m"} for index in range(60)]
    prompt = retry_prompt({}, errors, "python -m tools.validate_artifact schemas/x.json <файл ответа>")
    shown = prompt.count("\n- ")
    assert f"и ещё {60 - shown} ошибок — полный список: python -m tools.validate_artifact schemas/x.json" in prompt and prompt.rstrip().endswith(RETRY_TAIL_MORE)
