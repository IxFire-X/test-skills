"""Canaries in the reviewer's view, and the codeword of the generator.

A canary is a control error that code puts into the *reviewer's view* of a candidate (never into the canon, the bundles or
the journal): a removed assertion, a shifted boundary, a changed status, a dropped state check.  The reviewer who reads the
cases against their requirements should name it.  ``seed`` is a pure function of the candidate: the same candidate gives the
same canaries.  The map ``(case_id, field, original, mutated, …)`` lies in ``<run>/review-views/<sha>.canaries.json``; its
digest, the view's and the candidate's are bound by the attempt receipt ``review-canaries`` (a journal event pair), and
``verify`` reads the map only through that chain — a map, a view or a receipt that does not match is ``REVIEW_CANARY_MISMATCH``,
never «0 hits».

What this is not: a protection.  An agent can read the map in ``<run>``; «not shown to the model» means «not in the review
package and not in the reviewer's view».  It is a signal of the quality of the review, and the report says so.

The codeword: ``model-request`` of a generator gives a ``nonce`` (only in its output); the files of the run hold its sha256
alone.  The reviewer's answer names the word it saw in ``codeword_seen`` (stored as its sha256 as well): a named word means the
reviewer saw the generator's task — the same session, SELF, whatever ``--fresh-context`` said.  A word not named is a sign of a
fresh context, not a proof of it.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CLASSES = ("assertion_removed", "boundary", "status", "state_check_removed")
# the order the seeding takes the classes in: two of three are omissions (a removed line or a removed item of the checks) — the errors a
# reviewer misses; a changed code or a shifted number is caught every time (R&D-01), so these are put less often
_ROUND = ("assertion_removed", "boundary", "state_check_removed", "assertion_removed", "status", "state_check_removed")
# the text classes: steps without machine checks (an expectation with no assertions), every error tied to the text of the case's
# requirements; after the machine ones in each round, the flipped outcome and the dropped state first — the measure of strictness;
# a number or a quoted literal is caught every time and is only the floor (R&D-13 §5)
TEXT_CLASSES = ("outcome", "state", "literal", "number", "data")
TWIN = "twin"  # a harmless rewording (two parts around «; » swapped): a BLOCKING on it is a false alarm
CLASS_NAMES = {"assertion_removed": "убрана строка проверок", "boundary": "сдвинута граница", "status": "изменён статус", "state_check_removed": "убран пункт проверок",
               "outcome": "перевёрнут исход", "state": "убрана проверка состояния", "literal": "текст в кавычках из требования",
               "number": "число из требования", "data": "данные шага из требования"}
MAP_VERSION = "1.1.0"
# every number that sets how many errors are put and how many misses are tolerated is here: ⌈√n⌉ errors, from MIN to MAX (never all the cases);
# a review is UNRELIABLE when fewer than 1/THIRD of them are caught and WEAK when more than (N−1)//THIRD are missed
MIN_CANARIES, MAX_CANARIES, THIRD = 3, 15, 3
REVIEW_STAGE = "tc-reviewer:canonical"
RECEIPT_KIND = "review-canaries"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_FLIP = {"greater_than": "greater_or_equal", "greater_or_equal": "greater_than", "less_than": "less_or_equal", "less_or_equal": "less_than"}
_SHOWN_MARKERS = 4

NOTE = ("канарейки — сигнал качества ревью, не защита: карта лежит в <run>/review-views/ и агент в принципе может её прочитать; "
        "«модели не показывается» значит «не входит в пакет ревью и в вид ревьюера»")


def _digest_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _rank(*parts: str) -> int:
    return int(hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest(), 16)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def map_target(root: Path, candidate_digest: str) -> Path:
    return Path(root) / "review-views" / (candidate_digest.removeprefix("sha256:") + ".canaries.json")


def view_target(root: Path, candidate_digest: str) -> Path:
    return Path(root) / "review-views" / (candidate_digest.removeprefix("sha256:") + ".md")


class Seeded(list):
    """The rows of a seeding — control errors, then twins (class ``twin``) — and ``wanted``, how many errors the size of the set
    asked for (``None`` for a map of 1.0.0)."""

    wanted: int | None = None


def counted(rows: Sequence[Mapping[str, Any]]) -> int:
    """How many control errors the rows hold (twins are no errors)."""
    return sum(1 for row in rows if row["class"] != TWIN)


def map_bytes(candidate_digest: str, canaries: Sequence[Mapping[str, Any]]) -> bytes:
    twins = [dict(row) for row in canaries if row["class"] == TWIN]
    wanted = getattr(canaries, "wanted", None)
    return canonical_bytes({"schema_version": MAP_VERSION, "candidate_digest": candidate_digest,
                            "canaries": [dict(row) for row in canaries if row["class"] != TWIN],
                            **({"twins": twins} if twins else {}), **({"wanted": wanted} if wanted is not None else {})})


# --- seeding ---------------------------------------------------------------------------------------

def _line(expectation: Mapping[str, Any]) -> str:
    from tools.review_view import _assertion

    return "; ".join(_assertion(item) for item in expectation["assertions"])


def _int_literal(assertion: Mapping[str, Any]) -> int | None:
    expected = assertion.get("expected")
    if isinstance(expected, Mapping) and expected.get("kind") == "literal":
        value = expected.get("value")
        if type(value) is int:
            return value
    return None


def _copy_of_code(assertion: Mapping[str, Any]) -> bool:
    actual = assertion.get("actual") or {}
    return actual.get("kind") == "http_body" and actual.get("pointer") == "/status"


def _token(value: int) -> "re.Pattern[str]":
    return re.compile(rf"(?<![\d.,]){value}(?![\d])")


def _replace_token(text: str, value: int, new: int) -> str | None:
    match = _token(value).search(text)
    return None if match is None else text[:match.start()] + str(new) + text[match.end():]


def _actual_names(expectation: Mapping[str, Any] | None, assertion: Mapping[str, Any] | None, field: str) -> list[str]:
    """What an assertion reads (a result name or a pointer): a reviewer names the value, not the whole line."""
    pool = [assertion] if assertion is not None else list(expectation["assertions"]) if expectation is not None and field == "assertions" else []
    return [str((item.get("actual") or {}).get("name") or (item.get("actual") or {}).get("pointer") or "") for item in pool]


def _base(cls: str, case: Mapping[str, Any], step: Mapping[str, Any], expectation: Mapping[str, Any] | None, assertion: Mapping[str, Any] | None,
          field: str, original: str, mutated: str, markers: Iterable[str]) -> dict[str, Any]:
    return {"class": cls, "case_id": case["case_id"], "step_id": step["step_id"],
            "expectation_id": None if expectation is None else expectation["expectation_id"],
            "assertion_id": None if assertion is None else assertion["assertion_id"],
            "field": field, "original": original, "mutated": mutated,
            "markers": [item for item in dict.fromkeys([*markers, *_actual_names(expectation, assertion, field)]) if item][:_SHOWN_MARKERS]}


def _options(cls: str, case: dict[str, Any], digest: str) -> list[tuple[dict[str, Any], Any]]:
    """The mutations of one class that fit one case: ``(map row without id, apply)`` — ``apply`` mutates the working copy."""
    from tools.review_view import _assertion

    found: list[tuple[dict[str, Any], Any]] = []
    for step in case["steps"]:
        step_values = {_int_literal(item): item for exp in step["expectations"] for item in exp["assertions"] if _int_literal(item) is not None}
        for exp in step["expectations"]:
            assertions = exp["assertions"]
            if cls == "assertion_removed" and assertions:
                row = _base(cls, case, step, exp, None, "assertions", _line(exp), "", [_assertion(item) for item in assertions])

                def apply(exp=exp) -> None:
                    exp["assertions"] = []
                found.append((row, apply))
            elif cls == "state_check_removed" and len(assertions) >= 2:
                shown = {_int_literal(item) for item in assertions if (item.get("actual") or {}).get("kind") == "http_status"}
                # the code of the response is checked by a line of its own: its copy in the body (`/status`) is no error a reviewer can see (R&D-01)
                removable = [item for item in assertions if (item.get("actual") or {}).get("kind") != "http_status"
                             and not (_copy_of_code(item) and _int_literal(item) in shown)]
                if removable:
                    gone = removable[_rank(digest, case["case_id"], exp["expectation_id"]) % len(removable)]
                    kept = [item for item in assertions if item is not gone]
                    row = _base(cls, case, step, exp, gone, "assertions", _line(exp), "; ".join(_assertion(item) for item in kept), [_assertion(gone)])

                    def apply(exp=exp, kept=kept) -> None:
                        exp["assertions"] = kept
                    found.append((row, apply))
            elif cls == "boundary":
                for value, assertion in step_values.items():
                    shifted = _replace_token(exp["text"], value, value + 1)
                    if shifted is not None and shifted != exp["text"]:
                        row = _base(cls, case, step, exp, assertion, "expectation", exp["text"], shifted, [str(value + 1), str(value)])

                        def apply(exp=exp, shifted=shifted) -> None:
                            exp["text"] = shifted
                        found.append((row, apply))
                for item in assertions:
                    if item.get("operator") in _FLIP:
                        before = _line(exp)
                        flipped = {**item, "operator": _FLIP[item["operator"]]}
                        after = "; ".join(_assertion(flipped if other is item else other) for other in assertions)
                        row = _base(cls, case, step, exp, item, "assertions", before, after, [_assertion(item), _assertion(flipped)])

                        def apply(item=item) -> None:
                            item["operator"] = _FLIP[item["operator"]]
                        found.append((row, apply))
            elif cls == "status":
                for item in assertions:
                    value = _int_literal(item)
                    if (item.get("actual") or {}).get("kind") != "http_status" or value is None:
                        continue
                    other = 200 if value != 200 else 404
                    shifted = _replace_token(exp["text"], value, other)
                    if shifted is not None:
                        row = _base(cls, case, step, exp, item, "expectation", exp["text"], shifted, [str(other), str(value)])

                        def apply(exp=exp, shifted=shifted) -> None:
                            exp["text"] = shifted
                    else:
                        before = _line(exp)
                        changed = {**item, "expected": {**item["expected"], "value": other}}
                        after = "; ".join(_assertion(changed if x is item else x) for x in assertions)
                        row = _base(cls, case, step, exp, item, "assertions", before, after, [_assertion(item), _assertion(changed)])

                        def apply(item=item, other=other) -> None:
                            item["expected"] = {**item["expected"], "value": other}
                    found.append((row, apply))
    return found


# --- text control errors: steps without machine checks (R&D-13, rnd/scripts/q13/build_text_canaries.py) ----------------------

_NUMBER = re.compile(r"(?<![\w.])(\d{1,6})(?![\w.])")
_QUOTED = re.compile(r"«([^«»]{2,60})»")
_FLIP = (("принимается", "отклоняется"), ("отклоняется", "принимается"), ("принят", "отклонён"), ("принято", "отклонено"),
         ("отклонён", "принят"), ("отклонено", "принято"), ("сохраняется", "не сохраняется"), ("сохранён", "не сохранён"),
         ("сохранено", "не сохранено"), ("создаётся", "не создаётся"), ("создан", "не создан"), ("появляется", "не появляется"),
         ("показана", "не показана"), ("показано", "не показано"), ("показаны", "не показаны"), ("показан", "не показан"),
         ("открывается", "не открывается"), ("открыта", "не открыта"), ("присутствует", "отсутствует"), ("отсутствует", "присутствует"),
         ("выбрасывает", "не выбрасывает"), ("возвращает", "не возвращает"))
_STATE_WORDS = re.compile(r"\b(не\s+сохраня|не\s+созда|не\s+измен|оста[её]тся|без\s+изменени)", re.I)


def _has_number(text: str, value: str) -> bool:
    return re.search(rf"(?<![\w.]){value}(?![\w.])", text) is not None


def _shift_number(text: str, value: str) -> str:
    return re.sub(rf"(?<![\w.]){value}(?![\w.])", str(int(value) + 1), text, count=1)


def _flip(text: str, linked: str) -> tuple[str, str, str] | None:
    """``(mutated, word, new word)``: the first outcome word whose stem the requirement has, turned over.  Not a word already
    after «не», not one before «без» («создан без исключения» → «не создан без …» is no clear contradiction); the case of the
    first letter is kept («Показан» → «Не показан»)."""
    for word, other in _FLIP:
        hit = re.search(rf"(?<![\w-]){word}(?![\w-])", text, re.I)
        if hit is None or word[:5] not in linked:
            continue
        if text[:hit.start()].lower().endswith("не ") or text[hit.end():].lower().startswith(" без"):
            continue
        new = other[0].upper() + other[1:] if hit.group(0)[0].isupper() else other
        return text[:hit.start()] + new + text[hit.end():], hit.group(0), new
    return None


def _drop_state(text: str, linked: str) -> tuple[str, str] | None:
    """``(mutated, removed)``: the clause about an unchanged state («… не сохраняется / остаётся / без изменений»), when the requirement
    names that state and enough of the expectation is left."""
    hit = _STATE_WORDS.search(text)
    if hit is None:
        return None
    stem = re.sub(r"\s+", " ", hit.group(1).lower()).removeprefix("не ").removeprefix("без ")[:4]
    if stem not in linked:
        return None
    for clause in re.finditer(r"[;,]?\s*[^.;,]+", text):
        if hit.start() >= clause.start() and hit.end() <= clause.end():
            rest = (text[:clause.start()] + text[clause.end():]).strip()
            rest = re.sub(r"^[;,]\s*", "", re.sub(r"\s{2,}", " ", rest))
            if len(rest) <= 20:
                return None
            if text[:1].isupper() and rest[:1].islower():
                rest = rest[0].upper() + rest[1:]
            return rest, clause.group(0).strip(" ;,")
    return None


def _twin(text: str) -> str | None:
    """The same expectation with its two parts around «; » swapped — the meaning is the same (rnd/13: 0 BLOCKING of 26)."""
    parts = text.split("; ")
    if len(parts) != 2 or not all(len(part.strip()) > 10 for part in parts):
        return None
    first, second = parts[0].strip(), parts[1].strip()
    period = second.endswith(".")
    second = second.rstrip(".")
    if re.match(r"[а-яё]", second):
        second = second[0].upper() + second[1:]
    if re.match(r"[А-ЯЁ](?:[а-яё]|\s)", first):
        first = first[0].lower() + first[1:]
    return f"{second}; {first}" + ("." if period else "")


def _linked(document: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """``(text of each requirement, the quoted literals of all of them)`` — what a text error is tied to."""
    texts = {str(item["requirement_id"]): str(item.get("text") or "") for item in document.get("requirements") or []}
    literals = sorted({quoted.strip() for text in texts.values() for quoted in _QUOTED.findall(text)
                       if quoted == quoted.strip() and 3 <= len(quoted) <= 40 and re.search(r"[а-яёa-z]", quoted, re.I)})
    return texts, literals


def _text_options(cls: str, case: dict[str, Any], digest: str, texts: Mapping[str, str], literals: Sequence[str]) -> list[tuple[dict[str, Any], Any]]:
    """The text errors of one class that fit one case: ``(map row without id, apply)``, on expectations without assertions (and,
    for ``data``, the test data of a step none of whose expectations has assertions)."""
    linked = " ".join(texts.get(str(item), "") for item in case.get("requirement_ids") or []).lower()
    found: list[tuple[dict[str, Any], Any]] = []
    for step in case["steps"]:
        manual = [exp for exp in step["expectations"] if not exp["assertions"]]
        if cls == "data" and manual and len(manual) == len(step["expectations"]):
            data = str(step.get("test_data") or "")
            for value in sorted(set(_NUMBER.findall(data))):
                if _has_number(linked, value):
                    shifted = _shift_number(data, value)
                    row = _base(cls, case, step, None, None, "test_data", data, shifted, [str(int(value) + 1), value])

                    def apply(step=step, shifted=shifted) -> None:
                        step["test_data"] = shifted
                    found.append((row, apply))
        for exp in manual if cls != "data" else ():
            text, made = exp["text"], []
            if cls == "number":
                made = [(_shift_number(text, value), [str(int(value) + 1), value]) for value in sorted(set(_NUMBER.findall(text)))
                        if _has_number(linked, value)]
            elif cls == "literal":
                for quoted in sorted(set(_QUOTED.findall(text))):
                    others = [item for item in literals if item.lower() != quoted.lower()]
                    if quoted.lower() in linked and others:
                        new = others[_rank(digest, case["case_id"], exp["expectation_id"], quoted) % len(others)]
                        made.append((text.replace(f"«{quoted}»", f"«{new}»", 1), [new, quoted]))
            elif cls == "outcome":
                flipped = _flip(text, linked)
                made = [(flipped[0], [flipped[2], flipped[1]])] if flipped else []
            elif cls == "state":
                dropped = _drop_state(text, linked)
                made = [(dropped[0], [])] if dropped else []
            for mutated, markers in made:
                row = _base(cls, case, step, exp, None, "expectation", text, mutated, markers)

                def apply(exp=exp, mutated=mutated) -> None:
                    exp["text"] = mutated
                found.append((row, apply))
    return found


def _twins(order: Sequence[dict[str, Any]], used: set[str], count: int, digest: str) -> list[dict[str, Any]]:
    """Up to *count* twins, one a case, on expectations without assertions of the cases with no error; applied to the working copy."""
    rows: list[dict[str, Any]] = []
    for case in order:
        if len(rows) >= count:
            break
        if case["case_id"] in used:
            continue
        spots = [(step, exp, twin) for step in case["steps"] for exp in step["expectations"]
                 if not exp["assertions"] and (twin := _twin(exp["text"])) is not None]
        if not spots:
            continue
        step, exp, twin = spots[_rank(digest, case["case_id"], TWIN) % len(spots)]
        rows.append(_base(TWIN, case, step, exp, None, "expectation", exp["text"], twin, []))
        exp["text"] = twin
    for index, row in enumerate(rows, start=1):
        row["canary_id"] = f"TWIN-{index}"
    return rows


def canary_count(eligible: int) -> int:
    """How many control errors a candidate of *eligible* cases gets: ⌈√n⌉, at least 3 and at most 15, never all the cases (n−1)."""
    return max(0, min(MAX_CANARIES, max(MIN_CANARIES, math.isqrt(max(eligible, 1) - 1) + 1), eligible - 1))


def seed(document: Mapping[str, Any]) -> tuple[dict[str, Any], "Seeded"]:
    """``(document for the view, rows)``.  The document of the view is a copy with ``canary_count`` cases mutated (one error a
    case), none of them in a parameter group; the argument is not changed.  With fewer than two such cases nothing is seeded (one
    case of one would be a view that is all control).  Each round takes the machine classes, then the text ones (steps without
    machine checks); as many twins as text errors follow, on other cases.  ``rows.wanted`` — what the size asked for."""
    from tools.canonical_document import document_sha256, param_groups
    from tools.test_case_projections import SLOT

    digest = document_sha256(dict(document))
    work = copy.deepcopy(dict(document))
    grouped = set()
    if SLOT not in json.dumps(document, ensure_ascii=False):
        grouped = {case["case_id"] for group in param_groups(dict(document)) for case in group["cases"]}
    eligible = [case for case in work["test_cases"] if case["case_id"] not in grouped]
    order = sorted(eligible, key=lambda case: _rank(digest, case["case_id"]))
    wanted = canary_count(len(eligible))
    texts, literals = _linked(work)
    used: set[str] = set()
    rows: list[dict[str, Any]] = []
    progressed = True
    while len(rows) < wanted and progressed:
        progressed = False
        for cls in (*_ROUND, *TEXT_CLASSES):
            if len(rows) >= wanted:
                break
            for case in order:
                if case["case_id"] in used:
                    continue
                options = _options(cls, case, digest) if cls in CLASSES else _text_options(cls, case, digest, texts, literals)
                if not options:
                    continue
                row, apply = options[_rank(digest, case["case_id"], cls) % len(options)]
                apply()
                used.add(case["case_id"])
                rows.append(row)
                progressed = True
                break
    for index, row in enumerate(rows, start=1):
        row["canary_id"] = f"CAN-{index}"
    result = Seeded([*rows, *_twins(order, used, sum(row["class"] in TEXT_CLASSES for row in rows), digest)])
    result.wanted = wanted
    return work, result


# --- the chain of trust: journal -> receipt -> map and view -----------------------------------------

class CanaryMismatch(ValueError):
    pass


def receipt_facts(candidate_digest: str, view: bytes, data: bytes, count: int) -> dict[str, Any]:
    return {"candidate_digest": candidate_digest, "view_digest": _digest_of(view), "canary_map_digest": _digest_of(data), "canary_count": count}


def _mismatch(reason: str) -> CanaryMismatch:
    return CanaryMismatch(f"REVIEW_CANARY_MISMATCH: {reason}")


BATCH_ID = "review-canaries-v1"  # the mark of the events of the receipt: «seeded or not» is decided by the journal, not by a file


def _bound_events(run_root: Path, attempt_id: str) -> list[dict[str, Any]]:
    from tools import pilot_state as pilot

    return [event for event in pilot.derive_state(pilot._run_root(Path(run_root))[1])["events"]
            if event.get("attempt_id") == attempt_id and event.get("batch_id") == BATCH_ID]


def verify(run_root: Path, attempt_id: str) -> list[dict[str, Any]] | None:
    """The canaries of the attempt, read through the journal, or ``None`` when the journal has no event of a ``review-canaries``
    receipt for the attempt (a run opened by an earlier version: not measured).  Events that are there bind a receipt, and the receipt three
    digests; the receipt, the map and the view must all be there and give them — a file removed or changed is
    ``REVIEW_CANARY_MISMATCH``, whatever the reason it cannot be read."""
    events = _bound_events(run_root, attempt_id)
    if not events:
        return None
    try:
        return _read_canaries(run_root, attempt_id, events)
    except CanaryMismatch:
        raise
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise _mismatch(f"канарейки не читаются ({str(error)[:120]})") from error


def _read_canaries(run_root: Path, attempt_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from tools import pilot_state as pilot
    from tools.confined_output import read_confined_bytes
    from tools.schema_validation import schema_diagnostics

    project, root = pilot._run_root(Path(run_root))
    try:
        record = dict(pilot.read_attempt_receipt(root, attempt_id, RECEIPT_KIND, "ARTIFACT_READ_BACK")["record"])
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise _mismatch("квитанция review-canaries есть в журнале, а файл удалён или не проходит проверку") from error
    if {event["event_type"] for event in events if event.get("artifact_digest") == record["digest"]} != {"ARTIFACT_PUBLISHED", "ARTIFACT_READ_BACK"}:
        raise _mismatch("события журнала не связывают эту квитанцию")
    candidate = str(record["candidate_digest"])
    data = read_confined_bytes(project, root, map_target(root, candidate))
    view = read_confined_bytes(project, root, view_target(root, candidate))
    if data is None or view is None:
        raise _mismatch("квитанция есть, а карты канареек или вида ревьюера в review-views нет")
    if _digest_of(data) != record["canary_map_digest"]:
        raise _mismatch("карта канареек не совпадает с квитанцией журнала")
    if _digest_of(view) != record["view_digest"]:
        raise _mismatch("вид ревьюера не совпадает с квитанцией журнала")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise _mismatch("карта канареек не читается") from error
    schema = Path(__file__).resolve().parents[1] / "schemas" / "review-canaries.schema.json"
    if schema_diagnostics(value, schema, schema.parents[1]) or value.get("candidate_digest") != candidate \
            or len(value["canaries"]) != record["canary_count"]:
        raise _mismatch("карта канареек не соответствует схеме или кандидату")
    rows = Seeded([*value["canaries"], *(value.get("twins") or [])])  # twins ride along: an edit of one is dropped, a BLOCKING on one rejects nothing
    rows.wanted = value.get("wanted")
    return rows


def resume_binding(run_root: Path, attempt_id: str, expected: Any) -> bool:
    """Finish the binding of a seeding that was cut short.  The receipt file is there (the journal may lack one or both of its events).
    Files that agree with each other are no proof — an agent can write a receipt, a map and a view of its own before ``prepare`` of the review —
    so the bytes are compared with what the seeding gives now: *expected* is a function returning ``(facts, view, map)``, the seeding of
    the candidate of the journal (it is deterministic).  Equal byte for byte → the missing events are appended, ``True``.  No receipt
    file → ``False`` (nothing to finish; the seeding starts over).  Anything else is ``REVIEW_CANARY_MISMATCH``."""
    from tools import pilot_state as pilot
    from tools.confined_output import read_confined_bytes

    project, root = pilot._run_root(Path(run_root))
    try:
        raw = read_confined_bytes(project, root, pilot._receipt_target(root, attempt_id, RECEIPT_KIND))
    except (OSError, ValueError) as error:
        raise _mismatch("файл квитанции review-canaries не читается") from error
    if raw is None:
        return False
    facts, view, mapped = expected()
    try:
        record = json.loads(raw.decode("utf-8"))
        same = {key: record[key] for key in facts} == facts
        data = read_confined_bytes(project, root, map_target(root, facts["candidate_digest"]))
        shown = read_confined_bytes(project, root, view_target(root, facts["candidate_digest"]))
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise _mismatch("квитанция review-canaries не читается") from error
    if not same or data != mapped or shown != view:
        raise _mismatch("квитанция, карта и вид не равны тому, что даёт посев кандидата журнала: файлы подложены или испорчены")
    pilot.publish_review_canaries(run_root, attempt_id, facts)  # the same facts: the receipt is the one that is there, the events are added
    return True


# --- hits ------------------------------------------------------------------------------------------

def _names(marker: str, text: str) -> bool:
    if marker.isdigit():
        return re.search(rf"(?<!\d){marker}(?!\d)", text) is not None
    return marker in text


def _has_id(text: str, identifier: str) -> bool:
    """An ID named whole in a text (STEP-S1 is not in STEP-S10)."""
    return re.search(rf"(?<![A-Za-z0-9_.:-]){re.escape(identifier)}(?![A-Za-z0-9_.:-])", text) is not None


_BY_PLACE = frozenset({"assertion_removed", TWIN, *TEXT_CLASSES})


def finding_hits(canary: Mapping[str, Any], finding: Mapping[str, Any], *, strict: bool = False) -> bool:
    """A finding is about a canary when its ``related_ids`` or its text name the mutated step, expectation or assertion, or name
    the case and, in the text, a value of the canary.  A bare case ID in a general finding is not a hit.  *strict* — what a refusal
    may stand on: the ID of the step, expectation or assertion is in ``related_ids`` as a whole item **and** the text names the
    mutated or the original value (for ``assertion_removed``, whose view holds nothing to quote, the ID of the expectation or the
    assertion — the step only when there is no expectation — is enough); a looser hit is for the statistics only."""
    ids = {str(item) for item in finding.get("related_ids") or []}
    text = " ".join([str(finding.get("message", "")), *(str(item) for item in finding.get("evidence") or [])])
    specific = {str(canary[key]) for key in ("step_id", "expectation_id", "assertion_id") if canary.get(key)}
    named = any(_names(marker, text) for marker in canary.get("markers") or [])
    if strict:
        # a finding that names another step, expectation or assertion is not only about this canary (the prefixes are the ones of
        # schemas/canonical-test-document.schema.json)
        if any(item.startswith(("STEP-", "EXP-", "ASSERT-")) and item not in specific for item in ids):
            return False
        if canary.get("class") in _BY_PLACE:  # nothing to quote (a removed line, a dropped clause) or a text error: the place is the hit (R&D-13)
            place = {str(canary[key]) for key in ("expectation_id", "assertion_id") if canary.get(key)} or specific
            named_ids = set(re.findall(r"(?<![\w.:-])(?:STEP|EXP|ASSERT)-[\w.:-]*\w", text))  # a finding to the step is the canary's when it names the canary's expectation or assertion, and no other one
            return bool(ids & place) or (str(canary["step_id"]) in ids and bool(named_ids & place) and named_ids <= specific)
        return bool(ids & specific) and named
    if ids & specific or any(_has_id(text, item) for item in specific):
        return True
    return canary["case_id"] in ids and named


def edit_hits(canary: Mapping[str, Any], edit: Mapping[str, Any]) -> bool:
    """An edit of the text a canary mutated whose ``old`` is the mutated text (the canon holds the original)."""
    if canary["field"] not in {"expectation", "test_data"} or edit.get("case_id") != canary["case_id"] or edit.get("field") != canary["field"]:
        return False
    if edit.get("step_id") != canary["step_id"] or (canary["field"] == "expectation" and edit.get("expectation_id") != canary["expectation_id"]):
        return False
    return edit.get("old") == canary["mutated"]


def filter_edits(canaries: Sequence[Mapping[str, Any]], artifacts: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The artifacts of a reviewer answer without the edits that act on a canary, and the IDs of the canaries they hit."""
    edits = artifacts.get("successor_edits")
    if not isinstance(edits, list):
        return dict(artifacts), []
    kept, dropped = [], []
    for edit in edits:
        canary = next((row for row in canaries if isinstance(edit, Mapping) and edit_hits(row, edit)), None)
        if canary is None:
            kept.append(edit)
        else:
            dropped.append(str(canary["canary_id"]))
    result = {key: value for key, value in artifacts.items() if key != "successor_edits"}
    if kept:
        result["successor_edits"] = kept
    return result, dropped


