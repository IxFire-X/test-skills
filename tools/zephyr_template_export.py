"""FaT-7: a Zephyr import CSV filled from the project's own Zephyr export template, beside the published bundle.

``python -m tools.zephyr_template_export --project <abs> --run <id> --cases <bundle .zephyr-scale.csv>`` reads
``.skillsrc`` ``projection.zephyr.template`` and writes ``<doc>.r<N>.zephyr-import.csv`` next to the bundle.  The
bundle and its receipt never change.  Run it once, after the attempt is closed (``exec`` or ``finish``): only then
is «Автоматизирован» known.  The template's values stay in that CSV; the run keeps only the template path and digest.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from tools.test_case_projections import _CUSTOM_KEYS, _HEADERS, _formula_safe

TEMPLATE_LIMIT = 1024 * 1024
AUTOMATED = "Автоматизирован"
_YES_NO = {"да": "нет", "нет": "да", "yes": "no", "no": "yes", "true": "false", "false": "true"}
_YES = {"да", "yes", "true"}
_HINTS = {
    "ZEPHYR_TEMPLATE_OUTSIDE": "шаблон должен лежать внутри проекта: укажите путь от корня проекта в .skillsrc projection.zephyr.template",
    "ZEPHYR_TEMPLATE_MISSING": "файла шаблона нет: проверьте путь в .skillsrc projection.zephyr.template",
    "ZEPHYR_TEMPLATE_TOO_LARGE": "шаблон больше 1 МБ: оставьте в экспорте Zephyr несколько заглушек",
    "ZEPHYR_TEMPLATE_ENCODING": "сохраните шаблон в UTF-8 (Excel: Файл → Сохранить как → CSV UTF-8) и повторите",
    "ZEPHYR_TEMPLATE_NOT_CSV": "шаблон не похож на CSV: нужен экспорт тест-кейсов из Zephyr Scale в CSV",
    "ZEPHYR_TEMPLATE_DELIMITER": "не удалось определить разделитель по заголовку: нужен «,» или «;» и колонки Zephyr (Name, …)",
    "ZEPHYR_TEMPLATE_COLUMNS": f"в шаблоне нужна колонка Name и хотя бы одна из: {', '.join(_CUSTOM_KEYS)}",
    "ZEPHYR_TEMPLATE_EMPTY": "в шаблоне нет ни одной заполненной заглушки (строки с Name)",
    "ZEPHYR_EXPORT_NOT_CLOSED": "запустите экспорт после закрытия попытки (exec или finish): тогда известен статус автоматизации",
    "ZEPHYR_EXPORT_RUN_MISMATCH": "этот набор кейсов опубликован другим прогоном: укажите --run того прогона, который его опубликовал",
    "ZEPHYR_EXPORT_CASES": "--cases — это файл <doc>.r<N>.zephyr-scale.csv опубликованного набора, рядом с ним должен лежать <doc>.r<N>.json",
    "ZEPHYR_EXPORT_CONFLICT": "рядом уже лежит другой .zephyr-import.csv: переименуйте или удалите его, если он больше не нужен",
}


class ZephyrTemplateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code, self.hint = code, _HINTS[code]
        super().__init__(f"{code}: {detail or self.hint}")


@dataclass(frozen=True)
class Template:
    path: str
    digest: str
    delimiter: str
    header: list[str]
    values: dict[str, str] = field(default_factory=dict)


def _header_cells(line: str, delimiter: str) -> list[str]:
    return next(csv.reader([line], delimiter=delimiter), [])


def read_template(project: Path, relative: str) -> Template:
    """Read the template inside the project, through no link or junction, and take the first filled stub row."""
    project = Path(project).resolve()
    candidate = project / relative
    try:
        parts = candidate.relative_to(project).parts if not Path(relative).is_absolute() else ()
        if not parts or ".." in parts:
            raise ValueError
        current = project
        for part in parts:
            current = current / part
            if current.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(current):
                raise ValueError
    except ValueError:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_OUTSIDE", relative) from None
    if not candidate.is_file():
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_MISSING", relative)
    if candidate.stat().st_size > TEMPLATE_LIMIT:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_TOO_LARGE")
    raw = candidate.read_bytes()
    if len(raw) > TEMPLATE_LIMIT:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_TOO_LARGE")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_ENCODING") from None
    first = text.splitlines()[0] if text.strip() else ""
    splits = {delimiter: _header_cells(first, delimiter) for delimiter in (",", ";")}
    if max(len(cells) for cells in splits.values()) < 2:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_NOT_CSV")
    known = {delimiter: len(set(cells) & set(_HEADERS)) for delimiter, cells in splits.items()}
    if known[","] == known[";"]:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_DELIMITER")
    delimiter = max(known, key=known.__getitem__)
    rows = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    header = [cell.strip() for cell in rows[0]]
    if "Name" not in header or not set(header) & set(_CUSTOM_KEYS):
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_COLUMNS")
    name = header.index("Name")
    stub = next((row for row in rows[1:] if len(row) > name and row[name].strip()), None)
    if stub is None:
        raise ZephyrTemplateError("ZEPHYR_TEMPLATE_EMPTY")
    values = {column: (stub[index] if index < len(stub) else "") for index, column in enumerate(header)
              if column not in _HEADERS or column in _CUSTOM_KEYS}
    return Template(relative.replace("\\", "/"), "sha256:" + hashlib.sha256(raw).hexdigest(), delimiter, header, values)


def _in_form(sample: str, word: str) -> str:
    if sample.isupper() and len(sample) > 1:
        return word.upper()
    return word.capitalize() if sample[:1].isupper() else word


def _automated(sample: str, automated: bool) -> str:
    """«да» only for an accepted automation with code of this case; written in the template's own form."""
    lower = sample.strip().lower()
    if lower not in _YES_NO:
        return sample
    yes = lower if lower in _YES else _YES_NO[lower]
    return _in_form(sample.strip(), yes if automated else _YES_NO[yes])


