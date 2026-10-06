"""Review modes: ``pairs`` (legacy scopes and pair cross checks) and ``compact-v1``.

The mode is a run setting (``--review-mode``), recorded in the driver config and in
the review snapshot.  Every reader dispatches on the frozen plan, never on the
current setting, so a plan keeps the rules it was built with.
"""
from __future__ import annotations

from typing import Any, Mapping

PAIRS = "pairs"
COMPACT = "compact-v1"
MODES = (PAIRS, COMPACT)
DEFAULT_MODE = PAIRS
DEFAULT_INPUT_BYTES = 200_000
DEFAULT_RESERVE_BYTES = 20_000


def plan_mode(plan: Mapping[str, Any]) -> str:
    return COMPACT if plan.get("mode") == COMPACT else PAIRS


def snapshot_mode(payload: Mapping[str, Any]) -> str:
    return COMPACT if payload.get("review_mode") == COMPACT else PAIRS


def source_chunk_bytes(input_byte_budget: int, response_reserve_bytes: int) -> int:
    return max(1, (input_byte_budget - response_reserve_bytes) // 4)


def build_offline_plan(payload: Mapping[str, Any], *, mode: str = PAIRS, review_key: str = "canonical",
                       input_byte_budget: int = DEFAULT_INPUT_BYTES, response_reserve_bytes: int = DEFAULT_RESERVE_BYTES,
                       instructions: str = "offline measurement") -> dict[str, Any]:
    """The plan the driver would freeze for this snapshot payload, without a run (measurements, eval)."""
    from tools.review_parts import build_review_plan, document_index, review_digest, review_scopes

    payload = dict(payload)
    if mode == COMPACT:
        payload["review_mode"] = COMPACT
    specification = {"review_kind": "tc-reviewer" if review_key == "canonical" else "autotest-reviewer",
                     "revision": 1 if review_key == "canonical" else int(review_key[1]),
                     "snapshot_digest": review_digest(payload), "instructions": instructions,
                     "response_reserve_bytes": response_reserve_bytes, "document_index": document_index(payload)}
    if mode == COMPACT:
        from tools.review_compact import build_compact_plan

        return build_compact_plan(specification, payload, input_byte_budget=input_byte_budget)
    scopes = review_scopes(payload, source_chunk_bytes=source_chunk_bytes(input_byte_budget, response_reserve_bytes))
    return build_review_plan(specification, scopes, input_byte_budget=input_byte_budget)
