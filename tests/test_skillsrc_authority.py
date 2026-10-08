import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError

from tools import init_skillsrc
from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes, resolve_module_root


def _discovery() -> dict:
    return {
        "status": "ready",
        "project_name": "demo",
        "fingerprint": "a" * 64,
        "questions": [],
        "modules": [
            {
                "id": "root",
                "root": ".",
                "stack": {"language": "python", "build_tool": "pip"},
                "paths": {"source": ["src"], "tests": ["tests"]},
                "test": {
                    "framework": "pytest",
                    "adapter_id": "pytest:selected-symbols-v1",
                    "interpreter": ".venv/Scripts/python.exe",
                    "build_profile": "default",
                    "adapter_parameters": {},
                },
                "detected_from": ["pyproject.toml"],
            }
        ],
        "warnings": [],
        "errors": [],
    }


def _patch_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_skillsrc, "discover_project", lambda _root: _discovery())


def test_discovered_java_wrapper_survives_compilation_for_the_current_host(tmp_path: Path, monkeypatch) -> None:
    import os
    from tools.discover_project import discover_project

    source = tmp_path / "src/main/java/App.java"
    source.parent.mkdir(parents=True)
    source.write_text("class App {}", encoding="utf-8")
    (tmp_path / "pom.xml").write_text('<project><modelVersion>4.0.0</modelVersion><groupId>sample</groupId><artifactId>sample</artifactId><version>1</version><dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.0</version></dependency></dependencies></project>', encoding="utf-8")
    for name in ("mvnw", "mvnw.cmd"):
        (tmp_path / name).write_text("wrapper fixture", encoding="utf-8")
        (tmp_path / name).chmod(0o755)
    discovery = discover_project(tmp_path)
    assert discovery["status"] == "ready", discovery
    from tools.schema_validation import schema_diagnostics
    pack = Path(__file__).resolve().parents[1]
    assert schema_diagnostics(discovery, pack / "schemas/project-discovery-output.schema.json", pack) == []
    compiled = init_skillsrc.compile_skillsrc(discovery, {}, tmp_path)
    # Decision 17: the manifest keeps the logical wrapper name on every host.
    assert compiled["modules"][0]["test"]["wrapper"] == "mvnw"

    (tmp_path / "build.gradle").write_text("dependencies { testImplementation 'org.junit.jupiter:junit-jupiter:5.10.0' }", encoding="utf-8")
    for name in ("gradlew", "gradlew.bat"):
        (tmp_path / name).write_text("wrapper fixture", encoding="utf-8")
        (tmp_path / name).chmod(0o755)
    discovery = discover_project(tmp_path)
    assert discovery["status"] == "needs_input", discovery
    for build, wrapper in (("maven", "mvnw"), ("gradle", "gradlew")):
        compiled = init_skillsrc.compile_skillsrc(discovery, {"module:root:stack.build_tool": build}, tmp_path)
        assert compiled["modules"][0]["test"]["wrapper"] == wrapper
        assert compiled["modules"][0]["test"]["adapter_id"] == f"{build}-wrapper:selected-symbols-v1"

    for name in ("mvnw", "mvnw.cmd"):
        if os.name == "nt":
            (tmp_path / name).unlink()
        else:
            (tmp_path / name).chmod(0o644)
    launcher = tmp_path / ("mvn.cmd" if os.name == "nt" else "mvn")
    launcher.write_text("system Maven fixture", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    compiled = init_skillsrc.compile_skillsrc(discovery, {"module:root:stack.build_tool": "maven"}, tmp_path)
    assert compiled["modules"][0]["test"]["adapter_id"] == "maven:selected-symbols-v1"
    assert compiled["modules"][0]["test"]["executable"] == "mvn"


def test_missing_skillsrc_is_created_atomically_and_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_discovery(monkeypatch)

    receipt = init_skillsrc.ensure_skillsrc(tmp_path, {}, write=True)

    payload = (tmp_path / ".skillsrc").read_bytes()
    assert receipt["status"] == "created"
    assert receipt["schema_version"] == "5.0.0"
    assert receipt["written"] is True
    assert receipt["read_back_digest"] == "sha256:" + hashlib.sha256(payload).hexdigest()
    assert parse_skillsrc_bytes(payload)["schema_version"] == "5.0.0"
    assert parse_skillsrc_bytes(payload)["version"] == "3.0"


def test_existing_valid_skillsrc_is_authoritative_and_drift_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_discovery(monkeypatch)
    original = b"version: '3.0'\nproject:\n  name: existing\ndiscovery:\n  on_missing: automatic\n  conflict_policy: ask_user\nmodules:\n  - id: root\n    root: '.'\n    stack:\n      language: python\n      build_tool: pip\n    paths:\n      source: [src]\n      tests: [tests]\n    test:\n      framework: pytest\n      adapter_id: pytest:selected-symbols-v1\n      interpreter: .venv/Scripts/python.exe\n      build_profile: default\n      adapter_parameters: {}\n    detected_from: [pyproject.toml]\n"
    (tmp_path / ".skillsrc").write_bytes(original)

    receipt = init_skillsrc.ensure_skillsrc(tmp_path, {}, write=True)

    assert (tmp_path / ".skillsrc").read_bytes() == original
    assert receipt["status"] == "needs_input"
    assert receipt["written"] is False
    assert len(receipt["questions"]) == 1
    assert receipt["questions"][0]["id"] == "skillsrc:replace"
    assert receipt["proposal"]["original_digest"] == "sha256:" + hashlib.sha256(original).hexdigest()
    assert receipt["proposal"]["structural_diff"]
    assert "existing" not in repr(receipt["proposal"])
    assert "demo" not in repr(receipt["proposal"])


def test_existing_valid_v2_skillsrc_is_authoritative_without_explicit_replacement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_discovery(monkeypatch)
    original = b"project:\n  name: legacy\n  language: python\n  build_tool: pip\npaths:\n  source: src\n  tests: tests\ntest:\n  framework: pytest\n"
    (tmp_path / ".skillsrc").write_bytes(original)

    receipt = init_skillsrc.ensure_skillsrc(tmp_path, {}, write=True)

    assert (tmp_path / ".skillsrc").read_bytes() == original
    assert receipt["status"] == "needs_input"
    assert receipt["written"] is False
    assert len(receipt["questions"]) == 1
    assert receipt["proposal"]["original_digest"] == "sha256:" + hashlib.sha256(original).hexdigest()


def test_confirmed_replacement_requires_bound_digests_and_evidences_old_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_discovery(monkeypatch)
    original = b"version: '3.0'\nproject:\n  name: existing\ndiscovery:\n  on_missing: automatic\n  conflict_policy: ask_user\nmodules:\n  - id: root\n    root: '.'\n    stack:\n      language: python\n      build_tool: pip\n    paths:\n      source: [src]\n      tests: [tests]\n    test:\n      framework: pytest\n      adapter_id: pytest:selected-symbols-v1\n      interpreter: .venv/Scripts/python.exe\n      build_profile: default\n      adapter_parameters: {}\n    detected_from: [pyproject.toml]\n"
    (tmp_path / ".skillsrc").write_bytes(original)
    preview = init_skillsrc.ensure_skillsrc(tmp_path, {}, write=True)
    evidence_dir = tmp_path / "immutable-evidence"

    stale = init_skillsrc.ensure_skillsrc(
        tmp_path,
        {},
        write=True,
        replacement_approval={"approval_id": "approval-1", "original_digest": "sha256:" + "0" * 64, "proposed_digest": preview["proposal"]["proposed_digest"]},
        immutable_evidence_dir=evidence_dir,
    )
    assert stale["status"] == "error"
    assert stale["errors"] == ["replacement_unbound"]
    assert (tmp_path / ".skillsrc").read_bytes() == original
    evidence_dir.mkdir()
    existing_evidence = evidence_dir / (hashlib.sha256(original).hexdigest() + ".skillsrc")
    existing_evidence.write_bytes(original)

    receipt = init_skillsrc.ensure_skillsrc(
        tmp_path,
        {},
        write=True,
        replacement_approval={"approval_id": "approval-1", "original_digest": preview["proposal"]["original_digest"], "proposed_digest": preview["proposal"]["proposed_digest"]},
        immutable_evidence_dir=evidence_dir,
    )

    assert receipt["status"] == "updated"
    assert receipt["written"] is True
    assert existing_evidence.read_bytes() == original
    assert receipt["read_back_digest"] == "sha256:" + hashlib.sha256((tmp_path / ".skillsrc").read_bytes()).hexdigest()


def test_replacement_evidence_cannot_escape_the_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_discovery(monkeypatch)
    original = b"project:\n  name: legacy\n  language: python\n  build_tool: pip\npaths:\n  source: src\n  tests: tests\ntest:\n  framework: pytest\n"
    (tmp_path / ".skillsrc").write_bytes(original)
    preview = init_skillsrc.ensure_skillsrc(tmp_path, {}, write=True)
    outside = tmp_path.parent / "outside-evidence"
    outside.mkdir(exist_ok=True)

    receipt = init_skillsrc.ensure_skillsrc(
        tmp_path, {}, write=True,
        replacement_approval={
            "approval_id": "approval-1",
            "original_digest": preview["proposal"]["original_digest"],
            "proposed_digest": preview["proposal"]["proposed_digest"],
        },
        immutable_evidence_dir=outside,
    )

    assert receipt["status"] == "error"
    assert receipt["errors"] == ["evidence_write_error"]
    assert (tmp_path / ".skillsrc").read_bytes() == original


@pytest.mark.parametrize(
    "test,stack,valid",
    [
        ({"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": ".venv/Scripts/python.exe", "build_profile": "default", "adapter_parameters": {}}, {"language": "python", "build_tool": "pip"}, True),
        ({"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": "mvnw.cmd", "build_profile": "default", "adapter_parameters": {}}, {"language": "java", "build_tool": "maven"}, True),
        ({"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": ".venv/Scripts/python.exe", "build_profile": "default", "adapter_parameters": {}, "build_command": "pytest"}, {"language": "python", "build_tool": "pip"}, False),
        ({"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": "mvnw.cmd", "build_profile": "default", "adapter_parameters": {}}, {"language": "python", "build_tool": "pip"}, False),
        ({"framework": "pytest", "adapter_id": "pytest:selected-symbols-v1", "interpreter": ".venv/Scripts/python.exe", "build_profile": "default", "adapter_parameters": {}}, {"language": "typescript", "build_tool": "npm"}, False),
        ({"framework": "junit5", "adapter_id": "maven-wrapper:selected-symbols-v1", "wrapper": "scripts/maven-wrapper", "build_profile": "default", "adapter_parameters": {}}, {"language": "java", "build_tool": "maven"}, False),
        ({"framework": "junit5", "adapter_id": "gradle-wrapper:selected-symbols-v1", "wrapper": "tools/gradlew.bat", "build_profile": "default", "adapter_parameters": {}}, {"language": "java", "build_tool": "gradle"}, True),
    ],
)
def test_v3_test_adapter_is_closed_and_stack_compatible(test: dict, stack: dict, valid: bool) -> None:
    document = _discovery()
    document = {"version": "3.0", "project": {"name": "demo"}, "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"}, "modules": document["modules"]}
    document["modules"][0]["stack"] = stack
    document["modules"][0]["test"] = test

    if valid:
        assert parse_skillsrc_bytes(init_skillsrc._payload(document))["modules"][0]["test"] == test
    else:
        with pytest.raises(SkillsrcError) as failure:
            parse_skillsrc_bytes(init_skillsrc._payload(document))
        assert failure.value.code == "schema_invalid"


