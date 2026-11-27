"""v0.8: the product is «Камертон» (it was «Трасса»): the texts of the deliveries carry the new name only — a line copied from an old note
would bring the old one back.  «трассировка», «трасса прогона» and the like are terms, not the name: lowercase, they stay."""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import pack_registry

ROOT = Path(__file__).resolve().parents[1]
TITLES = {"README.md": "# Камертон\n", "HOW-TO-USE.md": "# Камертон: ", "HOW-TO-USE-MODE-B.md": "# Камертон, ", "QUICKSTART.md": "# Камертон: "}


@pytest.mark.parametrize("profile", ["cases-only", "mode-b"])
def test_the_texts_of_the_delivery_have_no_old_name(profile: str) -> None:
    paths = pack_registry.profile_paths([row["path"] for row in pack_registry.registry_rows(ROOT)], profile)
    texts = [path for path in paths if Path(path).suffix in (".md", ".txt", ".json", ".py", ".csv", ".example")]
    old = [path for path in texts if "Трасс" in (text := (ROOT / path).read_text(encoding="utf-8")) or "Trassa" in text]
    assert texts and old == [], old


def test_the_documents_of_the_tester_start_with_the_name() -> None:
    assert [name for name, title in TITLES.items() if not (ROOT / name).read_text(encoding="utf-8").startswith(title)] == []
