import json


def test_markdown_projections_equal_rendered_contract(render_contract_docs, root):
    """Catches Markdown projections drifting from the normative JSON contract."""
    contract = json.loads((root / "contracts/pipeline.json").read_text(encoding="utf-8"))

    assert (root / "CONTRACTS.md").read_text(encoding="utf-8") == render_contract_docs.render_contracts(contract)
    assert (root / "PIPELINE.md").read_text(encoding="utf-8") == render_contract_docs.render_pipeline(contract)
