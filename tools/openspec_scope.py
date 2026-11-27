"""The scope of a run of an OpenSpec change only (R&D-14): the lines for the tester and the neighbours of the change.

A requirement of the base is a neighbour of a requirement of the change when they share rare words: stems (a Russian word cut
to five letters, a latin identifier, a number, a quoted literal) weighted by rarity over the whole base, the share of the
smaller text at or above ``THRESHOLD`` (``SAME_AREA`` in the area of the requirement of the change), and the text of the change
names its subject: a stem of the name of the neighbour (the class of an input — «пустая строка», «три пробела» — is shared by
requirements of different subjects; 15 pairs of base and change of live/ on 27.11: 25 neighbours, 4 false → 23, 0).  Code only, no model; stdlib only, no input or output: the texts come from the
composition (``build_context``).  The heads of the lines are one constant: the report finds the lines by them.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

SCOPE_HEADS = ("Объём OpenSpec:", "Проверьте регресс:", "Объём неполный:", "Не применено:")
SCOPE, NEIGHBOURS, PARTIAL, DISPUTED = SCOPE_HEADS
# text 7 of 25.11 with the place of the MODIFIED in front: a warning of the marker is «… — …» by its schema (inline_provenance_string)
LINKED = re.compile(r"^(?:.+?:\d+ — )?(ADDED «.+» связан с неприменённым MODIFIED «.+»: его кейсы написаны по нынешнему тексту базы\. Верно ли это\?)$")
QUESTIONS = "## Требования изменения, которые не применились"
THRESHOLD = 0.3
SAME_AREA = 0.25  # a requirement of the base in the area of the requirement of the change
MOST = 5  # neighbours of one requirement of the change
IMPACT_MOST = 3  # neighbours of an area the proposal names under «Impact», below the threshold too
SHOWN = 8  # neighbours in the line of scan and ready; report.md has them all
_STOP = {"систе", "shall", "когда", "тогда", "scenario", "requirement", "given", "when", "then", "если", "после", "которы", "должн",
         "между", "также", "этого", "этому", "будет", "может", "нужно", "только"}


def stems(text: str) -> frozenset[str]:
    lowered = text.lower()
    found = set(re.findall(r'[«"]([^«»"]{3,40})[»"]', lowered))
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[а-яё]{4,}|\d{2,}", lowered):
        stem = word[:5] if re.match(r"[а-яё]", word) else word
        if stem not in _STOP:
            found.add(stem)
    return frozenset(found)


class Weights:
    """The rarity of a stem over ``texts`` (the requirements of the base)."""

    def __init__(self, texts: Iterable[str]):
        sets = [stems(text) for text in texts]
        self.count, self.found = len(sets), Counter(stem for found in sets for stem in found)

    def _idf(self, stem: str) -> float:
        return math.log(1 + self.count / (1 + self.found[stem]))

    def score(self, first: str, second: str) -> float:
        one, two = stems(first), stems(second)
        total = min(sum(map(self._idf, one)), sum(map(self._idf, two)))
        return round(sum(map(self._idf, one & two)) / total, 2) if total > 0 else 0.0


def impact_areas(proposal: str, areas: Iterable[str]) -> set[str]:
    """The ``areas`` named by a word in the section «## Impact» of the proposal of the change."""
    section = re.search(r"^## Impact[ \t]*\r?$(.*?)(?=^## |\Z)", proposal, re.M | re.S)  # a file of Windows keeps its \r
    words = set(re.findall(r"[\w-]+", section.group(1).casefold())) if section else set()
    return {area for area in areas if area.casefold() in words}


def neighbours(weights: Weights, text: str, pool: Sequence[tuple[str, str, str]], impact: set[str], own: str | None = None) -> list[tuple[float, str, str]]:
    """``(score, area, name)`` of the requirements of ``pool`` near ``text`` of the area ``own``: up to ``MOST`` at or above the threshold
    whose name ``text`` names by a stem, and up to ``IMPACT_MOST`` of each area of ``impact`` below it."""
    scored = sorted(((weights.score(text, body), area, name) for area, name, body in pool), key=lambda row: (-row[0], row[1], row[2]))
    named = stems(text)
    near = [row for row in scored if row[0] >= (SAME_AREA if row[1] == own else THRESHOLD) and stems(row[2]) & named][:MOST]
    for area in sorted(impact):
        near += [row for row in scored if row[1] == area and 0 < row[0] < THRESHOLD][:IMPACT_MOST]
    return near


