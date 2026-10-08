"""Offline replay of the live Petclinic runs of 2026-10-08 (runs b and d, package ``10ae352``).

``tests/fixtures/replay-petclinic-20261008`` keeps, per review (``canonical`` — test cases, ``r1`` — autotests), the
frozen review snapshot payload with the plan specification, the texts of the plan's base and additional parts as the
run had them, and the real answers of every part (run d; the case review's part 2 also as run b answered it — that
answer asked to check every source requirement).  ``automation-output`` is the accepted ``tc-to-autotest:r1`` answer.

No driver and no model: the current planner builds the plan from the snapshot and the recorded answers are bound to
it (plan, snapshot and part input digests — the answers keep their content), so every planner decision of the live
runs can be asserted on real data.  Rebuild: ``export_replay.py`` beside the fixtures, run from the run's own package
copy (``docs/superpowers/plans/2026-10-08-live-fixes.md``, R1).
"""
from __future__ import annotations

import contextlib
import copy
import functools
import gzip
import json
from pathlib import Path
from typing import Any, Mapping

BINDING = ("schema_version", "plan_digest", "snapshot_digest", "part_id", "input_digest")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "replay-petclinic-20261008"


@functools.lru_cache(maxsize=None)
def _load(name: str) -> Any:
    return json.loads(gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8"))


def load(name: str) -> Any:
    """A deep copy of one fixture file (``canonical-snapshot.json.gz`` …)."""
    return copy.deepcopy(_load(name))


def snapshot(key: str) -> dict[str, Any]:
    """The recorded snapshot of review ``key`` (``canonical`` or ``r1``)."""
    return load(f"{key}-snapshot.json.gz")


def _run_d_names_id(code: str, identifier: str) -> bool:
    """The ASSERT ID search of run d's package (``10ae352``): the exact quoted ID only."""
    return f'"{identifier}"' in code or f"'{identifier}'" in code


@functools.lru_cache(maxsize=None)
def _plan(key: str, run_lint: bool) -> str:
    from unittest import mock

    from tools.review_compact import build_compact_plan

    recorded = _load(f"{key}-snapshot.json.gz")
    specification = {name: value for name, value in recorded["specification"].items() if name != "projection_digest"}
    with mock.patch("tools.review_lint.names_id", _run_d_names_id) if run_lint else contextlib.nullcontext():
        plan = build_compact_plan(specification, recorded["payload"], input_byte_budget=recorded["input_byte_budget"])
    return json.dumps(plan, ensure_ascii=False)


def plan(key: str, *, run_lint: bool = False) -> dict[str, Any]:
    """The plan the current code builds from the recorded snapshot (without additions).  ``run_lint`` keeps the
    automation linter of run d, so the plan has the parts run d's answers were given for (the current linter raises
    fewer suspicions, and an automation plan packs its parts by size)."""
    return {**json.loads(_plan(key, run_lint)), "additions": [], "unavailable": {}}


def payload(key: str) -> dict[str, Any]:
    return load(f"{key}-snapshot.json.gz")["payload"]


def bind(the_plan: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    """A recorded answer bound to ``the_plan`` (same part ID): only the digests change."""
    from tools.review_compact import part_input
    from tools.review_modes import COMPACT, output_version
    from tools.review_parts import review_digest

    part = next(part for part in [*the_plan["parts"], *the_plan.get("additions", [])] if part["part_id"] == answer["part_id"])
    assessment = {key: copy.deepcopy(value) for key, value in answer.items() if key not in BINDING}
    # The binding pilot_state.review_part_diagnostics gives a submitted answer.
    return {"schema_version": output_version(COMPACT, assessment), "plan_digest": the_plan["digest"],
            "snapshot_digest": the_plan["snapshot"]["snapshot_digest"], "part_id": answer["part_id"],
            "input_digest": review_digest(part_input(the_plan, part)), **assessment}


def answers(key: str, *, base_only: bool = True, part_2: str = "d") -> list[dict[str, Any]]:
    """Recorded answers of review ``key`` in part order; ``part_2="b"`` swaps in run b's answer to case review part 2."""
    recorded = load(f"{key}-answers.json.gz")
    # A model answer carries no binding: the driver adds part and digests on submit.
    rows = [{**value, "part_id": name.split(".")[-2]} for name, value in sorted(recorded["d"].items())]
    if part_2 == "b":
        rows = [{**recorded["b_part_000002"], "part_id": "part-000002"} if row["part_id"] == "part-000002" else row for row in rows]
    if base_only:
        count = len(_load(f"{key}-snapshot.json.gz")["base_part_texts"])
        rows = [row for row in rows if int(row["part_id"].split("-")[1]) <= count]
    return rows
