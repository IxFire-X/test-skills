"""The result of ``suite-update-v1`` for people (wave 3, R): ``pr-description.md`` and a patch.

Both are built from the run's facts only — no clock, no host paths, sorted everywhere — so the
same facts give the same bytes (W3-Р11).  The package commits nothing: the working tree holds the
changes, the description and the patch are what a person or the project's CI turns into a PR.
"""
from __future__ import annotations

import difflib
from typing import Any, Mapping, Sequence

_REASONS = {
    "ASSERTION_FAILED": "проверка не прошла", "BEHAVIOR_CHANGED_WITHOUT_SPEC": "поведение изменилось без изменения требования",
    "FLAKY": "результат меняется между повторами", "ENVIRONMENT": "окружение", "REPAIR_FAILED": "ремонт не помог",
}


def _key_label(key: str) -> str:
    return key.split("#", 1)[-1]


def _code(text: str) -> str:
    """Inline code that survives backticks inside (a heading with `/path`)."""
    return f"`` {text} ``" if "`" in text else f"`{text}`"


def unified_patch(before: Mapping[str, bytes | None], after: Mapping[str, bytes | None]) -> str:
    """A git-style unified diff of every changed text file (``None`` — the file does not exist)."""
    out: list[str] = []
    for path in sorted(set(before) | set(after)):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        old_lines = [] if old is None else old.decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
        new_lines = [] if new is None else new.decode("utf-8").replace("\r\n", "\n").splitlines(keepends=True)
        old_name = "/dev/null" if old is None else f"a/{path}"
        new_name = "/dev/null" if new is None else f"b/{path}"
        out.append(f"diff --git a/{path} b/{path}\n")
        for line in difflib.unified_diff(old_lines, new_lines, old_name, new_name, n=3):
            out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def _case_diff(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> list[str]:
    """The human fields of one case, old against new, as a small diff."""
    def human(case: Mapping[str, Any] | None) -> list[str]:
        if case is None:
            return []
        lines = [f"Название: {case.get('title', '')}"]
        for number, step in enumerate(case.get("steps") or [], start=1):
            lines.append(f"Шаг {number}: {str(step.get('action') or '').strip()}")
            if step.get("test_data"):
                lines.append(f"  Данные: {str(step['test_data']).strip()}")
            for expectation in step.get("expectations") or []:
                lines += [f"  Ожидается: {text}" for text in str(expectation.get("text") or "").strip().splitlines()[:6]]
        return lines

    return [line.rstrip("\n") for line in difflib.unified_diff(human(before), human(after), "было", "стало", n=1, lineterm="")][2:]


def pr_description(facts: Mapping[str, Any]) -> str:
    """``facts``: the run's summary (see ``suite_update.summary_facts``)."""
    requirements = facts["requirements"]
    cases = facts["cases"]
    tests = facts["tests"]
    lines = [f"# Обновление набора тестов `{facts['suite_id']}`", "",
             f"Профиль `suite-update-v1`, прогон `{facts['run_id']}`; набор `{facts['suite_dir']}`. Пакет ничего не коммитит: изменения лежат в рабочем дереве, "
             f"patch — `{facts['patch_name']}`.", "", "## Итог", "",
             "| Что | Сколько |", "| --- | --- |",
             f"| Требования: добавлено / изменено / удалено / переименовано | {len(requirements['added'])} / {len(requirements['changed'])} / "
             f"{len(requirements['removed'])} / {len(requirements['renamed'])} |",
             f"| Кейсы: обновлено / новых / выведено / без изменений | {len(cases['changed'])} / {len(cases['new'])} / {len(cases['retired'])} / {cases['unchanged']} |",
             f"| Тесты: обновлено / новых / отремонтировано / удалено | {len(tests['updated'])} / {len(tests['new'])} / {len(tests['repaired'])} / {len(tests['removed'])} |",
             f"| Прогон набора: методов / прошло / упало / карантин / снят карантин | {tests['run']['methods']} / {tests['run']['passed']} / {tests['run']['failed']} / "
             f"{len(facts['quarantine'])} / {len(facts['released'])} |", ""]
    if tests["run"].get("not_run"):
        lines.insert(-1, f"| Прогон набора: не выполнено методов (не считаются прошедшими) | {tests['run']['not_run']} |")
    strength = facts.get("strength")
    if strength:
        lines.insert(-1, f"| Доля убитых мутантов: было → стало | {strength['before']} → {strength['after']} |")
    stop = facts.get("stop")
    if stop:
        lines += [f"## Остановка: `{stop['reason']}`", "",
                  "Прогон остановлен до конца: изменения ниже не проверены прогоном набора, манифест набора не обновлён."
                  + (" Тесты набора не выполнялись — цифры прогона в таблице выше не являются результатом." if stop["reason"] in {"SUITE_NOT_RUN", "SUITE_RUN_NONZERO_EXIT"} else ""),
                  "", f"Причина: {' '.join(str(stop.get('message') or '').split())[:1200]}", ""]
    if facts.get("migration") and facts["migration"].get("status") == "MIGRATED":
        lines += ["## Миграция набора", "", f"Формат {facts['migration']['from_format']} → {facts['migration']['to_format']}: "
                  + "; ".join(facts["migration"].get("steps") or []) + ".", ""]
    if facts.get("review_blocked"):
        lines += ["## Ревью не пропустило обновление", "", "Изменения кейсов не применены: ревью нашло блокирующие замечания.", ""]
        lines += [f"- **{row['severity']}** `{', '.join(row.get('related_ids') or [])}`: {row.get('message', '')}" for row in facts["review_findings"]] + [""]
    if any(requirements[name] for name in ("added", "changed", "removed", "renamed")):
        lines += ["## Требования (по ключам)", ""]
        lines += [f"- добавлено: {_code(_key_label(key))}" for key in requirements["added"]]
        lines += [f"- изменено: {_code(_key_label(key))}" for key in requirements["changed"]]
        lines += [f"- удалено: {_code(_key_label(key))}" for key in requirements["removed"]]
        lines += [f"- переименовано: {_code(_key_label(row['from']))} → {_code(_key_label(row['to']))}" for row in requirements["renamed"]]
        lines.append("")
    if cases["changed"] or cases["new"] or cases["retired"]:
        lines += ["## Кейсы", ""]
        for case_id in cases["changed"] + cases["new"]:
            before, after = facts["case_texts"].get(case_id, (None, None))
            title = (after or before or {}).get("title", "")
            lines += [f"### {case_id} — {'новый' if case_id in cases['new'] else 'обновлён'}: {title}", "", "```diff", *_case_diff(before, after), "```", ""]
        for row in cases["retired"]:
            lines.append(f"- выведен `{row['case_id']}`: {row.get('reason') or 'требование удалено'}")
        if cases["retired"]:
            lines.append("")
    if any(tests[name] for name in ("updated", "new", "repaired", "removed")):
        lines += ["## Тесты", "", "Diff методов — в patch.", ""]
        lines += [f"- обновлён `{locator}`" for locator in tests["updated"]]
        lines += [f"- новый `{locator}`" for locator in tests["new"]]
        lines += [f"- отремонтирован `{locator}` (ожидания не менялись)" for locator in tests["repaired"]]
        lines += [f"- удалён `{locator}`" for locator in tests["removed"]]
        lines.append("")
    notes = [row for row in facts.get("review_notes") or [] if row.get("severity") in {"BLOCKING", "WARNING"}]
    if notes and not facts.get("review_blocked"):
        lines += ["## Замечания ревью (не блокируют)", ""]
        lines += [f"- **{row['severity']}** {row.get('code', '')} `{', '.join(row.get('related_ids') or [])}`: {row.get('message', '')}" for row in notes] + [""]
    if facts["quarantine"]:
        lines += ["## Карантин", "", "| Тест | Кейс | Причина | Ссылка |", "| --- | --- | --- | --- |"]
        lines += [f"| `{row['locator']}` | {', '.join(row['case_ids'])} | {_REASONS.get(row['reason'], row['reason'])} | {row['ref']} |" for row in facts["quarantine"]]
        lines.append("")
        for row in facts["quarantine"]:
            if row.get("bug_report"):
                lines += [row["bug_report"], ""]
    if facts["released"]:
        lines += ["## Снят карантин", ""] + [f"- `{locator}`: теперь проходит" for locator in facts["released"]] + [""]
    if facts["questions"]:
        lines += ["## Вопросы аналитикам", ""] + [f"- {question}" for question in facts["questions"]] + [""]
    if facts["proposals"]:
        lines += ["## Ручные правки: предложения (пакет их не перезаписал)", ""] + [f"- {proposal}" for proposal in facts["proposals"]] + [""]
    if facts.get("strength_drops"):
        lines += ["## Сила тестов упала", ""] + [f"- `{row['case_id']}`: убито {row['before']} → {row['after']}" for row in facts["strength_drops"]] + [""]
    lines += ["## Что сделать человеку", "", "1. Просмотреть изменения в рабочем дереве (или применить patch к чистой копии).",
              "2. Решить по вопросам аналитикам и карантину.", "3. Сделать коммит и PR: пакет этого не делает.", ""]
    return "\n".join(lines)
