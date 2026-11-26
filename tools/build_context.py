"""Build a 5.0.0 context-marker envelope from authorized docs and source handles."""

from __future__ import annotations

import bisect
import difflib
import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from tools.json_cli import JsonArgumentParser, emit_error
from tools.openspec_scope import SCOPE_HEADS, SHOWN
from tools.openspec_scope import lines as scope_lines
from tools.schema_validation import schema_diagnostics

_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = _ROOT / "schemas" / "context-marker-output.schema.json"
_DOCS_MAX_FILE = 256 * 1024
_DOCS_MAX_TOTAL = 1024 * 1024

REQ_ID_RE = re.compile(r"\b(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b", re.I)
HEADING_RE = re.compile(r"^(?:#{1,6}\s+[^\n]+|(?:[-*]\s+)?(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b[^\n]*)", re.M | re.I)
API_PATH_RE = re.compile(r"""['"](/api/[^'"]+)['"]""")
FLASK_ROUTE_RE = re.compile(
    r"""@(?:app|router|bp|api)\.(?:get|post|put|patch|delete|route)\(\s*['"]([^'"]+)['"]""",
    re.I,
)


def _norm(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().strip(".:")


def _compare_key(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


_BUILTIN_ID = r"(?:REQ|AC|US|FR|BR|TR|ТР|ПС)[-_]\d+\b"


def compile_id_pattern(pattern: str | None) -> re.Pattern[str] | None:
    """The project's explicit requirement ID (``.skillsrc`` ``requirements.id_pattern``), or None.

    The pattern marks an ID at the start of a line, exactly like the built-in ``REQ-``/``AC-`` IDs;
    a pattern that does not compile or matches the empty string is rejected.
    """
    if pattern is None:
        return None
    if not isinstance(pattern, str) or not pattern or len(pattern) > 200:
        raise ValueError("requirements.id_pattern must be a non-empty string of at most 200 characters")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"requirements.id_pattern does not compile: {error}") from error
    if compiled.fullmatch("") is not None or compiled.match("") is not None:
        raise ValueError("requirements.id_pattern must not match the empty string")
    return compiled


def _heading_regex(id_re: re.Pattern[str] | None) -> re.Pattern[str]:
    if id_re is None:
        return HEADING_RE
    return re.compile(r"^(?:#{1,6}\s+[^\n]+|(?:[-*]\s+)?(?:" + _BUILTIN_ID + r"|(?-i:" + id_re.pattern + r"))[^\n]*)", re.M | re.I)


def extract_inventory(analytics: str, id_pattern: str | re.Pattern[str] | None = None) -> list[dict[str, Any]]:
    """Partition authorized requirement prose without dropping unlabelled content.

    Semantic classification belongs to context-marker/reviewer, not heading heuristics.  With ``id_pattern``
    (``.skillsrc`` ``requirements.id_pattern``) a line starting with the project's ID opens a requirement
    section like a built-in ``REQ-`` ID does; without it the split is the built-in one.
    """
    text = analytics or ""
    if not text.strip():
        return []
    id_re = id_pattern if isinstance(id_pattern, re.Pattern) else compile_id_pattern(id_pattern)
    found: list[dict[str, Any]] = []
    boundaries = sorted({0, len(text), *(match.start() for match in _heading_regex(id_re).finditer(text))})
    for start, stop in zip(boundaries, boundaries[1:]):
        section = text[start:stop].strip()
        if section:
            title = _norm(section.splitlines()[0].lstrip("# "))
            requirement = REQ_ID_RE.match(title) or (id_re is not None and id_re.match(title))
            found.append({"kind": "requirement" if requirement else "flow", "title": title, "text": section})
    return found


_LABEL = re.compile(r"^ {0,3}(?:[-*]\s+)?(?:([^\W\d_]{1,4}[-_]\d{1,5}(?:\.\d{1,3}){0,2})[.):]?|([^\W\d_]{1,3}\d{1,3})[.):]|(\d{1,3}(?:\.\d{1,3}){1,3})\.?)(?=[ \t])", re.M)  # FR-01 REQ-1001 BR-3.1 FR-12.4. ТР-3: · D01. U12) FR3: · 1.2.3. 2.10; not a heading, not «1.», not a date
LABELS_MIN = 8


def document_labels(text: str) -> list[str]:
    """The labels (numbers of statements) at the start of the lines of a plain document, in the order of its lines."""
    return [match.group(1) or match.group(2) or match.group(3) for match in _LABEL.finditer(text)]


_NAME = re.compile(r"[^\W\d_]+")


def counted_labels(text: str) -> list[tuple[int, str]]:
    """``(line, label)`` of the labels that stand for requirements.  A label that another label of the document continues (``1.1`` before ``1.1.1``) is the
    number of a section; a letter prefix with one distinct label in the document (``UTF-8``, ``SHA-256``) is a name, not a numbering."""
    ends = [match.start() for match in re.finditer("\n", text)]
    found = [(bisect.bisect_left(ends, match.start()) + 1, match.group(1) or match.group(2) or match.group(3)) for match in _LABEL.finditer(text)]
    distinct = {label for _line, label in found}
    parents = {".".join(parts[:cut]) for label in distinct for parts in [label.split(".")] for cut in range(1, len(parts))}
    names = Counter(match.group() for label in distinct if (match := _NAME.match(label)))
    return [(line, label) for line, label in found if label not in parents and not ((match := _NAME.match(label)) and names[match.group()] == 1)]


def document_label_counts(documents: dict[str, str], project: Path) -> dict[str, list[str]]:
    """path → the distinct labels of each plain document (OpenSpec paths and a project with ``requirements.id_pattern`` are no case), ``{}`` where they are fewer than LABELS_MIN."""
    if _project_id_pattern(project) is not None:
        return {}
    found = {path: list(dict.fromkeys(label for _line, label in counted_labels(text))) for path, text in documents.items() if not _OPENSPEC_PATH.fullmatch(path) and not path.startswith("openspec/")}
    found = {path: labels for path, labels in found.items() if labels}
    return found if sum(len(labels) for labels in found.values()) >= LABELS_MIN else {}


def label_refusal(requirements: int, found: dict[str, list[str]]) -> str | None:
    """Why an answer of ``requirements`` rows cannot stand for documents with so many labels: fewer than half as many requirements — the sections of the document were taken for them.
    Only paths, labels and numbers are said."""
    total = sum(len(labels) for labels in found.values())
    if not found or requirements * 2 >= total:
        return None
    where = ", ".join(f"{path}: {len(labels)} ({', '.join(labels[:4])}…)" for path, labels in sorted(found.items()))
    return (f"в документе меток {total} ({where[:300]}), в ответе требований {requirements} — меньше половины; fix: требование — каждое утверждение со своим номером "
            "или меткой, в порядке строк документа (роль разметки, шаг 2), заголовки разделов не требования; если эти метки — не номера требований (нумерованный "
            "список, приложение), проверку снимает человек: requirements.id_pattern в .skillsrc, затем новый scan")


def label_warning(requirements: int, found: dict[str, list[str]]) -> str | None:
    """The warning for fewer requirements than labels (not fewer than half: that is the refusal) — paths and numbers only."""
    total = sum(len(labels) for labels in found.values())
    if not found or requirements >= total:
        return None
    return f"ВНИМАНИЕ: в документе {', '.join(sorted(found))[:300]} номеров {total}, требований в разметке {requirements} — сверьте, что ни одно требование не пропущено и не склеено"


# Measures of the live runs of 16–17.11 (view of the review, B per number of the document; control errors caught of 3):
#   w3-big 967 883 / 128 = 7 562 (no review) · w3-sec 324 685 / 39 = 8 325 (1) · w3-mm 190 851 / 35 = 5 453 (3) · w1-pq1 228 079 / 26 = 8 772 (3) · w1-rw 246 574 / 34 = 7 252 (3)
#   pq1-v06a 252 197 / 33 = 7 642 (2) · pq1-v06a-after 191 442 / 27 = 7 090 (2) · w2-upd 187 557 / 44 = 4 263 (2) · w2-micro 148 674 / 24 = 6 195 (3) · w2-osb 114 751 / 16 = 7 172 (3)
#   input of the autotest review per number: pq1 16 497 · rw 10 442 · micro 10 000 · osb 11 155 · pq1-v06a 12 366 · w3-big (computed) 10 514
REVIEW_PER_NUMBER = 8_000  # B of the review view per number: up to 8.8 KB on the live runs (w3-sec 8.3); the hard bound, so 8 000 and not the mean of 6.5 KB
AUTOTEST_PER_NUMBER = 13_000  # B of the input of the autotest review per number: 10.0–16.5 KB on the live runs; with it w3-sec (39 × 13 000) is over the step, pq1/rw/micro/osb are not
IMPORT_PER_CASE = 4_500  # B of the review view per case of an imported CSV: w2-upd 187 557 / 44 cases = 4 263 (the case is the human's, not a numbered statement of 8 KB)
REVIEW_SOFT = 300_000  # the view up to 250 KB caught 3 of 3 in 6 runs of 8 and never fewer than 2; at 252 KB 2, at 325 KB 1: a warning from 300 KB (37 numbers), w3-mm (35 numbers, 280 KB) has none


NOT_DIVISIBLE = "по заголовкам документ разделить нечем"
NOT_DIVISIBLE_FIX = (NOT_DIVISIBLE + ": разделите требования по файлам (не больше 37 на файл, `--docs` — файл на прогон, прогоны по очереди) или попросите человека сузить документ; "
                     "этот прогон дальше не вести — ревью такого набора не состоится")
OPENSPEC_ADVICE = "назовите меньше областей: --docs openspec/specs/<область> по одной-две на прогон (прогоны по очереди одним запросом; где мы и сборка — tools.assemble_sections)"


def large_document(project: Path, documents: Mapping[str, str], named: Mapping[str, Any], automation: bool, requirements: int | None = None,
                   openspec: bool = False) -> tuple[bool, str, str | None] | None:
    """The numbers of the plain documents (*documents*: shown path → text; *named*: path → the headings ``--docs`` names, none for the whole file) against what one review holds,
    before any model: None where the set fits, else ``(hard, estimate, advice)`` — hard: the estimate is over the step budget (the plan refuses), else over the size that was
    checked well (a warning); the advice (None: no headings tell the numbers apart) names the runs by sections, taken together up to REVIEW_SOFT; ``say`` joins it to the estimate.
    *requirements*: the plan knows the requirements of the marker — the exact number, whatever the document is (without numbers, OpenSpec, ``id_pattern``): the estimate is theirs,
    the sections still come from the numbers of the document where it has them; *openspec*: the advice is fewer areas."""
    from tools.doc_sections import SectionMissing, find_section, split_sections
    from tools.step_size import role_files, step_budget

    if requirements is None and _project_id_pattern(project) is not None:
        return None
    marks = {}
    for path, text in documents.items():
        if _OPENSPEC_PATH.fullmatch(path) or path.startswith("openspec/") or _project_id_pattern(project) is not None:
            continue
        try:
            within = [find_section(text, heading, path) for heading in named.get(path) or ()]
        except SectionMissing:
            continue  # the docs row of ready says it
        lines = sorted(line for line, _label in counted_labels(text) if not within or any(item.first <= line <= item.last for item in within))
        if lines:
            marks[path] = (text, lines, within)
    total = requirements if requirements is not None else sum(len(lines) for _text, lines, _within in marks.values())
    budget = step_budget(project)
    room = budget - sum(item.stat().st_size for item in role_files("tc-reviewer"))
    pairs = [(total * REVIEW_PER_NUMBER, room)]
    if automation:
        pairs.append((total * AUTOTEST_PER_NUMBER, budget - sum(item.stat().st_size for item in role_files("autotest-reviewer"))))
    over = next(((size, limit) for size, limit in pairs if size > limit), None)
    if over is None and total * REVIEW_PER_NUMBER <= REVIEW_SOFT:
        return None
    cap = min(REVIEW_SOFT // REVIEW_PER_NUMBER, *(limit // (REVIEW_PER_NUMBER if index == 0 else AUTOTEST_PER_NUMBER) for index, (_size, limit) in enumerate(pairs)))
    leaves: list[tuple[str, str | None, int]] | None = []
    for path, (text, lines, within) in marks.items():
        found = split_sections(text, lines, cap, within) if within or len(lines) > cap else [(None, len(lines))]
        if found is None:
            leaves = None
            break
        leaves += [(path, title, number) for title, number in found]
    runs: list[list[tuple[str, str | None, int]]] = []
    for leaf in leaves or []:
        if runs and sum(item[2] for item in runs[-1]) + leaf[2] <= cap:
            runs[-1].append(leaf)
        else:
            runs.append([leaf])
    head = (f"оценка вида {'ревью' if over == pairs[0] else 'ревью автотестов'} {over[0]} Б при {total} номерах больше бюджета шага {over[1]} Б: ревью не состоится" if over else
            f"оценка вида ревью {total * REVIEW_PER_NUMBER} Б при {total} номерах: такой объём ревью на прошлых прогонах проверялся слабее (190 КБ — контрольные ошибки 3 из 3, 325 КБ — 1 из 3)")
    if len(runs) < 2:
        return bool(over), head, OPENSPEC_ADVICE if openspec else None
    from tools.run_pipeline import printed_or

    def argument(path: str, title: str | None) -> str:  # a heading the shell would read otherwise (a quote…) is not printed: the advice says so
        return "--docs " + printed_or(f"{path}#{title}" if title else path, f'"{path}#…" (заголовок «{title}» с символом, который оболочка читает иначе: набери аргумент сам)')

    listed = " | ".join(f"прогон {index} ({sum(item[2] for item in run)} ном.): " + " ".join(argument(item[0], item[1]) for item in run) for index, run in enumerate(runs, start=1))
    return bool(over), head, "по разделам, одним запросом, прогоны по очереди (каждый — от scan до report; где мы и сборка — tools.assemble_sections): " + listed


def bloated_marker(rows: int, found: Mapping[str, list[str]]) -> str | None:
    """The words for a marker with 1.2 times the requirements the document has numbered statements (``found``: ``document_label_counts``) and more: paragraphs
    without a number were taken for requirements, and the document is not too large; None where it is not so."""
    numbers = sum(len(labels) for labels in found.values())
    if not found or rows * 5 < numbers * 6:
        return None
    return f"разметка дала {rows} требований при {numbers} номерах документа (по номерам оценка вида ревью влезает): лишние ряды — со строк без номера (вводная часть, данные, условия работы)"


def _ranges(numbers: list[int], shown: int = 12) -> str:
    runs: list[list[int]] = []
    for number in numbers:
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    texts = [str(first) if first == last else f"{first}–{last}" for first, last in runs]
    return ", ".join(texts[:shown]) + (f" и ещё {len(texts) - shown}" if len(texts) > shown else "") if texts else "нет"


def numbering_view(documents: Mapping[str, str], project: Path) -> str | None:
    """What the step of the marker reads beside the documents, built by code: of each plain document with numbers (``document_label_counts``: LABELS_MIN, no OpenSpec,
    no ``id_pattern``) the numbered statements and the lines without a number — context, not requirements (a statement runs from its label to the next label or heading)."""
    found = document_label_counts(dict(documents), project)
    if not found:
        return None
    rows = ["Номера документа, как их читает код: требование — пункт с номером; строки без номера — контекст, не требования.", ""]
    for path in sorted(found):
        text = documents[path]
        lines = text.split("\n")
        starts = sorted({text.count("\n", 0, match.start()) + 1 for match in _LABEL.finditer(text)})
        heads = {number for number, line in enumerate(lines, start=1) if line.startswith("#")}
        stops = sorted({*starts, *heads})
        spans = [(start, next((stop for stop in stops if stop > start), len(lines) + 1) - 1) for start in starts]
        outside = [number for number, line in enumerate(lines, start=1) if line.strip() and number not in heads and not any(first <= number <= last for first, last in spans)]
        labels = found[path]
        rows.append(f"- {path}: пунктов с номерами {len(labels)}: {labels[0]}…{labels[-1]} (строки {starts[0]}–{spans[-1][1]}); строки без номера: {_ranges(outside)}")
    return "\n".join(rows) + "\n"


def csv_import_size(cases: int) -> tuple[bool, str, str] | None:
    """An import of cases without documents: its requirements are the cases of the CSV (4.3 KB of the view a case on the live run, not the 8 KB of a numbered statement), so only the warning —
    the step budget stays the guard."""
    if cases * IMPORT_PER_CASE <= REVIEW_SOFT:
        return None
    return False, f"при {cases} кейсах CSV ревью такого объёма на прошлых прогонах проверялось слабее", f"выгрузите из Zephyr меньше кейсов за раз (до {REVIEW_SOFT // IMPORT_PER_CASE} на прогон)"


def say(head: str, advice: str | None) -> str:
    return f"{head}; {advice or NOT_DIVISIBLE + ' — прогон идёт как есть'}"


def _project_id_pattern(project: Path) -> str | None:
    """``requirements.id_pattern`` of the project's ``.skillsrc``; None when absent or unreadable."""
    manifest = project / ".skillsrc"
    try:
        if not manifest.is_file():
            return None
        from tools.skillsrc_manifest import load_skillsrc

        value = (load_skillsrc(manifest).get("requirements") or {}).get("id_pattern")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _source_requirement_id(index: int) -> str:
    """Return a controller-owned sequential source ID after stable normalization."""
    return f"SREQ-{index:04d}"


def _confined(project: Path, candidate: Path) -> Path | None:
    from tools.output_guard import local_path_text

    if not local_path_text(str(candidate)):  # a share or a device: refused by its text, never resolved
        return None
    try:
        resolved = candidate.resolve()
        resolved.relative_to(project.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def extract_handles(text: str) -> list[str]:
    paths: list[str] = []
    for match in API_PATH_RE.findall(text):
        paths.append(match.split("?")[0])
    for match in FLASK_ROUTE_RE.findall(text):
        paths.append(match if match.startswith("/") else "/" + match)
    seen: set[str] = set()
    ordered: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


class RequirementConflict(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_OPENSPEC_PATH = re.compile(r"^openspec/(?:(specs)/(.+)|changes/([^/]+)/specs/(.+))/spec\.md$")
OPENSPEC_PATH = _OPENSPEC_PATH  # a spec or a delta of OpenSpec (public: the other modules of the package do not take the private name)
_OPENSPEC_HEADING = re.compile(r"^(##\s+[^\n]+|### Requirement:\s*[^\n]+|#### Scenario:\s*[^\n]+)$", re.M)


_OPENSPEC_CHANGE = re.compile(r"^openspec/changes/(?!archive/)([^/]+)/")


def openspec_selected_changes(docs: list[str]) -> set[str]:
    """The live changes the task documents name: any file under ``openspec/changes/<name>/`` selects ``<name>``."""
    return {match.group(1) for doc in docs if (match := _OPENSPEC_CHANGE.match(doc.replace("\\", "/")))}


def openspec_unselected(paths: list[str], docs: list[str]) -> list[str]:
    """The ``paths`` of other live changes and of the archive — no part of the selection the task documents make."""
    selected = openspec_selected_changes(docs)
    named = set(docs)
    return [path for path in paths if path not in named and path.startswith("openspec/changes/")
            and (path.startswith("openspec/changes/archive/") or (m := _OPENSPEC_CHANGE.match(path)) and m.group(1) not in selected)]


_OPENSPEC_CONTEXT = re.compile(r"^openspec/(?:changes/(?!archive/)[^/]+/)?[^/]+$")
_OPENSPEC_FOLDER = re.compile(r"^openspec/(?:specs(?:/.+)?|changes/(?!archive$)([^/]+)|changes/archive/[^/]+)$")


def openspec_context_only(path: str) -> bool:
    """A file of the first level of a live change (proposal, design, tasks) or of ``openspec/`` (project): context, never requirement rows.
    Any other file under ``openspec/`` is an ordinary document."""
    return bool(_OPENSPEC_CONTEXT.fullmatch(path))


def openspec_dir_files(folder: str, found: list[str]) -> list[str] | None:
    """The ``found`` files a folder of ``--docs`` stands for: the ``spec.md`` of ``openspec/specs[/<area>]``, the deltas (and the
    proposal, design, tasks as context) of ``openspec/changes/<name>``, all the deltas of ``openspec/changes/archive/<name>``.
    ``None``: the folder is none of these."""
    match = _OPENSPEC_FOLDER.fullmatch(folder)
    if not match:
        return None
    context = {f"{folder}/{name}.md" for name in ("proposal", "design", "tasks")} if match.group(1) else set()
    return [path for path in found if path in context or path.endswith("/spec.md") and (_OPENSPEC_PATH.fullmatch(path) or path.startswith(folder + "/specs/"))]


def docs_over_limit(sizes: list[int], named: tuple[int, ...] | list[int] = ()) -> bool:
    """The ``sizes`` of the files of folders beyond the limit of a file (256 KiB), with the ``named`` files beyond that of all (1 MiB)."""
    return max(sizes, default=0) > _DOCS_MAX_FILE or sum(sizes) + sum(named) > _DOCS_MAX_TOTAL


def openspec_live_changes(paths: list[str]) -> set[str]:
    """The live changes that have a delta ``spec.md`` among ``paths``."""
    return {match.group(3) for path in paths if (match := _OPENSPEC_PATH.fullmatch(path)) and match.group(3)}


def openspec_unnamed_baseline(path: str, docs: list[str]) -> bool:
    """A baseline ``spec.md`` that ``--docs`` does not name: context for the model, no requirements of the run."""
    return (match := _OPENSPEC_PATH.fullmatch(path)) is not None and match.group(1) == "specs" and path not in docs


def openspec_unnamed_areas(docs: list[str], paths: list[str]) -> list[str]:
    """The baseline ``spec.md`` (of ``paths``, the inventory) of the areas any delta of the selected change (named or not) changes, that ``--docs`` does not name."""
    selected = openspec_selected_changes(docs)
    areas = {f"openspec/specs/{match.group(4)}/spec.md" for path in paths if (match := _OPENSPEC_PATH.fullmatch(path)) and match.group(3) in selected}
    return sorted(path for path in areas if path in paths and path not in docs)


def openspec_change_only(docs: list[str], paths: list[str], cases: bool = False) -> list[str]:
    """The baseline ``spec.md`` (of ``paths``) of a run of the change only: the areas of the deltas of the selected change that
    ``docs`` names, whose spec.md ``docs`` does not name — the base of the composition and context, no requirements of the run.
    Only where code writes the requirements (``docs`` OpenSpec alone, no ``--cases``; decision of 26.11); empty — the run is as before."""
    if cases or not docs or not all(doc.startswith("openspec/") for doc in docs):
        return []
    selected = openspec_selected_changes(docs)
    areas = {f"openspec/specs/{match.group(4)}/spec.md" for doc in docs if (match := _OPENSPEC_PATH.fullmatch(doc)) and match.group(3) in selected}
    return sorted(path for path in areas if path in paths and path not in docs)


_ARCHIVE_DELTA = re.compile(r"^openspec/changes/archive/([^/]+)/specs/(.+)/spec\.md$")


def openspec_partial_archives(docs: list[str], paths: list[str]) -> list[str]:
    """The archived changes (of ``paths``, the inventory) that ``docs`` names by some of their deltas but not all of them."""
    named = {match.group(1) for doc in docs if (match := _ARCHIVE_DELTA.fullmatch(doc))}
    return sorted({match.group(1) for path in paths if (match := _ARCHIVE_DELTA.fullmatch(path)) and match.group(1) in named and path not in docs})


def openspec_unnamed_archive_areas(docs: list[str], paths: list[str]) -> list[str]:
    """The baseline ``spec.md`` (of ``paths``) of the areas an archived delta of ``docs`` changes, that ``docs`` does not name."""
    areas = {f"openspec/specs/{match.group(2)}/spec.md" for doc in docs if (match := _ARCHIVE_DELTA.fullmatch(doc))}
    return sorted(path for path in areas if path in paths and path not in docs)


def _same(first: str, second: str) -> bool:
    """The same words: line ends, indents, spaces and breaks of lines do not tell texts apart."""
    return first.split() == second.split()


def _close_names(baseline: Mapping[tuple[str, str], Any], key: tuple[str, str]) -> list[str]:
    """Up to three requirements of the area of ``key`` in the specification whose names are nearest to the one the delta gave."""
    return difflib.get_close_matches(key[1], [name for capability, name in baseline if capability == key[0] and name != key[1]], n=3, cutoff=0.4)


def _close(baseline: Mapping[tuple[str, str], Any], key: tuple[str, str]) -> str:
    """``; ближайшие имена …`` of ``_close_names``."""
    names = _close_names(baseline, key)
    return f"; ближайшие имена в spec.md области: {', '.join(names)}" if names else ""


def _blocks(text: str, section: str = "") -> list[tuple[str, str]]:
    """``(name, block)`` of every «### Requirement:» of a specification (of its ``## <section>`` only, when given)."""
    headings = list(_OPENSPEC_HEADING.finditer(text))
    found, current = [], ""
    for index, heading in enumerate(headings):
        title = heading.group().strip()
        if title.startswith("## "):
            current = title[3:].strip()
        elif title.startswith("### Requirement:") and (not section or current == section):
            stop = next((item.start() for item in headings[index + 1:] if not item.group().startswith("#### Scenario:")), len(text))
            found.append((title[len("### Requirement:"):].strip(), text[heading.start():stop].strip()))
    return found


def _scenarios(block: str) -> tuple[list[str], dict[str, list[str]]]:
    """The lines of a requirement before its first scenario, and the lines of each scenario by its title."""
    head, *parts = re.split(r"^#### Scenario:[ \t]*", block, flags=re.M)
    return ([line.strip() for line in head.splitlines()[1:] if line.strip()],
            {part.splitlines()[0].strip(): [line.strip() for line in part.splitlines()[1:] if line.strip()] for part in parts})


def openspec_change_diff(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each MODIFIED of a run of the change only (an entry ``base``) as «было → стало»: the scenarios added, removed and changed
    (their lines before and after) and the changed statement; empty for any other run and for a MODIFIED the base lacks."""
    if not any(entry.get("base") for entry in entries):
        return []
    was: dict[tuple[str, str], str] = {}
    deltas: list[tuple[str, str]] = []
    for entry in entries:
        match = _OPENSPEC_PATH.fullmatch(entry["path"].replace("\\", "/"))
        if match and match.group(1) and not entry.get("context"):
            was.update({(match.group(2), name): block for name, block in _blocks(entry["content"])})
        elif match and match.group(3):
            deltas.append((match.group(4), entry["content"]))
    found = []
    for area, text in sorted(deltas):
        for name, block in _blocks(text, "MODIFIED Requirements"):
            if (area, name) not in was:
                continue
            (old_head, old), (new_head, new) = _scenarios(was[(area, name)]), _scenarios(block)
            row: dict[str, Any] = {"area": area, "name": name, "added": [title for title in new if title not in old],
                                   "removed": [title for title in old if title not in new],
                                   "changed": [{"scenario": title, "was": old[title], "now": new[title]} for title in new if title in old and old[title] != new[title]]}
            if old_head != new_head:
                row["statement"] = {"was": old_head, "now": new_head}
            found.append(row)
    return found


def _openspec_rows(entries: list[dict[str, str]], applied: Counter[str] | None = None, shown: int | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Compose only standard spec documents explicitly present in authorized inputs.  ``applied`` counts, by change, what its delta
    says and the specification already has (ADDED or MODIFIED with the same text, REMOVED gone, RENAMED under the new name).

    Change only (an entry ``base``: the spec.md of an area the run does not name): the requirements of that area the change does
    not touch are context, no rows; a MODIFIED its spec.md lacks and an ADDED it has with another text leave the run as lines
    (``openspec_scope``), not a stop.  Entries ``context`` (the other spec.md, the proposal) give the neighbours only.
    ``shown`` cuts the list of the neighbours in the line (None: all of them)."""
    applied = Counter() if applied is None else applied
    baseline: dict[tuple[str, str], dict[str, Any]] = {}
    operations: list[tuple[str, tuple[str, str], dict[str, Any]]] = []
    renames: list[tuple[tuple[str, str], str, str, str]] = []
    warnings: list[str] = []
    changes: set[str] = set()
    based: set[str] = set()  # the areas whose spec.md is among the entries, with requirements or without
    based_only: set[str] = set()  # change only: the areas whose spec.md is the base of the change, no requirements of the run
    pool: list[tuple[str, str, str]] = []  # change only: the requirements of the base the change does not touch (neighbours, never rows)
    proposal = ""
    for entry in sorted(entries, key=lambda row: row["path"]):
        path = entry["path"].replace("\\", "/")
        if entry.get("context"):
            if path.endswith("/proposal.md"):
                proposal = entry["content"]
            elif (found := _OPENSPEC_PATH.fullmatch(path)) and found.group(1):
                pool += [(found.group(2), name, block) for name, block in _blocks(entry["content"])]
            continue
        if path.startswith("openspec/changes/archive/"):
            warnings.append(f"{path} — {entry['sha256']}; historical archived context, not reapplied to current requirements")
            continue
        match = _OPENSPEC_PATH.fullmatch(path)
        if not match:
            continue
        if entry["sha256"] != "sha256:" + hashlib.sha256(entry["content"].encode("utf-8")).hexdigest():
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"source digest does not match content: {path}")
        is_baseline, capability, change, delta_capability = match.groups()
        if is_baseline:
            based.add(capability)
            if entry.get("base"):
                based_only.add(capability)
        if change:
            changes.add(change)
        capability = capability or delta_capability
        text = entry["content"]
        headings = list(_OPENSPEC_HEADING.finditer(text))
        section = "Requirements" if is_baseline else ""
        for index, heading in enumerate(headings):
            title = heading.group().strip()
            if title.startswith("## "):
                section = title[3:].strip()
                if section == "RENAMED Requirements":
                    stop = next((item.start() for item in headings[index + 1:] if item.group().startswith("## ")), len(text))
                    pairs = re.findall(r"^\s*(?:[-*+]\s+)?(FROM|TO):\s*`?### Requirement:\s*(.+?)`?\s*$", text[heading.end():stop], re.M)
                    if not pairs or len(pairs) % 2 or any(pairs[i][0] != "FROM" or pairs[i + 1][0] != "TO" for i in range(0, len(pairs), 2)):
                        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"unpaired rename: {path}")
                    for position in range(0, len(pairs), 2):
                        renames.append(((capability, pairs[position][1]), pairs[position + 1][1], path, entry["sha256"]))
                continue
            if not title.startswith("### Requirement:"):
                continue
            name = title[len("### Requirement:"):].strip()
            stop = next((item.start() for item in headings[index + 1:] if not item.group().startswith("#### Scenario:")), len(text))
            block = text[heading.start():stop].strip()
            scenarios = [(item.group()[len("#### Scenario:"):].strip(), text.count("\n", 0, item.start()) + 1) for item in headings[index + 1:] if heading.start() < item.start() < stop and item.group().startswith("#### Scenario:")]
            line = text.count("\n", 0, heading.start()) + 1
            row = {"capability": capability, "name": name, "text": block, "path": path, "digest": entry["sha256"], "line": line, "scenarios": scenarios, "rename_provenance": []}
            key = (capability, name)
            if is_baseline:
                if section != "Requirements" or key in baseline:
                    raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"duplicate or misplaced requirement: {path}:{line} — {name}")
                baseline[key] = row
            else:
                operation = section.removesuffix(" Requirements")
                if operation not in {"ADDED", "MODIFIED", "REMOVED"}:
                    raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"requirement has no delta operation: {path}:{line} — {name}")
                operations.append((operation, key, row))
    if len(changes) > 1:
        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", "authorize one selected OpenSpec change, not multiple pending changes")
    changed: dict[tuple[str, str], tuple[str, str]] = {}  # (operation, text) of the requirements of the change that give cases
    for key, new_name, path, digest in renames:
        new_key = (key[0], new_name)
        if key not in baseline and new_key in baseline:  # the specification already has it under the new name
            applied[path.split("/")[2]] += 1
            changed[new_key] = ("RENAMED", baseline[new_key]["text"])
            continue
        if key not in baseline or new_key in baseline:
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"rename does not match baseline: {path} — {key[1]} -> {new_name}{_close(baseline, key)}")
        row = baseline.pop(key)
        row["name"] = new_name
        row["text"] = re.sub(r"^### Requirement:[^\n]+", lambda _: "### Requirement: " + new_name, row["text"], count=1)
        row["rename_provenance"] = [f"{path} — RENAMED Requirement: {key[1]} -> {new_name}", f"{path} — {digest}"]
        baseline[new_key] = row
        changed[new_key] = ("RENAMED", row["text"])
    seen: set[tuple[str, str]] = set()
    disputed: list[tuple[str, str, str, str, str, int, str]] = []  # change only: what did not apply, and why (decision of 25.11)
    for operation, key, row in operations:
        where = f"{row['path']}:{row['line']} — {key[1]}"
        have = baseline.get(key)
        if key in seen:
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"repeats a requirement of this change ({operation}): {where}")
        if operation == "MODIFIED" and have is None and key[0] in based and not any(area == key[0] for area, _name in baseline):  # the spec.md of the area is prose: no name to compare
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"MODIFIED on an unmarked baseline: {where}; в spec.md области {key[0]} нет ни одного «### Requirement:»")
        if operation == "MODIFIED" and have is None and key[0] in based_only:
            names = _close_names(baseline, key)
            disputed.append((operation, *key, row["text"], row["path"], row["line"], f"в spec.md области {key[0]} нет требования с таким именем"
                             + (f"; ближайшие: {', '.join(f'«{name}»' for name in names)}" if names else "")))
            seen.add(key)
            continue
        if operation == "MODIFIED" and have is None:
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"MODIFIED is not in the baseline: {where}{_close(baseline, key)}")
        if operation == "ADDED" and have is not None and not _same(have["text"], row["text"]) and key[0] in based_only:
            disputed.append((operation, *key, row["text"], row["path"], row["line"], f"в spec.md то же требование с другим текстом: {have['path']}:{have['line']}"))
            seen.add(key)
            continue
        if operation == "ADDED" and have is not None and not _same(have["text"], row["text"]):
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"ADDED has another text in the baseline: {where}; в spec.md: {have['path']}:{have['line']}")
        seen.add(key)
        if operation == "REMOVED" and have is None:  # nothing to remove: gone already, or the name does not match — the specification cannot tell, so the line says both
            warnings.append(f"{row['path']}:{row['line']} — REMOVED: требования {key[1]} в spec.md области нет, уже удалено или имя не совпало{_close(baseline, key)}")
        elif operation != "REMOVED" and have is not None and _same(have["text"], row["text"]):  # the specification has it as the change says
            applied[row["path"].split("/")[2]] += 1
        if operation == "REMOVED":
            baseline.pop(key, None)
        else:
            if have is not None:
                row["rename_provenance"] = have["rename_provenance"]
            baseline[key] = row
            changed[key] = (operation, row["text"])
    rows = sorted((row for key, row in baseline.items() if key[0] not in based_only or key in changed), key=lambda row: (row["path"], row["line"], row["name"]))
    if based_only and not rows:  # change only: no case to write — nothing of the change applied, or it only removes
        raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", "no requirement of the change applies: " + ("; ".join(
            f"{operation} {name} ({path}:{line}) — {cause}" for operation, _area, name, _text, path, line, cause in disputed) or "the change only removes requirements"))
    for row in rows:
        if not row["scenarios"]:
            warnings.append(f"{row['path']}:{row['line']} — Requirement: {row['name']}; missing: scenario/observable outcome; blocks: complete scenario coverage; question: which conditions and result are required?")
        if len({name for name, _ in row["scenarios"]}) != len(row["scenarios"]):
            raise RequirementConflict("OPENSPEC_SOURCE_CONFLICT", f"duplicate scenario: {row['path']} — {row['name']}")
    if based_only:
        pool += [(*key, row["text"]) for key, row in baseline.items() if key[0] in based_only and key not in changed]
        warnings += scope_lines(min(changes), sorted(based_only), [(operation, *key, text) for key, (operation, text) in changed.items()],
                                disputed, pool, proposal, shown)
    return rows, warnings


