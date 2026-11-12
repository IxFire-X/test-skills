"""V7a: the texts of the rework — the role file only the rework reads, the sentence of the reviewer's role."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REWORK = ROOT / "skills" / "tc-generator" / "rework.md"


def test_the_text_of_the_rework_fits_its_limit_and_no_batch_reads_it() -> None:
    from tools.step_size import role_files

    assert REWORK.stat().st_size <= 3000
    assert REWORK not in role_files("tc-generator")


def test_the_text_of_the_rework_names_the_form_the_code_checks() -> None:
    text = REWORK.read_text(encoding="utf-8")
    for word in ("tc-generator:rework-1", "incoming/tc-generator-rework-1.json", '"batch_id": "rework-1"', "case_id", "requirement_ids", "display_order", "param_group",
                 "REWORK_CASE_SET", "REWORK_CASE_IDENTITY", "REWORK_FOREIGN_CASE_CHANGED", "--transport-attempts 2"):
        assert word in text, word


def test_the_text_of_the_rework_tells_no_one_to_build_install_or_call_the_network() -> None:
    text = REWORK.read_text(encoding="utf-8")
    assert re.search(r"Не запускай сборку, не ставь зависимости, не ходи в сеть", text) and not re.search(r"\b(pip|mvn|gradle|npm|curl|git)\b", text)


def test_the_file_is_in_every_delivery_profile() -> None:
    from tools.pack_registry import PROFILES, profile_paths

    assert all("skills/tc-generator/rework.md" in profile_paths(["skills/tc-generator/rework.md"], profile) for profile in PROFILES)


def test_the_role_of_the_reviewer_says_a_blocking_finding_names_its_cases() -> None:
    text = (ROOT / "skills" / "tc-reviewer" / "SKILL.md").read_text(encoding="utf-8")
    assert "names the cases it is about in `related_ids`" in text and "names no case rejects the whole set" in text


def test_the_text_of_the_second_round_fits_its_limit_names_the_form_and_no_role_reads_it_by_default() -> None:
    from tools.pack_registry import PROFILES, profile_paths
    from tools.step_size import role_files

    path = ROOT / "skills" / "tc-reviewer" / "rework.md"
    text = path.read_text(encoding="utf-8")
    assert path.stat().st_size <= 2000 and path not in role_files("tc-reviewer")
    for word in ("tc-reviewer:rework-1", "incoming/tc-reviewer-rework-1.json", "REWORK_REVIEW_FIX", "reviewed_case_ids", "related_ids", "--transport-attempts 2"):
        assert word in text, word
    assert not re.search(r"(pip|mvn|gradle|npm|curl|git)", text) and "Не запускай сборку, не ставь зависимости, не ходи в сеть" in text
    assert all("skills/tc-reviewer/rework.md" in profile_paths(["skills/tc-reviewer/rework.md"], profile) for profile in PROFILES)
