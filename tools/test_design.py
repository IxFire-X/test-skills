"""Test design (D1a): the check of the design artifact, its brief and batch slices, the generator's references, design
coverage and code-assigned case priorities.

Pure functions, so the journal recomputes the brief and every slice and repeats every check.

- The design is built from the source requirements (``SREQ-*``) alone; it names parameters (``P-NNN``), equivalence
  partitions (``EQ-NNN``), boundary values (``BV-NNN``) and decision/state/error rules (``DR-NNN``), or declares a
  requirement not designable with a reason.
- A generator batch gets the slice of its own requirements; every case names the design elements it exercises
  (``design_refs``), and every element of the slice is covered or declared uncovered with a reason.
- Priorities come from the table in ``assign_priorities``; the model never chooses them, and CRITICAL is not assigned
  (requirements carry no criticality).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

SCHEMA_VERSION = "1.0.0"
SCHEMA_NAME = "test-design.schema.json"
DESIGN_FIELDS = ("parameters", "partitions", "boundaries", "rules", "not_designed")
LEVELS = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
DEGENERATE_SHARE = 0.7
DEGENERATE_MIN_CASES = 10  # fewer cases are too few to call a spread degenerate
ID_FORMATS = {"parameter": "P-NNN", "partition": "EQ-NNN", "boundary": "BV-NNN", "rule": "DR-NNN"}
_PREFIX = {"parameters": "P-", "partitions": "EQ-", "boundaries": "BV-", "rules": "DR-"}


def design_enabled(skillsrc: Mapping[str, Any]) -> bool:
    """``.skillsrc`` ``design.enabled``; the stage runs unless it is explicitly false."""
    section = skillsrc.get("design") if isinstance(skillsrc, Mapping) else None
    return not (isinstance(section, Mapping) and section.get("enabled") is False)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _rows(design: Mapping[str, Any], name: str) -> list[Mapping[str, Any]]:
    artifacts = design.get("artifacts") if isinstance(design, Mapping) else None
    rows = artifacts.get(name) if isinstance(artifacts, Mapping) else None
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _row(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


# --- the design artifact ------------------------------------------------------------------------------------------


def validate_design(design: Mapping[str, Any], requirement_ids: Iterable[str]) -> list[dict[str, str]]:
    """Every rule of the design the schema cannot express; any row refuses the answer."""
    known = set(requirement_ids)
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for name in ("parameters", "partitions", "boundaries", "rules"):
        for index, row in enumerate(_rows(design, name)):
            identifier = row.get("id")
            if identifier in seen:
                rows.append(_row(f"/artifacts/{name}/{index}/id", "DESIGN_ID_DUPLICATE", f"design ID {identifier} is used twice"))
            seen.add(identifier)
    parameters = {row.get("id"): row for row in _rows(design, "parameters")}
    partitions = {row.get("id"): row for row in _rows(design, "partitions")}
    boundaries = {row.get("id"): row for row in _rows(design, "boundaries")}

    def requirement_refs(name: str) -> None:
        for index, row in enumerate(_rows(design, name)):
            for position, item in enumerate(row.get("requirement_ids") or []):
                if item not in known:
                    rows.append(_row(f"/artifacts/{name}/{index}/requirement_ids/{position}", "DESIGN_REQUIREMENT_UNKNOWN",
                                     f"{item} is not a requirement of the marker"))

    def blank(path: str, value: Any) -> None:
        if isinstance(value, str) and not value.strip():
            rows.append(_row(path, "DESIGN_TEXT_BLANK", "text is blank"))

    for index, row in enumerate(_rows(design, "parameters")):
        blank(f"/artifacts/parameters/{index}/name", row.get("name"))
        blank(f"/artifacts/parameters/{index}/single_class_reason", row.get("single_class_reason"))
    for index, row in enumerate(_rows(design, "partitions")):
        blank(f"/artifacts/partitions/{index}/condition", row.get("condition"))
        if row.get("parameter_id") not in parameters:
            rows.append(_row(f"/artifacts/partitions/{index}/parameter_id", "DESIGN_REF_UNKNOWN", f"no parameter {row.get('parameter_id')}"))
    for index, row in enumerate(_rows(design, "boundaries")):
        blank(f"/artifacts/boundaries/{index}/value", row.get("value"))
        partition = partitions.get(row.get("partition_id"))
        if partition is None:
            rows.append(_row(f"/artifacts/boundaries/{index}/partition_id", "DESIGN_REF_UNKNOWN", f"no partition {row.get('partition_id')}"))
        elif parameters.get(partition.get("parameter_id"), {}).get("kind") not in (None, "ordered"):
            rows.append(_row(f"/artifacts/boundaries/{index}/partition_id", "DESIGN_BOUNDARY_NOT_ORDERED",
                             f"{row.get('id')} is a boundary of a parameter that is not ordered"))
    for index, row in enumerate(_rows(design, "rules")):
        blank(f"/artifacts/rules/{index}/expected", row.get("expected"))
        for position, condition in enumerate(row.get("conditions") or []):
            if not isinstance(condition, Mapping):
                continue
            path = f"/artifacts/rules/{index}/conditions/{position}"
            if "ref" in condition:
                ref = condition.get("ref")
                target = partitions if str(ref).startswith("EQ-") else boundaries
                if ref not in target:
                    rows.append(_row(f"{path}/ref", "DESIGN_REF_UNKNOWN", f"no partition or boundary {ref}"))
            else:
                blank(f"{path}/text", condition.get("text"))
    for name in ("parameters", "partitions", "rules"):
        requirement_refs(name)

    designed = {item for name in ("parameters", "partitions", "rules") for row in _rows(design, name) for item in row.get("requirement_ids") or []}
    declared: set[Any] = set()
    for index, row in enumerate(_rows(design, "not_designed")):
        requirement = row.get("requirement_id")
        blank(f"/artifacts/not_designed/{index}/reason", row.get("reason"))
        if requirement not in known:
            rows.append(_row(f"/artifacts/not_designed/{index}/requirement_id", "DESIGN_REQUIREMENT_UNKNOWN", f"{requirement} is not a requirement of the marker"))
        elif requirement in designed:
            rows.append(_row(f"/artifacts/not_designed/{index}/requirement_id", "DESIGN_NOT_DESIGNED_CONFLICT",
                             f"{requirement} is declared not designed but design elements name it"))
        declared.add(requirement)
    for requirement in sorted(known - designed - declared):
        rows.append(_row("/artifacts/not_designed", "DESIGN_REQUIREMENT_UNCOVERED",
                         f"{requirement} has no parameter or rule and is not declared not designed"))

    for index, row in enumerate(_rows(design, "parameters")):
        own = [partition for partition in partitions.values() if partition.get("parameter_id") == row.get("id")]
        path = f"/artifacts/parameters/{index}"
        if not own:
            rows.append(_row(path, "DESIGN_PARAMETER_UNPARTITIONED", f"{row.get('id')} has no partition"))
            continue
        if {partition.get("valid") for partition in own} != {True, False} and not row.get("single_class_reason"):
            rows.append(_row(path, "DESIGN_PARAMETER_ONE_SIDED",
                             f"{row.get('id')} needs a valid and an invalid partition, or single_class_reason"))
        if row.get("kind") == "ordered" and not any(boundary.get("partition_id") in {p.get("id") for p in own} for boundary in boundaries.values()):
            rows.append(_row(path, "DESIGN_ORDERED_WITHOUT_BOUNDARY", f"{row.get('id')} is ordered but has no boundary value"))
    return sorted(rows, key=lambda item: (item["path"], item["code"], item["message"]))


def design_warnings(design: Mapping[str, Any]) -> list[str]:
    """Warnings that do not refuse the answer."""
    if not any(_rows(design, name) for name in ("parameters", "rules")):
        return ["DESIGN_EMPTY: every requirement is declared not designed; the cases are not checked against a design"]
    return []


# --- brief and batch slice ----------------------------------------------------------------------------------------


def design_brief(requirements: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The stage input: the requirement texts in display order and the ID formats (no project code, no provenance)."""
    rows = sorted(({"source_requirement_id": row["source_requirement_id"], "display_order": row["display_order"], "text": row["text"]}
                   for row in requirements), key=lambda row: (row["display_order"], row["source_requirement_id"]))
    return {"schema_version": SCHEMA_VERSION, "requirements": rows, "id_formats": dict(ID_FORMATS)}


