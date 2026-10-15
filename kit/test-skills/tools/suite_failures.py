"""Failure triage of a suite run (wave 3, F) — decided by code, not by a model.

Per test method, from the outcomes of the run and its repeats:

* does not compile, or its own code broke (``broken``: an error other than an assertion, raised in
  the test's code, not the product's) while the case's requirements did not change — ``REPAIR``
  (one try, ``suite-update-v1`` only); an exception out of the product is a behaviour failure;
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

import re
from typing import Any, Mapping, Sequence

STATUSES = ("passed", "failed", "broken", "skipped")


def classify(runs: Sequence[str], *, compile_error: bool = False, requirement_changed: bool = False, edited_by_person: bool = False,
             quarantined: bool = False, product_error: bool = False) -> dict[str, Any]:
    """``{"outcome", "reason", "proposal_only"}`` for one method; ``runs`` are its statuses, first run first.

    ``product_error``: the errors came out of the product's code (``suite_run.error_origin``) — a
    behaviour failure like a failed assertion, never a repair of the test.
    """
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
    elif all(status == "broken" for status in ran) and not product_error:
        outcome, reason = "REPAIR", "TEST_CODE_ERROR"
    else:
        outcome, reason = "QUARANTINE", "BEHAVIOR_CHANGED_WITHOUT_SPEC"
    return {"outcome": outcome, "reason": reason, "proposal_only": bool(edited_by_person and outcome in {"REPAIR", "UPDATE", "QUARANTINE", "FIXED"})}


def _one_line(text: str | None, limit: int = 500) -> str:
    """A failure message on one line (AssertJ puts \"expected … but was …\" on the lines after the description)."""
    return " ".join(str(text or "").split())[:limit]


def _expected_text(step: Mapping[str, Any]) -> list[str]:
    return [str(item.get("text") or "").strip() for item in step.get("expectations") or [] if str(item.get("text") or "").strip()]


_ASSERTION_ID = re.compile(r"\bASSERT-[A-Za-z0-9]+-\d+\b")
_OBSERVED = re.compile(r"but was:?\s*<?([^<>\s]+)>?")
_HTTP_REQUEST = re.compile(r"\((?:GET|POST|PUT|PATCH|DELETE) /")
_HTTP_STATUS = {200: "страница или форма (200)", 201: "создано (201)", 204: "без содержимого (204)", 302: "перенаправление (302)",
                400: "ошибка запроса (400)", 403: "доступ запрещён (403)", 404: "не найдено (404)", 500: "ошибка сервера (500)"}


def _failed_check(case: Mapping[str, Any], failure: str | None) -> tuple[Mapping[str, Any], Mapping[str, Any]] | None:
    """The expectation and the assertion the failure names (``[ASSERT-… name]`` of the generated test), or None."""
    named = set(_ASSERTION_ID.findall(failure or ""))
    for step in case.get("steps") or []:
        for expectation in step.get("expectations") or []:
            for assertion in expectation.get("assertions") or []:
                if assertion.get("assertion_id") in named:
                    return expectation, assertion
    return None


def _observed_words(assertion: Mapping[str, Any], failure: str | None) -> str:
    match = _OBSERVED.search(_one_line(failure, 2000))
    value = match.group(1).strip(".,;") if match else None
    name = str((assertion.get("actual") or {}).get("name") or "значение")
    if value is None:
        return _one_line(failure) or "проверка не прошла"
    if name == "response_status" and value.isdigit() and int(value) in _HTTP_STATUS:
        return _HTTP_STATUS[int(value)]
    return f"{name} = {value}"


def _violated(case: Mapping[str, Any], document: Mapping[str, Any] | None) -> list[str]:
    """The case's own requirements: those shared with the fewest cases of the document (the general ones go to every case)."""
    ids = list(case.get("requirement_ids") or [])
    if not document or not ids:
        return ids
    spread: dict[str, int] = {}
    for other in document.get("test_cases") or []:
        for identifier in set(other.get("requirement_ids") or []):
            spread[identifier] = spread.get(identifier, 0) + 1
    least = min(spread.get(identifier, 0) for identifier in ids)
    return sorted(identifier for identifier in ids if spread.get(identifier, 0) == least)


def _setup_steps(steps: Sequence[Mapping[str, Any]]) -> int:
    """How many leading steps only prepare the scenario (no inputs, no request to the application): they go to the preconditions."""
    count = 0
    for step in steps:
        if step.get("inputs") or _HTTP_REQUEST.search(str(step.get("action") or "")):
            break
        count += 1
    return count if count < len(steps) else 0


def bug_report(case: Mapping[str, Any], *, locator: str, failure: str | None, run_id: str, first_run: bool = False,
               document: Mapping[str, Any] | None = None) -> str:
    """A bug report draft from the case: its steps as in the Zephyr export, the violated requirement and what the run observed.

    ``first_run``: the test was generated from the requirement in this run (``local-pilot-v1``),
    so nothing "changed" — the product does not meet the requirement's expectation.
    ``document``: the case's document; with it the draft names the violated requirement by its text
    (the case's own requirement, not the general ones every case shares).
    """
    lines = [f"### Черновик баг-репорта: {case.get('title') or case.get('case_id')}", "",
             f"- Кейс: `{case.get('case_id')}`; тест: `{locator}`, прогон `{run_id}`",
             ("- Тест построен по требованию в этом прогоне и упал: продукт не выполняет ожидание требования. Тест в карантине до решения аналитика."
              if first_run else "- Требование и тест не менялись: поведение продукта изменилось без спецификации. Тест в карантине до решения аналитика."), ""]
    texts = {row.get("requirement_id"): str(row.get("text") or "").strip() for row in (document or {}).get("requirements") or []}
    violated = _violated(case, document)
    if violated:
        lines += ["**Нарушенное требование**", *[f"- `{identifier}`" + (f": {texts[identifier]}" if texts.get(identifier) else "") for identifier in violated], ""]
    check = _failed_check(case, failure)
    lines += ["**Ожидалось / Получено**",
              f"- Ожидалось: {str(check[0].get('text') or '').strip() if check else 'см. ожидаемые результаты шагов'}",
              f"- Получено: {_observed_words(check[1], failure) if check else (_one_line(failure) or 'тест упал на проверке')}", ""]
    steps = list(case.get("steps") or [])
    setup = _setup_steps(steps)
    preconditions = [str(item) for item in case.get("preconditions") or [] if str(item).strip()]
    preconditions += [f"Подготовка (шаг {number}): {str(step.get('action') or '').strip()}" for number, step in enumerate(steps[:setup], start=1)]
    if preconditions:
        lines += ["**Предусловия**", *[f"- {item}" for item in preconditions], ""]
    lines.append("**Шаги**")
    lines.append("")
    for number, step in enumerate(steps, start=1):
        if number <= setup:
            continue
        lines.append(f"{number}. {str(step.get('action') or '').strip()}")
        data = [line.strip() for line in str(step.get("test_data") or "").splitlines() if line.strip()]
        if data:
            lines.append("   - Тестовые данные:")
            lines += [f"     - {line}" for line in data]
        expected = _expected_text(step)
        if expected:
            lines.append("   - Ожидаемый результат:")
            lines += [f"     - {line}" for text in expected for line in text.splitlines() if line.strip()]
    lines += ["", "**Доказательство (сообщение теста)**", "", "```text", (failure or "тест упал на проверке").strip(), "```", ""]
    return "\n".join(lines)


def analyst_question(case: Mapping[str, Any], *, failure: str | None, first_run: bool = False) -> str:
    observed = _one_line(failure, 300) or "проверка не прошла"
    if first_run:
        return (f"Кейс {case.get('case_id')} «{case.get('title')}» построен по требованию и не проходит ({observed}). "
                f"Это дефект продукта или требование нужно уточнить?")
    return (f"Кейс {case.get('case_id')} «{case.get('title')}» перестал проходить, хотя требование не менялось ({observed}). "
            f"Это дефект продукта или новое поведение, которое нужно описать в требовании?")
