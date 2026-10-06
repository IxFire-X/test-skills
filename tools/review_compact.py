"""``compact-v1`` review: whole cases and shared context once per part, short checked answers.

The part input is a deterministic text (``tools.review_projection``) with ID anchors.
Cases are packed into blocks with content-defined boundaries; when the whole
document fits one part the review is one part, otherwise every part is a pair of
blocks, so every pair of cases meets in at least one part.  Each case has one home
part, where its own area is answered; every part has a ``cross`` area over all of
its cases.  The original requirements (verbatim, with line numbers), SREQ → CREQ
and the full capabilities go into the first part when they fit, otherwise into
their own part.

The answer (``schemas/review-part-output-compact.schema.json``) is short: one
coverage row per assigned area with 1–3 refs to anchors of this part, findings,
corrections by ``target_id``/``field`` from a closed dictionary, one disposition
per lint suspicion of the part, and required checks by case/requirement IDs.  The
controller verifies every ref and disposition at submit.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.review_parts import review_bytes, review_digest
from tools.review_projection import (anchors, build_projection, canonical_ids, capability_signature, source_document_text)

ROOT = Path(__file__).resolve().parents[1]
MODE = "compact-v1"
PLAN_VERSION = "2.0.0"
OUTPUT_VERSION = "2.0.0"
MAX_REFS = 3
NOTE_CHARS = 200
MESSAGE_CHARS = 600
MAX_INFO = 5
# Content-defined block boundaries: a case closes its block when its ID hash is a
# marker and the block is at least this full, or when the next case does not fit.
MARKER_MODULUS = 4
MIN_FILL = 0.85

_CASE_AREA = "local-"
_FIELD = re.compile(r"^(?P<name>[a-z_]+(?:\.[a-z_]+)?)(?:\[(?P<index>0|[1-9][0-9]*)\])?$")
# Closed dictionary of correctable fields per target kind: scalar text fields and list items.
CORRECTION_FIELDS = {
    "case": {"title": False, "objective": False, "preconditions": True, "management.folder": False, "management.status": False,
             "management.owner": False, "management.estimated_time": False, "management.components": True, "management.labels": True},
    "step": {"action": False, "test_data": False, "manual_reason": False},
    "expectation": {"text": False},
    "requirement": {"text": False},
}
_SERVICE_FIELD = re.compile(r"^management\.")


class CompactReviewError(ValueError):
    """A compact answer that the controller rejects, with stable diagnostics."""

    def __init__(self, rows: Sequence[Mapping[str, str]]) -> None:
        self.rows = [dict(row) for row in rows]
        super().__init__("; ".join(f"{row['code']} {row.get('path', '')}: {row['message']}" for row in self.rows))


def _row(code: str, path: str, message: str) -> dict[str, str]:
    return {"code": code, "path": path, "message": message}


# --------------------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------------------

def skill_digest(review_kind: str) -> str:
    """Digest of the production role SKILL with its references (part of the carry key)."""
    directory = ROOT / "skills" / review_kind
    digest = hashlib.sha256()
    for path in [directory / "SKILL.md", *sorted((directory / "references").glob("*.md"))]:
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def review_policy(review_kind: str, *, instructions: str, model_id: str | None) -> dict[str, Any]:
    """What a carried assessment must share with the new review: mode, role, SKILL, instructions, model."""
    return {"mode": MODE, "role_policy": "canonical-reviewer-v2" if review_kind == "tc-reviewer" else "autotest-static-reviewer-v2",
            "skill_digest": skill_digest(review_kind), "instructions_digest": review_digest(instructions), "model_id": model_id}


def compact_payload(payload: Mapping[str, Any], policy: Mapping[str, Any]) -> dict[str, Any]:
    """The review snapshot payload of a compact review: mode, projection and carry policy are frozen with it."""
    result = dict(payload)
    result["review_mode"] = MODE
    result["projection"] = build_projection(payload["document"])
    result["review_policy"] = dict(policy)
    return result


def validate_compact_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("review_mode") != MODE or payload.get("projection") != build_projection(payload["document"]):
        raise ValueError("compact review projection differs from the canonical document")
    policy = payload.get("review_policy")
    if (not isinstance(policy, dict) or set(policy) != {"mode", "role_policy", "skill_digest", "instructions_digest", "model_id"}
            or policy["mode"] != MODE):
        raise ValueError("invalid compact review policy")


# --------------------------------------------------------------------------------------
# corrections: closed dictionary -> one JSON pointer
# --------------------------------------------------------------------------------------

def id_index(document: Mapping[str, Any]) -> dict[str, list[tuple[str, str, str | None]]]:
    """Every correctable ID: (kind, pointer, owning case) — a list, so duplicates stay visible."""
    index: dict[str, list[tuple[str, str, str | None]]] = {}
    for offset, requirement in enumerate(document.get("requirements") or []):
        index.setdefault(requirement.get("requirement_id"), []).append(("requirement", f"/requirements/{offset}", None))
    for case_offset, case in enumerate(document.get("test_cases") or []):
        case_id = case.get("case_id")
        index.setdefault(case_id, []).append(("case", f"/test_cases/{case_offset}", case_id))
        for step_offset, step in enumerate(case.get("steps") or []):
            step_pointer = f"/test_cases/{case_offset}/steps/{step_offset}"
            index.setdefault(step.get("step_id"), []).append(("step", step_pointer, case_id))
            for expectation_offset, expectation in enumerate(step.get("expectations") or []):
                index.setdefault(expectation.get("expectation_id"), []).append(
                    ("expectation", f"{step_pointer}/expectations/{expectation_offset}", case_id))
    return index


def _pointer_value(value: Any, pointer: str) -> Any:
    for token in pointer.split("/")[1:] if pointer else []:
        value = value[int(token)] if isinstance(value, list) else value[token]
    return value


def correction_pointer(document: Mapping[str, Any], target_id: str, field: str, index: Mapping[str, list] | None = None) -> tuple[str, str | None]:
    """The single JSON pointer of ``field`` of ``target_id`` and the owning case; raises CompactReviewError."""
    index = id_index(document) if index is None else index
    owners = index.get(target_id) or []
    if not owners:
        raise CompactReviewError([_row("REVIEW_CORRECTION_TARGET", "", f"unknown correction target {target_id}")])
    if len(owners) > 1:
        raise CompactReviewError([_row("REVIEW_CORRECTION_AMBIGUOUS", "", f"correction target {target_id} is not unique")])
    kind, base, case_id = owners[0]
    match = _FIELD.fullmatch(field or "")
    listed = None if match is None else CORRECTION_FIELDS[kind].get(match["name"])
    if match is None or listed is None or listed != (match["index"] is not None):
        raise CompactReviewError([_row("REVIEW_CORRECTION_FIELD", "", f"field {field!r} is not in the correction dictionary of a {kind}")])
    pointer = base + "/" + match["name"].replace(".", "/") + ("" if match["index"] is None else "/" + match["index"])
    try:
        value = _pointer_value(document, pointer)
    except (KeyError, IndexError, TypeError):
        raise CompactReviewError([_row("REVIEW_CORRECTION_FIELD", "", f"{target_id} has no {field}")]) from None
    if not isinstance(value, str):
        raise CompactReviewError([_row("REVIEW_CORRECTION_FIELD", "", f"{target_id}.{field} is not a text value")])
    return pointer, case_id


def legacy_corrections(document: Mapping[str, Any], part_id: str, corrections: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Compact corrections of one part as the mechanical corrections ``apply_review_corrections`` takes."""
    index = id_index(document)
    rows = []
    for number, item in enumerate(corrections, start=1):
        pointer, case_id = correction_pointer(document, item["target_id"], item["field"], index)
        if _pointer_value(document, pointer) != item["before"]:
            raise CompactReviewError([_row("REVIEW_CORRECTION_BEFORE", f"/corrections/{number - 1}/before",
                                           f"before differs from {item['target_id']}.{item['field']}")])
        related = [case_id, item["target_id"]] if case_id and case_id != item["target_id"] else [item["target_id"]]
        rows.append({"id": f"FIX-{part_id}-{number}", "correction_kind": "MECHANICAL", "path": pointer, "before": item["before"],
                     "after": item["after"], "related_ids": related, "description": item["why"], "evidence": [item["target_id"]]})
    return rows


