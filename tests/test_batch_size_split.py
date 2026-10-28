"""FaT-BO: tc-generator batches by the expected answer size; groups of requirements are never torn."""

import hashlib
import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "fat-bo"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _evidence() -> dict:
    return {"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": "sha256:" + "d" * 64}


def _requirement(number: int, text: str) -> dict:
    return {"source_requirement_id": f"SREQ-{number:04d}", "display_order": number, "text": text,
            "provenance": [f"docs/req.md — {number}"], "digest": "sha256:" + f"{number:064x}"}


def _owners(plan: dict) -> dict[str, int]:
    return {source: batch["ordinal"] for batch in plan["batches"] for source in batch["owned_source_requirement_ids"]}


@pytest.mark.parametrize("project", ["petclinic", "microservices"])
def test_campaign_single_batch_plan_reads_byte_identical_without_budget(project):
    from tools.batch_assembly import _plan_batches, canonical_bytes, plan_batches

    recorded = _load(f"{project}-plan.json")
    plan = plan_batches(recorded["source_requirements"], _load(f"{project}-grouping.json"))

    assert canonical_bytes(plan) == canonical_bytes(recorded)
    assert len(_plan_batches(recorded)) == 1


def test_project_that_fits_keeps_the_one_batch_plan_byte_identical():
    from tools.batch_assembly import canonical_bytes, plan_batches

    recorded = _load("microservices-plan.json")
    plan = plan_batches(recorded["source_requirements"], _load("microservices-grouping.json"),
                        window_bytes=1_000_000, input_bytes=198_000)

    assert canonical_bytes(plan) == canonical_bytes(recorded)
    assert any("одна пачка" in note for note in plan.notes)


def test_removed_independence_evidence_no_longer_splits():
    from tools.batch_assembly import plan_batches

    evidence = dict(_evidence(), complete=True, independence_proven=True, groups=[
        {"group_id": "a", "source_requirement_ids": ["SREQ-0001"], "independence_evidence": ["target:a"]},
        {"group_id": "b", "source_requirement_ids": ["SREQ-0002"], "independence_evidence": ["target:b"]},
    ])
    plan = plan_batches([_requirement(1, "O01. Создать"), _requirement(2, "P01. Добавить")], evidence)

    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001", "SREQ-0002"]]


def test_petclinic_campaign_answer_is_split_into_two_batches_covering_every_requirement_once():
    from tools.batch_assembly import plan_batches

    recorded = _load("petclinic-plan.json")
    plan = plan_batches(recorded["source_requirements"], _load("petclinic-grouping.json"),
                        window_bytes=1_000_000, input_bytes=385_000)

    assert len(plan["batches"]) == 2
    owned = [source for batch in plan["batches"] for source in batch["owned_source_requirement_ids"]]
    assert sorted(owned) == sorted(item["source_requirement_id"] for item in recorded["source_requirements"])
    assert [batch["namespace"] for batch in plan["batches"]] == ["B1", "B2"]
    assert any("пачек 2" in note for note in plan.notes)


def test_id_groups_are_never_torn_between_batches():
    from tools.batch_assembly import plan_batches

    recorded = _load("microservices-plan.json")
    texts = {item["source_requirement_id"]: item["text"] for item in recorded["source_requirements"]}
    plan = plan_batches(recorded["source_requirements"], _load("microservices-grouping.json"),
                        window_bytes=600_000, input_bytes=198_000)

    assert len(plan["batches"]) >= 2
    owners = _owners(plan)
    for letter in ("O", "P", "X"):
        members = [source for source, text in texts.items() if text.startswith(letter + "0")]
        assert len(members) >= 2 and len({owners[source] for source in members}) == 1, letter


def test_group_over_the_batch_budget_is_its_own_batch_with_a_warning():
    from tools.batch_assembly import plan_batches

    requirements = [_requirement(1, "A01. " + "а" * 400), _requirement(2, "B01. " + "б" * 4000), _requirement(3, "C01. " + "в" * 400)]
    plan = plan_batches(requirements, _evidence(), window_bytes=200_000, input_bytes=40_000)

    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001"], ["SREQ-0002"], ["SREQ-0003"]]
    assert any(note.startswith("BATCH_GROUP_OVER_BUDGET") and "SREQ-0002" in note for note in plan.notes)


