"""Review modes: ``pairs`` (legacy scopes and pair cross checks) and ``compact-v1``.

The mode is a run setting (``--review-mode``), recorded in the driver config and in
the review snapshot.  Every reader dispatches on the frozen plan, never on the
current setting, so a plan keeps the rules it was built with.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

PAIRS = "pairs"
COMPACT = "compact-v1"
MODES = (PAIRS, COMPACT)
DEFAULT_MODE = COMPACT
DEFAULT_INPUT_BYTES = 200_000
DEFAULT_RESERVE_BYTES = 20_000


def plan_mode(plan: Mapping[str, Any]) -> str:
    return COMPACT if plan.get("mode") == COMPACT else PAIRS


def snapshot_mode(payload: Mapping[str, Any]) -> str:
    return COMPACT if payload.get("review_mode") == COMPACT else PAIRS


def source_chunk_bytes(input_byte_budget: int, response_reserve_bytes: int) -> int:
    return max(1, (input_byte_budget - response_reserve_bytes) // 4)


ANSWER_FIELDS = {PAIRS: ("coverage", "findings", "corrections", "required_checks"),
                 COMPACT: ("coverage", "findings", "corrections", "lint_dispositions", "required_checks")}
OUTPUT_VERSION = {PAIRS: "1.0.0", COMPACT: "2.0.0"}
ANSWER_SCHEMA = {PAIRS: "review-part-output.schema.json", COMPACT: "review-part-output-compact.schema.json"}


def part_input(plan: Mapping[str, Any], part: Mapping[str, Any]) -> dict[str, Any]:
    """The exact input a fresh invocation receives for one part."""
    if plan_mode(plan) == COMPACT:
        from tools import review_compact

        return review_compact.part_input(plan, part)
    from tools import review_parts

    return review_parts.part_input(plan, part)


def validate_plan(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    if plan_mode(plan) == COMPACT:
        from tools import review_compact

        return review_compact.validate_compact_plan(plan)
    from tools import review_parts

    return review_parts.validate_review_plan(plan)


def validate_part(plan: Mapping[str, Any], part: Mapping[str, Any], result: Mapping[str, Any], payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every rule a submitted answer must satisfy; ``payload`` is the frozen review snapshot."""
    if plan_mode(plan) == COMPACT:
        from tools import review_compact

        return review_compact.validate_answer(plan, part, result, payload["document"], payload.get("automation"))
    from tools import review_parts

    return review_parts.validate_review_part(plan, part, result)


def additional_parts(plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]], payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    if plan_mode(plan) == COMPACT:
        from tools import review_compact

        return review_compact.additional_parts(plan, payload, results)
    from tools import review_parts

    return review_parts.additional_review_parts(plan, results)


def aggregate(plan: Mapping[str, Any], results: Sequence[Mapping[str, Any]], payload: Mapping[str, Any], *, legacy: bool = False) -> dict[str, Any]:
    if plan_mode(plan) == COMPACT:
        from tools import review_compact

        return review_compact.aggregate(plan, payload, results)
    from tools import review_parts

    return review_parts.aggregate_review_parts(plan, results, legacy=legacy)


def parallel_parts(plan: Mapping[str, Any]) -> bool:
    """Compact parts are independent and may be issued in a batch; legacy parts stay sequential."""
    return plan_mode(plan) == COMPACT


def build_offline_plan(payload: Mapping[str, Any], *, mode: str = PAIRS, review_key: str = "canonical",
                       input_byte_budget: int = DEFAULT_INPUT_BYTES, response_reserve_bytes: int = DEFAULT_RESERVE_BYTES,
                       instructions: str = "offline measurement") -> dict[str, Any]:
    """The plan the driver would freeze for this snapshot payload, without a run (measurements, eval)."""
    from tools.review_parts import build_review_plan, document_index, review_digest, review_scopes

    payload = dict(payload)
    if mode == COMPACT:
        from tools.review_compact import compact_payload, review_policy

        kind = "tc-reviewer" if review_key == "canonical" else "autotest-reviewer"
        payload = compact_payload(payload, review_policy(kind, instructions=instructions, model_id=None))
    specification = {"review_kind": "tc-reviewer" if review_key == "canonical" else "autotest-reviewer",
                     "revision": 1 if review_key == "canonical" else int(review_key[1]),
                     "snapshot_digest": review_digest(payload), "instructions": instructions,
                     "response_reserve_bytes": response_reserve_bytes, "document_index": document_index(payload)}
    if mode == COMPACT:
        from tools.review_compact import build_compact_plan

        return build_compact_plan(specification, payload, input_byte_budget=input_byte_budget)
    scopes = review_scopes(payload, source_chunk_bytes=source_chunk_bytes(input_byte_budget, response_reserve_bytes))
    return build_review_plan(specification, scopes, input_byte_budget=input_byte_budget)
