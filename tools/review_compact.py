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
# Additional check parts of a plan with this policy (live Petclinic run 2026-10-08: 38 base parts registered 58 more):
# added once, after every base part answered, from the base parts' answers only; one part per checked target
# (duplicate required checks merged); a correction is re-checked only on the cases it touches (a requirement
# correction — on the cases linked to that requirement), and a rewording the answer ranks INFO (or no finding
# names) is only journalled; additions are independent (a batch); at most a quarter of the base parts — the
# rest stays unchecked with REVIEW_CHECK_LIMIT, so the review is incomplete instead of silently expensive.
CHECK_POLICY = "merged-after-base-v1"
CHECK_LIMIT_REASON = "REVIEW_CHECK_LIMIT"
# A required check of a CHECK_POLICY plan whose requirements link more cases than one check part holds (live Petclinic
# runs b and d: "every SREQ", CREQ-B1-T-ISOLATION — 81 cases) checks only the cases it names; naming none, it gets no
# part: the aggregate keeps it as a WARNING finding and a question for the analyst, outside the addition limit.
TOO_BROAD_REASON = "REVIEW_CHECK_TOO_BROAD"


def check_limit(plan: Mapping[str, Any]) -> int:
    """How many additional parts a ``CHECK_POLICY`` plan may add: a quarter of its base parts, at least one."""
    return max(1, len(plan["parts"]) // 4)
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
        self.source_docs = [(f"SRC-{number}", source) for number, source in enumerate(payload["sources"], start=1)]
        self.context_docs = [(f"CTX-{number}", source) for number, source in enumerate(payload.get("contexts") or [], start=1)]

    def source_items(self) -> list[dict[str, Any]]:
        """Everything the source area compares, as packable items in a fixed order."""
        items = [{"group": "docs", "anchor": anchor, "text": text} for (anchor, _source), text in zip(self.source_docs, self.sources)]
        items += [{"group": "sreq", "anchor": item["source_requirement_id"], "text": self.source_requirements[item["source_requirement_id"]]}
                  for item in self.document["source_requirements"]]
        items.append({"group": "mapping", "anchor": "SREQ→CREQ", "text": self.mapping})
        items += [{"group": "capabilities", "anchor": item["capability_id"], "text": self.capabilities[item["capability_id"]]}
                  for item in self.document["operation_capabilities"]]
        items += [{"group": "contexts", "anchor": anchor, "text": text} for (anchor, _source), text in zip(self.context_docs, self.contexts)]
        return items

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
        if any(str(target).startswith(("SRC-", "SREQ")) for target in area["targets"]):
            return f"- {area['area_id']} — исходник: каждое условие исходных требований ↔ SREQ ↔ CREQ, требования без кейсов, возможности и их источники."
        # A continuation part holds only capabilities and project files (live Petclinic run, 2026-10-08: asked for
        # "every source condition" here, the reviewer found no source text and asked to re-check every SREQ).
        return (f"- {area['area_id']} — исходник (продолжение): возможности этой части и файлы проекта из их источников — сигнатуры, "
                "входы и выходы против источников, CREQ и кейсов. Текст требований (SRC, SREQ) сверяет другая часть исходника, здесь его нет.")
    if area["kind"] == "local":
        return f"- {area['area_id']} — кейс {area['targets'][0]}: шаги, данные, вызовы и входы, ожидания и проверки против CREQ/SREQ и возможностей."
    if area.get("question"):
        return f"- {area['area_id']} — проверка: {area['question']}"
    return (f"- {area['area_id']} — связи между всеми кейсами этой части ({len(area['targets'])}): одинаковые вызовы с разными ожиданиями, "
            "общее состояние, количество и полные списки ресурса, который меняют другие кейсы.")


_SOURCE_GROUPS = (("docs", "## Исходные требования (дословно, с номерами строк)"), ("sreq", "## SREQ — нормализованные исходные требования"),
                  ("mapping", "## Связь SREQ → CREQ"), ("capabilities", "## Возможности (полностью)"),
                  ("contexts", "## Файлы проекта из источников возможностей"))


def _part_text(context: _Context, *, part_id: str, title: str, areas: Sequence[Mapping[str, Any]], carried_ids: Sequence[str],
               case_ids: Sequence[str], source_items: Sequence[Mapping[str, Any]] | None, lint: Sequence[Mapping[str, Any]],
               notes: Sequence[str] = ()) -> str:
    source = bool(source_items)
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
    full = {item["anchor"] for item in source_items or [] if item["group"] == "capabilities"}
    if len(full) < len(document["operation_capabilities"]):
        out += ["", "## Возможности (сигнатуры)", *(context.signatures[item["capability_id"]] for item in document["operation_capabilities"]
                                                   if item["capability_id"] not in full)]
    if lint:
        out += ["", "## Подозрения линтера (ответь lint_dispositions по каждому)"]
        out.extend(f"[{row['lint_id']}] {row['rule']} · {', '.join(row['related_ids'])}: {row['message']}" for row in lint)
    for group, heading in _SOURCE_GROUPS:
        texts = [item["text"] for item in source_items or [] if item["group"] == group]
        if texts:
            out += ["", heading, *texts]
    if case_ids:
        out += ["", "## Кейсы", *(context.cases[case_id] + "\n" for case_id in case_ids)]
    return "\n".join(out).rstrip("\n") + "\n"


def _part(context: _Context, budget: int, reserve: int, *, part_id: str, title: str, areas: list, carried_ids: Sequence[str],
          case_ids: Sequence[str], source_items: Sequence[Mapping[str, Any]] | None = None, requested_check: str | None = None,
          notes: Sequence[str] = (), blocks: Sequence[Sequence[str]] | None = None, with_lint: bool = True,
          source_lint: bool = True) -> dict[str, Any]:
    lint = context.lint_for(case_ids, source=bool(source_items) and source_lint) if with_lint else []
    text = _part_text(context, part_id=part_id, title=title, areas=areas, carried_ids=carried_ids, case_ids=case_ids,
                      source_items=source_items, lint=lint, notes=notes)
    size = len(text.encode("utf-8"))
    return {"part_id": part_id, "case_ids": list(case_ids), "blocks": [list(block) for block in (blocks or [case_ids])],
            "source": bool(source_items), "areas": copy.deepcopy(areas), "carried_area_ids": list(carried_ids),
            "lint_ids": [row["lint_id"] for row in lint], "text": text, "requested_check": requested_check,
            "input_byte_count": size, "blocked_reason": "REVIEW_CONTEXT_LIMIT" if size + reserve > budget else None}


def _cross_area(context: _Context, case_ids: Sequence[str]) -> dict[str, Any]:
    return {"area_id": "cross-" + review_digest(list(case_ids))[7:23], "kind": "cross", "targets": list(case_ids),
            "fingerprint": review_digest({"cases": [context.case_fingerprint(case_id) for case_id in case_ids],
                                          "lint": [row for row in context.lint if len(set(row["case_ids"]) & set(case_ids)) > 1]})}


def _local_area(context: _Context, case_id: str) -> dict[str, Any]:
    return {"area_id": _CASE_AREA + case_id, "kind": "local", "targets": [case_id], "fingerprint": context.case_fingerprint(case_id)}


def _source_area(context: _Context, items: Sequence[Mapping[str, Any]], number: int = 1) -> dict[str, Any]:
    targets = list(dict.fromkeys(item["anchor"] for item in items)) or [context.document["document_id"]]
    return {"area_id": f"source-{number:06d}", "kind": "source", "targets": targets,
            "fingerprint": review_digest({"items": [dict(item) for item in items], "creq": context.requirements, "index": context.index,
                                          "lint": context.lint_for([], source=True) if number == 1 else []})}


def _chunks(item: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
    """A numbered document split by whole lines into pieces of at most ``limit`` bytes.

    Only the first piece carries the ``[SRC-n]``/``[CTX-n]`` anchor; a later piece names
    the same document without brackets, so an anchor is still defined once per part.
    """
    lines = item["text"].split("\n")
    head, body = lines[0], lines[1:]
    pieces, current, used = [], [head], len(head.encode("utf-8")) + 1
    for line in body:
        size = len(line.encode("utf-8")) + 1
        if len(current) > 1 and used + size > limit:
            pieces.append(current)
            current, used = [f"{item['anchor']} (продолжение)"], len(item["anchor"]) + 20
        current.append(line)
        used += size
    pieces.append(current)
    return [{**item, "text": "\n".join(piece), "piece": index} for index, piece in enumerate(pieces, start=1)]


def _pack_source(context: _Context, budget: int, reserve: int) -> list[list[dict[str, Any]]]:
    """Source items packed into as few source-only parts as fit; an indivisible oversize item stays alone (and blocks)."""
    skeleton = _part(context, budget, reserve, part_id="part-000000", title="0 из 0", case_ids=[], areas=[_source_area(context, [])],
                     carried_ids=(), source_items=[{"group": "mapping", "anchor": "x", "text": ""}])["input_byte_count"]
    room = max(1, budget - reserve - skeleton - 512)
    items: list[dict[str, Any]] = []
    for item in context.source_items():
        size = len(item["text"].encode("utf-8")) + 1
        items.extend(_chunks(item, room) if item["group"] in {"docs", "contexts"} and size > room else [dict(item)])
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    used = 0
    for item in items:
        size = len(item["text"].encode("utf-8")) + 1
        if current and used + size > room:
            groups.append(current)
            current, used = [], 0
        current.append(item)
        used += size
    if current:
        groups.append(current)
    return groups


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
    every_source = context.source_items()
    layouts: list[dict[str, Any]] = []
    whole = _part(context, input_byte_budget, reserve, part_id="part-000001", title="1 из 1", case_ids=order, source_items=every_source,
                  areas=[_source_area(context, every_source), *(_local_area(context, case_id) for case_id in order), _cross_area(context, order)],
                  carried_ids=())
    if whole["blocked_reason"] is None:
        layouts.append({"case_ids": order, "blocks": [order], "source": every_source, "source_number": 1, "home": order})
    else:
        skeleton = _part(context, input_byte_budget, reserve, part_id="part-000000", title="0 из 0", case_ids=[], areas=[], carried_ids=())
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
        for pair in pairs:
            case_ids = [case_id for block in pair for case_id in blocks[block]]
            home = [case_id for block in range(len(blocks)) if homes[block] == pair for case_id in blocks[block]]
            layouts.append({"case_ids": case_ids, "blocks": [blocks[block] for block in pair], "source": None, "source_number": 0, "home": home})
        first = layouts[0] if layouts else None
        trial = None if first is None else _part(
            context, input_byte_budget, reserve, part_id="part-000001", title="1", case_ids=first["case_ids"], source_items=every_source,
            areas=[_source_area(context, every_source), *(_local_area(context, case_id) for case_id in first["home"]), _cross_area(context, first["case_ids"])],
            carried_ids=())
        if trial is not None and trial["blocked_reason"] is None:
            first.update(source=every_source, source_number=1)
        else:
            # The source goes into its own part(s), split by whole items and document lines when needed.
            for number, group in enumerate(_pack_source(context, input_byte_budget, reserve), start=1):
                layouts.insert(number - 1, {"case_ids": [], "blocks": [], "source": group, "source_number": number, "home": []})
    carried_rows, carried_ids = _carry(context, carry, layouts)
    parts, total = [], sum(1 for layout in layouts if not _fully_carried(context, layout, carried_ids))
    for layout in layouts:
        if _fully_carried(context, layout, carried_ids):
            continue
        areas = _layout_areas(context, layout)
        part_id = f"part-{len(parts) + 1:06d}"
        parts.append(_part(context, input_byte_budget, reserve, part_id=part_id, title=f"{len(parts) + 1} из {total}", case_ids=layout["case_ids"],
                           source_items=layout["source"], areas=areas, carried_ids=[area["area_id"] for area in areas if area["area_id"] in carried_ids],
                           blocks=layout["blocks"], source_lint=layout["source_number"] == 1))
    if not parts:
        # Nothing changed at all: the whole review is sent again rather than carried blind.
        return build_compact_plan(specification, payload, input_byte_budget=input_byte_budget, carry=None)
    plan = {"schema_version": PLAN_VERSION, "mode": MODE, "snapshot": spec, "input_byte_budget": input_byte_budget,
            "lint": context.lint, "parts": parts, "check_policy": CHECK_POLICY, "digest": "sha256:" + "0" * 64}
    if carried_rows:
        plan["carried"] = carried_rows
    plan["digest"] = review_digest({key: value for key, value in plan.items() if key != "digest"})
    rows = validate_compact_plan(plan)
    if rows:
        raise ValueError(f"invalid compact review plan: {rows}")
    return plan


def _layout_areas(context: _Context, layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    areas = [_source_area(context, layout["source"], layout["source_number"])] if layout["source"] else []
    areas += [_local_area(context, case_id) for case_id in layout["home"]]
    if layout["case_ids"]:
        areas.append(_cross_area(context, layout["case_ids"]))
    return areas


def _fully_carried(context: _Context, layout: Mapping[str, Any], carried_ids: set) -> bool:
    return bool(carried_ids) and all(area["area_id"] in carried_ids for area in _layout_areas(context, layout))


def _carry(context: _Context, carry: Mapping[str, Any] | None, layouts: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], set]:
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
        for area in _layout_areas(context, layout):
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


def _part_refs(part: Mapping[str, Any]) -> tuple[set, dict[str, set], list[tuple[int, int, str]]]:
    """Anchors of a part, the shown lines of each numbered document and the code line ranges."""
    defined = set(anchors(part["text"]))
    lines: dict[str, set] = {}
    current = None
    for line in part["text"].split("\n"):
        match = re.match(r"^(?:\[((?:SRC|CTX)-[0-9]+)\]|((?:SRC|CTX)-[0-9]+) \(продолжение\))", line)
        if match:
            current = match.group(1) or match.group(2)
            continue
        number = re.match(r"^L([0-9]+)\| ", line)
        if current and number:
            lines.setdefault(current, set()).add(int(number.group(1)))
        elif not number:
            current = None
    ranges = [tuple(row) for row in part.get("code_ranges", [])]
    return defined, lines, ranges


def _ref_ok(ref: str, defined: set, lines: Mapping[str, set], ranges: Sequence[tuple]) -> bool:
    if ref in defined:
        return True
    match = _LINE_REF.fullmatch(ref)
    if match:
        return int(match["line"]) in lines.get(match["anchor"], set())
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


_FITS: dict[tuple[str, tuple[str, ...]], bool] = {}


def narrow_check(plan: Mapping[str, Any], payload: Mapping[str, Any], check: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """A required check of a ``CHECK_POLICY`` plan normalized to the cases it must see, and the requirements dropped as
    too broad: when the cases its requirements link do not fit one check part, only the cases it names are checked
    (none named — no check).  A check that fits is exactly ``resolve_check``'s."""
    document = payload["document"]
    resolved = resolve_check(document, check)
    named = [case_id for case_id in check.get("case_ids") or []]
    if resolved is None or not check.get("requirement_ids") or set(resolved["case_ids"]) == set(named):
        return resolved, []
    key = (plan["digest"], tuple(resolved["case_ids"]))
    if key not in _FITS:
        if len(_FITS) >= 4096:
            _FITS.clear()
        _FITS[key] = check_part(plan, payload, {"case_ids": resolved["case_ids"], "reason": resolved["reason"]}, len(plan["parts"]) + 1)["blocked_reason"] is None
    if _FITS[key]:
        return resolved, []
    dropped = list(check["requirement_ids"])
    if not named:
        return None, dropped
    return {"case_ids": [case_id for case_id in resolved["case_ids"] if case_id in set(named)], "reason": resolved["reason"]}, dropped


def _own_check(plan: Mapping[str, Any], payload: Mapping[str, Any], check: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """How the plan's policy reads one required check of an answer."""
    if plan.get("check_policy") == CHECK_POLICY:
        return narrow_check(plan, payload, check)
    return resolve_check(payload["document"], check), []


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
    # A check naming requirements sees their source requirements (live Petclinic run 2026-10-08: without them two
    # check parts came back UNCHECKED — "the original requirements are not in this part").
    named = set(check.get("source_requirement_ids") or [])
    items = [item for item in context.source_items() if item["group"] == "sreq" and item["anchor"] in named]
    if items:
        items.append(next(item for item in context.source_items() if item["group"] == "mapping"))
    return _part(context, plan["input_byte_budget"], plan["snapshot"]["response_reserve_bytes"], part_id=f"part-{index:06d}", title="проверка",
                 areas=[area], carried_ids=(), case_ids=check["case_ids"], source_items=items or None, requested_check=digest, notes=notes,
                 with_lint=False)


def _valid_results(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    parts = {part["part_id"]: part for part in [*plan["parts"], *plan.get("additions", [])]}
    return [result for result in results if result.get("part_id") in parts
            and not validate_answer(plan, parts[result["part_id"]], result, payload["document"], payload.get("automation"))]


def required_checks(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalized checks the answers asked for, plus correction re-checks once every base part answered."""
    if plan.get("check_policy") == CHECK_POLICY:
        return _merged_checks(plan, payload, results)
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


def _correction_level(result: Mapping[str, Any], item: Mapping[str, Any]) -> str:
    """The highest severity among the answer's findings that name the corrected target (``INFO`` when none does)."""
    order = ("INFO", "WARNING", "BLOCKING")
    levels = [finding["severity"] for finding in result["findings"] if item["target_id"] in finding["related_ids"]]
    return max(levels, key=order.index) if levels else "INFO"


def _merged_checks(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The checks of a ``CHECK_POLICY`` plan: none until every base part answered, then one per checked target."""
    document = payload["document"]
    completed = {result["part_id"] for result in results}
    if not all(part["part_id"] in completed or part["blocked_reason"] or part["part_id"] in plan.get("unavailable", {}) for part in plan["parts"]):
        return []
    base = {part["part_id"] for part in plan["parts"]}
    valid = sorted((result for result in _valid_results(plan, payload, results) if result["part_id"] in base), key=lambda result: result["part_id"])
    order = [case["case_id"] for case in document["test_cases"]]
    merged: dict[tuple[str, ...], dict[str, Any]] = {}

    mapped = {item["source_requirement_id"]: set(item["canonical_requirement_ids"]) for item in document["source_to_canonical_mappings"]}

    def source_requirements(requirement_ids) -> list[str]:
        """The source requirements a check names directly or through the canonical requirements it names."""
        wanted = set(requirement_ids or [])
        return [sreq for sreq, creqs in mapped.items() if sreq in wanted or creqs & wanted]

    def add(case_ids, reason: str, corrections=(), source: str | None = None, sreq=()) -> None:
        cases = tuple(case_id for case_id in order if case_id in set(case_ids))
        if not cases:
            return
        row = merged.setdefault(cases, {"reasons": [], "corrections": [], "sources": [], "sreq": []})
        row["sreq"].extend(item for item in sreq if item not in row["sreq"])
        if reason not in row["reasons"]:
            row["reasons"].append(reason)
        row["corrections"].extend(dict(item) for item in corrections if dict(item) not in row["corrections"])
        if source is not None and source not in row["sources"]:
            row["sources"].append(source)

    for result in valid:
        for check in result["required_checks"]:
            resolved, _dropped = narrow_check(plan, payload, check)
            if resolved is not None:
                add(resolved["case_ids"], resolved["reason"], source=review_digest(resolved), sreq=source_requirements(check.get("requirement_ids")))
    if payload.get("automation") is None:
        index = id_index(document)
        requirements = {item["requirement_id"] for item in document.get("requirements") or []}
        for result in valid:
            for item in result["corrections"]:
                if is_service_correction(item) or _correction_level(result, item) == "INFO":
                    continue  # journalled with the answer, never re-checked
                sreq = source_requirements([item["target_id"]])
                if item["target_id"] in requirements:
                    cases = [case["case_id"] for case in document["test_cases"] if item["target_id"] in (case.get("requirement_ids") or [])]
                else:
                    _pointer, case_id = correction_pointer(document, item["target_id"], item["field"], index)
                    cases = [case_id] if case_id else []
                add(cases, "Проверь, что предложенная правка " + item["target_id"] + "." + item["field"]
                    + " сохраняет смысл и не создаёт противоречий с другими кейсами.", [item], sreq=sreq)
    return _packed_checks(plan, payload, [(list(cases), row) for cases, row in merged.items()])


def _packed_checks(plan: Mapping[str, Any], payload: Mapping[str, Any], rows: Sequence[tuple[list[str], Mapping[str, Any]]]) -> list[dict[str, Any]]:
    """Checks packed in case order while a part still fits its byte budget (live Petclinic run 2026-10-08: 21 merged
    checks of one or a few cases each would each have cost a part); a check too large for one part is split into parts
    that fit (a request for every source requirement named all 81 cases: one 577 KB part, blocked, the run PARTIAL).
    ``sources`` names the digests of the answers' own checks a packed check answers."""
    order = [case["case_id"] for case in payload["document"]["test_cases"]]

    def question(items) -> str:
        # Case IDs without brackets: a bracketed ID is an anchor, defined once per part text.
        return "\n".join(" | ".join(row["reasons"]) if len(items) == 1 else "Кейсы " + ", ".join(cases) + ": " + " | ".join(row["reasons"])
                         for cases, row in items)

    def build(items) -> dict[str, Any]:
        wanted = {case_id for cases, _row in items for case_id in cases}
        check = {"case_ids": [case_id for case_id in order if case_id in wanted], "reason": question(items)[:4000]}
        corrections = [item for _cases, row in items for item in row["corrections"]]
        if corrections:
            check["corrections"] = corrections
        sources = [source for _cases, row in items for source in row["sources"]]
        if sources:
            check["sources"] = sources
        named = {sreq for _cases, row in items for sreq in row.get("sreq", [])}
        if named:
            check["source_requirement_ids"] = [item["source_requirement_id"] for item in payload["document"]["source_requirements"]
                                               if item["source_requirement_id"] in named]
        return check

    def fits(items) -> bool:
        part = check_part(plan, payload, build(items), len(plan["parts"]) + 1)
        found = anchors(part["text"])
        return len(question(items)) <= 4000 and part["blocked_reason"] is None and len(found) == len(set(found))

    def pieces(cases: list[str], row: Mapping[str, Any]) -> list[tuple[list[str], Mapping[str, Any]]]:
        if fits([(cases, row)]):
            return [(cases, row)]
        out: list[tuple[list[str], Mapping[str, Any]]] = []

        def piece(chunk: list[str]) -> tuple[list[str], Mapping[str, Any]]:
            note = f" (часть {len(out) + 1} проверки: остальные её кейсы — в других частях)"
            return chunk, {**row, "reasons": [reason + note for reason in row["reasons"]]}

        chunk: list[str] = []
        for case_id in cases:
            if chunk and not fits([piece([*chunk, case_id])]):
                out.append(piece(chunk))
                chunk = []
            chunk.append(case_id)
        out.append(piece(chunk))  # a single case that does not fit stays blocked: REVIEW_CONTEXT_LIMIT
        return out

    items = [item for cases, row in sorted(rows, key=lambda row: order.index(row[0][0])) for item in pieces(list(cases), row)]
    packed: list[list] = []
    for item in items:
        if packed and fits([*packed[-1], item]):
            packed[-1].append(item)
        else:
            packed.append([item])
    return [build(items) for items in packed]


def additional_parts(plan: Mapping[str, Any], payload: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    parts = [*plan["parts"], *plan.get("additions", [])]
    known = {part["requested_check"] for part in parts}
    additions = []
    checks = required_checks(plan, payload, results)
    if plan.get("check_policy") == CHECK_POLICY:
        checks = checks[:check_limit(plan)]  # the rest stays unchecked: REVIEW_CHECK_LIMIT in the aggregate
    for check in checks:
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
    resolved: list[dict[str, Any]] = []
    too_broad: list[dict[str, Any]] = []
    # Checks asked by check parts are never added (CHECK_POLICY); only base answers can be too broad.
    base_ids = {item["part_id"] for item in plan["parts"]} if plan.get("check_policy") == CHECK_POLICY else set()
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
        own_checks, own_broad = [], False
        for check in result["required_checks"]:
            narrowed, dropped = _own_check(plan, payload, check)
            if dropped and part["part_id"] in base_ids:
                too_broad.append({"part_id": part["part_id"], "requirement_ids": dropped, "case_ids": [] if narrowed is None else narrowed["case_ids"],
                                  "question": check["reason"]})
            if narrowed is None:
                # An additional part's own checks are never added: none of its areas can wait for one.
                own_broad = own_broad or (bool(dropped) and part["part_id"] in base_ids)
            else:
                own_checks.append(review_digest(narrowed))
        for row in result["coverage"]:
            if row["status"] == "CHECKED":
                checked.append(row["area_id"])
            elif not own_checks and own_broad:
                # Every check this area waits for is too broad: closed with that reason, the review stays complete.
                checked.append(row["area_id"])
                resolved.append({"part_id": part["part_id"], "scope_id": row["area_id"], "resolved_by": [], "reason": TOO_BROAD_REASON})
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
    # A packed check (CHECK_POLICY) answers the answers' own checks it names in ``sources``.
    alias: dict[str, list[str]] = {}
    for digest, check in requested.items():
        for source in check.get("sources", []):
            alias.setdefault(source, []).append(digest)  # a split check is answered by all of its parts
    open_rows = [(part_id, row, [item for digest in own for item in alias.get(digest, [digest])]) for part_id, row, own in open_rows]
    for index, part in enumerate(parts, start=1):
        if part["requested_check"] is not None:
            check = requested.get(part["requested_check"])
            if check is None or part != check_part(plan, payload, check, index):
                diagnostics.append(_row("REVIEW_ADDITION_BINDING", part["part_id"], "the check part is not the one its check asked for"))
    carried = [{"scope_id": row["area"]["area_id"], "fingerprint": row["fingerprint"], "from_attempt_id": row["from_attempt_id"],
                "from_plan_digest": row["from_plan_digest"]} for row in plan.get("carried", [])]
    checked.extend(row["scope_id"] for row in carried)
    for row in too_broad:
        findings.append({"severity": "WARNING", "code": TOO_BROAD_REASON,
                         "message": (f"Проверка ревьюера части {row['part_id']} по требованиям {', '.join(row['requirement_ids'])} охватывает больше кейсов, "
                                     "чем помещается в одну часть проверки: " + (f"проверены только названные кейсы {', '.join(row['case_ids'])}"
                                                                                  if row["case_ids"] else "часть проверки не создана")
                                     + f". Вопрос аналитику: {row['question']}")[:4000],
                         "evidence": [row["part_id"]], "related_ids": list(row["requirement_ids"])})
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
    limited = set(list(requested)[check_limit(plan):]) if plan.get("check_policy") == CHECK_POLICY else set()
    for digest, check in requested.items():
        if digest not in closed:
            reason = (f"{CHECK_LIMIT_REASON}: more than {check_limit(plan)} additional checks; not checked ({', '.join(check['case_ids'])}): {check['reason']}"
                      if digest in limited else check["reason"])
            unchecked.append({"scope_id": "cross-" + digest[7:], "reason": reason[:4000]})
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
    if too_broad:
        result["too_broad"] = too_broad
    if carried:
        result["carried"] = carried
    return result


def envelope_bytes(plan: Mapping[str, Any]) -> int:
    return sum(len(review_bytes(part_input(plan, part))) for part in plan["parts"])


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
