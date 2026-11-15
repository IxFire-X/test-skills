"""pm2a: the answer of the automation generator that names a path the project already holds is refused at ``submit``, not after two stages at ``materialize``."""
from __future__ import annotations

from pathlib import Path

from tests.helpers import prepare_stage, put_in_slot, submit_stage
from tests.test_autotest_cycle import GENERATOR, autotest_run
from tests.test_stage_steps import _events


def _held_run(tmp_path: Path) -> dict:
    """The project holds the file that the answer of the fixture generator writes — a test an earlier run left."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_products.py").write_text("def test_old():\n    pass\n", encoding="utf-8")
    return autotest_run(tmp_path)


def _answer(run: dict, path: str | None = None) -> dict:
    from tests.test_automation_revision_budget import automation

    artifact = automation(run["document"], effective_bundle_receipt_digest=run["bundle"])
    if path is not None:
        artifact["artifacts"]["generated_files"][0]["path"] = path
    return artifact


def test_a_path_the_project_holds_is_refused_at_submit_with_both_ways_out(tmp_path: Path) -> None:
    run = _held_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    put_in_slot(run["root"], GENERATOR, _answer(run))
    before = _events(run["root"])
    code, out = submit_stage(run["root"], GENERATOR, None)
    assert code == 2 and out["code"] == "MODEL_STAGE_ARTIFACT_INVALID" and "/artifacts/generated_files/0/path MATERIALIZATION_CONFLICT" in out["message"], out
    assert "другой путь" in out["fix"] and "scan --new-run" in out["fix"] and _events(run["root"]) == before
    assert (tmp_path / "tests" / "test_products.py").read_text(encoding="utf-8") == "def test_old():\n    pass\n"


def test_another_path_beside_the_held_one_is_taken(tmp_path: Path) -> None:
    run = _held_run(tmp_path)
    prepare_stage(run["root"], GENERATOR)
    code, _out = submit_stage(run["root"], GENERATOR, _answer(run, "tests/test_products_again.py"))
    assert code == 0 and _events(run["root"])[-2:] == [f"MODEL_RESPONSE_RECEIVED:{GENERATOR}", f"CANDIDATE_PUBLISHED:{GENERATOR}"]


def test_the_held_paths_of_a_module_are_told_in_the_namespace_of_its_test_root() -> None:
    from tools import generated_delta

    baseline = {"module": "services/api", "inputs": [{"project_path": "services/api/tests/test_a.py"}, {"project_path": "other/tests/test_b.py"}]}
    generated = [{"path": "tests/test_a.py"}, {"path": "tests/test_b.py"}, {"path": "tests\\test_a.py"}]
    assert generated_delta.held_indexes(baseline, generated) == [0, 2]


def test_removing_the_old_test_and_a_new_scan_free_the_path(tmp_path: Path, monkeypatch) -> None:
    """The second way out of the fix: the baseline is frozen by ``scan``, so only a new scan sees the project without the old test."""
    from tests.test_first_run import _args, _python_project
    from tools import generated_delta, pilot_state, run_pipeline

    project = _python_project(tmp_path, runtime=True)
    old = project / "services" / "api" / "tests" / "test_products.py"
    old.parent.mkdir()
    old.write_text("def test_old():\n    pass\n", encoding="utf-8")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)

    def held() -> list[int]:
        assert run_pipeline.cmd_scan(args) == 0, payloads[-1]
        run_root = Path(payloads[-1]["run_root"])
        frozen = pilot_state.frozen_baseline(run_root, pilot_state.derive_state(run_root)["attempts"][-1])
        return generated_delta.held_indexes(frozen, [{"path": "tests/test_products.py"}])

    args = _args(project)
    assert held() == [0]
    old.unlink()
    args.new_run = True
    assert held() == []


def test_another_refusal_of_the_same_code_does_not_advise_a_new_path() -> None:
    """``MATERIALIZATION_CONFLICT`` has many causes: its fix is no row of the table, so none of them is told «name another path»."""
    from tools.generated_delta import GeneratedDeltaError
    from tools.json_cli import error_fields

    assert "fix" not in error_fields(GeneratedDeltaError("MATERIALIZATION_CONFLICT", "generated file content digest does not bind UTF-8 source bytes"))
