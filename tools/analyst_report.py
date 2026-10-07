"""Analyst report of one attempt: ``analyst-report.json`` and ``analyst-report.md``.

Questions for analysts come from three sources and are joined deterministically:

* the context-marker — structured ``requirement_gaps`` (context-marker output 5.1.0) and, for
  older answers, gap lines of the free ``warnings`` text («источник — наблюдение; недостающее: …;
  заблокированные проверки: …; вопрос: …», or the OpenSpec ``missing:/blocks:/question:`` form);
* reviewer findings that carry ``analyst_question`` (compact answer 2.1.0): the problem is in the
  requirement, not in the case;
* ``SPEC_GAP`` decisions of the survivor triage.

Duplicates are joined by requirement and normalized question; every item keeps all of its
sources with a reference.  Building twice from the same inputs gives the same bytes.  The
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


SIMILAR = 0.7


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFC", text or "").casefold()
    value = re.sub(r"\s+", " ", value).strip()
    return value.rstrip(" ?.!;:")


def similarity(left: str, right: str) -> float:
    """Jaccard similarity of the words (3+ letters) of two normalized questions."""
    words = [set(re.findall(r"[\w-]{3,}", value)) for value in (left, right)]
    union = words[0] | words[1]
    return 1.0 if not union else len(words[0] & words[1]) / len(union)


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


def triage_items(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{"requirement_ids": [row["requirement_id"]], "question": row["question"], "missing": None, "blocks": None,
             "source": {"kind": "mutation-triage", "ref": row["group_id"], "location": ", ".join(row.get("refs", [])[:3])}}
            for row in rows if row.get("decision") == "SPEC_GAP"]


def build_report(items: Sequence[Mapping[str, Any]], *, run_id: str, attempt_id: str, sources_of: Mapping[str, Sequence[str]] | None = None) -> dict[str, Any]:
    """Join items by requirement and normalized question; ``sources_of`` maps a CREQ to its SREQs so both spellings meet."""
    joined: dict[tuple, dict[str, Any]] = {}

    def requirements(ids: Sequence[str]) -> tuple[str, ...]:
        mapped = set()
        for identifier in ids:
            mapped.update((sources_of or {}).get(identifier) or [identifier])
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
    # Near-duplicates of one requirement (the same question about another field, say) join the first of
    # them in sorted order when their words mostly coincide; the joined wording stays in ``also_asked``.
    clusters: list[tuple[tuple, dict[str, Any]]] = []
    for key in sorted(joined):
        row = joined[key]
        home = next((cluster for cluster in clusters if cluster[0][0] == key[0] and similarity(cluster[0][1], key[1]) >= SIMILAR), None)
        if home is None:
            clusters.append((key, {**row, "also_asked": []}))
            continue
        target = home[1]
        target["missing"] = target["missing"] or row["missing"]
        target["blocks"] = target["blocks"] or row["blocks"]
        target["also_asked"].append(row["question"])
        target["sources"].extend(source for source in row["sources"] if source not in target["sources"])
    rows = []
    for key, row in clusters:
        row["sources"].sort(key=lambda source: (_SOURCE_ORDER.get(source["kind"], 9), source["ref"]))
        row["item_id"] = "AQ-" + hashlib.sha256(json.dumps([list(key[0]), key[1]], ensure_ascii=False).encode("utf-8")).hexdigest()[:10].upper()
        rows.append({name: row[name] for name in ("item_id", "requirement_ids", "question", "also_asked", "missing", "blocks", "sources")})
    return {"schema_version": "1.0.0", "run_id": run_id, "attempt_id": attempt_id, "items": rows,
            "counts": {kind: sum(any(source["kind"] == kind for source in row["sources"]) for row in rows) for kind in _SOURCE_ORDER}}


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = ["# Вопросы аналитикам", "", f"Прогон `{report['run_id']}`, попытка `{report['attempt_id']}`. Вопросов: {len(report['items'])}.", ""]
    if not report["items"]:
        lines += ["Вопросов нет.", ""]
        return "\n".join(lines)
    names = {"context-marker": "разметка требований", "tc-reviewer": "ревью кейсов", "autotest-reviewer": "ревью автотестов", "mutation-triage": "разбор мутантов"}
    for row in report["items"]:
        lines.append(f"## {row['item_id']}: {', '.join(row['requirement_ids'])}")
        lines.append("")
        lines.append(f"**Вопрос.** {row['question']}")
        for variant in row.get("also_asked") or []:
            lines.append(f"**Тот же вопрос иначе.** {variant}")
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
