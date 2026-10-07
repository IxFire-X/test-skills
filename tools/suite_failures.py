"""Failure triage of a suite run (wave 3, F) — decided by code, not by a model.

Per test method, from the outcomes of the run and its repeats:

* does not compile, or its own code broke (``broken``: an error other than an assertion) while
  the case's requirements did not change — ``REPAIR`` (one try, ``suite-update-v1`` only);
* failed on an assertion and a linked requirement changed — ``UPDATE``;
* failed on an assertion with neither the requirement nor the test changed — the behaviour
  changed without a specification: ``QUARANTINE`` with reason ``BEHAVIOR_CHANGED_WITHOUT_SPEC``,
  a question for the analysts and a bug report draft from the case; the expectation stays;
* the outcome differs between repeats — ``QUARANTINE`` with reason ``FLAKY`` (instability or
  environment), never a defect;
* passed — ``PASS``, or ``FIXED`` when it was quarantined (its mark can be released).

A method a person edited (its slice digest differs from the manifest) is never repaired,
updated or quarantined by the package: the decision becomes a proposal in the PR description.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

STATUSES = ("passed", "failed", "broken", "skipped")


def classify(runs: Sequence[str], *, compile_error: bool = False, requirement_changed: bool = False, edited_by_person: bool = False,
             quarantined: bool = False) -> dict[str, Any]:
    """``{"outcome", "reason", "proposal_only"}`` for one method; ``runs`` are its statuses, first run first."""
    if any(status not in STATUSES for status in runs):
        raise ValueError(f"unknown test status in {list(runs)}")
    ran = [status for status in runs if status != "skipped"]
    if compile_error:
        outcome, reason = ("UPDATE", "REQUIREMENT_CHANGED") if requirement_changed else ("REPAIR", "TEST_DOES_NOT_COMPILE")
    elif not ran:
        outcome, reason = "NOT_RUN", None
    elif all(status == "passed" for status in ran):
        outcome, reason = ("FIXED", None) if quarantined else ("PASS", None)
    elif any(status == "passed" for status in ran):
        outcome, reason = "QUARANTINE", "FLAKY"
    elif requirement_changed:
        outcome, reason = "UPDATE", "REQUIREMENT_CHANGED"
    elif all(status == "broken" for status in ran):
        outcome, reason = "REPAIR", "TEST_CODE_ERROR"
    else:
        outcome, reason = "QUARANTINE", "BEHAVIOR_CHANGED_WITHOUT_SPEC"
    return {"outcome": outcome, "reason": reason, "proposal_only": bool(edited_by_person and outcome in {"REPAIR", "UPDATE", "QUARANTINE", "FIXED"})}


def _one_line(text: str | None, limit: int = 500) -> str:
    """A failure message on one line (AssertJ puts \"expected … but was …\" on the lines after the description)."""
    return " ".join(str(text or "").split())[:limit]


def _expected_text(step: Mapping[str, Any]) -> list[str]:
    return [str(item.get("text") or "").strip() for item in step.get("expectations") or [] if str(item.get("text") or "").strip()]


def bug_report(case: Mapping[str, Any], *, locator: str, failure: str | None, run_id: str) -> str:
    """A bug report draft from the case: its steps and expectations, and what the run observed."""
    lines = [f"### Черновик баг-репорта: {case.get('title') or case.get('case_id')}", "",
             f"- Кейс: `{case.get('case_id')}`; требования: {', '.join(f'`{item}`' for item in case.get('requirement_ids') or []) or '—'}",
             f"- Тест: `{locator}`, прогон `{run_id}`",
             "- Требование и тест не менялись: поведение продукта изменилось без спецификации. Тест в карантине до решения аналитика.", ""]
    preconditions = [str(item) for item in case.get("preconditions") or [] if str(item).strip()]
    if preconditions:
        lines += ["**Предусловия**", *[f"- {item}" for item in preconditions], ""]
    lines.append("**Шаги**")
    expected = []
    for number, step in enumerate(case.get("steps") or [], start=1):
        lines.append(f"{number}. {str(step.get('action') or '').strip()}" + (f" Данные: {str(step.get('test_data')).strip()}" if step.get("test_data") else ""))
        expected += _expected_text(step)
    expected_lines = [f"- {text.splitlines()[0]}" for text in expected] or ["- (см. кейс)"]
    lines += ["", "**Ожидается**", *expected_lines, "", "**Фактически**",
              f"- {_one_line(failure) or 'тест упал на проверке'}", ""]
    return "\n".join(lines)


def analyst_question(case: Mapping[str, Any], *, failure: str | None) -> str:
    observed = _one_line(failure, 300) or "проверка не прошла"
    return (f"Кейс {case.get('case_id')} «{case.get('title')}» перестал проходить, хотя требование не менялось ({observed}). "
            f"Это дефект продукта или новое поведение, которое нужно описать в требовании?")
