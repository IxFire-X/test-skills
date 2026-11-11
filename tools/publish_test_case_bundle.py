"""Publish canonical JSON/HTML/CSV bundles with a derived Markdown companion."""

from __future__ import annotations

import copy
import hashlib
import html
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from tools.canonical_document import CanonicalDocumentError, canonical_bytes, document_sha256, require_valid_canonical_document
from tools.schema_validation import classify_version
from tools.test_case_projections import (
    _reject_unknown_profile as _reject_projection_profile,
    _render_html_preview_validated,
    _render_markdown_validated,
    _render_zephyr_csv_validated,
    escape_inline,
    section_labels,
)


_PROFILE_V4 = "zephyr-scale-step-row-24-v4"
_PROFILE_V5 = "zephyr-scale-step-row-24-v5"
_PROFILE = _PROFILE_V5
_CURRENT_PROFILES = frozenset((_PROFILE_V4, _PROFILE_V5))
_ORDER = ("json", "preview", "csv", "markdown")
_MESSAGES = {
    "OUTPUT_DIRECTORY_ERROR": "Output directory is unavailable.",
    "HARDLINK_UNAVAILABLE": "Atomic hard-link publication is unavailable.",
    "TEMPORARY_WRITE_FAILED": "Temporary bundle payload could not be written.",
    "TARGET_INSTALL_FAILED": "Bundle target could not be installed.",
    "TARGET_READ_FAILED": "Bundle target could not be read.",
    "INPUT_JSON_UNREADABLE": "Input must be strict UTF-8 canonical JSON.",
}


@dataclass(frozen=True)
class BundlePayloads:
    json_bytes: bytes
    preview_bytes: bytes
    csv_bytes: bytes
    warnings: tuple[str, ...]
    markdown_bytes: bytes = b""


@dataclass(frozen=True)
class Receipt:
    document_id: str
    revision: int
    csv_profile: str
    json_path: str
    preview_path: str
    csv_path: str
    document_sha256: str
    preview_sha256: str
    csv_sha256: str


class BundleMismatchError(ValueError):
    """One or more bundle targets are not precisely the expected payload."""

    def __init__(self, mismatches: tuple[str, ...] | list[str]) -> None:
        self._mismatches = tuple(mismatches)
        super().__init__("bundle targets do not match expected payloads: " + ", ".join(self._mismatches))

    @property
    def mismatches(self) -> tuple[str, ...]:
        return self._mismatches


class BundlePublicationError(OSError):
    """A filesystem primitive failed without exposing filesystem details."""

    def __init__(self, code: str) -> None:
        self._code = code
        self._safe_message = _MESSAGES[code]
        super().__init__(self._safe_message)

    @property
    def code(self) -> str:
        return self._code

    @property
    def safe_message(self) -> str:
        return self._safe_message


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _reject_unknown_profile(csv_profile: str, *, allow_historical: bool = False) -> None:
    _reject_projection_profile(csv_profile)
    if not allow_historical and csv_profile not in _CURRENT_PROFILES:
        raise ValueError("historical Zephyr profiles are verification-only and require an explicit receipt profile")


def _validate_once(document: dict[str, Any], csv_profile: str, *, allow_historical: bool = False) -> None:
    _reject_unknown_profile(csv_profile, allow_historical=allow_historical)
    version = classify_version(document)
    if version["code"] == "V2_1_BREAKING_CHANGE":
        raise CanonicalDocumentError(({
            "path": "/schema_version",
            "code": "V2_1_BREAKING_CHANGE",
            "message": version["message"],
        },))
    require_valid_canonical_document(document)


def build_bundle(document: dict[str, Any], csv_profile: str = _PROFILE, *, _allow_historical: bool = False, partial: dict[str, Any] | None = None) -> BundlePayloads:
    """Build all exact bytes after one canonical validation pass.  *partial* (``partial_context``): the set is a subtraction of the reviewer's set —
    its cases keep the numbers they had there, its header and its last section say what was taken out and which requirements are left without a case."""
    _validate_once(document, csv_profile, allow_historical=_allow_historical)
    extras = None if partial is None else {"labels": partial["labels"]}
    preview = _render_html_preview_validated(document, csv_profile, extras) if csv_profile in _CURRENT_PROFILES else _render_markdown_validated(document, csv_profile)
    csv = _render_zephyr_csv_validated(document, csv_profile)
    markdown = _render_markdown_validated(document, csv_profile, extras).payload if csv_profile in _CURRENT_PROFILES else b""
    if partial is not None:
        warnings, preview, markdown = preview.warnings, _partial_html(preview.payload, partial), _partial_markdown(markdown, partial)
        return BundlePayloads(canonical_bytes(document), preview, csv.payload, warnings + csv.warnings, markdown)
    return BundlePayloads(canonical_bytes(document), preview.payload, csv.payload, preview.warnings + csv.warnings, markdown)


