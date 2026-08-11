from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPOSITORY_ROOT / "skills" / "context-marker" / "SKILL.md"
CONTRACT = (
    REPOSITORY_ROOT
    / "skills"
    / "context-marker"
    / "references"
    / "context-artifact-contract.md"
)


def test_requirements_preserve_allowlisted_observables_without_secret_values():
    skill_text = " ".join(SKILL.read_text(encoding="utf-8").split())
    contract_text = " ".join(CONTRACT.read_text(encoding="utf-8").split())
    guidance = f"{skill_text} {contract_text}"

    for required_policy in (
        "action/condition plus externally observable result",
        "status, response field, error code, state/event",
        "deterministic and testable",
        "join only exact supported facts",
        "provenance for every contributing fact in deterministic order",
        "Do not copy secret values or credentials while preserving non-secret observables",
        "preserve the partial behavior but emit a warning/gap rather than inventing an oracle",
    ):
        assert required_policy in guidance

    assert "pocketbase" not in guidance.lower()
