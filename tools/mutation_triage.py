"""Survivor triage (``mutation-triage-v1``): task inputs with anchors, answer checks, decisions.

After a terminal local attempt whose mutation receipt is ``MEASURED``, every survivor
group marked for triage (``mutation.triage_limit``, default 30) goes to the model in
compact tasks.  A task input is plain text; every line that starts with ``[ID]`` is an
anchor (the same rule as the compact review projection):

* ``[MUT-0001]`` the group, ``[MUT-0001-1]`` each mutation, ``[MUT-0001:L59]`` product lines
  (the mutated line is marked ``>``);
* the linked cases as the automation reviewer sees them (``[TC-…]``, ``[STEP-…]`` …);
* linked canonical and source requirements with their text (``[CREQ-…]``, ``[SREQ-…]``);
* slices of the generated test methods that covered the line (``[T:L0120]``);
* the mutated class whole and the product methods that call the mutated method
  (``[F1:L0048]``: one key per product file).

A task fits ``budget`` bytes (``--review-input-bytes``): the class always goes in whole, and at
least one covering test method with its cases; then the other methods, each with its cases
(whole or not at all), most specific first — those that killed other mutants of the same
method — and the callers with what is left.  What did not fit is named on a line
``… не вошли по бюджету: …`` (case IDs stay valid for ``TEST_GAP``/``SPEC_GAP``).

The driver accepts an answer only when every reference is an anchor of that task, each
group of the task is answered once, a ``TEST_GAP`` proposal names one of the group's
cases (and a step of it), a ``SPEC_GAP`` question names one of the group's requirements,
and the group's own product line is cited when the line is in the input.  Triage changes
nothing in the attempt: decisions are a report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "mutation-triage-output.schema.json"
DECISIONS = ("TEST_GAP", "SPEC_GAP", "EQUIVALENT", "OUT_OF_SCOPE")
TASK_BUDGET_BYTES = 60_000
NEIGHBOURS = 4
_ANCHOR = re.compile(r"^\s*\[([^\]\s]+)\]", re.M)

INSTRUCTIONS = (
    "Единственный вход — текст задачи разбора выживших мутантов (якоря [ID] в начале строк). Другие файлы не читай. "
    "По каждой группе [MUT-…] выбери одно решение: TEST_GAP (требование задаёт это поведение, а проверки нет — proposal: case_id одного из "
    "кейсов группы, по желанию step_id, text — какой шаг или ожидание добавить), SPEC_GAP (требование поведение не задаёт — question до 300 "
    "символов и requirement_id одного из требований группы; ожидание из кода не выводи), EQUIVALENT (поведение не меняется — rationale), "
    "OUT_OF_SCOPE (код вне фичи — rationale). Код мутируемого класса дан целиком, вызыватели — в пределах бюджета; EQUIVALENT обосновывай "
    "только кодом из входа, знания проекта вне входа не используй. refs — 1–6 якорей этой задачи, среди них строка своей группы (MUT-000N:L…), если код показан. "
    "rationale до 600 символов, по-русски. Верни только {\"groups\": [...]} — по объекту на каждую группу задачи, в её порядке.")


def anchors(text: str) -> list[str]:
    return _ANCHOR.findall(text)


def pending_groups(receipt: Mapping[str, Any]) -> list[dict[str, Any]]:
    if receipt.get("status") != "MEASURED":
        return []
    return [dict(group) for group in receipt.get("survivor_groups", []) if group.get("triage")]


def product_path(group: Mapping[str, Any]) -> str:
    """Project-relative Java source path of a group's class (``src/main/java`` layout)."""
    outer = str(group["class"]).split("$", 1)[0]
    package = outer.rpartition(".")[0]
    name = str(group.get("source_file") or outer.rpartition(".")[2] + ".java")
    return "/".join(["src", "main", "java", *([part for part in package.split(".") if part]), name])


