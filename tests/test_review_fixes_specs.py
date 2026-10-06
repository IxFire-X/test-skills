"""Regression tests for the 2026-10-05 review: specs, context, inventory, discovery.

Covers B9, B3 (inventory part), B11, M27-M32, M34 and M37.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import init_skillsrc, project_inventory, run_pipeline, scan_project
from tools.build_context import build_context, extract_inventory, openspec_diagnostics
from tools.discover_project import discover_project
from tools.project_inventory import (
    ContextSelectionError,
    build_execution_baseline,
    build_inventory,
    build_skillsrc_authority_receipt,
    select_context_batches,
    validate_context_receipt_binding,
)
from tools.schema_validation import schema_diagnostics

PACK = Path(__file__).resolve().parents[1]
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
SLACK_TOKEN = "xoxb-" + "123456789012-abcdefghijklmnop"
JWT = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1gFWFOEjXk"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAxyzabcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH\n-----END RSA PRIVATE KEY-----"


def _paths(inventory: dict) -> set[str]:
    return {item["project_path"] for item in inventory["files"]}


def _excluded(inventory: dict) -> dict[str, dict]:
    return {item["project_path"]: item for item in inventory["exclusions"]}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot(path: str, content: str) -> dict[str, str]:
    return {"path": path, "content": content, "sha256": "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()}


def _requirements(context: dict) -> list[dict]:
    return context["artifacts"]["analytics_documentation"]["requirements"]


def _pilot_skillsrc(project: Path, module: str) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / ".skillsrc").write_text(
        '{"schema_version":"5.0.0","version":"3.0","project":{"name":"fixture"},"discovery":{"on_missing":"automatic","conflict_policy":"ask_user"},'
        f'"modules":[{{"id":"selected","root":"{module}",'
        '"stack":{"language":"python","build_tool":"pip"},'
        '"paths":{"source":["src"],"tests":["tests"]},'
        '"test":{"framework":"pytest","adapter_id":"pytest:selected-symbols-v1",'
        '"interpreter":".venv/Scripts/python.exe","build_profile":"default","adapter_parameters":{}},"detected_from":["pyproject.toml"]}]}'
        "\n",
        encoding="utf-8",
    )


def _local_pilot_baseline(project: Path, inventory: dict) -> dict:
    skillsrc_id = next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == ".skillsrc")
    return build_execution_baseline(
        inventory, project_root=project,
        requirements={"requirement_id": "req-1", "digest": "sha256:" + "a" * 64},
        skillsrc_file_id=skillsrc_id,
        skillsrc_authority=build_skillsrc_authority_receipt((project / ".skillsrc").read_bytes()),
        execution_file_ids=[item["opaque_id"] for item in inventory["files"] if item["opaque_id"] != skillsrc_id],
        parent_build_file_ids=[],
        interpreter_path=".venv/Scripts/python.exe",
        interpreter_identity="sha256:" + "b" * 64,
        wrapper_path=None, wrapper_identity=None,
        adapter_id="pytest:selected-symbols-v1", build_profile="default", adapter_parameters={},
    )


# --------------------------------------------------------------------------- B9


def test_b9_ordinary_names_and_auth_code_are_not_treated_as_secrets(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / "password_reset.py", 'password = request.form.get("password")\n')
    _write(project / "token_utils.py", 'token = client.post("/login")\nsecret = settings.SECRET_KEY\n')
    _write(project / "private_messages.py", "api_key = os.environ['SERVICE_API_KEY_NAME']\n")
    _write(project / "id_generator.py", "def next_id(): return 1\n")
    _write(project / "docs" / "password-reset.md", "# Сброс пароля\nПользователь вводит password = новый пароль.\n")
    _write(project / "docs" / "api-key-rotation.md", "# Ротация\ntoken: выдаётся заново\n")
    _write(project / "short.py", 'password = "fifteen-chars-x"\n')

    inventory = build_inventory(project, project)

    assert _paths(inventory) == {
        "password_reset.py", "token_utils.py", "private_messages.py", "id_generator.py",
        "docs/password-reset.md", "docs/api-key-rotation.md", "short.py",
    }
    assert inventory["exclusions"] == []
    assert all("redactions" not in item for item in inventory["files"])


def test_b9_explicit_secret_file_names_are_still_excluded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    names = [".env", ".env.local", ".envrc", "server.pem", "tls.key", "store.p12", "cert.pfx", "vault.kdbx", "id_rsa", "id_ed25519.pub"]
    for name in names:
        _write(project / name, "material\n")
    _write(project / "secrets" / "notes.txt", "plain\n")
    _write(project / "credentials" / "data.json", "{}\n")
    _write(project / "app.py", "VALUE = 1\n")

    inventory = build_inventory(project, project)

    excluded = _excluded(inventory)
    assert _paths(inventory) == {"app.py"}
    for name in [*names, "secrets", "credentials"]:
        assert excluded[name]["reason_code"] == "SECRET_SUSPECTED", name


@pytest.mark.parametrize(
    ("rule", "line"),
    [
        ("aws-key-v1", f"AWS = '{AWS_KEY}'"),
        ("github-token-v1", f"GH = {GITHUB_TOKEN}"),
        ("service-token-v1", f"slack: {SLACK_TOKEN}"),
        ("jwt-v1", f"Authorization: Bearer {JWT}"),
        ("private-key-block-v1", PEM),
        ("token-assignment-v1", 'api_key = "0123456789abcdef"'),
    ],
)
def test_b9_high_confidence_content_still_excludes_non_documents_and_blocks_execution(tmp_path: Path, rule: str, line: str) -> None:
    project = tmp_path / "project"
    module = project / "service"
    _pilot_skillsrc(project, "service")
    _write(module / "app.py", "VALUE = 1\n")
    leaked = _write(module / "settings.py", f"DEBUG = True\n{line}\n")

    inventory = build_inventory(project, module)

    assert _excluded(inventory)["service/settings.py"] == {
        "project_path": "service/settings.py", "reason_code": "SECRET_SUSPECTED",
        "scanner_rule_id": rule, "safe_label": "token-signature",
    }
    serialized = json.dumps(inventory)
    assert AWS_KEY not in serialized and GITHUB_TOKEN not in serialized and SLACK_TOKEN not in serialized
    assert hashlib.sha256(leaked.read_bytes()).hexdigest() not in serialized
    with pytest.raises(project_inventory.InventoryError, match="NOT_RUNNABLE/BASELINE_INCOMPLETE"):
        _local_pilot_baseline(project, inventory)


def test_b9_document_with_secret_is_masked_with_receipt_and_does_not_block_execution(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    _pilot_skillsrc(project, "service")
    _write(module / "app.py", "VALUE = 1\n")
    spec = _write(
        module / "docs" / "auth.md",
        f"# Вход\nКлюч стенда: {AWS_KEY}\nПользователь входит по паролю.\n{PEM}\nКонец.\n",
    )

    inventory = build_inventory(project, module)

    assert "service/docs/auth.md" in _paths(inventory)
    assert not [item for item in inventory["exclusions"] if item["reason_code"] == "SECRET_SUSPECTED"]
    entry = next(item for item in inventory["files"] if item["project_path"] == "service/docs/auth.md")
    assert entry["redactions"] == [
        {"line": 2, "scanner_rule_id": "aws-key-v1"},
        {"line": 4, "scanner_rule_id": "private-key-block-v1"},
        {"line": 5, "scanner_rule_id": "private-key-block-v1"},
        {"line": 6, "scanner_rule_id": "private-key-block-v1"},
    ]
    assert entry["content_digest"] == "sha256:" + hashlib.sha256(spec.read_bytes()).hexdigest()
    assert AWS_KEY not in json.dumps(inventory)
    assert schema_diagnostics(inventory, PACK / "schemas" / "inventory-receipt.schema.json", PACK) == []

    # A masked document is an ordinary baseline input: local-pilot stays runnable.
    baseline = _local_pilot_baseline(project, inventory)
    assert any(item["project_path"] == "service/docs/auth.md" for item in baseline["inputs"])

    batches = select_context_batches(inventory, project, [entry["opaque_id"]], byte_budget=64 * 1024)
    delivered = next(item for batch in batches for item in batch["files"] if item["project_path"] == "service/docs/auth.md")
    text = delivered["bytes"].decode("utf-8")
    assert text.splitlines() == [
        "# Вход", "[REDACTED:aws-key-v1]", "Пользователь входит по паролю.",
        "[REDACTED:private-key-block-v1]", "[REDACTED:private-key-block-v1]", "[REDACTED:private-key-block-v1]", "Конец.",
    ]
    assert AWS_KEY not in text and "MIIEow" not in text
    for batch in batches:
        validate_context_receipt_binding(inventory, project, batch["receipt"])


def test_b9_api_specifications_with_example_tokens_are_masked_not_excluded(tmp_path: Path) -> None:
    project = tmp_path / "project"
    module = project / "service"
    _pilot_skillsrc(project, "service")
    _write(module / "app.py", "VALUE = 1\n")
    _write(module / "openapi.yaml", f"openapi: 3.0.0\ncomponents:\n  examples:\n    token:\n      value: {JWT}\n")
    _write(module / "docs" / "errors.json", '{"example": "https://user:hunter2@example.test/api"}\n')
    _write(module / "features" / "login.feature", f"Feature: Login\n  Scenario: Token\n    Given header {GITHUB_TOKEN}\n")
    _write(module / "config" / "settings.yaml", f"auth: {JWT}\n")

    inventory = build_inventory(project, module)

    redacted = {item["project_path"]: [row["scanner_rule_id"] for row in item["redactions"]] for item in inventory["files"] if "redactions" in item}
    assert redacted == {
        "service/openapi.yaml": ["jwt-v1"], "service/docs/errors.json": ["url-credentials-v1"],
        "service/features/login.feature": ["github-token-v1"],
    }
    # A real configuration file is still excluded and still blocks local execution.
    assert _excluded(inventory)["service/config/settings.yaml"]["scanner_rule_id"] == "jwt-v1"
    with pytest.raises(project_inventory.InventoryError, match="NOT_RUNNABLE/BASELINE_INCOMPLETE"):
        _local_pilot_baseline(project, inventory)
    (module / "config" / "settings.yaml").write_text("auth: ${AUTH_TOKEN}\n", encoding="utf-8")
    assert _local_pilot_baseline(project, build_inventory(project, module))["policy_profile"] == "local-pilot-v1"


def test_b9_build_context_masks_secret_lines_in_requirement_text() -> None:
    content = f"## REQ-1\nИспользовать ключ {AWS_KEY} для стенда.\nОтвет 200.\n"
    context = build_context(Path.cwd(), docs_snapshot=[_snapshot("docs/auth.md", content)])

    requirement = _requirements(context)[0]
    assert AWS_KEY not in json.dumps(context, ensure_ascii=False)
    assert "[REDACTED:aws-key-v1]" in requirement["text"] and "Ответ 200." in requirement["text"]
    assert any("docs/auth.md:2" in warning and "[REDACTED:aws-key-v1]" in warning for warning in context["warnings"])
    assert schema_diagnostics({key: value for key, value in context.items() if key != "status"}, PACK / "schemas" / "context-marker-output.schema.json", PACK) == []


# --------------------------------------------------------------------------- B3 / M32


def _git(project: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=project, check=True, capture_output=True, timeout=60)


@pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")
def test_b3_inventory_respects_gitignore_in_a_git_repository(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / ".gitignore", "local-output/\n*.tmp\n")
    _write(project / "src" / "app.py", "VALUE = 1\n")
    _write(project / "src" / "untracked.py", "VALUE = 2\n")
    _write(project / "src" / "scratch.tmp", "noise\n")
    _write(project / "local-output" / "report.txt", "noise\n")
    _write(project / ".skillsrc", "{}\n")
    _git(project, "init", "-q")
    _git(project, "add", ".gitignore", "src/app.py")

    inventory = build_inventory(project, project)

    assert _paths(inventory) == {".gitignore", ".skillsrc", "src/app.py", "src/untracked.py"}
    excluded = _excluded(inventory)
    assert excluded["src/scratch.tmp"]["reason_code"] == "GIT_IGNORED"
    assert excluded["local-output"]["reason_code"] == "GIT_IGNORED"
    assert schema_diagnostics(inventory, PACK / "schemas" / "inventory-receipt.schema.json", PACK) == []

    # A nested module inside the repository uses the same ignore rules.
    nested = build_inventory(project, project / "src")
    assert _paths(nested) == {".skillsrc", "src/app.py", "src/untracked.py"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git is unavailable")
def test_b3_gitignored_skillsrc_is_still_a_baseline_candidate(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / ".gitignore", ".skillsrc\n")
    _write(project / ".skillsrc", "{}\n")
    _write(project / "app.py", "VALUE = 1\n")
    _git(project, "init", "-q")

    assert ".skillsrc" in _paths(build_inventory(project, project))


def test_b3_default_exclusions_cover_build_tool_state_ide_coverage_and_logs(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / "app.py", "VALUE = 1\n")
    _write(project / "coverage.py", "def measure(): return 1\n")
    for noise in (
        ".gradle/8.5/fileHashes.bin.txt", ".kotlin/sessions/s.txt", ".idea/workspace.xml", ".vscode/settings.json",
        "coverage.xml", ".coverage.host.123", "coverage-final.json", "coverage/lcov.info", "coverage-report/index.html",
        "server.log", "logs/app.log",
    ):
        _write(project / noise, "noise\n")

    inventory = build_inventory(project, project)

    assert _paths(inventory) == {"app.py", "coverage.py"}
    excluded = _excluded(inventory)
    for path in (".gradle", ".kotlin", ".idea", ".vscode", "coverage.xml", ".coverage.host.123", "coverage-final.json", "coverage", "coverage-report", "server.log", "logs/app.log"):
        assert path in excluded, path
    assert schema_diagnostics(inventory, PACK / "schemas" / "inventory-receipt.schema.json", PACK) == []


def test_b3_git_failures_fall_back_to_the_filesystem_walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    _write(project / "app.py", "VALUE = 1\n")
    (project / ".git").mkdir()
    calls: list[tuple[list[str], dict]] = []

    def missing(argv, **kwargs):
        calls.append((list(argv), kwargs))
        raise FileNotFoundError("git")

    monkeypatch.setattr(project_inventory.subprocess, "run", missing)
    assert _paths(build_inventory(project, project)) == {"app.py"}
    assert calls and calls[0][0][0] == "git"
    assert all("safe.directory" not in part for argv, _ in calls for part in argv)
    assert all(isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0 for _, kwargs in calls)

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))

    monkeypatch.setattr(project_inventory.subprocess, "run", slow)
    assert _paths(build_inventory(project, project)) == {"app.py"}

    monkeypatch.setattr(project_inventory.subprocess, "run", lambda argv, **kwargs: SimpleNamespace(returncode=128, stdout=b"", stderr=b"fatal: detected dubious ownership"))
    assert _paths(build_inventory(project, project)) == {"app.py"}


def test_m32_source_packages_named_build_generated_coverage_stay_in_inventory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    kept = {
        "app/build/__init__.py", "app/build/steps.py", "pkg/generated/models.py",
        "src/main/java/com/acme/coverage/Report.java", "src/main/java/com/acme/build/Plan.java",
        "internal/build/build.go",
    }
    for path in kept:
        _write(project / path, "content\n")
    for noise in ("build/libs/app.txt", "build/lib/pkg/mod.py", "generated/openapi/client.txt", "coverage/lcov-report/index.html", "target/classes/a.txt"):
        _write(project / noise, "noise\n")

    inventory = build_inventory(project, project)

    assert _paths(inventory) == kept
    excluded = _excluded(inventory)
    assert {"build", "generated", "coverage", "target"} <= set(excluded)

    from tools.stack_catalog import confined_files

    discovered = {path.relative_to(project).as_posix() for path in confined_files(project)}
    assert kept <= discovered
    assert not {"build/libs/app.txt", "build/lib/pkg/mod.py", "coverage/lcov-report/index.html"} & discovered


# --------------------------------------------------------------------------- B11


def test_b11_python_package_without_src_directory_is_source_ready(tmp_path: Path) -> None:
    project = tmp_path / "shop"
    _write(project / "requirements.txt", "django\npytest\n")
    _write(project / "manage.py", "import sys\n")
    _write(project / "shop" / "__init__.py", "")
    _write(project / "shop" / "views.py", "def index(): return 1\n")
    _write(project / "orders" / "__init__.py", "")
    _write(project / "orders" / "models.py", "class Order: pass\n")
    _write(project / "tests" / "test_views.py", "def test_ok(): pass\n")
    _write(project / "docs" / "requirements.txt", "sphinx\n")

    report = discover_project(project)

    assert report["status"] == "ready", report
    assert [module["root"] for module in report["modules"]] == ["."]
    module = report["modules"][0]
    assert module["readiness"] == "source_ready"
    assert module["paths"]["source"] == ["orders", "shop"]
    assert schema_diagnostics(report, PACK / "schemas" / "project-discovery-output.schema.json", PACK) == []


def test_b11_flat_python_module_uses_the_module_root_as_source(tmp_path: Path) -> None:
    project = tmp_path / "flat"
    _write(project / "pyproject.toml", "[project]\nname = 'flat'\ndependencies = ['pytest']\n")
    _write(project / "app.py", "def main(): return 1\n")

    report = discover_project(project)

    assert report["status"] == "ready", report
    assert report["modules"][0]["paths"]["source"] == ["."]


def test_b11_maven_pom_packaging_module_is_skipped(tmp_path: Path) -> None:
    project = tmp_path / "reactor"
    junit = "<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.0</version></dependency></dependencies>"
    _write(project / "pom.xml", "<project><modelVersion>4.0.0</modelVersion><packaging>pom</packaging><modules><module>bom</module><module>app</module></modules></project>")
    _write(project / "bom" / "pom.xml", "<project><modelVersion>4.0.0</modelVersion><artifactId>bom</artifactId><packaging>pom</packaging></project>")
    _write(project / "app" / "pom.xml", f"<project><modelVersion>4.0.0</modelVersion><artifactId>app</artifactId>{junit}</project>")
    _write(project / "app" / "src" / "main" / "java" / "App.java", "class App {}\n")

    report = discover_project(project)

    assert report["status"] == "ready", report
    assert [module["root"] for module in report["modules"]] == ["app"]


def test_b11_requirements_txt_without_python_sources_is_not_a_module(tmp_path: Path) -> None:
    project = tmp_path / "docs-only"
    _write(project / "docs" / "requirements.txt", "sphinx\n")
    _write(project / "docs" / "index.md", "# Docs\n")

    report = discover_project(project)

    assert report["status"] == "error"
    assert report["errors"] == ["runnable modules were not found"]


def test_b11_go_module_is_recognized_without_a_source_question(tmp_path: Path) -> None:
    project = tmp_path / "gosvc"
    _write(project / "go.mod", "module example.com/gosvc\n\ngo 1.22\n")
    _write(project / "main.go", "package main\nfunc main() {}\n")
    _write(project / "internal" / "api" / "api.go", "package api\n")

    report = discover_project(project)

    assert report["status"] == "ready", report
    module = report["modules"][0]
    assert module["stack"]["language"] == "go"
    assert module["readiness"] == "source_ready"
    assert not report["questions"]
    assert schema_diagnostics(report, PACK / "schemas" / "project-discovery-output.schema.json", PACK) == []

    receipt = init_skillsrc.ensure_skillsrc(project, {}, write=True)
    assert receipt["status"] == "created", receipt
    from tools.skillsrc_manifest import load_skillsrc

    written = load_skillsrc(project / ".skillsrc")
    assert written["modules"][0]["stack"]["language"] == "go"
    assert "test" not in written["modules"][0]

    empty = tmp_path / "goempty"
    _write(empty / "go.mod", "module example.com/goempty\n")
    report = discover_project(empty)
    assert report["status"] == "ready", report
    assert not any(question["field"].endswith(".paths.source") for question in report["questions"])


def test_b11_init_accepts_the_source_root_answer_and_writes_skillsrc(tmp_path: Path) -> None:
    project = tmp_path / "odd"
    _write(project / "pyproject.toml", "[project]\nname = 'odd'\ndependencies = ['pytest']\n")
    _write(project / "tests" / "code" / "service.py", "def run(): return 1\n")

    first = init_skillsrc.ensure_skillsrc(project, {}, write=True)
    assert first["status"] == "needs_input", first
    question = next(item for item in first["questions"] if item["field"].endswith(".paths.source"))
    assert not (project / ".skillsrc").exists()
    assert schema_diagnostics(first, PACK / "schemas" / "skillsrc-init-output.schema.json", PACK) == []

    invalid = init_skillsrc.ensure_skillsrc(project, {question["id"]: "../outside"}, write=True)
    assert invalid["status"] == "error" and invalid["errors"] == ["source_target_invalid"]
    missing = init_skillsrc.ensure_skillsrc(project, {question["id"]: "no-such-dir"}, write=True)
    assert missing["status"] == "error" and missing["errors"] == ["source_target_invalid"]
    assert not (project / ".skillsrc").exists()

    receipt = init_skillsrc.ensure_skillsrc(project, {question["id"]: "tests/code"}, write=True)

    assert receipt["status"] == "created", receipt
    assert schema_diagnostics(receipt, PACK / "schemas" / "skillsrc-init-output.schema.json", PACK) == []
    from tools.skillsrc_manifest import load_skillsrc

    written = load_skillsrc(project / ".skillsrc")
    assert written["modules"][0]["paths"]["source"] == ["tests/code"]
    assert init_skillsrc.ensure_skillsrc(project, {}, write=True)["status"] == "unchanged"


# --------------------------------------------------------------------------- M27 / M28


_BASE_SPEC = (
    "## Requirements\n"
    "### Requirement: Search\nSearch the catalog.\n#### Scenario: Found\n- WHEN search\n- THEN results\n"
    "### Requirement: Legacy export\nExport CSV.\n#### Scenario: Export\n- WHEN export\n- THEN file\n"
)
_DELTA_SPEC = (
    "## REMOVED Requirements\n### Requirement: Legacy export\nReason: retired.\n"
    "## MODIFIED Requirements\n### Requirement: Search\nSearch with filters.\n#### Scenario: Filtered\n- WHEN filter\n- THEN narrowed\n"
)


def test_m27_openspec_in_a_nested_directory_applies_deltas() -> None:
    entries = [
        _snapshot("svc/openspec/specs/catalog/spec.md", _BASE_SPEC),
        _snapshot("svc/openspec/changes/cleanup/specs/catalog/spec.md", _DELTA_SPEC),
        _snapshot("svc/openspec/changes/archive/2025-old/specs/catalog/spec.md", "## ADDED Requirements\n### Requirement: Archived\nOld.\n"),
    ]

    context = build_context(Path.cwd(), docs_snapshot=entries)

    rows = _requirements(context)
    assert len(rows) == 1
    assert "Search with filters." in rows[0]["text"] and "Legacy export" not in rows[0]["text"]
    assert any("capability=catalog; ### Requirement: Search" in mark for mark in rows[0]["provenance"])
    assert any("historical archived context" in warning for warning in context["warnings"])
    assert openspec_diagnostics(context, entries) == []


def test_m27_two_openspec_roots_keep_independent_capabilities_and_changes() -> None:
    entries = [
        _snapshot("a/openspec/specs/catalog/spec.md", _BASE_SPEC),
        _snapshot("a/openspec/changes/one/specs/catalog/spec.md", _DELTA_SPEC),
        _snapshot("b/openspec/specs/catalog/spec.md", _BASE_SPEC),
        _snapshot("b/openspec/changes/two/specs/catalog/spec.md", "## ADDED Requirements\n### Requirement: Sorting\nSort.\n#### Scenario: Sorted\n- WHEN sort\n- THEN ordered\n"),
    ]

    rows = _requirements(build_context(Path.cwd(), docs_snapshot=entries))

    assert [row["provenance"][2].split(" — ")[0].rsplit(":", 1)[0] for row in rows] == [
        "a/openspec/changes/one/specs/catalog/spec.md",
        "b/openspec/changes/two/specs/catalog/spec.md",
        "b/openspec/specs/catalog/spec.md",
        "b/openspec/specs/catalog/spec.md",
    ]


def test_m28_openspec_headings_tolerate_spacing_case_and_bom() -> None:
    base = (
        "﻿## requirements\n"
        "###Requirement: Search\nSearch the catalog.\n####Scenario: Found\n- WHEN search\n- THEN results\n"
        "###  requirement:Browse\nBrowse the catalog.\n#### scenario: Listed\n- WHEN browse\n- THEN list\n"
    )
    delta = (
        "﻿## Added Requirements\n### Requirement: Sorting\nSort.\n#### Scenario: Sorted\n- WHEN sort\n- THEN ordered\n"
        "##  modified   requirements\n###Requirement: Browse\nBrowse with paging.\n#### Scenario: Paged\n- WHEN browse\n- THEN page\n"
        "## Renamed Requirements\n- from: `###Requirement: Search`\n- to: `### Requirement: Find`\n"
    )
    entries = [_snapshot("openspec/specs/catalog/spec.md", base), _snapshot("openspec/changes/c1/specs/catalog/spec.md", delta)]

    context = build_context(Path.cwd(), docs_snapshot=entries)

    rows = _requirements(context)
    names = sorted(mark.split("### Requirement: ")[1] for row in rows for mark in row["provenance"] if "; ### Requirement:" in mark and "#### Scenario" not in mark)
    assert names == ["Browse", "Find", "Sorting"]
    browse = next(row for row in rows if "Browse with paging." in row["text"])
    assert "Search the catalog" not in browse["text"]
    search = next(row for row in rows if "Search the catalog." in row["text"])
    assert "Browse" not in search["text"]
    assert all("﻿" not in row["text"] for row in rows)
    assert openspec_diagnostics(context, entries) == []


# --------------------------------------------------------------------------- M29 / M30


def test_m29_source_requirement_ids_follow_document_order_and_stay_stable() -> None:
    original = "## Яблоки последними по алфавиту\nЯ: первое в документе.\n\n## Абрикосы\nА: второе в документе.\n"
    first = _requirements(build_context(Path.cwd(), docs_snapshot=[_snapshot("docs/spec.md", original)]))
    assert [(row["source_requirement_id"], row["text"].splitlines()[0]) for row in first] == [
        ("SREQ-0001", "## Яблоки последними по алфавиту"), ("SREQ-0002", "## Абрикосы"),
    ]

    extended = original + "\n## Айва\nНовый раздел в конце.\n"
    second = _requirements(build_context(Path.cwd(), docs_snapshot=[_snapshot("docs/spec.md", extended)]))
    assert [row["text"].splitlines()[0] for row in second] == ["## Яблоки последними по алфавиту", "## Абрикосы", "## Айва"]
    assert [row["source_requirement_id"] for row in second[:2]] == ["SREQ-0001", "SREQ-0002"]
    assert [row["display_order"] for row in second] == [1, 2, 3]


def test_m30_heading_without_requirement_text_is_not_a_requirement() -> None:
    content = (
        "# Владельцы\n\n"
        "## Оглавление\n- [Создание](#создание)\n- [Глоссарий](#глоссарий)\n\n"
        "## Создание\nВладелец создаётся с именем и телефоном.\n\n"
        "## Глоссарий\nВладелец — клиент клиники.\n\n"
        "## Удаление\n\n### Запрет\nНельзя удалить владельца с питомцами.\n"
    )

    rows = _requirements(build_context(Path.cwd(), docs_snapshot=[_snapshot("docs/owners.md", content)]))

    assert [row["text"].splitlines()[0] for row in rows] == ["## Создание", "### Запрет"]
    kinds = {row["title"]: row["kind"] for row in extract_inventory(content)}
    assert kinds["Владельцы"] == "structure" and kinds["Удаление"] == "structure"
    assert kinds["Оглавление"] == "reference" and kinds["Глоссарий"] == "reference"
    assert kinds["Создание"] == "flow"


# --------------------------------------------------------------------------- M31


def test_m31_oversized_file_becomes_an_explicit_gap_instead_of_failing_selection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write(project / "small.py", "VALUE = 1\n")
    _write(project / "openapi.json", "{" + " " * 400 + "}")
    inventory = build_inventory(project, project)
    ids = [item["opaque_id"] for item in inventory["files"]]

    batches = select_context_batches(inventory, project, ids, byte_budget=64)

    assert [item["project_path"] for batch in batches for item in batch["files"]] == ["small.py"]
    gaps = [gap for batch in batches for gap in batch["receipt"].get("gaps", [])]
    assert [(gap["project_path"], gap["reason_code"], gap["size"]) for gap in gaps] == [("openapi.json", "FILE_EXCEEDS_BYTE_BUDGET", 402)]
    assert batches[0]["receipt"]["byte_budget"] == 64
    for batch in batches:
        assert schema_diagnostics(batch["receipt"], PACK / "schemas" / "context-selection-receipt.schema.json", PACK) == []
        validate_context_receipt_binding(inventory, project, batch["receipt"])

    forged = json.loads(json.dumps(batches[0]["receipt"]))
    forged["gaps"][0]["project_path"] = "small.py"
    forged["digest"] = project_inventory._digest({key: value for key, value in forged.items() if key != "digest"})
    with pytest.raises(ContextSelectionError):
        validate_context_receipt_binding(inventory, project, forged)

    only_large = select_context_batches(inventory, project, [next(item["opaque_id"] for item in inventory["files"] if item["project_path"] == "openapi.json")], byte_budget=64)
    assert len(only_large) == 1 and only_large[0]["files"] == [] and len(only_large[0]["receipt"]["gaps"]) == 1
    validate_context_receipt_binding(inventory, project, only_large[0]["receipt"])


def _scan_project_fixture(tmp_path: Path, limits: dict | None = None) -> tuple[Path, Path]:
    project = tmp_path / "project"
    module = project / "services" / "api"
    (module / "src").mkdir(parents=True)
    document = {
        "schema_version": "5.0.0", "version": "3.0", "project": {"name": "demo"},
        "discovery": {"on_missing": "automatic", "conflict_policy": "ask_user"},
        "modules": [{
            "id": "nested", "root": "services/api", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]},
            "detected_from": ["services/api/pyproject.toml"],
        }],
    }
    if limits is not None:
        document["limits"] = limits
    (project / ".skillsrc").write_bytes(init_skillsrc._payload(document))
    (module / "pyproject.toml").write_text("[project]\nname = 'api'\n", encoding="utf-8")
    return project, module


def test_m31_scan_reports_oversized_file_as_gap_and_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module = _scan_project_fixture(tmp_path)
    (module / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (module / "openapi.json").write_bytes(b"{" + b" " * run_pipeline._SCAN_CONTEXT_BYTE_BUDGET + b"}")
    payloads: list[dict] = []
    monkeypatch.setattr(run_pipeline, "_print", payloads.append)
    args = SimpleNamespace(project=str(project), module="nested", target=None, docs=None, profile="cases-only-v1")

    assert run_pipeline.cmd_scan(args) == 0

    scan = payloads[-1]
    assert scan["status"] == "ok"
    assert scan["context_gaps"] == [{"project_path": "services/api/openapi.json", "reason_code": "FILE_EXCEEDS_BYTE_BUDGET", "size": run_pipeline._SCAN_CONTEXT_BYTE_BUDGET + 2}]
    receipts = sorted((Path(scan["run_root"]) / "context-selections" / scan["attempt_id"]).glob("*.json"))
    stored = [json.loads(path.read_text(encoding="utf-8")) for path in receipts]
    assert any(gap["project_path"] == "services/api/openapi.json" for receipt in stored for gap in receipt.get("gaps", []))
    assert any(item["project_path"] == "services/api/src/app.py" for receipt in stored for item in receipt["files"])


def test_m31_context_limits_are_configurable_in_skillsrc(tmp_path: Path) -> None:
    from tools.skillsrc_manifest import load_skillsrc

    project, module = _scan_project_fixture(tmp_path, {"context_batch_bytes": 2048, "docs_file_bytes": 4096, "docs_total_bytes": 6000})
    document = load_skillsrc(project / ".skillsrc")
    assert run_pipeline._scan_context_budget({}, document) == 2048
    assert run_pipeline._scan_context_budget({}, {"version": "3.0"}) == run_pipeline._SCAN_CONTEXT_BYTE_BUDGET

    big = _write(module / "docs" / "big.md", "## REQ-1\n" + "я" * 3000 + "\n")
    with pytest.raises(ValueError, match="NEED_DOCS_LIMIT|exceeds"):
        build_context(project, docs=[big])
    (project / ".skillsrc").write_bytes(init_skillsrc._payload({**document, "limits": {"docs_file_bytes": 16384, "docs_total_bytes": 32768}}))
    assert build_context(project, docs=[big])["status"] == "ok"
    # An explicit argument wins over the manifest, and manifests without limits keep the defaults.
    with pytest.raises(ValueError):
        build_context(project, docs=[big], max_file_bytes=1024)
    legacy = {key: value for key, value in document.items() if key != "limits"}
    (project / ".skillsrc").write_bytes(init_skillsrc._payload(legacy))
    assert build_context(project, docs=[big])["status"] == "ok"
    assert project_inventory.context_limits(legacy) == {"context_batch_bytes": 256 * 1024, "docs_file_bytes": 256 * 1024, "docs_total_bytes": 1024 * 1024}


# --------------------------------------------------------------------------- M34


def _ready_discovery() -> dict:
    return {
        "status": "ready", "project_name": "demo", "fingerprint": "a" * 64, "questions": [],
        "modules": [{
            "id": "root", "root": ".", "stack": {"language": "python", "build_tool": "pip"},
            "paths": {"source": ["src"], "tests": ["tests"]}, "detected_from": ["pyproject.toml"],
        }],
        "warnings": [], "errors": [],
    }


def test_m34_skillsrc_creation_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_skillsrc, "discover_project", lambda _root: _ready_discovery())
    destination = tmp_path / ".skillsrc"
    document = init_skillsrc.compile_skillsrc(_ready_discovery(), {}, tmp_path)
    concurrent = b"version: '3.0'\n# written by a concurrent initializer\n"

    with pytest.raises(init_skillsrc.InitError) as caught:
        init_skillsrc.atomic_write_skillsrc(
            tmp_path, destination, document, "a" * 64, None,
            before_replace=lambda: destination.write_bytes(concurrent),
        )

    assert caught.value.code == "destination_changed"
    assert destination.read_bytes() == concurrent
    assert sorted(path.name for path in tmp_path.iterdir()) == [".skillsrc"]

    destination.unlink()
    written = init_skillsrc.atomic_write_skillsrc(tmp_path, destination, document, "a" * 64, None)
    assert destination.read_bytes() == written
    assert sorted(path.name for path in tmp_path.iterdir()) == [".skillsrc"]


def test_m34_exclusive_creation_falls_back_when_hard_links_are_unsupported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(init_skillsrc, "discover_project", lambda _root: _ready_discovery())
    destination = tmp_path / ".skillsrc"
    document = init_skillsrc.compile_skillsrc(_ready_discovery(), {}, tmp_path)

    def unsupported(*_args, **_kwargs):
        raise OSError(95, "hard links are not supported")

    monkeypatch.setattr(init_skillsrc.os, "link", unsupported)
    written = init_skillsrc.atomic_write_skillsrc(tmp_path, destination, document, "a" * 64, None)
    assert destination.read_bytes() == written

    with pytest.raises(init_skillsrc.InitError) as caught:
        init_skillsrc.atomic_write_skillsrc(tmp_path, tmp_path / ".skillsrc", document, "a" * 64, None, before_replace=lambda: None)
    assert caught.value.code == "destination_changed"
    assert destination.read_bytes() == written


def test_m34_unreachable_merge_and_v2_migration_code_is_removed() -> None:
    for name in ("reconcile_skillsrc", "_merge_additive", "_v2_matches_detected", "_v2_unsupported_fields", "Conflict"):
        assert not hasattr(init_skillsrc, name), name


# --------------------------------------------------------------------------- M37


def test_m37_java_import_resolution_indexes_once_and_applies_the_secret_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "project"
    java = project / "src" / "main" / "java" / "com" / "acme"
    target = _write(
        java / "web" / "OrderController.java",
        "package com.acme.web;\nimport com.acme.core.Order;\nimport com.acme.core.Pricing;\nimport com.acme.core.Keys;\nimport java.util.List;\nclass OrderController {}\n",
    )
    _write(java / "core" / "Order.java", "package com.acme.core;\npublic class Order {}\n")
    _write(java / "core" / "Pricing.java", "package com.acme.core;\npublic class Pricing {}\n")
    _write(java / "core" / "Keys.java", f'package com.acme.core;\npublic class Keys {{ String aws = "{AWS_KEY}"; }}\n')
    walks: list[Path] = []
    original = scan_project.confined_files

    def counting(root):
        walks.append(Path(root))
        return original(root)

    monkeypatch.setattr(scan_project, "confined_files", counting)

    resolved = scan_project._java_imported_sources(str(project), str(target))

    assert resolved == ["src/main/java/com/acme/core/Order.java", "src/main/java/com/acme/core/Pricing.java"]
    assert len(walks) == 1
    companions = scan_project.extract_companions(str(project), str(target), "java")
    assert "src/main/java/com/acme/core/Keys.java" not in companions
    assert AWS_KEY not in scan_project.render_source_block(str(project), companions)
