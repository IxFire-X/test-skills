from pathlib import Path

from helpers import projection_fixture, run_module


def test_checked_in_projections_are_exact_machine_truth_renders(pack_root: Path) -> None:
    result = run_module(pack_root, "tools.render_contract_docs", "--root", str(pack_root), "--check")

    assert result.returncode == 0, result.stderr or result.stdout


def test_projection_check_rejects_a_stale_render(pack_root: Path, tmp_path: Path) -> None:
    fixture_root = projection_fixture(pack_root, tmp_path / "projection-root")
    (fixture_root / "CONTRACTS.md").write_text("stale render\n", encoding="utf-8")

    result = run_module(pack_root, "tools.render_contract_docs", "--root", str(fixture_root), "--check")

    assert result.returncode == 1
    assert result.stdout == "projection drift: CONTRACTS.md\n"
