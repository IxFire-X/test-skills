"""Deterministic merge of context-marker batch answers into one ``context-marker:baseline`` artifact (P1a).

A pure function of the batch artifacts: the driver calls it to build the merged answer, the run journal calls it
again when the merged answer is written and read, so the merged marker can always be recomputed from the
recorded batch answers.  No driver, journal or file access here.

Rules (batch order, then answer order; the first occurrence wins):
- requirements must be the same in every batch and are taken as they are;
- ``schema_version`` is the highest of the batches;
- ``sources``: duplicates are the same text after whitespace normalisation; the draft placeholder goes once a
  batch observed real code;
- ``warnings``: the duplicate key is (source, observation) split at the first « — »;
- ``requirement_gaps``: the duplicate key is (requirement, location, missing); the field is present only when a
  batch had it.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Callable, Mapping, Sequence

PLACEHOLDER = "driver — источники кода не анализировались детерминированно"
SEPARATOR = " — "


def _normal(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _warning_key(text: Any) -> tuple[str, ...]:
    normal = _normal(text)
    source, found, observation = normal.partition(SEPARATOR.strip())
    return (_normal(source), _normal(observation)) if found else (normal,)


def _gap_key(gap: Any) -> tuple[str, ...]:
    if not isinstance(gap, Mapping):
        return (_normal(gap),)
    return tuple(_normal(gap.get(name, "")) for name in ("requirement", "location", "missing"))


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


def _version(value: Any) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return (0,)


def merge_marker_artifacts(artifacts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """One marker artifact from the batch answers, in batch order."""
    if not artifacts:
        raise ValueError("context-marker merge needs at least one batch answer")
    requirements = [artifact["artifacts"]["analytics_documentation"]["requirements"] for artifact in artifacts]
    if any(item != requirements[0] for item in requirements[1:]):
        raise ValueError("context-marker batch answers carry different requirements")
    stages = {artifact.get("stage") for artifact in artifacts}
    if stages != {"context-marker"}:
        raise ValueError("context-marker merge accepts context-marker answers only")
    sources = _union([artifact["artifacts"]["source_code_and_diff"]["sources"] for artifact in artifacts], _normal)
    if any(_normal(item) != PLACEHOLDER for item in sources):
        sources = [item for item in sources if _normal(item) != PLACEHOLDER]
    merged: dict[str, Any] = {
        "schema_version": max((str(artifact["schema_version"]) for artifact in artifacts), key=_version),
        "stage": "context-marker",
        "artifacts": {"analytics_documentation": copy.deepcopy(dict(artifacts[0]["artifacts"]["analytics_documentation"])),
                      "source_code_and_diff": {**copy.deepcopy(dict(artifacts[0]["artifacts"]["source_code_and_diff"])), "sources": sources}},
        "warnings": _union([artifact.get("warnings") or [] for artifact in artifacts], _warning_key),
    }
    if any("requirement_gaps" in artifact for artifact in artifacts):
        merged["requirement_gaps"] = _union([artifact.get("requirement_gaps") or [] for artifact in artifacts], _gap_key)
    return merged
