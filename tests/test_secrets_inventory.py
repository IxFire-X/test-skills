"""Package A, the secrets of the inventory (review 9b2e8a65, S1-S7): what a secret rule leaves out and what it keeps, the walk of the
tree stamp past a junction, «**/x» at the root, the runs of the package out of discovery, and overlapping context roots refused."""
from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_stage_steps import _junction
from tests.test_two_repos import _declare, _stand
from tools.project_inventory import InventoryError, _secret_rule, _tree_stamp, skillsrc_context
from tools.scan_rules import include_mode
from tools.stack_catalog import confined_files


@pytest.mark.parametrize(("rel", "data", "rule"), [
    ("config/app.json", b'{"api_key": "abcd1234efgh5678"}', "token-assignment-v1"),  # S1: a quoted key in settings
    ("config/app.yaml", b'"access_token": "zz9988xxyyqq"\n', "token-assignment-v1"),
    ("config/settings.json", b'{"token": "abcdef123456"}', "token-assignment-v1"),
    ("docs/req.md", b'"password": "Secret-123"', None),  # an example in a document: the task document stays
    ("api/login.bru", b'"password": "password123"', None),
    ("conf/app.ini", b"password = hunter2\n", "password-assignment-v1"),  # S2: any length, more settings formats
    ("conf/app.toml", b'password = "x!y"\n', "password-assignment-v1"),
    ("conf/db.cfg", b"secret=abc\n", "password-assignment-v1"),
    ("deploy.sh", b"PASSWORD=hunter2\n", "password-assignment-v1"),
    ("deploy2.sh", b'PASSWORD="$1"\n', None),  # a reference of the script, no secret
    ("env.sh", b"export DB_PASSWORD=hunter2\n", "password-assignment-v1"),  # review 7d38d7ce: the usual form of a script
    ("env2.sh", b'export PASSWORD="$1"\n', None),
    ("env3.sh", b"DB_PASSWORD=${DB_PASSWORD:=s3cr3t!}\n", "password-assignment-v1"),  # review 6778d0da: a default is a value
    ("env4.sh", b'export PASSWORD="${PASSWORD:-secret}"\n', "password-assignment-v1"),
    ("env5.sh", b"PASSWORD=${PASSWORD}\n", None),
    ("env6.sh", b"PASSWORD=${PASSWORD:-}\n", None),
    ("app/application.yml", b'openapi:\n  title: x\n"api_key": "abcd1234efgh"\n', "token-assignment-v1"),  # a block «openapi:» is no spec
    ("api/openapi.yaml", b'openapi: 3.0.1\n"api_key": "abcd1234efgh"\n', None),
    ("api/openapi.json", b'{\n  "openapi": "3.0.0",\n  "example": {"api_key": "abcd1234efgh"}}', None),  # a spec is a document
    ("notes.txt", b"password: hunter2\n", None),  # .txt is a document format
    ("secrets.json", b"{}", "secret-filename-v1"),  # S3: the plural
    ("passwords.txt", b"x", "secret-filename-v1"),
    ("api_keys.yaml", b"x: 1", "secret-filename-v1"),
    ("tokens.json", b"{}", None),  # design tokens
    ("prod.env", b"A=1", "filename-dotenv-v1"),
    ("secrets.env", b"A=1", "filename-dotenv-v1"),
    ("putty.ppk", b"x", "private-extension-v1"),
    ("key.asc", b"-----BEGIN PGP PRIVATE KEY BLOCK-----\nx", "private-key-pem-v1"),
])
def test_a_secret_rule_leaves_out_its_file_and_keeps_the_others(rel: str, data: bytes, rule: str | None) -> None:
    found = _secret_rule(Path(rel), data, rel)
    assert (found and found[0]) == rule


def test_the_tree_stamp_does_not_walk_into_a_junction(tmp_path: Path) -> None:
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "far.txt").write_text("x", encoding="utf-8")
    (tmp_path / "project").mkdir()
    _junction(tmp_path / "project" / "link", tmp_path / "outside")
    assert [row[0] for row in _tree_stamp(tmp_path / "project", ())] == []


def test_a_glob_from_any_depth_matches_at_the_root_too() -> None:
    assert include_mode("secrets-notes.md", ["**/secrets-notes.md"]) == include_mode("docs/secrets-notes.md", ["**/secrets-notes.md"]) == "full"


def test_discovery_does_not_read_the_runs_of_the_package(tmp_path: Path) -> None:
    (tmp_path / ".pilot-runs" / "run").mkdir(parents=True)
    (tmp_path / ".pilot-runs" / "run" / "report.md").write_text("# report", encoding="utf-8")
    (tmp_path / "req.md").write_text("# req", encoding="utf-8")
    assert [path.name for path in confined_files(tmp_path)] == ["req.md"]


def test_overlapping_context_roots_are_refused_with_the_key_to_fix(tmp_path: Path) -> None:
    root = _stand(tmp_path)
    (root / "app" / "src").mkdir(exist_ok=True)
    _declare(root, ["app", "app/src"])
    with pytest.raises(InventoryError) as refused:
        skillsrc_context(root, root / "autotests")
    assert str(refused.value) == "CONTEXT_ROOT_INVALID: app/src: пересекается с другим корнем paths.context — оставьте в .skillsrc у модуля один из них"


def test_an_init_answer_with_overlapping_context_roots_names_both(tmp_path: Path) -> None:
    from tools.discover_project import discover_project
    from tools.init_skillsrc import InitError, _tests_only_context

    root = _stand(tmp_path)
    answers = {"module:autotests:service_url_env": "APP_URL", "module:autotests:paths.context": "app,app/src"}
    with pytest.raises(InitError) as refused:
        _tests_only_context(root, discover_project(root), answers, set())
    assert str(refused.value) == "app/src: пересекается с app — назовите один из них"  # approved 27.11


def test_the_init_cli_prints_the_overlap_and_its_fix(tmp_path: Path) -> None:
    import json
    import subprocess
    import sys

    from tests.test_two_repos import _past_the_stand

    root = _stand(tmp_path)
    answers, questions = _past_the_stand(root)
    answers.update({key: question["options"][0]["id"] for key, question in questions.items()})
    answers[next(key for key in questions if key.endswith(":paths.context"))] = "app,app/src"
    (root / "answers.json").write_text(json.dumps(answers), encoding="utf-8")
    ran = subprocess.run([sys.executable, "-m", "tools.init_skillsrc", "--project", str(root), "--answers", str(root / "answers.json"), "--write"],
                         cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, encoding="utf-8")
    output = json.loads(ran.stdout)
    assert (output["message"], output["fix"]) == ("app/src: пересекается с app — назовите один из них", "ответ на вопрос о коде приложения — путь папки от корня проекта вне модуля (несколько — через запятую), без пересечений между ними")  # approved 27.11, п. 10 and п. 12