def build_import_csv(bundle_csv: bytes, document: Mapping[str, Any], template: Template, *, automated_case_ids: set[str]) -> bytes:
    """Our bundle CSV, template-only columns appended, custom fields of each case's first row filled."""
    rows = list(csv.reader(io.StringIO(bundle_csv.decode("utf-8-sig"), newline=""), delimiter=","))
    ours = rows[0]
    extra = [column for column in template.header if column not in ours]
    name = ours.index("Name")
    cases = iter(document["test_cases"])
    output = [ours + extra]
    for row in rows[1:]:
        if not row[name]:
            output.append(row + [""] * len(extra))
            continue
        case = next(cases)
        automated = case["case_id"] in automated_case_ids and not case.get("automation_blockers")
        filled = list(row)
        for index, column in enumerate(ours):
            if column == AUTOMATED and column in template.values:
                filled[index] = _formula_safe(_automated(template.values[column], automated))
            elif column in _CUSTOM_KEYS and column in template.values and not filled[index]:
                filled[index] = _formula_safe(template.values[column])
        output.append(filled + [_formula_safe(template.values.get(column, "")) for column in extra])
    buffer = io.StringIO(newline="")
    csv.writer(buffer, delimiter=template.delimiter, quotechar='"', lineterminator="\r\n").writerows(output)
    return buffer.getvalue().encode("utf-8-sig")


def _automation_facts(run_root: Path) -> tuple[bool, set[str]]:
    """Whether the last attempt is closed, and the cases with code in an accepted automation."""
    from tools import pilot_state as pilot

    state = pilot.derive_state(run_root)
    if not state["attempts"] or state["attempts"][-1]["state"] != "TERMINAL":
        return False, set()
    attempt_id = state["attempts"][-1]["attempt_id"]
    if pilot.read_terminal_result(run_root, attempt_id).get("accepted") is not True:
        return True, set()
    try:
        artifacts = pilot.read_execution_inputs(run_root, attempt_id)["automation_artifact"]["artifacts"]
    except (KeyError, OSError, TypeError, ValueError):
        return True, set()
    return True, {str(row["case_id"]) for row in artifacts.get("implementation_relations") or [] if row.get("case_id")}