def design_slice(design: Mapping[str, Any], owned_requirement_ids: Iterable[str]) -> dict[str, Any]:
    """The design a generator batch receives: the elements naming one of its requirements, every parameter they touch
    with all its partitions and boundaries, and the partitions and boundaries its rules name."""
    owned = set(owned_requirement_ids)
    partitions = {row.get("id"): row for row in _rows(design, "partitions")}
    boundaries = {row.get("id"): row for row in _rows(design, "boundaries")}

    def mine(row: Mapping[str, Any]) -> bool:
        return bool(owned & set(row.get("requirement_ids") or []))

    rules = [row for row in _rows(design, "rules") if mine(row)]
    parameter_ids = {row.get("id") for row in _rows(design, "parameters") if mine(row)}
    parameter_ids |= {row.get("parameter_id") for row in partitions.values() if mine(row)}
    for rule in rules:
        for condition in rule.get("conditions") or []:
            ref = condition.get("ref") if isinstance(condition, Mapping) else None
            partition = partitions.get(ref) or partitions.get(boundaries.get(ref, {}).get("partition_id"))
            if partition is not None:
                parameter_ids.add(partition.get("parameter_id"))
    kept_partitions = [row for row in _rows(design, "partitions") if row.get("parameter_id") in parameter_ids]
    partition_ids = {row.get("id") for row in kept_partitions}
    value = {
        "parameters": [row for row in _rows(design, "parameters") if row.get("id") in parameter_ids],
        "partitions": kept_partitions,
        "boundaries": [row for row in _rows(design, "boundaries") if row.get("partition_id") in partition_ids],
        "rules": rules,
        "not_designed": [row for row in _rows(design, "not_designed") if row.get("requirement_id") in owned],
    }
    return {"schema_version": SCHEMA_VERSION, "stage": "test-design", "artifacts": json.loads(canonical_bytes(value)), "warnings": []}