def is_service_correction(correction: Mapping[str, Any]) -> bool:
    return bool(_SERVICE_FIELD.match(str(correction.get("field", ""))))


# --------------------------------------------------------------------------------------
# packing
# --------------------------------------------------------------------------------------

def _marker(case_id: str) -> bool:
    return int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16) % MARKER_MODULUS == 0


def content_blocks(items: Sequence[tuple[str, int, frozenset]], cap: int, extra: Mapping[str, int]) -> list[list[str]]:
    """Blocks of case IDs with content-defined boundaries.

    ``items`` are ``(case_id, own_bytes, linked_ids)``; ``extra[linked]`` is the size of
    a linked requirement text, counted once per block.  A block closes after a marker
    case once it is ``MIN_FILL`` full, or before a case that would overflow ``cap``.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    linked: set = set()
    used = 0
    for case_id, size, links in items:
        add = size + sum(extra.get(item, 0) for item in links - linked)
        if current and used + add > cap:
            blocks.append(current)
            current, linked, used = [], set(), 0
            add = size + sum(extra.get(item, 0) for item in links)
        current.append(case_id)
        linked |= links
        used += add
        if _marker(case_id) and used >= MIN_FILL * cap:
            blocks.append(current)
            current, linked, used = [], set(), 0
    if current:
        blocks.append(current)
    return blocks


def block_pairs(count: int) -> list[tuple[int, ...]]:
    if count <= 1:
        return [(0,)] if count == 1 else []
    return [(left, right) for left in range(count) for right in range(left + 1, count)]


def home_pair(block: int, count: int) -> tuple[int, ...]:
    """The part where the cases of ``block`` are answered: its pair with the next block (cyclic)."""
    return (0,) if count == 1 else tuple(sorted((block, (block + 1) % count)))


class _Context:
    """Texts of one canonical snapshot, indexed for part assembly."""

    def __init__(self, specification: Mapping[str, Any], payload: Mapping[str, Any], lint: Sequence[Mapping[str, Any]]) -> None:
        document = payload["document"]
        self.specification = specification
        self.document = document
        projection = payload.get("projection") or build_projection(document)
        by_kind: dict[str, dict[str, str]] = {}
        for section in projection["sections"]:
            by_kind.setdefault(section["kind"], {})[section["anchor"]] = section["text"]
        self.header = by_kind["document"][document["document_id"]]
        self.cases = by_kind.get("case", {})
        self.requirements = by_kind.get("requirement", {})
        self.source_requirements = by_kind.get("source_requirement", {})
        self.capabilities = by_kind.get("capability", {})
        self.mapping = by_kind.get("mapping", {}).get(None, "")
        self.signatures = {item["capability_id"]: capability_signature(item) for item in document["operation_capabilities"]}
        self.case_order = [case["case_id"] for case in document["test_cases"]]
        self.case_titles = {case["case_id"]: case["title"].split("\n")[0] for case in document["test_cases"]}
        self.case_requirements = {case["case_id"]: list(case["requirement_ids"]) for case in document["test_cases"]}
        self.case_capabilities = {case["case_id"]: sorted({step["operation"].get("capability_id") for step in case["steps"]
                                                           if step.get("operation") and step["operation"].get("capability_id")})
                                  for case in document["test_cases"]}
        self.sources = [source_document_text(number, source) for number, source in enumerate(payload["sources"], start=1)]
        self.contexts = [source_document_text(number, source).replace(f"[SRC-{number}]", f"[CTX-{number}]", 1)
                         for number, source in enumerate(payload.get("contexts") or [], start=1)]
        self.lint = [dict(row) for row in lint]
        self.index = "\n".join(f"{case_id} · {', '.join(self.case_requirements[case_id])} · {self.case_titles[case_id]}" for case_id in self.case_order)

    def lint_for(self, case_ids: Sequence[str], *, source: bool) -> list[dict[str, Any]]:
        wanted = set(case_ids)
        return [row for row in self.lint if set(row["case_ids"]) & wanted or (source and not row["case_ids"])]

    def requirement_ids_for(self, case_ids: Sequence[str]) -> list[str]:
        linked = {requirement for case_id in case_ids for requirement in self.case_requirements[case_id]}
        return [requirement["requirement_id"] for requirement in self.document["requirements"] if requirement["requirement_id"] in linked]

    def case_fingerprint(self, case_id: str) -> str:
        """Everything the case area of ``case_id`` is answered from."""
        return review_digest({"case": self.cases[case_id],
                              "requirements": [self.requirements[item] for item in self.requirement_ids_for([case_id])],
                              "capabilities": [self.capabilities[item] for item in self.case_capabilities[case_id] if item in self.capabilities],
                              "lint": [row for row in self.lint if case_id in row["case_ids"]]})


def _area_line(area: Mapping[str, Any]) -> str:
    if area["kind"] == "source":
        return f"- {area['area_id']} — исходник: каждое условие исходных требований ↔ SREQ ↔ CREQ, требования без кейсов, возможности и их источники."
    if area["kind"] == "local":
        return f"- {area['area_id']} — кейс {area['targets'][0]}: шаги, данные, вызовы и входы, ожидания и проверки против CREQ/SREQ и возможностей."
    if area.get("question"):
        return f"- {area['area_id']} — проверка: {area['question']}"
    return (f"- {area['area_id']} — связи между всеми кейсами этой части ({len(area['targets'])}): одинаковые вызовы с разными ожиданиями, "
            "общее состояние, количество и полные списки ресурса, который меняют другие кейсы.")


def _part_text(context: _Context, *, part_id: str, title: str, areas: Sequence[Mapping[str, Any]], carried_ids: Sequence[str],
               case_ids: Sequence[str], source: bool, lint: Sequence[Mapping[str, Any]], notes: Sequence[str] = ()) -> str:
    spec = context.specification
    document = context.document
    answer = [area for area in areas if area["area_id"] not in set(carried_ids)]
    out = [f"# Ревью кейсов compact-v1 · {document['document_id']} r{spec['revision']} · {part_id} ({title})", "",
           f"Инструкции контроллера: {spec['instructions']}",
           "Ответ: coverage — строка на каждую область ниже, в этом порядке (area_id, status CHECKED|UNCHECKED, refs — 1–3 якоря [ID] "
           "из этой части, у области кейса хотя бы один якорь самого кейса; строки исходника — SRC-n:Lk; note — до 200 символов). "
           "findings — severity, code, related_ids, message (до 600 символов; INFO не больше 5). "
           "corrections — target_id, field из словаря, before, after, why. lint_dispositions — по каждому [LINT-…] этой части. "
           "required_checks — case_ids/requirement_ids и reason.", "", "## Области ответа"]
    out.extend(_area_line(area) for area in answer)
    if carried_ids:
        out.append(f"Перенесены из прошлого ревью без изменений (не отвечать): {', '.join(carried_ids)}.")
    out.extend(notes)
    out += ["", "## Индекс всех кейсов документа", context.index]
    requirement_ids = [item["requirement_id"] for item in document["requirements"]] if source else context.requirement_ids_for(case_ids)
    out += ["", "## Требования CREQ" + ("" if source else " кейсов этой части"), *(context.requirements[item] for item in requirement_ids)]
    if source:
        out += ["", "## Возможности (полностью)", *(context.capabilities[item["capability_id"]] for item in document["operation_capabilities"])]
    else:
        out += ["", "## Возможности (сигнатуры)", *(context.signatures[item["capability_id"]] for item in document["operation_capabilities"])]
    if lint:
        out += ["", "## Подозрения линтера (ответь lint_dispositions по каждому)"]
        out.extend(f"[{row['lint_id']}] {row['rule']} · {', '.join(row['related_ids'])}: {row['message']}" for row in lint)
    if source:
        out += ["", "## Исходные требования (дословно, с номерами строк)", *context.sources]
        out += ["", "## SREQ — нормализованные исходные требования", *(context.source_requirements[item["source_requirement_id"]]
                                                                      for item in document["source_requirements"])]
        out += ["", "## Связь SREQ → CREQ", context.mapping]
        if context.contexts:
            out += ["", "## Файлы проекта из источников возможностей", *context.contexts]
    if case_ids:
        out += ["", "## Кейсы", *(context.cases[case_id] + "\n" for case_id in case_ids)]
    return "\n".join(out).rstrip("\n") + "\n"


def _part(context: _Context, budget: int, reserve: int, *, part_id: str, title: str, areas: list, carried_ids: Sequence[str],
          case_ids: Sequence[str], source: bool, requested_check: str | None = None, notes: Sequence[str] = (),
          blocks: Sequence[Sequence[str]] | None = None, with_lint: bool = True) -> dict[str, Any]:
    lint = context.lint_for(case_ids, source=source) if with_lint else []
    text = _part_text(context, part_id=part_id, title=title, areas=areas, carried_ids=carried_ids, case_ids=case_ids,
                      source=source, lint=lint, notes=notes)
    size = len(text.encode("utf-8"))
    return {"part_id": part_id, "case_ids": list(case_ids), "blocks": [list(block) for block in (blocks or [case_ids])],
            "source": source, "areas": copy.deepcopy(areas), "carried_area_ids": list(carried_ids),
            "lint_ids": [row["lint_id"] for row in lint], "text": text, "requested_check": requested_check,
            "input_byte_count": size, "blocked_reason": "REVIEW_CONTEXT_LIMIT" if size + reserve > budget else None}


def _cross_area(context: _Context, case_ids: Sequence[str]) -> dict[str, Any]:
    return {"area_id": "cross-" + review_digest(list(case_ids))[7:23], "kind": "cross", "targets": list(case_ids),
            "fingerprint": review_digest({"cases": [context.case_fingerprint(case_id) for case_id in case_ids],
                                          "lint": [row for row in context.lint if len(set(row["case_ids"]) & set(case_ids)) > 1]})}


def _local_area(context: _Context, case_id: str) -> dict[str, Any]:
    return {"area_id": _CASE_AREA + case_id, "kind": "local", "targets": [case_id], "fingerprint": context.case_fingerprint(case_id)}


def _source_area(context: _Context) -> dict[str, Any]:
    document = context.document
    return {"area_id": "source-000001", "kind": "source", "targets": [source.split("\n", 1)[0] for source in context.sources] or [document["document_id"]],
            "fingerprint": review_digest({"sources": context.sources, "contexts": context.contexts, "mapping": context.mapping,
                                          "sreq": context.source_requirements, "creq": context.requirements, "capabilities": context.capabilities,
                                          "index": context.index, "lint": context.lint_for([], source=True)})}


def build_compact_plan(specification: Mapping[str, Any], payload: Mapping[str, Any], *, input_byte_budget: int,
                       carry: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Pack a compact review deterministically; ``carry`` (parent plan/aggregate) moves unchanged areas over."""
    if payload.get("automation") is not None:
        from tools.review_compact_automation import build_automation_plan

        return build_automation_plan(specification, payload, input_byte_budget=input_byte_budget, carry=carry)
    from tools.review_lint import lint_document

    spec = {key: copy.deepcopy(value) for key, value in specification.items() if key != "document_index"}
    projection = payload.get("projection") or build_projection(payload["document"])
    spec["projection_digest"] = projection["digest"]
    context = _Context(spec, payload, lint_document(payload["document"]))
    reserve = int(spec["response_reserve_bytes"])
    usable = input_byte_budget - reserve
    order = context.case_order
    source_area = _source_area(context)
    layouts: list[dict[str, Any]] = []
    whole = _part(context, input_byte_budget, reserve, part_id="part-000001", title="1 из 1", case_ids=order, source=True,
                  areas=[source_area, *(_local_area(context, case_id) for case_id in order), _cross_area(context, order)], carried_ids=())
    if whole["blocked_reason"] is None:
        layouts.append({"case_ids": order, "blocks": [order], "source": True, "home": order})
    else:
        skeleton = _part(context, input_byte_budget, reserve, part_id="part-000000", title="0 из 0", case_ids=[], source=False,
                         areas=[], carried_ids=())
        # A requirement linked to many cases rides in every part once: it is overhead,
        # not a block weight.  The others are counted once per block that links them.
        linked: dict[str, int] = {}
        for case_id in order:
            for requirement in context.case_requirements[case_id]:
                linked[requirement] = linked.get(requirement, 0) + 1
        common = {item for item, count in linked.items() if count >= max(2, len(order) // 4)}
        extra = {item: len(text.encode("utf-8")) + 1 for item, text in context.requirements.items() if item not in common}
        overhead = (skeleton["input_byte_count"] + sum(len(row["message"]) + 120 for row in context.lint)
                    + sum(len(context.requirements[item].encode("utf-8")) + 1 for item in common if item in context.requirements))
        cap = max(1, (usable - overhead) // 2)
        # Case text plus its line in the area list (an upper bound: only home cases have one).
        sizes = {case_id: len(context.cases[case_id].encode("utf-8")) + 2 + len(_area_line(_local_area(context, case_id)).encode("utf-8"))
                 for case_id in order}
        items = [(case_id, sizes[case_id], frozenset(context.case_requirements[case_id]) - common) for case_id in order]
        blocks = content_blocks(items, cap, extra)
        pairs = block_pairs(len(blocks))
        homes = {block: home_pair(block, len(blocks)) for block in range(len(blocks))}
        source_alone = True
        for pair in pairs:
            case_ids = [case_id for block in pair for case_id in blocks[block]]
            home = [case_id for block in range(len(blocks)) if homes[block] == pair for case_id in blocks[block]]
            layouts.append({"case_ids": case_ids, "blocks": [blocks[block] for block in pair], "source": False, "home": home})
        if layouts:
            first = layouts[0]
            trial = _part(context, input_byte_budget, reserve, part_id="part-000001", title="1", case_ids=first["case_ids"], source=True,
                          areas=[source_area, *(_local_area(context, case_id) for case_id in first["home"]), _cross_area(context, first["case_ids"])],
                          carried_ids=())
            if trial["blocked_reason"] is None:
                first["source"] = True
                source_alone = False
        if source_alone:
            layouts.insert(0, {"case_ids": [], "blocks": [], "source": True, "home": []})
    carried_rows, carried_ids = _carry(context, carry, layouts, source_area)
    parts, total = [], sum(1 for layout in layouts if not _fully_carried(context, layout, source_area, carried_ids))
    for layout in layouts:
        if _fully_carried(context, layout, source_area, carried_ids):
            continue
        areas = ([source_area] if layout["source"] else []) + [_local_area(context, case_id) for case_id in layout["home"]]
        if layout["case_ids"]:
            areas.append(_cross_area(context, layout["case_ids"]))
        part_id = f"part-{len(parts) + 1:06d}"
        parts.append(_part(context, input_byte_budget, reserve, part_id=part_id, title=f"{len(parts) + 1} из {total}", case_ids=layout["case_ids"],
                           source=layout["source"], areas=areas, carried_ids=[area["area_id"] for area in areas if area["area_id"] in carried_ids],
                           blocks=layout["blocks"]))
    if not parts:
        # Nothing changed at all: the whole review is sent again rather than carried blind.
        return build_compact_plan(specification, payload, input_byte_budget=input_byte_budget, carry=None)
    plan = {"schema_version": PLAN_VERSION, "mode": MODE, "snapshot": spec, "input_byte_budget": input_byte_budget,
            "lint": context.lint, "parts": parts, "digest": "sha256:" + "0" * 64}
    if carried_rows:
        plan["carried"] = carried_rows
    plan["digest"] = review_digest({key: value for key, value in plan.items() if key != "digest"})
    rows = validate_compact_plan(plan)
    if rows:
        raise ValueError(f"invalid compact review plan: {rows}")
    return plan


def _layout_areas(context: _Context, layout: Mapping[str, Any], source_area: Mapping[str, Any]) -> list[dict[str, Any]]:
    areas = ([dict(source_area)] if layout["source"] else []) + [_local_area(context, case_id) for case_id in layout["home"]]
    if layout["case_ids"]:
        areas.append(_cross_area(context, layout["case_ids"]))
    return areas


def _fully_carried(context: _Context, layout: Mapping[str, Any], source_area: Mapping[str, Any], carried_ids: set) -> bool:
    return bool(carried_ids) and all(area["area_id"] in carried_ids for area in _layout_areas(context, layout, source_area))


def _carry(context: _Context, carry: Mapping[str, Any] | None, layouts: Sequence[Mapping[str, Any]],
           source_area: Mapping[str, Any]) -> tuple[list[dict[str, Any]], set]:
    """Areas whose exact input, policy and checked r1 coverage carry over (rework r2 only).

    An area carries when the parent plan had the same area ID with the same
    fingerprint, the parent aggregate checked it, the review policy (mode, role,
    SKILL and instructions digests, model) is the same, and no BLOCKING parent
    finding names one of its cases.
    """
    if not carry:
        return [], set()
    parent_plan, parent_aggregate = carry["plan"], carry["aggregate"]
    if carry.get("policy") != carry.get("parent_policy") or parent_plan.get("mode") != MODE:
        return [], set()
    parent = {}
    for part in [*parent_plan["parts"], *parent_plan.get("additions", [])]:
        for area in part["areas"]:
            parent[area["area_id"]] = (area, part["part_id"])
    for row in parent_plan.get("carried", []):
        parent[row["area"]["area_id"]] = (row["area"], row.get("from_part_id"))
    checked = set(parent_aggregate["checked_scope_ids"])
    blocking = {identifier for finding in parent_aggregate["findings"] if finding["severity"] == "BLOCKING" for identifier in finding["related_ids"]}
    rows, ids = [], set()
    for layout in layouts:
        for area in _layout_areas(context, layout, source_area):
            before = parent.get(area["area_id"])
            if (before is None or area["area_id"] not in checked or before[0]["fingerprint"] != area["fingerprint"]
                    or set(area["targets"]) & blocking or area["area_id"] in ids):
                continue
            ids.add(area["area_id"])
            rows.append({"area": area, "fingerprint": area["fingerprint"], "from_attempt_id": carry["from_attempt_id"],
                         "from_plan_digest": parent_plan["digest"], "from_part_id": before[1]})
    return rows, ids


# --------------------------------------------------------------------------------------
# plan validation and the exact part input
# --------------------------------------------------------------------------------------

def part_input(plan: Mapping[str, Any], part: Mapping[str, Any]) -> dict[str, Any]:
    return {"plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"], "review_kind": plan["snapshot"]["review_kind"],
            "revision": plan["snapshot"]["revision"], "mode": MODE, "part_id": part["part_id"], "text": part["text"]}


def validate_compact_plan(plan: Mapping[str, Any]) -> list[dict[str, str]]:
    from tools.schema_validation import schema_diagnostics

    base = {key: value for key, value in plan.items() if key not in {"additions", "unavailable"}}
    rows = schema_diagnostics(base, ROOT / "schemas/review-plan-compact.schema.json", ROOT)
    if rows:
        return rows
    if base["digest"] != review_digest({key: value for key, value in base.items() if key != "digest"}):
        return [_row("REVIEW_PLAN_DIGEST", "", "compact plan digest differs")]
    parts = [*base["parts"], *plan.get("additions", [])]
    areas = [area["area_id"] for part in parts for area in part["areas"] if area["area_id"] not in part["carried_area_ids"]]
    areas += [row["area"]["area_id"] for row in base.get("carried", [])]
    if ([part["part_id"] for part in parts] != [f"part-{index:06d}" for index in range(1, len(parts) + 1)]
            or len(set(areas)) != len(areas)):
        rows.append(_row("REVIEW_PLAN_OWNERSHIP", "", "compact parts or areas are not uniquely owned"))
    for part in parts:
        size = len(part["text"].encode("utf-8"))
        blocked = "REVIEW_CONTEXT_LIMIT" if size + plan["snapshot"]["response_reserve_bytes"] > plan["input_byte_budget"] else None
        if part["input_byte_count"] != size or part["blocked_reason"] != blocked or set(anchors(part["text"])) and len(anchors(part["text"])) != len(set(anchors(part["text"]))):
            rows.append(_row("REVIEW_PART_INPUT", part["part_id"], "compact part input differs from its text"))
    return rows


def answer_areas(part: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [area for area in part["areas"] if area["area_id"] not in set(part["carried_area_ids"])]


# --------------------------------------------------------------------------------------
# answers
# --------------------------------------------------------------------------------------

_LINE_REF = re.compile(r"^(?P<anchor>(?:SRC|CTX)-[0-9]+):L(?P<line>[0-9]+)$")
_CODE_REF = re.compile(r"^(?:(?P<file>[A-Za-z0-9_.:-]+):)?L(?P<line>[0-9]+)$")


def _part_refs(part: Mapping[str, Any]) -> tuple[set, dict[str, int], list[tuple[int, int, str]]]:
    """Anchors of a part, line counts of its numbered documents and code line ranges."""
    defined = set(anchors(part["text"]))
    lines: dict[str, int] = {}
    for match in re.finditer(r"^\[((?:SRC|CTX)-[0-9]+)\] .* \(([0-9]+) строк\)$", part["text"], re.MULTILINE):
        lines[match.group(1)] = int(match.group(2))
    ranges = [tuple(row) for row in part.get("code_ranges", [])]
    return defined, lines, ranges


def _ref_ok(ref: str, defined: set, lines: Mapping[str, int], ranges: Sequence[tuple]) -> bool:
    if ref in defined:
        return True
    match = _LINE_REF.fullmatch(ref)
    if match:
        return match["anchor"] in lines and 1 <= int(match["line"]) <= lines[match["anchor"]]
    match = _CODE_REF.fullmatch(ref)
    if match and ranges:
        line = int(match["line"])
        return any(start <= line <= end and (match["file"] in (None, file_id)) for start, end, file_id in ranges)
    return False


def known_ids(plan: Mapping[str, Any], document: Mapping[str, Any], automation: Mapping[str, Any] | None = None) -> set:
    ids = set(canonical_ids(document)) | {row["lint_id"] for row in plan.get("lint", [])}
    if automation is not None:
        generated = automation["artifacts"]
        ids |= {row["file_id"] for row in generated["generated_files"]} | {row["symbol_id"] for row in generated["generated_symbols"]}
    return ids


def resolve_check(document: Mapping[str, Any], check: Mapping[str, Any]) -> dict[str, Any] | None:
    """A required check normalized to the cases it must see, in document order; None when it names unknown IDs."""
    cases = [case["case_id"] for case in document["test_cases"]]
    wanted = list(check.get("case_ids") or [])
    if any(case_id not in cases for case_id in wanted):
        return None
    sreq = {mapping["source_requirement_id"]: set(mapping["canonical_requirement_ids"]) for mapping in document["source_to_canonical_mappings"]}
    creq = {item["requirement_id"] for item in document["requirements"]}
    for requirement_id in check.get("requirement_ids") or []:
        linked = sreq.get(requirement_id, {requirement_id} if requirement_id in creq else set())
        holders = [case["case_id"] for case in document["test_cases"] if linked & set(case["requirement_ids"])]
        if not holders:
            return None
        wanted.extend(holders)
    if not wanted:
        return None
    return {"case_ids": [case_id for case_id in cases if case_id in set(wanted)], "reason": check["reason"]}


def validate_answer(plan: Mapping[str, Any], part: Mapping[str, Any], result: Mapping[str, Any], document: Mapping[str, Any],
                    automation: Mapping[str, Any] | None = None) -> list[dict[str, str]]:
    """Every rule the controller enforces on a compact answer; the empty list accepts it."""
    from tools.schema_validation import schema_diagnostics

    rows = schema_diagnostics(dict(result), ROOT / "schemas/review-part-output-compact.schema.json", ROOT)
    if rows:
        return rows
    if (part not in [*plan["parts"], *plan.get("additions", [])] or part["blocked_reason"] is not None
            or result["plan_digest"] != plan["digest"] or result["snapshot_digest"] != plan["snapshot"]["snapshot_digest"]
            or result["part_id"] != part["part_id"] or result["input_digest"] != review_digest(part_input(plan, part))):
        rows.append(_row("REVIEW_PART_BINDING", part["part_id"], "the answer is bound to another part or plan"))
    areas = answer_areas(part)
    if [row["area_id"] for row in result["coverage"]] != [area["area_id"] for area in areas]:
        rows.append(_row("REVIEW_PART_COVERAGE", "/coverage", "coverage needs one row per assigned area, in order: "
                         + ", ".join(area["area_id"] for area in areas)))
    defined, lines, ranges = _part_refs(part)
    own = _own_anchors(part, document, automation)
    by_id = {area["area_id"]: area for area in areas}
    for index, row in enumerate(result["coverage"]):
        bad = [ref for ref in row["refs"] if not _ref_ok(ref, defined, lines, ranges)]
        if bad:
            rows.append(_row("REVIEW_REF_UNKNOWN", f"/coverage/{index}/refs", f"refs are not anchors of this part: {', '.join(bad)}"))
            continue
        area = by_id.get(row["area_id"])
        if area is not None and area["kind"] == "local" and not _own_ref(part, area["targets"][0], row["refs"], own):
            rows.append(_row("REVIEW_REF_FOREIGN", f"/coverage/{index}/refs",
                             f"a case area cites at least one anchor of its own case {area['targets'][0]}"))
    known = known_ids(plan, document, automation)
    infos = 0
    for index, finding in enumerate(result["findings"]):
        infos += finding["severity"] == "INFO"
        unknown = [item for item in finding["related_ids"] if item not in known]
        if unknown:
            rows.append(_row("REVIEW_FINDING_UNKNOWN_ID", f"/findings/{index}/related_ids", f"unknown IDs: {', '.join(unknown)}"))
    if infos > MAX_INFO:
        rows.append(_row("REVIEW_INFO_LIMIT", "/findings", f"at most {MAX_INFO} INFO findings per part"))
    expected_lint = list(part["lint_ids"])
    answered = [row["lint_id"] for row in result["lint_dispositions"]]
    if sorted(answered) != sorted(expected_lint) or len(set(answered)) != len(answered):
        missing = [item for item in expected_lint if item not in answered]
        rows.append(_row("REVIEW_LINT_UNANSWERED", "/lint_dispositions",
                         "every lint suspicion of the part needs exactly one disposition" + (f"; missing: {', '.join(missing)}" if missing else "")))
    if automation is None and result["corrections"]:
        index = id_index(document)
        for number, correction in enumerate(result["corrections"]):
            try:
                pointer, _case = correction_pointer(document, correction["target_id"], correction["field"], index)
            except CompactReviewError as error:
                rows.extend({**item, "path": f"/corrections/{number}"} for item in error.rows)
                continue
            if correction["target_id"] not in defined:
                rows.append(_row("REVIEW_CORRECTION_SCOPE", f"/corrections/{number}", f"{correction['target_id']} is not in this part"))
            elif _pointer_value(document, pointer) != correction["before"]:
                rows.append(_row("REVIEW_CORRECTION_BEFORE", f"/corrections/{number}/before", "before differs from the current value"))
        pointers = [correction_pointer(document, item["target_id"], item["field"], index)[0] for item in result["corrections"]
                    if not any(row["path"].startswith("/corrections/") and row["code"] in {"REVIEW_CORRECTION_TARGET", "REVIEW_CORRECTION_AMBIGUOUS", "REVIEW_CORRECTION_FIELD"}
                               for row in rows)]
        if len(set(pointers)) != len(pointers):
            rows.append(_row("REVIEW_CORRECTION_AMBIGUOUS", "/corrections", "two corrections change the same field"))
    elif automation is not None and result["corrections"]:
        rows.append(_row("REVIEW_CORRECTION_SCOPE", "/corrections", "automation review proposes no document corrections"))
    for index, check in enumerate(result["required_checks"]):
        if resolve_check(document, check) is None:
            rows.append(_row("REVIEW_CHECK_SCOPE", f"/required_checks/{index}", "the check names unknown cases or requirements"))
    return rows


def _own_anchors(part: Mapping[str, Any], document: Mapping[str, Any], automation: Mapping[str, Any] | None) -> dict[str, set]:
    """Anchors that belong to each case of the part (its own IDs, and code lines of its slice)."""
    from tools.review_projection import case_anchor_ids

    own = {case["case_id"]: set(case_anchor_ids(case)) for case in document["test_cases"] if case["case_id"] in set(part["case_ids"])}
    return own


def _own_ref(part: Mapping[str, Any], case_id: str, refs: Sequence[str], own: Mapping[str, set]) -> bool:
    """A case area cites one of its own anchors, or a code line of its method or the helpers it calls."""
    if set(refs) & own.get(case_id, set()):
        return True
    ranges = [tuple(row) for row in (part.get("case_ranges") or {}).get(case_id, [])]
    for ref in refs:
        match = _CODE_REF.fullmatch(ref)
        if match and any(start <= int(match["line"]) <= end and match["file"] in (None, file_id) for start, end, file_id in ranges):
            return True
    return False


# --------------------------------------------------------------------------------------
# additions: required checks and correction re-checks
# --------------------------------------------------------------------------------------

def _context_for(plan: Mapping[str, Any], payload: Mapping[str, Any]) -> _Context:
    return _Context(plan["snapshot"], payload, plan.get("lint", []))


def check_part(plan: Mapping[str, Any], payload: Mapping[str, Any], check: Mapping[str, Any], index: int) -> dict[str, Any]:
    """One bounded check part: the named cases with the shared context and the question."""
    context = _context_for(plan, payload)
    digest = review_digest(check)
    area = {"area_id": "cross-" + digest[7:], "kind": "cross", "targets": list(check["case_ids"]), "question": check["reason"],
            "fingerprint": review_digest({"cases": [context.case_fingerprint(case_id) for case_id in check["case_ids"]], "reason": check["reason"]})}
    notes = [f"Вопрос проверки: {check['reason']}"]
    for correction in check.get("corrections") or []:
        notes.append(f"Предложенная правка {correction['target_id']}.{correction['field']}: «{correction['before']}» → «{correction['after']}» ({correction['why']})")
    return _part(context, plan["input_byte_budget"], plan["snapshot"]["response_reserve_bytes"], part_id=f"part-{index:06d}", title="проверка",
                 areas=[area], carried_ids=(), case_ids=check["case_ids"], source=False, requested_check=digest, notes=notes, with_lint=False)


def _valid_results(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    parts = {part["part_id"]: part for part in [*plan["parts"], *plan.get("additions", [])]}
    return [result for result in results if result.get("part_id") in parts
            and not validate_answer(plan, parts[result["part_id"]], result, payload["document"], payload.get("automation"))]


def required_checks(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalized checks the answers asked for, plus correction re-checks once every base part answered."""
    document = payload["document"]
    valid = _valid_results(plan, payload, results)
    checks = [resolve_check(document, check) for result in valid for check in result["required_checks"]]
    behavioral = [(result["part_id"], item) for result in valid for item in result["corrections"] if not is_service_correction(item)]
    completed = {result["part_id"] for result in results}
    base_done = all(part["part_id"] in completed or part["blocked_reason"] or part["part_id"] in plan.get("unavailable", {}) for part in plan["parts"])
    if behavioral and base_done and payload.get("automation") is None:
        index = id_index(document)
        corrected: dict[str, list] = {}
        for _part_id, item in behavioral:
            _pointer, case_id = correction_pointer(document, item["target_id"], item["field"], index)
            corrected.setdefault(case_id or item["target_id"], []).append(dict(item))
        # The corrected case is re-checked only in the parts that hold it.
        for part in plan["parts"]:
            touched = [case_id for case_id in part["case_ids"] if case_id in corrected]
            if not touched:
                continue
            proposals = [item for case_id in touched for item in corrected[case_id]]
            checks.append({"case_ids": list(part["case_ids"]), "reason": "Проверь, что предложенные правки сохраняют смысл кейсов "
                           + ", ".join(touched) + " и не создают противоречий с остальными кейсами этой части.",
                           "corrections": proposals})
    unique = []
    for check in checks:
        if check is not None and check not in unique:
            unique.append(check)
    return unique


def additional_parts(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    parts = [*plan["parts"], *plan.get("additions", [])]
    known = {part["requested_check"] for part in parts}
    additions = []
    for check in required_checks(plan, payload, results):
        digest = review_digest(check)
        if digest in known:
            continue
        known.add(digest)
        additions.append(check_part(plan, payload, check, len(parts) + len(additions) + 1))
    return additions


# --------------------------------------------------------------------------------------
# aggregate
# --------------------------------------------------------------------------------------

def aggregate(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The legacy aggregate shape (``review_output`` reads it), built from compact answers."""
    document = payload["document"]
    diagnostics = validate_compact_plan(plan)
    parts = [*plan["parts"], *plan.get("additions", [])]
    by_id: dict[str, list] = {}
    for result in results:
        by_id.setdefault(str(result.get("part_id")), []).append(result)
    if set(by_id) - {part["part_id"] for part in parts}:
        diagnostics.append(_row("REVIEW_FOREIGN_PART", "", "an answer names an unknown part"))
    open_rows: list[tuple[str, dict[str, Any], list[str]]] = []
    findings, corrections, checked, bindings, dispositions = [], [], [], [], []
    for part in parts:
        candidates = by_id.get(part["part_id"], [])
        errors = (validate_answer(plan, part, candidates[0], document, payload.get("automation")) if len(candidates) == 1
                  else [_row("REVIEW_PART_MISSING_OR_DUPLICATE", part["part_id"], "one answer per part")])
        if errors:
            diagnostics.extend(errors)
            reason = plan.get("unavailable", {}).get(part["part_id"]) or part["blocked_reason"] or errors[0]["code"]
            open_rows.extend((part["part_id"], {"scope_id": area["area_id"], "reason": reason}, []) for area in answer_areas(part))
            continue
        result = candidates[0]
        bindings.append({"part_id": part["part_id"], "result_digest": review_digest(result)})
        own_checks = [review_digest(resolve_check(document, check)) for check in result["required_checks"]]
        for row in result["coverage"]:
            if row["status"] == "CHECKED":
                checked.append(row["area_id"])
            else:
                open_rows.append((part["part_id"], {"scope_id": row["area_id"], "reason": row["note"] or "UNCHECKED"}, own_checks))
        for finding in result["findings"]:
            findings.append({"severity": finding["severity"], "code": finding["code"], "message": finding["message"],
                             "evidence": list(finding["related_ids"]), "related_ids": list(finding["related_ids"])})
        if payload.get("automation") is None:
            for item in legacy_corrections(document, part["part_id"], result["corrections"]):
                if all(item["path"] != existing["path"] for existing in corrections):
                    corrections.append(item)
        dispositions.extend({"part_id": part["part_id"], **row} for row in result["lint_dispositions"])
    checks = required_checks(plan, payload, results)
    requested = {review_digest(check): check for check in checks}
    for index, part in enumerate(parts, start=1):
        if part["requested_check"] is not None:
            check = requested.get(part["requested_check"])
            if check is None or part != check_part(plan, payload, check, index):
                diagnostics.append(_row("REVIEW_ADDITION_BINDING", part["part_id"], "the check part is not the one its check asked for"))
    carried = [{"scope_id": row["area"]["area_id"], "fingerprint": row["fingerprint"], "from_attempt_id": row["from_attempt_id"],
                "from_plan_digest": row["from_plan_digest"]} for row in plan.get("carried", [])]
    checked.extend(row["scope_id"] for row in carried)
    resolved: list[dict[str, Any]] = []
    while True:
        closing = {part["requested_check"]: part["part_id"] for part in parts
                   if part["requested_check"] is not None and all(area["area_id"] in checked for area in answer_areas(part))}
        ready = [row for row in open_rows if row[2] and all(digest in closing for digest in row[2])]
        if not ready:
            break
        for row in ready:
            open_rows.remove(row)
            checked.append(row[1]["scope_id"])
            resolved.append({"part_id": row[0], "scope_id": row[1]["scope_id"], "resolved_by": sorted({closing[digest] for digest in row[2]})})
    unchecked = [row[1] for row in open_rows]
    closed = {part["requested_check"] for part in parts if all(area["area_id"] in checked for area in answer_areas(part))}
    for digest, check in requested.items():
        if digest not in closed:
            unchecked.append({"scope_id": "cross-" + digest[7:], "reason": check["reason"]})
    complete = not diagnostics and not unchecked
    blocked = bool(diagnostics or unchecked or any(item["severity"] == "BLOCKING" for item in findings))
    result = {"plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"],
              "addition_digests": [review_digest(part) for part in plan.get("additions", [])],
              "parts": bindings, "checked_scope_ids": checked, "unchecked": unchecked, "findings": findings,
              "corrections": corrections, "required_checks": [{key: value for key, value in check.items()} for check in checks],
              "complete": complete, "blocked": blocked, "eligible": complete and not blocked, "diagnostics": diagnostics,
              "mode": MODE, "lint_dispositions": dispositions}
    if resolved:
        result["resolved_unchecked"] = resolved
    if carried:
        result["carried"] = carried
    return result


def envelope_bytes(plan: Mapping[str, Any]) -> int:
    return sum(len(review_bytes(part_input(plan, part))) for part in plan["parts"])


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
