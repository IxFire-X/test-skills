"""W3-Р9: is an update cheaper than a full run? Petclinic, one requirement changed, offline.

    python evals/living-suite/cost.py [--requirement O05] [--out results/<date>/petclinic-update-cost.json]

The Petclinic example document (``docs/examples/petclinic-owner-lifecycle/cases.json``, the base of wave 1) is the suite.  A change of one
requirement (default ``O05``) affects, through source requirement → canonical requirements →
cases, a set of cases; ``suite-update-v1`` reviews only those (``subset_document``).  The plan of
that review — parts and input bytes — is compared with the full compact-v1 plan of the document
(the baseline of wave 1: 11 parts, 1 853 124 bytes).  Two cuts of the specification:

* with ``.skillsrc`` ``requirements.id_pattern = [OPVX]\\d\\d\\.`` — the changed requirement is its
  own source requirement;
* without it — the built-in split puts every ``O…`` paragraph into one source requirement of the
  section «Владельцы», so a change of one paragraph affects every case of the section.

No model runs; the numbers are bytes of the review input the driver would send.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals" / "review-scaling"))

_ID = re.compile(r"^\s*(?:#+\s*)?([OPVX]\d\d)\.", re.M)
SECTION = {"O": "Владельцы", "P": "Питомцы", "V": "Визиты", "X": "Сквозные связи"}


def _plan(document: dict) -> dict:
    import eval_data
    from tools import pipeline_driver
    from tools.review_modes import build_offline_plan, part_input

    snapshot = eval_data.petclinic_case_snapshot(document)
    plan = build_offline_plan(snapshot, mode="compact-v1", review_key="canonical", instructions=pipeline_driver._REVIEW_INSTRUCTIONS["canonical"])
    return {"parts": len(plan["parts"]), "input_bytes": sum(len(part_input(plan, part)["text"].encode("utf-8")) for part in plan["parts"]),
            "max_part_bytes": max(len(part_input(plan, part)["text"].encode("utf-8")) for part in plan["parts"])}


def affected(document: dict, requirement: str, *, with_pattern: bool) -> list[str]:
    """Cases linked to the source requirements a change of ``requirement`` touches."""
    sources = {}
    for row in document["source_requirements"]:
        match = _ID.search(row["text"])
        sources[row["source_requirement_id"]] = match.group(1) if match else None
    if with_pattern:
        touched = {sreq for sreq, ident in sources.items() if ident == requirement}
    else:
        # The built-in split: every paragraph of the same section is one source requirement.
        touched = {sreq for sreq, ident in sources.items() if ident and ident[0] == requirement[0]}
    creqs = {creq for row in document["source_to_canonical_mappings"] if row["source_requirement_id"] in touched for creq in row["canonical_requirement_ids"]}
    return sorted(case["case_id"] for case in document["test_cases"] if set(case.get("requirement_ids") or []) & creqs)


def main(argv: list[str] | None = None) -> int:
    import eval_data
    from tools.suite_update import subset_document

    parser = argparse.ArgumentParser()
    parser.add_argument("--requirement", default="O05")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    document = eval_data.petclinic_document()
    full = _plan(document)
    rows = {"requirement": args.requirement, "cases_total": len(document["test_cases"]), "full": full}
    for name, with_pattern in (("with_id_pattern", True), ("builtin_split", False)):
        cases = affected(document, args.requirement, with_pattern=with_pattern)
        plan = _plan(subset_document(document, cases)) if cases else {"parts": 0, "input_bytes": 0, "max_part_bytes": 0}
        rows[name] = {"affected_cases": len(cases), "case_ids": cases, **plan,
                      "share_of_full_bytes": round(plan["input_bytes"] / full["input_bytes"], 4) if full["input_bytes"] else None}
    text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
