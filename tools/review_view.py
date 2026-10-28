"""FaT-CV: the compact view of a canonical candidate that the reviewer reads instead of the full JSON.

The view is a pure function of the candidate: the same candidate gives the same bytes.  It keeps what a person
reviews — source and canonical requirements, case title, requirements, objective, preconditions, each step's action,
test data, automation blockers, expected text and its assertions in one line — and (FaT-CV2) the machine part whose
meaning only the reviewer judges: one line per step with its operation, inputs and outputs, and the list of
capabilities with action, arguments, results and the first line of provenance (the test boundary).  The validator
checks structure and references, not meaning.  A literal longer than ``LITERAL_LIMIT`` is cut with a mark; a sensitive
input is ``***``.  FaT-PT: a parameter group is shown once at its first case — its lines with the IDs of the first
case and ``<параметр>`` where the cases differ, then a table with a row per case ID.  ``CV_DATA_MISMATCH`` is a code hint, not a stop: a literal input value of a step is missing from the
step's action and data — a number of any length as a whole token or (FaT-NW) a Russian numeral of the same value
(«через три дня» names 3), other text from ``MIN_LITERAL`` characters.  The hint never changes the case text.

``python -m tools.review_view --project <abs> --run <id> --candidate <r1.json>`` writes
``<run>/review-views/<candidate sha>.md`` and prints ``{path, bytes, candidate_sha256, data_mismatch, …}``.

FaT-AV: the profile ``autotest`` is the view of the effective document that the automation generator reads instead of
the full JSON (``--effective`` writes ``<run>/review-views/<document sha>.autotest.md``).  It keeps what a test is built
from — the canonical requirements (ID and text: the testing-work constraints), the capabilities with their whole
provenance (the test boundary), each case's requirement IDs, objective, preconditions, each step's action, operation,
inputs, outputs, blockers, expectation text and every assertion with its ID — and the machine part whole: every ID,
target (name or body pointer), semantic type, type provenance and literal exactly, a secret input as its handle and safe
label, nothing cut.  It drops what the generator does not use: source requirements, mappings, the provenance and
design of requirements, management, blocker provenance and the step's test data (values come from the structured
inputs only).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.canonical_document import param_groups
from tools.json_cli import JsonArgumentParser
from tools.test_case_projections import SLOT, template_texts

VIEW_VERSION = "review-view-v2"
AUTOTEST_VIEW_VERSION = "autotest-view-v1"
PROFILES = ("review", "autotest")
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


def _soft_forms(word: str) -> tuple[str, ...]:
    """Nominative, genitive/dative/prepositional and instrumental of a numeral in -ь (пять, пяти, пятью)."""
    return word, word[:-1] + "и", word[:-1] + "ью"


# FaT-NW: Russian cardinal numerals as the equal of a digit token ("через три дня" names 3), every case form
_NUMBER_WORDS: dict[str, int] = {
    **dict.fromkeys(("ноль", "нуль", "ноля", "нуля", "нолю", "нулю", "нолём", "нулём", "нолем", "нулем"), 0),
    **dict.fromkeys(("один", "одна", "одно", "одни", "одного", "одной", "одному", "одним", "одном", "одну", "одною", "одних", "одними"), 1),
    **dict.fromkeys(("два", "две", "двух", "двум", "двумя"), 2),
    **dict.fromkeys(("три", "трёх", "трех", "трём", "трем", "тремя"), 3),
    **dict.fromkeys(("четыре", "четырёх", "четырех", "четырём", "четырем", "четырьмя"), 4),
    **{form: value for value, word in ((5, "пять"), (6, "шесть"), (7, "семь"), (9, "девять"), (10, "десять"), (11, "одиннадцать"),
                                       (12, "двенадцать"), (13, "тринадцать"), (14, "четырнадцать"), (15, "пятнадцать"),
                                       (16, "шестнадцать"), (17, "семнадцать"), (18, "восемнадцать"), (19, "девятнадцать"),
                                       (20, "двадцать"), (30, "тридцать")) for form in _soft_forms(word)},
    **dict.fromkeys(("восемь", "восьми", "восемью", "восьмью"), 8),
    **dict.fromkeys(("сорок", "сорока"), 40),
    **dict.fromkeys(("пятьдесят", "пятидесяти", "пятьюдесятью"), 50),
    **dict.fromkeys(("шестьдесят", "шестидесяти", "шестьюдесятью"), 60),
    **dict.fromkeys(("семьдесят", "семидесяти", "семьюдесятью"), 70),
    **dict.fromkeys(("восемьдесят", "восьмидесяти", "восемьюдесятью"), 80),
    **dict.fromkeys(("девяносто", "девяноста"), 90),
    **dict.fromkeys(("сто", "ста"), 100),
    # a date offset in days named by the day ("дата D (сегодня)" names the offset 0)
    "позавчера": -2, "вчера": -1, "сегодня": 0, "завтра": 1, "послезавтра": 2,
}
_WORD = re.compile(r"[а-яё]+")


def _numbers_in_words(text: str) -> set[int]:
    """FaT-NW: the values the numerals of a text name — each word alone and a ten with its unit (двадцать пять = 25)."""
    values = [_NUMBER_WORDS.get(word) for word in _WORD.findall(text.lower())]
    named = {value for value in values if value is not None}
    named |= {tens + unit for tens, unit in zip(values, values[1:])
              if tens is not None and unit is not None and 20 <= tens <= 90 and tens % 10 == 0 and 1 <= unit <= 9}
    return named


def _named(leaf: str, text: str) -> bool:
    """FaT-CV2 (2.10): a number of any length is named only as a whole token (17 is not in 170 or 1.17), FaT-NW: or as a
    Russian numeral of the same integer value; other text from MIN_LITERAL."""
    if _NUMERIC.fullmatch(leaf):
        if re.search(rf"(?<!\d)(?<!\d[.,]){re.escape(leaf)}(?!\d)(?![.,]\d)", text) is not None:
            return True
        return re.fullmatch(r"-?\d+", leaf) is not None and int(leaf) in _numbers_in_words(text)
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


def _exact(value: Mapping[str, Any]) -> str:
    """FaT-AV: a source, actual or expected value whole: a literal as its exact JSON, else its kind and its fields."""
    if value.get("kind") == "literal":
        return _json(value.get("value"))
    rest = {key: item for key, item in value.items() if key != "kind"}
    return str(value.get("kind")) + (_json(rest) if rest else "")


def _semantic(row: Mapping[str, Any]) -> str:
    semantic = dict(row.get("semantic_type") or {})
    head = f"{semantic.pop('kind', '?')}/{semantic.pop('type', '?')}"
    return head + (_json(semantic) if semantic else "")


def _typed(row: Mapping[str, Any]) -> str:
    """The semantic type of an input or output and, when the document gives it, where the type comes from."""
    return _semantic(row) + (f" (тип: {_json(row['type_provenance'])})" if row.get("type_provenance") else "")


def _target(target: Mapping[str, Any]) -> str:
    """Where an input goes, exactly: a name (path, query, header, arg) or the JSON pointer of the body."""
    return f"{target.get('location')}:{target['pointer'] if 'pointer' in target else target.get('name')}"


def _autotest_capability(capability: Mapping[str, Any]) -> list[str]:
    arguments = ", ".join(f"{row['name']}:{_semantic(row)}{'*' if row.get('required') else ''}" for row in capability["arguments"]) or "—"
    results = ", ".join(f"{row['name']}:{_semantic(row)}" for row in capability["results"]) or "—"
    return ([f"- {capability['capability_id']}: {capability['action']}, adapter {capability['adapter']}; аргументы {arguments};"
             f" результаты {results}"] + [f"  граница: {line}" for line in capability["provenance"]])


def _autotest_step(step: Mapping[str, Any]) -> list[str]:
    manual = f" (ручной: {step.get('manual_reason') or '—'})" if step.get("manual_only") else ""
    lines = [f"{step['display_order']}. [{step['step_id']}]{manual} {step['action']}", f"   Операция: {_json(step['operation'])}"]
    for item in sorted(step["inputs"], key=lambda row: row["display_order"]):
        # a secret input shows its source as is: a secret_handle is the handle and its safe label, never the secret
        secret = " (секрет)" if item["target"].get("sensitive") else ""
        lines.append(f"   Вход {item['input_id']} → {_target(item['target'])}{secret} {_typed(item)} = {_exact(item['source'])}")
    lines += [f"   Выход {row['output_id']} {_typed(row)} ← {_exact(row['source'])}"
              for row in sorted(step["outputs"], key=lambda row: row["display_order"])]
    lines += [f"   Блокер автоматизации {blocker['blocker_id']} [{blocker['code']}] {blocker['field_path']}: {blocker['reason']}"
              for blocker in step["automation_blockers"]]
    for expectation in sorted(step["expectations"], key=lambda row: row["display_order"]):
        lines.append(f"   Ожидание [{expectation['expectation_id']}]: {expectation['text']}")
        lines += [f"     {row['assertion_id']}: {_exact(row['actual'])} {row['operator']}"
                  + (f" {_exact(row['expected'])}" if isinstance(row.get("expected"), Mapping) else "")
                  for row in sorted(expectation["assertions"], key=lambda row: row["display_order"])]
    return lines


def _autotest(document: Mapping[str, Any], digest: str) -> list[str]:
    lines = [
        f"# Вид для генератора автотестов {document['document_id']} r{document['revision']}", "",
        f"- Вид: {AUTOTEST_VIEW_VERSION}, построен кодом из effective-документа; одинаковый документ — одинаковые байты.",
        f"- document_sha256: {digest}",
        "- Машинная часть целиком: ID, цели, типы и литералы точно как в JSON (литерал — его JSON). Значения запроса бери только"
        " отсюда; текст действия и ожидания — пояснение. Полный JSON — только для диагностики валидатора.", "",
        # the testing-work constraints (boundary, framework, dependencies, edits, interface, authorization) live in the
        # canonical requirements the cases link (automation-output-contract)
        "## Требования", "", *(f"- {row['requirement_id']}: {row['text']}" for row in document["requirements"]), "",
        "## Операции (capabilities)", "",
        *([line for row in document["operation_capabilities"] for line in _autotest_capability(row)] or ["- нет"]), "",
        "## Кейсы",
    ]
    for case in document["test_cases"]:
        lines += ["", f"### {case['case_id']} {case['title']}", "", f"- требования {', '.join(case['requirement_ids'])}",
                  f"- Цель: {case['objective']}"]
        lines += [f"- Предусловие {index}: {text}" for index, text in enumerate(case["preconditions"])]
        for step in sorted(case["steps"], key=lambda row: row["display_order"]):
            lines += _autotest_step(step)
    return lines


def render(document: Mapping[str, Any], profile: str = "review") -> bytes:
    """The deterministic view of a valid canonical document: ``review`` for the reviewer, ``autotest`` (FaT-AV) for the
    automation generator."""
    from tools.canonical_document import document_sha256, require_valid_canonical_document

    if profile not in PROFILES:
        raise ValueError(f"unknown view profile: {profile}")
    require_valid_canonical_document(dict(document))
    if profile == "autotest":
        return ("\n".join(_autotest(document, document_sha256(dict(document)))) + "\n").encode("utf-8")
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
    groups = {group["cases"][0]["case_id"]: group for group in param_groups(document)} if SLOT not in json.dumps(document, ensure_ascii=False) else {}
    folded = {case["case_id"] for group in groups.values() for case in group["cases"][1:]}
    for case in document["test_cases"]:
        if case["case_id"] in groups:
            lines += _group_lines(groups[case["case_id"]], actions)
        elif case["case_id"] not in folded:
            lines += ["", f"### {case['case_id']} {case['title']}", *_case_lines(case, actions)]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _case_lines(case: Mapping[str, Any], actions: Mapping[str, str]) -> list[str]:
    facts = [f"требования {', '.join(case['requirement_ids'])}", f"приоритет {case['priority']}",
             f"категории {', '.join(case['categories'])}"]
    if case.get("technique"):
        facts.append(f"техника {case['technique']}")
    lines = ["", "- " + "; ".join(facts), f"- Цель: {case['objective']}"]
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
    return lines


def _as_first(case: Mapping[str, Any], first: Mapping[str, Any], line: str) -> str:
    """A line of a group case with its step and expectation IDs written as those of the first case (same positions)."""
    for own_step, base_step in zip(case["steps"], first["steps"]):
        pairs = [(own_step["step_id"], base_step["step_id"])]
        pairs += [(own["expectation_id"], base["expectation_id"]) for own, base in zip(own_step["expectations"], base_step["expectations"])]
        for own, base in pairs:
            line = re.sub(r"(?<![A-Za-z0-9_:-])" + re.escape(own) + r"(?![A-Za-z0-9_:-])", base, line)
    return line


def _group_lines(group: Mapping[str, Any], actions: Mapping[str, str]) -> list[str]:
    """FaT-PT: the lines of a group once — ``<параметр>`` where its cases differ — and a row per case ID."""
    cases, parameter = group["cases"], group["parameter"]
    per_case = [[_as_first(case, cases[0], line) for line in _case_lines(case, actions)] for case in cases]
    values: list[list[str]] = [[] for _ in cases]
    expected: list[list[str]] = [[] for _ in cases]
    shown = []
    for texts in zip(*per_case):
        line, middles = template_texts(list(texts))
        into = expected if line.lstrip().startswith(("Ожидание", "Проверки")) else values
        for own, middle in zip(into, middles):
            if middle.strip() and middle.strip() not in own:
                own.append(middle.strip())
        shown.append(line.replace(SLOT, f"<{parameter['name']}>"))
    title, _ = template_texts([case["title"] for case in cases])
    titled = len({case["title"] for case in cases}) > 1
    rows = []
    for index, case in enumerate(cases):
        own = [item for item in expected[index] if item not in values[index]]
        cells = [str(index + 1), "; ".join(values[index]) or "—", "; ".join(own) or "—", *([case["title"]] if titled else []), case["case_id"]]
        rows.append("| " + " | ".join(cell.replace("|", "\\|").replace("\n", " ") for cell in cells) + " |")
    head = ["№", parameter["name"], "ожидаемое", *(["название"] if titled else []), "ID кейса"]
    return ["", f"### {group['group_id']} {title.replace(SLOT, '<' + parameter['name'] + '>')}", "",
            f"- Группа {group['group_id']}: кейсы {', '.join(case['case_id'] for case in cases)} — варианты параметра {parameter['id']}"
            f" «{parameter['name']}», шаги одни; ниже ID шагов и ожиданий первого кейса, у остальных — те же позиции в JSON кандидата.",
            *shown, "", "| " + " | ".join(head) + " |", "|" + "---|" * len(head), *rows]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="Write the compact view of the assembled candidate (reviewer) or of the effective "
                                            "document (automation generator) of a run.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--candidate")
    source.add_argument("--effective", action="store_true", help="FaT-AV: the autotest view of the effective document")
    args = parser.parse_args(argv)
    try:
        from tools.canonical_document import document_sha256
        from tools.confined_output import atomic_write_confined_bytes_at_root
        from tools.pilot_state import derive_state, read_effective_canonical
        from tools.step_size import autotest_view

        project = Path(args.project).resolve()
        if Path(args.run).name != args.run or args.run in {"", ".", ".."}:
            raise ValueError(f"unknown run: {args.run}")
        root = project / ".pilot-runs" / args.run
        state = derive_state(root)
        if args.effective:
            if not state["attempts"]:
                raise ValueError("the run has no attempt yet")
            effective = read_effective_canonical(root, str(state["attempts"][-1]["attempt_id"]))
            document, profile = effective["document"], "autotest"
            digest = document_sha256(dict(document))
            if digest != effective["document_digest"]:
                raise ValueError("the effective document does not match its digest")
            target = autotest_view(root, digest)
        else:
            document, profile = json.loads(Path(args.candidate).read_text(encoding="utf-8")), "review"
            digest = document_sha256(document)
            published = [event for event in state["events"]
                         if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"]
            if not published or published[-1].get("artifact_digest") != digest:
                raise ValueError("the candidate is not the assembled candidate of the run")
            target = root / "review-views" / (digest.removeprefix("sha256:") + ".md")
        data = render(document, profile)
        atomic_write_confined_bytes_at_root(project, root, target, data)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"status": "error", "message": str(error)[:2000]}, ensure_ascii=False))
        return 2
    view = AUTOTEST_VIEW_VERSION if profile == "autotest" else VIEW_VERSION
    print(json.dumps({"status": "ok", "path": str(target), "bytes": len(data), "candidate_sha256": digest, "view": view, "profile": profile,
                      "data_mismatch": len(data_mismatches(document))}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
