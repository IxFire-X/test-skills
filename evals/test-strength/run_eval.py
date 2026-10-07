"""Eval of the test strength wave (wave 2, 2026-10-07): mutations, survivor triage.

    python evals/test-strength/run_eval.py petclinic --project <clone> --out <dir> [--java-home <jdk>]
    python evals/test-strength/run_eval.py triage-plan --seeds seeds/triage-step5.json --out <dir> --java-home <jdk>
    python evals/test-strength/run_eval.py triage-check --dir <dir> --label triage-01 --answer <answer.json>
    python evals/test-strength/run_eval.py triage-score --dir <dir> --answers <dir with triage-NN.json> --model <name> [--json out.json]

``petclinic`` runs the production MUTATION stage (``tools.mutation.measure``) on the
September Petclinic class ``b7733d39`` compiled in a Petclinic clone outside the
archive: the class is written into the clone, Maven runs it once (the authoritative
report: 63 PASS, 1 FAIL expected), then PIT challenges the passing methods.  It gives
W2-Р15 (PIT with JUnit 6), W2-Р4 (stage time) and W2-Р3 (FAIL on a real project).
The result (facts, summary, the project snapshot before and after) is written
compressed to ``--out``.

``triage-plan`` builds the seeded step5 scenario (W2-Р6): product edits and a weakened
test from ``seeds/triage-step5.json`` on a copy of step5, a driver run of ``9340016c``
with ``--mutation`` (review parts answered clean) up to the survivor triage tasks; the
task inputs, the mutation receipt and the seeds go to ``--out``.  A fresh subagent with
the production SKILL answers each task; ``triage-check`` validates an answer with the
production check, ``triage-score`` matches decisions to the seeds.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals" / "review-scaling"))

import eval_data  # noqa: E402


def _gz(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress((json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"), mtime=0))


def _surefire_report(project: Path, automation: dict[str, Any], runner: Any, timeout: int) -> dict[str, Any]:
    """Run the class once with the project's Maven wrapper and build the authoritative report the stage reads."""
    artifacts = automation["artifacts"]
    [generated] = artifacts["generated_files"]
    target = project / generated["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(generated["content"].encode("utf-8"))
    class_fqn = artifacts["generated_symbols"][0]["locator"]["class_fqn"]
    wrapper = project / ("mvnw.cmd" if os.name == "nt" else "mvnw")
    argv = [str(wrapper), f"-Dtest={class_fqn.rsplit('.', 1)[-1]}", "-Dsurefire.failIfNoSpecifiedTests=false", "-B", "-ntp", "test"]
    started = time.monotonic()
    outcome = runner(argv, project, None, timeout=timeout)
    seconds = round(time.monotonic() - started, 1)
    report_path = project / "target" / "surefire-reports" / f"TEST-{class_fqn}.xml"
    root = ET.parse(report_path).getroot()
    status = {}
    for case in root.iter("testcase"):
        status[case.get("name")] = "FAILED" if case.find("failure") is not None or case.find("error") is not None else "PASSED"
    evidence = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"], "status": status.get(row["locator"]["method_name"], "MISSING")}
                for row in artifacts["generated_symbols"]]
    verdict = "PASS" if all(row["status"] == "PASSED" for row in evidence) else "FAIL"
    return {"verdict": verdict, "evidence_authoritative": True, "target": {"language": "java"}, "execution_evidence": evidence,
            "execution": {"adapter_id": "maven-wrapper:selected-symbols-v1", "executable_path": str(wrapper), "cwd": str(project), "argv": argv, "build_profile": "default"},
            "maven": {"exit_code": getattr(outcome, "exit_code", None), "seconds": seconds,
                      "tests": int(root.get("tests", 0)), "failures": int(root.get("failures", 0)), "errors": int(root.get("errors", 0))}}


