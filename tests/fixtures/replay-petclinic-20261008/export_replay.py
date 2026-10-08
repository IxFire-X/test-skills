"""Export replay fixtures of the Petclinic runs b and d (run from the run d package copy: cwd = its .tools/test-skills).

usage: python export_replay.py <out_dir>
"""
import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from tools.pilot_state import read_attempt_receipt, read_reviewer_session_ledger  # noqa: E402

D = {"project": r"D:\AI-Projects\live\petclinic-20261008d", "run": "0f779fd621bf43ed833bc5eab67d1fbc", "attempt": "b14327e246b345bcbeada1a79ee201ca"}
B = {"project": r"D:\AI-Projects\live\petclinic-20261008b", "run": "8fb318032d9b45a0ba822d81851cf41d", "attempt": "d3416c8c2b084280968f63569a97a7fa"}


def norm(text: str, side) -> str:
    project = side["project"]
    for value in (project.replace("\\", "\\\\"), project, project.replace("\\", "/")):
        text = text.replace(value, "<PROJECT>")
    return text


def dump(out: Path, name: str, value, side) -> None:
    text = norm(json.dumps(value, ensure_ascii=False, sort_keys=True), side)
    data = text.encode("utf-8")
    with open(out / name, "wb") as handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=9) as gz:
            gz.write(data)
    print(name, len(data), (out / name).stat().st_size)


def outputs(side, pattern: str) -> dict:
    directory = Path(side["project"]) / ".pilot-runs" / (side["run"] + ".driver") / "outputs"
    return {path.name.split(".", 1)[1]: json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob(side["attempt"][:8] + pattern))}


def main() -> int:
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    run_root = Path(D["project"]) / ".pilot-runs" / D["run"]
    result = {}
    for key in ("canonical", "r1"):
        snapshot = read_attempt_receipt(run_root, D["attempt"], f"review-snapshot-{key}", "ARTIFACT_READ_BACK")["record"]
        plan = read_attempt_receipt(run_root, D["attempt"], f"review-plan-{key}", "ARTIFACT_READ_BACK")["record"]["plan"]
        ledger = read_reviewer_session_ledger(run_root, D["attempt"], review_key=key)
        additions = [event["part"] for event in ledger["events"] if event["event_type"] == "REVIEW_CHECK_ADDED"]
        spec = {k: v for k, v in plan["snapshot"].items()}
        dump(out, f"{key}-snapshot.json.gz", {"payload": snapshot["payload"], "snapshot_digest": snapshot["digest"], "specification": spec,
                                              "input_byte_budget": plan["input_byte_budget"], "plan_digest": plan["digest"],
                                              "base_part_texts": [part["text"] for part in plan["parts"]],
                                              "addition_texts": [part["text"] for part in additions],
                                              "addition_checks": [part.get("requested_check") for part in additions]}, D)
        result[key] = (len(plan["parts"]), len(additions))
    answers = outputs(D, ".review.canonical.part-*.json")
    b_two = outputs(B, ".review.canonical.part-000002.json")
    dump(out, "canonical-answers.json.gz", {"d": answers, "b_part_000002": b_two["review.canonical.part-000002.json"]}, D)
    dump(out, "r1-answers.json.gz", {"d": outputs(D, ".review.r1.part-*.json")}, D)
    dump(out, "automation-output.json.gz", outputs(D, ".tc-to-autotest.r1.json"), D)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