def element_ids(design: Mapping[str, Any]) -> dict[str, str]:
    """Design element ID -> its kind (``partitions``, ``boundaries``, ``rules``): the elements a case can name."""
    return {row.get("id"): name for name in ("partitions", "boundaries", "rules") for row in _rows(design, name)}


# --- the generator's references -----------------------------------------------------------------------------------


def fragment_design_problems(design_slice_value: Mapping[str, Any], cases: Sequence[Mapping[str, Any]],
                             uncovered: Sequence[Mapping[str, Any]], mappings: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, str]]:
    """The design references of one generator answer against its slice; any row refuses the answer.

    A case needs references unless every source requirement it covers (through ``mappings``, the answer's
    ``source_to_canonical_mappings``) is declared not designed.
    """
    elements = element_ids(design_slice_value)
    not_designed = {row.get("requirement_id") for row in _rows(design_slice_value, "not_designed")}
    sources_of: dict[Any, set[Any]] = {}
    for mapping in mappings:
        for canonical in mapping.get("canonical_requirement_ids") or []:
            sources_of.setdefault(canonical, set()).add(mapping.get("source_requirement_id"))
    rows: list[dict[str, str]] = []
    covered: set[str] = set()
    negative: set[str] = set()
    for index, case in enumerate(cases):
        refs = case.get("design_refs")
        if not isinstance(refs, list) or not refs:
            sources = set().union(*(sources_of.get(item, set()) for item in case.get("requirement_ids") or []))
            if not (sources and sources <= not_designed):
                rows.append(_row(f"/test_cases/{index}/design_refs", "DESIGN_REFS_MISSING", f"{case.get('case_id')} names no design element"))
            continue
        for position, ref in enumerate(refs):
            if ref not in elements:
                rows.append(_row(f"/test_cases/{index}/design_refs/{position}", "DESIGN_REF_UNKNOWN", f"{ref} is not in the design of this batch"))
                continue
            covered.add(ref)
            if "negative" in (case.get("categories") or []):
                negative.add(ref)
    declared: set[str] = set()
    for index, row in enumerate(uncovered):
        ref = row.get("ref")
        if ref not in elements:
            rows.append(_row(f"/design_uncovered/{index}/ref", "DESIGN_REF_UNKNOWN", f"{ref} is not in the design of this batch"))
        elif ref in covered:
            rows.append(_row(f"/design_uncovered/{index}/ref", "DESIGN_UNCOVERED_CONFLICT", f"{ref} is covered by a case and declared uncovered"))
        declared.add(ref)
    for ref in sorted(set(elements) - covered - declared):
        rows.append(_row("/design_uncovered", "DESIGN_ELEMENT_UNCOVERED", f"{ref} is covered by no case and not declared uncovered"))
    for row in _rows(design_slice_value, "partitions"):
        ref = row.get("id")
        if row.get("valid") is False and ref in covered and ref not in negative:
            rows.append(_row("/test_cases", "DESIGN_NEGATIVE_MISSING", f"invalid partition {ref} has no negative case"))
    return sorted(rows, key=lambda item: (item["path"], item["code"], item["message"]))


# --- coverage -----------------------------------------------------------------------------------------------------


def _ratio(covered: int, total: int) -> float | None:
    return round(covered / total, 4) if total else None


