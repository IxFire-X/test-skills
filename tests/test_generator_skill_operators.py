"""FaT-BL: the generator SKILL names the assertion operators of the canonical schema exactly — before a dialect blocker."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_dialect_blocker_rule_names_exactly_the_schema_operators() -> None:
    schema = json.loads((ROOT / "schemas" / "canonical-test-document.schema.json").read_text(encoding="utf-8"))
    operators = next(definition["properties"]["operator"]["enum"] for definition in schema["$defs"].values()
                     if isinstance(definition, dict) and "operator" in definition.get("properties", {}))
    skill = (ROOT / "skills" / "tc-generator" / "SKILL.md").read_text(encoding="utf-8")
    rule = next(line for line in skill.splitlines() if "UNSUPPORTED_ASSERTION_DIALECT" in line)
    named = re.findall(r"`([a-z_]+)`", rule.split("operators of the canonical schema:", 1)[1].split(".", 1)[0])
    assert named == operators
    assert "`step_output`" in rule and "`literal`" in rule
