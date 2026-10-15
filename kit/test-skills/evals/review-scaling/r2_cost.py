r"""Р7, локальность как стоимость: сколько частей и байт уходит на доработку r2 Petclinic.

    python evals/review-scaling/r2_cost.py [--trials 30] [--seed 5]

Строит план compact-v1 кейсов Petclinic (r1), затем правит на месте ожидания случайных
кейсов и строит план r2 с переносом областей, проверенных в r1 (тот же построитель, что у
драйвера).  Печатает по сценариям: частей r2, байт входа r2 и блоков, которых не было в r1.
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import eval_data  # noqa: E402
from tools import review_compact  # noqa: E402
from tools.review_parts import document_index, review_digest  # noqa: E402

POLICY = {"mode": "compact-v1", "role_policy": "canonical-reviewer-v2", "skill_digest": "sha256:" + "a" * 64,
          "instructions_digest": "sha256:" + "b" * 64, "model_id": "claude-fable-5-1"}
SCENARIOS = (("1 кейс, текст ±5 %", 1, 0.05), ("1 кейс, текст ±30 %", 1, 0.30), ("3 кейса, текст ±30 %", 3, 0.30))


def plan(snapshot: dict, carry: dict | None = None) -> dict:
    payload = review_compact.compact_payload(snapshot, POLICY)
    specification = {"review_kind": "tc-reviewer", "revision": 1, "snapshot_digest": review_digest(payload), "instructions": "x",
                     "response_reserve_bytes": 20_000, "document_index": document_index(payload)}
    return review_compact.build_compact_plan(specification, payload, input_byte_budget=200_000, carry=carry)


def carry(parent: dict) -> dict:
    areas = [area["area_id"] for part in parent["parts"] for area in part["areas"]]
    return {"plan": parent, "aggregate": {"checked_scope_ids": areas, "findings": []}, "from_attempt_id": "0" * 31 + "1",
            "policy": POLICY, "parent_policy": POLICY}


def blocks(built: dict) -> set[tuple[str, ...]]:
    return {tuple(block) for part in built["parts"] for block in part["blocks"] if block}


def edit(case: dict, scale: float, rng: random.Random) -> None:
    size = sum(len(step["action"]) + len(step["test_data"]) + sum(len(e["text"]) for e in step["expectations"]) for step in case["steps"])
    delta = int(size * scale)
    expectation = case["steps"][-1]["expectations"][0]
    if rng.random() < 0.5:
        expectation["text"] += " " + "уточнение " * max(1, delta // 11)
    else:
        expectation["text"] = expectation["text"][: max(10, len(expectation["text"]) - delta)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=30)
    parser.add_argument("--seed", type=int, default=5)
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    snapshot = eval_data.petclinic_case_snapshot()
    parent = plan(snapshot)
    before = blocks(parent)
    case_ids = [case["case_id"] for case in snapshot["document"]["test_cases"]]
    report = {"r1": {"parts": len(parent["parts"]), "input_bytes": sum(part["input_byte_count"] for part in parent["parts"]),
                     "blocks": len(before)}, "trials": args.trials, "seed": args.seed, "scenarios": []}
    for label, count, scale in SCENARIOS:
        parts, sizes, shifted = [], [], []
        for _trial in range(args.trials):
            child = copy.deepcopy(snapshot)
            for case_id in rng.sample(case_ids, count):
                edit(next(case for case in child["document"]["test_cases"] if case["case_id"] == case_id), scale, rng)
            built = plan(child, carry(parent))
            parts.append(len(built["parts"]))
            sizes.append(sum(part["input_byte_count"] for part in built["parts"]))
            shifted.append(len(blocks(built) - before))
        report["scenarios"].append({"scenario": label, "parts_median": statistics.median(parts), "parts_min": min(parts), "parts_max": max(parts),
                                    "input_bytes_median": int(statistics.median(sizes)), "input_bytes_max": max(sizes),
                                    "new_blocks_median": statistics.median(shifted), "new_blocks_max": max(shifted)})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
