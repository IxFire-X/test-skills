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


def _yaml_project(tmp_path: Path, text: str = YAML_SKILLSRC) -> Path:
    import shutil

    project = tmp_path / "yaml-project"
    shutil.copytree(FIXTURE.parent, project, ignore=shutil.ignore_patterns(".skillsrc"))
    (project / "docs").mkdir(exist_ok=True)
    (project / "docs" / "req.md").write_text("# Требования\n\nR1. Сложение двух чисел.\n", encoding="utf-8")
    (project / ".skillsrc").write_text(text, encoding="utf-8")
    return project


def _cli(*args: str) -> tuple[int, dict]:
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          env={**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"})
    return done.returncode, json.loads(done.stdout)


def test_load_skillsrc_of_an_older_yaml_one_carries_the_russian_text_and_fix_of_ready(tmp_path: Path) -> None:
    from tools.ready import check_skillsrc

    project = _yaml_project(tmp_path)
    row = check_skillsrc(project)[0]
    with pytest.raises(SkillsrcError) as caught:
        load_skillsrc(project / ".skillsrc")
    assert caught.value.code == "invalid_json"
    assert str(caught.value) == row["message"] and "YAML" in str(caught.value)
    assert caught.value.fix == row["fix"] and "--write" in caught.value.fix and "service_url_env: SERVICE_URL" in caught.value.fix
    assert (project / ".skillsrc").read_text(encoding="utf-8") == YAML_SKILLSRC


def test_scan_of_an_older_yaml_skillsrc_says_what_to_do(tmp_path: Path) -> None:
    project = _yaml_project(tmp_path)
    code, out = _cli("tools.run_pipeline", "scan", "--project", str(project), "--docs", "docs/req.md", "--profile", "cases-only-v1")
    assert code == 2 and out["status"] == "error" and out["code"] == "invalid_json"
    assert "YAML" in out["message"] and "JSON" in out["message"] and "must be" not in out["message"]
    assert out["fix"] and "tools.init_skillsrc" in out["fix"] and "--write" in out["fix"] and ".skillsrc.yaml.bak" in out["fix"]
    assert (project / ".skillsrc").read_text(encoding="utf-8") == YAML_SKILLSRC


@pytest.mark.parametrize("flags", [(), ("--write",)])
def test_init_skillsrc_of_an_older_yaml_skillsrc_says_what_to_do_and_writes_nothing(tmp_path: Path, flags: tuple[str, ...]) -> None:
    project = _yaml_project(tmp_path)
    code, out = _cli("tools.init_skillsrc", "--project", str(project), *flags)
    assert code == 2 and out["status"] == "error" and out["errors"] == ["invalid_json"] and out["written"] is False
    assert "YAML" in out["message"] and "JSON" in out["message"]
    assert ".skillsrc.yaml.bak" in out["fix"] and "--write" in out["fix"] and "service_url_env: SERVICE_URL" in out["fix"]
    assert (project / ".skillsrc").read_text(encoding="utf-8") == YAML_SKILLSRC
    assert not (project / ".skillsrc.yaml.bak").exists()


def test_a_truly_broken_json_skillsrc_is_refused_with_its_cause_and_a_fix(tmp_path: Path) -> None:
    # F5 (8.10): the code stays invalid_json and the text "must be one JSON object"; what changed is the fix (it was None) —
    # the cause (the line of the JSON error) and the way out, in the scan and in init_skillsrc alike
    project = _yaml_project(tmp_path, '{"version": "3.0", ' + chr(10))
    with pytest.raises(SkillsrcError) as caught:
        load_skillsrc(project / ".skillsrc")
    assert caught.value.code == "invalid_json" and "must be one JSON object" in str(caught.value)
    assert "line 2" in caught.value.fix and ".skillsrc.bak" in caught.value.fix and "--write" in caught.value.fix
    code, out = _cli("tools.run_pipeline", "scan", "--project", str(project), "--docs", "docs/req.md", "--profile", "cases-only-v1")
    assert code == 2 and out["code"] == "invalid_json" and "must be one JSON object" in out["message"] and out["fix"] == caught.value.fix
    assert not (project / ".pilot-runs").exists()
    code, out = _cli("tools.init_skillsrc", "--project", str(project), "--write")
    assert code == 2 and out["errors"] == ["invalid_json"] and out["fix"] == caught.value.fix and "must be one JSON object" in out["message"]


def test_the_v2_feature_sources_reach_the_module_only_when_there_are_some() -> None:
    from tools.skillsrc_manifest import normalize_skillsrc

    plain = {"version": "2.0", "project": {"language": "python"}}
    assert "feature_sources" not in normalize_skillsrc(plain)["modules"][0]
    declared = normalize_skillsrc({**plain, "sdd": {"requirements": "docs/r.md"}})["modules"][0]
    assert declared["feature_sources"] == {"requirements": ["docs/r.md"]}
