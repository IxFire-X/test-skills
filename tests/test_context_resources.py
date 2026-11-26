"""q13: a template that the code under ``--target`` names by a string literal comes with the code (``--target`` cut it off); the files are the inventory's, the bytes capped."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_context_filter import MODULE, _project as _python_module, _receipts, _scan
from tests.test_context_batches import _requirements, three  # noqa: F401 — the fixture
from tests.test_first_run import _write
from tools.project_inventory import build_inventory, select_context_batches
from tools.ready import check_context
from tools.run_scan import context_preview

VIEW = 'def show():\n    return render("owners/detail")\n'
DETAIL, LIST_ = "templates/owners/detail.html", "templates/owners/list.html"


def _project(tmp_path: Path, files: dict[str, str], exclude: list[str] | None = None) -> Path:
    for path, text in files.items():
        _write(tmp_path, path, text)
    if exclude:
        document = {"schema_version": "5.0.0", "version": "3.0", "project": {"name": "p"}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
                    "modules": [{"id": "root", "root": ".", "stack": {"language": "java", "build_tool": "maven", "framework": "unknown"}, "detected_from": ["pom.xml"],
                                 "paths": {"source": ["src"], "tests": ["tests"], "resources": ["templates"], "exclude": exclude},
                                 "test": {"framework": "junit5", "adapter_id": "maven:selected-symbols-v1", "executable": "mvn", "build_profile": "default", "adapter_parameters": {}}}]}
        _write(tmp_path, ".skillsrc", json.dumps(document))
    return tmp_path


def _preview(project: Path, target: str | None = "src/web") -> tuple[set[str], list[dict[str, Any]], dict[str, Any]]:
    inventory = build_inventory(project, project)
    kept, skipped, _budget, templates = context_preview(inventory, project, project, None, target)
    paths = {row["opaque_id"]: row["project_path"] for row in inventory["files"]}
    return {paths[opaque] for opaque in kept}, skipped, templates


def test_a_template_the_code_names_comes_with_it(tmp_path: Path) -> None:
    kept, _skipped, templates = _preview(_project(tmp_path, {"src/web/views.py": VIEW, DETAIL: "<p>{{ name }}</p>\n", LIST_: "<ul></ul>\n"}))
    assert DETAIL in kept and LIST_ not in kept and templates["paths"] == [DETAIL]


def test_a_literal_with_the_whole_file_name_names_it_too(tmp_path: Path) -> None:
    kept, *_ = _preview(_project(tmp_path, {"src/web/views.py": 'PAGE = "classpath:/templates/owners/list.html"\n', DETAIL: "<p></p>\n", LIST_: "<ul></ul>\n"}))
    assert LIST_ in kept and DETAIL not in kept


def test_a_literal_with_only_the_file_name_finds_the_template_deeper_in_the_resources(tmp_path: Path) -> None:
    deep = "src/main/resources/screen/orders/detailsScreen.ftlh"
    kept, *_ = _preview(_project(tmp_path, {"src/web/views.py": 'TEMPLATE = "detailsScreen.ftlh"\n', deep: "<#list widgets as w></#list>\n"}))
    assert deep in kept


def test_a_literal_that_names_no_file_adds_nothing(tmp_path: Path) -> None:
    kept, _skipped, templates = _preview(_project(tmp_path, {"src/web/views.py": 'PAGE = "nowhere/page"\n', DETAIL: "<p></p>\n"}))
    assert DETAIL not in kept and templates["paths"] == [] and templates["unfound"] is None


def test_a_bare_name_that_one_template_carries_finds_it(tmp_path: Path) -> None:
    kept, *_ = _preview(_project(tmp_path, {"src/web/views.py": 'def home():\n    return "welcome"\n', "templates/welcome.html": "<p></p>\n"}))
    assert "templates/welcome.html" in kept


def test_a_bare_name_that_two_templates_carry_names_none(tmp_path: Path) -> None:
    kept, _skipped, templates = _preview(_project(tmp_path, {"src/web/views.py": 'def show():\n    return "list"\n', "templates/owners/list.html": "<ul></ul>\n", "templates/vets/list.html": "<ul></ul>\n"}))
    assert templates["paths"] == [] and "templates/owners/list.html" not in kept and "templates/vets/list.html" not in kept


def test_the_plan_with_no_context_receipts_names_no_file() -> None:
    from tools.batch_assembly import pick_context

    assert pick_context(Path("."), [], 0, [], []) is None


def test_a_template_of_a_test_folder_is_not_taken(tmp_path: Path) -> None:
    kept, *_ = _preview(_project(tmp_path, {"src/web/views.py": VIEW, "tests/templates/owners/detail.html": "<p></p>\n"}))
    assert "tests/templates/owners/detail.html" not in kept


def test_without_a_target_nothing_is_added(tmp_path: Path) -> None:
    kept, _skipped, templates = _preview(_project(tmp_path, {"src/web/views.py": VIEW, DETAIL: "<p></p>\n"}), None)
    assert DETAIL in kept and templates["paths"] == []


def test_a_template_the_user_excluded_stays_out(tmp_path: Path) -> None:
    kept, skipped, templates = _preview(_project(tmp_path, {"src/web/views.py": VIEW, DETAIL: "<p></p>\n"}, exclude=["templates/**"]))
    assert DETAIL not in kept and templates["paths"] == [] and [row["reason"] for row in skipped] == ["CONTEXT_EXCLUDED_BY_USER"]


def test_templates_over_the_limit_are_listed_and_not_read(tmp_path: Path) -> None:
    names = {f"templates/page{number}.html": "x" * 30_000 for number in range(3)}  # a quarter of a batch is 64 KiB: two of them
    kept, skipped, templates = _preview(_project(tmp_path, {"src/web/views.py": 'A = ("page0", "page1", "page2")\n', **names}))
    assert len(templates["paths"]) == 2 and sum(path in kept for path in names) == 2
    assert [(row["project_path"], row["reason"]) for row in skipped] == [("templates/page2.html", "CONTEXT_RESOURCE_OVER_LIMIT")]


def test_a_template_comes_first_in_the_first_batch(tmp_path: Path) -> None:
    project = _project(tmp_path, {"src/web/views.py": VIEW, DETAIL: "<p></p>\n"})
    inventory = build_inventory(project, project)
    kept, _skipped, _budget, templates = context_preview(inventory, project, project, None, "src/web")
    first = select_context_batches(inventory, project, kept, byte_budget=100_000, front=templates["ids"])[0]
    assert first["files"][0]["project_path"] == DETAIL and first["receipt"]["referenced"] == [DETAIL]


def test_a_receipt_without_templates_has_no_referenced(tmp_path: Path) -> None:
    project = _project(tmp_path, {"src/web/views.py": VIEW, DETAIL: "<p></p>\n"})
    inventory = build_inventory(project, project)
    kept, *_ = context_preview(inventory, project, project, None, "src/web")
    assert "referenced" not in select_context_batches(inventory, project, kept, byte_budget=100_000)[0]["receipt"]


def test_the_scan_gives_the_template_to_the_model_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = _python_module(tmp_path)
    _write(project, f"{MODULE}/src/app.py", 'PAGE = "pages/home"\n')
    _write(project, f"{MODULE}/templates/pages/home.html", "<p></p>\n")
    from tests.test_context_filter import DOCS
    from tests.test_phase2_scan import _args
    from tools import run_pipeline

    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = _args(project, "src")
    args.profile, args.docs = "cases-only-v1", [DOCS]
    assert run_pipeline.cmd_scan(args) == 0, payloads[-1]
    first = _receipts(payloads[-1])[0]
    template = f"{MODULE}/templates/pages/home.html"
    assert first["referenced"] == [template] and first["files"][0]["project_path"] == template
    assert payloads[-1]["context_summary"]["resource_files"]["files"] == 1


def test_ready_names_the_templates_that_came(tmp_path: Path) -> None:
    project = _project(tmp_path, {"src/web/views.py": VIEW, DETAIL: "<p></p>\n"})
    row = check_context(project, {"root": "."}, [], build_inventory(project, project), "src/web")
    assert row["level"] == "OK" and "подтянуты шаблоны: 1 файлов" in row["message"] and DETAIL in row["message"]


def test_ready_warns_when_the_code_puts_a_template_name_together(tmp_path: Path) -> None:
    project = _project(tmp_path, {"src/web/views.py": 'def show(name):\n    return render(name + ".html")\n    SUFFIX = ".html"\n', DETAIL: "<p></p>\n"})
    row = check_context(project, {"root": "."}, [], build_inventory(project, project), "src/web")
    assert row["level"] == "WARN" and ".html" in row["message"] and "--target src/main" in row["fix"]


def test_a_url_that_ends_with_html_is_no_warning(tmp_path: Path) -> None:
    project = _project(tmp_path, {"src/web/views.py": 'ROUTE = "/vets.html"\n', DETAIL: "<p></p>\n"})
    assert check_context(project, {"root": "."}, [], build_inventory(project, project), "src/web")["level"] == "OK"


def test_the_plan_reads_a_referenced_template_among_the_named_files(three) -> None:
    from tools.batch_assembly import pick_context

    project, receipts = three[0], three[4]
    marked = [{**receipts[0], "referenced": ["src/part2.py"]}, *receipts[1:]]  # a file of the third batch, as a template would be
    assert pick_context(project, marked, 1, _requirements(), [])[0][0] == "src/part2.py"
