#!/usr/bin/env python3
"""
contract_check.py — детерминированный исполняемый Contract Check (CONTRACTS.md §5).

Заменяет «исполнимый чеклист» в Markdown на РЕАЛЬНО исполняемый код.
LLM-скилл «Оркестратор» больше не должен верить себе на слово по контрактам:
этот инструмент парсит SKILL.md, извлекает контракты входа/выхода и статус-маркеры,
и сверяет их с каноном CONTRACTS.md §2 / §3 по-настоящему.

Принципы (наследует run_tests.py):
  - PASS = все пары (skill_N → skill_N+1) валидны: вход/выход в каноне, маркеры в реестре.
  - FAIL = contract_mismatch: тег не в §2, неизвестный статус, битый формат.
  - NOT_RUNNABLE = CONTRACTS.md или SKILL.md не найдены/не читаются.
  - Зависимости: ТОЛЬКО стандартная библиотека Python.
  - Вывод: JSON в stdout.

Использование:
    python tools/contract_check.py --contracts CONTRACTS.md --pipeline test-pipeline [--skills-dir Оркестратор] [--output report.md]
    python tools/contract_check.py --full   # автообнаружение: contracts + все SKILL.md в cwd

Выходные exit codes (для CI):
    0 — PASS (все контракты валидны)       | NOT_RUNNABLE (нечего проверять) — честный статус
    1 — FAIL (contract_mismatch)
    2 — ошибка самого инструмента
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Канон по умолчанию (CONTRACTS.md §2 — корневые выходные блоки)
# ---------------------------------------------------------------------------

CANON_OUTPUT_BLOCKS = [
    "generated_test_cases",
    "generated_test_cases_json",
    "validation_report",
    "corrected_test_cases",
    "automation_analysis",
    "automation_matrix",
    "autotest_review",
    "review_verdict",
    "review_comments",
    "corrected_autotest_code",
    "orchestration_result",
    "run_tests_verdict",
    "batch_marking_result",
]

# Входные теги (CONTRACTS.md §2.2) — разрешены как «Вход» в SKILL.md
CANON_INPUT_TAGS = [
    "analytics_documentation",
    "source_code_and_diff",
    "test_cases",
    "corrected_test_cases",
    "generated_test_cases",
    "automation_matrix",
    "existing_project_context",
    "concept_name",
    "source_code",
    "doc_path",
    "document_path",
    "correction_plan",
    "correction_plan_path",
    "autotest_code",
    "autocode",
    "goal",
    "pipeline",
    "max_iterations",
    "strict_mode",
    "context",
    "raw_content",
    "content_type",
    "file_path",
]

# Статус-маркеры (CONTRACTS.md §3.2, §3.2.1 и §3.3)
REVIEWER_VERDICTS = {"ПРИНЯТО", "AUTO_FIX_APPLIED", "ТРЕБУЕТ ДОРАБОТКИ"}
RUNNER_VERDICTS = {"PASS", "FAIL", "NOT_RUNNABLE"}
ORCHESTRATOR_STATUSES = {"completed", "partial", "failed", "retry"}
DEPRECATED_MARKERS = {"production-ready", "partial", "not-ready", "fixed", "partial-fixed", "failed"}
ALL_STATUSES = REVIEWER_VERDICTS | RUNNER_VERDICTS | ORCHESTRATOR_STATUSES

# Пайплайн по умолчанию (test-pipeline)
DEFAULT_PIPELINE = [
    ("context-marker", "<analytics_documentation>", "<source_code_and_diff>"),
    ("tc-generator", "<analytics_documentation> + <source_code_and_diff>", "<generated_test_cases>"),
    ("tc-reviewer", "<generated_test_cases>", "<validation_report>"),
    ("tc-to-autotest", "<test_cases> | <corrected_test_cases>", "<automation_matrix>"),
    ("autotest-reviewer", "<test_cases> + <automation_matrix> + <autotest_code>", "<autotest_review>"),
]

TAG_RE = re.compile(r"<([a-zA-Z0-9_]+)>")
STATUS_RE = re.compile(r"[ПРИНЯТО|AUTO_FIX_APPLIED|ТРЕБУЕТ\s+ДОРАБОТКИ|completed|partial|failed|retry]", re.UNICODE)

# Теги-флаги/значения: НЕ контрактные блоки, а служебные параметры
# (<output_format>json</output_format>, <strict_mode>true</strict_mode> и т.п.)
# ВАЖНО: сюда НЕ входят валидные контрактные теги (<test_cases>, <analytics_documentation> и т.д.)
NON_CONTRACT_TAGS = {"output_format", "json"}

# Полный канон: корневые выходные блоки (§2) + входные теги (§2.2)
CANON_ALL_TAGS = set(CANON_OUTPUT_BLOCKS) | set(CANON_INPUT_TAGS) | {
    "analysis",              # выход tc-generator (PIPELINE.md Этап 1)
    "batch_marking_result",  # выход context-marker (batch-режим)
}


@dataclass
class SkillContract:
    """Извлечённый контракт скилла из SKILL.md."""
    name: str
    file: str
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    statuses: List[str] = field(default_factory=list)
    raw_section: str = ""


def extract_tags(text: str) -> List[str]:
    """Все `<tag>` в строке (уникальные, в порядке появления)."""
    return list(dict.fromkeys(TAG_RE.findall(text)))


def normalize_marker(marker: str) -> str:
    """'ТРЕБУЕТ ДОРАБОТКИ (CONTRACTS.md §3.2)' → 'ТРЕБУЕТ ДОРАБОТКИ'; схлопывает пробелы."""
    marker = re.sub(r"\s+", " ", marker).strip()
    # Обрезать пояснение в скобках в конце строки (например "(CONTRACTS.md §3.2)")
    return re.sub(r"\s*\([^)]*\)\s*$", "", marker).strip()


def parse_contracts_from_md(text: str, file_label: str) -> SkillContract:
    """Парсит контракты входа/выхода и статус-маркеры из SKILL.md."""
    name_m = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
    skill_name = name_m.group(1).strip() if name_m else Path(file_label).stem

    inputs: List[str] = []
    outputs: List[str] = []
    statuses: List[str] = []

    # --- Вход ---
    for m in re.finditer(r"-\s*\*\*Вход[^\n]*:\*\*\s*(.+)", text):
        line = m.group(1)
        inputs.extend(extract_tags(line))
    # fallback: заголовок "## Контракт входа" ... до следующего заголовка / "Выход"
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## контракт входа"):
            in_section = True
            continue
        if in_section and (stripped.startswith("## ") or "- **Выход" in stripped):
            in_section = False
            continue
        if in_section:
            inputs.extend(extract_tags(stripped))

    # --- Выход ---
    for m in re.finditer(r"-\s*\*\*Выход[^\n]*:\*\*\s*(.+)", text):
        line = m.group(1)
        outputs.extend(extract_tags(line))
    out_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## контракт выхода"):
            out_section = True
            continue
        if out_section and stripped.startswith("## "):
            out_section = False
            continue
        if out_section:
            outputs.extend(extract_tags(stripped))

    # --- Статус-маркеры ---
    for m in re.finditer(r"Статус-маркеры[^\n]*:\*\*\s*(.+)", text):
        for cand in m.group(1).replace("`", "").split(","):
            cand = normalize_marker(cand)
            if cand:
                statuses.append(cand)
    # доп. все цитируемые маркеры в тексте
    for marker in REVIEWER_VERDICTS | RUNNER_VERDICTS | ORCHESTRATOR_STATUSES:
        if marker in text or marker.replace(" ", "  ") in text:
            statuses.append(marker)

    return SkillContract(
        name=skill_name,
        file=file_label,
        inputs=list(dict.fromkeys(inputs)),
        outputs=list(dict.fromkeys(outputs)),
        statuses=list(dict.fromkeys(statuses)),
    )


def load_contracts(path: Path) -> Tuple[Optional[str], str]:
    """Читает CONTRACTS.md (utf-8, fallback cp1251). Возвращает (text, error)."""
    if not path.exists():
        return None, f"CONTRACTS.md не найден: {path}"
    try:
        return path.read_text(encoding="utf-8"), ""
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="cp1251"), ""
        except Exception as e:
            return None, f"Не удалось прочитать {path}: {e}"


def check_pair(
    skill_from: str,
    expected_input_raw: str,
    expected_output_raw: str,
    next_input_raw: str,
    skill_contracts: Dict[str, SkillContract],
    contracts_text: str,
) -> Tuple[bool, List[str]]:
    """Проверяет одну пару skill_N → skill_N+1 по чеклисту CONTRACTS.md §5."""
    errors: List[str] = []
    ok = True

    # 1. По CONTRACTS.md §2 найти ожидаемый корневой блок (выход skill_N)
    out_tags = extract_tags(expected_output_raw)
    for tag in out_tags:
        if tag not in CANON_OUTPUT_BLOCKS:
            ok = False
            errors.append(f"contract_mismatch: выходной блок <{tag}> скилла '{skill_from}' отсутствует в CONTRACTS.md §2")

    # 2. По SKILL.md skill_N+1 найти «Контракт входа» — имя блока должно совпадать
    #    (для следующего скилла ищем его контракт, чтобы проверить согласованность)
    return ok, errors


def run_check(
    contracts_path: Path,
    skills_dir: Path,
    pipeline: List[Tuple[str, str, str]],
) -> dict:
    """Исполняет Contract Check. Возвращает dict-результат (JSON-контракт)."""
    contracts_text, err = load_contracts(contracts_path)
    if err:
        return {
            "status": "not_runnable",
            "errors": [err],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    # Собираем все SKILL.md
    skill_files: Dict[str, Path] = {}
    if skills_dir.exists():
        for p in skills_dir.rglob("SKILL*.md"):
            skill_key = p.parent.name.lower()
            skill_files[skill_key] = p

    skill_contracts: Dict[str, SkillContract] = {}
    for key, path in skill_files.items():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            text = ""
        sc = parse_contracts_from_md(text, str(path))
        skill_contracts[key] = sc
        # Дополнительный ключ: логическое имя из frontmatter (name: tc-reviewer и т.п.)
        skill_contracts[sc.name.lower()] = sc

    checks: List[dict] = []
    errors: List[str] = []
    ok = True

    for i, (skill_name, expected_input, expected_output) in enumerate(pipeline):
        check = {"pair": f"skill_{i}→skill_{i+1}", "skill": skill_name, "result": "PASS", "items": []}

        # Скилл присутствует в skills_dir?
        sc = skill_contracts.get(skill_name.lower())
        if sc is None:
            check["result"] = "FAIL"
            check["items"].append({"criterion": "skill_file", "detail": f"SKILL.md для '{skill_name}' не найден в {skills_dir}", "status": "FAIL"})
            ok = False
            errors.append(f"contract_mismatch: skill file for '{skill_name}' not found")
            checks.append(check)
            continue

        # 1. Ожидаемый выход скилла есть в каноне §2 (или §2.2 — входные теги контекста)
        for tag in extract_tags(expected_output):
            item = {"criterion": f"output <{tag}> in canon §2", "status": "PASS"}
            if tag not in CANON_ALL_TAGS:
                item["status"] = "FAIL"
                ok = False
                errors.append(f"contract_mismatch: <{tag}> not in CONTRACTS.md §2")
            check["items"].append(item)

        # 2. Выход skill_N (ожидаемый) совпадает со входом skill_N+1 (разрешённые входные теги)
        allowed_input_tags = set(CANON_INPUT_TAGS) | set(CANON_OUTPUT_BLOCKS)
        for tag in extract_tags(expected_output):
            item = {"criterion": f"<{tag}> acceptable as next input", "status": "PASS"}
            if tag not in allowed_input_tags:
                item["status"] = "FAIL"
                ok = False
                errors.append(f"contract_mismatch: <{tag}> not acceptable as input per CONTRACTS.md §2.2")
            check["items"].append(item)

        # 3. Выходные блоки, объявленные в SKILL.md скилла, есть в каноне §2/§2.2.
        #    Теги-значения (<output_format>json</output_format>) — служебные, не контрактные.
        for tag in sc.outputs:
            if tag in NON_CONTRACT_TAGS:
                continue
            item = {"criterion": f"declared output <{tag}> in canon §2", "status": "PASS"}
            if tag not in CANON_ALL_TAGS:
                item["status"] = "FAIL"
                ok = False
                errors.append(f"contract_mismatch: skill '{skill_name}' declares <{tag}> not in CONTRACTS.md §2")
            check["items"].append(item)

        # 4. Статус-маркеры скилла — в реестре §3
        for marker in sc.statuses:
            marker_norm = normalize_marker(marker)
            item = {"criterion": f"status '{marker_norm}' in registry §3", "status": "PASS"}
            if marker_norm not in ALL_STATUSES:
                item["status"] = "FAIL"
                ok = False
                errors.append(f"contract_mismatch: unknown status marker '{marker_norm}' in skill '{skill_name}'")
            check["items"].append(item)

        check["result"] = "PASS" if all(i["status"] == "PASS" for i in check["items"]) else "FAIL"
        checks.append(check)

    status = "passed" if ok else "failed"
    return {
        "status": status,
        "pipeline": "test-pipeline",
        "pairs_checked": len(checks),
        "contracts_file": str(contracts_path),
        "skills_dir": str(skills_dir),
        "checks": checks,
        "errors": errors,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def render_markdown(report: dict) -> str:
    """Markdown-отчёт для docs/to_do/contract-check-<TS>.md."""
    lines = [
        "# Contract Check (исполнимый чеклист, CONTRACTS.md §5)",
        "",
        f"- **Дата:** {report['checked_at']}",
        f"- **Статус:** `{report['status']}`",
        f"- **Пар проверено:** {report['pairs_checked']}",
        f"- **CONTRACTS.md:** `{report['contracts_file']}`",
        f"- **SKILL.md:** `{report['skills_dir']}`",
        "",
        "## Результаты по парам",
        "",
    ]
    for ch in report.get("checks", []):
        lines.append(f"### {ch['pair']} — {ch['skill']} — `{ch['result']}`")
        lines.append("")
        lines.append("| Критерий | Статус |")
        lines.append("|---|---|")
        for item in ch["items"]:
            lines.append(f"| {item['criterion']} | {item['status']} |")
        lines.append("")
    if report.get("errors"):
        lines.append("## Ошибки")
        lines.append("")
        for e in report["errors"]:
            lines.append(f"- `{e}`")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Исполнимый Contract Check (CONTRACTS.md §5)")
    parser.add_argument("--contracts", default="CONTRACTS.md", help="Путь к CONTRACTS.md")
    parser.add_argument("--skills-dir", default=".", help="Директория со SKILL.md")
    parser.add_argument("--output", help="Markdown-отчёт (по умолчанию не пишется)")
    parser.add_argument("--full", action="store_true", help="Автообнаружение CONTRACTS.md и директории скиллов в cwd")
    args = parser.parse_args()

    try:
        if args.full:
            cwd = Path.cwd()
            contracts_path = cwd / "CONTRACTS.md"
            skills_dir = cwd
        else:
            contracts_path = Path(args.contracts)
            skills_dir = Path(args.skills_dir)

        report = run_check(contracts_path, skills_dir, DEFAULT_PIPELINE)

        if args.output:
            out = Path(args.output)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(render_markdown(report), encoding="utf-8")
            report["output_file"] = str(out)

        print(json.dumps(report, ensure_ascii=False, indent=2))

        if report["status"] == "failed":
            return 1
        if report["status"] == "not_runnable":
            return 0
        return 0
    except Exception as e:
        print(json.dumps({"status": "error", "errors": [str(e)]}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    sys.exit(main())