def openspec_unmarked_areas(entries: list[dict[str, str]]) -> list[str]:
    """The areas a delta of ``entries`` changes whose spec.md (among ``entries``) has no «### Requirement:» — no requirement of the area comes from it, so the run has no regression of it."""
    matched = [(match, entry["content"]) for entry in entries if (match := _OPENSPEC_PATH.fullmatch(entry["path"].replace("\\", "/")))]
    bases = {match.group(2): text for match, text in matched if match.group(1)}
    return sorted({match.group(4) for match, _text in matched if match.group(3) and match.group(4) in bases and not re.search(r"^### Requirement:", bases[match.group(4)], re.M)})


def openspec_applied(entries: list[dict[str, str]]) -> tuple[dict[str, int], list[str]]:
    """By change, how many requirements its delta shows already written in the specification, and the lines of the REMOVED whose name the specification lacks
    and of the scope of a change only (whole files only); ``RequirementConflict`` when they do not compose."""
    applied: Counter[str] = Counter()
    warnings = _openspec_rows([row for row in entries if not row.get("section")], applied, SHOWN)[1]
    return dict(applied), [line for line in warnings if " — REMOVED: " in line or line.startswith(SCOPE_HEADS)]


def openspec_gone_names(entries: list[dict[str, str]]) -> tuple[list[str], int]:
    """The ``capability/name`` the delta of an update of cases touches that no requirement of the composed documents has, and how many it touches."""
    from tools.cases_scope import scope

    touched = scope(entries, [row["path"] for row in entries]).affected
    try:
        have = {(row["capability"], row["name"]) for row in _openspec_rows(entries)[0]}
    except RequirementConflict:  # the composition says what is wrong, at its own step
        return [], 0
    return [f"{capability}/{name}" for capability, name in sorted(touched - have)], len(touched)


