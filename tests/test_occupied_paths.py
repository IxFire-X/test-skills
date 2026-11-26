"""pm2b: ``prepare`` of the automation generator names the paths the project already holds, before the answer is written (``submit`` refuses them: pm2a)."""
from __future__ import annotations

import json
from pathlib import Path

from tests.helpers import prepare_stage
from tests.test_autotest_cycle import GENERATOR
from tests.test_automation_path_held import _held_run


def test_prepare_of_the_generator_names_the_held_path_and_the_rule(tmp_path: Path) -> None:
    run = _held_run(tmp_path)
    code, out = prepare_stage(run["root"], GENERATOR)
    held = out["result"]["read"]["occupied_paths"]
    assert code == 0 and held["paths"] == ["tests/test_products.py"], out
    assert "новый файл называй иначе" in held["rule"] and "`submit` откажет" in held["rule"] and "регистр" in held["rule"]


def test_the_paths_of_a_module_are_told_as_the_generator_names_its_own() -> None:
    from tools.generated_delta import occupied_paths

    baseline = {"module": "services/api", "test_root": "tests", "inputs": [
        {"project_path": "services/api/tests/test_b.py"}, {"project_path": "services/api/tests/test_a.py"},
        {"project_path": "services/api/src/app.py"}, {"project_path": "other/tests/test_c.py"}]}
    assert occupied_paths(baseline) == ["tests/test_a.py", "tests/test_b.py"]  # no prefix of the module, nothing outside its test root


def test_a_long_list_is_cut_by_bytes_and_counts_the_rest() -> None:
    from tools.step_size import occupied_field

    paths = [f"src/test/java/org/example/very/long/package/name/Test{index:04d}.java" for index in range(400)]
    field = occupied_field(paths)
    assert 3900 < len(json.dumps(field, ensure_ascii=False).encode("utf-8")) <= 4096
    assert field["paths"] == paths[:len(field["paths"])] and 0 < len(field["paths"]) < 400
    assert field["more"] == f"и ещё {400 - len(field['paths'])}"
    assert "more" not in occupied_field(paths[:3])


def test_the_reviewer_gets_no_field_and_a_module_without_held_paths_none_either(tmp_path: Path) -> None:
    from tests.test_finish import _project
    from tools.generated_delta import occupied_paths
    from tools.step_size import request_files

    _project_path, run_root, _attempt_id, _values = _project(tmp_path)  # its project holds tests/conftest.py and tests/test_existing.py
    assert request_files(run_root, "tc-to-autotest:r1")["occupied_paths"]["paths"] == ["tests/conftest.py", "tests/test_existing.py"]
    assert "occupied_paths" not in request_files(run_root, "autotest-reviewer:r1")
    assert occupied_paths({"module": ".", "test_root": "tests", "inputs": [{"project_path": "src/app.py"}]}) == []
