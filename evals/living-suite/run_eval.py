"""Eval of the living suite (wave 3, 2026-10-08): scenarios 1–6 of the design (section 3.11) on step5.

    python evals/living-suite/run_eval.py prepare --scenario N --out <dir>      # step5 copy, suite, scenario edits
    python evals/living-suite/run_eval.py start --dir <dir> [--mutation]        # suite-update-v1: the first task or the result
    python evals/living-suite/run_eval.py submit --dir <dir> --task-id <id>     # after a model wrote the task's output
    python evals/living-suite/run_eval.py report --dir <dir> --out <json>       # facts of the finished run
    python evals/living-suite/run_eval.py auto --scenario N --out <dir> --json <json>   # scenarios without model tasks (3–6)

The suite is created by a real ``local-pilot-v1 --suite`` run of step5 with the recorded answers of
the live run ``9340016c`` (review parts answered clean), with Maven on the machine (``--java-home``
or ``TEST_SKILLS_JAVA_HOME``).  Scenarios:

1. a requirement changed (AC-7, the product implements it), one added (AC-9, behaviour the product
   already has) and one removed (3.8 and AC-8, hello-world), and a person edited the title of an
   unaffected case — only the affected cases are rebuilt, the person's edit stays;
2. a product class the test refers to is renamed (``Student`` → ``StudentDto``) — the test is
   repaired, the case does not change;
3. the behaviour changed without a requirement change — the test goes to quarantine with a
   question and a bug report draft, the expectation is not touched;
4. an endpoint without a requirement is added — no case changes, the analysts get a question;
5. a check is weakened (``isEqualTo`` → ``isNotNull``) — the tests stay green, the drop of the kill
   ratio reaches the PR description (needs ``--mutation``);
6. a suite of the previous format (a run's bundle without a manifest) is migrated and read by the
   new version of the package.

Scenarios 1 and 2 give model tasks (update, review, automation, repair): a fresh model with the
production SKILL answers each one; the others run to the end without a model.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

DOCS = "docs/requirements/student-controller.md"
CONTROLLER = "src/main/java/net/javaguides/springboot/controller/StudentController.java"
BEAN = "src/main/java/net/javaguides/springboot/bean/Student.java"
GENERATED = "src/test/java/net/javaguides/springboot/controller/StudentControllerPipelineTest.java"
OLD, NEW = "Student Successfully Deleted!", "Student deleted"
OBJECT_BODY = ('        net.javaguides.springboot.bean.Student request = new net.javaguides.springboot.bean.Student(5, "Ivan", "Petrov");\n'
               '        Map<String, Object> body = new ObjectMapper().convertValue(request, new TypeReference<Map<String, Object>>() {});')


def _java(java_home: str | None) -> str:
    home = java_home or os.environ.get("TEST_SKILLS_JAVA_HOME")
    if not home or not (Path(home) / "bin").is_dir():
        raise SystemExit("pass --java-home or set TEST_SKILLS_JAVA_HOME to a JDK 17+")
    os.environ["JAVA_HOME"] = home
    os.environ["PATH"] = str(Path(home) / "bin") + os.pathsep + os.environ.get("PATH", "")
    return home


def _rewrite(path: Path, old: str, new: str, *, regex: bool = False) -> None:
    raw = path.read_bytes().decode("utf-8")
    text = re.sub(old, new, raw, flags=re.S) if regex else raw.replace(old, new)
    if text == raw:
        raise SystemExit(f"nothing to change in {path}: {old}")
    path.write_bytes(text.encode("utf-8"))


def create_suite(out: Path, *, object_body: bool = False, mutation: bool = False, suite: bool = True) -> Path:
    """step5 with a suite written by local-pilot-v1 --suite (recorded answers of 9340016c)."""
    from tests.live_step5 import Replay
    from tests.review_scaling_helpers import clean_compact_answer, part_text

    def answer(task):
        if task.get("review_mode") == "compact-v1":
            return clean_compact_answer(part_text(task))
        if task["stage"].startswith("mutation-triage:"):
            from tests.test_mutation_java import _triage_answer

            return _triage_answer(task)
        if object_body and task["stage"].startswith("tc-to-autotest:"):
            value = next(value for label, value in replay.recorded.items() if label.startswith("tc-to-autotest."))
            files = []
            for row in value["generated_files"]:
                content = row["content"].replace('        Map<String, Object> body = student(5, "Ivan", "Petrov");', OBJECT_BODY, 1)
                files.append({**row, "content": content, "content_digest": "sha256:" + __import__("hashlib").sha256(content.encode("utf-8")).hexdigest()})
            return {**value, "generated_files": files}
        return None

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    replay = Replay("9340016c", out, override=answer)
    replay.review_mode = None
    (replay.project / GENERATED).unlink()
    if mutation:
        skillsrc = replay.project / ".skillsrc"
        text = skillsrc.read_text(encoding="utf-8").replace("schema_version: 5.0.0", "schema_version: 5.2.0", 1)
        skillsrc.write_text(text + "mutation:\n  enabled: true\n  threads: 2\n", encoding="utf-8")
    flags = (["--suite"] if suite else []) + (["--mutation"] if mutation else [])
    _code, done = replay.drive(replay.start_with(*flags)[1])
    if done["result"].get("verification") != "PASS":
        raise SystemExit(f"suite creation did not pass: {done['result']}")
    (out / "creation.json").write_text(json.dumps(done["result"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return replay.project


def prepare(scenario: int, out: Path) -> dict[str, Any]:
    if scenario == 6:
        project = create_suite(out, suite=False)
        creation = json.loads((out / "creation.json").read_text(encoding="utf-8"))
        suite = project / "test-cases"
        suite.mkdir()
        for path in Path(creation["paths"]["candidate_bundle"]).glob("TCDOC-*"):
            shutil.copy2(path, suite / path.name)
        return {"scenario": 6, "project": str(project)}
    project = create_suite(out, object_body=scenario == 2, mutation=scenario == 5)
    if scenario == 1:
        canonical = project / "test-cases" / "test-cases.json"
        document = json.loads(canonical.read_text(encoding="utf-8"))
        document["test_cases"][0]["title"] += " (уточнено аналитиком)"
        canonical.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        doc = project / DOCS
        _rewrite(doc, OLD, NEW)
        _rewrite(project / CONTROLLER, OLD, NEW)
        _rewrite(doc, r"### 3\.8\. GET `/hello-world`.*?(?=\r?\n## 4\.)", "", regex=True)
        _rewrite(doc, r"- AC-8:[^\n]*\n", "", regex=True)
        _rewrite(doc, "- AC-7:", "- AC-9: DELETE `/students/abc/delete` с нечисловым идентификатором возвращает `400 Bad Request`.\r\n- AC-7:")
    elif scenario == 2:
        for path in (project / BEAN, project / CONTROLLER):
            _rewrite(path, r"\bStudent\b(?! Successfully| deleted| removed)", "StudentDto", regex=True)
        (project / BEAN).rename(project / BEAN.replace("Student.java", "StudentDto.java"))
    elif scenario == 3:
        _rewrite(project / CONTROLLER, OLD, "Student removed")
    elif scenario == 4:
        controller = project / CONTROLLER
        text = controller.read_bytes().decode("utf-8")
        closing = text.rstrip().rfind("}")
        controller.write_bytes((text[:closing] + '    @GetMapping("students/count")\r\n    public int countStudents() {\r\n        return 4;\r\n    }\r\n}\r\n').encode("utf-8"))
    elif scenario == 5:
        test = project / GENERATED
        _rewrite(test, '.as("ASSERT-B1-001-01-1-3").isEqualTo(student(1, "Ramseh", "Mishra"))', '.as("ASSERT-B1-001-01-1-3").isNotNull()')
        _rewrite(test, '.as("ASSERT-B1-001-01-1-2").isEqualTo(APPLICATION_JSON)', '.as("ASSERT-B1-001-01-1-2").isNotEqualTo("text/xml")')
        _rewrite(test, r'\.as\("ASSERT-B1-001-01-1-4"\)\s*\.isEqualTo\("[^\n]*?"\);', '.as("ASSERT-B1-001-01-1-4").isNotNull();', regex=True)
    return {"scenario": scenario, "project": str(project)}


def _driver(*argv: str) -> dict[str, Any]:
    from tools import pipeline_driver

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        pipeline_driver.main(list(argv))
    return json.loads(buffer.getvalue())


def cmd_prepare(args: argparse.Namespace) -> int:
    _java(args.java_home)
    started = time.monotonic()
    info = prepare(args.scenario, Path(args.out).resolve())
    info["prepare_seconds"] = round(time.monotonic() - started, 1)
    (Path(args.out) / "scenario.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(info, ensure_ascii=False))
    return 0


def _save(directory: Path, payload: dict[str, Any]) -> dict[str, Any]:
    (directory / "last.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if payload.get("run_id"):
        info = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
        info["run_id"] = payload["run_id"]
        (directory / "scenario.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload


def cmd_start(args: argparse.Namespace) -> int:
    _java(args.java_home)
    directory = Path(args.dir).resolve()
    info = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    info["started"] = time.time()
    (directory / "scenario.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    payload = _driver("next", "--project", info["project"], "--profile", "suite-update-v1", "--model-id", args.model_id, "--reviewer-isolation", "fresh",
                      "--host-cli", "Claude Code", "--host-cli-version", "desktop", *(["--mutation"] if args.mutation else []), "--max-tasks", "4")
    print(json.dumps(_save(directory, payload), ensure_ascii=False, indent=2))
    return 0


def cmd_submit(args: argparse.Namespace) -> int:
    _java(args.java_home)
    directory = Path(args.dir).resolve()
    info = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    payload = _driver("submit", "--project", info["project"], "--run", info["run_id"], "--task-id", args.task_id)
    print(json.dumps(_save(directory, payload), ensure_ascii=False, indent=2))
    return 0


def report(directory: Path) -> dict[str, Any]:
    from tools.suite_manifest import read_suite, verify

    info = json.loads((directory / "scenario.json").read_text(encoding="utf-8"))
    project = Path(info["project"])
    last = json.loads((directory / "last.json").read_text(encoding="utf-8"))
    result = last.get("result") or {}
    manifest = read_suite(project, "test-cases/")
    facts: dict[str, Any] = {"scenario": info["scenario"], "outcome": result.get("outcome"), "counts": result.get("counts"), "reviews": result.get("reviews")}
    if manifest is not None:
        facts["manifest"] = {"format_version": manifest["format_version"], "cases": len(manifest["cases"]),
                             "by_status": {status: sum(row["status"] == status for row in manifest["cases"]) for status in ("ACTIVE", "QUARANTINED", "RETIRED")},
                             "quarantine": [{"case_id": row["case_id"], **row["quarantine"]} for row in manifest["cases"] if row["quarantine"]],
                             "history": manifest["history"], "edited": verify(project, manifest)}
    if result.get("paths"):
        description = Path(result["paths"]["pr_description"]).read_text(encoding="utf-8")
        facts["pr_description"] = {"bytes": len(description.encode("utf-8")), "sections": re.findall(r"^## (.+)$", description, re.M)}
        facts["patch_bytes"] = Path(result["paths"]["patch"]).stat().st_size
    if info.get("started"):
        facts["seconds_from_start"] = round(time.time() - info["started"], 1)
    return facts


def cmd_report(args: argparse.Namespace) -> int:
    facts = report(Path(args.dir).resolve())
    text = json.dumps(facts, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    """Scenarios 3–6: prepare and run to the end; a model task is an error (they need none)."""
    _java(args.java_home)
    directory = Path(args.out).resolve()
    started = time.monotonic()
    info = prepare(args.scenario, directory)
    info["prepare_seconds"] = round(time.monotonic() - started, 1)
    (directory / "scenario.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    if args.scenario == 6:
        from tools.suite import main as suite_main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            suite_main(["migrate", "--project", info["project"]])
        migration = json.loads(buffer.getvalue())
        with contextlib.redirect_stdout(buffer := io.StringIO()):
            suite_main(["status", "--project", info["project"]])
        facts = {"scenario": 6, "migration": migration, "status": json.loads(buffer.getvalue())}
    else:
        info["started"] = time.time()
        (directory / "scenario.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        payload = _driver("next", "--project", info["project"], "--profile", "suite-update-v1", "--model-id", "none", "--reviewer-isolation", "fresh",
                          *(["--mutation"] if args.scenario == 5 else []))
        _save(directory, payload)
        if payload.get("action") != "done":
            raise SystemExit(f"scenario {args.scenario} asked for a model: {payload.get('stage')}")
        facts = report(directory)
        if args.scenario == 4:
            from tools.suite_impact import impact

            facts["impact_questions"] = [row["question"] for row in impact(Path(info["project"]))["analyst_questions"]]
    facts["prepare_seconds"] = info["prepare_seconds"]
    facts["update_seconds"] = round(time.monotonic() - started - info["prepare_seconds"], 1)
    text = json.dumps(facts, ensure_ascii=False, indent=2) + "\n"
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "start", "submit", "report", "auto"):
        command = commands.add_parser(name)
        command.add_argument("--java-home")
        if name in {"prepare", "auto"}:
            command.add_argument("--scenario", type=int, choices=range(1, 7), required=True)
            command.add_argument("--out", required=True)
        if name in {"start", "submit", "report"}:
            command.add_argument("--dir", required=True)
        if name == "start":
            command.add_argument("--mutation", action="store_true")
            command.add_argument("--model-id", default="claude-sonnet-5-5")
        if name == "submit":
            command.add_argument("--task-id", required=True)
        if name == "report":
            command.add_argument("--out")
        if name == "auto":
            command.add_argument("--json")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return {"prepare": cmd_prepare, "start": cmd_start, "submit": cmd_submit, "report": cmd_report, "auto": cmd_auto}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
