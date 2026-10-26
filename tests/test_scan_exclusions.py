"""FaT-2: what the scan leaves out is judged by name and place, and a person sees a summary of it.

Names of build output, dependencies and secrets that are also package names are left out only outside a ``src``
tree; secret file names are a rule for non-source files; ``to_do`` (task specifications) stays in the inventory;
``.skillsrc`` ``paths.include`` brings back a directory left out by its name only, and only on the way of its glob.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_phase2_scan import _args, _project
from tools import run_pipeline
from tools.project_inventory import build_inventory

JAVA = "package com.x;\n\npublic class Foo {\n}\n"


def _write(root: Path, relative: str, text: str = JAVA) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _paths(project: Path, **options) -> tuple[set[str], list[dict]]:
    inventory = build_inventory(project, project, **options)
    return {item["project_path"] for item in inventory["files"]}, inventory["exclusions"]


def test_dual_names_are_code_inside_src_and_output_outside(tmp_path: Path) -> None:
    project = tmp_path / "project"
    for relative in ("src/main/java/com/x/build/Foo.java", "src/main/java/com/x/coverage/Bar.java", "src/main/java/com/x/vendor/Baz.java",
                     "src/main/java/com/x/target/Qux.java", "build/Out.java", "target/Out.java", "dist/out.js", "coverage/report.txt",
                     "vendor/lib.py", "out/Out.java"):
        _write(project, relative)
    files, exclusions = _paths(project)
    assert {"src/main/java/com/x/build/Foo.java", "src/main/java/com/x/coverage/Bar.java", "src/main/java/com/x/vendor/Baz.java",
            "src/main/java/com/x/target/Qux.java"} <= files
    assert not {"build/Out.java", "target/Out.java", "dist/out.js", "coverage/report.txt", "vendor/lib.py", "out/Out.java"} & files
    assert {row["project_path"] for row in exclusions} >= {"build", "target", "dist", "coverage", "vendor", "out"}


def test_secret_directories_inside_src_are_judged_by_content(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project, "credentials/key.txt", "anything\n")
    _write(project, "src/main/java/com/x/credentials/CredentialsService.java", JAVA)
    _write(project, "src/main/java/com/x/credentials/Leaky.java", 'class Leaky { String password="secret123"; }\n')
    files, exclusions = _paths(project)
    assert "src/main/java/com/x/credentials/CredentialsService.java" in files
    assert "src/main/java/com/x/credentials/Leaky.java" not in files and "credentials/key.txt" not in files
    leaky = next(row for row in exclusions if row["project_path"].endswith("Leaky.java"))
    assert leaky["reason_code"] == "SECRET_SUSPECTED" and leaky["scanner_rule_id"] == "token-assignment-v1"


def test_secret_file_names_are_a_rule_for_non_source_files_only(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project, "app/token_service.py", "def issue():\n    return None\n")
    _write(project, "app/private_api.py", "VALUE = 1\n")
    _write(project, "conf/secret.txt", "nothing secret inside\n")
    _write(project, "conf/api-key.json", "{}\n")
    files, _ = _paths(project)
    assert {"app/token_service.py", "app/private_api.py"} <= files
    assert not {"conf/secret.txt", "conf/api-key.json"} & files


def test_tooling_and_agent_directories_are_out_everywhere(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project, ".gigacode/skills/test-skills/tools/driver.py", "X = 1\n")
    _write(project, "src/.idea/workspace.xml", "<x/>\n")
    files, exclusions = _paths(project)
    assert not files
    rows = {row["project_path"]: row for row in exclusions}
    assert rows[".gigacode"]["reason_code"] == "EXCLUDED_DIRECTORY" and rows[".gigacode"]["scanner_rule_id"] == "tooling-directory-v1"
    assert rows["src/.idea"]["scanner_rule_id"] == "tooling-directory-v1"


def test_task_specifications_in_to_do_stay_in_the_inventory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project, "docs/to_do/TASK-1/spec.md", "Спецификация задачи\n")
    _write(project, "src/app.py", "X = 1\n")
    files, exclusions = _paths(project)
    assert files == {"src/app.py", "docs/to_do/TASK-1/spec.md"} and not exclusions


def test_include_brings_back_a_directory_left_out_by_name_but_no_secret(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project, "build/gen/Api.java", JAVA)
    _write(project, "build/secret.txt", "x\n")
    _write(project, "build/Leaky.java", 'class Leaky { String token="abcdefgh12345"; }\n')
    _write(project, ".git/config", "[core]\n")
    files, _ = _paths(project, include=("build/**", ".git"))
    assert "build/gen/Api.java" in files
    assert not {"build/secret.txt", "build/Leaky.java", ".git/config"} & files


def test_the_summary_names_reasons_and_only_the_suspicious_exclusions() -> None:
    from tools.scan_rules import exclusion_summary

    rows = [
        {"project_path": "target", "reason_code": "BUILD_OUTPUT", "scanner_rule_id": "output-directory-v1", "safe_label": "excluded-directory"},
        {"project_path": "docs/coverage", "reason_code": "BUILD_OUTPUT", "scanner_rule_id": "output-directory-v1", "safe_label": "excluded-directory"},
        {"project_path": "conf/secret.txt", "reason_code": "SECRET_SUSPECTED", "scanner_rule_id": "secret-filename-v1", "safe_label": "credential-material"},
        {"project_path": "src/A.java", "reason_code": "SECRET_SUSPECTED", "scanner_rule_id": "token-assignment-v1", "safe_label": "token-signature"},
        {"project_path": ".git", "reason_code": "VCS", "scanner_rule_id": "vcs-directory-v1", "safe_label": "excluded-directory"},
    ]
    summary = exclusion_summary(rows)
    assert summary["total"] == 5 and summary["by_reason"]["BUILD_OUTPUT"]["count"] == 2
    assert summary["by_reason"]["BUILD_OUTPUT"]["rules"] == {"output-directory-v1": 2}
    assert summary["suspicious"] == {"dual_name_outside_module_root": ["docs/coverage"],
                                     "secret_filename": ["conf/secret.txt"], "source_secret_content": ["src/A.java"]}
    nested = exclusion_summary([{**rows[0], "project_path": "services/api/target"}], module="services/api")
    assert nested["suspicious"] == {}


def test_scan_prints_the_summary_and_reads_docs_in_drafts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module = _project(tmp_path)
    _write(module, "src/app.py", "X = 1\n")
    _write(module, "docs/coverage/notes.md", "x\n")
    _write(module, "docs/to_do/task.md", "Пользователь видит список.\n")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.docs = ["services/api/docs/to_do/task.md"]
    assert run_pipeline.cmd_scan(args) == 0, payloads[-1]
    summary = payloads[-1]["exclusion_summary"]
    assert summary["suspicious"]["dual_name_outside_module_root"] == ["services/api/docs/coverage"]
    assert "paths.include" in summary["hint"]
    status_payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", status_payloads.append)
    run_pipeline.cmd_status(type("A", (), {"project": str(project), "run": payloads[-1]["run_id"]})())
    assert status_payloads[-1]["exclusion_summary"] == summary


def test_include_in_skillsrc_is_a_relative_glob() -> None:
    from tools.schema_validation import schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    schema = root / "schemas" / "skillsrc.schema.json"
    from tests.test_phase2_scan import _skillsrc
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    document = parse_skillsrc_bytes(_skillsrc())
    for good in (["build/generated/**"], ["docs/to_do"]):
        document["modules"][0]["paths"]["include"] = good
        assert not schema_diagnostics(document, schema, root), good
    for bad in (["../outside/**"], ["/abs/path"], ["C:/abs"], ["a/../b"]):
        document["modules"][0]["paths"]["include"] = bad
        assert schema_diagnostics(document, schema, root), bad


def test_an_exclusion_receipt_from_before_fat2_is_still_valid() -> None:
    from tools.schema_validation import schema_diagnostics

    root = Path(__file__).resolve().parents[1]
    old = {"schema_version": "1.0.0", "exclusions": [
        {"project_path": "build", "reason_code": "BUILD_OUTPUT", "scanner_rule_id": "output-directory-v1", "safe_label": "excluded-directory"},
        {"project_path": ".idea", "reason_code": "DEPENDENCY", "scanner_rule_id": "dependency-directory-v1", "safe_label": "excluded-directory"}],
        "digest": "sha256:" + "a" * 64}
    assert not schema_diagnostics(old, root / "schemas" / "exclusion-receipt.schema.json", root)


def test_module_search_accepts_a_module_inside_a_src_build_directory(tmp_path: Path) -> None:
    from tools.stack_catalog import confined_files

    project = tmp_path / "project"
    _write(project, "src/main/build/pom.xml", "<project/>\n")
    _write(project, "build/pom.xml", "<project/>\n")
    found = {path.relative_to(project).as_posix() for path in confined_files(project)}
    assert "src/main/build/pom.xml" in found and "build/pom.xml" not in found


def test_a_glob_into_a_build_directory_is_a_passage_not_the_whole_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project, "build/generated-api/A.java", JAVA)
    _write(project, "build/generated-api/deep/B.java", JAVA)
    _write(project, "build/reports/index.html", "<html/>\n")
    _write(project, "build/tmp/x.txt", "x\n")
    _write(project, "build/top.txt", "x\n")
    files, exclusions = _paths(project, include=("build/generated-api/**",))
    assert {"build/generated-api/A.java", "build/generated-api/deep/B.java"} <= files
    assert not {"build/reports/index.html", "build/tmp/x.txt", "build/top.txt"} & files
    rows = {row["project_path"]: row["scanner_rule_id"] for row in exclusions}
    assert rows["build/reports"] == rows["build/tmp"] == rows["build/top.txt"] == "output-directory-v1"


def test_scan_puts_a_task_specification_in_to_do_into_the_model_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module = _project(tmp_path)
    _write(module, "src/app.py", "X = 1\n")
    _write(module, "docs/to_do/task/spec.md", "Пользователь видит список.\n")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project)
    args.docs = ["services/api/docs/to_do/task/spec.md"]
    assert run_pipeline.cmd_scan(args) == 0, payloads[-1]
    run_root, attempt_id = Path(payloads[-1]["run_root"]), payloads[-1]["attempt_id"]
    inventory = json.loads(next((run_root / "inventories").glob("*.json")).read_text(encoding="utf-8"))
    spec = next(item for item in inventory["files"] if item["project_path"] == "services/api/docs/to_do/task/spec.md")
    selected = [json.loads(path.read_text(encoding="utf-8")) for path in (run_root / "context-selections" / attempt_id).glob("*.json")]
    assert any(spec["opaque_id"] in json.dumps(receipt) for receipt in selected)


def test_the_module_search_still_skips_to_do(tmp_path: Path) -> None:
    from tools.stack_catalog import confined_files, is_ignored_path

    project = tmp_path / "project"
    _write(project, "docs/to_do/task/pom.xml", "<project/>\n")
    assert not confined_files(project) and is_ignored_path(("docs", "to_do", "task"))
