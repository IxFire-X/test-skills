r"""Р14, запас: на сколько может вырасти документ, чтобы ревью автотестов Petclinic осталось в 6 частях.

    python evals/review-scaling/r14_margin.py [--parts 6] [--trials 5] [--seed 7]

Рост моделируется так: текст ожиданий случайных кейсов удлиняется, пока план ревью
автотестов не превысит ``--parts`` частей (тот же построитель, что у драйвера, бюджет по
умолчанию).  Печатает рост проекции кейсов в процентах в момент, когда частей становится
больше, — минимум и медиану по испытаниям.
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
from tools.review_modes import build_offline_plan  # noqa: E402
from tools.review_projection import automation_case_text  # noqa: E402


def parts(snapshot: dict) -> int:
    return len(build_offline_plan(snapshot, mode="compact-v1", review_key="r1")["parts"])


def view_bytes(document: dict) -> int:
    return sum(len(automation_case_text(case).encode("utf-8")) for case in document["test_cases"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parts", type=int, default=6)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    base = eval_data.petclinic_automation_snapshot()
    start = view_bytes(base["document"])
    rng = random.Random(args.seed)
    growth = []
    for _trial in range(args.trials):
        snapshot = copy.deepcopy(base)
        expectations = [expectation for case in snapshot["document"]["test_cases"] for step in case["steps"] for expectation in step["expectations"]]
        while parts(snapshot) <= args.parts:
            before = view_bytes(snapshot["document"])
            for expectation in rng.sample(expectations, 8):
                expectation["text"] += " уточнение" * 25
            last = before
        growth.append(round(100 * (last - start) / start, 1))
    print(json.dumps({"parts_limit": args.parts, "case_view_bytes": start, "parts_now": parts(base),
                      "growth_percent_min": min(growth), "growth_percent_median": statistics.median(growth), "trials": growth}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