def _published_digests(run_root: Path, state: Mapping[str, Any]) -> set[str]:
    """The case documents this run published: the assembled candidate and each attempt's effective revision."""
    from tools import pilot_state as pilot

    digests = {str(event["artifact_digest"]) for event in state["events"]
               if event["event_type"] == "CANDIDATE_PUBLISHED" and event.get("stage_instance_id") == "assembly"}
    for attempt in state["attempts"]:
        try:
            effective = pilot.read_effective_canonical_if_present(run_root, attempt["attempt_id"])
        except (KeyError, OSError, TypeError, ValueError):
            effective = None
        if effective and isinstance(effective.get("document_digest"), str):
            digests.add(effective["document_digest"])
    return digests


def _template_setting(project: Path) -> str | None:
    path = project / ".skillsrc"
    if not path.is_file():
        return None
    from tools.skillsrc_manifest import load_skillsrc

    value = ((load_skillsrc(path).get("projection") or {}).get("zephyr") or {}).get("template")
    return value if isinstance(value, str) and value else None


def _install(target: Path, data: bytes) -> None:
    """Hard-link a complete temporary file, as the bundle is installed: no link is followed, no file replaced."""
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=target.parent) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            os.link(temporary, target)
        except FileExistsError:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != data:
                raise ZephyrTemplateError("ZEPHYR_EXPORT_CONFLICT", str(target)) from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def export_import_csv(project: Path, run_id: str, bundle_csv: Path) -> dict[str, Any]:
    from tools.confined_output import atomic_write_confined_bytes_at_root

    project = Path(project).resolve()
    relative = _template_setting(project)
    if relative is None:
        return {"status": "skipped", "reason": "NO_TEMPLATE"}
    template = read_template(project, relative)
    from tools.canonical_document import document_sha256
    from tools.run_pipeline import _read_run_coordinate

    root, _run, state = _read_run_coordinate(project, run_id)
    bundle_csv = Path(bundle_csv).resolve()
    base = bundle_csv.name.removesuffix(".zephyr-scale.csv")
    try:
        bundle_csv.relative_to(Path(project).resolve())  # FaT-B: the import file is written only inside the project
        if base == bundle_csv.name:
            raise ValueError
        document = json.loads(bundle_csv.with_name(base + ".json").read_text(encoding="utf-8"))
        cases_bytes = bundle_csv.read_bytes()
    except (OSError, ValueError):
        raise ZephyrTemplateError("ZEPHYR_EXPORT_CASES", str(bundle_csv)) from None
    # FaT-7: case IDs repeat between runs, so the set must be one this run published.
    if document_sha256(document) not in _published_digests(root, state):
        raise ZephyrTemplateError("ZEPHYR_EXPORT_RUN_MISMATCH", str(bundle_csv))
    closed, automated = _automation_facts(root)
    if not closed:
        raise ZephyrTemplateError("ZEPHYR_EXPORT_NOT_CLOSED")
    data = build_import_csv(cases_bytes, document, template, automated_case_ids=automated)
    target = bundle_csv.with_name(base + ".zephyr-import.csv")
    _install(target, data)
    result = {
        "status": "ok", "output": str(target), "template_path": template.path, "template_digest": template.digest,
        "delimiter": template.delimiter, "columns_added": [column for column in template.header if column not in _HEADERS],
        "rows": sum(1 for _ in csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=template.delimiter)) - 1,
    }
    record = {key: result[key] for key in ("output", "template_path", "template_digest", "columns_added", "rows")}
    atomic_write_confined_bytes_at_root(project, root, root / "zephyr-import.json",
                                        json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n")
    return result


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Zephyr import CSV from the project's Zephyr export template (FaT-7).")
    parser.add_argument("--project", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--cases", required=True, help="the published bundle .zephyr-scale.csv")
    args = parser.parse_args(argv)
    try:
        result = export_import_csv(Path(args.project), args.run, Path(args.cases))
    except ZephyrTemplateError as error:
        print(json.dumps({"status": "error", "code": error.code, "hint": error.hint}, ensure_ascii=False))
        return 2
    except Exception as error:  # noqa: BLE001 - FaT-B: HostStop of the run coordinate and any other failure: one JSON, code 2
        code = getattr(error, "code", None)
        print(json.dumps({"status": "error", "code": code if isinstance(code, str) and code else "ZEPHYR_EXPORT_FAILED",
                          "hint": str(error) or type(error).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
