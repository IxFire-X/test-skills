r"""Р7, локальность блоков: сколько блоков меняет вставка или удаление одного кейса.

    python evals/review-scaling/locality.py [--trials 300] [--seed 1]

Строит настоящие планы compact-v1 (тот же построитель, что у драйвера) для случайных
документов до 200 кейсов, вставляет или удаляет один кейс и считает изменившиеся блоки
как max(|старые \ новые|, |новые \ старые|).  Печатает долю случаев больше двух блоков.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tests.review_scaling_helpers import synthetic_document, synthetic_snapshot  # noqa: E402
from tools.review_modes import build_offline_plan  # noqa: E402


def blocks(document: dict, budget: int) -> set[tuple[str, ...]]:
    plan = build_offline_plan(synthetic_snapshot(document), mode="compact-v1", input_byte_budget=budget, response_reserve_bytes=2_000)
    return {tuple(block) for part in plan["parts"] for block in part["blocks"] if block}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    rng = random.Random(args.seed)
    counts: dict[int, int] = {}
    multi = 0
    for _trial in range(args.trials):
        count = rng.randint(2, 200)
        sizes = [rng.choice([rng.randint(50, 1500), rng.randint(1500, 6000)]) for _ in range(count)]
        budget = rng.randint(40_000, 160_000)
        base = synthetic_document(sizes, seed=rng.getrandbits(32))
        before = blocks(base, budget)
        if len(before) > 1:
            multi += 1
        edited = synthetic_document(sizes, seed=0, ids=[case["case_id"] for case in base["test_cases"]])
        if rng.random() < 0.5:
            position = rng.randint(0, count)
            extra = synthetic_document([rng.randint(50, 6000)], seed=rng.getrandbits(32))["test_cases"][0]
            edited["test_cases"].insert(position, extra)
        else:
            del edited["test_cases"][rng.randrange(count)]
        after = blocks(edited, budget)
        changed = max(len(before - after), len(after - before))
        counts[changed] = counts.get(changed, 0) + 1
    over = sum(value for key, value in counts.items() if key > 2)
    report = {"trials": args.trials, "multi_block_documents": multi, "changed_blocks": dict(sorted(counts.items())),
              "more_than_two": over, "share_more_than_two": round(over / args.trials, 4)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
