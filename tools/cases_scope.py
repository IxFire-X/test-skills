"""Which old cases an update of cases judges — the requirements a delta of OpenSpec touches.

An import with documents judges the cases of the CSV against the current text of their requirements.  The scope of that
judgement is decided by code, from the delta files of OpenSpec, never by the model:

* a live change in the snapshots — its delta;
* an archived change the human named in ``--docs`` — its delta, read for the *names* only; the archive is never applied again;
* neither — every case is judged.

Pure functions over snapshots (``{"path", "sha256", "content"}`` rows the caller already checked against their bytes): nothing is
read, written or composed here.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, NamedTuple, Sequence

_ARCHIVE = "openspec/changes/archive/"
_DELTA = re.compile(r"^openspec/changes/(?:archive/)?(?P<change>[^/]+)/specs/(?P<capability>.+)/spec\.md$")
_HEADING = re.compile(r"^(##\s+[^\n]+|### Requirement:\s*[^\n]+)$", re.M)
_PAIR = re.compile(r"^\s*(?:[-*+]\s+)?(FROM|TO):\s*`?### Requirement:\s*(.+?)`?\s*$", re.M)
_IDENTITY = re.compile(r"capability=(?P<capability>.+?); ### Requirement: (?P<name>.+?)(?:; #### Scenario: .*)?$")

Name = tuple[str, str]  # (capability, requirement name)


class ScopeSourceError(ValueError):
    """A delta file the human named cannot be read as one."""


class Scope(NamedTuple):
    source: str  # "all", "change", "archive" or "change+archive"
    affected: frozenset[Name]  # added, modified or renamed (the new name) by the delta
    gone: frozenset[Name]  # removed by the delta
    added: frozenset[Name] = frozenset()  # the ADDED ones: a requirement that did not exist, so no old case can check it


def _path(entry: Mapping[str, str]) -> str:
    return entry["path"].replace("\\", "/")


def delta_names(entries: Sequence[Mapping[str, str]]) -> tuple[set[Name], set[Name]]:
    """``(affected, gone)`` of delta files: ADDED, MODIFIED and the new name of RENAMED touch a requirement, REMOVED takes one away."""
    return _delta(entries)[:2]


def _delta(entries: Sequence[Mapping[str, str]]) -> tuple[set[Name], set[Name], set[Name]]:
    """``(affected, gone, added)``: ``added`` is the part of ``affected`` the ADDED sections name."""
    affected: set[Name] = set()
    gone: set[Name] = set()
    added: set[Name] = set()
    for entry in entries:
        capability = _DELTA.fullmatch(_path(entry))["capability"]
        text, section = entry["content"], ""
        headings = list(_HEADING.finditer(text))
        for index, heading in enumerate(headings):
            title = heading.group().strip()
            if title.startswith("## "):
                section = title[3:].strip().removesuffix(" Requirements")
                if section == "RENAMED":
                    stop = next((item.start() for item in headings[index + 1:] if item.group().startswith("## ")), len(text))
                    affected |= {(capability, name.strip()) for kind, name in _PAIR.findall(text[heading.end():stop]) if kind == "TO"}
                continue
            name = (capability, title[len("### Requirement:"):].strip())
            if section in ("ADDED", "MODIFIED"):
                affected.add(name)
                if section == "ADDED":
                    added.add(name)
            elif section == "REMOVED":
                gone.add(name)
    return affected, gone, added


def scope(snapshots: Sequence[Mapping[str, str]], docs: Sequence[str]) -> Scope:
    """The scope of the run: the live change(s) of the snapshots and the archived delta files ``docs`` names.
    A named file under ``openspec/changes/archive/`` that is no delta among the snapshots, or names no operation, is
    ``ScopeSourceError``: an empty scope would read as «every case fits»."""
    by_path = {_path(entry): entry for entry in snapshots}
    named = [path for path in dict.fromkeys(item.replace("\\", "/") for item in docs) if path.startswith(_ARCHIVE)]
    unread = [path for path in named if path not in by_path or not _DELTA.fullmatch(path)]
    if unread:
        raise ScopeSourceError(f"архивное изменение названо, но это не файл дельты среди снимков: {', '.join(unread)}")
    live = [entry for path, entry in by_path.items() if _DELTA.fullmatch(path) and not path.startswith(_ARCHIVE)]
    archived = [by_path[path] for path in named]
    empty = [path for path in named if not any(delta_names([by_path[path]]))]  # nobody else sifts an archived file out
    if live and not any(delta_names(live)):  # a live change that names no operation is no «nothing changed» either
        empty += [_path(entry) for entry in live]
    if empty:
        raise ScopeSourceError(f"в файле нет операций ADDED / MODIFIED / REMOVED / RENAMED: {', '.join(empty)}")
    if not live and not archived:
        return Scope("all", frozenset(), frozenset())
    affected, gone, added = _delta([*live, *archived])
    return Scope("+".join(label for label, found in (("change", live), ("archive", archived)) if found), frozenset(affected), frozenset(gone), frozenset(added))


def identities(requirement: Mapping[str, Any]) -> set[Name]:
    """The OpenSpec requirements a normalized requirement stands for, from the identity marks of its provenance."""
    marks = (_IDENTITY.search(str(mark)) for mark in requirement.get("provenance") or [])
    return {(found["capability"], found["name"].strip()) for found in marks if found}


def unmatched(requirements: Sequence[Mapping[str, Any]], found: Scope) -> list[Name]:
    """The names the delta touches that no requirement stands for (an archived change the specification has moved past, or a spec.md not named)."""
    return sorted(found.affected - set().union(*(identities(item) for item in requirements)))


def judged_rows(links: Sequence[Mapping[str, Any]], requirements: Sequence[Mapping[str, Any]], found: Scope) -> list[int]:
    """The CSV rows whose case must carry a verdict: every row without a delta; with one, the rows linked to a requirement
    the delta touches, and — when it removes a requirement — the rows linked to none (the case of a removed requirement
    has nothing to link to)."""
    if found.source == "all":
        return [link["row"] for link in links]
    touched = {str(item["source_requirement_id"]) for item in requirements if identities(item) & found.affected}
    return [link["row"] for link in links
            if touched & set(link["source_requirement_ids"]) or (found.gone and not link["source_requirement_ids"])]
