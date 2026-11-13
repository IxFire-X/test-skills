"""F4a: the texts of the frequent step say what the code and the schemas say.

Every field and command name in the texts is checked against a real marker answer, a schema or the parser — not against a copy of the
words.  The regression it closes (PT2): the fix of ``plan`` and the sample called the requirements ``source_requirements`` of the marker
answer, a field that answer does not have, and gave the provenance as ``file#Lstart-Lend`` while the order check reads ``path:line``."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "orchestrate" / "SKILL.md"
SAMPLE = ROOT / "skills" / "orchestrate" / "references" / "fragment-sample.md"
CONTRACT = ROOT / "skills" / "orchestrate" / "references" / "orchestration-contract.md"
GENERATOR = ROOT / "skills" / "tc-generator" / "SKILL.md"
SKILL_BYTES_LIMIT = 24_560  # SKILL.md of orchestrate after V8 (52 492 after PT2): the agent at work reads it whole; the limit only goes down
GENERATOR_ROLE_BYTES_LIMIT = 35_826  # tc-generator SKILL + references before F4a


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_the_order_check_reads_the_provenance_as_path_and_line_not_as_a_fragment() -> None:
    from tools.pilot_state import _PROVENANCE_LINE

    for filled in ("docs/feature.md:4", "docs/feature.md:4-7"):
        assert _PROVENANCE_LINE.match(filled)
    assert not _PROVENANCE_LINE.match("docs/feature.md#L4-L7")  # the form PT2 gave: the order check skips it without a word


# ---- A5-1 = 8.7: model-response prints a receipt; --out leaves only the file ---------------------------------------------


def test_model_response_prints_a_receipt_without_the_body_and_the_text_says_so() -> None:
    import inspect

    from tools import api

    source = inspect.getsource(api._cmd_model_response)
    assert 'if key != "artifact"' in source  # the receipt, not the answer
    text = _flat(SKILL)
    assert "весь артефакт" not in text and "сотни КБ" not in text
    assert "и печатает квитанцию" in text and "не тело" in text  # V5-B1: the one cycle — `submit` prints the receipt
    for key in ("`result.path`", "`content_digest`", "`output_bytes`", "`warning`"):
        assert key in text, key


def test_the_out_text_matches_what_main_prints_with_out(tmp_path: Path) -> None:
    from tests.test_output_guard import _run
    from tools import api

    _project, run_root, _attempt = _run(tmp_path)  # SEC-A: --out is written through confined_output, in <run>/tmp/
    assert set(api._write(run_root / "tmp" / "x.json", {"result": 1}, run_root)) == {"out", "bytes", "sha256"}


# ---- A5-2 + A5-4: the review is two commands everywhere -------------------------------------------------------------------


def test_the_map_of_calls_and_how_to_use_describe_the_review_through_prepare_and_submit() -> None:
    rows = [row for row in CONTRACT.read_text(encoding="utf-8").splitlines() if row.startswith("| ") and ("Ревью" in row or "review" in row.lower())]
    table = "\n".join(rows)
    assert "`tools.api prepare --stage tc-reviewer:canonical`" in table and "`tools.api submit --stage tc-reviewer:canonical`" in table
    for old in ("session-start", "review-requested", "complete-review"):
        assert old not in table, old
    how = _flat(ROOT / "HOW-TO-USE.md")
    assert "девяти команд" not in how and "deprecated" not in how and "v0.1" not in how
    assert "`prepare --stage tc-reviewer:canonical`" in how and "`submit --dry-run`" in how


# ---- O-13 / A5-11: the answer without indentation; who sets the digest fields ----------------------------------------------


def test_the_generator_and_the_sample_ask_for_json_without_indentation() -> None:
    phrase = "JSON без отступов"
    assert phrase in _flat(GENERATOR) or "JSON in one line" in _flat(GENERATOR)
    sample = _flat(SAMPLE)
    assert phrase in sample
    assert "не одной строкой на сотни килобайт" not in sample  # read as «indent it»
    assert "retry_prompt" in _flat(SKILL)


def test_the_generator_skill_and_the_sample_say_the_same_about_the_digest_fields() -> None:
    generator = _flat(GENERATOR)
    assert "the code puts its `digest` and plan/header/context digests" not in generator
    assert "The code puts the mechanics (digest fields, `context_receipt`," in generator and "do not write it" in generator  # since V6 the code puts them; the model writes none
    sample = _flat(SAMPLE)
    assert "Код ставит сам (не пиши)" in sample and "`context_receipt`, digest-поля" in sample  # the sample says the same: not written by the model
    assert "можно опустить: поставит model-response" not in sample


def test_the_fragment_without_the_four_digest_fields_passes_validate_fragment_and_model_response_fills_them() -> None:
    import copy

    from tests.test_diagnostics_at_once import _synthetic
    from tools.batch_assembly import fill_fragment_bindings, validate_fragment

    fragment, plan, header, context = _synthetic()
    digests = ("plan_digest", "header_digest", "context_receipt_digest", "digest")
    schema = json.loads((ROOT / "schemas" / "candidate-fragment.schema.json").read_text(encoding="utf-8"))
    assert set(digests) <= set(schema["required"])  # the schema asks for them, the code puts them: the sample says «can be omitted»
    bare = {key: value for key, value in copy.deepcopy(fragment).items() if key not in digests}
    assert validate_fragment(bare, plan, header, context) is None
    filled = fill_fragment_bindings(copy.deepcopy(bare), plan)
    assert {key: filled[key] for key in digests} == {key: fragment[key] for key in digests}


# ---- O-17: one step-inputs per batch, a dry run only on doubt ------------------------------------------------------------


def test_the_skill_asks_for_one_step_size_call_per_batch_and_names_the_key_of_each_command() -> None:
    text = _flat(SKILL)
    assert "один вызов размера на пачку" in text
    assert "`files_summary`" in text and "input_files_summary" not in text  # V0: one key for both commands
    assert "`list_path`" in text and "`step_size`" in text  # V5-B1: the list is in `read` of prepare, no command of its own
    assert "`--dry-run` — только проверки" in text
    assert "оба" not in text.split("один вызов размера на пачку", 1)[1][:200]  # not «step-inputs and step_size»


def test_the_key_names_of_the_two_size_commands_are_those_the_skill_gives() -> None:
    import inspect

    from tools import step_size

    source = inspect.getsource(step_size)
    assert '"files_summary"' in source and "files_summary=" in source and "input_files_summary" not in source


# ---- the size ----------------------------------------------------------------------------------------------------------


def test_the_controller_skill_is_not_longer_than_after_pt2_and_the_generator_role_is_not_over_its_base() -> None:
    assert SKILL.stat().st_size <= SKILL_BYTES_LIMIT
    role = ROOT / "skills" / "tc-generator"
    total = (role / "SKILL.md").stat().st_size + sum(path.stat().st_size for path in (role / "references").glob("*.md"))
    assert total <= GENERATOR_ROLE_BYTES_LIMIT, total


def test_no_command_of_the_texts_gives_model_response_the_out_flag() -> None:
    """With ``--out`` the stdout holds no ``result.path``: the texts that send the agent to ``result.path`` must not put ``--out`` in the command."""
    offenders = []
    for path in [*(ROOT / "skills").rglob("*.md"), ROOT / "HOW-TO-USE.md", ROOT / "README.md"]:
        text = path.read_text(encoding="utf-8")
        fence = r"```[a-z]*" + chr(10) + r"(.*?)```"
        blocks = re.findall(fence, text, re.S)
        spans = [" ".join(span.split()) for span in re.findall(r"`([^`]+)`", re.sub(fence, "", text, flags=re.S))]
        spans += [" ".join(block.split()) for block in blocks]
        offenders += [f"{path.name}: {span}" for span in spans if "model-response" in span and "--out" in span]
    assert offenders == []
