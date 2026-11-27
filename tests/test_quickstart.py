"""t1: QUICKSTART.md — the short way in, in both deliveries."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import pack_registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("profile", ["cases-only", "mode-b"])
def test_quickstart_is_in_the_delivery(profile: str) -> None:
    paths = [row["path"] for row in pack_registry.registry_rows(ROOT)]
    assert "QUICKSTART.md" in pack_registry.profile_paths(paths, profile)


def test_quickstart_is_short() -> None:
    assert len((ROOT / "QUICKSTART.md").read_text(encoding="utf-8").splitlines()) <= 95


def test_quickstart_leads_to_cases_only_first() -> None:  # approved 27.11, texts-approval p. 17 (Q1, Q3)
    text = (ROOT / "QUICKSTART.md").read_text(encoding="utf-8")
    assert "Первый прогон — только тест-кейсы и их ревью, без автотестов: так работает любая поставка." in text
    assert "До первого шага модели от вас нужно от одного до пяти решений:" in text
