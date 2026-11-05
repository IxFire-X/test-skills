"""P6b (V3): the build of mode B travels between machines, and the level of the autotests is a fact of ``.skillsrc``.

- ``init_skillsrc`` writes ``executable: mvn`` (the name, found on PATH), not the absolute path of this machine; an old absolute path still works
  and a repeated init does not propose a replacement because of its form;
- ``test.level: in-process | stand`` — a key of the ``test`` block; no key = the old derivation (``paths.context`` -> stand) and the warning
  TEST_LEVEL_DERIVED with the fix; a declared level that contradicts the other keys is the refusal ``test_level_conflict``;
- the report says «Уровень автотестов: …» and where the level comes from (declared or derived), checked against the ``.skillsrc`` of the scan.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_first_run import _fake_mvn, _java_project
from tests.test_f5_first_run import _project, _ready, _row, _scan
from tests.test_phase2_scan import _skillsrc
from tools.init_skillsrc import _payload, ensure_skillsrc
from tools import skillsrc_manifest
from tools.skillsrc_manifest import SkillsrcError, parse_skillsrc_bytes


def _document(**module_extra) -> dict:
    document = json.loads(_skillsrc())
    module = document["modules"][0]
    for key, value in module_extra.items():
        if key == "test":
            module["test"].update(value)
        elif key == "paths":
            module["paths"].update(value)
        else:
            module[key] = value
    return document


# --- executable: the name, not the path of this machine -------------------------------------------------------------

def test_init_writes_the_name_mvn_and_the_level_of_a_module_without_a_stand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.skillsrc_manifest import load_skillsrc

    project = _java_project(tmp_path)
    _fake_mvn(tmp_path, monkeypatch)
    assert ensure_skillsrc(project, {}, True)["status"] == "created"
    [module] = load_skillsrc(project / ".skillsrc")["modules"]
    assert module["test"] == {"framework": "junit5", "adapter_id": "maven:selected-symbols-v1", "executable": "mvn",
                              "build_profile": "default", "adapter_parameters": {}, "level": "in-process"}


def test_a_repeated_init_keeps_an_old_absolute_path_and_a_missing_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.skillsrc_manifest import load_skillsrc

    project = _java_project(tmp_path)
    launcher = _fake_mvn(tmp_path, monkeypatch)
    ensure_skillsrc(project, {}, True)
    document = load_skillsrc(project / ".skillsrc")
    del document["modules"][0]["test"]["level"]
    document["modules"][0]["test"]["executable"] = str(launcher.resolve())  # the file as an older init wrote it
    (project / ".skillsrc").write_bytes(_payload(document))
    receipt = ensure_skillsrc(project, {}, False)
    assert receipt["status"] == "unchanged", receipt
    assert load_skillsrc(project / ".skillsrc")["modules"][0]["test"]["executable"] == str(launcher.resolve())


def test_a_repeated_init_keeps_the_declared_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.skillsrc_manifest import load_skillsrc

    project = _java_project(tmp_path)
    _fake_mvn(tmp_path, monkeypatch)
    ensure_skillsrc(project, {}, True)
    document = load_skillsrc(project / ".skillsrc")
    document["modules"][0]["test"]["level"] = "stand"
    document["modules"][0]["service_url_env"] = "STAND_URL"
    (project / ".skillsrc").write_bytes(_payload(document))
    assert ensure_skillsrc(project, {}, False)["status"] == "unchanged"


# --- the key and its facts ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("level", ["in-process", "stand"])
def test_the_schema_takes_the_level_in_every_variant_of_the_test_block(level: str) -> None:
    extra = {"service_url_env": "STAND_URL"} if level == "stand" else {}
    pytest_doc = _document(test={"level": level}, **extra)
    assert parse_skillsrc_bytes(_payload(pytest_doc))["modules"][0]["test"]["level"] == level
    for adapter, runtime in (("maven:selected-symbols-v1", {"executable": "mvn"}),
                             ("maven-wrapper:selected-symbols-v1", {"wrapper": "mvnw"}),
                             ("gradle-wrapper:selected-symbols-v1", {"wrapper": "gradlew"})):
        document = _document(**extra)
        document["modules"][0]["stack"] = {"language": "java", "build_tool": "gradle" if "gradle" in adapter else "maven"}
        document["modules"][0]["test"] = {"framework": "junit5", "adapter_id": adapter, "build_profile": "default", "adapter_parameters": {},
                                          "level": level, **runtime}
        assert parse_skillsrc_bytes(_payload(document))["modules"][0]["test"]["level"] == level, adapter


def test_the_schema_refuses_another_level() -> None:
    with pytest.raises(SkillsrcError) as caught:
        parse_skillsrc_bytes(_payload(_document(test={"level": "unit"})))
    assert caught.value.code == "schema_invalid" and caught.value.fix


@pytest.mark.parametrize(("extra", "fragment"), [
    ({"test": {"level": "stand"}}, "service_url_env"),
    ({"test": {"level": "in-process"}, "service_url_env": "STAND_URL"}, "service_url_env"),
    ({"test": {"level": "in-process"}, "paths": {"context": ["services/other/src"]}}, "paths.context"),
])
def test_a_declared_level_that_contradicts_the_other_keys_is_refused_with_a_fix(extra: dict, fragment: str) -> None:
    with pytest.raises(SkillsrcError) as caught:
        parse_skillsrc_bytes(_payload(_document(**extra)))
    error = caught.value
    assert error.code == "test_level_conflict" and "nested" in str(error) and fragment in str(error) + error.fix, (str(error), error.fix)
    assert "test.level" in error.fix


def test_a_declared_level_that_agrees_and_a_derived_one_never_conflict() -> None:
    assert parse_skillsrc_bytes(_payload(_document(test={"level": "stand"}, service_url_env="STAND_URL", paths={"context": ["services/other/src"]})))
    assert parse_skillsrc_bytes(_payload(_document(paths={"context": ["services/other/src"]})))  # derived stand without the env: lowered at scan, not refused
    assert parse_skillsrc_bytes(_payload(_document()))


def test_module_test_level_says_the_value_and_where_it_comes_from() -> None:
    module_test_level = skillsrc_manifest.module_test_level
    module = _document()["modules"][0]
    assert module_test_level(module) == ("in-process", "derived")
    module["paths"]["context"] = ["x/src"]
    assert module_test_level(module) == ("stand", "derived")
    module["test"]["level"] = "stand"
    assert module_test_level(module) == ("stand", "declared")
    module.pop("test")
    assert module_test_level(module) is None  # a module without a test block (documents) has no level


# --- the warning in scan and ready ----------------------------------------------------------------------------------

def test_scan_and_ready_warn_about_a_derived_level_with_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module_id, doc = _project(tmp_path, "svc")
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
    [warning] = [item for item in payload["warnings"] if str(item).startswith("TEST_LEVEL_DERIVED")]
    assert "in-process" in warning and "test.level" in warning, warning
    row = _row(_ready(project, [doc], module_id), "test:level")
    assert row["level"] == "WARN" and "test.level: in-process" in row["fix"], row


def test_a_declared_level_is_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module_id, doc = _project(tmp_path, "svc")
    document = json.loads((project / ".skillsrc").read_bytes())
    document["modules"][0]["test"]["level"] = "in-process"
    (project / ".skillsrc").write_bytes(_payload(document))
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
    assert not [item for item in payload["warnings"] if str(item).startswith("TEST_LEVEL_DERIVED")]
    assert _row(_ready(project, [doc], module_id), "test:level")["level"] == "OK"


# --- the report ------------------------------------------------------------------------------------------------------

def _report(project: Path, monkeypatch: pytest.MonkeyPatch, module_id: str, doc: str):
    from tools.run_report import build_report, render_markdown

    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
    report, _rows = build_report(project, payload["run_id"])
    return report, render_markdown(report)


def test_the_report_says_a_declared_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module_id, doc = _project(tmp_path, "svc")
    document = json.loads((project / ".skillsrc").read_bytes())
    document["modules"][0]["test"]["level"] = "in-process"
    (project / ".skillsrc").write_bytes(_payload(document))
    report, markdown = _report(project, monkeypatch, module_id, doc)
    assert report["test_level"] == {"value": "in-process", "source": "declared", "module": module_id}, report["test_level"]
    assert "Уровень автотестов: in-process (задан в .skillsrc)" in markdown


def test_the_report_says_a_derived_level_and_what_to_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project, module_id, doc = _project(tmp_path, "svc")
    report, markdown = _report(project, monkeypatch, module_id, doc)
    assert report["test_level"]["source"] == "derived" and report["test_level"]["value"] == "in-process"
    assert "Уровень автотестов: in-process (выведен" in markdown and "test.level" in markdown


def test_the_report_does_not_confirm_a_level_of_a_changed_skillsrc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.run_report import build_report, render_markdown

    project, module_id, doc = _project(tmp_path, "svc")
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
    document = json.loads((project / ".skillsrc").read_bytes())
    document["modules"][0]["test"]["level"] = "in-process"  # the file changed after the scan
    (project / ".skillsrc").write_bytes(_payload(document))
    report, _rows = build_report(project, payload["run_id"])
    assert report["test_level"]["value"] is None and report["test_level"]["source"] == "unconfirmed", report["test_level"]
    assert "Уровень автотестов: не подтверждён" in render_markdown(report)


def test_the_delivery_without_execution_speaks_of_no_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest import mock

    from tools import execution_gate
    from tools.run_report import build_report, render_markdown

    project, module_id, doc = _project(tmp_path, "svc")
    with mock.patch.object(execution_gate, "execution_available", lambda: False):  # (_scan undoes monkeypatch, so a context here)
        payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
        assert not [item for item in payload.get("warnings", []) if str(item).startswith("TEST_LEVEL_DERIVED")]
        assert all(row["id"] != "test:level" for row in _ready(project, [doc], module_id)["checks"])
        report, _rows = build_report(project, payload["run_id"])
    assert report["test_level"] is None and "Уровень автотестов" not in render_markdown(report)


def test_an_inventory_planted_in_the_run_folder_does_not_name_a_level_of_a_changed_skillsrc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The source of the level is the journal-bound baseline, not a file of the run folder an agent could write."""
    from tools.run_report import build_report, render_markdown

    project, module_id, doc = _project(tmp_path, "svc")
    payload = _scan(project, monkeypatch, "--docs", doc, "--module", module_id, expect=0)
    run_root = Path(payload["run_root"])
    [inventory_path] = (run_root / "inventories").glob("*.json")
    document = json.loads((project / ".skillsrc").read_bytes())
    document["modules"][0]["test"]["level"] = "in-process"
    (project / ".skillsrc").write_bytes(_payload(document))  # changed after the scan
    import hashlib

    forged = json.loads(inventory_path.read_text(encoding="utf-8"))
    for item in forged["files"]:
        if item["project_path"] == ".skillsrc":
            item["content_digest"] = "sha256:" + hashlib.sha256((project / ".skillsrc").read_bytes()).hexdigest()
    (run_root / "inventories" / "zzz-forged.json").write_text(json.dumps(forged), encoding="utf-8")
    report, _rows = build_report(project, payload["run_id"])
    assert report["test_level"]["source"] == "unconfirmed" and report["test_level"]["value"] is None, report["test_level"]
    assert "Уровень автотестов: не подтверждён" in render_markdown(report)
