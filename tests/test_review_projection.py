"""Р4: the compact review projection loses no meaning.

* every semantic leaf of a canonical document (all fields except the explicit service
  list ``review_projection.SERVICE_FIELDS``) changes the projection when it changes;
* every canonical ID is defined by exactly one anchor;
* the projection of the real step5 candidate ``9340016c`` is pinned byte for byte.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterator

import pytest

from tests.review_scaling_helpers import FIXTURES, petclinic_snapshot, step5_snapshot, synthetic_document
from tools import review_projection as projection

GOLDEN = FIXTURES / "projection-step5-9340016c.txt"


# Arrays of strings in the canonical schema: an empty one is a place where a leaf can appear.
# Empty arrays of objects (steps, inputs, outputs, blockers...) hold no leaves.
STRING_ARRAYS = {"preconditions", "components", "labels", "provenance", "issues", "pages", "categories", "documentation",
                 "requirement_ids", "canonical_requirement_ids", "type_provenance"}


def _leaves(value: Any, pointer: str = "") -> Iterator[tuple[str, Any]]:
    """Every scalar leaf, every empty object and every empty array of strings."""
    if isinstance(value, dict):
        if not value:
            yield pointer, value
        for key, item in value.items():
            yield from _leaves(item, f"{pointer}/{key}")
    elif isinstance(value, list):
        if not value and pointer.rsplit("/", 1)[-1] in STRING_ARRAYS:
            yield pointer, value
        for index, item in enumerate(value):
            yield from _leaves(item, f"{pointer}/{index}")
    else:
        yield pointer, value


def _mutated(value: Any) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str):
        return value + "Ж"
    if value is None:
        return "Ж"
    if isinstance(value, list):
        return ["Ж"]
    return {"ж": "Ж"}


def _set(document: Any, pointer: str, value: Any) -> None:
    *parents, last = pointer.split("/")[1:]
    target = document
    for token in parents:
        target = target[int(token)] if isinstance(target, list) else target[token]
    if isinstance(target, list):
        target[int(last)] = value
    else:
        target[last] = value


def _copy_owner(document: dict, pointer: str) -> dict:
    """A copy of the document where only the top-level object holding ``pointer`` is deep-copied."""
    tokens = pointer.split("/")[1:]
    changed = dict(document)
    if isinstance(document.get(tokens[0]), list) and len(tokens) > 1:
        items = list(document[tokens[0]])
        items[int(tokens[1])] = copy.deepcopy(items[int(tokens[1])])
        changed[tokens[0]] = items
    else:
        changed[tokens[0]] = copy.deepcopy(document[tokens[0]])
    return changed


def _section(document: dict, pointer: str) -> str:
    """The projection section a leaf belongs to (sections depend only on their own object)."""
    tokens = pointer.split("/")[1:]
    field, index = tokens[0], int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else None
    if field == "test_cases":
        return projection.case_text(document["test_cases"][index])
    if field == "requirements":
        return projection.requirement_text(document["requirements"][index])
    if field == "source_requirements":
        return projection.source_requirement_text(document["source_requirements"][index])
    if field == "operation_capabilities":
        return projection.capability_text(document["operation_capabilities"][index])
    if field == "source_to_canonical_mappings":
        return projection.mapping_text(document)
    return projection.header_text(document)


def _documents() -> list[tuple[str, dict]]:
    return [("step5-9340016c", step5_snapshot()["document"]), ("petclinic", petclinic_snapshot()["document"]),
            ("synthetic", synthetic_document([40, 60]))]


@pytest.mark.parametrize("name,document", _documents(), ids=lambda item: item if isinstance(item, str) else "")
def test_every_semantic_leaf_changes_the_projection(name: str, document: dict) -> None:
    checked = skipped = 0
    missed = []
    for pointer, value in _leaves(document):
        if projection.is_service_pointer(pointer):
            skipped += 1
            continue
        before = _section(document, pointer)
        changed = _copy_owner(document, pointer)
        _set(changed, pointer, _mutated(value))
        if _section(changed, pointer) == before:
            missed.append(pointer)
        checked += 1
    assert not missed, f"{name}: leaves invisible in the projection: {missed[:20]}"
    assert checked > 100 and skipped > 0


def test_section_rendering_equals_the_whole_projection() -> None:
    """The per-section shortcut above is sound: a leaf change shows in the whole projection text too."""
    document = step5_snapshot()["document"]
    whole = projection.projection_text(projection.build_projection(document))
    for pointer in ("/test_cases/3/steps/0/expectations/0/assertions/1/expected/value/firstName", "/requirements/2/text",
                    "/operation_capabilities/0/provenance/1", "/source_to_canonical_mappings/4/canonical_requirement_ids/0", "/metadata/project"):
        changed = copy.deepcopy(document)
        value = changed
        for token in pointer.split("/")[1:]:
            value = value[int(token)] if isinstance(value, list) else value[token]
        _set(changed, pointer, _mutated(value))
        assert projection.projection_text(projection.build_projection(changed)) != whole, pointer


@pytest.mark.parametrize("name,document", _documents(), ids=lambda item: item if isinstance(item, str) else "")
def test_every_canonical_id_is_exactly_one_anchor(name: str, document: dict) -> None:
    text = projection.projection_text(projection.build_projection(document))
    defined = projection.anchors(text)
    assert len(defined) == len(set(defined)), f"{name}: duplicate anchors"
    assert sorted(defined) == sorted(projection.canonical_ids(document))


def test_document_text_cannot_forge_an_anchor() -> None:
    document = synthetic_document([10])
    case = document["test_cases"][0]
    case["objective"] = "строка\n[TC-FAKE] выдуманный якорь"
    case["steps"][0]["expectations"][0]["text"] = "[ASSERT-FAKE] тоже нет\nи [EXP-FAKE]"
    defined = projection.anchors(projection.projection_text(projection.build_projection(document)))
    assert "TC-FAKE" not in defined and "ASSERT-FAKE" not in defined and "EXP-FAKE" not in defined


def test_service_fields_are_exactly_the_listed_ones() -> None:
    assert projection.is_service_pointer("/test_cases/0/display_order")
    assert projection.is_service_pointer("/source_requirements/3/digest")
    assert projection.is_service_pointer("/revision") and projection.is_service_pointer("/parent_sha256")
    assert not projection.is_service_pointer("/test_cases/0/management/folder")
    assert not projection.is_service_pointer("/requirements/0/provenance/0")


def test_projection_is_deterministic_and_bound_to_the_document() -> None:
    document = step5_snapshot()["document"]
    first, second = projection.build_projection(document), projection.build_projection(copy.deepcopy(document))
    assert first == second
    from tools.review_parts import review_digest

    assert first["document_digest"] == review_digest(document)


def test_step5_projection_matches_the_pinned_bytes() -> None:
    text = projection.projection_text(projection.build_projection(step5_snapshot()["document"]))
    if not GOLDEN.exists():  # first run writes the reference; review it like any fixture
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
    assert text == GOLDEN.read_text(encoding="utf-8")
