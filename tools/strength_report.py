"""Test strength report of one attempt: ``test-strength.json`` and ``test-strength.md``.

Built deterministically from the durable mutation receipt (and, once present, the
survivor triage) into the attempt's projection bundle directory, next to the
canonical JSON, Markdown, HTML and CSV.  The receipt keeps counts only; ratios are
computed here.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.mutation import score


def _percent(killed: int, covered: int) -> str:
    return "—" if covered == 0 else f"{100 * killed / covered:.0f} %"


def strength_view(receipt: Mapping[str, Any], triage: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    totals = receipt.get("totals") or {}
    decisions = {row["group_id"]: row for row in triage}
    groups = []
    for group in receipt.get("survivor_groups", []):
        row = {key: group[key] for key in ("group_id", "class", "method", "line", "mutants", "case_ids", "requirement_ids", "source_requirement_ids", "triage")}
        if group["group_id"] in decisions:
            row["decision"] = {key: value for key, value in decisions[group["group_id"]].items() if key != "group_id"}
        groups.append(row)
    return {
        "schema_version": "1.0.0", "run_id": receipt["run_id"], "attempt_id": receipt["attempt_id"], "mutation_receipt_digest": receipt["digest"],
        "status": receipt["status"], "reason_code": receipt["reason_code"], "message": receipt["message"], "verification": receipt["verification"],
        "tool": {"name": receipt["tool"]["name"], "version": receipt["tool"]["version"], "plugin_version": receipt["tool"]["plugin_version"]},
        "score": score(int(totals.get("killed", 0)), int(totals.get("survived", 0))) if totals else None,
        "totals": totals or None, "excluded_methods": list(receipt.get("excluded_methods", [])), "target_classes": list(receipt.get("target_classes", [])),
        "cases": [{**row, "score": score(row["killed"], row["covered"] - row["killed"])} for row in receipt.get("cases", [])],
        "requirements": [{**row, "score": score(row["killed"], row["covered"] - row["killed"])} for row in receipt.get("requirements", [])],
        "source_requirements": [{**row, "score": score(row["killed"], row["covered"] - row["killed"])} for row in receipt.get("source_requirements", [])],
        "survivor_groups": groups,
        "duration_seconds": round(int(receipt.get("durations_ms", {}).get("stage", 0)) / 1000, 1),
    }


def render_markdown(view: Mapping[str, Any]) -> str:
    lines = ["# Сила тестов (мутации)", ""]
    if view["status"] != "MEASURED":
        lines += [f"Статус: `{view['status']}` ({view['reason_code']}). {view['message'] or ''}".rstrip(), ""]
        return "\n".join(lines)
    totals = view["totals"]
    counted = totals["killed"] + totals["survived"]
    lines += [
        f"Инструмент: PIT {view['tool']['version']} + pitest-junit5-plugin {view['tool']['plugin_version']}; время этапа {view['duration_seconds']} с.",
        f"Доля убитых среди покрытых: **{_percent(totals['killed'], counted)}** ({totals['killed']} из {counted}); без покрытия {totals['no_coverage']}, "
        f"таймаут {totals['timed_out']}, ошибка памяти {totals['memory_error']}, ошибка запуска {totals['run_error']}.",
    ]
    if view["excluded_methods"]:
        lines.append("Исключены упавшие методы: " + ", ".join(f"`{item}`" for item in view["excluded_methods"]) + ".")
    lines += ["", "## По кейсам", "", "| Кейс | Покрыто | Убито | Доля |", "| --- | --- | --- | --- |"]
    lines += [f"| {row['case_id']} | {row['covered']} | {row['killed']} | {_percent(row['killed'], row['covered'])} |" for row in view["cases"]]
    lines += ["", "## По требованиям", "", "| Требование | Покрыто | Убито | Доля |", "| --- | --- | --- | --- |"]
    lines += [f"| {row['requirement_id']} | {row['covered']} | {row['killed']} | {_percent(row['killed'], row['covered'])} |" for row in view["requirements"]]
    lines += [f"| {row['source_requirement_id']} | {row['covered']} | {row['killed']} | {_percent(row['killed'], row['covered'])} |" for row in view["source_requirements"]]
    lines += ["", "## Выжившие мутанты", ""]
    if not view["survivor_groups"]:
        lines.append("Выживших нет.")
    else:
        lines += ["| Группа | Место | Мутации | Кейсы | Требования | Разбор |", "| --- | --- | --- | --- | --- | --- |"]
        for group in view["survivor_groups"]:
            mutants = "; ".join(item["description"] or item["mutator"] for item in group["mutants"])
            decision = group.get("decision", {}).get("decision") or ("ожидает разбора" if group["triage"] else "сверх предела, без разбора")
            lines.append(f"| {group['group_id']} | `{group['class'].rsplit('.', 1)[-1]}#{group['method']}:{group['line']}` | {mutants} | "
                         f"{', '.join(group['case_ids'])} | {', '.join(group['requirement_ids'])} | {decision} |")
    lines.append("")
    return "\n".join(lines)


def write_strength_report(directory: Path, receipt: Mapping[str, Any], triage: Sequence[Mapping[str, Any]] = ()) -> dict[str, str]:
    """Write both files (same bytes for the same inputs) and return their paths."""
    view = strength_view(receipt, triage)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "test-strength.json"
    markdown_path = directory / "test-strength.md"
    json_path.write_text(json.dumps(view, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    markdown_path.write_text(render_markdown(view), encoding="utf-8", newline="\n")
    return {"test_strength_json": str(json_path), "test_strength_markdown": str(markdown_path)}
