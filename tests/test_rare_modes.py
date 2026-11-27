"""W3.2: the inserts of the rare modes import (``scan --cases``) and parameter groups — read only by a step of that mode; the ordinary
role is shorter by them; the generator never gets the group insert (it decides on a group itself)."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_param_groups import EXAMPLE, grouped
from tools import cases_import, pilot_state, stage_steps, step_size

STATE = {"attempts": [{"attempt_id": "a" * 32}]}
PACK = Path(__file__).resolve().parents[1]


def _modes(stage: str) -> tuple[str, ...]:
    return stage_steps._modes(PACK, PACK, STATE, "a" * 32, stage, {})


@pytest.fixture
def plain(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setattr(step_size, "module_stand", lambda *_args: None)
    monkeypatch.setattr(cases_import, "cases_path", lambda _root: None)
    monkeypatch.setattr(stage_steps, "_grouped", lambda *_args: False)
    return monkeypatch


def test_a_run_with_cases_reads_the_import_insert_in_the_generator_and_the_review(plain) -> None:
    assert _modes("tc-generator:BATCH-a") == _modes("tc-reviewer:canonical") == ()
    plain.setattr(cases_import, "cases_path", lambda _root: "cases/zephyr.csv")
    assert _modes("tc-generator:BATCH-a") == _modes("tc-reviewer:canonical") == ("import",) and _modes("tc-to-autotest:r1") == ()
    assert step_size.role_files("tc-generator", ("import",))[-1].parts[-2:] == ("modes", "import.md")


def test_a_document_with_a_group_is_read_and_gives_the_group_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    monkeypatch.setattr(pilot_state, "read_effective_canonical_if_present", lambda *_args: {"document": grouped()})
    assert stage_steps._grouped(PACK, "a" * 32, "tc-to-autotest") is True
    monkeypatch.setattr(pilot_state, "read_effective_canonical_if_present", lambda *_args: {"document": json.loads(EXAMPLE.read_text(encoding="utf-8"))})
    assert stage_steps._grouped(PACK, "a" * 32, "autotest-reviewer") is False


def test_the_review_reads_the_groups_of_the_candidate_of_the_journal(monkeypatch: pytest.MonkeyPatch) -> None:
    from tools import orchestrate_test_case_revision

    monkeypatch.setattr(orchestrate_test_case_revision, "journal_assembly", lambda *_args: (grouped(), {}, []))
    assert stage_steps._grouped(PACK, "a" * 32, "tc-reviewer") is True


def test_a_document_that_cannot_be_read_has_no_group(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args):
        raise ValueError("no effective document")

    monkeypatch.setattr(pilot_state, "read_effective_canonical_if_present", broken)
    assert stage_steps._grouped(PACK, "a" * 32, "tc-to-autotest") is False


def test_the_generator_never_gets_the_group_insert(plain) -> None:
    plain.setattr(stage_steps, "_grouped", lambda *_args: True)
    assert _modes("tc-generator:BATCH-a") == () and _modes("tc-to-autotest:r1") == ("param-groups",)


def test_the_modes_go_in_one_order(plain) -> None:
    plain.setattr(step_size, "module_stand", lambda *_args: {"service_url_env": "URL"})
    plain.setattr(cases_import, "cases_path", lambda _root: "cases/zephyr.csv")
    plain.setattr(stage_steps, "_grouped", lambda *_args: True)
    assert _modes("tc-reviewer:canonical") == ("stand", "import", "param-groups")


def test_every_insert_is_small() -> None:
    inserts = sorted((PACK / "skills").glob("*/modes/*.md"))
    assert len(inserts) == 9 and all(path.stat().st_size <= 1600 for path in inserts), [(path.name, path.stat().st_size) for path in inserts]


@pytest.mark.parametrize(("role", "mode", "words"), [
    ("tc-generator", "import", "The last input `imported` holds the cases of this batch"),
    ("tc-reviewer", "import", "Импорт готовых кейсов:"),
    ("tc-reviewer", "param-groups", "A parameter group (`param_group`) is shown once"),
    ("tc-to-autotest", "param-groups", "Группа `param_group` в Java:"),
    ("autotest-reviewer", "param-groups", "Группа `param_group` (Java):"),
])
def test_a_moved_line_is_in_its_insert_only(role: str, mode: str, words: str) -> None:
    ordinary = "".join(path.read_text(encoding="utf-8") for path in step_size.role_files(role))
    assert words not in ordinary and words in step_size.role_files(role, (mode,))[-1].read_text(encoding="utf-8")


def test_prepare_of_a_batch_of_a_run_with_cases_names_the_import_insert(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.helpers import prepare_stage
    from tests.test_cases_import import _to_generator

    run = _to_generator(tmp_path, monkeypatch)
    code, out = prepare_stage(run["root"], f"tc-generator:{run['plan']['batches'][0]['batch_id']}")
    assert code == 0 and Path(out["result"]["read"]["role_files"][-1]).parts[-2:] == ("modes", "import.md"), out