def _openspec_marks(row: dict[str, Any]) -> list[str]:
    prefix = f"capability={row['capability']}; ### Requirement: {row['name']}"
    marks = [f"{row['path']}:{row['line']} — {prefix}"]
    marks.extend(f"{row['path']}:{line} — {prefix}; #### Scenario: {name}" for name, line in row["scenarios"])
    return marks


def openspec_diagnostics(context: dict[str, Any], docs_snapshot: list[dict[str, str]]) -> list[dict[str, str]]:
    """Reconcile final identities and origins; semantic body review is still required."""
    expected, warnings = _openspec_rows(docs_snapshot)
    marks = {mark: row for row in expected for mark in _openspec_marks(row)}
    actual: set[str] = set()
    diagnostics: list[dict[str, str]] = []
    for warning in warnings:
        if "; missing:" in warning and warning not in (context.get("warnings") or []):
            diagnostics.append({"path": warning.split(" — ")[0], "code": "OPENSPEC_GAP_MISSING", "message": warning})
    for item in (context.get("artifacts") or {}).get("analytics_documentation", {}).get("requirements") or []:
        provenance = item.get("provenance") or []
        identities = [mark for mark in provenance if " — capability=" in mark and "; ### Requirement:" in mark]
        if not identities and any(_OPENSPEC_PATH.fullmatch(mark.split(" — ")[0].split(":")[0]) for mark in provenance):
            diagnostics.append({"path": str(item.get("source_requirement_id", "")), "code": "OPENSPEC_EXTRA", "message": f"{provenance[0]} — normalized OpenSpec row has no requirement/scenario identity"})
        for mark in provenance:
            if " — capability=" not in mark or "; ### Requirement:" not in mark:
                continue
            actual.add(mark)
            row = marks.get(mark)
            if row and (item.get("digest") != row["digest"] or f"{row['path']} — {row['digest']}" not in provenance or not set(row["rename_provenance"]).issubset(provenance)):
                diagnostics.append({"path": mark.split(" — ")[0], "code": "OPENSPEC_SOURCE_MISMATCH", "message": f"{mark} — normalized origin must retain exact source digest and rename provenance"})
    for mark in sorted(marks.keys() - actual):
        diagnostics.append({"path": mark.split(" — ")[0], "code": "OPENSPEC_MISSING", "message": f"{mark} — missing from normalized requirement/scenario set"})
    for mark in sorted(actual - marks.keys()):
        diagnostics.append({"path": mark.split(" — ")[0], "code": "OPENSPEC_EXTRA", "message": f"{mark} — absent from final selected specification"})
    return diagnostics