def _group_block(group: Mapping[str, Any], lines: Sequence[str] | None) -> list[str]:
    owner = str(group["class"]).rpartition(".")[2]
    out = [f"### [{group['group_id']}] {owner}#{group['method']}, строка {group['line']} ({group['source_file']})"]
    for number, mutant in enumerate(group["mutants"], start=1):
        out.append(f"[{group['group_id']}-{number}] {mutant['mutator']}: {mutant['description']}")
    out.append(f"Кейсы: {', '.join(group['case_ids']) or '—'}; требования: {', '.join([*group['requirement_ids'], *group['source_requirement_ids']]) or '—'}")
    if lines is None:
        out.append("Код продукта не входит в контекст попытки: опирайся на описание мутации.")
        return out
    line = int(group["line"])
    first, last = max(1, line - NEIGHBOURS), min(len(lines), line + NEIGHBOURS)
    out.append(f"Код продукта ({group['source_file']}):")
    for number in range(first, last + 1):
        marker = ">" if number == line else " "
        out.append(f"[{group['group_id']}:L{number}] {marker} {lines[number - 1]}")
    return out


def _slices(automation: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], list[Mapping[str, Any]], Any]]:
    """Each generated file with its symbols and member slices (files that cannot be sliced are left out)."""
    from tools.code_slices import SliceError, slice_file

    artifacts = automation["artifacts"]
    out = []
    for generated in artifacts["generated_files"]:
        symbols = [row for row in artifacts["generated_symbols"] if row["file_id"] == generated["file_id"]]
        try:
            out.append((generated, symbols, slice_file(generated, symbols)))
        except SliceError:
            continue
    return out


def _test_block(sliced: Sequence[tuple[Mapping[str, Any], Sequence[Mapping[str, Any]], Any]], methods: Sequence[str]) -> list[str]:
    """Slices (with helpers) of the generated methods that covered the task's lines, each line once."""
    wanted = {method.split("#", 1)[1] for method in methods}
    out: list[str] = []
    for generated, symbols, slices in sliced:
        ranges: set[int] = set()
        for symbol in symbols:
            if symbol["locator"].get("method_name") not in wanted:
                continue
            member = slices.symbols[symbol["symbol_id"]]
            ranges.update(range(member.start, member.end + 1))
            for helper in slices.helpers[symbol["symbol_id"]]:
                ranges.update(range(helper.start, helper.end + 1))
        if not ranges:
            continue
        out.append(f"Файл теста {generated['path']}:")
        width = max(4, len(str(len(slices.lines))))
        previous = None
        for number in sorted(ranges):
            if previous is not None and number != previous + 1:
                out.append("…")
            out.append(f"[T:L{number:0{width}d}] {slices.lines[number - 1]}")
            previous = number
    return out


def _requirement_block(document: Mapping[str, Any], marker_requirements: Sequence[Mapping[str, Any]], creq_ids: set[str], sreq_ids: set[str]) -> list[str]:
    from tools.review_projection import requirement_text, source_requirement_text

    out = [requirement_text(row) for row in document.get("requirements", []) if row.get("requirement_id") in creq_ids]
    out.extend(source_requirement_text(row) for row in marker_requirements if row.get("source_requirement_id") in sreq_ids)
    return out


def _ranked_methods(groups: Sequence[Mapping[str, Any]], mutants: Sequence[Mapping[str, Any]]) -> list[str]:
    """Covering test methods, most specific first: those that killed mutants on the group's line, then in its method."""
    from tools.mutation import descriptor_method

    score: dict[str, tuple[int, int]] = {}
    for group in groups:
        for mutant in mutants:
            if mutant.get("status") != "KILLED" or mutant.get("class") != group["class"] or mutant.get("method") != group["method"]:
                continue
            same_line = int(int(mutant.get("line") or 0) == int(group["line"]))
            for descriptor in mutant.get("killing") or ():
                key = descriptor_method(descriptor)
                if key is not None:
                    name = f"{key[0]}#{key[1]}"
                    line_kills, method_kills = score.get(name, (0, 0))
                    score[name] = (line_kills + same_line, method_kills + 1)
    methods = sorted({method for group in groups for method in group["covering_methods"]})
    return sorted(methods, key=lambda method: (-score.get(method, (0, 0))[0], -score.get(method, (0, 0))[1], method))


