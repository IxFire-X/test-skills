"""P7: canaries in the reviewer's view, and the codeword of the generator.

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
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

CLASSES = ("assertion_removed", "boundary", "status", "state_check_removed")
MAP_VERSION = "1.0.0"
MAX_CANARIES = 3
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


def map_bytes(candidate_digest: str, canaries: Sequence[Mapping[str, Any]]) -> bytes:
    return canonical_bytes({"schema_version": MAP_VERSION, "candidate_digest": candidate_digest, "canaries": [dict(row) for row in canaries]})


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
                removable = [item for item in assertions if (item.get("actual") or {}).get("kind") != "http_status"]
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


def seed(document: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``(document for the view, canaries)``.  The document of the view is a copy with 0..3 cases mutated, none of them in a
    parameter group; the argument is not changed.  With fewer than two such cases nothing is seeded (one case of one would be
    a view that is all control)."""
    from tools.canonical_document import document_sha256, param_groups
    from tools.test_case_projections import SLOT

    digest = document_sha256(dict(document))
    work = copy.deepcopy(dict(document))
    grouped = set()
    if SLOT not in json.dumps(document, ensure_ascii=False):
        grouped = {case["case_id"] for group in param_groups(dict(document)) for case in group["cases"]}
    eligible = [case for case in work["test_cases"] if case["case_id"] not in grouped]
    order = sorted(eligible, key=lambda case: _rank(digest, case["case_id"]))
    wanted = min(MAX_CANARIES, max(0, len(eligible) - 1))
    used: set[str] = set()
    rows: list[dict[str, Any]] = []
    progressed = True
    while len(rows) < wanted and progressed:
        progressed = False
        for cls in CLASSES:
            if len(rows) >= wanted:
                break
            for case in order:
                if case["case_id"] in used:
                    continue
                options = _options(cls, case, digest)
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
    return work, rows


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
    receipt for the attempt (a run opened before P7: not measured).  Events that are there bind a receipt, and the receipt three
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
    return list(value["canaries"])


def resume_binding(run_root: Path, attempt_id: str) -> bool:
    """Finish the binding of a seeding that was cut short.  The receipt file is there (the journal may lack one or both of its events),
    and the map and the view match it byte for byte: the missing events are appended and ``True`` returned.  No receipt file —
    ``False`` (nothing to finish; the seeding starts over).  A receipt file whose map or view do not match it is a real divergence:
    ``REVIEW_CANARY_MISMATCH``."""
    from tools import pilot_state as pilot
    from tools.confined_output import read_confined_bytes

    project, root = pilot._run_root(Path(run_root))
    try:
        raw = read_confined_bytes(project, root, pilot._receipt_target(root, attempt_id, RECEIPT_KIND))
    except (OSError, ValueError) as error:
        raise _mismatch("файл квитанции review-canaries не читается") from error
    if raw is None:
        return False
    try:
        record = json.loads(raw.decode("utf-8"))
        facts = {key: record[key] for key in ("candidate_digest", "view_digest", "canary_map_digest", "canary_count")}
        data = read_confined_bytes(project, root, map_target(root, facts["candidate_digest"]))
        view = read_confined_bytes(project, root, view_target(root, facts["candidate_digest"]))
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise _mismatch("квитанция review-canaries не читается") from error
    if data is None or view is None or _digest_of(data) != facts["canary_map_digest"] or _digest_of(view) != facts["view_digest"]:
        raise _mismatch("файл квитанции есть, а карта или вид не совпадают с ним")
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


def finding_hits(canary: Mapping[str, Any], finding: Mapping[str, Any], *, strict: bool = False) -> bool:
    """A finding is about a canary when its ``related_ids`` or its text name the mutated step, expectation or assertion, or name
    the case and, in the text, a value of the canary.  A bare case ID in a general finding is not a hit.  *strict* — what a refusal
    may stand on: the ID of the step, expectation or assertion is in ``related_ids`` as a whole item **and** the text names the
    mutated or the original value; a looser hit is for the statistics only."""
    ids = {str(item) for item in finding.get("related_ids") or []}
    text = " ".join([str(finding.get("message", "")), *(str(item) for item in finding.get("evidence") or [])])
    specific = {str(canary[key]) for key in ("step_id", "expectation_id", "assertion_id") if canary.get(key)}
    named = any(_names(marker, text) for marker in canary.get("markers") or [])
    if strict:
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