def build_context(
    project: Path,
    docs: list[Path] | None = None,
    source_files: list[Path] | None = None,
    *,
    docs_snapshot: list[dict[str, str]] | None = None,
    source_snapshot: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    project = project.resolve()
    id_re = compile_id_pattern(_project_id_pattern(project))
    if docs is not None and docs_snapshot is not None:
        raise ValueError("cannot pass docs and docs_snapshot together")
    if source_files and source_snapshot:
        raise ValueError("cannot pass source_files and source_snapshot together")
    requirements: list[dict[str, Any]] = []
    warnings: list[str] = []
    total_docs = 0
    doc_entries: list[dict[str, str]] = []
    if docs_snapshot is None:
        for doc in docs or []:
            confined = _confined(project, doc if doc.is_absolute() else project / doc)
            if confined is None or not confined.is_file():
                raise ValueError(f"docs path must stay inside the project: {doc}" + ("; fix: один файл или одна папка OpenSpec на каждый --docs, флаг повторяется (не через запятую)" if "," in str(doc) else ""))
            with confined.open("rb") as stream:
                data = stream.read(_DOCS_MAX_FILE + 1)
            if len(data) > _DOCS_MAX_FILE:
                raise RequirementConflict("NEED_DOCS_LIMIT", f"docs file exceeds 256KiB: {doc}")
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as error:
                raise ValueError(f"DOCS_ENCODING: docs file is not UTF-8: {doc}; fix: сохрани документ в UTF-8 и повтори scan") from error
            doc_entries.append(
                {
                    "path": confined.relative_to(project).as_posix(),
                    "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                    "content": text,
                }
            )
    else:
        doc_entries = docs_snapshot
    normalized_rows: list[tuple[str, str, str, str]] = []
    openspec_entries: list[dict[str, str]] = []
    openspec_provenance: dict[tuple[str, str, str, str], list[str]] = {}
    lines: dict[tuple[str, str, str, str], int] = {}
    for entry in doc_entries:
        rel = entry.get("path")
        digest = entry.get("sha256")
        text = entry.get("content")
        if not isinstance(rel, str) or not isinstance(digest, str) or not isinstance(text, str):
            raise ValueError("docs snapshot row is invalid")
        data = text.encode("utf-8")
        if digest != "sha256:" + hashlib.sha256(data).hexdigest():
            raise ValueError(f"docs snapshot digest does not match content: {rel}")
        if entry.get("context"):  # change only: the other spec.md and the proposal give the neighbours — no document of the run, no size of the documents
            openspec_entries.append(entry)
            continue
        if len(data) > _DOCS_MAX_FILE:
            raise RequirementConflict("NEED_DOCS_LIMIT", f"docs file exceeds 256KiB: {rel}")
        total_docs += len(data)
        if total_docs > _DOCS_MAX_TOTAL:
            raise RequirementConflict("NEED_DOCS_LIMIT", "docs snapshot exceeds 1MiB")
        if _OPENSPEC_PATH.fullmatch(rel.replace("\\", "/")) or rel.replace("\\", "/").startswith("openspec/changes/archive/"):
            openspec_entries.append(entry)
            continue
        if openspec_context_only(rel.replace("\\", "/")):
            warnings.append(f"{rel} — OpenSpec контекст (proposal/design/tasks), не требования: строк требований из него нет")
            continue
        rows = extract_inventory(text, id_re)
        for row in rows:
            snippet = str(row.get("text") or row["title"])
            normalized_rows.append((_compare_key(rel.replace("\\", "/")), digest, snippet, _norm(row["title"])))
            lines[normalized_rows[-1]] = text.count("\n", 0, max(text.find(snippet), 0)) + 1  # the line of the section: the order of a plain document and its provenance ``path:line``

    openspec_rows, openspec_warnings = _openspec_rows(openspec_entries)
    warnings.extend(openspec_warnings)
    for row in openspec_rows:
        key = (_compare_key(row["path"]), row["digest"], row["text"], row["name"])
        normalized_rows.append(key)
        openspec_provenance[key] = _openspec_marks(row) + row["rename_provenance"]

    # Snapshot order is not provenance. Collect/dedupe/sort the complete normalized set
    # before assigning any generated SREQ ID, so a resumed/controller-provided snapshot
    # has exactly the same identity as an equivalent CLI snapshot.
    deduped_rows = sorted(set(normalized_rows), key=lambda row: (row[0], row[1], lines.get(row, 0), _compare_key(row[2]), row[2], row[3]))
    for index, (rel, digest, snippet, title) in enumerate(deduped_rows, start=1):
        req_id = _source_requirement_id(index)
        mark = f"{rel} — {_nfc(title)}: {_nfc(snippet)}"
        digest_mark = f"{rel} — {digest}"
        item = {
            "source_requirement_id": req_id,
            "display_order": len(requirements) + 1,
            "text": snippet,
            "provenance": ([f"{rel}:{lines[(rel, digest, snippet, title)]}"] if (rel, digest, snippet, title) in lines else []) + [mark, digest_mark] + openspec_provenance.get((rel, digest, snippet, title), []),
            "digest": digest,
        }
        requirements.append(item)
    sources: list[str] = []
    snapshot_entries = source_snapshot if source_snapshot is not None else None
    if snapshot_entries is None:
        for path in source_files or []:
            confined = _confined(project, path if path.is_absolute() else project / path)
            if confined is None or not confined.is_file():
                continue
            rel = confined.relative_to(project).as_posix()
            text = confined.read_text(encoding="utf-8", errors="replace")
            snapshot_entries = (snapshot_entries or []) + [{"path": rel, "sha256": "", "content": text}]
    for entry in snapshot_entries or []:
        rel = str(entry.get("path") or "")
        text = str(entry.get("content") or "")
        handles = extract_handles(text)
        if not handles:
            sources.append(f"{rel} — source observed")
            continue
        for handle in handles:
            sources.append(f"{rel} — {handle}")
    status = "ok" if requirements else "NEED_DOCS"
    if not requirements:
        warnings.append("no system analytics; pass --docs. Refusing to invent business cases from code. — NEED_DOCS")
    if not sources:
        sources = ["(none) — no authorized source files"]
    else:
        sources = sorted(set(sources))
    warnings = sorted(set(warnings))
    envelope = {
        "schema_version": "5.0.0",
        "stage": "context-marker",
        "artifacts": {
            "analytics_documentation": {"requirements": requirements},
            "source_code_and_diff": {"sources": sources},
        },
        "warnings": warnings,
    }
    envelope["status"] = status
    return envelope


def main() -> int:
    parser = JsonArgumentParser(description="Build a context-marker envelope from docs and sources.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--docs", action="append", default=[])
    parser.add_argument("--source", action="append", default=[])
    args = parser.parse_args()
    project = Path(args.project)
    if not project.is_dir():
        emit_error(f"project not found: {project}")
        return 2
    from tools.run_pipeline import HostStop, expand_docs  # a folder of OpenSpec is the files it stands for, by the one function of ``scan``

    try:
        envelope = build_context(
            project,
            [Path(item) for item in expand_docs(project, args.docs)],
            [Path(item) for item in args.source],
        )
    except HostStop as stop:
        emit_error(f"{stop.code}: {stop}; fix: {stop.fix}")
        return 2
    except ValueError as error:
        emit_error(str(error))
        return 2
    errors = schema_diagnostics(
        {key: value for key, value in envelope.items() if key != "status"},
        _SCHEMA,
        _ROOT,
    )
    if errors and envelope.get("artifacts", {}).get("analytics_documentation", {}).get("requirements"):
        print(json.dumps({"status": "error", "errors": errors}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(envelope, ensure_ascii=False, indent=2))
    return 0 if envelope.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
