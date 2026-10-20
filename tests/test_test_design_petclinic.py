"""Test design (D1a step 5) on the Petclinic requirements of the 2026-10-08 record: a hand-written design of the 29
source requirements (``tests/fixtures/test-design``) passes the schema and the validator, its brief stays small, the
one-batch slice is the whole design, and the record's own cases — written before the stage — show why it exists."""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from tools import test_design as design_tools
from tools.batch_assembly import plan_batches
from tools.schema_validation import schema_diagnostics

ROOT = Path(__file__).resolve().parents[1]
DESIGN = json.loads((ROOT / "tests" / "fixtures" / "test-design" / "petclinic-20261008-design.json").read_text(encoding="utf-8"))
DOCUMENT = json.loads(gzip.decompress((ROOT / "tests" / "fixtures" / "replay-petclinic-20261008" / "canonical-snapshot.json.gz").read_bytes()))["payload"]["document"]
SOURCES = DOCUMENT["source_requirements"]


def test_the_petclinic_design_is_valid_for_its_29_requirements() -> None:
    assert len(SOURCES) == 29
    assert schema_diagnostics(DESIGN, ROOT / "schemas" / "test-design.schema.json", ROOT) == []
    assert design_tools.validate_design(DESIGN, [row["source_requirement_id"] for row in SOURCES]) == []
    assert design_tools.design_warnings(DESIGN) == []
    assert [row["requirement_id"] for row in DESIGN["artifacts"]["not_designed"]] == ["SREQ-0001", "SREQ-0002", "SREQ-0029"]


def test_the_brief_holds_the_requirement_text_only_and_is_small() -> None:
    brief = design_tools.design_brief(SOURCES)
    assert [row["source_requirement_id"] for row in brief["requirements"]] == [row["source_requirement_id"] for row in SOURCES]
    assert "provenance" not in json.dumps(brief) and "src/main" not in json.dumps(brief, ensure_ascii=False)
    assert len(design_tools.canonical_bytes(brief)) < 25_000  # about 6 000 tokens, far below the 1 000 000 byte task budget


def test_a_single_batch_gets_the_whole_design() -> None:
    plan = plan_batches(SOURCES, {"header_digest": "sha256:" + "1" * 64, "context_receipt_digest": "sha256:" + "2" * 64})
    [batch] = plan["batches"]
    sliced = design_tools.design_slice(DESIGN, batch["owned_source_requirement_ids"])
    assert sliced["artifacts"] == DESIGN["artifacts"]


def test_the_recorded_cases_predate_the_design() -> None:
    cases = DOCUMENT["test_cases"]
    assert len(cases) == 81 and not any("design_refs" in case for case in cases)
    coverage = design_tools.coverage(DESIGN, cases, [])
    assert (coverage["partitions"]["covered"], coverage["boundaries"]["covered"], coverage["rules"]["covered"]) == (0, 0, 0)
    rows = design_tools.fragment_design_problems(DESIGN, cases, [], DOCUMENT["source_to_canonical_mappings"])
    assert {row["code"] for row in rows} >= {"DESIGN_REFS_MISSING", "DESIGN_ELEMENT_UNCOVERED"}
    # Their model-chosen priorities are the degenerate spread the code check reports.
    counts = design_tools.priority_counts(cases)
    assert counts == {"CRITICAL": 9, "HIGH": 69, "MEDIUM": 3, "LOW": 0}
    assert "HIGH — 69 из 81" in design_tools.priority_warning(counts)
