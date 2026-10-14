"""B2 (finding 9): an incomplete review is not reported as blocking findings.

Live suite-update U1 (run 11cf9c60): the canonical review ended with one UNCHECKED scope and
no BLOCKING finding, yet ``pr-description.md`` said the review found blocking findings.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.suite_summary import pr_description
from tools.suite_update import review_record, summary_review_facts

ROOT = Path(__file__).resolve().parents[1]
AGGREGATE = json.loads((ROOT / "tests" / "fixtures" / "live-u1" / "review-canonical-aggregate.json").read_text(encoding="utf-8"))
SCOPE = "cross-666d4860460eefcec003b6897ebed87f6c6fb9095457b60036351df1f2799d21"


def _facts(verdict: dict[str, Any]) -> dict[str, Any]:
    state: dict[str, Any] = {"facts": {}}
    review_record(state, "canonical", verdict, parts=2, input_bytes=131653)
    return {
        "suite_id": "SUITE-petclinic-20261009g-root", "run_id": "11cf9c60ff9847edbb7c7738049ca550", "suite_dir": "test-cases/", "patch_name": "suite.patch",
        "migration": None, "requirements": {"added": [], "changed": ["O05"], "removed": [], "renamed": []},
        "cases": {"changed": [], "new": [], "retired": [], "unchanged": 81}, "case_texts": {},
        "tests": {"updated": [], "new": [], "repaired": [], "removed": [], "run": {"methods": 0, "passed": 0, "failed": 0, "not_run": 0}},
        "quarantine": [], "released": [], "questions": [], "proposals": [], "strength": None, "strength_drops": [], "stop": None,
        **summary_review_facts(state),
    }


def test_u1_incomplete_review_names_its_unchecked_scopes() -> None:
    facts = _facts(AGGREGATE)
    assert facts["review_blocked"] is True and facts["review_unchecked"] == [{"scope_id": SCOPE, "reasons": [row["reason"] for row in AGGREGATE["unchecked"]]}]
    text = pr_description(facts)
    assert "блокирующие замечания" not in text
    assert "ревью неполно: 1 область UNCHECKED" in text
    assert f"- `{SCOPE}`: " + AGGREGATE["unchecked"][0]["reason"] in text
    assert AGGREGATE["unchecked"][1]["reason"] in text
    # The WARNING/INFO findings stay visible, but not as blocking ones.
    assert "## Замечания ревью (не блокируют)" in text and "SOURCE_CONDITION_WITHOUT_CREQ" in text


def test_a_blocking_finding_is_still_reported_as_blocking() -> None:
    verdict = json.loads(json.dumps(AGGREGATE))
    verdict["findings"][0]["severity"] = "BLOCKING"
    verdict["unchecked"] = []
    text = pr_description(_facts(verdict))
    assert "ревью нашло блокирующие замечания" in text and "UNCHECKED" not in text
