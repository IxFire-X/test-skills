"""Check human wording and report optional scenario-design recommendations."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from tools.json_cli import JsonArgumentParser, emit_error
from tools.schema_validation import load_json_strict

HTTP_LEAD = re.compile(r"^\s*(GET|POST|PUT|PATCH|DELETE)\b", re.I)
TITLE_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё_-]+")
INTERNAL_CALL = re.compile(r"\breverse\s*\(|\b[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+\s*\(", re.I)
RAW_BINDING = re.compile(
    r"(?:^|\n)\s*(?:body:/|(?:path|query|header|arg):[^\s=]+\s*=|env:[A-Za-z_]|secret:[^\s])|"
    r"\$\{(?:STEP|INPUT|OUTPUT|ASSERT|EXP)-",
    re.I,
)
MACHINE_EXPECTED = re.compile(
    r"(?:^|\n)\s*(?:Выход:|Проверка:|http_(?:body|status|header):?|project_result:)",
    re.I,
)
SETUP_LEAD = re.compile(r"^\s*(создать|завести|create|add)\s+\S+", re.I)
LOGIN_ONLY = re.compile(r"вошёл|вошел|logged in|аутентифиц", re.I)
ACTOR = re.compile(r"роль|прав|role|permission|админ|оператор", re.I)
VAGUE_BODY = re.compile(r"как в коде|тело как", re.I)
VAGUE_VALUE = re.compile(
    r"\b(random|faker|любое значение|любое имя|any value|tbd)\b",
    re.I,
)
AMBIGUOUS_CODE = re.compile(r"\b([1245]\d\d)\s*(?:or|или|/)\s*([1245]\d\d)\b", re.I)
HTTP_ONLY = re.compile(r"^\s*(HTTP\s*)?[1245]\d\d\.?\s*$", re.I)
SUCCESS_ONLY = re.compile(r"^\s*(успешн\w*|ok|okay|готово|passed|success)\.?\s*$", re.I)
REFUSAL_CODE = re.compile(r"\b(400|403|404|409|422)\b")
REFUSAL_ORACLE = re.compile(
    r"на месте|отказ|отклон|запрет|ошибк|нельзя|нет карточ|не найден|"
    r"still there|refus|denied|forbidden|not found|cannot\b|403|404",
    re.I,
)
TRANSLATIONESE = re.compile(
    r"\b(?:project-native|runtime setup|manual gap|authorized sources|random port)\b",
    re.I,
)
GAP_RE = re.compile(r"пробел|нет ручки|не автоматиз|\bmanual\b", re.I)
def _expected_parts(step: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    if step.get("expected"):
        parts.append(str(step["expected"]))
    for item in step.get("expectations") or []:
        if isinstance(item, dict):
            if item.get("text"):
                parts.append(str(item["text"]))
        elif item:
            parts.append(str(item))
    return parts


def _expected_text(step: dict[str, Any]) -> str:
    return " ".join(_expected_parts(step)).strip()


def _data_text(step: dict[str, Any]) -> str:
    if step.get("test_data"):
        return str(step["test_data"])
    if step.get("data"):
        return str(step["data"])
    inputs = step.get("inputs") or []
    if not inputs:
        return ""
    return json.dumps(inputs, ensure_ascii=False)


def _is_gap(step: dict[str, Any]) -> bool:
    if step.get("manual_only"):
        return True
    if step.get("automation_blockers"):
        return True
    blob = f"{step.get('action', '')} {_data_text(step)} {_expected_text(step)} {step.get('manual_reason') or ''}"
    return bool(GAP_RE.search(blob))


def check_canonical(document: dict[str, Any]) -> dict[str, Any]:
    cases = list(document.get("test_cases") or [])
    problems: list[str] = []
    recommendations: list[str] = []
    if not cases:
        problems.append("no scenarios")

    live: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    for case in cases:
        steps = list(case.get("steps") or [])
        live_steps = [step for step in steps if not _is_gap(step)]
        gap_steps = [step for step in steps if _is_gap(step)]
        if live_steps:
            live.append((case, live_steps))
        if gap_steps and len(gap_steps) >= max(2, (len(steps) + 1) // 2) and not live_steps:
            case_id = case.get("case_id") or case.get("id") or "?"
            problems.append(
                f"{case_id}: mostly ПРОБЕЛ — put UI/plugin gaps in «Пробелы», not a test case"
            )

    bad_http_actions: list[str] = []
    internal_actions: list[str] = []
    missing_data: list[str] = []
    raw_data: list[str] = []
    machine_expected: list[str] = []
    secret_exposure: list[str] = []
    translationese: list[str] = []
    weak_titles: list[str] = []
    http_titles: list[str] = []
    http_only_expected: list[str] = []
    success_only: list[str] = []
    duplicate_titles: list[str] = []
    weak_refusal: list[str] = []
    setup_heavy: list[str] = []
    vague: list[str] = []
    vague_values: list[str] = []
    ambiguous: list[str] = []
    weak_pre: list[str] = []

    for case in cases:
        case_id = str(case.get("case_id") or case.get("id") or "?")
        title = case.get("title") or ""
        if len(title.strip()) < 12 or len(TITLE_WORD.findall(title)) < 2:
            weak_titles.append(case_id)
        if HTTP_LEAD.search(title) or "/api/" in title.lower() or "reverse(" in title.lower():
            http_titles.append(case_id)
        pres = case.get("preconditions") or []
        if pres and all(LOGIN_ONLY.search(item) for item in pres) and not any(ACTOR.search(item) for item in pres):
            weak_pre.append(case_id)
        if TRANSLATIONESE.search(" ".join((title, case.get("objective") or "", *pres))):
            translationese.append(case_id)

        live_steps = [step for step in (case.get("steps") or []) if not _is_gap(step)]
        setup_prefix = 0
        for step in live_steps:
            if SETUP_LEAD.search(step.get("action") or ""):
                setup_prefix += 1
            else:
                break
        if setup_prefix >= 3:
            setup_heavy.append(f"{case_id} ({setup_prefix} setup steps)")

        for step in live_steps:
            action = step.get("action") or ""
            data = _data_text(step)
            expected = _expected_text(step)
            operation = step.get("operation")
            if operation and operation.get("kind") == "http":
                signature = f"{operation['method']} {operation['path']}"
                if signature.casefold() not in action.casefold():
                    bad_http_actions.append(case_id)
                if not data.strip():
                    missing_data.append(case_id)
            if INTERNAL_CALL.search(action):
                internal_actions.append(case_id)
            if RAW_BINDING.search(data):
                raw_data.append(case_id)
            if MACHINE_EXPECTED.search(expected):
                machine_expected.append(case_id)
            secret_handles = [
                source.get("handle", "")
                for source in (
                    [item.get("source", {}) for item in step.get("inputs") or []]
                    + [
                        assertion.get("expected", {})
                        for expectation in step.get("expectations") or []
                        if isinstance(expectation, dict)
                        for assertion in expectation.get("assertions") or []
                    ]
                )
                if source.get("kind") == "secret_handle"
            ]
            if any(handle and handle in f"{action}\n{data}\n{expected}" for handle in secret_handles):
                secret_exposure.append(case_id)
            if TRANSLATIONESE.search(f"{action}\n{data}\n{expected}\n{step.get('manual_reason') or ''}"):
                translationese.append(case_id)
            if VAGUE_BODY.search(data) or VAGUE_BODY.search(action):
                vague.append(case_id)
            if VAGUE_VALUE.search(f"{data} {action}"):
                vague_values.append(case_id)
            if AMBIGUOUS_CODE.search(expected):
                ambiguous.append(case_id)
            if any(HTTP_ONLY.match(part) or SUCCESS_ONLY.match(part) for part in _expected_parts(step)):
                http_only_expected.append(case_id)
                if any(SUCCESS_ONLY.match(part) for part in _expected_parts(step)):
                    success_only.append(case_id)
            if REFUSAL_CODE.search(expected) and not REFUSAL_ORACLE.search(expected):
                weak_refusal.append(case_id)

    seen_titles: dict[str, str] = {}
    for case in cases:
        case_id = str(case.get("case_id") or case.get("id") or "?")
        key = re.sub(r"\s+", " ", (case.get("title") or "").lower()).strip()
        if not key:
            continue
        if key in seen_titles:
            duplicate_titles.append(f"{seen_titles[key]}/{case_id}")
        else:
            seen_titles[key] = case_id

    if bad_http_actions:
        problems.append("HTTP Action must contain the exact canonical method/path and no invented transport: " + ", ".join(dict.fromkeys(bad_http_actions)))
    if internal_actions:
        problems.append("Действие contains an internal helper/method call instead of a human operation: " + ", ".join(dict.fromkeys(internal_actions)))
    if missing_data:
        problems.append("HTTP step has empty Тестовые данные / запрос: " + ", ".join(dict.fromkeys(missing_data)))
    if raw_data:
        problems.append("Тестовые данные contains raw binding DSL instead of human parameters/JSON: " + ", ".join(dict.fromkeys(raw_data)))
    if machine_expected:
        problems.append("Ожидаемый результат contains output/assertion DSL instead of observable results: " + ", ".join(dict.fromkeys(machine_expected)))
    if secret_exposure:
        problems.append("human fields expose a secret handle instead of safe_label: " + ", ".join(dict.fromkeys(secret_exposure)))
    if translationese:
        problems.append("human fields contain English-template translationese: " + ", ".join(dict.fromkeys(translationese)))
    if weak_titles:
        recommendations.append("check that title names the behavior and object: " + ", ".join(weak_titles))
    if http_titles:
        problems.append("title contains HTTP/path instead of domain behavior: " + ", ".join(dict.fromkeys(http_titles)))
    if duplicate_titles:
        recommendations.append("check duplicate titles: " + ", ".join(duplicate_titles))
    if http_only_expected:
        problems.append("expected result is only an HTTP code or «успешно»: " + ", ".join(dict.fromkeys(http_only_expected)))
    if weak_refusal:
        problems.append("refusal step needs a human oracle (entity still there / error), not only 400: " + ", ".join(dict.fromkeys(weak_refusal)))
    if setup_heavy:
        recommendations.append("consider moving fixture setup to Предусловия: " + ", ".join(setup_heavy))
    if vague:
        problems.append("test data says «как в коде» — write the actual body: " + ", ".join(dict.fromkeys(vague)))
    if vague_values:
        problems.append("test data is not deterministic (random/faker/любое значение): " + ", ".join(dict.fromkeys(vague_values)))
    if ambiguous:
        problems.append("expected result forks HTTP codes (200 or 201) — pick the code from context: " + ", ".join(dict.fromkeys(ambiguous)))
    if weak_pre:
        problems.append("preconditions need an actor/role, not only «пользователь вошёл»: " + ", ".join(weak_pre))

    joined = " ".join(
        f"{step.get('action', '')} {_data_text(step)} {_expected_text(step)}"
        for _case, steps in live
        for step in steps
    ).lower()
    writes = any(word in joined for word in ("post", "put", "patch", "create", "созда"))
    reads = any(word in joined for word in ("get", "read", "прочит", "повтор", "откры"))
    if writes and not reads:
        recommendations.append("consider whether the specified write outcome needs readback")

    human = bool(
        bad_http_actions
        or internal_actions
        or missing_data
        or raw_data
        or machine_expected
        or secret_exposure
        or translationese
        or http_titles
        or vague
        or vague_values
        or ambiguous
        or weak_refusal
        or http_only_expected
        or weak_pre
        or any("ПРОБЕЛ" in item for item in problems)
    )
    status = "ok" if not problems else ("HUMAN" if human else "THIN")
    return {
        "status": status,
        "case_count": len(cases),
        "cases": [
            {
                "id": case.get("case_id") or case.get("id"),
                "title": case.get("title"),
                "steps": len(case.get("steps") or []),
            }
            for case in cases
        ],
        "problems": problems,
        "recommendations": recommendations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = JsonArgumentParser(description="Human-scenario gate for a canonical test document.")
    parser.add_argument("--canonical", required=True)
    args = parser.parse_args(argv)
    path = Path(args.canonical)
    if not path.is_file():
        emit_error(f"missing {path}")
        return 2
    try:
        document = load_json_strict(path)
    except Exception as error:
        emit_error(str(error))
        return 2
    if not isinstance(document, dict):
        emit_error("canonical document must be an object")
        return 2
    if "test_cases" not in document and "artifacts" in document:
        document = document.get("artifacts", {}).get("canonical_document") or document
    result = check_canonical(document)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
