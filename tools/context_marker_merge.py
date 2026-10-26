"""Deterministic merge of the context-marker parts into one ``context-marker:baseline`` artifact (FaT-5, from main P1).

The marker of a large module is made in parts: ``context-marker:requirements`` normalises the requirements from the
documents alone, each ``context-marker:batch-NNN`` adds the code observations of one context batch with the same
requirements.  The merge is a pure function of those answers: the controller calls it to publish the merged marker, the
run journal calls it again when the merged marker is written and read.  No journal or file access here.

Rules (part order, then answer order; the first occurrence wins):
- requirements are the requirements answer's, and every part must carry them unchanged;
- ``sources``: union, duplicates are the same text after whitespace normalisation; the placeholder of the requirements
  step goes away once a part observed code;
- ``warnings``: union of the requirements answer's and the parts'; the duplicate key is (source, observation) split at
  the first « — ».
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping, Sequence

# The one source the requirements step writes: it reads documents, no code.
REQUIREMENTS_PLACEHOLDER = "шаг требований — код не анализировался"
SEPARATOR = " — "


def _normal(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _warning_key(text: Any) -> tuple[str, ...]:
    normal = _normal(text)
    source, found, observation = normal.partition(SEPARATOR.strip())
    return (_normal(source), _normal(observation)) if found else (normal,)


def _union(groups: Sequence[Sequence[Any]], key: Callable[[Any], Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for group in groups:
        for item in group:
            marker = key(item)
            if marker in seen:
                continue
            seen.add(marker)
            result.append(copy.deepcopy(item))
    return result


def requirements_of(artifact: Mapping[str, Any]) -> Any:
    return artifact["artifacts"]["analytics_documentation"]["requirements"]


def merge_marker_artifacts(requirements: Mapping[str, Any], parts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One marker artifact from the requirements answer and the part answers, in part order."""
    if not parts:
        raise ValueError("context-marker merge needs at least one part answer")
    if {artifact.get("stage") for artifact in (requirements, *parts)} != {"context-marker"}:
        raise ValueError("context-marker merge accepts context-marker answers only")
    expected = requirements_of(requirements)
    if any(requirements_of(part) != expected for part in parts):
        raise ValueError("CONTEXT_MARKER_REQUIREMENTS_CHANGED: a part changed the requirements of the requirements step")
    sources = _union([part["artifacts"]["source_code_and_diff"]["sources"] for part in parts], _normal)
    sources = [item for item in sources if _normal(item) != REQUIREMENTS_PLACEHOLDER] or [REQUIREMENTS_PLACEHOLDER]
    return {
        "schema_version": str(requirements["schema_version"]),
        "stage": "context-marker",
        "artifacts": {"analytics_documentation": copy.deepcopy(dict(requirements["artifacts"]["analytics_documentation"])),
                      "source_code_and_diff": {**copy.deepcopy(dict(parts[0]["artifacts"]["source_code_and_diff"])), "sources": sources}},
        "warnings": _union([requirements.get("warnings") or [], *(part.get("warnings") or [] for part in parts)], _warning_key),
    }
