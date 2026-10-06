"""``compact-v1`` automation review: case slices, one SUPPORT area per file, no pair areas.

Each case is reviewed once, in one part, from its canonical projection and the exact
slice of its test method (original line numbers).  The rest of the file — header,
fields, setup, fixtures, helpers, nested types — is the file's SUPPORT code, sent in
every part that holds one of its cases, with a shared-state table built by code; its
own area is answered in the first such part.  Every part has a ``cross`` area over
its cases through the SUPPORT code and shared state.  Code checks (ASSERT IDs,
literals, request method/path/body) arrive as lint suspicions.

When a file cannot be sliced exactly, the whole file is the SUPPORT code; when a part
cannot fit it, the part is blocked with ``REVIEW_CONTEXT_LIMIT`` — never truncated.
"""
from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from tools.review_compact import MODE, PLAN_VERSION, _Context, validate_compact_plan
from tools.review_parts import review_digest
from tools.review_projection import build_projection


def _slices(automation: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    from tools.code_slices import SliceError, slice_file

    generated = automation["artifacts"]
    slices, failures = {}, []
    for file in generated["generated_files"]:
        symbols = [row for row in generated["generated_symbols"] if row["file_id"] == file["file_id"]]
        try:
            slices[file["file_id"]] = slice_file(file, symbols)
        except SliceError as error:
            failures.append({"file_id": file["file_id"], "reason": str(error)})
    return slices, failures


def _whole_lines(file: Mapping[str, Any]) -> list[str]:
    lines = str(file["content"]).split("\n")
    return lines[:-1] if lines and lines[-1] == "" else lines


def _numbered(lines: Sequence[str], start: int, end: int) -> str:
    width = max(4, len(str(len(lines))))
    return "\n".join(f"L{number:0{width}d}| {lines[number - 1]}" for number in range(start, end + 1))


class _Code:
    """Per-file code views: SUPPORT ranges, case method ranges and helpers."""

    def __init__(self, automation: Mapping[str, Any], document: Mapping[str, Any]) -> None:
        generated = automation["artifacts"]
        self.files = {row["file_id"]: row for row in generated["generated_files"]}
        self.slices, self.failures = _slices(automation)
        failed = {row["file_id"] for row in self.failures}
        self.case_files: dict[str, list[str]] = {}
        self.case_symbols: dict[str, list[tuple[str, str]]] = {}
        for relation in generated["implementation_relations"]:
            files = self.case_files.setdefault(relation["case_id"], [])
            if relation["file_id"] not in files:
                files.append(relation["file_id"])
            pairs = self.case_symbols.setdefault(relation["case_id"], [])
            if (relation["file_id"], relation["symbol_id"]) not in pairs:
                pairs.append((relation["file_id"], relation["symbol_id"]))
        self.failed = failed
        self.manual = {}
        for row in generated["manual_dispositions"]:
            self.manual.setdefault(row.get("case_id"), []).append(row)
        self.diagnostics = list(generated.get("diagnostics") or [])
        referenced = {relation["file_id"] for relation in generated["implementation_relations"]}
        self.support_only = [file_id for file_id in self.files if file_id not in referenced]

    def lines(self, file_id: str) -> list[str]:
        return self.slices[file_id].lines if file_id in self.slices else _whole_lines(self.files[file_id])

    def support_ranges(self, file_id: str) -> list[tuple[int, int]]:
        if file_id in self.slices and file_id not in self.support_only:
            return self.slices[file_id].support_ranges()
        return [(1, len(self.lines(file_id)))]

    def support_text(self, file_id: str) -> str:
        from tools.code_slices import shared_state_text

        file = self.files[file_id]
        lines = self.lines(file_id)
        head = f"### {file_id} · {file.get('path')} · {file.get('language')}/{file.get('framework')} · {len(lines)} строк · {file.get('content_digest')}"
        out = [head]
        if file_id in self.failed:
            reason = next(row["reason"] for row in self.failures if row["file_id"] == file_id)
            out.append(f"Срез не удался ({reason}): ниже весь файл.")
        out.extend(_numbered(lines, start, end) for start, end in self.support_ranges(file_id))
        if file_id in self.slices:
            out += ["", "Таблица общего состояния (построена кодом):", shared_state_text(self.slices[file_id])]
        return "\n".join(out)

    def case_ranges(self, case_id: str) -> list[list[Any]]:
        """Line ranges that belong to the case: its methods and the helpers they call."""
        ranges = []
        for file_id, symbol_id in self.case_symbols.get(case_id, []):
            if file_id in self.slices and symbol_id in self.slices[file_id].symbols:
                member = self.slices[file_id].symbols[symbol_id]
                ranges.append([member.start, member.end, file_id])
                ranges.extend([helper.start, helper.end, file_id] for helper in self.slices[file_id].helpers[symbol_id])
            elif file_id in self.files:
                ranges.append([1, len(self.lines(file_id)), file_id])
        return ranges

    def case_code(self, case_id: str) -> str:
        out = []
        for file_id, symbol_id in self.case_symbols.get(case_id, []):
            if file_id in self.slices and symbol_id in self.slices[file_id].symbols:
                file = self.slices[file_id]
                member = file.symbols[symbol_id]
                helpers = ", ".join(f"{helper.name} L{helper.start}–L{helper.end}" for helper in file.helpers[symbol_id]) or "нет"
                out.append(f"  код {symbol_id} · {file_id} · {member.name} L{member.start}–L{member.end} · хелперы (в общем коде): {helpers}")
                out.append(file.text(member.start, member.end))
            else:
                out.append(f"  код {symbol_id} · {file_id}: метод в общем коде файла (срез не построен)")
        for row in self.manual.get(case_id, []):
            out.append(f"  ручное: {row}")
        return "\n".join(out)


def _part_text(context: _Context, code: _Code, *, part_id: str, title: str, areas: Sequence[Mapping[str, Any]], case_ids: Sequence[str],
               files: Sequence[str], lint: Sequence[Mapping[str, Any]], full_capabilities: bool) -> str:
    spec = context.specification
    document = context.document
    out = [f"# Ревью автотестов compact-v1 · {document['document_id']} r{spec['revision']} · {part_id} ({title})", "",
           f"Инструкции контроллера: {spec['instructions']}",
           "Ответ: coverage — строка на каждую область ниже, в этом порядке (area_id, status, refs — 1–3 якоря: [ID] кейса или строка кода "
           "L<номер> из этой части; у области кейса хотя бы одна ссылка на сам кейс или строки его метода и хелперов; note до 200 символов). "
           "findings — severity, code, related_ids (ID кейсов, проверок, файлов, символов), message до 600 символов; INFO не больше 5. "
           "corrections — пусто (ревью автотестов не правит кейсы). lint_dispositions — по каждому [LINT-…] части. "
           "required_checks — case_ids/requirement_ids и reason.", "", "## Области ответа"]
    for area in areas:
        if area["kind"] == "support":
            out.append(f"- {area['area_id']} — общий код файла {area['targets'][0]}: поля, настройка и очистка, фикстуры, хелперы, "
                       "граница приложения, общее состояние.")
        elif area["kind"] == "local":
            out.append(f"- {area['area_id']} — кейс {area['targets'][0]}: метод и вызываемые хелперы против шагов, входов, ожиданий и проверок кейса.")
        else:
            out.append(f"- {area['area_id']} — взаимодействия тестов этой части через общий код и состояние: порядок, побочные эффекты, "
                       "общие хелперы, изоляция данных.")
    # Automation parts have no pair areas: the IDs are enough to address a required check.
    out += ["", "## Все кейсы документа (ID)", ", ".join(context.case_order)]
    out += ["", "## Требования CREQ кейсов этой части", *(context.requirements[item] for item in context.requirement_ids_for(case_ids))]
    if full_capabilities:
        out += ["", "## Возможности (полностью)", *(context.capabilities[item["capability_id"]] for item in document["operation_capabilities"])]
    else:
        out += ["", "## Возможности (сигнатуры)", *(context.signatures[item["capability_id"]] for item in document["operation_capabilities"])]
    if lint:
        out += ["", "## Подозрения (сверки кода и линтер; ответь lint_dispositions по каждому)"]
        out.extend(f"[{row['lint_id']}] {row['rule']} · {', '.join(row['related_ids'])}: {row['message']}" for row in lint)
    if code.diagnostics:
        out += ["", "## Диагностика генератора", *(f"- {row}" for row in code.diagnostics)]
    out += ["", "## Общий код (SUPPORT), строки исходного файла", *(code.support_text(file_id) for file_id in files)]
    if case_ids:
        out += ["", "## Кейсы и их методы"]
        for case_id in case_ids:
            out += [context.cases[case_id], code.case_code(case_id), ""]
    return "\n".join(out).rstrip("\n") + "\n"


def build_automation_plan(specification: Mapping[str, Any], payload: Mapping[str, Any], *, input_byte_budget: int,
                          carry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from tools.review_lint import lint_automation, lint_document

    del carry  # automation revisions are reviewed whole (one complete r2 at most)
    spec = {key: copy.deepcopy(value) for key, value in specification.items() if key != "document_index"}
    document = payload["document"]
    automation = payload["automation"]
    projection = payload.get("projection") or build_projection(document)
    spec["projection_digest"] = projection["digest"]
    code = _Code(automation, document)
    lint = [*lint_automation(document, automation, code.slices), *(row for row in lint_document(document) if row["case_ids"])]
    context = _Context(spec, payload, lint)
    reserve = int(spec["response_reserve_bytes"])
    order = context.case_order

    def files_for(case_ids: Sequence[str]) -> list[str]:
        wanted = [file_id for case_id in case_ids for file_id in code.case_files.get(case_id, [])]
        return [file_id for file_id in code.files if file_id in wanted or file_id in code.support_only]

    homes: set[str] = set()

    def areas_for(case_ids: Sequence[str]) -> list[dict[str, Any]]:
        areas = []
        for file_id in files_for(case_ids):
            if file_id not in homes:
                # "local-file-" keeps the legacy reviewed-file accounting of review_output.
                areas.append({"area_id": "local-file-" + file_id, "kind": "support", "targets": [file_id],
                              "fingerprint": review_digest(code.support_text(file_id))})
        areas.extend({"area_id": "local-" + case_id, "kind": "local", "targets": [case_id],
                      "fingerprint": review_digest({"case": context.cases[case_id], "code": code.case_code(case_id)})} for case_id in case_ids)
        if case_ids:
            areas.append({"area_id": "cross-" + review_digest(list(case_ids))[7:23], "kind": "cross", "targets": list(case_ids),
                          "fingerprint": review_digest([context.case_fingerprint(case_id) for case_id in case_ids])})
        return areas

    def make(part_id: str, title: str, case_ids: Sequence[str]) -> dict[str, Any]:
        files = files_for(case_ids)
        areas = areas_for(case_ids)
        rows = context.lint_for(case_ids, source=False)
        full = any(area["kind"] == "support" for area in areas)
        text = _part_text(context, code, part_id=part_id, title=title, areas=areas, case_ids=case_ids, files=files, lint=rows, full_capabilities=full)
        size = len(text.encode("utf-8"))
        ranges = [[start, end, file_id] for file_id in files for start, end in code.support_ranges(file_id)]
        case_ranges = {case_id: code.case_ranges(case_id) for case_id in case_ids}
        for case_id in case_ids:
            ranges.extend(row for row in case_ranges[case_id] if row not in ranges)
        return {"part_id": part_id, "case_ids": list(case_ids), "blocks": [list(case_ids)], "source": False, "areas": areas,
                "carried_area_ids": [], "lint_ids": [row["lint_id"] for row in rows], "text": text, "code_ranges": ranges,
                "case_ranges": case_ranges, "requested_check": None, "input_byte_count": size,
                "blocked_reason": "REVIEW_CONTEXT_LIMIT" if size + reserve > input_byte_budget else None}

    # Greedy in document order: a case joins the current part while the part still fits.
    groups: list[list[str]] = []
    current: list[str] = []
    for case_id in order:
        if current and make("part-000000", "x", [*current, case_id])["blocked_reason"] is not None:
            groups.append(current)
            current = []
        current.append(case_id)
    if current:
        groups.append(current)
    parts = []
    for index, group in enumerate(groups, start=1):
        part = make(f"part-{index:06d}", f"{index} из {len(groups)}", group)
        homes.update(area["targets"][0] for area in part["areas"] if area["kind"] == "support")
        parts.append(part)
    plan = {"schema_version": PLAN_VERSION, "mode": MODE, "snapshot": spec, "input_byte_budget": input_byte_budget,
            "lint": lint, "parts": parts, "digest": "sha256:" + "0" * 64}
    if code.failures:
        plan["slice_failures"] = code.failures
    plan["digest"] = review_digest({key: value for key, value in plan.items() if key != "digest"})
    rows = validate_compact_plan(plan)
    if rows:
        raise ValueError(f"invalid compact automation plan: {rows}")
    return plan
