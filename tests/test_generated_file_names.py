"""S1c (security review note 5): a generated file is a test by its name, never a build or collection hook."""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from tools.automation_validation import is_generated_test_path, validate_automation_artifact

ROOT = Path(__file__).resolve().parents[1]
RECORDS_G = json.loads(gzip.open(ROOT / "tests" / "fixtures" / "live-g-triage" / "triage-g.json.gz", "rt", encoding="utf-8").read())
LIVE_STEP5 = ROOT / "tests" / "fixtures" / "live-step5-20261006"


def _generated_files(node, found: list[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        if {"path", "language", "content"} <= set(node) and isinstance(node["path"], str):
            found.append((node["language"], node["path"]))
        for value in node.values():
            _generated_files(value, found)
    elif isinstance(node, list):
        for value in node:
            _generated_files(value, found)
    elif isinstance(node, str) and node[:1] in "{[" and '"generated_files"' in node:
        try:
            _generated_files(json.loads(node), found)
        except ValueError:
            pass


def test_the_records_of_run_g_pass_with_all_81_methods() -> None:
    automation, document = RECORDS_G["automation"], RECORDS_G["document"]
    assert len(automation["artifacts"]["generated_symbols"]) == 81
    assert validate_automation_artifact(automation, document) == []


def test_every_generated_file_of_the_live_step5_records_is_a_test() -> None:
    found: list[tuple[str, str]] = []
    for path in sorted(LIVE_STEP5.rglob("*.json*")):
        raw = gzip.decompress(path.read_bytes()) if path.name.endswith(".gz") else path.read_bytes()
        try:
            _generated_files(json.loads(raw.decode("utf-8")), found)
        except ValueError:
            continue
    assert found, "the live-step5 records hold generated files"
    assert [row for row in found if not is_generated_test_path(row[1], row[0])] == []


@pytest.mark.parametrize("language,path", [
    ("java", "src/test/java/a/b/OwnerLifecycleFormTests.java"),
    ("java", "src/test/java/a/StudentControllerPipelineTest.java"),
    ("java", "src/test/java/a/OwnerApiIT.java"),
    ("java", "src/test/java/$Odd_Test.java"),
    ("python", "tests/test_products.py"),
    ("python", "tests/api/products_test.py"),
    ("python", "tests/TEST_PRODUCTS.py"),
])
def test_test_names_are_allowed(language: str, path: str) -> None:
    assert is_generated_test_path(path, language)


@pytest.mark.parametrize("language,path", [
    ("python", "tests/conftest.py"),
    ("python", "tests/__init__.py"),
    ("python", "tests/CONFTEST.py"),
    ("python", "pytest.ini"),
    ("python", "setup.cfg"),
    ("python", "setup.py"),
    ("python", "tests/helpers.py"),
    ("python", "tests/test_products.pyc"),
    ("python", "tests/test_products.py.bak"),
    ("python", "tests/test_.py"),
    ("python", "tests/test-products.py"),
    ("java", "pom.xml"),
    ("java", "build.gradle"),
    ("java", "build.gradle.kts"),
    ("java", "settings.gradle"),
    ("java", "src/test/java/a/Helper.java"),
    ("java", "src/test/java/a/OwnerTests.kt"),
    ("java", "src/test/java/a/OwnerTest.java.txt"),
    ("java", "src/test/java/a/Test.java"),
    ("java", "src/test/java/a/ownertest.java"),
    ("java", "src/test/java/a/test_products.py"),
    ("python", "src/test/java/a/OwnerTest.java"),
])
def test_other_names_are_refused(language: str, path: str) -> None:
    assert not is_generated_test_path(path, language)


@pytest.mark.parametrize("name", ["conftest.py", "pom.xml", "Helper.java", "build.gradle"])
def test_a_generated_file_that_is_not_a_test_is_a_diagnostic(name: str) -> None:
    automation, document = copy.deepcopy(RECORDS_G["automation"]), RECORDS_G["document"]
    row = automation["artifacts"]["generated_files"][0]
    row["path"] = row["path"].rsplit("/", 1)[0] + "/" + name
    row["content_digest"] = "sha256:" + hashlib.sha256(row["content"].encode("utf-8")).hexdigest()
    codes = {item["code"] for item in validate_automation_artifact(automation, document)}
    assert codes == {"AUTOMATION_PATH_NOT_A_TEST"}