def _resolved_output_directory(output_dir: str | os.PathLike[str], create: bool) -> Path:
    try:
        directory = Path(output_dir).resolve()
        if directory.exists():
            if not directory.is_dir():
                raise OSError
        elif create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory
    except OSError:
        raise BundlePublicationError("OUTPUT_DIRECTORY_ERROR") from None


def _targets(document: dict[str, Any], directory: Path, csv_profile: str = _PROFILE) -> tuple[tuple[str, Path], ...]:
    prefix = f"{document['document_id']}.r{document['revision']}"
    return (
        ("json", directory / f"{prefix}.json"),
        ("preview", directory / f"{prefix}.{'html' if csv_profile in _CURRENT_PROFILES else 'md'}"),
        ("csv", directory / f"{prefix}.zephyr-scale.csv"),
    )


def _payloads(bundle: BundlePayloads) -> tuple[tuple[str, bytes], ...]:
    return tuple(zip(_ORDER, (bundle.json_bytes, bundle.preview_bytes, bundle.csv_bytes, bundle.markdown_bytes)))


def _preflight(targets: tuple[tuple[str, Path], ...], bundle: BundlePayloads) -> tuple[str, ...]:
    missing: list[str] = []
    mismatches: list[str] = []
    expected = dict(_payloads(bundle))
    for label, target in targets:
        try:
            if not os.path.lexists(target):
                missing.append(label)
            elif not target.is_file() or target.read_bytes() != expected[label]:
                mismatches.append(label)
        except OSError:
            mismatches.append(label)
    if mismatches:
        raise BundleMismatchError(mismatches)
    return tuple(missing)


