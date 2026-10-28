"""FaT-CV: the compact view of a canonical candidate that the reviewer reads instead of the full JSON.

The view is a pure function of the candidate: the same candidate gives the same bytes.  It keeps what a person
reviews — source and canonical requirements, case title, requirements, objective, preconditions, each step's action,
test data, automation blockers, expected text and its assertions in one line — and (FaT-CV2) the machine part whose
meaning only the reviewer judges: one line per step with its operation, inputs and outputs, and the list of
capabilities with action, arguments, results and the first line of provenance (the test boundary).  The validator
checks structure and references, not meaning.  A literal longer than ``LITERAL_LIMIT`` is cut with a mark; a sensitive
input is ``***``.  ``CV_DATA_MISMATCH`` is a code hint, not a stop: a literal input value of a step is missing from the
step's action and data — a number of any length as a whole token, other text from ``MIN_LITERAL`` characters.

``python -m tools.review_view --project <abs> --run <id> --candidate <r1.json>`` writes
``<run>/review-views/<candidate sha>.md`` and prints ``{path, bytes, candidate_sha256, data_mismatch, …}``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.json_cli import JsonArgumentParser

VIEW_VERSION = "review-view-v2"
DATA_MISMATCH = "CV_DATA_MISMATCH"
MIN_LITERAL = 3
LITERAL_LIMIT = 200
_NUMERIC = re.compile(r"^-?\d+(?:[.,]\d+)?$")
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


def _cut(text: str) -> str:
    """A long literal is cut with a mark; the full value stays in the candidate JSON at the same step."""
    return text if len(text) <= LITERAL_LIMIT else f"{text[:LITERAL_LIMIT]}…(+{len(text) - LITERAL_LIMIT} симв.)"


def _input(item: Mapping[str, Any]) -> str:
    target = item.get("target") or {}
    name = target.get("name") or item.get("input_id")
    return f"{name}=***" if target.get("sensitive") else f"{name}={_cut(_value(item.get('source') or {}))}"


def _operation(step: Mapping[str, Any], actions: Mapping[str, str]) -> str:
    """One line of the machine part of a step: what it calls, with which inputs, and what it outputs."""
    operation = step.get("operation") or {}
    capability_id = operation.get("capability_id")
    if capability_id:
        head = f"{capability_id} ({actions.get(capability_id, '—')})"
    else:
        rest = {key: value for key, value in operation.items() if key != "kind"}
        head = str(operation.get("kind") or "—") + (f" {_cut(_json(rest))}" if rest else "")
    inputs = ", ".join(_input(item) for item in sorted(step.get("inputs") or [], key=lambda row: row.get("display_order", 0))) or "—"
    outputs = ", ".join(str(row.get("output_id")) for row in step.get("outputs") or []) or "—"
    return f"   Операция: {head} | входы: {inputs} | выходы: {outputs}"


def _type(row: Mapping[str, Any]) -> str:
    semantic = row.get("semantic_type") or {}
    return str(semantic.get("type") or semantic.get("kind") or "?")


def _capability(capability: Mapping[str, Any]) -> str:
    arguments = ", ".join(f"{row['name']}:{_type(row)}{'*' if row.get('required') else ''}" for row in capability.get("arguments") or []) or "—"
    results = ", ".join(f"{row['name']}:{_type(row)}" for row in capability.get("results") or []) or "—"
    line = f"- {capability['capability_id']}: {capability['action']}, adapter {capability['adapter']}; аргументы {arguments}; результаты {results}"
    first = next(iter(str((capability.get("provenance") or [""])[0]).splitlines()), "")
    return line + (f"; граница: {_cut(first)}" if first else "")


def _named(leaf: str, text: str) -> bool:
    """FaT-CV2 (2.10): a number is named only as a whole token (17 is not in 170 or 1.17); other text from MIN_LITERAL."""
    if _NUMERIC.fullmatch(leaf):
        return re.search(rf"(?<!\d)(?<!\d[.,]){re.escape(leaf)}(?!\d)(?![.,]\d)", text) is not None
    return len(leaf) < MIN_LITERAL or leaf in text


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
                for leaf in _leaves(source.get("value")):
                    if not _named(leaf, text) and leaf not in missing:
                        missing.append(leaf)
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
        "- Код проверил структуру и ссылки (типы, ID, порядок, поток данных между шагами), не смысл. Смысл операций, входов"
        " и выходов шага проверяешь ты: та ли capability и тот ли action, те ли метод, путь и значения, что в действии,"
        " данных и требовании. Длинный литерал урезан с пометкой «…(+N симв.)» — полное значение в JSON кандидата у того же шага.",
        "- reviewed_case_ids по порядку: " + ", ".join(case["case_id"] for case in document["test_cases"]), "",
        "## Исходные требования", "",
        *(f"- {row['source_requirement_id']}: {row['text']}" for row in document["source_requirements"]), "",
        "## Требования", "",
        *(f"- {row['requirement_id']} (из {', '.join(sources.get(row['requirement_id'], [])) or '—'}): {row['text']}"
          for row in document["requirements"]), "",
        "## Подсказки кода", "", *(_hint_lines(hints) or ["- нет"]), "",
        "## Операции (capabilities)", "",
        *([_capability(row) for row in document.get("operation_capabilities") or []] or ["- нет"]), "",
        "## Кейсы",
    ]
    actions = {row["capability_id"]: row["action"] for row in document.get("operation_capabilities") or []}
    for case in document["test_cases"]:
        facts = [f"требования {', '.join(case['requirement_ids'])}", f"приоритет {case['priority']}",
                 f"категории {', '.join(case['categories'])}"]
        if case.get("technique"):
            facts.append(f"техника {case['technique']}")
        lines += ["", f"### {case['case_id']} {case['title']}", "", "- " + "; ".join(facts), f"- Цель: {case['objective']}"]
        lines += [f"- Предусловие {index}: {text}" for index, text in enumerate(case["preconditions"])]
        for step in case["steps"]:
            manual = f" (ручной: {step.get('manual_reason') or '—'})" if step.get("manual_only") else ""
            lines += [f"{step['display_order']}. [{step['step_id']}]{manual} {step['action']}", f"   Данные: {step.get('test_data') or '—'}",
                      _operation(step, actions)]
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
    parser = JsonArgumentParser(description="Write the compact review view of the assembled candidate of a run.")
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
