"""FaT-CV: the compact view of a canonical candidate that the reviewer reads instead of the full JSON.

The view is a pure function of the candidate: the same candidate gives the same bytes.  It keeps what a person
reviews — source and canonical requirements, case title, requirements, objective, preconditions, each step's action,
test data, automation blockers, expected text and its assertions in one line — and leaves out the machine part (inputs, outputs, operation,
capabilities, provenance, digests), which the canonical validator checks.  ``CV_DATA_MISMATCH`` is a code hint, not a
stop: a literal input value of a step (at least ``MIN_LITERAL`` characters) is missing from the step's action and data.

``python -m tools.review_view --project <abs> --run <id> --candidate <r1.json>`` writes
``<run>/review-views/<candidate sha>.md`` and prints ``{path, bytes, candidate_sha256, data_mismatch, …}``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

VIEW_VERSION = "review-view-v1"
DATA_MISMATCH = "CV_DATA_MISMATCH"
MIN_LITERAL = 3
HINT_LIMIT = 50
_SHOWN = 3
_OPERATORS = {
    "equals": "=", "not_equals": "≠", "exists": "есть", "not_exists": "нет", "contains": "содержит", "matches": "~",
    "greater_than": ">", "greater_or_equal": "≥", "less_than": "<", "less_or_equal": "≤", "length_equals": "длина =",
    "schema_matches": "по схеме",
}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _value(value: Mapping[str, Any]) -> str:
    kind = value.get("kind")
    if kind == "literal":
        return _json(value.get("value"))
    if kind == "step_output":
        return f"{value.get('step_id')}.{value.get('output_id')}"
    if kind == "http_status":
        return "status"
    if kind == "http_body":
        return "body" + str(value.get("pointer", ""))
    if kind in {"http_header", "project_result"}:
        return str(value.get("name"))
    return f"{kind}:{value['name']}" if "name" in value else str(kind)


def _assertion(assertion: Mapping[str, Any]) -> str:
    text = f"{_value(assertion['actual'])} {_OPERATORS.get(assertion['operator'], assertion['operator'])}"
    return text + (f" {_value(assertion['expected'])}" if isinstance(assertion.get("expected"), Mapping) else "")


def _leaves(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from _leaves(item)
    elif not isinstance(value, bool) and value is not None:
        yield str(value)


def data_mismatches(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One ``CV_DATA_MISMATCH`` per step whose literal inputs are not all named in its action or test data."""
    hints = []
    for case in document["test_cases"]:
        for step in case["steps"]:
            text = f"{step.get('action') or ''}\n{step.get('test_data') or ''}"
            missing: list[str] = []
            for item in step["inputs"]:
                source = item.get("source") or {}
                if source.get("kind") != "literal" or (item.get("target") or {}).get("sensitive"):
                    continue
                missing += [leaf for leaf in _leaves(source.get("value")) if len(leaf) >= MIN_LITERAL and leaf not in text and leaf not in missing]
            if missing:
                hints.append({"code": DATA_MISMATCH, "case_id": case["case_id"], "step_id": step["step_id"], "count": len(missing),
                              "values": [value[:40] for value in missing[:_SHOWN]]})
    return hints


def _hint_lines(hints: list[dict[str, Any]]) -> list[str]:
    lines = [f"- {hint['code']} {hint['case_id']} {hint['step_id']}: нет в действии и данных шага — "
             + ", ".join(_json(value) for value in hint["values"]) + (f" и ещё {hint['count'] - _SHOWN}" if hint["count"] > _SHOWN else "")
             for hint in hints[:HINT_LIMIT]]
    return lines + ([f"- ещё {len(hints) - HINT_LIMIT} шаг(ов) с {DATA_MISMATCH}"] if len(hints) > HINT_LIMIT else [])


def render(document: Mapping[str, Any]) -> bytes:
    """The deterministic review view of a valid canonical document."""
    from tools.canonical_document import document_sha256, require_valid_canonical_document

    require_valid_canonical_document(dict(document))
    sources: dict[str, list[str]] = {}
    for mapping in document["source_to_canonical_mappings"]:
        for requirement_id in mapping["canonical_requirement_ids"]:
            sources.setdefault(requirement_id, []).append(mapping["source_requirement_id"])
    hints = data_mismatches(document)
    lines = [
        f"# Вид для ревью {document['document_id']} r{document['revision']}", "",
        f"- Вид: {VIEW_VERSION}, построен кодом из кандидата; одинаковый кандидат — одинаковые байты.",
        f"- candidate document_sha256: {document_sha256(dict(document))}",
        "- Машинную часть (входы и выходы шагов, операции, связи с источником) проверил код. Здесь — то, что читает человек,"
        " и проверки шага одной строкой.",
        "- reviewed_case_ids по порядку: " + ", ".join(case["case_id"] for case in document["test_cases"]), "",
        "## Исходные требования", "",
        *(f"- {row['source_requirement_id']}: {row['text']}" for row in document["source_requirements"]), "",
        "## Требования", "",
        *(f"- {row['requirement_id']} (из {', '.join(sources.get(row['requirement_id'], [])) or '—'}): {row['text']}"
          for row in document["requirements"]), "",
        "## Подсказки кода", "", *(_hint_lines(hints) or ["- нет"]), "",
        "## Кейсы",
    ]
    for case in document["test_cases"]:
        facts = [f"требования {', '.join(case['requirement_ids'])}", f"приоритет {case['priority']}",
                 f"категории {', '.join(case['categories'])}"]
        if case.get("technique"):
            facts.append(f"техника {case['technique']}")
        lines += ["", f"### {case['case_id']} {case['title']}", "", "- " + "; ".join(facts), f"- Цель: {case['objective']}"]
        lines += [f"- Предусловие {index}: {text}" for index, text in enumerate(case["preconditions"])]
        for step in case["steps"]:
            manual = f" (ручной: {step.get('manual_reason') or '—'})" if step.get("manual_only") else ""
            lines += [f"{step['display_order']}. [{step['step_id']}]{manual} {step['action']}", f"   Данные: {step.get('test_data') or '—'}"]
            # the reviewer judges whether a gap is justified (missing oracle, input, access or setup), so a blocker is shown
            lines += [f"   Блокер автоматизации [{blocker['code']}] {blocker['field_path']}: {blocker['reason']}"
                      for blocker in step["automation_blockers"]]
            for expectation in step["expectations"]:
                lines.append(f"   Ожидание [{expectation['expectation_id']}]: {expectation['text']}")
                if expectation["assertions"]:
                    lines.append("   Проверки: " + "; ".join(_assertion(item) for item in expectation["assertions"]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Write the compact review view of the assembled candidate of a run.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    try:
        from tools.canonical_document import document_sha256
        from tools.confined_output import atomic_write_confined_bytes_at_root
        from tools.pilot_state import derive_state

        project = Path(args.project).resolve()
        if Path(args.run).name != args.run or args.run in {"", ".", ".."}:
            raise ValueError(f"unknown run: {args.run}")
        root = project / ".pilot-runs" / args.run
        document = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        digest = document_sha256(document)
        published = [event for event in derive_state(root)["events"]
                     if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"]
        if not published or published[-1].get("artifact_digest") != digest:
            raise ValueError("the candidate is not the assembled candidate of the run")
        data = render(document)
        target = root / "review-views" / (digest.removeprefix("sha256:") + ".md")
        atomic_write_confined_bytes_at_root(project, root, target, data)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error)[:2000]}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "ok", "path": str(target), "bytes": len(data), "candidate_sha256": digest, "view": VIEW_VERSION,
                      "data_mismatch": len(data_mismatches(document))}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