def _unlink_current(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def _probe_hardlink(directory: Path) -> None:
    source: Path | None = None
    destination: Path | None = None
    failed_cleanup = False
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as temporary:
            source = Path(temporary.name)
            temporary.write(b"probe")
            temporary.flush()
        destination = directory / f".bundle-link-probe-{uuid.uuid4().hex}"
        os.link(source, destination)
    except OSError:
        raise BundlePublicationError("HARDLINK_UNAVAILABLE") from None
    finally:
        if destination is not None:
            failed_cleanup = not _unlink_current(destination) or failed_cleanup
        if source is not None:
            failed_cleanup = not _unlink_current(source) or failed_cleanup
        if failed_cleanup:
            raise BundlePublicationError("HARDLINK_UNAVAILABLE") from None


def _write_temps(directory: Path, missing: tuple[str, ...], bundle: BundlePayloads) -> dict[str, Path]:
    expected = dict(_payloads(bundle))
    temporary_paths: dict[str, Path] = {}
    try:
        for label in missing:
            with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as temporary:
                temporary_paths[label] = Path(temporary.name)
                temporary.write(expected[label])
                temporary.flush()
        return temporary_paths
    except OSError:
        for path in temporary_paths.values():
            _unlink_current(path)
        raise BundlePublicationError("TEMPORARY_WRITE_FAILED") from None


def _read_final(targets: tuple[tuple[str, Path], ...], bundle: BundlePayloads) -> tuple[bytes, ...]:
    expected = dict(_payloads(bundle))
    values: list[bytes] = []
    mismatches: list[str] = []
    for label, target in targets:
        try:
            payload = target.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            mismatches.append(label)
            continue
        except OSError:
            raise BundlePublicationError("TARGET_READ_FAILED") from None
        if payload != expected[label]:
            mismatches.append(label)
        values.append(payload)
    if mismatches:
        raise BundleMismatchError(mismatches)
    return tuple(values)  # type: ignore[return-value]


def _receipt(document: dict[str, Any], csv_profile: str, targets: tuple[tuple[str, Path], ...], payloads: tuple[bytes, ...]) -> Receipt:
    return Receipt(
        document_id=document["document_id"], revision=document["revision"], csv_profile=csv_profile,
        json_path=str(targets[0][1]), preview_path=str(targets[1][1]), csv_path=str(targets[2][1]),
        document_sha256=_digest(payloads[0]), preview_sha256=_digest(payloads[1]), csv_sha256=_digest(payloads[2]),
    )


def publish_bundle(document: dict[str, Any], output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE, partial: dict[str, Any] | None = None) -> Receipt:
    """Recoverably publish a bundle by hard-linking complete local temporary files."""
    bundle = build_bundle(document, csv_profile, partial=partial)
    directory = _resolved_output_directory(output_dir, create=True)
    targets = _targets(document, directory, csv_profile)
    # Markdown is a derived companion; existing authoritative receipt fields stay unchanged.
    targets += (("markdown", directory / f"{document['document_id']}.r{document['revision']}.md"),)
    missing = _preflight(targets, bundle)
    if not missing:
        return _receipt(document, csv_profile, targets, _read_final(targets, bundle))
    _probe_hardlink(directory)
    temporary_paths = _write_temps(directory, missing, bundle)
    expected = dict(_payloads(bundle))
    try:
        for label, target in targets:
            if label not in temporary_paths:
                continue
            temporary = temporary_paths[label]
            try:
                os.link(temporary, target)
            except FileExistsError:
                try:
                    installed = target.read_bytes()
                except OSError:
                    raise BundleMismatchError((label,)) from None
                if installed != expected[label]:
                    raise BundleMismatchError((label,))
            except OSError:
                raise BundlePublicationError("TARGET_INSTALL_FAILED") from None
            else:
                if not _unlink_current(temporary):
                    raise BundlePublicationError("TARGET_INSTALL_FAILED")
                temporary_paths.pop(label, None)
        return _receipt(document, csv_profile, targets, _read_final(targets, bundle))
    finally:
        cleanup_failed = False
        for temporary in temporary_paths.values():
            cleanup_failed = not _unlink_current(temporary) or cleanup_failed
        if cleanup_failed and sys.exc_info()[0] is None:
            raise BundlePublicationError("TARGET_INSTALL_FAILED") from None


def planned_receipt(document: dict[str, Any], output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE, partial: dict[str, Any] | None = None) -> Receipt:
    """The receipt of the bundle ``publish_bundle`` writes once the set is accepted — computed, nothing read or written."""
    bundle = build_bundle(document, csv_profile, _allow_historical=True, partial=partial)
    targets = _targets(document, _resolved_output_directory(output_dir, create=False), csv_profile)
    return _receipt(document, csv_profile, targets, tuple(payload for _label, payload in _payloads(bundle)[:3]))


_STATUS_HEADERS = {"REJECTED": "ОТКЛОНЁН РЕВЬЮЕРОМ", "UNREVIEWED": "НЕ ПРОВЕРЕН РЕВЬЮЕРОМ", "REJECTED-CASES": "НЕ ПРИНЯТЫ РЕВЬЮЕРОМ"}
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_\[\]()#|>~])")


def status_payloads(document: dict[str, Any], csv_profile: str, status: str, notes: Sequence[str] = (), *, extras: dict[str, Any] | None = None,
                    intro: str = "") -> tuple[tuple[str, bytes], ...]:
    """The ``.html`` and ``.md`` of a set that is not accepted, with a header that says so: ``(suffix, bytes)``.  Never a CSV.
    The notes are model text: HTML-escaped, one line each, markdown signs escaped — they cannot reach the markup around them.
    ``REJECTED-CASES``: the cases the review did not accept — a title and an introduction, with the numbers and notes of *extras*."""
    title = f"{_STATUS_HEADERS[status]} — не импортировать в Zephyr"
    lines = [" ".join(html.escape(note).split()) for note in notes]
    if status == "REJECTED-CASES":
        header_html = f'<div style="border:3px solid #b00;background:#fee;padding:1rem;margin:0 0 1.5rem"><h1 style="margin:0 0 .5rem">{title}</h1><p>{html.escape(intro)}</p></div>'
        header_md = f"# {title}\n\n{escape_inline(intro)}\n\n"
    else:
        header_html = ('<div style="border:3px solid #b00;background:#fee;padding:1rem;margin:0 0 1.5rem"><strong>' + title + "</strong>"
                       + ("<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>" if lines else "") + "</div>")
        header_md = f"> **{title}**\n" + "".join("> - " + _MARKDOWN_SPECIAL.sub(lambda m: "\\" + m.group(1), line) + "\n" for line in lines) + "\n"
    _validate_once(document, csv_profile)
    page = _render_html_preview_validated(document, csv_profile, extras).payload.decode("utf-8")
    if "<body>" not in page:  # the header is the whole point of the file: a template without the tag is a refusal, not a file without it
        raise ValueError("STATUS_HEADER_NOT_PLACED: the HTML preview has no <body> tag to put the status header after")
    return (("html", page.replace("<body>", "<body>" + header_html, 1).encode("utf-8")),
            ("md", header_md.encode("utf-8") + _render_markdown_validated(document, csv_profile, extras).payload))


def verify_bundle(document: dict[str, Any], output_dir: str | os.PathLike[str], csv_profile: str = _PROFILE, partial: dict[str, Any] | None = None) -> Receipt:
    """Read and verify the immutable bundle without creating or modifying anything."""
    bundle = build_bundle(document, csv_profile, _allow_historical=True, partial=partial)
    directory = _resolved_output_directory(output_dir, create=False)
    targets = _targets(document, directory, csv_profile)
    expected = dict(_payloads(bundle))
    values: list[bytes] = []
    mismatches: list[str] = []
    for label, target in targets:
        try:
            if not target.is_file():
                mismatches.append(label)
                continue
            payload = target.read_bytes()
        except OSError:
            mismatches.append(label)
            continue
        if payload != expected[label]:
            mismatches.append(label)
        values.append(payload)
    if mismatches:
        raise BundleMismatchError(mismatches)
    return _receipt(document, csv_profile, targets, tuple(values))  # type: ignore[arg-type]


# --- partial acceptance: the files of a set the code took cases out of ----------------------------------------------------------

def plural(number: int, one: str, few: str, many: str) -> str:
    return one if number % 10 == 1 and number % 100 != 11 else few if number % 10 in (2, 3, 4) and number % 100 not in (12, 13, 14) else many


def partial_context(candidate: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    """What the files and the report of a ``PARTIAL`` outcome say, a function of the reviewer's set r1 and the outcome (nothing is stored):
    the numbers of r1, the cases taken out with what the reviewer said of them, and the requirements left without a case."""
    from tools.revision_selection import coverage_gaps

    labels, out = section_labels(candidate), {*outcome["rejected"], *outcome["with_group"]}
    rejected = [{"case_id": case["case_id"], "label": labels[case["case_id"]], "title": case["title"], "requirements": case["requirement_ids"],
                 "said": [[row["code"], row["message"]] for row in outcome["said"].get(case["case_id"], [])], "with_group": case["case_id"] in outcome["with_group"]}
                for case in candidate["test_cases"] if case["case_id"] in out]
    gaps = [{"requirement": item, "labels": [row["label"] for row in rejected if item in row["requirements"]],
             "said": list(dict.fromkeys(text for row in rejected if item in row["requirements"] for _code, text in row["said"]))} for item in coverage_gaps(outcome["effective"])]
    return {"total": len(candidate["test_cases"]), "kept": len(outcome["effective"]["test_cases"]), "labels": labels, "rejected": rejected, "gaps": gaps,
            "file": f"{candidate['document_id']}.r{outcome['effective']['revision']}.REJECTED-CASES.md"}


def _partial_header(partial: dict[str, Any]) -> tuple[str, str]:
    """``(bold, rest)``: the sentence that says the set is partial and the one that says where the rest is."""
    rows, count = partial["rejected"], len(partial["rejected"])
    names = ", ".join(f"ТК-{row['label']}" + (f" «{row['title']}»" if count <= 5 else "") for row in rows)
    where = (f"Кейс {names} ревью не прошёл и в этот набор не входит. Он лежит" if count == 1
             else f"Кейсы {names} ревью не прошли и в этот набор не входят. Они лежат") + f" в файле {partial['file']} вместе с замечаниями ревьюера."
    gaps = len(partial["gaps"])
    left = (f" Без принятого кейса {plural(gaps, 'осталось', 'остались', 'остались')} {gaps} {plural(gaps, 'требование', 'требования', 'требований')}"
            " — см. раздел «Пробелы покрытия» в конце.") if gaps else ""
    return f"Принят частично: {partial['kept']} из {partial['total']} кейсов.", where + left


def gap_rows(partial: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(row["requirement"], "Его покрывали только отклонённые кейсы: " + ", ".join(f"ТК-{label}" for label in row["labels"]), "; ".join(" ".join(text.split()) for text in row["said"]) or "—")
            for row in partial["gaps"]]


def _partial_html(page: bytes, partial: dict[str, Any]) -> bytes:
    bold, rest = _partial_header(partial)
    text = page.decode("utf-8")
    box = f'<div style="border:2px solid #c80;background:#fff8e5;padding:1rem;margin:0 0 1.5rem"><strong>{html.escape(bold)}</strong> {html.escape(rest)}</div>'
    table = ""
    if partial["gaps"]:
        rows = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in gap_rows(partial))
        table = ("<section><h2>Пробелы покрытия</h2><table><thead><tr><th>Требование</th><th>Почему нет кейса</th><th>Что сказал ревьюер</th></tr></thead>"
                 f"<tbody>{rows}</tbody></table></section>")
    if "</h1>" not in text or "</body>" not in text:
        raise ValueError("PARTIAL_HEADER_NOT_PLACED: the HTML preview has no title or body tag")
    return text.replace("</h1>", "</h1>" + box, 1).replace("</body>", table + "</body>", 1).encode("utf-8")


def _partial_markdown(page: bytes, partial: dict[str, Any]) -> bytes:
    bold, rest = _partial_header(partial)
    head, _, tail = page.decode("utf-8").partition("\n\n")
    section = ""
    if partial["gaps"]:
        cell = lambda value: escape_inline(value)  # noqa: E731
        section = ("\n---\n\n## Пробелы покрытия\n\n| Требование | Почему нет кейса | Что сказал ревьюер |\n|---|---|---|\n"
                   + "".join("| " + " | ".join(cell(item) for item in row) + " |\n" for row in gap_rows(partial)))
    return (head + f"\n\n> **{bold}** {escape_inline(rest)}\n\n" + tail + section).encode("utf-8")


def rejected_cases(candidate: dict[str, Any], partial: dict[str, Any], run_id: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    """``(document, extras, intro)`` of ``*.rN.REJECTED-CASES``: the rejected cases of r1 as a set of their own (a variant of a group stands alone, so its
    ``param_group`` is left out of this copy) with the numbers of r1 and, under each, its requirements and what the reviewer said."""
    ids = {row["case_id"] for row in partial["rejected"]}
    shown = copy.deepcopy(candidate)
    shown["revision"], shown["parent_sha256"] = candidate["revision"] + 1, document_sha256(candidate)
    shown["test_cases"] = [{key: value for key, value in case.items() if key != "param_group"} for case in shown["test_cases"] if case["case_id"] in ids]
    notes: dict[str, list[tuple[str, str]]] = {}
    for row in partial["rejected"]:
        alone = [gap["requirement"] for gap in partial["gaps"] if gap["requirement"] in row["requirements"]]
        notes[row["case_id"]] = [*([("Без другого принятого кейса:", ", ".join(alone) + ".")] if alone else []),
                                 *([("Вынесен вместе с группой параметров:", "в ней остался один вариант, а группа из одного кейса невозможна.")] if row["with_group"] else []),
                                 *((f"Замечание ревьюера (блокирующее, {code}):", text) for code, text in row["said"])]
    subject = candidate["metadata"]["subject"]
    name = subject["name"] if subject["kind"] != "http_endpoint" else f"{subject['method']} {subject['path']}"
    intro = (f"Прогон {run_id[:8]}…, набор «{name}». Эти кейсы не прошли ревью; доработка не запускалась. "
             "Их можно доработать вручную или запросить новый прогон для этих требований.")
    return shown, {"labels": partial["labels"], "notes": notes}, intro
