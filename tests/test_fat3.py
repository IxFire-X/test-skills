"""FaT-3 items 1–4 against the 239k window: Zephyr priority, project requirement IDs, step size, the next step after /clear."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NUMBERED = """# Спецификация

## 3. Каталог

3.1 Пользователь видит список товаров.
3.2 Пользователь открывает карточку товара.

## 6. Оплата

6.3.1 Оплата картой подтверждается кодом.
Текст без номера остаётся в разделе.
"""


def test_zephyr_critical_is_high() -> None:
    from tools.test_case_projections import _PRIORITIES

    assert _PRIORITIES == {"CRITICAL": "High", "HIGH": "High", "MEDIUM": "Normal", "LOW": "Low"}


def test_a_project_id_pattern_splits_a_numbered_specification() -> None:
    from tools.build_context import extract_inventory

    pattern = r"\d{1,2}\.\d{1,2}(?:\.\d{1,2})?\s"
    rows = extract_inventory(NUMBERED, pattern)
    titles = [row["title"] for row in rows]
    assert "3.1 Пользователь видит список товаров" in titles and "6.3.1 Оплата картой подтверждается кодом" in titles
    requirements = [row for row in rows if row["kind"] == "requirement"]
    assert len(requirements) == 3
    assert any("Текст без номера" in row["text"] for row in rows)


def test_without_a_pattern_the_split_is_unchanged() -> None:
    from tools.build_context import extract_inventory

    rows = extract_inventory(NUMBERED)
    assert [(row["kind"], row["title"]) for row in rows] == [("flow", "Спецификация"), ("flow", "3. Каталог"), ("flow", "6. Оплата")]
    assert set(rows[0]) == {"kind", "title", "text"}
    builtin = extract_inventory("REQ-1 Вход\nтекст\n## Раздел\n")
    assert [(row["kind"], row["title"]) for row in builtin] == [("requirement", "REQ-1 Вход"), ("flow", "Раздел")]


@pytest.mark.parametrize("pattern", ["(", "", "x?", "a" * 201])
def test_a_bad_id_pattern_is_rejected(pattern: str) -> None:
    from tools.build_context import compile_id_pattern

    with pytest.raises(ValueError):
        compile_id_pattern(pattern)


def test_skillsrc_carries_requirements_and_limits(tmp_path: Path) -> None:
    from tests.test_phase2_scan import _skillsrc
    from tools.schema_validation import schema_diagnostics
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    document = parse_skillsrc_bytes(_skillsrc())
    document["requirements"] = {"id_pattern": r"\d{1,2}\.\d{1,2}\s"}
    document["limits"] = {"step_input_bytes": 300000}
    assert not schema_diagnostics(document, ROOT / "schemas" / "skillsrc.schema.json", ROOT)
    document["limits"] = {"step_input_bytes": 10}
    assert schema_diagnostics(document, ROOT / "schemas" / "skillsrc.schema.json", ROOT)


def _step(*args: str, cwd: Path = ROOT) -> tuple[int, dict]:
    completed = subprocess.run([sys.executable, "-m", "tools.step_size", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    return completed.returncode, json.loads(completed.stdout)


def test_step_size_fits_and_does_not_fit(tmp_path: Path) -> None:
    small = tmp_path / "small.md"
    small.write_text("x" * 1000, encoding="utf-8")
    big = tmp_path / "big.md"
    big.write_bytes(b"y" * 600_000)
    code, report = _step("--project", str(tmp_path), "--files", str(small))
    assert code == 0 and report["fits"] and report["bytes"] == 1000 and report["budget_bytes"] == 500_000
    assert report["tokens_est"] == 240
    assert report["warning"] is None
    code, report = _step("--project", str(tmp_path), "--files", str(small), str(big))
    # FaT-RV: over the budget is a warning, not a stop — the run goes on, the advice to narrow --target stays.
    assert code == 0 and not report["fits"] and report["warning"] == "STEP_OVER_BUDGET" and "--target" in report["hint"]


def test_step_size_counts_the_role_instructions_and_reads_the_skillsrc_budget(tmp_path: Path) -> None:
    from tests.test_phase2_scan import _skillsrc
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    doc = tmp_path / "doc.md"
    doc.write_text("x" * 1000, encoding="utf-8")
    code, plain = _step("--project", str(tmp_path), "--files", str(doc))
    code, with_role = _step("--project", str(tmp_path), "--files", str(doc), "--role", "tc-generator")
    skill = (ROOT / "skills" / "tc-generator" / "SKILL.md").stat().st_size
    assert with_role["bytes"] >= plain["bytes"] + skill and with_role["role_bytes"] >= skill
    document = parse_skillsrc_bytes(_skillsrc())
    document["limits"] = {"step_input_bytes": 1500}
    (tmp_path / ".skillsrc").write_bytes(_payload(document))
    code, report = _step("--project", str(tmp_path), "--files", str(doc), "--role", "tc-generator")
    assert report["budget_bytes"] == 1500 and code == 0 and report["warning"] == "STEP_OVER_BUDGET"


def test_step_size_reads_the_context_marker_input_from_the_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_phase2_scan import _args, _project
    from tools import run_pipeline

    project, module = _project(tmp_path)
    (module / "src" / "app.py").write_text("X = 1\n" * 100, encoding="utf-8")
    (module / "docs").mkdir()
    (module / "docs" / "feature.md").write_text("Требование.\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.docs = ["services/api/docs/feature.md"]
    args.profile = "cases-only-v1"
    assert run_pipeline.cmd_scan(args) == 0
    code, report = _step("--project", str(project), "--run", payloads[-1]["run_id"], "--stage", "context-marker")
    skill = (ROOT / "skills" / "context-marker" / "SKILL.md").stat().st_size
    assert code == 0 and report["files"] >= 2 and report["bytes"] >= 600 + len("Требование.\n".encode()) + skill
    rows = (project / ".pilot-runs" / payloads[-1]["run_id"] / "step-sizes.jsonl").read_text(encoding="utf-8").splitlines()
    assert report["recorded"] and [json.loads(row)["step"] for row in rows] == ["context-marker"]


def _scanned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str]:
    from tests.test_phase2_scan import _args, _project
    from tools import run_pipeline

    project, module = _project(tmp_path)
    (module / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.profile = "cases-only-v1"
    assert run_pipeline.cmd_scan(args) == 0
    return project, payloads[-1]["run_id"]


def test_step_size_records_the_step_and_bytes_in_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, run_id = _scanned(tmp_path, monkeypatch)
    cases = project / "cases.json"
    cases.write_bytes(b"c" * 2000)
    code, fits = _step("--project", str(project), "--run", run_id, "--files", str(cases), "--step", "tc-reviewer:canonical", "--budget-bytes", "5000")
    assert code == 0 and fits["fits"] and fits["recorded"]
    code, over = _step("--project", str(project), "--run", run_id, "--files", str(cases), "--role", "tc-reviewer", "--step", "tc-reviewer:canonical")
    assert code == 0 and over["warning"] is None
    code, over = _step("--project", str(project), "--run", run_id, "--files", str(cases), "--step", "tc-to-autotest", "--budget-bytes", "1000")
    assert code == 0 and over["warning"] == "STEP_OVER_BUDGET" and over["recorded"]
    rows = [json.loads(line) for line in (project / ".pilot-runs" / run_id / "step-sizes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["step"] for row in rows] == ["tc-reviewer:canonical", "tc-reviewer:canonical", "tc-to-autotest"]
    assert rows[-1]["bytes"] == 2000 and rows[-1]["budget_bytes"] == 1000 and rows[-1]["fits"] is False
    assert rows[-1]["warning"] == "STEP_OVER_BUDGET" and rows[1]["role"] == "tc-reviewer" and rows[1]["role_bytes"] > 0
    # the record keeps no project content: no file paths, only the step, the sizes and the code
    assert all("cases.json" not in json.dumps(row) and "files" in row and isinstance(row["files"], int) for row in rows)


def test_step_size_without_a_run_records_nothing_and_an_unknown_run_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, run_id = _scanned(tmp_path, monkeypatch)
    doc = project / "doc.md"
    doc.write_bytes(b"d" * 10)
    code, report = _step("--project", str(project), "--files", str(doc))
    assert code == 0 and report["recorded"] is False and not (project / ".pilot-runs" / run_id / "step-sizes.jsonl").exists()
    code, report = _step("--project", str(project), "--run", "0" * 32, "--files", str(doc))
    assert code == 2 and report["status"] == "error"
    code, report = _step("--project", str(project), "--run", "..", "--files", str(doc))
    assert code == 2 and report["status"] == "error"


def test_status_names_the_next_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_phase2_scan import _args, _project
    from tools import run_pipeline

    project, module = _project(tmp_path)
    (module / "src" / "app.py").write_text("X = 1\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.profile = "cases-only-v1"
    assert run_pipeline.cmd_scan(args) == 0
    run_pipeline.cmd_status(type("A", (), {"project": str(project), "run": payloads[-1]["run_id"]})())
    step = payloads[-1]["next_step"]
    assert step["action"] and step["stage"] and step["skill_section"]
    assert step["stage"] != "unknown" and step["skill_section"] != "Stop conditions"


def _next_artifacts() -> set[str]:
    """FaT-B: every literal next_expected_artifact that _durable_status_facts can set, read from its source."""
    import ast
    import inspect
    import textwrap

    from tools import run_pipeline

    tree = ast.parse(textwrap.dedent(inspect.getsource(run_pipeline._durable_status_facts)))
    found: set[str] = set()

    def strings(node: ast.AST | None) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.add(node.value)
        elif isinstance(node, ast.IfExp):
            strings(node.body), strings(node.orelse)
        elif isinstance(node, ast.Dict):
            for value in node.values:
                strings(value)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):  # {...}.get(stage, default)
            strings(node.func.value), [strings(argument) for argument in node.args[1:]]
        elif isinstance(node, ast.Subscript):
            strings(node.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "next_expected_artifact":
            strings(node.value)
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value == "next_expected_artifact":
                    strings(value)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and any(isinstance(target, ast.Name) and target.id == "next_artifact" for target in getattr(node, "targets", [getattr(node, "target", None)])):
            strings(node.value)
        elif isinstance(node, ast.Assign) and any(isinstance(target, ast.Tuple) and any(isinstance(item, ast.Name) and item.id == "next_artifact" for item in target.elts) for target in node.targets):
            strings(node.value.elts[0]) if isinstance(node.value, ast.Tuple) else None
        elif isinstance(node, ast.For) and isinstance(node.target, ast.Tuple) and any(isinstance(item, ast.Name) and item.id == "next_artifact" for item in node.target.elts):
            index = [getattr(item, "id", None) for item in node.target.elts].index("next_artifact")
            for row in node.iter.elts:
                strings(row.elts[index])
        elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "record" and len(node.args) >= 3:
            strings(node.args[2])
    return found


def test_every_next_artifact_the_code_sets_has_a_next_step() -> None:
    from tools.run_pipeline import _next_step

    artifacts = _next_artifacts()
    assert {"authorized context", "context-marker response", "generator fragments", "remaining fragments or assembled candidate",
            "reviewer ledger and effective selection or negative closure", "static automation review"} <= artifacts
    assert len(artifacts) >= 24
    for expected in sorted(artifacts) + ["context-marker:baseline response", "tc-generator:BATCH-1 response"]:
        step = _next_step({"next_expected_artifact": expected})
        assert step["stage"] != "unknown" and step["skill_section"] != "Stop conditions", expected
    assert "pending_request_path" not in _next_step({"next_expected_artifact": "context-marker response"})["action"]


def test_the_automation_role_counts_its_conventions_fat_b() -> None:
    from tools.step_size import role_files

    names = {path.name for path in role_files("tc-to-autotest")}
    assert {"SKILL.md", "automation-output-contract.md", "java-junit5.md", "python-pytest.md"} <= names
