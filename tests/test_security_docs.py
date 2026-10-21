"""S1e/S1f (security review notes 10, 11): the documents say what the package does, the copy for a project leaves agent files out."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USAGE = (ROOT / "USAGE.md").read_text(encoding="utf-8")
ORCHESTRATE = (ROOT / "skills" / "orchestrate" / "SKILL.md").read_text(encoding="utf-8")


def test_no_document_promises_that_nothing_is_installed_or_that_python_never_calls_a_model() -> None:
    for name, text in (("USAGE.md", USAGE), ("orchestrate/SKILL.md", ORCHESTRATE)):
        flat = " ".join(text.split())
        assert "ничего не устанавливает" not in flat, name
        assert "Python не вызывает модель" not in flat, name
        assert "сам модель не вызывает" not in flat, name


def test_usage_has_the_permissions_section_of_the_package() -> None:
    match = re.search(r"^## Права, сеть и исполнение\n(.*?)(?=^## )", USAGE, flags=re.S | re.M)
    assert match, "USAGE.md has no section «Права, сеть и исполнение»"
    section = match.group(1)
    for needle in ("Читает", "Пишет", "Запускает", "Сеть", "--mutation", ".skillsrc", "~/.m2", "git", "execution.mode: human", "ts.py",
                   "пакет не запускает"):
        assert needle in section, needle
    network = re.search(r"\*\*Сеть\.\*\*(.*?)(?=\n\nЭкспорт|\n\n\*\*|\Z)", section, flags=re.S).group(1)
    assert "пакет не запускает процессов, которые ходят в" in network and "запускает человек" in network
    assert "пакет ничего не" in network and "только через процессы, которые он" not in network
    # H1: the host delivery launches no model CLI; the process mode is described for the universal build only.
    for absent in ("--review-runner process", "run --runner process", "claude -p", "codex exec"):
        assert absent not in section, absent


def test_the_sparse_copy_for_a_project_leaves_agent_and_ci_files_out() -> None:
    line = next(line for line in USAGE.splitlines() if "git sparse-checkout set" in line)
    for pattern in ("'!/tests/'", "'!/docs/*.pptx'", "'!/CLAUDE.md'", "'!/.github/'", "'!/.claude/'"):
        assert pattern in line, pattern