def _blocking_on(row: Mapping[str, Any], findings: Sequence[Any]) -> bool:
    return any(isinstance(item, Mapping) and item.get("severity") == "BLOCKING" and finding_hits(row, item, strict=True) for item in findings)


def hit_ids(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any]) -> dict[str, str]:
    """``{canary_id: how}`` for each canary the answer names — by an edit of its text, or by a finding: of any severity for a
    machine error, only a BLOCKING on its place for a text error (a WARNING on a flipped outcome is a miss, R&D-13).  Twins: never."""
    artifacts = answer.get("artifacts") or {}
    findings = (artifacts.get("validation_report") or {}).get("findings") or []
    found: dict[str, str] = {}
    for canary in canaries:
        if canary["class"] == TWIN:
            continue
        if any(isinstance(edit, Mapping) and edit_hits(canary, edit) for edit in artifacts.get("successor_edits") or []):
            found[str(canary["canary_id"])] = "edit"
        elif _blocking_on(canary, findings) if canary["class"] in TEXT_CLASSES else \
                any(finding_hits(canary, finding) for finding in findings if isinstance(finding, Mapping)):
            found[str(canary["canary_id"])] = "finding"
    return found


def canary_finding(canaries: Sequence[Mapping[str, Any]], finding: Mapping[str, Any], cases: set[str]) -> bool:
    """A finding is a canary's when it stands on a canary's place by the strict rule and names no case but the canary's own."""
    return any(finding_hits(row, finding, strict=True) and cases <= {row["case_id"]} for row in canaries)