def _method_cases(automation: Mapping[str, Any], groups: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """The group cases each covering test method implements."""
    artifacts = automation["artifacts"]
    method_of = {row["symbol_id"]: f"{row['locator'].get('class_fqn')}#{row['locator'].get('method_name')}" for row in artifacts["generated_symbols"]}
    linked = {case for group in groups for case in group["case_ids"]}
    out: dict[str, set[str]] = {}
    for row in artifacts["implementation_relations"]:
        method = method_of.get(row["symbol_id"])
        if method is not None and row["case_id"] in linked:
            out.setdefault(method, set()).add(row["case_id"])
    return {method: sorted(cases) for method, cases in out.items()}


def _called_name(group: Mapping[str, Any]) -> str | None:
    """The identifier a caller uses for the mutated method (a constructor: its class name)."""
    method = str(group["method"])
    if method == "<init>":
        return str(group["class"]).rpartition(".")[2].rpartition("$")[2]
    if method.startswith("lambda$"):
        return method.split("$")[1] or None
    return None if method.startswith("<") else method


def _callers(groups: Sequence[Mapping[str, Any]], product_paths: Sequence[str], product_lines: Callable[[str], Sequence[str] | None]) -> list[dict[str, Any]]:
    """Product methods outside the mutated classes that name a mutated method; files of the same package first."""
    from tools.code_slices import SliceError, java_file

    own = {product_path(group) for group in groups}
    targets = {name for name in (_called_name(group) for group in groups) if name}
    packages = {path.rpartition("/")[0] for path in own}
    out: list[dict[str, Any]] = []
    for path in sorted(product_paths, key=lambda path: (path.rpartition("/")[0] not in packages, path)):
        if path in own or not path.endswith(".java"):
            continue
        lines = product_lines(path)
        if lines is None:
            continue
        try:
            members, _header = java_file(path, path, "\n".join(lines))
        except SliceError:
            continue
        for member in members:
            if member.kind == "method" and member.names & targets:
                out.append({"path": path, "name": f"{member.owner}#{member.name}", "start": member.start, "end": member.end, "lines": lines})
    return out


def _numbered(key: str, lines: Sequence[str], numbers: Sequence[int]) -> list[str]:
    width = max(4, len(str(len(lines))))
    return [f"[{key}:L{number:0{width}d}] {lines[number - 1]}" for number in numbers]


def _caller_blocks(callers: Sequence[Mapping[str, Any]], first_key: int) -> list[str]:
    out: list[str] = []
    keys: dict[str, str] = {}
    for caller in sorted(callers, key=lambda row: (row["path"], row["start"])):
        if caller["path"] not in keys:
            keys[caller["path"]] = f"F{first_key + len(keys)}"
            out += ["", f"### [{keys[caller['path']]}] {caller['path']} — вызыватели"]
        else:
            out.append("…")
        out += _numbered(keys[caller["path"]], caller["lines"], range(caller["start"], caller["end"] + 1))
    return out


def _omitted(label: str, names: Sequence[str], limit: int = 60) -> list[str]:
    if not names:
        return []
    shown = ", ".join(names[:limit]) + (f" и ещё {len(names) - limit}" if len(names) > limit else "")
    return ["", f"{label} не вошли по бюджету: {shown}"]


def task_text(groups: Sequence[Mapping[str, Any]], document: Mapping[str, Any], automation: Mapping[str, Any],
              marker_requirements: Sequence[Mapping[str, Any]], product_lines: Callable[[str], Sequence[str] | None], *, title: str,
              budget: int | None = None, mutants: Sequence[Mapping[str, Any]] = (), product_paths: Sequence[str] = ()) -> str:
    """The task input.  Without ``budget``: every covering method and case (the size used to pack groups).  With it: the
    class whole and one covering method with its cases always, then methods with their cases and the callers that fit."""
    from tools.review_projection import automation_case_text

    cases = {case["case_id"]: case for case in document.get("test_cases", [])}
    case_text: dict[str, str] = {}
    sliced = _slices(automation)
    ranked = _ranked_methods(groups, mutants)
    method_cases = _method_cases(automation, groups)
    linked_cases = sorted({case for group in groups for case in group["case_ids"]})
    group_creq = {requirement for group in groups for requirement in group["requirement_ids"]}
    group_sreq = {requirement for group in groups for requirement in group["source_requirement_ids"]}
    sources_of: dict[str, set[str]] = {}
    for row in document.get("source_to_canonical_mappings", []):
        for creq in row.get("canonical_requirement_ids", []):
            sources_of.setdefault(creq, set()).add(row["source_requirement_id"])
    head = [f"# Разбор выживших мутантов — {title}", "", "Решение по каждой группе: TEST_GAP, SPEC_GAP, EQUIVALENT или OUT_OF_SCOPE (SKILL роли).", "", "## Группы"]
    for group in groups:
        head += ["", *_group_block(group, product_lines(product_path(group)))]
    classes: list[str] = []
    class_count = 0
    for path in dict.fromkeys(product_path(group) for group in groups):
        lines = product_lines(path)
        if lines is not None:
            class_count += 1
            key = f"F{class_count}"
            classes += ["", f"### [{key}] {path} — класс целиком", *_numbered(key, lines, range(1, len(lines) + 1))]
    callers = _callers(groups, product_paths, product_lines) if budget is not None and class_count else []

    def compose(methods: Sequence[str], chosen_callers: Sequence[Mapping[str, Any]]) -> str:
        if budget is None:
            included, creq, sreq = linked_cases, group_creq, group_sreq
        else:
            included = sorted({case for method in methods for case in method_cases.get(method, [])})
            creq = {requirement for case in included for requirement in cases.get(case, {}).get("requirement_ids", [])} & group_creq
            sreq = {source for requirement in creq for source in sources_of.get(requirement, ())} & group_sreq
        out = list(head)
        if classes:
            out += ["", "## Код продукта", "", "Код мутируемого класса дан целиком; вызыватели мутируемого метода — в пределах бюджета задачи.", *classes]
            out += _caller_blocks(chosen_callers, class_count + 1)
            if budget is not None:
                out += _omitted("Вызыватели", [f"{row['name']} ({row['path']})" for row in callers if row not in chosen_callers])
        out += ["", "## Кейсы", ""]
        for case_id in included:
            if case_id in cases:
                if case_id not in case_text:
                    case_text[case_id] = automation_case_text(cases[case_id])
                out.append(case_text[case_id])
        if budget is not None:
            out += _omitted("Кейсы", [case for case in linked_cases if case not in included])
        out += ["", "## Требования", "", *_requirement_block(document, marker_requirements, creq, sreq)]
        out += ["", "## Код тестов", "", *(_test_block(sliced, methods) or ["Срезы методов недоступны."])]
        if budget is not None:
            out += _omitted("Срезы методов", [method for method in ranked if method not in methods])
        return "\n".join(out) + "\n"

    if budget is None:
        return compose(ranked, [])
    fits = lambda methods, chosen: len(compose(methods, chosen).encode("utf-8")) <= budget  # noqa: E731
    methods = ranked[:1]  # one covering method with its cases goes in even over the budget, like the class
    for method in ranked[1:]:
        if fits([*methods, method], []):
            methods.append(method)
    chosen: list[Mapping[str, Any]] = []
    for caller in callers:
        if fits(methods, [*chosen, caller]):
            chosen.append(caller)
    return compose(methods, chosen)


def build_tasks(receipt: Mapping[str, Any], document: Mapping[str, Any], automation: Mapping[str, Any], marker_requirements: Sequence[Mapping[str, Any]],
                product_lines: Callable[[str], Sequence[str] | None], *, budget: int = TASK_BUDGET_BYTES,
                mutants: Sequence[Mapping[str, Any]] = (), product_paths: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Pack pending groups in order while their full input fits ``budget`` (a single group may exceed it); each task's
    input is then cut to the budget (``task_text``)."""
    tasks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []

    def close() -> None:
        number = len(tasks) + 1
        text = task_text(current, document, automation, marker_requirements, product_lines, title=f"часть {number}", budget=budget,
                         mutants=mutants, product_paths=product_paths)
        tasks.append({"label": f"triage-{number:02d}", "group_ids": [group["group_id"] for group in current], "text": text})

    for group in pending_groups(receipt):
        trial = [*current, group]
        size = len(task_text(trial, document, automation, marker_requirements, product_lines, title="часть 00").encode("utf-8"))
        if current and size > budget:
            close()
            current = [group]
        else:
            current = trial
    if current:
        close()
    return tasks


def _row(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


def validate(task: Mapping[str, Any], answer: Any, receipt: Mapping[str, Any], document: Mapping[str, Any]) -> list[dict[str, str]]:
    """Why an answer would be rejected (empty when accepted)."""
    from tools.schema_validation import schema_diagnostics

    rows = schema_diagnostics(answer, SCHEMA, ROOT) if isinstance(answer, dict) else [_row("TRIAGE_ANSWER_INVALID", "", "the answer is a JSON object")]
    if rows:
        return rows
    defined = set(anchors(task["text"]))
    groups = {group["group_id"]: group for group in receipt.get("survivor_groups", [])}
    answered = [row["group_id"] for row in answer["groups"]]
    if answered != list(task["group_ids"]):
        rows.append(_row("TRIAGE_GROUPS", "/groups", "answer every group of the task once, in task order: " + ", ".join(task["group_ids"])))
        return rows
    steps = {case["case_id"]: {step["step_id"] for step in case.get("steps", [])} for case in document.get("test_cases", [])}
    for index, row in enumerate(answer["groups"]):
        group = groups[row["group_id"]]
        bad = [ref for ref in row["refs"] if ref not in defined]
        if bad:
            rows.append(_row("TRIAGE_REF_UNKNOWN", f"/groups/{index}/refs", "refs are not anchors of this task: " + ", ".join(bad)))
        own_lines = {ref for ref in defined if ref.startswith(f"{group['group_id']}:L")}
        if own_lines and not own_lines & set(row["refs"]):
            rows.append(_row("TRIAGE_REF_FOREIGN", f"/groups/{index}/refs", f"cite at least one line of the group itself ({group['group_id']}:L…)"))
        if row["decision"] == "TEST_GAP":
            proposal = row["proposal"]
            if proposal["case_id"] not in group["case_ids"]:
                rows.append(_row("TRIAGE_CASE_FOREIGN", f"/groups/{index}/proposal/case_id", "the proposal names a case of the group: " + ", ".join(group["case_ids"])))
            elif "step_id" in proposal and proposal["step_id"] not in steps.get(proposal["case_id"], set()):
                rows.append(_row("TRIAGE_STEP_FOREIGN", f"/groups/{index}/proposal/step_id", "the step belongs to the proposed case"))
        if row["decision"] == "SPEC_GAP":
            linked = {*group["requirement_ids"], *group["source_requirement_ids"]}
            if row["requirement_id"] not in linked:
                rows.append(_row("TRIAGE_REQUIREMENT_FOREIGN", f"/groups/{index}/requirement_id", "the question names a requirement of the group: " + ", ".join(sorted(linked))))
    return rows


def decision_rows(answers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every accepted group decision, ordered by group."""
    rows = [dict(row) for answer in answers for row in answer["groups"]]
    return sorted(rows, key=lambda row: row["group_id"])


def counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    return {decision: sum(row["decision"] == decision for row in rows) for decision in DECISIONS}


def proposals(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Strengthening proposals (``TEST_GAP``) kept for the suite update; they change nothing now."""
    return [{"group_id": row["group_id"], **row["proposal"], "refs": list(row["refs"])} for row in rows if row["decision"] == "TEST_GAP"]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
