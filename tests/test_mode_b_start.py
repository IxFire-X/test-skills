"""V8-W3: HOW-TO-USE-MODE-B.md starts with "С чего начать": the request for a full run differs from the cases-only request only in its profile, permission and result lines."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ("Выполни .tools/test-skills/skills/orchestrate/SKILL.md.", "Проект: <проект>.", "Требования: docs/feature.md, путь от корня проекта.",
          "Инструменты пакета запускай командами python -m tools.… через Python", "3.11–3.13; функции пакета не импортируй.")


def _request(name: str) -> list[str]:
    blocks = re.findall(r"```text\n(.*?)```", (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n"), re.S)
    return next(block.split("\n") for block in blocks if block.startswith("Выполни"))


def test_the_mode_b_text_starts_with_the_way_to_begin() -> None:
    text = (ROOT / "HOW-TO-USE-MODE-B.md").read_text(encoding="utf-8")
    assert text.index("## 0. С чего начать") < text.index("## 1. Что делает режим Б")
    assert "--profile local-pilot-v1" in text and "<python пакета> -m tools.ready --project <проект>" in text


def test_the_full_run_request_keeps_the_lines_of_the_cases_request_and_asks_the_human_to_run() -> None:
    request = _request("HOW-TO-USE-MODE-B.md")
    assert all(line in request for line in SHARED) and all(line in _request("HOW-TO-USE.md") for line in SHARED)
    assert any(line.startswith("Профиль: local-pilot-v1") for line in request)
    assert "Сборку и тесты сам не запускай" in " ".join(request) and "— её выполню я." in " ".join(" ".join(request).split())