def refuse_if_only_canaries(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any], edits_dropped: Sequence[str]) -> None:
    """The refusal before the answer is written (and in ``--dry-run``): fixes that stand on canaries alone.  A blocking finding on a canary rejects nothing."""
    report = (answer.get("artifacts") or {}).get("validation_report") or {}
    verdict = report.get("verdict")
    tail = ("этих ошибок нет в кандидате, их поставил код в вид ревьюера; оставь эти находки с severity INFO, правки по ним убери, "
            "вердикт вынеси по остальному (ПРИНЯТО, если больше ничего); отдай ответ единственным повтором стадии (--transport-attempts 2), "
            "ничего не записано")
    if verdict == "AUTO_FIX_APPLIED" and edits_dropped and "successor_edits" not in _filtered(canaries, answer) \
            and "successor_document" not in (answer.get("artifacts") or {}):
        named = ", ".join(sorted(set(edits_dropped)))
        raise ValueError(f"REVIEW_ONLY_CANARY_FIXES: все правки ответа — по контрольным ошибкам ({named}); fix: {tail}")


def _filtered(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any]) -> dict[str, Any]:
    return filter_edits(canaries, answer.get("artifacts") or {})[0]


# --- outcome (derived on demand from the journal; nothing is stored) -------------------------------

def outcome(canaries: Sequence[Mapping[str, Any]] | None, answer: Mapping[str, Any] | None) -> dict[str, Any]:
    if canaries is None:
        return {"status": "NOT_SEEDED", "seeded": None, "caught": None, "warning": "REVIEW_UNMEASURED",
                "note": "ревью не измерено канарейками (прогон открыт до P7)"}
    wanted = getattr(canaries, "wanted", None)
    twins = [row for row in canaries if row["class"] == TWIN]
    canaries = [row for row in canaries if row["class"] != TWIN]
    seeded = len(canaries)
    if seeded == 0:
        return {"status": "NONE", "seeded": 0, "caught": 0, "warning": None, "note": "контроль ревьюера не измерен: ни одной контрольной ошибки не поставлено — в кандидате нечего мутировать"}
    if answer is None:
        return {"status": "PENDING", "seeded": seeded, "caught": None, "warning": None, "note": "ответ ревьюера ещё не записан"}
    hits = hit_ids(canaries, answer)
    missed = [row for row in canaries if row["canary_id"] not in hits]
    classes = sorted({row["class"] for row in missed})
    allowed = (seeded - 1) // THIRD  # misses that are no sign of a weak review: none up to 3 errors, a third of the rest
    # UNRELIABLE (and accepted false) stands on the machine errors only: in v0.8 a text error counts towards WEAK and the report, not
    # towards the acceptance — the flipped outcome and the dropped state were caught as BLOCKING 0 of 4 (R&D-13); the user decides on a measure
    machine = [row for row in canaries if row["class"] in CLASSES]
    caught_machine = sum(1 for row in machine if row["canary_id"] in hits)
    if machine and THIRD * caught_machine < len(machine):  # fewer than a third caught (none of 3, as before)
        status, warning = "UNRELIABLE", "REVIEW_UNRELIABLE"
        got = "ни одна из" if not caught_machine else f"поймано {caught_machine} из"
        note = (f"ревью ненадёжно (REVIEW_UNRELIABLE): {got} {len(machine)} контрольных ошибок {'не поймана' if not caught_machine else 'меньше трети'}, "
                f"классы {', '.join(sorted({row['class'] for row in machine if row['canary_id'] not in hits}))}")
    elif len(missed) > allowed:
        status, warning = "WEAK", "REVIEW_WEAK"
        note = f"ревью слабое: пропущено {len(missed)} из {seeded}, классы {', '.join(classes)}"
    elif missed:
        status, warning, note = "MOSTLY_CAUGHT", None, f"поймано {len(hits)} из {seeded} контрольных ошибок (допустимо пропустить до {allowed}), классы пропущенных: {', '.join(classes)}"
    else:
        status, warning, note = "ALL_CAUGHT", None, f"поймано {seeded} из {seeded} контрольных ошибок"
    by_class = {cls: {"seeded": sum(row["class"] == cls for row in canaries), "caught": sum(row["class"] == cls and row["canary_id"] in hits for row in canaries)}
                for cls in (*CLASSES, *TEXT_CLASSES) if any(row["class"] == cls for row in canaries)}
    findings = (answer.get("artifacts") or {}).get("validation_report", {}).get("findings") or []
    blocking = sum(1 for item in findings  # the blocking findings that reject no case: they stand on a canary
                   if isinstance(item, Mapping) and item.get("severity") == "BLOCKING"
                   and canary_finding(canaries, item, {str(i) for i in item.get("related_ids") or [] if str(i).startswith("TC-")}))
    texts = sum(1 for row in canaries if row["class"] in TEXT_CLASSES)
    alarms = sum(1 for row in twins if _blocking_on(row, findings))
    notes = [note]
    if texts:
        notes.append("текстовые контрольные ошибки (в ручных шагах) засчитываются только как BLOCKING: WARNING или INFO на них — пропуск")
    if twins:
        notes.append(f"безобидных правок в виде ревьюера (двойников): {len(twins)}, ложных BLOCKING на них: {alarms}")
    if texts and wanted is not None and seeded < wanted:
        notes.append(f"в ручных кейсах мало мест, привязанных к требованию: контрольных ошибок {seeded} из {wanted} по размеру набора")
    return {"status": status, "seeded": seeded, "caught": len(hits), "missed_classes": classes, "by_class": by_class, "hits": hits, "blocking": blocking, "warning": warning,
            **({"twins": {"seeded": len(twins), "blocking": alarms}} if twins else {}), **({"wanted": wanted} if texts and wanted is not None else {}),
            "note": "; ".join([*notes, NOTE])}


