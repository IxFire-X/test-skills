"""V6b: the files of a partially accepted set — the numbers of the reviewer's set, the header, the gaps, the rejected cases, no CSV for them.

Pure functions over the example document (see test_partial_acceptance): ``section_labels``, ``partial_context``, ``build_bundle(partial=…)``,
``status_payloads(…, "REJECTED-CASES")``."""
from __future__ import annotations

import pytest

from tests.test_partial_acceptance import _finding, _grouped, _group_ids, _outcome, _real, r1_document

PROFILE = "zephyr-scale-step-row-24-v5"


def _partial(document: dict, findings: list[dict]) -> tuple[dict, dict]:
    from tools.publish_test_case_bundle import partial_context

    outcome = _outcome(document, findings)
    assert outcome["kind"] == "PARTIAL", outcome
    return outcome["effective"], partial_context(document, outcome)


def _markdown(effective: dict, partial: dict) -> str:
    from tools.publish_test_case_bundle import build_bundle

    return build_bundle(effective, PROFILE, partial=partial).markdown_bytes.decode("utf-8")


# --- the numbers ----------------------------------------------------------------------------------

def test_a_group_is_one_section_and_the_next_case_follows_it() -> None:
    from tools.test_case_projections import section_labels

    document = _grouped()
    labels, ids = section_labels(document), _group_ids(document)
    assert len({labels[item] for item in ids}) == 1
    assert labels[ids[0]] == 1 and labels["TC-B1-002"] == 2 and document["test_cases"][3]["case_id"] == "TC-B1-002"  # three variants, one section


def test_the_cases_of_a_subtraction_keep_the_numbers_of_the_set_the_reviewer_read() -> None:
    effective, partial = _partial(r1_document(), _real(r1_document(), 1))
    headings = [line for line in _markdown(effective, partial).splitlines() if line.startswith("## ТК-")]
    assert headings[0].startswith("## ТК-2. ") and len(headings) == 63 and not any(item.startswith("## ТК-1. ") for item in headings)


def test_a_set_with_nothing_taken_out_is_numbered_as_before() -> None:
    from tools.publish_test_case_bundle import build_bundle

    document = r1_document()
    assert build_bundle(document, PROFILE).markdown_bytes == build_bundle(document, PROFILE, partial=None).markdown_bytes
    assert b"## \xd0\xa2\xd0\x9a-1. " in build_bundle(document, PROFILE).markdown_bytes


# --- the header and the gaps ----------------------------------------------------------------------

def test_the_header_says_what_is_taken_out_where_it_lies_and_what_is_left_without_a_case() -> None:
    document = r1_document()
    effective, partial = _partial(document, _real(document, 1))
    title = document["test_cases"][0]["title"]
    head = _markdown(effective, partial).split("\n\n", 2)[1]
    assert head.startswith("> **Принят частично: 63 из 64 кейсов.** Кейс ТК-1 «") and "ревью не прошёл и в этот набор не входит. Он лежит в файле" in head
    assert "TCDOC-" in head and ".r2.REJECTED-CASES.md вместе с замечаниями ревьюера." in head
    assert "Без принятого кейса остались 3 требования — см. раздел «Пробелы покрытия» в конце." in head and title.split()[0] in head


def test_several_cases_are_named_and_more_than_five_only_by_number() -> None:
    document = r1_document()
    few = _markdown(*_partial(document, _real(document, 1, 2)))
    assert "Кейсы ТК-1 «" in few and ", ТК-2 «" in few and "ревью не прошли и в этот набор не входят. Они лежат" in few
    many = _markdown(*_partial(document, _real(document, 1, 2, 3, 4, 5, 6)))
    assert "Кейсы ТК-1, ТК-2, ТК-3, ТК-4, ТК-5, ТК-6 ревью не прошли" in many


def test_the_gaps_are_the_last_section_with_the_reviewers_words() -> None:
    document = r1_document()
    finding = _finding("BLOCKING", [document["test_cases"][0]["case_id"]], "Шаг 6 не проверяет, что питомец не изменился.")
    text = _markdown(*_partial(document, [finding]))
    section = text[text.index("## Пробелы покрытия"):]
    assert "| Требование | Почему нет кейса | Что сказал ревьюер |" in section and section.count("\n| CREQ-") == 3
    assert "| CREQ-B1-008 | Его покрывали только отклонённые кейсы: ТК-1 | Шаг 6 не проверяет, что питомец не изменился. |" in section
    assert "## ТК-64." in text[:text.index("## Пробелы покрытия")]


