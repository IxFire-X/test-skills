"""Which old cases an update judges: the requirements a delta of OpenSpec touches (``tools.cases_scope``)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tools.build_context import build_context
from tools.cases_scope import ScopeSourceError, delta_names, identities, judged_rows, scope

PACK_ROOT = Path(__file__).resolve().parents[1]
BASELINE = "## Requirements\n" + "\n".join(
    f"### Requirement: {name}\nBaseline {name}.\n#### Scenario: {name} ok\n- WHEN used\n- THEN ok\n" for name in ("Kept", "Changed", "Deleted", "Old Name"))
DELTA = ("## ADDED Requirements\n### Requirement: Added\nNew.\n#### Scenario: s\n- WHEN a\n- THEN b\n"
         "## MODIFIED Requirements\n### Requirement: Changed\nNew text.\n#### Scenario: s\n- WHEN a\n- THEN b\n"
         "## REMOVED Requirements\n### Requirement: Deleted\nReason: retired.\n"
         "## RENAMED Requirements\n- FROM: `### Requirement: Old Name`\n- TO: `### Requirement: New Name`\n")
LIVE = "openspec/changes/update/specs/catalog/spec.md"
ARCHIVE = "openspec/changes/archive/2026-11-01-update/specs/catalog/spec.md"
OTHER_ARCHIVE = "openspec/changes/archive/2026-10-01-other/specs/catalog/spec.md"
AFFECTED = {("catalog", name) for name in ("Added", "Changed", "New Name")}
GONE = {("catalog", "Deleted")}


def _entry(path: str, content: str) -> dict[str, str]:
    return {"path": path, "content": content, "sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()}


def _base() -> dict[str, str]:
    return _entry("openspec/specs/catalog/spec.md", BASELINE)


def test_delta_names_are_what_the_four_operations_touch() -> None:
    assert delta_names([_entry(LIVE, DELTA)]) == (AFFECTED, GONE)


def test_delta_names_read_a_file_with_windows_line_ends() -> None:
    assert delta_names([_entry(LIVE, DELTA.replace("\n", "\r\n"))]) == (AFFECTED, GONE)


def test_delta_names_are_the_changes_of_the_composed_requirement_set() -> None:
    before = {("catalog", name) for name in ("Kept", "Changed", "Deleted", "Old Name")}
    context = build_context(PACK_ROOT, docs_snapshot=[_base(), _entry(LIVE, DELTA)])
    after = set().union(*(identities(row) for row in context["artifacts"]["analytics_documentation"]["requirements"]))
    assert after == (before - GONE - {("catalog", "Old Name")}) | AFFECTED


def test_an_archived_delta_is_not_applied_to_the_requirement_set() -> None:
    context = build_context(PACK_ROOT, docs_snapshot=[_base(), _entry(ARCHIVE, DELTA)])
    names = {name for row in context["artifacts"]["analytics_documentation"]["requirements"] for _cap, name in identities(row)}
    assert names == {"Kept", "Changed", "Deleted", "Old Name"}


def test_without_a_delta_the_scope_is_every_case() -> None:
    assert scope([_base()], []).source == "all"


def test_a_live_change_sets_the_scope_from_its_delta() -> None:
    found = scope([_base(), _entry(LIVE, DELTA)], [])
    assert (found.source, found.affected, found.gone) == ("change", AFFECTED, GONE)


def test_a_named_archived_change_sets_the_scope_from_its_delta() -> None:
    found = scope([_base(), _entry(ARCHIVE, DELTA)], [ARCHIVE])
    assert (found.source, found.affected, found.gone) == ("archive", AFFECTED, GONE)


def test_an_archived_change_nobody_named_is_ignored() -> None:
    assert scope([_base(), _entry(ARCHIVE, DELTA), _entry(OTHER_ARCHIVE, DELTA)], [OTHER_ARCHIVE]).source == "archive"
    assert scope([_base(), _entry(ARCHIVE, DELTA)], []).source == "all"


def test_a_live_change_and_a_named_archive_are_both_the_scope() -> None:
    found = scope([_base(), _entry(LIVE, DELTA), _entry(ARCHIVE, "## REMOVED Requirements\n### Requirement: Kept\n")], [ARCHIVE])
    assert (found.source, found.affected, found.gone) == ("change+archive", AFFECTED, GONE | {("catalog", "Kept")})


@pytest.mark.parametrize("named", [
    "openspec/changes/archive/2026-11-01-update/specs/catalog/spec.md",  # not among the snapshots
    "openspec/changes/archive/2026-11-01-update/proposal.md",  # not a delta
])
def test_a_named_archive_file_that_cannot_be_read_as_a_delta_is_refused(named: str) -> None:
    with pytest.raises(ScopeSourceError, match=named):
        scope([_base()], [named])


@pytest.mark.parametrize("content", ["", "## Added Requirements\n### Requirement: Added\nNew.\n", "# a file of another schema\nText.\n"])
def test_a_named_archive_file_without_one_operation_is_refused(content: str) -> None:
    with pytest.raises(ScopeSourceError, match="нет операций ADDED / MODIFIED / REMOVED / RENAMED"):
        scope([_base(), _entry(ARCHIVE, content)], [ARCHIVE])


def _requirements() -> list[dict[str, object]]:
    context = build_context(PACK_ROOT, docs_snapshot=[_base(), _entry(LIVE, DELTA)])
    return [{**row, "source_requirement_id": f"SREQ-{number:04d}"}
            for number, row in enumerate(context["artifacts"]["analytics_documentation"]["requirements"], start=1)]


def _link(row: int, *ids: str) -> dict[str, object]:
    return {"row": row, "source_requirement_ids": list(ids)}


def _id_of(name: str) -> str:
    return next(str(row["source_requirement_id"]) for row in _requirements() if ("catalog", name) in identities(row))


def test_identities_are_read_from_the_provenance_of_a_requirement_with_its_scenarios() -> None:
    assert {name for row in _requirements() for _cap, name in identities(row)} == {"Added", "Changed", "Kept", "New Name"}


def test_every_row_is_judged_in_a_run_without_a_delta() -> None:
    links = [_link(1, "SREQ-0001"), _link(2)]
    assert judged_rows(links, _requirements(), scope([_base()], [])) == [1, 2]


def test_a_delta_judges_only_the_rows_linked_to_what_it_touches() -> None:
    links = [_link(1, _id_of("Kept")), _link(2, _id_of("Changed")), _link(3, _id_of("Kept"), _id_of("New Name"))]
    assert judged_rows(links, _requirements(), scope([_base(), _entry(LIVE, DELTA)], [])) == [2, 3]


def test_an_unlinked_row_is_judged_when_the_delta_removes_a_requirement() -> None:
    links = [_link(1, _id_of("Kept")), _link(2)]
    assert judged_rows(links, _requirements(), scope([_base(), _entry(LIVE, DELTA)], [])) == [2]


def test_an_unlinked_row_is_not_judged_when_the_delta_removes_nothing() -> None:
    delta = "## MODIFIED Requirements\n### Requirement: Changed\nNew text.\n#### Scenario: s\n- WHEN a\n- THEN b\n"
    links = [_link(1, _id_of("Kept")), _link(2)]
    assert judged_rows(links, _requirements(), scope([_base(), _entry(LIVE, delta)], [])) == []
