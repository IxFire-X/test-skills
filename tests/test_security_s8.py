"""S8 of the security fixes: credential directories, keys and .env files never reach the model context.

The inventory excludes them (SECRET_SUSPECTED) wherever they sit in the project, a credential line in an ordinary file
excludes the file, and a document keeps its structure with the line masked.  The driver's context receipts and the
files of the model tasks carry none of them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_review_fixes_driver import SavedModel, _drive, _project, _run_root, _start
from tests.test_review_fixes_specs import _excluded, _paths, _write
from tools import pipeline_driver as driver
from tools.project_inventory import build_inventory, content_secret_rule

AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
CREDENTIAL_FILES = [
    ".ssh/config", ".ssh/known_hosts", ".ssh/id_rsa", ".aws/credentials", ".aws/config", ".kube/config", ".gnupg/pubring.kbx",
    ".docker/config.json", ".azure/accessTokens.json", ".config/gcloud/credentials.db", ".config/gh/hosts.yml",
    ".netrc", "_netrc", ".pgpass", ".git-credentials", ".npmrc", ".pypirc", ".env", ".env.production", "deploy/server.pem",
    "module/.aws/credentials",
]


def _covered(inventory: dict, path: str) -> dict | None:
    excluded = _excluded(inventory)
    for prefix in [path, *("/".join(path.split("/")[:index]) for index in range(1, path.count("/") + 1))]:
        if prefix in excluded:
            return excluded[prefix]
    return None


def _credentials(project: Path) -> None:
    for name in CREDENTIAL_FILES:
        _write(project / name, f"[default]\naws_secret_access_key = {AWS_SECRET}\n")


def test_credential_directories_and_files_are_excluded_wherever_they_are(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _credentials(project)
    _write(project / "app.py", "VALUE = 1\n")
    _write(project / ".config" / "tool.json", "{}\n")  # an ordinary .config file stays
    inventory = build_inventory(project, project)
    assert _paths(inventory) == {"app.py", ".config/tool.json"}
    for name in CREDENTIAL_FILES:
        row = _covered(inventory, name)
        assert row is not None and row["reason_code"] == "SECRET_SUSPECTED", name


def test_an_unquoted_aws_secret_excludes_the_file_and_is_masked_in_a_document(tmp_path: Path) -> None:
    assert content_secret_rule(f"aws_secret_access_key = {AWS_SECRET}\n".encode()) is not None
    assert content_secret_rule(f"AWS_SECRET_ACCESS_KEY={AWS_SECRET}\n".encode()) is not None
    assert content_secret_rule(b"aws_secret_access_key = os.environ['X']\n") is None  # a reference, not a value
    project = tmp_path / "project"
    _write(project / "settings.ini", f"[s3]\naws_secret_access_key = {AWS_SECRET}\n")
    _write(project / "docs" / "setup.md", f"# Настройка\naws_secret_access_key = {AWS_SECRET}\nдалее текст\n")
    inventory = build_inventory(project, project)
    assert "settings.ini" not in _paths(inventory) and _excluded(inventory)["settings.ini"]["reason_code"] == "SECRET_SUSPECTED"
    document = next(item for item in inventory["files"] if item["project_path"] == "docs/setup.md")
    assert document.get("redactions")


def test_a_keystore_stays_a_build_input_and_never_reaches_the_context(tmp_path: Path) -> None:
    project = tmp_path / "project"
    keystore = project / "src" / "test" / "resources" / "keystore.jks"
    keystore.parent.mkdir(parents=True)
    keystore.write_bytes(b"\xfe\xed\xfe\xed\x00\x00\x00\x02" + bytes(range(256)))
    _write(project / "app.py", "VALUE = 1\n")
    inventory = build_inventory(project, project)
    # Binary: never a context file; not a secret exclusion, so the execution baseline does not refuse the run.
    row = _covered(inventory, "src/test/resources/keystore.jks")
    assert row is not None and row["reason_code"] == "BINARY_OR_GENERATED"
    from tools.project_inventory import _secret_exclusion_is_execution_significant

    assert not _secret_exclusion_is_execution_significant(row, ".")
    assert not any(_secret_exclusion_is_execution_significant(item, ".") for item in _credential_inventory(tmp_path)["exclusions"])


def _credential_inventory(tmp_path: Path) -> dict:
    project = tmp_path / "credentials"
    _credentials(project)
    _write(project / "app.py", "VALUE = 1\n")
    return build_inventory(project, project)


def test_the_model_tasks_never_carry_credential_files(tmp_path: Path) -> None:
    project = _project(tmp_path, local=False)
    _credentials(project)
    model = SavedModel("cases-only-v1")
    first = _start(project, "cases-only-v1", confirm_context="yes")
    run_root = _run_root(project, first)
    selected = {item["project_path"] for receipt in driver._context_receipts(run_root, first["attempt_id"]) for item in receipt["files"]}
    assert selected and not selected & set(CREDENTIAL_FILES) and not any(path.startswith((".ssh/", ".aws/", ".kube/", ".config/gh/")) for path in selected)
    # The person confirming the model input sees what stayed out and why.
    listing = Path(first["listing_path"]).read_text(encoding="utf-8")
    assert "исключено\tcredential-directory-v1\t.aws" in listing and "исключено\tcredential-file-v1\t.netrc" in listing
    assert AWS_SECRET not in listing
    first = driver.submit(project, run_root, first["task_id"], answer=first["confirmation_digest"])
    seen: list[dict] = []
    done = _drive(project, first, model, until=lambda task: seen.append(task) or False)
    assert done["action"] == "done"
    for task in seen:
        for path in task.get("inputs", []):
            assert AWS_SECRET not in Path(path).read_text(encoding="utf-8", errors="replace"), (task.get("stage"), path)