def test_zero_window_disables_the_split():
    from tools.batch_assembly import plan_batches

    requirements = [_requirement(1, "A01. " + "а" * 40000), _requirement(2, "B01. " + "б" * 40000)]
    plan = plan_batches(requirements, _evidence(), window_bytes=0, input_bytes=900_000)

    assert len(plan["batches"]) == 1


def test_id_pattern_with_a_named_group_defines_the_group():
    import re

    from tools.batch_assembly import plan_batches

    requirements = [_requirement(1, "3.1 Первое " + "а" * 1000), _requirement(2, "3.2 Второе " + "а" * 1000),
                    _requirement(3, "4.1 Третье " + "а" * 1000), _requirement(4, "4.2 Четвёртое " + "а" * 1000)]
    plan = plan_batches(requirements, _evidence(), window_bytes=400_000, input_bytes=100_000,
                        id_pattern=re.compile(r"(?P<group>\d+)\.\d+\s"))

    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001", "SREQ-0002"], ["SREQ-0003", "SREQ-0004"]]


def test_ids_without_a_letter_group_fall_back_to_document_sections():
    from tools.batch_assembly import plan_batches

    body = "а" * 1000
    texts = [f"REQ-1 Оплата {body}", f"REQ-2 Отмена {body}", f"REQ-3 Возврат {body}", f"REQ-4 Отчёт {body}"]
    document = "# Платежи\n\n## Оплата\n\n" + texts[0] + "\n\n" + texts[1] + "\n\n## Возвраты\n\n" + texts[2] + "\n\n" + texts[3] + "\n"
    requirements = [_requirement(number, text) for number, text in enumerate(texts, start=1)]
    plan = plan_batches(requirements, _evidence(), window_bytes=400_000, input_bytes=100_000,
                        documents={"docs/req.md": document})

    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001", "SREQ-0002"], ["SREQ-0003", "SREQ-0004"]]
    assert not any("ни в одну группу" in note for note in plan.notes)


def test_requirement_outside_every_group_is_named_in_the_notes():
    from tools.batch_assembly import plan_batches

    requirements = [_requirement(1, "REQ-1 " + "а" * 3000), _requirement(2, "REQ-2 " + "б" * 3000), _requirement(3, "REQ-3 " + "в" * 3000)]
    plan = plan_batches(requirements, _evidence(), window_bytes=400_000, input_bytes=100_000, documents={})

    assert len(plan["batches"]) >= 2
    unplaced = [note for note in plan.notes if "не попали ни в одну группу" in note]
    assert len(unplaced) == 1 and all(f"SREQ-000{number}" in unplaced[0] for number in (1, 2, 3))


def _context_receipt() -> dict:
    receipt = {"schema_version": "1.0.0", "inventory_digest": "sha256:" + "e" * 64, "batch_index": 1,
               "files": [], "byte_count": 0, "byte_set_digest": "sha256:" + "f" * 64}
    receipt["digest"] = "sha256:" + hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return receipt


def _split_fragments(capability_ids):
    from tests.test_batch_assembly import _fragment, _header, _header_digest, _refresh
    from tools.batch_assembly import plan_batches

    header, context = _header(), _context_receipt()
    requirements = [_requirement(1, "A01. " + "а" * 4000), _requirement(2, "B01. " + "б" * 4000)]
    plan = plan_batches(requirements, {"header_digest": _header_digest(header), "context_receipt_digest": context["digest"]},
                        window_bytes=300_000, input_bytes=100_000)
    assert len(plan["batches"]) == 2
    fragments = []
    for index, capability in enumerate(capability_ids):
        fragment = _fragment(plan, context, index)
        fragment["operation_capabilities"][0]["capability_id"] = capability.format(ns=fragment["namespace"])
        _refresh(fragment)
        fragments.append(fragment)
    return header, context, plan, fragments


def test_split_assembly_keeps_duplicate_warning_and_refuses_an_uncovered_requirement():
    from tests.test_batch_assembly import _refresh
    from tools.batch_assembly import BatchAssemblyError, assemble_candidate, validate_fragment

    header, context, plan, fragments = _split_fragments(["CAP-shared", "CAP-shared"])
    document, receipt = assemble_candidate(header, plan, fragments)
    assert len(document["test_cases"]) == 2
    assert [item["code"] for item in receipt["warnings"]] == ["POSSIBLE_DUPLICATE"]

    fragments[1]["source_to_canonical_mappings"] = []
    _refresh(fragments[1])
    with pytest.raises(BatchAssemblyError):
        validate_fragment(fragments[1], plan, header, context)


