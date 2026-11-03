"""P5: ``.skillsrc`` is a JSON object (the file keeps its name). An older YAML one is not parsed; ``ready`` lists its keys."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import init_skillsrc
from tools.skillsrc_manifest import SkillsrcError, load_skillsrc, parse_skillsrc_bytes

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "projects" / "python-pytest" / ".skillsrc"
YAML_SKILLSRC = """# написано прежним пакетом
schema_version: "5.0.0"
version: "3.0"
project:
  name: "demo"
limits:
  step_window_bytes: 0
  context_batch_bytes: 100000
review:
  mode: human
modules:
  - id: "backend"
    root: "."
    service_url_env: SERVICE_URL
    paths:
      source: ["src"]
      tests: ["tests"]
      include:
        - "docs/**"
        - "generated/api.md"
projection:
  enabled: true
"""


def test_init_skillsrc_writes_a_json_object_with_the_same_name() -> None:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload = init_skillsrc._payload(document)
    assert payload.startswith(b"{") and payload.endswith(b"}\n") and b"\r" not in payload
    assert json.loads(payload.decode("utf-8")) == document
    assert parse_skillsrc_bytes(payload) == document
    assert init_skillsrc._payload(document) == payload  # the same bytes every time: the digests bind them


def test_init_skillsrc_creates_dot_skillsrc_in_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_skillsrc_authority import _patch_discovery

    _patch_discovery(monkeypatch)
    receipt = init_skillsrc.ensure_skillsrc(tmp_path, {}, write=True)
    assert receipt["status"] == "created" and receipt["skillsrc_path"] == ".skillsrc"
    written = (tmp_path / ".skillsrc").read_bytes()
    assert json.loads(written)["modules"][0]["id"] == "root"
    assert not (tmp_path / ".skillsrc.json").exists()
    assert load_skillsrc(tmp_path / ".skillsrc")["version"] == "3.0"


def test_a_bom_is_tolerated_and_a_duplicate_key_is_refused() -> None:
    raw = FIXTURE.read_bytes()
    assert parse_skillsrc_bytes(b"\xef\xbb\xbf" + raw) == parse_skillsrc_bytes(raw)
    with pytest.raises(SkillsrcError) as caught:
        parse_skillsrc_bytes(b'{"version": "3.0", "version": "3.0"}')
    assert caught.value.code == "invalid_json" and "duplicate" in caught.value.details[0]["message"]


@pytest.mark.parametrize("payload", [YAML_SKILLSRC.encode("utf-8"), b"", b"{", b"[1]\n", b"limits: [unclosed\n"])
def test_anything_but_a_json_object_is_refused_with_a_clear_code(payload: bytes) -> None:
    with pytest.raises(SkillsrcError) as caught:
        parse_skillsrc_bytes(payload)
    assert caught.value.code in {"invalid_json", "invalid_shape"}
    if caught.value.code == "invalid_json":
        assert "JSON" in str(caught.value)


def test_ready_prints_the_keys_of_an_older_yaml_skillsrc_without_parsing_it(tmp_path: Path) -> None:
    from tools.ready import check_skillsrc, legacy_yaml_keys

    (tmp_path / ".skillsrc").write_text(YAML_SKILLSRC, encoding="utf-8")
    lines = legacy_yaml_keys(tmp_path)
    assert lines is not None
    text = "\n".join(lines)
    for expected in ("limits", "step_window_bytes: 0", "review", "mode: human", "service_url_env: SERVICE_URL", "paths.include", '"docs/**"', "projection", "enabled: true"):
        assert expected in text, (expected, text)
    assert "schema_version" not in text and "modules" not in text
    rows = check_skillsrc(tmp_path)
    assert len(rows) == 1 and rows[0]["id"] == "skillsrc" and rows[0]["level"] == "STOP"
    assert "YAML" in rows[0]["message"] and "service_url_env: SERVICE_URL" in rows[0]["fix"] and "--write" in rows[0]["fix"]
    assert rows[0]["details"]["legacy_yaml_keys"] == lines
    assert (tmp_path / ".skillsrc").read_text(encoding="utf-8") == YAML_SKILLSRC  # ready only reads


def test_ready_does_not_call_a_json_skillsrc_old(tmp_path: Path) -> None:
    from tools.ready import legacy_yaml_keys

    assert legacy_yaml_keys(tmp_path) is None
    (tmp_path / ".skillsrc").write_bytes(FIXTURE.read_bytes())
    assert legacy_yaml_keys(tmp_path) is None
    (tmp_path / ".skillsrc").write_bytes(b"\xef\xbb\xbf" + FIXTURE.read_bytes())
    assert legacy_yaml_keys(tmp_path) is None
    (tmp_path / ".skillsrc").write_text("# nothing to carry over\nschema_version: '5.0.0'\n", encoding="utf-8")
    assert legacy_yaml_keys(tmp_path) == []


def test_the_example_is_json_for_a_java_project_and_valid() -> None:
    text = (ROOT / ".skillsrc.example").read_text(encoding="utf-8")
    assert text.lstrip().startswith("{")
    document = parse_skillsrc_bytes(text.encode("utf-8"))
    assert document["modules"] and document["modules"][0]["stack"]["language"] == "java"
