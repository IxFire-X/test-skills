"""Analyst report of one attempt: ``analyst-report.json`` and ``analyst-report.md``.

Questions for analysts come from three sources and are joined deterministically:

* the context-marker — structured ``requirement_gaps`` (context-marker output 5.1.0) and, for
  older answers, gap lines of the free ``warnings`` text («источник — наблюдение; недостающее: …;
  заблокированные проверки: …; вопрос: …», or the OpenSpec ``missing:/blocks:/question:`` form);
* reviewer findings that carry ``analyst_question`` (compact answer 2.1.0): the problem is in the
  requirement, not in the case; and reviewers' required checks too broad for one check part
  (``too_broad`` of the review aggregate) — asked as the reviewer worded them;
* ``SPEC_GAP`` decisions of the survivor triage.

Questions are joined per requirement (the most specific one a question names); every item keeps
all of its wordings and sources with a reference.  Building twice from the same inputs gives the same bytes.  The
package never writes into ``openspec/``: ``export`` prints a change comment to stdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

_REQUIREMENT = re.compile(r"\b(?:SREQ-\d{4}|CREQ-[A-Za-z0-9]+-\d{3,})\b")
_FIELDS = {
    "missing": ("недостающее:", "missing:"),
    "blocks": ("заблокированные проверки:", "blocks:"),
    "question": ("вопрос:", "question:"),
    "observed": ("наблюдение по коду:", "observed:"),
}
_SOURCE_ORDER = {"context-marker": 0, "tc-reviewer": 1, "autotest-reviewer": 2, "mutation-triage": 3}



def normalize(text: str) -> str:
    value = unicodedata.normalize("NFC", text or "").casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(" ?.!;:")


_NEGATION = re.compile(r"(?<![\w-])(?:не|нет|ни|без|нельзя|not|no|never|none|without|cannot|can't|don't|doesn't|isn't|shouldn't|mustn't)(?![\w-])")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def parse_warning(line: str) -> dict[str, Any] | None:
    """One gap from a legacy warning line, or None when the line is not a requirement gap."""
    if not isinstance(line, str) or " — " not in line:
        return None
    source, _, rest = line.partition(" — ")
    lowered = rest.casefold()
    positions = sorted((lowered.find(marker), name, marker) for name, markers in _FIELDS.items() for marker in markers if lowered.find(marker) >= 0)
    if not any(name in {"missing", "question"} for _position, name, _marker in positions):
        return None
    fields: dict[str, str] = {}
    for index, (position, name, marker) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(rest)
        fields.setdefault(name, rest[position + len(marker):end].strip().strip(";").strip())
    head = rest[:positions[0][0]].strip().strip(";").strip() if positions else rest.strip()
    return {"source_path": source.strip(), "summary": head, "requirement_ids": sorted(set(_REQUIREMENT.findall(rest))),
            "missing": fields.get("missing") or None, "blocks": fields.get("blocks") or None, "question": fields.get("question") or None}


def marker_items(marker: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not marker:
        return []
    items = []
    for index, gap in enumerate(marker.get("requirement_gaps") or [], start=1):
        items.append({"requirement_ids": sorted(set([gap["requirement"]] if _REQUIREMENT.fullmatch(str(gap.get("requirement", ""))) else
                                                    _REQUIREMENT.findall(str(gap.get("requirement", ""))))) or [str(gap.get("requirement"))],
                      "question": gap.get("question") or gap.get("missing"), "missing": gap.get("missing"), "blocks": gap.get("blocks"),
                      "source": {"kind": "context-marker", "ref": f"requirement_gaps[{index - 1}]", "location": gap.get("location")}})
    for index, line in enumerate(marker.get("warnings") or []):
        gap = parse_warning(line)
        if gap is None:
            continue
        items.append({"requirement_ids": gap["requirement_ids"] or [gap["source_path"]], "question": gap["question"] or gap["missing"] or gap["summary"],
                      "missing": gap["missing"], "blocks": gap["blocks"],
                      "source": {"kind": "context-marker", "ref": f"warnings[{index}]", "location": gap["source_path"]}})
    return items


def review_items(parts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """``parts``: ``{"review_key", "part_id", "result"}`` rows of accepted compact answers."""
    items = []
    for part in parts:
        kind = "tc-reviewer" if part["review_key"] == "canonical" else "autotest-reviewer"
        for index, finding in enumerate(part["result"].get("findings") or []):
            question = finding.get("analyst_question")
            if not question:
                continue
            requirements = sorted({identifier for identifier in finding.get("related_ids", []) if _REQUIREMENT.fullmatch(identifier)})
            items.append({"requirement_ids": requirements or sorted(finding.get("related_ids", [])), "question": question, "missing": None, "blocks": None,
                          "source": {"kind": kind, "ref": f"{part['review_key']}:{part['part_id']}:findings[{index}]", "location": finding.get("code"),
                                     "severity": finding.get("severity")}})
    return items


def too_broad_items(review_key: str, aggregate: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A reviewer's required check too broad for one check part (``REVIEW_CHECK_TOO_BROAD`` in the review aggregate):
    its request goes to the analyst as asked."""
    kind = "tc-reviewer" if review_key == "canonical" else "autotest-reviewer"
    return [{"requirement_ids": sorted(row["requirement_ids"]), "question": row["question"], "missing": None, "blocks": None,
             "source": {"kind": kind, "ref": f"{review_key}:{row['part_id']}:too_broad[{index}]", "location": "REVIEW_CHECK_TOO_BROAD",
                        "severity": "WARNING"}}
            for index, row in enumerate(aggregate.get("too_broad") or [])]


