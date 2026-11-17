"""One document by sections, one request: where the runs stand, and one Zephyr CSV of them.

``python -m tools.assemble_sections --project <abs> --doc <path>`` reads the runs of the project (their journals) and says, for each section the document is told
apart by (the sections ``ready`` names), whether a run has passed it (closed, its set published and its CSV the one the receipt of the publication stands for),
has no CSV (``UNREVIEWED``, rejected, not closed — with the code) or was not started.  Nothing is written.
With ``--write`` — or with ``--run <id>`` (once for each run, in the order of the CSV) — the CSVs of the runs are put into one: one header, the records of every
run in order, byte for byte as published.  It lies, with the summary (numbers, codes, run ids and the sections as the scans recorded them — no text of a case or
of a requirement), in ``<last run>/assembled/``.  The files of the runs never change.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from tools import output_guard
from tools.fixes import fix_for
from tools.json_cli import JsonArgumentParser
from tools.run_lock import JournalRefusal

_BOM = b"\xef\xbb\xbf"
_HINTS = {
    "ASSEMBLE_ARGUMENTS": "--doc <документ> (где мы; с --write — сборка по разделам документа) или --run <id> … --write (сборка названных прогонов)",
    "ASSEMBLE_DOC": "--doc — Markdown-файл внутри проекта, обычный файл в UTF-8",
    "ASSEMBLE_SECTIONS_MISSING": "разделы без прогона: проведи по ним прогоны (scan --docs путь#Раздел …) и повтори; собрать только то, что есть, — явным списком --run",
    "ASSEMBLE_NOTHING": "ни у одного прогона нет CSV для Zephyr: сводка называет причину по каждому",
    "ASSEMBLE_CSV_MISMATCH": "CSV прогона не тот, что опубликован, или шапки прогонов разные: ничего не правь руками; повтори `submit --stage tc-reviewer:canonical` прогона, а если прогон испорчен — проведи его заново",
    "ASSEMBLE_CONFLICT": "в <прогон>/assembled/ уже лежит другой файл с таким именем: переименуй или удали его, если он больше не нужен",
    "ASSEMBLE_RUN": "прогон не читается (занят другой командой или повреждён): подожди и повтори, id берётся из вывода scan",
    "ASSEMBLE_RUN_UNREADABLE": "часть прогонов проекта не читается (заняты или повреждены): раздел, который такой прогон покрывал, нельзя считать не начатым; подожди и повтори, повреждённый прогон проведи заново",
}


class AssembleRefused(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.hint = code, _HINTS[code]
        super().__init__(f"{code}: {detail or self.hint}")


def _numbers(root: Path) -> dict[str, Any]:
    """The few numbers of ``report.json`` the summary keeps — each by its type and its name, never a line of text."""
    try:
        report = json.loads(output_guard.input_file(root / "report.json", root).read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}
    found: dict[str, Any] = {}
    acceptance = report.get("acceptance") if isinstance(report, dict) else None
    if isinstance(acceptance, dict):
        outcome = acceptance.get("outcome")
        found.update({key: acceptance[key] for key in ("generated", "accepted") if type(acceptance.get(key)) is int})
        found.update({"outcome": outcome} if isinstance(outcome, str) and re.fullmatch(r"[A-Z_]{1,40}", outcome) else {})
    canaries = report.get("review_canaries") if isinstance(report, dict) else None
    if isinstance(canaries, dict) and type(canaries.get("seeded")) is int and type(canaries.get("caught")) is int:
        found["canaries"] = {"seeded": canaries["seeded"], "caught": canaries["caught"],
                             "missed_classes": [item for item in canaries.get("missed_classes") or [] if isinstance(item, str) and re.fullmatch(r"[a-z_]{1,40}", item)]}
    return found


def _facts(project: Path, run_id: str) -> dict[str, Any]:
    """What one run says by its journal: its ``--docs``, the CSV of its accepted set — read where the publication was and compared with what its receipt stands for — or why it has none."""
    from tools import pilot_state as pilot
    from tools.orchestrate_test_case_revision import published_csv
    from tools.run_pipeline import _read_run_coordinate

    try:
        root, _run, state = _read_run_coordinate(project, run_id)
        docs = [str(item) for item in (pilot.read_run(root)["manifest"].get("scan_request") or {}).get("docs") or []]
    except JournalRefusal:  # a busy run or a damaged journal says so, by its own code
        raise
    except Exception:  # noqa: BLE001 - any other run that cannot be read is one answer
        raise AssembleRefused("ASSEMBLE_RUN", run_id) from None
    fact: dict[str, Any] = {"run_id": run_id, "docs": docs, "started": str(state["events"][0]["observed_at"]), "csv": None, "code": None, "titles": []}
    if not state["attempts"] or state["attempts"][-1]["state"] != "TERMINAL":
        return {**fact, "code": "NOT_CLOSED"}
    attempt = state["attempts"][-1]["attempt_id"]
    try:
        effective = pilot.read_effective_canonical_if_present(root, attempt)
        result = pilot.read_terminal_result(root, attempt)
    except (OSError, ValueError, KeyError):
        return {**fact, "code": "RUN_DAMAGED"}
    if not effective:
        return {**fact, "code": str(result.get("reason_code") or result.get("completion") or "NO_ACCEPTED_SET"), **_numbers(root)}
    receipt, document = effective["effective_bundle_receipt"], effective["document"]
    try:  # the set is the one the journal published; its CSV is the file of the publication, and must be what the set and its tail of the human's cases build and the receipt names
        data = output_guard.input_file(root / "published" / Path(str(receipt["csv_path"])).name, root).read_bytes()
        valid = data == published_csv(root, attempt, document, receipt["csv_profile"]) and "sha256:" + hashlib.sha256(data).hexdigest() == receipt["csv_sha256"]
    except Exception:  # noqa: BLE001 - a missing, linked or historical-profile bundle is a CSV that does not stand
        valid = False
    if not valid:
        return {**fact, "code": "CSV_MISMATCH"}
    return {**fact, "csv": data, "titles": [" ".join(str(case["title"]).split()).casefold() for case in document["test_cases"]], **_numbers(root)}


def _leaves(project: Path, doc: str) -> tuple[str, list[tuple[str | None, int, int, int]]]:
    """The document's path in the project and the sections ``ready`` tells it apart by: ``(heading or None for the whole file, first line, last line, numbers)``."""
    from tools.build_context import REVIEW_PER_NUMBER, REVIEW_SOFT, counted_labels
    from tools.doc_sections import find_section, split_sections

    try:
        path = output_guard.input_file(doc, project, base=project)
        text = path.read_bytes().decode("utf-8")
    except (OSError, ValueError):
        raise AssembleRefused("ASSEMBLE_DOC", doc) from None
    lines = sorted(line for line, _label in counted_labels(text))
    found = split_sections(text, lines, REVIEW_SOFT // REVIEW_PER_NUMBER) if len(lines) > REVIEW_SOFT // REVIEW_PER_NUMBER else None
    rel = path.relative_to(project.resolve()).as_posix()
    if found is None:
        return rel, [(None, 1, len(text.splitlines()), len(lines))]
    return rel, [(title, (section := find_section(text, str(title))).first, section.last, number) for title, number in found]


def _covers(project: Path, fact: Mapping[str, Any], rel: str, text: str, first: int, last: int) -> bool:
    """Whether a run's ``--docs`` named the file whole or a section that holds the lines *first*–*last*."""
    from tools.doc_sections import SectionMissing, find_section, resolve_spec
    from tools.run_pipeline import relative_to_project

    for doc in fact["docs"]:
        try:
            path, heading = resolve_spec(project, doc)
            if relative_to_project(project.resolve(), path) != rel:
                continue
        except (OSError, ValueError):
            continue  # a path of the old run that no longer resolves in the project covers nothing
        if heading is None:
            return True
        try:
            section = find_section(text, heading)
        except SectionMissing:
            continue  # the document has changed since: that run names a section it no longer has
        if section.first <= first and last <= section.last:
            return True
    return False


def _state_of(fact: Mapping[str, Any]) -> str:
    return "passed" if fact["csv"] is not None else "no_csv"


def where(project: Path, doc: str) -> dict[str, Any]:
    """Each section of the document: the latest run that covers it (a run with a CSV first) and its state."""
    project = project.resolve()
    rel, leaves = _leaves(project, doc)
    text = output_guard.input_file(doc, project, base=project).read_bytes().decode("utf-8")
    runs, unreadable = [], []
    for item in sorted((project / ".pilot-runs").iterdir()) if (project / ".pilot-runs").is_dir() else []:
        if re.fullmatch(r"[0-9a-f]{32}", item.name) and item.is_dir():
            try:
                runs.append(_facts(project, item.name))
            except (AssembleRefused, JournalRefusal) as error:  # a run that cannot be read now (busy, damaged) may have been the answer to a section: said, with its code
                unreadable.append({"run_id": item.name, "code": str(getattr(error, "code", "ASSEMBLE_RUN"))})
    runs.sort(key=lambda fact: fact["started"])
    sections = []
    for title, first, last, number in leaves:
        covering = [fact for fact in runs if _covers(project, fact, rel, text, first, last)]
        best = next((fact for fact in reversed(covering) if fact["csv"] is not None), covering[-1] if covering else None)
        sections.append({"section": title, "numbers": number, "state": _state_of(best) if best else "not_started", "run_id": best["run_id"] if best else None,
                         "code": best["code"] if best else None})
    return {"document": rel, "sections": sections, "unreadable": unreadable, "complete": all(row["state"] != "not_started" for row in sections) and not unreadable}


def assemble(project: Path, run_ids: list[str], *, doc: str | None = None) -> dict[str, Any]:
    """The one CSV of the runs (the runs of a document's sections when *run_ids* is empty), written below the last run, and its summary."""
    from tools.confined_output import atomic_write_confined_bytes_at_root
    from tools.run_pipeline import _read_run_coordinate

    project = project.resolve()
    where_found = where(project, doc) if doc else None
    if where_found is not None and not run_ids:
        if where_found["unreadable"]:
            raise AssembleRefused("ASSEMBLE_RUN_UNREADABLE", ", ".join(f"{row['run_id']} ({row['code']})" for row in where_found["unreadable"]))
        missing = [row["section"] for row in where_found["sections"] if row["state"] == "not_started"]
        if missing:
            raise AssembleRefused("ASSEMBLE_SECTIONS_MISSING", f"{len(missing)} из {len(where_found['sections'])}: " + "; ".join(str(title) for title in missing))
        run_ids = list(dict.fromkeys(row["run_id"] for row in where_found["sections"]))
    run_ids = list(dict.fromkeys(run_ids))  # a run named twice is one run, in the place of its first mention
    facts = [_facts(project, run_id) for run_id in run_ids]
    if any(fact["code"] == "CSV_MISMATCH" for fact in facts):
        raise AssembleRefused("ASSEMBLE_CSV_MISMATCH", ", ".join(fact["run_id"] for fact in facts if fact["code"] == "CSV_MISMATCH"))
    included = [fact for fact in facts if fact["csv"] is not None]
    if not included:
        raise AssembleRefused("ASSEMBLE_NOTHING")
    headers, parts = set(), []
    for fact in included:
        head, _, rest = bytes(fact["csv"]).removeprefix(_BOM).decode("utf-8").partition("\r\n")
        headers.add(head)
        parts.append(rest if rest.endswith("\r\n") or not rest else rest + "\r\n")
    if len(headers) != 1:
        raise AssembleRefused("ASSEMBLE_CSV_MISMATCH", "шапки прогонов разные: " + ", ".join(fact["run_id"] for fact in included))
    data = _BOM + (headers.pop() + "\r\n" + "".join(parts)).encode("utf-8")
    seen: set[str] = set()
    runs = []
    for fact in facts:
        repeats = sum(title in seen for title in fact["titles"])
        seen.update(fact["titles"])
        runs.append({"run_id": fact["run_id"], "docs": fact["docs"], "csv": "none" if fact["csv"] is None else "included", "code": fact["code"], "cases": len(fact["titles"]),
                     "title_repeats": repeats, **{key: fact[key] for key in ("outcome", "generated", "accepted", "canaries") if key in fact}})
    from tools.skillsrc_manifest import load_skillsrc

    manifest = project / ".skillsrc"
    template = ((load_skillsrc(manifest).get("projection") or {}).get("zephyr") or {}).get("template") if manifest.is_file() else None
    stem = "sections-" + hashlib.sha256("\n".join(f"{fact['run_id']}:{hashlib.sha256(bytes(fact['csv'])).hexdigest()}" for fact in included).encode("utf-8")).hexdigest()[:12]
    summary = {"schema": "sections-assembly/1", "document": where_found and where_found["document"], "csv_sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "cases": sum(row["cases"] for row in runs if row["csv"] == "included"),
               "title_repeats": sum(row["title_repeats"] for row in runs), "runs": runs, "template": "skipped" if template else None}
    root = _read_run_coordinate(project, run_ids[-1])[0]
    for name, payload in ((f"{stem}.zephyr-scale.csv", data), (f"{stem}.summary.json", json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8") + b"\n")):
        target = root / "assembled" / name
        if target.is_symlink() or target.exists():
            if target.is_symlink() or not target.is_file() or output_guard.input_file(target, root).read_bytes() != payload:
                raise AssembleRefused("ASSEMBLE_CONFLICT", str(target))
            continue
        atomic_write_confined_bytes_at_root(project, root, target, payload)
    return {"status": "ok", "output": str(root / "assembled" / f"{stem}.zephyr-scale.csv"), "summary": str(root / "assembled" / f"{stem}.summary.json"), **{key: summary[key] for key in ("cases", "title_repeats")},
            "runs": [{key: row[key] for key in ("run_id", "csv", "code")} for row in runs]}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="Where the runs of a document's sections stand, and one Zephyr CSV of them.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", help="the document told apart by sections: where the runs stand; with --write, the CSV of the runs of its sections")
    parser.add_argument("--run", action="append", default=[], help="a run id, in the order of the CSV (with --write)")
    parser.add_argument("--write", action="store_true", help="write the one CSV and its summary below the last run")
    args = parser.parse_args(argv)
    try:
        if bool(args.doc) == bool(args.run) or (args.run and not args.write):
            raise AssembleRefused("ASSEMBLE_ARGUMENTS")
        result = assemble(Path(args.project), args.run, doc=args.doc) if args.write else where(Path(args.project), args.doc)
    except AssembleRefused as error:
        print(json.dumps({"status": "error", "code": error.code, "hint": error.hint, "detail": str(error)}, ensure_ascii=False))
        return 2
    except Exception as error:  # noqa: BLE001 - one JSON, code 2
        code = getattr(error, "code", None)
        code = code if isinstance(code, str) and code else "ASSEMBLE_FAILED"
        print(json.dumps({"status": "error", "code": code, "hint": str(error) or type(error).__name__, **({"fix": fix_for(code)} if fix_for(code) else {})}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