def coverage(design: Mapping[str, Any], cases: Sequence[Mapping[str, Any]], uncovered: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Design coverage of the assembled cases: per element kind, the covered share; an element any case names is
    covered even if another batch declared it uncovered."""
    covered = {ref for case in cases for ref in case.get("design_refs") or []}
    negative = {ref for case in cases if "negative" in (case.get("categories") or []) for ref in case.get("design_refs") or []}
    reasons: dict[str, str] = {}
    for row in uncovered:
        if row.get("ref") not in covered:
            reasons.setdefault(row.get("ref"), row.get("reason"))
    result: dict[str, Any] = {}
    for name in ("partitions", "boundaries", "rules"):
        ids = [row.get("id") for row in _rows(design, name)]
        hit = sum(ref in covered for ref in ids)
        result[name] = {"total": len(ids), "covered": hit, "uncovered": sum(ref in reasons for ref in ids), "ratio": _ratio(hit, len(ids))}
    invalid = [row.get("id") for row in _rows(design, "partitions") if row.get("valid") is False]
    hit = sum(ref in negative for ref in invalid)
    result["negative_partitions"] = {"total": len(invalid), "covered": hit, "uncovered": sum(ref in reasons for ref in invalid),
                                     "ratio": _ratio(hit, len(invalid))}
    known = element_ids(design)
    result["uncovered"] = [{"ref": ref, "reason": reasons[ref]} for ref in sorted(reasons) if ref in known]
    return result


# --- priorities ---------------------------------------------------------------------------------------------------


def assign_priorities(design: Mapping[str, Any], cases: Sequence[Mapping[str, Any]]) -> list[str]:
    """The priority of every case in order, from its design references:

    1. an ``error`` rule or an invalid partition -> HIGH;
    2. a boundary just outside, or of an invalid partition -> HIGH; another boundary -> MEDIUM;
    3. the first case naming a not yet named valid partition or decision/state rule -> MEDIUM (the main scenario);
    4. otherwise -> LOW (an alternative or auxiliary case).
    """
    partitions = {row.get("id"): row for row in _rows(design, "partitions")}
    boundaries = {row.get("id"): row for row in _rows(design, "boundaries")}
    rules = {row.get("id"): row for row in _rows(design, "rules")}
    seen: set[str] = set()
    levels: list[str] = []
    for case in cases:
        refs = [ref for ref in case.get("design_refs") or [] if isinstance(ref, str)]
        main = {ref for ref in refs if (ref in rules and rules[ref].get("kind") != "error") or partitions.get(ref, {}).get("valid") is True}
        named = [boundaries[ref] for ref in refs if ref in boundaries]
        if any(rules.get(ref, {}).get("kind") == "error" or partitions.get(ref, {}).get("valid") is False for ref in refs):
            level = "HIGH"
        elif any(row.get("side") == "just_outside" or partitions.get(row.get("partition_id"), {}).get("valid") is False for row in named):
            level = "HIGH"
        elif named:
            level = "MEDIUM"
        elif main - seen:
            level = "MEDIUM"
        else:
            level = "LOW"
        seen |= main
        levels.append(level)
    return levels


GENERATOR_RULES = (
    "Каждый кейс перечисляет в design_refs ID элементов дизайна из файла design_file, которые он проверяет: разбиения EQ-, границы BV-, правила DR-.",
    "Кейс на границу указывает и границу BV-, и разбиение EQ-, в котором лежит проверяемое значение; для just_outside — соседнее невалидное разбиение.",
    "Каждое разбиение, граница и правило файла покрыты хотя бы одним кейсом; элемент, который покрыть нельзя, внеси в design_uncovered с причиной.",
    "Разбиение с valid: false покрывается негативным кейсом (категория negative); ожидаемый результат кейса по правилу совпадает с expected правила.",
    "Кейс по требованию из not_designed может не иметь design_refs. Поле priority не заполняй: приоритет назначает драйвер по дизайну.",
)


def priority_counts(cases: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys(LEVELS, 0)
    for case in cases:
        if case.get("priority") in counts:
            counts[case["priority"]] += 1
    return counts


def priority_warning(counts: Mapping[str, int]) -> str | None:
    """A warning when one level holds more than 70 % of at least 10 cases."""
    total = sum(counts.get(level, 0) for level in LEVELS)
    if total < DEGENERATE_MIN_CASES:
        return None
    level = max(LEVELS, key=lambda name: counts.get(name, 0))
    if counts.get(level, 0) / total <= DEGENERATE_SHARE:
        return None
    spread = ", ".join(f"{name} {counts.get(name, 0)}" for name in LEVELS)
    return (f"PRIORITY_DEGENERATE: {level} — {counts[level]} из {total} кейсов ({round(100 * counts[level] / total)} %), больше 70 %; "
            f"распределение: {spread}")