def hit_ids(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any]) -> dict[str, str]:
    """``{canary_id: how}`` for each canary the answer names — by a finding of any severity or by an edit of its text."""
    artifacts = answer.get("artifacts") or {}
    report = artifacts.get("validation_report") or {}
    found: dict[str, str] = {}
    for canary in canaries:
        if any(isinstance(edit, Mapping) and edit_hits(canary, edit) for edit in artifacts.get("successor_edits") or []):
            found[str(canary["canary_id"])] = "edit"
        elif any(finding_hits(canary, finding) for finding in report.get("findings") or [] if isinstance(finding, Mapping)):
            found[str(canary["canary_id"])] = "finding"
    return found


def only_canary_verdict(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any]) -> list[str]:
    """For ``ТРЕБУЕТ ДОРАБОТКИ`` whose every BLOCKING finding is about a canary: those findings as «#n code (step)»; else ``[]``."""
    report = (answer.get("artifacts") or {}).get("validation_report") or {}
    blocking = [(index, item) for index, item in enumerate(report.get("findings") or [], start=1)
                if isinstance(item, Mapping) and item.get("severity") == "BLOCKING"]
    named = []
    for index, finding in blocking:
        canary = next((row for row in canaries if finding_hits(row, finding, strict=True)), None)
        if canary is None:
            return []
        named.append(f"находка #{index} {finding.get('code')} (шаг {canary['step_id']})")
    return named


def refuse_if_only_canaries(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any], edits_dropped: Sequence[str]) -> None:
    """The two refusals before the answer is written (and in ``--dry-run``): a verdict that stands on canaries alone."""
    report = (answer.get("artifacts") or {}).get("validation_report") or {}
    verdict = report.get("verdict")
    tail = ("этих ошибок нет в кандидате, их поставил код в вид ревьюера; оставь эти находки с severity INFO, правки по ним убери, "
            "вердикт вынеси по остальному (ПРИНЯТО, если больше ничего); отдай ответ единственным повтором стадии (--transport-attempts 2), "
            "ничего не записано")
    if verdict == "AUTO_FIX_APPLIED" and edits_dropped and "successor_edits" not in _filtered(canaries, answer) \
            and "successor_document" not in (answer.get("artifacts") or {}):
        named = ", ".join(sorted(set(edits_dropped)))
        raise ValueError(f"REVIEW_ONLY_CANARY_FIXES: все правки ответа — по контрольным ошибкам ({named}); fix: {tail}")
    if verdict == "ТРЕБУЕТ ДОРАБОТКИ":
        named = only_canary_verdict(canaries, answer)
        if named:
            raise ValueError(f"REVIEW_CANARY_ONLY_VERDICT: отказ держится только на контрольных ошибках ({'; '.join(named)}); fix: {tail}")


def _filtered(canaries: Sequence[Mapping[str, Any]], answer: Mapping[str, Any]) -> dict[str, Any]:
    return filter_edits(canaries, answer.get("artifacts") or {})[0]


# --- outcome (derived on demand from the journal; nothing is stored) -------------------------------

def outcome(canaries: Sequence[Mapping[str, Any]] | None, answer: Mapping[str, Any] | None) -> dict[str, Any]:
    if canaries is None:
        return {"status": "NOT_SEEDED", "seeded": None, "caught": None, "warning": "REVIEW_UNMEASURED",
                "note": "ревью не измерено канарейками (прогон открыт до P7)"}
    seeded = len(canaries)
    if seeded == 0:
        return {"status": "NONE", "seeded": 0, "caught": 0, "warning": None, "note": "канареек нет: в кандидате нечего мутировать"}
    if answer is None:
        return {"status": "PENDING", "seeded": seeded, "caught": None, "warning": None, "note": "ответ ревьюера ещё не записан"}
    hits = hit_ids(canaries, answer)
    missed = [row for row in canaries if row["canary_id"] not in hits]
    classes = sorted({row["class"] for row in missed})
    if not hits:
        status, warning = "UNRELIABLE", "REVIEW_UNRELIABLE"
        note = f"ревью ненадёжно (REVIEW_UNRELIABLE): ни одна из {seeded} контрольных ошибок не поймана, классы {', '.join(classes)}"
    elif missed:
        status, warning = "WEAK", "REVIEW_WEAK"
        note = f"ревью слабое: пропущено {len(missed)} из {seeded}, классы {', '.join(classes)}"
    else:
        status, warning, note = "ALL_CAUGHT", None, f"поймано {seeded} из {seeded} контрольных ошибок"
    return {"status": status, "seeded": seeded, "caught": len(hits), "missed_classes": classes, "hits": hits, "warning": warning,
            "note": note + "; " + NOTE}


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
    "no_word": "генератор слова не получал (его вывод model-request потерян или запрос до P7): сверять нечего",
}