def test_api_plan_batches_reads_the_window_and_measures_the_step_input(tmp_path, capsys):
    from tools import api

    project = tmp_path / "project"
    (project / "docs").mkdir(parents=True)
    document = "# Требования\n\n## Оплата\n\nREQ-1 Оплата " + "а" * 3000 + "\n\n## Возвраты\n\nREQ-2 Возврат " + "б" * 3000 + "\n"
    (project / "docs" / "req.md").write_text(document, encoding="utf-8")
    from tests.test_phase2_scan import _skillsrc
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    manifest = parse_skillsrc_bytes(_skillsrc())
    manifest["limits"] = {"step_window_bytes": 1_000_000}
    (project / ".skillsrc").write_bytes(_payload(manifest))
    data = (project / "docs" / "req.md").read_bytes()
    receipt = {"schema_version": "1.0.0", "inventory_digest": "sha256:" + "e" * 64, "batch_index": 1,
               "files": [{"opaque_id": "file-" + "a" * 24, "project_path": "docs/req.md",
                          "content_digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)}],
               "byte_count": len(data), "byte_set_digest": "sha256:" + "f" * 64}
    receipt["digest"] = "sha256:" + hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    run_tmp = project / ".pilot-runs" / "run1" / "tmp"
    run_tmp.mkdir(parents=True)
    marker = run_tmp / "marker.json"
    marker.write_bytes(b"{" + b" " * 700_000 + b"}")
    requirements = [_requirement(1, "REQ-1 Оплата " + "а" * 3000), _requirement(2, "REQ-2 Возврат " + "б" * 3000)]
    (run_tmp / "req.json").write_text(json.dumps(requirements, ensure_ascii=False), encoding="utf-8")
    (run_tmp / "grouping.json").write_text(json.dumps({"header_digest": "sha256:" + "c" * 64, "context_receipt_digest": receipt["digest"]}), encoding="utf-8")
    (run_tmp / "context.json").write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    code = api.main(["plan-batches", "--requirements", str(run_tmp / "req.json"), "--grouping", str(run_tmp / "grouping.json"),
                     "--project", str(project), "--input-file", str(marker), "--context-receipt", str(run_tmp / "context.json"),
                     "--out", str(run_tmp / "plan.json")])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0 and payload["status"] == "ok", payload
    plan = json.loads((run_tmp / "plan.json").read_text(encoding="utf-8"))
    assert [batch["owned_source_requirement_ids"] for batch in plan["batches"]] == [["SREQ-0001"], ["SREQ-0002"]]
    assert any("пачек 2" in note for note in payload["notes"])


def _three_batch_fragments():
    from tests.test_batch_assembly import _fragment, _header, _header_digest
    from tools.batch_assembly import plan_batches

    header, context = _header(), _context_receipt()
    requirements = [_requirement(number, letter + "01. " + "а" * 4000) for number, letter in enumerate("ABC", start=1)]
    plan = plan_batches(requirements, {"header_digest": _header_digest(header), "context_receipt_digest": context["digest"]},
                        window_bytes=300_000, input_bytes=100_000)
    assert len(plan["batches"]) == 3
    return header, context, plan, [_fragment(plan, context, index) for index in range(3)]


def test_prior_capabilities_are_the_capabilities_of_the_earlier_batches_with_a_self_digest():
    from tests.test_batch_assembly import _refresh
    from tools.batch_assembly import BatchAssemblyError, prior_capabilities, with_digest

    header, context, plan, fragments = _three_batch_fragments()
    extra = dict(fragments[1]["operation_capabilities"][0], capability_id="CAP-other", action="other")
    fragments[1]["operation_capabilities"].append(extra)
    _refresh(fragments[1])
    third = plan["batches"][2]["batch_id"]

    value = prior_capabilities(plan, third, [fragments[1], fragments[0]])

    assert value["batch_id"] == third and value["plan_digest"] == plan["digest"]
    assert [row["batch_id"] for row in value["fragment_digests"]] == [plan["batches"][0]["batch_id"], plan["batches"][1]["batch_id"]]
    assert [item["capability_id"] for item in value["operation_capabilities"]] == ["CAP-other", "CAP-shared"]
    assert value["digest"] == with_digest({key: item for key, item in value.items() if key != "digest"})["digest"]
    with pytest.raises(BatchAssemblyError, match="first batch"):
        prior_capabilities(plan, plan["batches"][0]["batch_id"], [])
    with pytest.raises(BatchAssemblyError, match=plan["batches"][1]["namespace"]):
        prior_capabilities(plan, third, [fragments[0]])


def test_a_later_batch_must_reuse_the_capabilities_of_the_earlier_ones_before_the_answer_is_recorded():
    from tests.test_batch_assembly import _refresh
    from tools.batch_assembly import BatchAssemblyError, validate_fragment

    header, context, plan, fragments = _three_batch_fragments()
    validate_fragment(fragments[1], plan, header, context, [fragments[0]])
    with pytest.raises(BatchAssemblyError, match="--prior-fragment"):
        validate_fragment(fragments[1], plan, header, context)

    changed = fragments[1]
    changed["operation_capabilities"][0]["action"] = "different"
    _refresh(changed)
    with pytest.raises(BatchAssemblyError, match="CAP-shared") as refused:
        validate_fragment(changed, plan, header, context, [fragments[0]])
    assert '"action":"call"' in str(refused.value)

    renamed = fragments[2]
    renamed["operation_capabilities"][0]["capability_id"] = "CAP-renamed"
    _refresh(renamed)
    with pytest.raises(BatchAssemblyError, match="CAP-shared"):
        validate_fragment(renamed, plan, header, context, [fragments[0], fragments[1]])


def test_api_validate_fragment_with_prior_fragments(tmp_path, capsys):
    from tools import api

    header, context, plan, fragments = _three_batch_fragments()
    run_tmp = tmp_path / ".pilot-runs" / "run1" / "tmp"
    run_tmp.mkdir(parents=True)
    files = {"plan": plan, "header": header, "context": context, "f1": fragments[0], "f2": fragments[1]}
    for name, value in files.items():
        (run_tmp / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    code = api.main(["validate-fragment", "--fragment", str(run_tmp / "f2.json"), "--plan", str(run_tmp / "plan.json"),
                     "--header", str(run_tmp / "header.json"), "--context-receipt", str(run_tmp / "context.json"),
                     "--prior-fragment", str(run_tmp / "f1.json")])
    assert code == 0, capsys.readouterr().out
    capsys.readouterr()
    code = api.main(["validate-fragment", "--fragment", str(run_tmp / "f1.json"), "--plan", str(run_tmp / "plan.json"),
                     "--header", str(run_tmp / "header.json"), "--context-receipt", str(run_tmp / "context.json")])
    assert code == 0, capsys.readouterr().out


def test_group_of_several_requirements_over_the_budget_is_cut_in_order_into_batches_under_the_budget():
    from tools.batch_assembly import ANSWER_PER_TEXT_BYTE, plan_batches

    requirements = [_requirement(number, f"FR-{number} " + "а" * 1500) for number in range(1, 7)]
    requirements.append(_requirement(7, "NFR-1 " + "б" * 1500))
    plan = plan_batches(requirements, _evidence(), window_bytes=400_000, input_bytes=100_000)

    cap = int(400_000 * 0.85) - 100_000 - len(json.dumps(requirements, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    sizes = {item["source_requirement_id"]: len(item["text"].encode("utf-8")) * ANSWER_PER_TEXT_BYTE for item in requirements}
    owned = [batch["owned_source_requirement_ids"] for batch in plan["batches"]]
    fr = [ids for ids in owned if any(source != "SREQ-0007" for source in ids)]
    assert len(fr) >= 2 and all(sum(sizes[source] for source in ids) <= cap for ids in owned)
    assert [source for ids in owned for source in ids] == [f"SREQ-{number:04d}" for number in range(1, 8)]
    note = next(note for note in plan.notes if note.startswith("BATCH_GROUP_OVER_BUDGET"))
    assert "разрезана" in note and "SREQ-0001" in note and "|" in note
