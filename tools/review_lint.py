"""Deterministic review linter: suspicions only, never findings.

A suspicion goes into the compact review envelope of every part that holds its
cases.  The reviewer answers each one in ``lint_dispositions`` (``confirmed`` or
``rejected``); only the reviewer's own finding can turn it into a defect.
"""
from __future__ import annotations

from typing import Any, Callable, Mapping

LINT_VERSION = "review-lint-v1"

Rule = Callable[[Mapping[str, Any]], list[dict[str, Any]]]
RULES: list[tuple[str, Rule]] = []


def lint_document(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every suspicion of every rule in a stable order.

    The ID is derived from the suspicion itself, so a change elsewhere in the
    document does not renumber it (carried review areas keep their inputs).
    """
    from tools.review_parts import review_digest

    rows: list[dict[str, Any]] = []
    for rule, check in RULES:
        for row in check(document):
            item = {"rule": rule, "case_ids": list(row["case_ids"]), "related_ids": list(row["related_ids"]), "message": str(row["message"])}
            item = {"lint_id": "LINT-" + review_digest(item)[7:17].upper(), **item}
            if item["lint_id"] not in {existing["lint_id"] for existing in rows}:
                rows.append(item)
    return rows