def cmd_petclinic(args: argparse.Namespace) -> int:
    from tools import mutation
    from tools.run_tests import run_subprocess

    if args.java_home:
        os.environ["JAVA_HOME"] = args.java_home
        os.environ["PATH"] = str(Path(args.java_home) / "bin") + os.pathsep + os.environ.get("PATH", "")
    project = Path(args.project).resolve()
    snapshot = eval_data.petclinic_automation_snapshot()
    automation, document = snapshot["automation"], snapshot["document"]
    report = _surefire_report(project, automation, run_subprocess, timeout=1800)
    settings = {**mutation.DEFAULTS, "threads": args.threads, "timeout_seconds": args.timeout}
    workdir = Path(args.out) / "work"
    facts = mutation.measure(mutation.StageInputs(project=project, workdir=workdir, report=report, automation=automation, document=document, settings=settings))
    result = {"project": str(project), "maven": report["maven"], "verdict": report["verdict"],
              "failing": [row["symbol_id"] for row in report["execution_evidence"] if row["status"] != "PASSED"],
              "summary": mutation.summary(facts), "facts": facts}
    _gz(Path(args.out) / "petclinic-mutation.json.gz", result)
    if (workdir / "report" / "mutations.xml").is_file():
        (Path(args.out) / "petclinic-mutations.xml.gz").write_bytes(gzip.compress((workdir / "report" / "mutations.xml").read_bytes(), mtime=0))
    print(json.dumps({"maven": report["maven"], "verdict": report["verdict"], "failing": result["failing"], "summary": result["summary"],
                      "durations_ms": facts["durations_ms"], "excluded": facts["excluded_methods"], "target_classes": facts["target_classes"],
                      "inventory_unchanged": facts["project_inventory"]["unchanged"]}, ensure_ascii=False, indent=2))
    return 0


def _apply(text: str, edit: dict[str, Any]) -> str:
    if text.count(edit["find"]) != 1:
        raise SystemExit(f"seed {edit['id']}: the anchor text must occur once")
    return text.replace(edit["find"], edit["replace"])


def cmd_triage_plan(args: argparse.Namespace) -> int:
    from tests.live_step5 import Replay
    from tests.review_scaling_helpers import clean_compact_answer, part_text
    from tools.pilot_state import read_mutation_receipt_if_present

    if args.java_home:
        os.environ["JAVA_HOME"] = args.java_home
        os.environ["PATH"] = str(Path(args.java_home) / "bin") + os.pathsep + os.environ.get("PATH", "")
    seeds = json.loads(Path(args.seeds).read_text(encoding="utf-8"))
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    def answer(task):
        if task.get("review_mode") == "compact-v1":
            return clean_compact_answer(part_text(task))
        if task["stage"].startswith("tc-to-autotest:"):
            value = next(value for label, value in replay.recorded.items() if label.startswith("tc-to-autotest."))
            files = []
            for row in value["generated_files"]:
                content = row["content"]
                for edit in seeds["test_edits"]:
                    content = _apply(content, edit)
                files.append({**row, "content": content})
            return {**value, "generated_files": files}
        return None

    replay = Replay("9340016c", out, override=answer)
    replay.review_mode = None
    (replay.project / "src/test/java/net/javaguides/springboot/controller/StudentControllerPipelineTest.java").unlink()
    for edit in seeds["product_edits"]:
        target = replay.project / edit["path"]
        if "create" in edit:
            target.write_text(edit["create"], encoding="utf-8", newline="\n")
        else:
            target.write_text(_apply(target.read_text(encoding="utf-8"), edit), encoding="utf-8", newline="\n")
    skillsrc = replay.project / ".skillsrc"
    skillsrc.write_text(skillsrc.read_text(encoding="utf-8") + "mutation:\n  enabled: true\n  threads: 2\n", encoding="utf-8")
    code, payload = replay.start_with("--mutation", "--max-tasks", "8")
    code, task = replay.drive(payload, until=lambda item: str(item.get("stage", "")).startswith("mutation-triage:") or item.get("action") == "batch")
    tasks = task["tasks"] if task.get("action") == "batch" else [task]
    run_root = replay.run_root(tasks[0])
    receipt = read_mutation_receipt_if_present(run_root, tasks[0]["attempt_id"])
    plan = {"seeds": str(Path(args.seeds).resolve()), "project": str(replay.project), "run_root": str(run_root), "attempt_id": tasks[0]["attempt_id"],
            "tasks": [{"task_id": item["task_id"], "label": item["triage_label"], "group_ids": item["group_ids"], "input_path": item["inputs"][0],
                       "skill_path": item["skill_path"], "schema_path": item["schema_path"], "instructions": item["instructions"]} for item in tasks],
            "summary": {"killed": receipt["totals"]["killed"], "survived": receipt["totals"]["survived"], "groups": len(receipt["survivor_groups"])}}
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