def test_no_gap_section_and_no_gap_sentence_when_every_requirement_keeps_a_case() -> None:
    from tools.publish_test_case_bundle import build_bundle, partial_context
    from tools.revision_selection import review_outcome

    document = r1_document()
    document["test_cases"][1]["requirement_ids"] = sorted({*document["test_cases"][1]["requirement_ids"], *document["test_cases"][0]["requirement_ids"]},
                                                          key=lambda item: [row["requirement_id"] for row in document["requirements"]].index(item))
    outcome = review_outcome(document, _report_for(document, _real(document, 1)), (), 20)
    partial = partial_context(document, outcome)
    text = build_bundle(outcome["effective"], PROFILE, partial=partial).markdown_bytes.decode("utf-8")
    assert partial["gaps"] == [] and "Пробелы покрытия" not in text and "Без принятого кейса" not in text


def _report_for(document: dict, findings: list[dict]) -> dict:
    from tests.test_partial_acceptance import _report

    return _report(document, *findings)


def test_the_html_has_the_header_and_the_gaps_too_and_the_model_text_is_escaped() -> None:
    from tools.publish_test_case_bundle import build_bundle

    document = r1_document()
    effective, partial = _partial(document, [_finding("BLOCKING", [document["test_cases"][0]["case_id"]], "<b>Нет</b> | проверки")])
    page = build_bundle(effective, PROFILE, partial=partial).preview_bytes.decode("utf-8")
    assert "<strong>Принят частично: 63 из 64 кейсов.</strong>" in page and "<h2>Пробелы покрытия</h2>" in page
    assert "&lt;b&gt;Нет&lt;/b&gt; | проверки" in page and "<b>Нет</b>" not in page
    assert "<h2>ТК-2. " in page and "<h2>ТК-1. " not in page


def test_the_import_csv_holds_only_what_is_accepted_and_the_bundle_verifies_only_with_its_context(tmp_path) -> None:
    from tools.publish_test_case_bundle import BundleMismatchError, publish_bundle, verify_bundle

    document = r1_document()
    effective, partial = _partial(document, _real(document, 1))
    receipt = publish_bundle(effective, tmp_path, PROFILE, partial=partial)
    csv_text = (tmp_path / f"{effective['document_id']}.r2.zephyr-scale.csv").read_text(encoding="utf-8")
    assert document["test_cases"][0]["title"] not in csv_text and document["test_cases"][1]["title"] in csv_text
    assert verify_bundle(effective, tmp_path, PROFILE, partial=partial) == receipt
    with pytest.raises(BundleMismatchError):
        verify_bundle(effective, tmp_path, PROFILE)


# --- the rejected cases ---------------------------------------------------------------------------

def _rejected_files(document: dict, findings: list[dict]) -> dict[str, str]:
    from tools.publish_test_case_bundle import rejected_cases, status_payloads

    effective, partial = _partial(document, findings)
    shown, extras, intro = rejected_cases(document, partial, "abc123456789")
    payloads = status_payloads(shown, PROFILE, "REJECTED-CASES", extras=extras, intro=intro)
    return {suffix: data.decode("utf-8") for suffix, data in payloads}


def test_the_rejected_cases_are_whole_with_their_numbers_requirements_and_findings_and_a_csv_never() -> None:
    document = r1_document()
    files = _rejected_files(document, [_finding("BLOCKING", [document["test_cases"][2]["case_id"]], "Нет <проверки> | состояния.")])
    text = files["md"]
    assert text.startswith("# НЕ ПРИНЯТЫ РЕВЬЮЕРОМ — не импортировать в Zephyr\n\nПрогон abc") and set(files) == {"html", "md"}
    assert "## ТК-3. " in text and "**Требования:** CREQ-B1-001" in text and "Без другого принятого кейса: CREQ-B1-011" in text
    assert "**Замечание ревьюера (блокирующее, REVIEW\\_NOTE):**" in text and "Нет \\<проверки\\> \\| состояния" in text
    assert "&lt;проверки&gt;" in files["html"] and "<h2>ТК-3. " in files["html"] and "НЕ ПРИНЯТЫ РЕВЬЮЕРОМ" in files["html"]


def test_a_variant_of_a_group_stands_alone_in_the_rejected_file_and_one_dragged_with_its_group_says_so() -> None:
    document = _grouped()
    ids = _group_ids(document)
    one = _rejected_files(document, [_finding("BLOCKING", [ids[1]])])["md"]
    assert "\n## ТК-1. " in one and "Вынесен вместе с группой" not in one  # the variant keeps the number of its section: the group is ТК-1 in the set too
    both = _rejected_files(document, [_finding("BLOCKING", ids[:2])])["md"]
    assert both.count("**Вынесен вместе с группой параметров:**") == 1 and both.count("\n## ТК-") == 3


def test_the_warnings_of_the_projections_survive_a_partial_bundle() -> None:
    from tools.publish_test_case_bundle import build_bundle

    document = r1_document()
    document["metadata"]["documentation"] = []
    effective, partial = _partial(document, _real(document, 1))
    assert any(item.startswith("MISSING_DOCUMENTATION") for item in build_bundle(effective, PROFILE, partial=partial).warnings)