def recorded_answer(run_root: Path, attempt_id: str) -> Mapping[str, Any] | None:
    """The recorded tc-reviewer answer of the attempt, or ``None`` before it."""
    from tools import pilot_state as pilot

    project, root = pilot._run_root(Path(run_root))
    events = [event for event in pilot.derive_state(root)["events"] if event.get("attempt_id") == attempt_id
              and event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == REVIEW_STAGE]
    if len(events) != 1:
        return None
    return pilot.read_model_stage_artifact(root, attempt_id, REVIEW_STAGE, str(events[0]["artifact_digest"]))["artifact"]


def summary(run_root: Path, attempt_id: str, answer: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The outcome for the report and the status: never raises — and never silent: a map that does not match, or any failure to read
    the canaries of a run whose journal has them, is ``MISMATCH`` with the warning ``REVIEW_CANARY_MISMATCH``."""
    try:
        canaries = verify(run_root, attempt_id)
        return outcome(canaries, answer if answer is not None else recorded_answer(run_root, attempt_id))
    except CanaryMismatch as error:
        note = str(error)
    except (OSError, KeyError, TypeError, ValueError) as error:
        note = f"REVIEW_CANARY_MISMATCH: канарейки или ответ ревьюера не прочитаны ({str(error)[:160]})"
    return {"status": "MISMATCH", "seeded": None, "caught": None, "warning": "REVIEW_CANARY_MISMATCH", "note": note}


def status_fields(run_root: Path, attempt_id: str, status: Mapping[str, Any]) -> dict[str, Any]:
    """The fields ``status`` adds once the reviewer's answer is recorded: the outcome, REVIEW_UNRELIABLE and, then, ``accepted`` false."""
    try:
        answer = recorded_answer(run_root, attempt_id)
    except (OSError, KeyError, TypeError, ValueError):
        answer = None
    found = summary(run_root, attempt_id, answer)
    if answer is None and found["status"] != "MISMATCH":  # before the answer only a map that does not match is news
        return {}
    fields: dict[str, Any] = {"review_canaries": found}
    if found.get("warning"):
        fields["warnings"] = [*(status.get("warnings") or []), found["warning"]]
    if found["status"] == "UNRELIABLE":
        fields["review_unreliable"] = True
        if "accepted" in status:
            fields["accepted"] = False
    return fields


# --- the codeword ----------------------------------------------------------------------------------

def nonce_digest(nonce: str) -> str:
    return _digest_of(nonce.encode("utf-8"))


def hash_codeword(answer: Mapping[str, Any]) -> dict[str, Any]:
    """The reviewer's answer with ``codeword_seen`` as the sha256 of the word (a word is never written into the run)."""
    value = dict(answer)
    seen = value.get("codeword_seen")
    if isinstance(seen, str):
        seen = seen.strip()
        value["codeword_seen"] = None if not seen else seen if _DIGEST.fullmatch(seen) else nonce_digest(seen)
    return value


def generator_nonce_digests(run_root: Path, attempt_id: str) -> list[str]:
    from tools import pilot_state as pilot

    project, root = pilot._run_root(Path(run_root))
    digests = []
    for event in pilot.derive_state(root)["events"]:
        if event.get("attempt_id") == attempt_id and event["event_type"] == "MODEL_REQUESTED" \
                and str(event.get("stage_instance_id")).startswith("tc-generator:"):
            request = pilot.read_model_request(root, attempt_id, str(event["stage_instance_id"]), str(event["artifact_digest"]))
            if request.get("nonce_sha256"):
                digests.append(str(request["nonce_sha256"]))
    return digests


def codeword_state(run_root: Path, attempt_id: str, answer: Mapping[str, Any] | None) -> str:
    """``named`` — the reviewer named the word of a generator request (SELF); ``wrong`` — a word that is none of them (taken as
    not named); ``not_named`` — a sign of a fresh context, not a proof; ``no_word`` — the generator got none (nothing to compare)."""
    nonces = generator_nonce_digests(run_root, attempt_id)
    if not nonces:
        return "no_word"
    seen = (hash_codeword(answer or {})).get("codeword_seen")
    if not seen:
        return "not_named"
    return "named" if seen in nonces else "wrong"


CODEWORD_NOTES = {
    "named": "кодовое слово названо: ревьюер видел задачу генератора — ревью в той же сессии (SELF), что бы ни заявил --fresh-context",
    "wrong": "названо слово, которого генератор не получал: считается, что слово не названо (признак свежести, не доказательство)",
    "not_named": "кодовое слово не названо: признак свежего контекста, не доказательство",
    "no_word": "генератор слова не получал (его вывод prepare потерян или запрос до P7): сверять нечего",
    "reused": "ревью взято из прошлого прогона: слово генератора этого прогона не сверялось, независимость — заявление источника",
}
