"""Eval of review modes on seeded defects (review-scaling wave, 2026-10-07).

    python evals/review-scaling/run_eval.py plan  --set E1 --mode compact-v1 --out <dir> [--clean] [--budget N]
    python evals/review-scaling/run_eval.py check --dir <dir> --part part-000001
    python evals/review-scaling/run_eval.py score --dir <dir>

``plan`` applies the seeds of a set to its base snapshot and builds the parts with the
production planner (``tools.review_modes.build_offline_plan``); every part gets the
exact input the driver would send (``.input.md`` text or ``.input.json`` envelope), the
driver's task instructions and task schema.  ``check`` validates one answer with the
production validator and logs the try.  ``score`` binds every answer, aggregates with
the production aggregate and matches findings and corrections to the seeds:

* a seed is found by a finding of at least its minimum severity whose ``related_ids``
  meet the seed ``locus`` (and, when the seed lists ``keywords``, whose message names
  one of them), or — for a mechanical seed — by a correction on its locus;
* every match is listed; BLOCKING/WARNING findings that meet no seed are listed as
  unseeded (candidate false alarms, labelled by hand in the report).
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import eval_data  # noqa: E402

SEVERITY = {"INFO": 0, "WARNING": 1, "BLOCKING": 2}
SETS = {"E1": ("step5-canonical", "canonical"), "E2": ("petclinic-canonical", "canonical"),
        "E3": ("step5-automation", "r1"), "E4": ("petclinic-automation", "r1"),
        "L-2c10d733": ("live-2c10d733", "canonical"), "L-3e852e76": ("live-3e852e76", "canonical"),
        "L-d1834358": ("live-d1834358", "canonical"), "L-9340016c": ("live-9340016c", "canonical")}


# --------------------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------------------

def base_snapshot(name: str) -> dict[str, Any]:
    from tests.live_step5 import review_state

    if name == "step5-canonical":
        return copy.deepcopy(review_state("9340016c", "review-snapshot-canonical")["payload"])
    if name.startswith("live-"):
        return copy.deepcopy(review_state(name.removeprefix("live-"), "review-snapshot-canonical")["payload"])
    if name == "step5-automation":
        return copy.deepcopy(review_state("9340016c", "review-snapshot-r1")["payload"])
    if name == "petclinic-canonical":
        return eval_data.petclinic_case_snapshot()
    if name == "petclinic-automation":
        snapshot = eval_data.petclinic_automation_snapshot()
        if snapshot is None:
            raise SystemExit("the Petclinic class is missing from evals/review-scaling/data")
        return snapshot
    raise SystemExit(f"unknown base {name}")


def _case(document: dict, case_id: str) -> dict:
    return next(case for case in document["test_cases"] if case["case_id"] == case_id)


def _walk(value: Any, path: str) -> tuple[Any, str | int]:
    tokens = [int(token) if token.isdigit() else token for token in path.split("/")]
    for token in tokens[:-1]:
        value = value[token]
    return value, tokens[-1]


def apply_mutation(snapshot: dict, mutation: dict) -> None:
    document = snapshot["document"]
    op = mutation["op"]
    if op == "set":
        parent, key = _walk(_case(document, mutation["case"]), mutation["path"])
        parent[key] = copy.deepcopy(mutation["value"])
    elif op == "replace":
        parent, key = _walk(_case(document, mutation["case"]), mutation["path"])
        if mutation["old"] not in parent[key]:
            raise SystemExit(f"seed text not found: {mutation['old'][:60]!r}")
        parent[key] = parent[key].replace(mutation["old"], mutation["new"])
    elif op == "delete_case":
        document["test_cases"] = [case for case in document["test_cases"] if case["case_id"] != mutation["case"]]
        for index, case in enumerate(document["test_cases"], start=1):
            case["display_order"] = index
    elif op in {"code", "code_regex"}:
        file = next(row for row in snapshot["automation"]["artifacts"]["generated_files"] if row["file_id"] == mutation["file"])
        content = file["content"]
        if op == "code":
            if content.count(mutation["old"]) != 1:
                raise SystemExit(f"seed code must occur once: {mutation['old'][:60]!r} x{content.count(mutation['old'])}")
            content = content.replace(mutation["old"], mutation["new"])
        else:
            content, count = re.subn(mutation["pattern"], lambda _match: mutation["new"], content, count=1)
            if count != 1:
                raise SystemExit(f"seed pattern not found: {mutation['pattern'][:60]!r}")
        file["content"] = content
        # The artifact binds the digest of each file's bytes; a seeded file keeps a consistent one.
        file["content_digest"] = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    else:
        raise SystemExit(f"unknown mutation {op}")


def seeded(set_name: str, *, clean: bool) -> tuple[dict, list[dict], str]:
    base, key = SETS[set_name]
    snapshot = base_snapshot(base)
    seeds_path = HERE / "seeds" / f"{set_name}.json"
    seeds = json.loads(seeds_path.read_text(encoding="utf-8"))["defects"] if seeds_path.is_file() and not clean else []
    for seed in seeds:
        for mutation in seed["mutations"]:
            apply_mutation(snapshot, mutation)
    return snapshot, seeds, key


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------

def _gz(path: Path, value: Any) -> None:
    path.write_bytes(gzip.compress(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"), 9, mtime=0))


def _read_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def _seeds_path(directory: Path) -> Path:
    return directory.parent / f"{directory.name}.seeds.json"


def cmd_plan(args: argparse.Namespace) -> int:
    from tools import pipeline_driver
    from tools.review_modes import build_offline_plan, part_input

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    snapshot, seeds, key = seeded(args.set, clean=args.clean)
    instructions = pipeline_driver._REVIEW_INSTRUCTIONS["canonical" if key == "canonical" else "automation"]
    plan = build_offline_plan(snapshot, mode=args.mode, review_key=key, input_byte_budget=args.budget, instructions=instructions)
    compact = args.mode == "compact-v1"
    if compact:
        from tools.review_compact import compact_payload, review_policy

        snapshot = compact_payload(snapshot, review_policy("tc-reviewer" if key == "canonical" else "autotest-reviewer", instructions=instructions, model_id=None))
    _gz(out / "plan.json.gz", plan)
    _gz(out / "snapshot.json.gz", snapshot)
    # The seeds stay outside the reviewer's directory: a reviewer never sees them.
    _seeds_path(out).write_text(json.dumps(seeds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    schema = out / "answer.schema.json"
    schema.write_text(json.dumps(pipeline_driver._review_schema(args.mode), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    skill = ROOT / "skills" / ("tc-reviewer" if key == "canonical" else "autotest-reviewer") / "SKILL.md"
    tasks = []
    for part in plan["parts"]:
        envelope = part_input(plan, part)
        if compact:
            source = out / f"{part['part_id']}.input.md"
            source.write_text(envelope["text"], encoding="utf-8", newline="\n")
        else:
            source = out / f"{part['part_id']}.input.json"
            source.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        task = {"part_id": part["part_id"], "input": str(source), "output": str(out / f"{part['part_id']}.answer.json"), "schema": str(schema),
                "skill": str(skill), "instructions": pipeline_driver.review_task_instructions(compact=compact, fresh=True),
                "input_bytes": source.stat().st_size, "blocked_reason": part["blocked_reason"]}
        tasks.append(task)
    meta = {"set": args.set, "mode": args.mode, "clean": args.clean, "review_key": key, "budget": args.budget,
            "parts": len(plan["parts"]), "blocked": sum(1 for part in plan["parts"] if part["blocked_reason"]),
            "input_bytes": sum(part["input_byte_count"] for part in plan["parts"]), "tasks": tasks}
    (out / "tasks.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: value for key, value in meta.items() if key != "tasks"}, ensure_ascii=False))
    return 0


def _bound(plan: dict, part: dict, answer: dict, mode: str) -> dict:
    from tools.review_modes import OUTPUT_VERSION, part_input
    from tools.review_parts import review_digest

    return {"schema_version": OUTPUT_VERSION[mode], "plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
            "part_id": part["part_id"], "input_digest": review_digest(part_input(plan, part)), **answer}


def _load(directory: Path) -> tuple[dict, dict, dict]:
    meta = json.loads((directory / "tasks.json").read_text(encoding="utf-8"))
    return meta, _read_gz(directory / "plan.json.gz"), _read_gz(directory / "snapshot.json.gz")


def diagnostics(directory: Path, part_id: str) -> list[dict[str, str]]:
    from tools.review_modes import ANSWER_FIELDS, plan_mode, validate_part

    meta, plan, snapshot = _load(directory)
    part = next(item for item in plan["parts"] if item["part_id"] == part_id)
    path = directory / f"{part_id}.answer.json"
    try:
        answer = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [{"code": "ANSWER_UNREADABLE", "path": "", "message": str(error)}]
    mode = plan_mode(plan)
    if not isinstance(answer, dict) or set(answer) != set(ANSWER_FIELDS[mode]):
        return [{"code": "REVIEW_ASSESSMENT_FIELDS", "path": "", "message": "the answer has exactly: " + ", ".join(ANSWER_FIELDS[mode])}]
    return validate_part(plan, part, _bound(plan, part, answer, meta["mode"]), snapshot)


def cmd_check(args: argparse.Namespace) -> int:
    directory = Path(args.dir)
    rows = diagnostics(directory, args.part)
    log = directory / f"{args.part}.tries.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": time.time(), "accepted": not rows, "codes": sorted({row["code"] for row in rows})}, ensure_ascii=False) + "\n")
    print(json.dumps({"status": "accepted" if not rows else "rejected", "errors": rows[:30]}, ensure_ascii=False, indent=1))
    return 0 if not rows else 1


def _match(seed: dict, finding: dict) -> bool:
    if SEVERITY[finding["severity"]] < SEVERITY[seed.get("min_severity", "WARNING")]:
        return False
    if not set(finding["related_ids"]) & set(seed["locus"]):
        return False
    keywords = seed.get("keywords") or []
    return not keywords or any(word.lower() in finding["message"].lower() for word in keywords)


def cmd_score(args: argparse.Namespace) -> int:
    from tools.review_modes import aggregate

    directory = Path(args.dir)
    meta, plan, snapshot = _load(directory)
    seeds = json.loads(_seeds_path(directory).read_text(encoding="utf-8"))
    results, missing, answer_bytes = [], [], []
    for part in plan["parts"]:
        path = directory / f"{part['part_id']}.answer.json"
        if not path.is_file():
            missing.append(part["part_id"])
            continue
        answer = json.loads(path.read_text(encoding="utf-8"))
        answer_bytes.append(len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))
        results.append(_bound(plan, part, answer, meta["mode"]))
    result = aggregate(plan, results, snapshot)
    findings = result["findings"]
    corrections = result["corrections"]
    rows = []
    matched_findings: set[int] = set()
    for seed in seeds:
        hits = [index for index, finding in enumerate(findings) if _match(seed, finding)]
        fixes = [item for item in corrections if seed.get("mechanical") and set(item["related_ids"]) & set(seed["locus"])]
        matched_findings.update(hits)
        rows.append({"id": seed["id"], "class": seed["class"], "found": bool(hits or fixes),
                     "findings": [{"severity": findings[index]["severity"], "code": findings[index]["code"], "related_ids": findings[index]["related_ids"],
                                   "message": findings[index]["message"]} for index in hits],
                     "corrections": [{"path": item["path"], "after": item["after"]} for item in fixes]})
    seeded_ids = {identifier for seed in seeds for identifier in seed["locus"]}
    unseeded = [finding for index, finding in enumerate(findings) if index not in matched_findings and finding["severity"] in {"BLOCKING", "WARNING"}
                and not set(finding["related_ids"]) & seeded_ids]
    tries = {}
    for part in plan["parts"]:
        log = directory / f"{part['part_id']}.tries.jsonl"
        tries[part["part_id"]] = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.is_file() else []
    report = {"set": meta["set"], "mode": meta["mode"], "clean": meta["clean"], "parts": len(plan["parts"]), "missing_answers": missing,
              "input_bytes": meta["input_bytes"], "answer_bytes": answer_bytes, "answer_bytes_total": sum(answer_bytes),
              "complete": result["complete"], "diagnostics": result["diagnostics"][:20],
              "unchecked": result["unchecked"], "found": sum(row["found"] for row in rows), "seeds": len(seeds), "rows": rows,
              "severity_counts": {name: sum(1 for item in findings if item["severity"] == name) for name in SEVERITY},
              "unseeded_blocking_warning": unseeded, "corrections": [{"path": item["path"], "related_ids": item["related_ids"], "after": item["after"]} for item in corrections],
              "lint_dispositions": result.get("lint_dispositions", []), "tries": tries}
    (directory / "score.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({key: report[key] for key in ("set", "mode", "clean", "parts", "found", "seeds", "complete", "severity_counts", "answer_bytes_total")}, ensure_ascii=False))
    for row in rows:
        print(("FOUND " if row["found"] else "MISSED") + f" {row['id']} {row['class']}")
    for finding in unseeded:
        print(f"UNSEEDED {finding['severity']} {finding['code']} {finding['related_ids']}: {finding['message'][:160]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--set", required=True, choices=sorted(SETS))
    plan.add_argument("--mode", required=True, choices=("pairs", "compact-v1"))
    plan.add_argument("--out", required=True)
    plan.add_argument("--clean", action="store_true")
    plan.add_argument("--budget", type=int, default=200_000)
    check = commands.add_parser("check")
    check.add_argument("--dir", required=True)
    check.add_argument("--part", required=True)
    score = commands.add_parser("score")
    score.add_argument("--dir", required=True)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return {"plan": cmd_plan, "check": cmd_check, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
