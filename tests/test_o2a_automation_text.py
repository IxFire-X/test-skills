"""o2a: the reviewer of the automation reads the code as files and the symbols and relations as tables, not the one JSON of the answer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tests.helpers import prepare_stage
from tests.test_autotest_cycle import GENERATOR, INDEPENDENT, REVIEWER, autotest_run, run_generator
from tools import pilot_state

DIGEST = "sha256:" + "ab" * 32
OPERATION, ASSERTION = "operation", "assertion"


def _artifact(cases: int = 2, asserts: int = 3) -> dict:
    """An answer with a Java class, a Python function and a Python method, several cases and assertions."""
    files = [{"file_id": "FILE-1", "path": "src/test/java/a/B.java", "language": "java", "framework": "junit5", "content": "class B {\n\t@Test void t() {}\n}\n", "content_digest": ""},
             {"file_id": "FILE-2", "path": "tests/test_c.py", "language": "python", "framework": "pytest", "content": "def test_f():\n    assert 'é' == 'é'\n", "content_digest": ""}]
    for row in files:
        row["content_digest"] = "sha256:" + hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
    symbols = [{"file_id": "FILE-1", "symbol_id": "SYMBOL-001", "locator": {"kind": "java_class_method", "class_fqn": "a.B", "method_name": "t"}},
               {"file_id": "FILE-2", "symbol_id": "SYMBOL-002", "locator": {"kind": "python_module_function", "function_name": "test_f"}},
               {"file_id": "FILE-2", "symbol_id": "SYMBOL-003", "locator": {"kind": "python_class_method", "qualified_class_name": "TestG", "method_name": "test_m"}}]
    relations = []
    for symbol in symbols:
        for case in range(cases):
            ids = {"case_id": f"TC-{case}", "step_id": f"STEP-{case}-1", "file_id": symbol["file_id"], "symbol_id": symbol["symbol_id"]}
            relations.append({"kind": OPERATION, **ids})
            for number in range(asserts):
                relations.append({"kind": ASSERTION, **ids, "expectation_id": f"EXP-{case}-1-{number % 2}", "assertion_id": f"ASSERT-{case}-1-{number}"})
    return {"schema_version": "5.0.0", "stage": "tc-to-autotest", "warnings": ["w"], "artifacts": {
        "automation_status": "GENERATED", "automation_revision": 1, "generated_files": files, "generated_symbols": symbols,
        "implementation_relations": relations, "manual_dispositions": [{"case_id": "TC-9", "step_id": "STEP-9-1"}], "diagnostics": []}}


def _sections(text: str) -> dict[str, str]:
    return {head.strip(): body.strip("\n") for head, body in (part.split("\n", 1) for part in text.split("\n## ")[1:])}


def _read_back(parts: list[tuple[str, str, bytes]]) -> dict:
    """The inverse of the tables — here, in the test: the production code only writes them."""
    by_name = {name.split(".", 1)[1]: data for name, _basis, data in parts}
    tables = next(data for name, data in by_name.items() if name == "tables.txt").decode("utf-8")
    sections = _sections(tables)
    value = json.loads(sections["прочие поля (JSON)"])
    artifacts = value["artifacts"]
    for row in artifacts["generated_files"]:
        row["content"] = next(data for name, data in by_name.items() if name.startswith(row["file_id"] + ".")).decode("utf-8")
    artifacts["generated_symbols"] = [_symbol(line) for line in sections["символы"].split("\n")] if "символы" in sections else json.loads(sections["символы (JSON)"])
    artifacts["implementation_relations"] = (
        _relations(sections["связи"]) if "связи" in sections else json.loads(sections["связи (JSON)"]))
    return value


def _symbol(line: str) -> dict:
    symbol, file_id, kind, value = line.split(" ")
    names = {"java_class_method": ("class_fqn", "method_name"), "python_module_function": ("function_name",), "python_class_method": ("qualified_class_name", "method_name")}[kind]
    return {"file_id": file_id, "symbol_id": symbol, "locator": {"kind": kind, **dict(zip(names, value.split("#")))}}


def _relations(section: str) -> list[dict]:
    """``SYMBOL FILE TC STEP[ | operation]``, then ``  EXP`` and ``    ASSERT[ text]`` lines."""
    rows, ids, expectation = [], {}, ""
    for line in section.split("\n"):
        if not line.startswith(" "):
            head, *operation = line.split(" | ", 1)
            symbol, file_id, case, step = head.split(" ")
            ids = {"case_id": case, "step_id": step, "file_id": file_id, "symbol_id": symbol}
            rows += [{"kind": OPERATION, **ids}] if operation else []
        elif not line.startswith("    "):
            expectation = line.strip()
        else:
            rows.append({"kind": ASSERTION, **ids, "expectation_id": expectation, "assertion_id": line.split()[0]})
    return rows


def test_the_tables_read_back_into_the_symbols_and_the_relations_of_the_answer_row_by_row() -> None:
    from tools.automation_text import render

    artifact = _artifact()
    assert _read_back(render(artifact, DIGEST)) == artifact


def test_a_file_of_code_is_its_content_byte_for_byte_and_its_sha256_is_the_content_digest() -> None:
    from tools.automation_text import render

    artifact = _artifact()
    files = {row["file_id"]: row for row in artifact["artifacts"]["generated_files"]}
    parts = render(artifact, DIGEST)
    code = [(name, data) for name, basis, data in parts if basis == "код"]
    assert [name.split(".", 1)[1] for name, _data in code] == ["FILE-1.java.txt", "FILE-2.py.txt"]
    assert all(name.endswith(".txt") for name, _data in code)  # inert text: no file of a language before the review
    for (_name, data), row in zip(code, files.values()):
        assert data == row["content"].encode("utf-8") and "sha256:" + hashlib.sha256(data).hexdigest() == row["content_digest"]


@pytest.mark.parametrize("damage,section", [
    (lambda a: a["implementation_relations"][1].update(extra=1), "связи (JSON)"),
    (lambda a: a["implementation_relations"][0].update(kind="other"), "связи (JSON)"),
    (lambda a: a["implementation_relations"][0].update(case_id="TC 0"), "связи (JSON)"),
    (lambda a: a["implementation_relations"].insert(2, dict(a["implementation_relations"][0])), "связи (JSON)"),  # an operation row after the assertion of its head: its place would be lost
    (lambda a: a["generated_symbols"][0]["locator"].update(kind="unknown_kind"), "символы (JSON)"),
    (lambda a: a["generated_symbols"][1]["locator"].update(extra="x"), "символы (JSON)"),
    (lambda a: a["generated_symbols"][2].update(symbol_id="SYMBOL | 3"), "символы (JSON)"),
])
def test_a_row_or_a_locator_of_an_unknown_shape_leaves_that_part_as_the_json_it_was(damage, section: str) -> None:
    from tools.automation_text import render

    artifact = _artifact()
    damage(artifact["artifacts"])
    tables = next(data for name, _basis, data in render(artifact, DIGEST) if name.endswith(".tables.txt")).decode("utf-8")
    assert section in _sections(tables)
    other = "символы" if section.startswith("связи") else "связи"
    assert other in _sections(tables)  # the other part stays a table
    assert _read_back(render(artifact, DIGEST)) == artifact


def test_two_files_that_would_get_one_name_leave_the_whole_answer_as_it_was() -> None:
    from tools.automation_text import render

    artifact = _artifact()
    for row, name in zip(artifact["artifacts"]["generated_files"], ("FILE-a:1", "FILE-a_1")):
        row.update(file_id=name, path="tests/test_x.py")
    assert render(artifact, DIGEST) is None


def test_two_files_whose_names_differ_only_in_case_leave_the_whole_answer_as_it_was() -> None:
    """On Windows ``FILE-A.txt`` and ``FILE-a.txt`` are one file."""
    from tools.automation_text import render

    artifact = _artifact()
    for row, name in zip(artifact["artifacts"]["generated_files"], ("FILE-A", "FILE-a")):
        row.update(file_id=name, path="tests/test_x.py")
    assert render(artifact, DIGEST) is None


def test_the_text_form_of_an_answer_with_many_relations_is_smaller_than_its_json() -> None:
    from tools.automation_text import render

    artifact = _artifact(cases=30, asserts=8)
    total = sum(len(data) for _name, _basis, data in render(artifact, DIGEST))
    assert total < len(json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")) * 0.6


def _prepared(tmp_path: Path) -> dict:
    run = autotest_run(tmp_path)
    run_generator(run)
    return run


def _events(root: Path) -> list[str]:
    return [f"{event['event_type']}:{event.get('stage_instance_id')}:{event.get('artifact_digest')}" for event in pilot_state.derive_state(root)["events"]]


def test_prepare_of_the_reviewer_lists_the_code_and_the_tables_instead_of_the_json_of_the_answer(tmp_path: Path) -> None:
    run = _prepared(tmp_path)
    code, out = prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    read = out["result"]["read"]
    assert code == 0 and read["by_basis"].get("код") == 1 and read["by_basis"].get("таблицы") == 1 and "model-stage-artifacts" not in json.dumps(read), read
    views = run["root"] / "review-views"
    digest = pilot_state.read_attempt_receipt(run["root"], run["attempt"], "automation-review-boundary-r1", "ARTIFACT_READ_BACK")["record"]["automation_digest"].removeprefix("sha256:")
    assert sorted(path.name.removeprefix(digest + ".") for path in views.glob(digest + ".*")) == ["FILE-products.py.txt", "autotest-review.md", "tables.txt"]
    assert read["by_basis"].get("вид кейсов") == 1 and "вид документа" not in read["by_basis"]  # the short form of the view instead of the generator's view
    assert (views / (digest + ".autotest-review.md")).read_text(encoding="utf-8").count("Вид для ревьюера, короткая запись") == 1


def test_the_derived_files_are_written_before_the_request_and_change_no_event_or_input_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _prepared(tmp_path)
    written: list[str] = []
    real = pilot_state.publish_model_request

    def spy(root, attempt_id, stage, **kwargs):
        if stage == REVIEWER:
            written.append(",".join(sorted(path.name[65:] for path in (Path(root) / "review-views").glob("*.tables.txt"))))
        return real(root, attempt_id, stage, **kwargs)

    monkeypatch.setattr(pilot_state, "publish_model_request", spy)
    assert prepare_stage(run["root"], REVIEWER, *INDEPENDENT)[0] == 0
    assert written == ["tables.txt"]
    state = pilot_state.derive_state(run["root"])
    request = next(event for event in state["events"] if event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == REVIEWER)
    boundary = pilot_state.read_attempt_receipt(run["root"], run["attempt"], "automation-review-boundary-r1", "ARTIFACT_READ_BACK")["record"]
    assert pilot_state.read_model_request(run["root"], run["attempt"], REVIEWER, str(request["artifact_digest"]))["input_digests"] == [boundary["automation_digest"], boundary["digest"]]
    before = _events(run["root"])
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)  # the repeat writes nothing
    assert _events(run["root"]) == before


def test_a_derived_file_the_agent_changed_is_written_again_by_the_next_prepare(tmp_path: Path) -> None:
    run = _prepared(tmp_path)
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    table = next((run["root"] / "review-views").glob("*.tables.txt"))
    original = table.read_bytes()
    table.write_bytes(b"changed")
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    assert table.read_bytes() == original


def test_an_answer_whose_bytes_are_not_its_digest_is_not_turned_into_files(tmp_path: Path) -> None:
    from tools.step_size import request_files

    run = _prepared(tmp_path)
    prepare_stage(run["root"], REVIEWER, *INDEPENDENT)
    boundary = pilot_state.read_attempt_receipt(run["root"], run["attempt"], "automation-review-boundary-r1", "ARTIFACT_READ_BACK")["record"]
    source = pilot_state._model_stage_artifact_target(run["root"], run["attempt"], boundary["automation_digest"])
    source.write_bytes(source.read_bytes() + b" ")
    found = request_files(run["root"], REVIEWER)
    assert [row["path"] for row in found["files"] if row["digest"] == boundary["automation_digest"]] == [str(source)]
    assert [row["basis"] for row in found["files"] if row["digest"] != boundary["automation_digest"] and row.get("basis", "").startswith("вид")] == ["вид документа"]  # the generator's view, as before


def test_the_rows_of_a_big_answer_are_smaller_than_its_json_and_the_hint_check_still_reads_the_json(tmp_path: Path) -> None:
    from tools import autotest_hints
    from tools.step_size import automation_text_rows

    run = _prepared(tmp_path)
    big = run["root"] / "big.json"  # a file of the run: it is read through the core
    big.write_bytes(json.dumps(_artifact(cases=30, asserts=8), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    digest = "sha256:" + hashlib.sha256(big.read_bytes()).hexdigest()
    rows = automation_text_rows(tmp_path, run["root"], [big], digest, True)
    assert rows and sum(row["bytes"] for row in rows) < big.stat().st_size * 0.6 and all(Path(row["path"]).stat().st_size == row["bytes"] for row in rows)
    answered = next(str(event["artifact_digest"]) for event in pilot_state.derive_state(run["root"])["events"] if event["event_type"] == "CANDIDATE_PUBLISHED")
    assert autotest_hints.main(["--automation", str(pilot_state._model_stage_artifact_target(run["root"], run["attempt"], answered))]) == 0  # the check of the instruction runs on the JSON, as before


def test_the_generator_reads_what_it_read_before(tmp_path: Path) -> None:
    from tools.step_size import request_files

    run = _prepared(tmp_path)
    found = request_files(run["root"], GENERATOR)
    assert [row["basis"] for row in found["files"]] == ["вид"] and not list((run["root"] / "review-views").glob("*.tables.txt"))


EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "petclinic-owner-lifecycle" / "cases.json"


def _example() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _answer(document: dict) -> dict:
    """An answer that relates every step of the example but the last step of the first case, and of the second step of the first case only its first assertion."""
    rows = []
    for case_index, case in enumerate(document["test_cases"]):
        for step_index, step in enumerate(case["steps"]):
            if case_index == 0 and step_index == len(case["steps"]) - 1:
                continue
            ids = {"case_id": case["case_id"], "step_id": step["step_id"], "file_id": "FILE-1", "symbol_id": "SYMBOL-1"}
            rows.append({"kind": "operation", **ids})
            for expectation in step["expectations"]:
                for assertion in expectation["assertions"][: 1 if (case_index, step_index) == (0, 1) else None]:
                    rows.append({"kind": "assertion", **ids, "expectation_id": expectation["expectation_id"], "assertion_id": assertion["assertion_id"]})
    code = "class B {}\n"
    return {"schema_version": "5.0.0", "stage": "tc-to-autotest", "warnings": [], "artifacts": {
        "automation_status": "GENERATED", "automation_revision": 1, "manual_dispositions": [], "diagnostics": [], "implementation_relations": rows,
        "generated_files": [{"file_id": "FILE-1", "path": "src/test/java/a/B.java", "language": "java", "framework": "junit5", "content": code,
                             "content_digest": "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()}],
        "generated_symbols": [{"file_id": "FILE-1", "symbol_id": "SYMBOL-1", "locator": {"kind": "java_class_method", "class_fqn": "a.B", "method_name": "t"}}]}}


def _text(parts: list, suffix: str) -> str:
    return next(data for name, _basis, data in parts if name.endswith(suffix)).decode("utf-8")


def test_the_operation_and_the_assertions_of_a_related_step_are_in_the_tables_and_not_in_the_view() -> None:
    from tools.automation_text import render
    from tools.review_view import render as view_of

    document, legend = _example(), "- Вид для ревьюера"
    parts = render(_answer(document), DIGEST, document)
    relations = _text(parts, ".tables.txt").split("## связи\n")[1].strip("\n").split("\n")
    short = [line for line in view_of(document, profile="autotest-review").decode("utf-8").split("\n") if not line.startswith(legend)]  # proved above to be the generator's view
    shown, back = [line for line in _text(parts, ".autotest-review.md").split("\n") if not line.startswith(legend)], []
    for line in relations:  # a fact of the tables, written as the short view writes it
        if line.startswith("    "):
            name, text = line.strip().split(" ", 1)
            back.append(f"     {name}: {text}")
        elif not line.startswith("  "):
            back.append(f"   Операция: {line.split(' | ', 1)[1]}")
    assert back and set(back) <= set(short) and sorted(shown + back) == sorted(short)  # the view and the tables hold the very lines of the short view
    assert sum(line.startswith("   Операция:") for line in shown) == 1 and any(line.strip().startswith("ASSERT-") for line in shown)  # the step without a relation keeps its operation and its assertions
