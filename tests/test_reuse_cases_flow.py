"""V5b-1 end to end, by the commands of a delivery: a run whose cases the review accepted whole, a new scan of the same project that offers the reuse, and the new run
through ``prepare --reuse-from`` — the marker, the plan, the batches, the review — to the report.  No model is called in the new run."""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from tests import test_profile_flows as flows
from tests.test_pack_profile import copy_profile


@pytest.fixture(scope="module")
def pack(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return copy_profile(tmp_path_factory.mktemp("reuse1") / "pack", "cases-only")


@pytest.fixture(scope="module")
def source(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    run = flows._front(pack, tmp_path_factory.mktemp("src"), "cases-only-v1")
    flows._cases_to_effective(pack, run)
    return run


@pytest.fixture(scope="module")
def self_source(pack: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    real = flows._skillsrc

    def with_self(project: Path, window: int | None) -> None:
        real(project, window)
        manifest = json.loads((project / ".skillsrc").read_text(encoding="utf-8"))
        (project / ".skillsrc").write_text(json.dumps({**manifest, "review": {"self": True}}, ensure_ascii=False), encoding="utf-8")

    patch = pytest.MonkeyPatch()
    patch.setattr(flows, "_skillsrc", with_self)
    try:
        run = flows._front(pack, tmp_path_factory.mktemp("self"), "cases-only-v1")
        flows._cases_to_effective(pack, run, same_session=True)
    finally:
        patch.undo()
    return run


def _scan(pack: Path, project: Path) -> dict:
    code, scanned = flows._scan(pack, "--project", str(project), "--docs", "docs/requirements.md", "--profile", "cases-only-v1", "--module", "python-pytest", "--new-run")
    assert code == 0, scanned
    return scanned


def _step(pack: Path, scanned: dict, stage: str, *extra: str) -> tuple[int, dict]:
    return flows._api(pack, "prepare", "--run-root", scanned["run_root"], "--stage", stage, *extra)


def _reuse_all(pack: Path, scanned: dict, source_id: str, *extra: str) -> list[dict]:
    """Every stage the scan offers, by its own command; each answers from the source and calls no model."""
    done = []
    for row in scanned["reuse"]["stages"]:
        code, out = _step(pack, scanned, row["stage"], "--reuse-from", source_id, *(extra if row["stage"].startswith("tc-reviewer") else ()))
        assert code == 0 and out["result"]["answered"] is True and out["result"].get("model_call") is False, (row["stage"], out)
        done.append(out["result"])
    return done


def _report(pack: Path, scanned: dict, project: Path) -> dict:
    name = Path(scanned["run_root"]).name
    for command in ("finish", "report"):
        code, out = flows._cli(pack, "tools.run_pipeline", command, "--project", str(project), "--run", name)
        assert code in (0, 1), (command, out)
    return json.loads((Path(scanned["run_root"]) / "report.json").read_text(encoding="utf-8"))


def test_the_scan_offers_the_whole_set_and_the_new_run_takes_it_without_a_model_call(pack: Path, source: dict) -> None:
    scanned = _scan(pack, source["project"])
    stages = [row["stage"] for row in scanned["reuse"]["stages"]]
    assert scanned["reuse"]["run_id"] == source["run_root"].name and stages[:2] == ["context-marker:baseline", "plan"] and stages[-1] == "tc-reviewer:canonical"
    _reuse_all(pack, scanned, source["run_root"].name)
    report = _report(pack, scanned, source["project"])
    assert report["counts"]["model_calls"] == 0 and report["counts"]["reused_calls"] == len(stages) - 1  # the plan is no call
    assert "ревью из прогона " + source["run_root"].name[:8] in report["review"] and "свежем контексте" in report["review"]


def test_the_document_of_the_new_run_is_the_one_of_the_source(pack: Path, source: dict) -> None:
    from tools.pilot_state import read_effective_canonical

    scanned = _scan(pack, source["project"])
    _reuse_all(pack, scanned, source["run_root"].name)
    effective = [dict(read_effective_canonical(Path(root), attempt)) for root, attempt in ((source["run_root"], source["attempt_id"]), (scanned["run_root"], scanned["attempt_id"]))]
    assert effective[0]["document_digest"] == effective[1]["document_digest"]


def test_a_changed_file_of_the_project_cancels_the_reuse_and_the_scan_says_so(pack: Path, source: dict) -> None:
    added = source["project"] / "src" / "added_after.py"
    added.write_text("x = 1\n", encoding="utf-8")
    try:
        scanned = _scan(pack, source["project"])
        assert scanned["reuse"]["run_id"] is None and any("inventory" in row["input"] for row in scanned["reuse"]["differences"])
        code, refused = _step(pack, scanned, "context-marker:baseline", "--reuse-from", source["run_root"].name)
        assert code == 2 and refused["code"] == "MODEL_REUSE_REFUSED" and "prepare без --reuse-from" in refused["fix"], refused
    finally:
        added.unlink()


def test_a_damaged_answer_of_the_source_is_refused_with_its_name(pack: Path, source: dict) -> None:
    scanned = _scan(pack, source["project"])
    artifacts = Path(source["run_root"]) / "model-stage-artifacts"
    target = next(path for path in sorted(artifacts.rglob("*.json")) if b"test_cases" in path.read_bytes())
    data = target.read_bytes()
    target.write_bytes(data.replace(b"test_cases", b"test_casex", 1))
    try:
        code, refused = _step(pack, scanned, "context-marker:baseline", "--reuse-from", source["run_root"].name)
        assert code == 2 and refused["code"] == "REUSE_SOURCE_INVALID" and "model-stage-artifacts" in refused["message"], refused
    finally:
        target.write_bytes(data)


# ---- the date of the header --------------------------------------------------------------------------------------------------------

def _tomorrow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """The clock of every command that follows says it is the next day (a ``sitecustomize`` of the test, on the path of the subprocesses only)."""
    day = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    clock = tmp_path / "clock"
    clock.mkdir()
    (clock / "sitecustomize.py").write_text(
        "import datetime as d, os\n_y, _m, _d = (int(x) for x in os.environ['FAKE_DAY'].split('-'))\n_real = d.datetime\n\n"
        "class _Fake(_real):\n    @classmethod\n    def now(cls, tz=None):\n        return _real.now(tz).replace(year=_y, month=_m, day=_d)\n\nd.datetime = _Fake\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(clock))
    monkeypatch.setenv("FAKE_DAY", day)
    return day


def _header_date(root: str, attempt: str) -> str:
    from tools.pilot_state import read_attempt_receipt

    return read_attempt_receipt(Path(root), attempt, "generator-header", "ARTIFACT_READ_BACK")["record"]["header"]["metadata"]["date"]


def test_after_midnight_the_header_keeps_the_date_of_the_source_and_the_reuse_still_works(pack: Path, source: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import read_attempt_receipt

    day = _tomorrow(tmp_path, monkeypatch)
    scanned = _scan(pack, source["project"])
    _reuse_all(pack, scanned, source["run_root"].name)
    wanted = read_attempt_receipt(source["run_root"], source["attempt_id"], "generator-header", "ARTIFACT_READ_BACK")["record"]["header"]
    new = _header_date(scanned["run_root"], scanned["attempt_id"])
    assert new == wanted["metadata"]["date"] != day
    report = _report(pack, scanned, source["project"])
    assert "из прошлого прогона" in report["header_date_note"] and source["run_root"].name[:8] in report["header_date_note"] and wanted["metadata"]["date"] in report["header_date_note"]


def test_a_date_from_the_source_leaves_its_trace_without_the_id_when_the_batches_were_made_the_ordinary_way(pack: Path, source: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tools.pilot_state import read_model_stage_artifact

    _tomorrow(tmp_path, monkeypatch)
    scanned = _scan(pack, source["project"])
    root, attempt = Path(scanned["run_root"]), scanned["attempt_id"]
    code, prepared = _step(pack, scanned, "context-marker:baseline")  # the marker the ordinary way: from the file of the source's answer
    assert code == 0, prepared
    marker = read_model_stage_artifact(source["run_root"], source["attempt_id"], "context-marker:baseline", source["marker_digest"])["artifact"]
    Path(prepared["result"]["answer_slot"]).write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    assert flows._api(pack, "submit", "--run-root", str(root), "--stage", "context-marker:baseline")[0] == 0
    assert _step(pack, scanned, "plan", "--reuse-from", source["run_root"].name)[0] == 0
    flows._cases_to_effective(pack, {**source, "run_root": root, "attempt_id": attempt, "tmp": root / "tmp"})  # the fragments of the source fit the plan taken from it
    report = _report(pack, scanned, source["project"])
    assert "из прошлого прогона" in report["header_date_note"] and source["run_root"].name[:8] not in report["header_date_note"]


# ---- the review of a SELF source -------------------------------------------------------------------------------------------------------

def test_a_self_source_stays_self_to_the_report(pack: Path, self_source: dict) -> None:
    scanned = _scan(pack, self_source["project"])  # the .skillsrc of the project admits SELF: the consent of the new run
    review = next(row for row in scanned["reuse"]["stages"] if row["stage"] == "tc-reviewer:canonical")
    assert review["review"] == "SELF"
    _reuse_all(pack, scanned, self_source["run_root"].name)
    report = _report(pack, scanned, self_source["project"])
    assert "SELF" in report["review"] and "ревью из прогона " + self_source["run_root"].name[:8] in report["review"]
    assert report["reviewer_isolation_state"] != "verified"
