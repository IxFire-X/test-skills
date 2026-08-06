import json
import re

SUPPORTED_FRONTMATTER_KEYS = {"name", "description"}
TOP_LEVEL_KEY = re.compile(r"^([A-Za-z][A-Za-z0-9_-]*):(?:\s*(.*))?$")


def _frontmatter_fields(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---"
    closing_index = lines.index("---", 1)
    fields = {}
    descriptions = []
    active_key = None
    for line in lines[1:closing_index]:
        match = TOP_LEVEL_KEY.match(line)
        if match:
            active_key = match.group(1)
            fields[active_key] = (match.group(2) or "").strip()
        elif active_key == "description" and line.strip():
            descriptions.append(line.strip())
    if fields.get("description") in {">", "|", ""}:
        fields["description"] = " ".join(descriptions)
    return fields


def test_registered_skill_packages_have_supported_frontmatter(root):
    """Catches legacy metadata keys that the portable skill validator rejects."""
    contract = json.loads((root / "contracts/pipeline.json").read_text(encoding="utf-8"))

    assert set(contract["skill_files"]) == {
        "context-marker",
        "tc-generator",
        "tc-reviewer",
        "tc-to-autotest",
        "autotest-reviewer",
        "orchestrate",
    }
    for skill_id, relative_path in contract["skill_files"].items():
        fields = _frontmatter_fields(root / relative_path)

        assert set(fields) <= SUPPORTED_FRONTMATTER_KEYS
        assert fields["name"] == skill_id
        assert fields["description"]
        assert "<" not in fields["description"]
        assert ">" not in fields["description"]