def _plan(directory: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    from tools.pilot_state import read_effective_canonical

    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    receipt = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
    document = read_effective_canonical(Path(plan["run_root"]), plan["attempt_id"])["document"]
    return plan, receipt, document


def cmd_triage_check(args: argparse.Namespace) -> int:
    from tools.mutation_triage import validate

    plan, receipt, document = _plan(Path(args.dir))
    task = next(item for item in plan["tasks"] if item["label"] == args.label)
    answer = json.loads(Path(args.answer).read_text(encoding="utf-8"))
    rows = validate({"group_ids": task["group_ids"], "text": Path(task["input_path"]).read_text(encoding="utf-8")}, answer, receipt, document)
    log = Path(args.dir) / "checks.jsonl"
    with log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"label": args.label, "answer": str(args.answer), "accepted": not rows, "errors": rows}, ensure_ascii=False) + "\n")
    print(json.dumps({"accepted": not rows, "errors": rows}, ensure_ascii=False, indent=2))
    return 0 if not rows else 1


def cmd_triage_score(args: argparse.Namespace) -> int:
    from tools.mutation_triage import decision_rows, product_path

    directory = Path(args.dir)
    plan, receipt, _document = _plan(directory)
    seeds = json.loads(Path(plan["seeds"]).read_text(encoding="utf-8"))
    answers = [json.loads((Path(args.answers) / f"{task['label']}.json").read_text(encoding="utf-8")) for task in plan["tasks"]]
    rows = {row["group_id"]: row for row in decision_rows(answers)}
    groups = {group["group_id"]: group for group in receipt["survivor_groups"]}
    project = Path(plan["project"])

    def line_text(group: dict[str, Any]) -> str:
        path = project / product_path(group)
        lines = path.read_text(encoding="utf-8").split("\n") if path.is_file() else []
        return lines[group["line"] - 1] if 0 < group["line"] <= len(lines) else ""

    matched, scored = set(), []
    for seed in seeds["expected"]:
        candidates = [group for group in groups.values() if group["class"] == seed["class"] and group["method"] == seed["method"]
                      and seed.get("line_contains", "") in line_text(group)]
        decisions = [rows[group["group_id"]]["decision"] for group in candidates if group["group_id"] in rows]
        correct = bool(candidates) and len(decisions) == len(candidates) and all(decision in seed["decisions"] for decision in decisions)
        if correct and "case_id" in seed:
            correct = all(rows[group["group_id"]].get("proposal", {}).get("case_id") == seed["case_id"] for group in candidates)
        matched.update(group["group_id"] for group in candidates)
        scored.append({"seed": seed["seed"], "expected": seed["decisions"], "groups": [group["group_id"] for group in candidates], "decisions": decisions,
                       "correct": correct, "answers": [rows.get(group["group_id"]) for group in candidates]})
    unseeded = [{"group_id": group_id, "method": groups[group_id]["method"], "line": groups[group_id]["line"], "decision": rows[group_id]["decision"],
                 "rationale": rows[group_id]["rationale"]} for group_id in sorted(set(rows) - matched)]
    report = {"model": args.model, "correct": sum(row["correct"] for row in scored), "seeds": len(scored), "scored": scored, "unseeded": unseeded}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    petclinic = commands.add_parser("petclinic")
    petclinic.add_argument("--project", required=True)
    petclinic.add_argument("--out", required=True)
    petclinic.add_argument("--java-home")
    petclinic.add_argument("--threads", type=int, default=4)
    petclinic.add_argument("--timeout", type=int, default=3600)
    plan = commands.add_parser("triage-plan")
    plan.add_argument("--seeds", default=str(HERE / "seeds" / "triage-step5.json"))
    plan.add_argument("--out", required=True)
    plan.add_argument("--java-home")
    check = commands.add_parser("triage-check")
    check.add_argument("--dir", required=True)
    check.add_argument("--label", required=True)
    check.add_argument("--answer", required=True)
    score = commands.add_parser("triage-score")
    score.add_argument("--dir", required=True)
    score.add_argument("--answers", required=True)
    score.add_argument("--model", default="unknown")
    score.add_argument("--json")
    args = parser.parse_args(argv)
    commands_by_name = {"petclinic": cmd_petclinic, "triage-plan": cmd_triage_plan, "triage-check": cmd_triage_check, "triage-score": cmd_triage_score}
    return commands_by_name[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
