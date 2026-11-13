"""P7: the control word of the generator.

- ``prepare`` of a generator gives a ``nonce`` in its output only; the request file and the journal hold its sha256;
- the word is nowhere in the files of the run and not in the review package;
- the reviewer's ``codeword_seen`` is stored as a sha256 too; a named word is SELF whatever ``--fresh-context`` said (refused in a
  run that does not admit SELF, before anything is written); a word not named is a sign of a fresh context, not a proof."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_review_canaries import _answer, _flags, _open, _select
from tests.test_review_requested import _generated_run


def _with_words(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The nonces the fixture's generator requests gave, by stage: ``_generated_run`` drops the output of ``publish_model_request``."""
    from tools import pilot_state

    given: dict[str, str] = {}
    real = pilot_state.publish_model_request

    def spy(run_root, attempt_id, stage, **kwargs):
        published = real(run_root, attempt_id, stage, **kwargs)
        if "nonce" in published:
            given[stage] = published["nonce"]
        return published

    monkeypatch.setattr(pilot_state, "publish_model_request", spy)
    return given


def _every_file(run: dict) -> bytes:
    return b"".join(path.read_bytes() for base in (run["run_root"], run["project"] / "bundles") if base.exists()
                    for path in sorted(base.rglob("*")) if path.is_file() and path != run["run_root"] / "incoming" / "tc-reviewer-canonical.json")  # that slot is the reviewer's own file, as it wrote it


def test_the_generator_request_gives_a_nonce_and_keeps_its_sha256_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import read_model_request
    from tools.review_canaries import nonce_digest

    words = _with_words(monkeypatch)
    run = _generated_run(tmp_path)
    assert len(words) == 2 and all(len(word) == 12 for word in words.values()) and len(set(words.values())) == 2
    events = [json.loads(line) for line in (run["run_root"] / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    for stage, word in words.items():
        digest = next(row["artifact_digest"] for row in events if row["event_type"] == "MODEL_REQUESTED" and row["stage_instance_id"] == stage)
        request = read_model_request(*_flags(run), stage, digest)
        assert request["nonce_sha256"] == nonce_digest(word) and "nonce" not in request
        assert word.encode() not in _every_file(run)  # in no file of the run


def test_only_the_generator_gets_a_word(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import publish_model_request

    run = _generated_run(tmp_path)
    marker = next(row for row in (json.loads(line) for line in (run["run_root"] / "events.jsonl").read_text(encoding="utf-8").splitlines())
                  if row["event_type"] == "MODEL_REQUESTED" and row["stage_instance_id"] == "context-marker:baseline")
    from tools.pilot_state import read_model_request

    assert "nonce_sha256" not in read_model_request(*_flags(run), "context-marker:baseline", marker["artifact_digest"])
    assert publish_model_request  # imported for the next test's readers


def test_the_word_is_not_in_the_review_package_the_view_or_the_open_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    words = _with_words(monkeypatch)
    run = _generated_run(tmp_path)
    opened = _open(run, monkeypatch)
    blob = json.dumps(opened, ensure_ascii=False).encode("utf-8") + _every_file(run)
    for word in words.values():
        assert word.encode() not in blob


def test_a_named_word_is_refused_in_a_run_that_does_not_admit_self_even_with_fresh_context_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    words = _with_words(monkeypatch)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch, fresh=True)  # the host said «fresh»
    before = (run["run_root"] / "events.jsonl").read_bytes()
    with pytest.raises(ValueError) as refused:
        _select(run, _answer(run, codeword=next(iter(words.values()))))
    assert "REVIEW_CODEWORD_NAMED" in str(refused.value) and "ничего не записано" in str(refused.value)
    assert (run["run_root"] / "events.jsonl").read_bytes() == before  # the stage keeps its answer: a fresh session can be asked again
    done = _select(run, _answer(run, codeword=None))  # the fresh session names nothing
    assert done["status"] == "EFFECTIVE_SELECTED" and done["codeword"]["state"] == "not_named"
    assert "не доказательство" in done["codeword"]["note"]


def test_in_a_run_that_admits_self_a_named_word_is_recorded_as_a_hash_and_marks_self(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.review_canaries import nonce_digest, recorded_answer

    words = _with_words(monkeypatch)
    run = _generated_run(tmp_path, self_review=True)
    _open(run, monkeypatch, fresh=True)
    word = words["tc-generator:BATCH-B2-refund"] if "tc-generator:BATCH-B2-refund" in words else next(iter(words.values()))
    answer = _answer(run, codeword=word)
    done = _select(run, answer)
    assert done["codeword"]["state"] == "named" and "REVIEWER_NAMED_CODEWORD" in done["review_warnings"] and "SELF" in done["codeword"]["note"]
    stored = recorded_answer(*_flags(run))
    assert stored["codeword_seen"] == nonce_digest(word)  # the word itself is not stored
    assert word.encode() not in _every_file(run)


def test_a_wrong_word_counts_as_not_named_and_is_said_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_words(monkeypatch)
    run = _generated_run(tmp_path)
    _open(run, monkeypatch)
    done = _select(run, _answer(run, codeword="выдумано"), dry_run=True)
    assert done["codeword"]["state"] == "wrong" and "REVIEWER_NAMED_CODEWORD" not in done["review_warnings"]


def test_a_request_without_a_word_leaves_nothing_to_compare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.review_canaries import codeword_state

    run = _generated_run(tmp_path)  # the word was lost with the output: the answer has nothing to be compared with only if no request had one
    answer = _answer(run, codeword="что-то")
    assert codeword_state(*_flags(run), answer) in {"wrong", "no_word"}


def test_the_reviewer_answer_schema_takes_codeword_seen_and_the_request_schema_the_hash() -> None:
    from tools.schema_validation import schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    answer = {"schema_version": "5.0.0", "stage": "tc-reviewer", "warnings": [], "codeword_seen": None, "artifacts": {"validation_report": {
        "verdict": "ПРИНЯТО", "candidate": {"document_id": "TCDOC-x", "revision": 1, "document_sha256": "sha256:" + "a" * 64},
        "effective": {"document_id": "TCDOC-x", "revision": 1, "document_sha256": "sha256:" + "a" * 64}, "reviewed_case_ids": ["TC-1"],
        "findings": [], "corrections": []}}}
    schema = root / "schemas" / "tc-reviewer-output.schema.json"
    assert schema_diagnostics(answer, schema, root) == []
    assert schema_diagnostics({**answer, "codeword_seen": "слово"}, schema, root) == []
    assert schema_diagnostics({**answer, "codeword_seen": 5}, schema, root) != []
