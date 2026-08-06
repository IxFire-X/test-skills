import copy
import json
import sys


def test_markdown_projections_equal_rendered_contract(render_contract_docs, root):
    """Catches Markdown projections drifting from the normative JSON contract."""
    contract = json.loads((root / "contracts/pipeline.json").read_text(encoding="utf-8"))

    assert (root / "CONTRACTS.md").read_text(encoding="utf-8") == render_contract_docs.render_contracts(contract)
    assert (root / "PIPELINE.md").read_text(encoding="utf-8") == render_contract_docs.render_pipeline(contract)


def test_renderer_rejects_outside_projection_without_creating_target(render_contract_docs, contract, tmp_path, monkeypatch):
    """Catches a renderer write outside its declared portable pack root."""
    portable_root = tmp_path / "portable-pack"
    contract_dir = portable_root / "contracts"
    contract_dir.mkdir(parents=True)
    outside_target = tmp_path / "outside-contracts.md"
    invalid_contract = copy.deepcopy(contract)
    invalid_contract["projections"]["contracts"] = "../outside-contracts.md"
    (contract_dir / "pipeline.json").write_text(json.dumps(invalid_contract), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["render_contract_docs.py", "--root", str(portable_root)])

    exit_code = render_contract_docs.main()

    assert not outside_target.exists()
    assert exit_code == 2
