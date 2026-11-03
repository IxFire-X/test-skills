"""FaT-P3: ``.skillsrc limits.generator_context`` — ``all`` (default, as before) or ``own``.  ``own`` (own group of the batch plus an
index of the rest) waits for the measurement on the PQ1 line and a definition of «own group»: it is refused with its own code."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import api


def _project(tmp_path: Path, limits: dict | None) -> Path:
    from tests.test_phase2_scan import _skillsrc
    from tools.init_skillsrc import _payload
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    project = tmp_path / "project"
    project.mkdir(parents=True)
    manifest = parse_skillsrc_bytes(_skillsrc())
    if limits is not None:
        manifest["limits"] = limits
    (project / ".skillsrc").write_bytes(_payload(manifest))
    return project


def test_the_default_is_all_and_the_key_is_read(tmp_path: Path) -> None:
    from tools.step_size import generator_context

    assert generator_context(tmp_path) == "all"  # no .skillsrc
    assert generator_context(_project(tmp_path / "a", None)) == "all"
    assert generator_context(_project(tmp_path / "b", {"generator_context": "all"})) == "all"
    assert generator_context(_project(tmp_path / "c", {"generator_context": "own"})) == "own"


def test_the_schema_knows_only_all_and_own(tmp_path: Path) -> None:
    from tools.skillsrc_manifest import SkillsrcError, load_skillsrc

    assert load_skillsrc(_project(tmp_path / "a", {"generator_context": "own"}) / ".skillsrc")["limits"]["generator_context"] == "own"
    with pytest.raises((SkillsrcError, ValueError)):
        load_skillsrc(_project(tmp_path / "b", {"generator_context": "some"}) / ".skillsrc")


def test_own_is_refused_by_plan_batches_with_a_code_and_a_fix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tools.step_size import GeneratorContextNotImplemented

    project = _project(tmp_path, {"generator_context": "own"})
    run_tmp = project / ".pilot-runs" / "run1" / "tmp"
    run_tmp.mkdir(parents=True)
    for name, value in (("req.json", []), ("grouping.json", {}), ("context.json", {})):
        (run_tmp / name).write_text(json.dumps(value), encoding="utf-8")
    code = api.main(["plan-batches", "--requirements", str(run_tmp / "req.json"), "--grouping", str(run_tmp / "grouping.json"),
                     "--project", str(project), "--input-file", str(run_tmp / "req.json"), "--context-receipt", str(run_tmp / "context.json")])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2 and payload["code"] == GeneratorContextNotImplemented.code == "GENERATOR_CONTEXT_NOT_IMPLEMENTED", payload
    assert "all" in payload["fix"] and "plan-new" in payload["fix"]
