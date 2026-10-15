"""Review parts of the live Petclinic runs (2026-10-08) for the wrapper selftest and the model benchmark.

The fixtures beside this file (``fixtures/``) are those of ``tests/fixtures/replay-petclinic-20261008`` of the
package: per review (``canonical`` — test cases, ``r1`` — autotests) the frozen snapshot and run d's answers (Sonnet).
The package's own planner builds the plan from the snapshot; a part's model input is composed exactly as the driver's
process runner composes it; an answer is checked by the answer schema and ``review_compact.validate_answer`` — the
checks a real ``submit`` runs on the part.
"""
from __future__ import annotations

import contextlib
import copy
import functools
import gzip
import json
import sys
from pathlib import Path
from typing import Any, Mapping

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BINDING = ("schema_version", "plan_digest", "snapshot_digest", "part_id", "input_digest")


def use_pack(pack: str | Path) -> Path:
    """Import the package from ``pack`` (a test-skills copy)."""
    root = Path(pack).resolve()
    if not (root / "tools" / "review_compact.py").is_file():
        raise SystemExit(f"{root} is not a test-skills copy (tools/review_compact.py is missing)")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return root


@functools.lru_cache(maxsize=None)
def _load(name: str) -> Any:
    return json.loads(gzip.decompress((FIXTURES / name).read_bytes()).decode("utf-8"))


def _run_d_names_id(code: str, identifier: str) -> bool:
    return f'"{identifier}"' in code or f"'{identifier}'" in code


@functools.lru_cache(maxsize=None)
def _plan(key: str) -> str:
    from unittest import mock

    from tools.review_compact import build_compact_plan

    recorded = _load(f"{key}-snapshot.json.gz")
    specification = {name: value for name, value in recorded["specification"].items() if name != "projection_digest"}
    # The automation review keeps run d's linter, so its parts are those run d's answers were given for.
    with mock.patch("tools.review_lint.names_id", _run_d_names_id) if key != "canonical" else contextlib.nullcontext():
        plan = build_compact_plan(specification, recorded["payload"], input_byte_budget=recorded["input_byte_budget"])
    return json.dumps(plan, ensure_ascii=False)


def plan(key: str) -> dict[str, Any]:
    return {**json.loads(_plan(key)), "additions": [], "unavailable": {}}


def payload(key: str) -> dict[str, Any]:
    return _load(f"{key}-snapshot.json.gz")["payload"]


def reference(key: str, part_id: str) -> dict[str, Any]:
    """Run d's (Sonnet) answer to the part."""
    recorded = _load(f"{key}-answers.json.gz")["d"]
    name = next(name for name in recorded if name.split(".")[-2] == part_id)
    return copy.deepcopy(recorded[name])


def part(key: str, part_id: str) -> dict[str, Any]:
    return next(row for row in plan(key)["parts"] if row["part_id"] == part_id)


def part_text(key: str, part_id: str) -> str:
    from tools.review_compact import part_input

    return part_input(plan(key), part(key, part_id))["text"]


def base_parts(key: str) -> list[tuple[str, int]]:
    """``(part_id, bytes of the part text)`` of the plan's base parts that run d answered."""
    answered = {name.split(".")[-2] for name in _load(f"{key}-answers.json.gz")["d"]}
    return [(row["part_id"], len(part_text(key, row["part_id"]).encode("utf-8"))) for row in plan(key)["parts"] if row["part_id"] in answered]


def schema() -> dict[str, Any]:
    from tools import pipeline_driver

    return pipeline_driver._review_schema("compact-v1")


def task_input(pack: Path, key: str, part_id: str, directory: Path) -> str:
    """The stdin the driver's process runner gives a model for this part."""
    from tools.model_runner import compose_input
    from tools.pipeline_driver import review_task_instructions

    role = "tc-reviewer" if key == "canonical" else "autotest-reviewer"
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / f"replay.review.{key}.{part_id}.input.md"
    source.write_bytes(part_text(key, part_id).encode("utf-8"))
    return compose_input(Path(pack) / "skills" / role / "SKILL.md", review_task_instructions(compact=True, fresh=True, analyst=True), schema(), [source])


def check(key: str, part_id: str, answer: Any) -> list[str]:
    """The codes a real submit would reject the answer with ([] — accepted)."""
    import jsonschema

    from tools import review_compact
    from tools.review_compact import part_input
    from tools.review_modes import COMPACT, output_version
    from tools.review_parts import review_digest

    if not isinstance(answer, dict):
        return ["NOT_AN_OBJECT"]
    errors = sorted(error.message[:120] for error in jsonschema.Draft202012Validator(schema()).iter_errors(answer))
    if errors:
        return ["SCHEMA: " + error for error in errors[:5]]
    the_plan, the_part = plan(key), part(key, part_id)
    assessment = {name: copy.deepcopy(value) for name, value in answer.items() if name not in BINDING}
    bound = {"schema_version": output_version(COMPACT, assessment), "plan_digest": the_plan["digest"], "snapshot_digest": the_plan["snapshot"]["snapshot_digest"],
             "part_id": part_id, "input_digest": review_digest(part_input(the_plan, the_part)), **assessment}
    recorded = payload(key)
    return [row["code"] for row in review_compact.validate_answer(the_plan, the_part, bound, recorded["document"], recorded["automation"])]


def findings(answer: Mapping[str, Any]) -> set[tuple[str, tuple[str, ...]]]:
    """``(code, sorted related_ids)`` of an answer's findings — the unit the benchmark compares."""
    return {(str(row.get("code")), tuple(sorted(row.get("related_ids") or []))) for row in answer.get("findings") or []}