def _regression(areas: Sequence[str]) -> str:
    return f"--docs openspec/specs/{areas[0]}" if len(areas) == 1 else f"--docs openspec/specs/<область> (области: {', '.join(areas)})"


def _regression_line(change: str, areas: Sequence[str]) -> str:
    return f"{SCOPE} только изменение {change}; базовые спецификации областей — контекст; для полной регрессии области добавьте {_regression(areas)}"


def lines(change: str, areas: Sequence[str], applied: Sequence[tuple[str, str, str, str]], disputed: Sequence[tuple[str, str, str, str, str, int, str]],
          pool: Sequence[tuple[str, str, str]], proposal: str, shown: int | None = None) -> list[str]:
    """The lines of the scope (texts of 25.11): ``applied`` — ``(operation, area, name, text)`` of the requirements of the change that
    give cases; ``disputed`` — ``(operation, area, name, text, path, line, cause)`` of those that did not apply; ``pool`` — the
    requirements of the base the change does not touch; ``shown`` cuts the list of the neighbours (None: all of them)."""
    weights = Weights([body for _area, _name, body in pool] + [row[3] for row in applied])
    impact = impact_areas(proposal, {area for area, _name, _body in pool})
    found = [_regression_line(change, areas)]
    near: dict[tuple[str, str], tuple[float, str]] = {}
    for _operation, own, name, text in applied:
        for score, area, other in neighbours(weights, text, pool, impact, own):
            if score > near.get((area, other), (-1.0, ""))[0]:
                near[(area, other)] = (score, name)
    if near:
        rows = sorted(near.items(), key=lambda item: (-item[1][0], item[0]))
        items = [f"{area}/«{other}» (рядом с «{name}»)" for (area, other), (_score, name) in rows]
        cut = shown is not None and len(items) > shown
        listed = "; ".join(items[:shown] if cut else items) + (f" и ещё {len(items) - shown} — в report.md" if cut else "")
        found.append(f"{NEIGHBOURS} смежные требования базы, которых изменение {change} не меняет, но может задеть: {listed}; "
                     f"полная регрессия области — {_regression(sorted({area for area, _other in near}))}")
    if disputed:
        found.append(f"{PARTIAL} применено {len(applied)} из {len(applied) + len(disputed)} требований изменения {change}; "
                     f"не взято {len(disputed)} — причины в строках «{DISPUTED.removesuffix(':')}» (scan, status, report.md); "
                     "вопросы аналитику — в questions-for-analyst.md после report")
    for operation, own, name, text, path, line, cause in disputed:  # an ADDED the base has with another text is no neighbour of itself
        others = [row for row in pool if (row[0], row[1]) != (own, name)]
        close = ", ".join(f"{area}/«{other}»" for _score, area, other in neighbours(weights, text, others, impact, own))
        found.append(f"{DISPUTED} {operation} «{name}» ({path}:{line}) — {cause}" + (f"; смежные с ним: {close}" if close else ""))
    for operation, _area, name, text in applied:
        for _operation, _other_area, other, other_text, other_path, other_line, _cause in disputed:
            if operation == "ADDED" and _operation == "MODIFIED" and weights.score(text, other_text) >= THRESHOLD:
                found.append(f"{other_path}:{other_line} — ADDED «{name}» связан с неприменённым MODIFIED «{other}»: его кейсы написаны по нынешнему тексту базы. Верно ли это?")
    return found


def summary_tail(said: str, warnings: Iterable[object]) -> str:
    """``human_summary`` of a run with the tail of 25.11 when requirements of the change only did not apply (the marker ``warnings``)."""
    left = sum(str(line).startswith(DISPUTED) for line in warnings)
    return f"{said.removesuffix('.')}; объём неполный: {left} требований изменения не взято." if left else said


_DISPUTED_LINE = re.compile(r"^Не применено: (ADDED|MODIFIED) «(.+)» \((.+?:\d+)\) — (.+)$")


def question(line: str) -> str | None:
    """The question to the analyst (text 6 of 25.11) of a line «Не применено: …»; the line of text 7 as it is; None for any other line."""
    if (linked := LINKED.fullmatch(line)) is not None:
        return f"- {linked.group(1)}"
    match = _DISPUTED_LINE.fullmatch(line)
    if match is None:
        return None
    operation, name, place, cause = match.groups()
    cause = cause.split("; смежные с ним: ", 1)[0]
    return f"- {operation} «{name}» ({place}): {cause}. Кейсы на него не написаны. Поправьте изменение или spec.md и повторите прогон."