def triage_items(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"requirement_ids": [row["requirement_id"]], "question": row["question"], "missing": None, "blocks": None,
             "source": {"kind": "mutation-triage", "ref": row["group_id"], "location": ", ".join(row.get("refs", [])[:3])}}
            for row in rows if row.get("decision") == "SPEC_GAP"]


def build_report(items: Sequence[Mapping[str, Any]], *, run_id: str, attempt_id: str, sources_of: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:
    """One item per requirement: the questions of one requirement join, every wording and source kept.

    ``sources_of`` maps a CREQ (and a case) to its SREQs so both spellings meet.  A requirement named
    only inside a source label («docs/… (SREQ-0009, O07, …)») counts as that requirement, so the
    context-marker's structured gap and its warning line are one question.  A question tied to several
    requirements belongs to the most specific of them — the one the fewest cases map to (the general
    requirements every case shares do not collect everybody's questions).  The most asked wording heads
    the item, the others stay in ``also_asked`` (live run g, 2026-10-09: 47 questions, 15 requirements).
    """
    joined: dict[tuple, dict[str, Any]] = {}

    def requirements(ids: Sequence[str]) -> tuple[str, ...]:
        mapped = set()
        for identifier in ids:
            named = [identifier] if _REQUIREMENT.fullmatch(identifier) else _REQUIREMENT.findall(identifier) or [identifier]
            for name in named:
                mapped.update((sources_of or {}).get(name) or [name])
        return tuple(sorted(mapped))

    for item in items:
        if not item.get("question"):
            continue
        key = (requirements(item["requirement_ids"]), normalize(item["question"]))
        row = joined.setdefault(key, {"requirement_ids": list(key[0]), "question": item["question"].strip(), "missing": None, "blocks": None, "sources": []})
        row["missing"] = row["missing"] or item.get("missing")
        row["blocks"] = row["blocks"] or item.get("blocks")
        source = {name: value for name, value in item["source"].items() if value is not None}
        if source not in row["sources"]:
            row["sources"].append(source)
    # How general a requirement is: how many CREQs and cases map to it (without a mapping: how many questions name it).
    spread: dict[str, int] = {}
    for names in (sources_of.values() if sources_of else (key[0] for key in joined)):
        for name in set(names):
            spread[name] = spread.get(name, 0) + 1

    def primary(names: tuple[str, ...]) -> str:
        candidates = [name for name in names if _REQUIREMENT.fullmatch(name)] or list(names) or [""]
        return min(candidates, key=lambda name: (spread.get(name, 0), name))

    groups: dict[str, list[tuple]] = {}
    for key in sorted(joined):
        groups.setdefault(primary(key[0]), []).append(key)
    rows = []
    for home, keys in sorted(groups.items()):
        head = max(keys, key=lambda key: len(joined[key]["sources"]))  # the most asked; the first in sorted order on a tie
        ordered = [head, *[key for key in keys if key != head]]
        row = {"requirement_ids": [home, *sorted({name for key in keys for name in key[0]} - {home})], "question": joined[head]["question"],
               "also_asked": [joined[key]["question"] for key in ordered[1:]],
               "missing": next((joined[key]["missing"] for key in ordered if joined[key]["missing"]), None),
               "blocks": next((joined[key]["blocks"] for key in ordered if joined[key]["blocks"]), None),
               "sources": [source for key in ordered for source in joined[key]["sources"]]}
        row["sources"] = [source for index, source in enumerate(row["sources"]) if source not in row["sources"][:index]]
        row["sources"].sort(key=lambda source: (_SOURCE_ORDER.get(source["kind"], 9), source["ref"]))
        row["item_id"] = "AQ-" + hashlib.sha256(json.dumps([[home], head[1]], ensure_ascii=False).encode("utf-8")).hexdigest()[:10].upper()
        rows.append({name: row[name] for name in ("item_id", "requirement_ids", "question", "also_asked", "missing", "blocks", "sources")})
    return {"schema_version": "1.0.0", "run_id": run_id, "attempt_id": attempt_id, "items": rows,
            "counts": {kind: sum(any(source["kind"] == kind for source in row["sources"]) for row in rows) for kind in _SOURCE_ORDER}}


REWORDING = 0.45


def rewording(left: str, right: str) -> bool:
    """One question in other words: the same negations, numbers that do not contradict (one wording may leave
    a number out), and mostly the same words (Jaccard of the 3+ letter words).  Questions that differ in a
    negation or a number are different questions ("возвращать" / "не возвращать", "длиннее 40" / "длиннее 20")
    — review 2.1 item 14.  Live run g: nine reviewers asked
    the V05 question at 0.48–0.70; different questions of one requirement stayed at 0–0.16."""
    if sorted(_NEGATION.findall(left)) != sorted(_NEGATION.findall(right)):
        return False
    numbers = [set(_NUMBER.findall(value)) for value in (left, right)]
    if not (numbers[0] <= numbers[1] or numbers[1] <= numbers[0]):
        return False
    words = [set(re.findall(r"[\w-]{3,}", value)) for value in (left, right)]
    union = words[0] | words[1]
    return not union or len(words[0] & words[1]) / len(union) >= REWORDING


def _plural(count: int, one: str, few: str, many: str) -> str:
    return one if count % 10 == 1 and count % 100 != 11 else few if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14) else many


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Вопросы аналитикам", "", f"Прогон `{report['run_id']}`, попытка `{report['attempt_id']}`. Требований с вопросами: {len(report['items'])}.", ""]
    if not report["items"]:
        lines += ["Вопросов нет.", ""]
        return "\n".join(lines)
    names = {"context-marker": "разметка требований", "tc-reviewer": "ревью кейсов", "autotest-reviewer": "ревью автотестов", "mutation-triage": "разбор мутантов"}
    for row in report["items"]:
        # The heading names requirements only; the case, step and check ids stay in the JSON and in the sources.
        heading = [name for name in row["requirement_ids"] if _REQUIREMENT.fullmatch(name)] or row["requirement_ids"]
        lines.append(f"## {row['item_id']}: {', '.join(heading)}")
        lines.append("")
        lines.append(f"**Вопрос.** {row['question']}")
        count = len(row["sources"])
        if count > 1:
            lines.append(f"**Спрошено {count} {_plural(count, 'раз', 'раза', 'раз')}.**")
        variants = row.get("also_asked") or []
        same = [variant for variant in variants if rewording(normalize(row["question"]), normalize(variant))]
        if same:
            lines.append(f"**Тот же вопрос иначе:** ещё {len(same)} {_plural(len(same), 'формулировка', 'формулировки', 'формулировок')} (в JSON, `also_asked`).")
        lines += [f"**Ещё по этому требованию.** {variant}" for variant in variants if variant not in same]
        if row["missing"]:
            lines.append(f"**Чего не хватает.** {row['missing']}")
        if row["blocks"]:
            lines.append(f"**Какие проверки блокирует.** {row['blocks']}")
        lines.append("**Источники.** " + "; ".join(f"{names.get(source['kind'], source['kind'])} `{source['ref']}`" + (f" ({source['location']})" if source.get("location") else "")
                                                for source in row["sources"]))
        lines.append("")
    return "\n".join(lines)


def write_report(directory: Path, report: Mapping[str, Any]) -> dict[str, str]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = directory / "analyst-report.json", directory / "analyst-report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    markdown_path.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return {"analyst_report_json": str(json_path), "analyst_report_markdown": str(markdown_path)}


def openspec_comment(report: Mapping[str, Any], change: str | None = None) -> str:
    """A comment for an OpenSpec change (printed; the package never writes into ``openspec/``)."""
    head = f"Вопросы к change `{change}`" if change else "Вопросы к требованиям"
    lines = [f"<!-- test-skills analyst report {report['run_id']}/{report['attempt_id']} -->", f"### {head}", ""]
    lines += [f"- [ ] **{', '.join(row['requirement_ids'])}** — {row['question']} (`{row['item_id']}`)" for row in report["items"]] or ["Вопросов нет."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyst report: print an OpenSpec change comment for a run.")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--project", required=True)
    export.add_argument("--run", required=True)
    export.add_argument("--change")
    args = parser.parse_args(argv)
    from tools.pipeline_driver_analyst import attempt_report

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = attempt_report(Path(args.project).resolve(), args.run)
    sys.stdout.write(openspec_comment(report, args.change))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
