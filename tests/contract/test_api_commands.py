"""V0: every command of ``tools.api`` is one the texts tell the agent about — or one named here, with the reason it is not.

A command the agent never hears of is dead weight at best and a hidden way round the protocol at worst.  The list of service commands is
explicit: a new command that no text names fails this test until a text names it or somebody writes the reason here."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[2]
SERVICE_COMMANDS = {
    "complete-review": "closing command of the review of nine commands; exists only where the old CLI is (execution_gate.legacy_available()); the texts teach review-open / review-select",
    "document-digest": "the sha256 of a canonical document, used by the tests and by hand to compare files; no step of the protocol needs it",
}


def _texts() -> dict[Path, str]:
    paths = [*(ROOT / "skills").rglob("*.md"), ROOT / "README.md", ROOT / "HOW-TO-USE.md", *ROOT.glob("USAGE*.md")]
    return {path: path.read_text(encoding="utf-8") for path in paths}


def _named(command: str, texts: dict[Path, str]) -> bool:
    pattern = re.compile(rf"(?:tools\.api\s+|`){re.escape(command)}(?![\w-])")
    return any(pattern.search(text) for text in texts.values())


def test_the_commands_of_the_api_are_the_commands_the_texts_name_plus_the_explicit_service_ones() -> None:
    from tools import api

    texts = _texts()
    named = {command for command in api.COMMANDS if _named(command, texts)}
    assert set(api.COMMANDS) - named == set(SERVICE_COMMANDS), (
        f"a command no text names (name it in a SKILL or add it, with a reason, to SERVICE_COMMANDS): {sorted(set(api.COMMANDS) - named - set(SERVICE_COMMANDS))}; "
        f"a service command that a text names now (remove it from SERVICE_COMMANDS): {sorted(set(SERVICE_COMMANDS) & named)}")
    assert set(SERVICE_COMMANDS) <= set(api.COMMANDS), "a service command that does not exist any more"