def test_module_root_requires_existing_non_reparse_directory(tmp_path: Path) -> None:
    with pytest.raises(SkillsrcError) as failure:
        resolve_module_root(tmp_path, {"root": "missing"})
    assert failure.value.code == "unsafe_module_root"


def test_creation_and_replacement_receipts_require_readback_and_proposal(pack_root: Path) -> None:
    schema = json.loads((pack_root / "schemas" / "skillsrc-init-output.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    created = {
        "schema_version": "5.0.0", "status": "created", "skillsrc_path": ".skillsrc", "written": True,
        "module_ids": ["root"], "questions": [], "changes": [{"operation": "create", "path": ".skillsrc"}],
        "warnings": [], "errors": [], "discovery_fingerprint": "0" * 64,
    }
    updated = {
        **created,
        "status": "updated",
        "changes": [{"operation": "update", "path": ".skillsrc"}],
        "read_back_digest": "sha256:" + "0" * 64,
    }

    with pytest.raises(ValidationError):
        validator.validate(created)
    with pytest.raises(ValidationError):
        validator.validate(updated)


def test_the_example_skillsrc_is_valid_with_its_optional_sections_enabled(tmp_path: Path) -> None:
    """Independent review 2.2: the example names the schema version its sections need (5.2.0)."""
    import re

    from tools.skillsrc_manifest import load_skillsrc

    root = Path(__file__).resolve().parents[1]
    text = (root / ".skillsrc.example").read_text(encoding="utf-8")
    assert re.search(r'^schema_version: "5\.2\.0"', text, re.M)
    (tmp_path / ".skillsrc").write_text(text, encoding="utf-8")
    assert load_skillsrc(tmp_path / ".skillsrc")["schema_version"] == "5.2.0"
    enabled = re.sub(r"^# ((?:mutation|review_runner|requirements|suite):.*)$", r"\1", text, flags=re.M)
    enabled = re.sub(r"^#(  .*)$", r"\1", enabled, flags=re.M)
    (tmp_path / ".skillsrc").write_text(enabled, encoding="utf-8")
    loaded = load_skillsrc(tmp_path / ".skillsrc")
    assert {"mutation", "review_runner", "requirements", "suite"} <= set(loaded)
