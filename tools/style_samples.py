"""Style samples of a team (v0.9, R&D-15 step 1): up to three cases of ``.skillsrc`` ``style.samples`` (a Zephyr CSV) show the
generator the form of the team's cases — never their facts.

``select(cases)`` keeps the human fields of the first ``SAMPLES`` cases whose text is at most ``SAMPLE_BYTES`` (no Key, owner,
labels or own fields: an owner is a person's name).  ``transferred(document, samples, known)`` names, per new case, the facts of
the samples — numbers, quoted values, Latin words, capitalised words inside a sentence, mail, addresses — that the case carries
while neither the requirements nor the context of the run (*known*) give them: the hint ``CV_STYLE_SAMPLE`` of the reviewer.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SAMPLES = 3
SAMPLE_BYTES = 2048
FILE_BYTES = 256 * 1024  # the CSV is refused above it before the parse: three short cases need a few KB
CODE = "CV_STYLE_SAMPLE"
_FACT = re.compile(
    r"«([^»\n]{1,80})»|\"([^\"\n]{1,80})\"|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)|(https?://\S+|(?<![\w/])/[\w./{}-]+)|(\d+(?:[.,:]\d+)*)"
    r"|([A-Za-z][A-Za-z0-9_.-]*[A-Za-z0-9])|(?:(?<=[^\W\d_] )|(?<=[:,;—–-] ))([А-ЯЁ][а-яё]+)")  # a capital inside a sentence or after «:», «,», «—»


def _texts(case: Mapping[str, Any]) -> list[str]:
    return [case["title"], case["objective"], case["preconditions"],
            *(text for step in case["steps"] for text in (step["action"], step["test_data"], step["expected"]))]


def human(case: Mapping[str, Any]) -> dict[str, Any]:
    """The human fields of a parsed Zephyr case (``zephyr_csv.parse_zephyr_csv``), nothing of its management."""
    return {"title": case["title"], "objective": case["objective"], "preconditions": case["preconditions"],
            "steps": [{"action": step["action"], "test_data": step["test_data"], "expected": step["expected"]} for step in case["steps"]]}


def select(cases: Sequence[Mapping[str, Any]], path: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``(samples, warnings of the scan)``: the first three cases of at most 2 KB of human text, in the order of the file."""
    samples: list[dict[str, Any]] = []
    warnings: list[str] = []
    for case in cases:
        if len(samples) == SAMPLES:  # first: a case after the three is not taken whatever its length
            warnings.append(f"В {path} кейсов больше трёх — взяты первые три.")
            break
        sample = human(case)
        if sum(len(text.encode("utf-8")) for text in _texts(sample)) > SAMPLE_BYTES:
            warnings.append(f"Образец «{case['title']}» длиннее 2 КБ — не взят.")
        else:
            samples.append(sample)
    if samples:
        warnings.insert(0, f"Образцы стиля: взято {len(samples)} {'кейс' if len(samples) == 1 else 'кейса'} из {path} — модель возьмёт форму, не факты.")
    return samples, warnings


def facts(texts: Iterable[str]) -> set[str]:
    """The facts of the texts, lower case: what a sample may carry over that is not form."""
    found = set()
    for text in texts:
        for match in _FACT.finditer(str(text)):
            value = next(group for group in match.groups() if group is not None).strip().lower()
            if len(value) >= 2:
                found.add(value)
    return found


def _within(value: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(value) + r"(?!\w)", text) is not None


def transferred(document: Mapping[str, Any], samples: Sequence[Mapping[str, Any]], known: str) -> dict[str, list[str]]:
    """``case_id -> the facts of the samples the case carries and *known* does not give``, only the cases that carry some."""
    carried = facts(text for sample in samples for text in _texts(sample))
    known = known.lower()
    carried = {value for value in carried if not _within(value, known)}
    found: dict[str, list[str]] = {}
    for case in document["test_cases"]:
        text = "\n".join([case["title"], case["objective"], *case["preconditions"],
                          *(part for step in case["steps"] for part in (step["action"], step["test_data"], *(item["text"] for item in step["expectations"])))]).lower()
        hit = sorted(value for value in carried if _within(value, text))
        if hit:
            found[str(case["case_id"])] = hit
    return found


def known_text(project: Path, document: Mapping[str, Any], files: Sequence[Mapping[str, Any]]) -> str:
    """The requirements of *document* and the context files of the run (``project_path``, ``content_digest`` of the context
    receipts) whose bytes still match — what the run itself gives."""
    from tools.output_guard import project_file

    parts = [str(item["text"]) for key in ("source_requirements", "requirements") for item in document.get(key) or []]
    for row in files:
        try:
            raw = project_file(Path(project), str(row["project_path"])).read_bytes()  # by its text, inside the project, no link on the way
        except (OSError, ValueError):
            continue
        if "sha256:" + hashlib.sha256(raw).hexdigest() == row.get("content_digest"):  # a file changed after the scan is not the scanned project
            parts.append(raw.decode("utf-8", errors="replace"))
    return "\n".join(parts)


def hint_lines(found: Mapping[str, Sequence[str]], shown: int = 10) -> list[str]:
    """The line of the review view; none without a carried fact (the view keeps its bytes)."""
    if not found:
        return []
    cases = list(found.items())
    listed = "; ".join(f"{case_id}: " + ", ".join(f"«{value}»" for value in values[:5]) for case_id, values in cases[:shown])
    more = f"; и ещё {len(cases) - shown}" if len(cases) > shown else ""
    return [f"- {CODE}: в кейсах есть значения из образцов стиля, которых нет в требованиях и данных прогона: {listed}{more} — справка: "
            "такое значение — находка, если требования его не дают"]
