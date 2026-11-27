"""Package Д, J1/J2 of the full review (handoff/v08/model-answers-plan.md): a malformed answer or journal file is a typed refusal
with its way out, never a general error."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_automation_revision_budget import automated_document, automation, host_evidence, review
from tools.model_records import ModelStageArtifactInvalid
from tools.model_steps import check_model_stage_artifact


def test_a_pending_event_with_a_number_for_its_run_id_is_journal_corrupt(tmp_path: Path) -> None:
    """J1: a field of another type in the pending marker was a TypeError past ``_typed_corruption``."""
    from tests.test_run_lock import _cli, _run
    from tools import fixes
    from tools.run_store import _canonical_bytes, _sealed

    root = _run(tmp_path)
    digest = "sha256:" + "0" * 64
    marker = _sealed({"schema_version": "1.0.0", "run_id": 1, "seq": 2, "event_path": "events/0000000002.json", "event_digest": digest,
                      "bytes_digest": digest, "bytes_length": 1, "prior_digest": None, "prior_bytes_digest": digest, "prior_bytes_length": 0})
    (root / "pending-events").mkdir(exist_ok=True)
    (root / "pending-events" / "0000000002.json").write_bytes(_canonical_bytes(marker))
    answer = _cli("tools.run_pipeline", "status", "--project", str(tmp_path), "--run", root.name)
    assert answer["code"] == "JOURNAL_CORRUPT" and answer["fix"] == fixes.FIXES["JOURNAL_CORRUPT"], answer


def test_automation_with_null_artifacts_gets_the_retry_prompt() -> None:
    """J2: ``artifacts: null`` was an AttributeError before the schema."""
    answer = automation(automated_document())
    answer["artifacts"] = None
    with pytest.raises(ModelStageArtifactInvalid, match="retry_prompt"):
        check_model_stage_artifact("tc-to-autotest:r1", answer)


def test_automation_of_another_revision_is_a_retry_of_the_stage() -> None:
    with pytest.raises(ModelStageArtifactInvalid) as refused:
        check_model_stage_artifact("tc-to-autotest:r2", automation(automated_document()))
    assert "--transport-attempts 2" in str(refused.value)
    assert "- /artifacts/automation_revision: ожидается STAGE_REVISION (номер ревизии — из имени стадии: 2); пришло 1" in str(refused.value)


def test_a_review_of_another_revision_is_a_retry_of_the_stage() -> None:
    document = automated_document()
    answer = review(document, automation(document), host_evidence("s"), "ПРИНЯТО", session_id="s")
    with pytest.raises(ModelStageArtifactInvalid, match="(?s)/artifacts/autotest_review/automation_revision.*STAGE_REVISION"):
        check_model_stage_artifact("autotest-reviewer:r2", answer)
