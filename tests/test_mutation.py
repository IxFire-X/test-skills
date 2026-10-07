"""Opt-in MUTATION stage (wave 2, M): PIT argv, pinned tool, attribution, project inventory.

Recorded PIT reports (``tests/fixtures/mutation``) come from real runs of PIT 1.30.0 with
``pitest-junit5-plugin`` 1.2.3 on the step5 class of ``9340016c`` (JUnit 5.11) and on the
September Petclinic class ``b7733d39`` (JUnit 6.0.3, one failing method skipped).
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.live_step5 import LIVE, review_state
from tools import mutation

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mutation"


def _xml(name: str) -> bytes:
    return gzip.decompress((FIXTURES / name).read_bytes())


def _step5() -> tuple[dict, dict]:
    payload = review_state("9340016c", "review-snapshot-r1")["payload"]
    return payload["automation"], payload["document"]


def _petclinic() -> tuple[dict, dict]:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "review-scaling"))
    import eval_data

    snapshot = eval_data.petclinic_automation_snapshot()
    return snapshot["automation"], snapshot["document"]


# ------------------------------------------------------------------ report and attribution

def test_report_parses_the_recorded_matrix() -> None:
    rows = mutation.parse_report(_xml("step5-9340016c-mutations.xml.gz"))
    assert len(rows) == 17
    assert sum(row["status"] == "KILLED" for row in rows) == 11 and sum(row["status"] == "SURVIVED" for row in rows) == 6
    assert all(row["covering"] for row in rows)
    assert mutation.descriptor_method(
        "a.B.[engine:junit-jupiter]/[class:a.B]/[method:tcB1008CreateStudentWithoutLastNameEchoesNull()]") == ("a.B", "tcB1008CreateStudentWithoutLastNameEchoesNull")
    assert mutation.descriptor_method("x.[engine:junit-jupiter]/[class:a.B]/[nested-class:Inner]/[test-template:p(int)]/[test-template-invocation:#1]") == ("a.B$Inner", "p")
    assert mutation.descriptor_method("garbage") is None
    with pytest.raises(mutation.MutationStop) as stop:
        mutation.parse_report(b"<mutations><mutation status='WHAT'><lineNumber>1</lineNumber></mutation></mutations>")
    assert stop.value.code == "MUTATION_REPORT_INVALID"


def test_step5_mutants_are_attributed_to_cases_and_requirements() -> None:
    automation, document = _step5()
    facts = mutation.attribute(mutation.parse_report(_xml("step5-9340016c-mutations.xml.gz")), automation, document)
    assert (facts["totals"]["killed"], facts["totals"]["survived"]) == (11, 6)
    cases = {row["case_id"]: row for row in facts["cases"]}
    # Each survivor is covered by the cases whose methods ran on its line; every counted mutant belongs to a case.
    assert all(row["killed"] <= row["covered"] for row in cases.values())
    assert sum(row["covered"] for row in cases.values()) >= 17
    for group in facts["survivor_groups"]:
        assert group["case_ids"] and group["requirement_ids"] and group["source_requirement_ids"]
        case_requirements = {req for case in document["test_cases"] if case["case_id"] in group["case_ids"] for req in case["requirement_ids"]}
        assert set(group["requirement_ids"]) == case_requirements
        assert all(method.startswith("net.javaguides.springboot.controller.StudentControllerPipelineTest#") for method in group["covering_methods"])
    assert [group["group_id"] for group in facts["survivor_groups"]] == [f"MUT-{n:04d}" for n in range(1, len(facts["survivor_groups"]) + 1)]
    sources = {row["source_requirement_id"] for row in facts["source_requirements"]}
    assert sources and all(source.startswith("SREQ-") for source in sources)


def test_petclinic_report_counts_and_the_failed_method_is_absent() -> None:
    automation, document = _petclinic()
    mutants = mutation.parse_report(_xml("petclinic-b7733d39-mutations.xml.gz"))
    facts = mutation.attribute(mutants, automation, document)
    assert {key: facts["totals"][key] for key in ("killed", "survived", "no_coverage")} == {"killed": 93, "survived": 25, "no_coverage": 20}
    covering = {mutation.descriptor_method(item) for row in mutants for item in row["covering"]}
    assert ("org.springframework.samples.petclinic.owner.OwnerLifecycleB7733d39Tests", "case056") not in covering
    assert len(covering) == 63
    assert facts["survivor_groups"] and all(group["case_ids"] for group in facts["survivor_groups"])


def test_triage_limit_marks_only_the_first_groups() -> None:
    automation, document = _step5()
    facts = mutation.attribute(mutation.parse_report(_xml("step5-9340016c-mutations.xml.gz")), automation, document, triage_limit=1)
    assert [group["triage"] for group in facts["survivor_groups"]][:2] == [True, False]


# ------------------------------------------------------------------ pinned tool

def _fake_pins(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    jars = {"pitest-1.30.0.jar": b"pit", "pitest-junit5-plugin-1.2.3.jar": b"plugin"}
    pins = json.loads(mutation.PINS_PATH.read_text(encoding="utf-8"))
    pins["java"]["jars"] = [{"file": name, "sha256": hashlib.sha256(data).hexdigest()} for name, data in jars.items()]
    path = tmp_path / "pins.json"
    path.write_text(json.dumps(pins), encoding="utf-8")
    return path, jars


def test_pinned_jars_are_verified(tmp_path: Path) -> None:
    pins_path, jars = _fake_pins(tmp_path)
    pins = mutation.load_pins(pins_path)
    repo = tmp_path / "m2"
    repo.mkdir()
    entries = []
    for name, data in {**jars, "junit-platform-launcher-6.0.3.jar": b"launcher"}.items():
        (repo / name).write_bytes(data)
        entries.append(str(repo / name))
    rows = mutation.verify_tool_jars(entries, pins)
    assert [row["pinned"] for row in rows] == [True, True, False]
    (repo / "pitest-1.30.0.jar").write_bytes(b"tampered")
    with pytest.raises(mutation.MutationStop) as stop:
        mutation.verify_tool_jars(entries, pins)
    assert (stop.value.status, stop.value.code) == ("NOT_RUNNABLE", "MUTATION_TOOL_DIGEST_MISMATCH")
    (repo / "pitest-1.30.0.jar").write_bytes(b"pit")
    (repo / "evil-1.0.jar").write_bytes(b"x")
    with pytest.raises(mutation.MutationStop) as stop:
        mutation.verify_tool_jars([*entries, str(repo / "evil-1.0.jar")], pins)
    assert stop.value.code == "MUTATION_TOOL_UNPINNED"
    with pytest.raises(mutation.MutationStop) as stop:
        mutation.verify_tool_jars(entries[1:], pins)
    assert stop.value.code == "MUTATION_TOOL_MISSING"


def test_the_shipped_pins_cover_the_whole_closure() -> None:
    pins = mutation.load_pins()
    names = {row["file"] for row in pins["java"]["jars"]}
    assert {"pitest-command-line-1.30.0.jar", "pitest-junit5-plugin-1.2.3.jar", "commons-text-1.14.0.jar"} <= names
    assert all(len(row["sha256"]) == 64 for row in pins["java"]["jars"])
    pom = mutation.resolver_pom(pins, "6.0.3")
    assert "<artifactId>pitest-command-line</artifactId><version>1.30.0</version>" in pom
    assert "<artifactId>junit-platform-launcher</artifactId><version>6.0.3</version>" in pom


def test_pit_argv_targets_only_generated_classes_and_skips_failing_tests(tmp_path: Path) -> None:
    pins = mutation.load_pins()
    settings = {**mutation.DEFAULTS}
    argv = mutation.pit_argv(Path("java"), ["a.jar", "b.jar"], report_dir=tmp_path / "r", classes=["p.*"], tests=["p.GenTest"], source_dirs=[tmp_path],
                             classpath_file=tmp_path / "cp.txt", settings=settings, pins=pins)
    joined = " ".join(argv)
    for flag in ("--classPathFile", "--fullMutationMatrix", "--skipFailingTests", "--outputFormats XML", "--targetTests p.GenTest", "--mutators DEFAULTS",
                 "--timestampedReports=false", "--inputEncoding UTF-8"):
        assert flag in joined
    assert "--includedTestMethods" not in argv
    argv = mutation.pit_argv(Path("java"), ["a.jar"], report_dir=tmp_path / "r", classes=["p.*"], tests=["p.GenTest"], source_dirs=[], classpath_file=tmp_path / "cp.txt",
                             settings=settings, pins=pins, included_methods=["a", "b"])
    assert argv[argv.index("--includedTestMethods") + 1] == "a,b"


def test_target_classes_come_from_capability_provenance() -> None:
    automation, document = _step5()
    classes = mutation.target_classes(mutation.DEFAULTS, document, LIVE / "project", mutation.target_tests(automation))
    assert classes == ["net.javaguides.springboot.bean.*", "net.javaguides.springboot.controller.*"]
    assert mutation.target_classes({**mutation.DEFAULTS, "target_classes": ["x.Y"]}, document, LIVE / "project", []) == ["x.Y"]


# ------------------------------------------------------------------ the stage with a fake build tool

def _report(tmp_path: Path, automation: dict, verdict: str = "PASS", failing: tuple[str, ...] = ()) -> dict:
    project = tmp_path / "project"
    (project / "target" / "classes").mkdir(parents=True)
    (project / "target" / "test-classes").mkdir(parents=True)
    (project / "src" / "main" / "java").mkdir(parents=True)
    (project / "pom.xml").write_text("<project/>", encoding="utf-8")
    evidence = [{"file_id": row["file_id"], "symbol_id": row["symbol_id"], "status": "FAILED" if row["locator"]["method_name"] in failing else "PASSED"}
                for row in automation["artifacts"]["generated_symbols"]]
    return {"verdict": verdict, "evidence_authoritative": True, "target": {"language": "java"}, "execution_evidence": evidence,
            "execution": {"adapter_id": "maven-wrapper:selected-symbols-v1", "executable_path": str(project / "mvnw.cmd"), "cwd": str(project),
                          "argv": [str(project / "mvnw.cmd"), "-Dtest=X", "-B", "-ntp", "test"], "build_profile": "default"}}


class FakeBuild:
    """Answers the build tool and PIT the way the real tools write their files."""

    def __init__(self, tmp_path: Path, pins: dict, jars: dict[str, bytes], *, xml: bytes, touch: Path | None = None) -> None:
        self.calls: list[list[str]] = []
        self.repo = tmp_path / "m2"
        self.repo.mkdir(exist_ok=True)
        self.jars, self.xml, self.touch = jars, xml, touch

    def __call__(self, argv, cwd, environment=None, *, timeout):
        self.calls.append(list(argv))
        output = next((item.split("=", 1)[1] for item in argv if item.startswith("-Dmdep.outputFile=")), None)
        if output and "-f" in argv:  # tool resolver
            paths = []
            for name, data in {**self.jars, "junit-platform-launcher-5.11.4.jar": b"l"}.items():
                (self.repo / name).write_bytes(data)
                paths.append(str(self.repo / name))
            Path(output).write_text(";".join(paths) if Path(output).drive else ":".join(paths), encoding="utf-8")
        elif output:  # project classpath
            (self.repo / "junit-platform-engine-5.11.4.jar").write_bytes(b"e")
            Path(output).write_text(str(self.repo / "junit-platform-engine-5.11.4.jar"), encoding="utf-8")
        else:  # PIT
            report = Path(argv[argv.index("--reportDir") + 1])
            report.mkdir(parents=True, exist_ok=True)
            (report / "mutations.xml").write_bytes(self.xml)
            if self.touch is not None:
                self.touch.write_text("changed", encoding="utf-8")
        return SimpleNamespace(exit_code=0, stdout="", stderr="", kind="EXIT")


def _inputs(tmp_path: Path, automation: dict, document: dict, report: dict, fake: FakeBuild, pins_path: Path) -> mutation.StageInputs:
    java = tmp_path / "jdk" / "bin" / ("java.exe" if mutation.os.name == "nt" else "java")
    java.parent.mkdir(parents=True, exist_ok=True)
    java.write_bytes(b"")
    return mutation.StageInputs(project=tmp_path / "project", workdir=tmp_path / "run" / "mutation", report=report, automation=automation, document=document,
                                settings=dict(mutation.DEFAULTS), run=fake, environment={"JAVA_HOME": str(tmp_path / "jdk")}, pins_path=pins_path)


def test_the_stage_measures_and_proves_the_project_unchanged(tmp_path: Path) -> None:
    automation, document = _step5()
    pins_path, jars = _fake_pins(tmp_path)
    fake = FakeBuild(tmp_path, mutation.load_pins(pins_path), jars, xml=_xml("step5-9340016c-mutations.xml.gz"))
    facts = mutation.measure(_inputs(tmp_path, automation, document, _report(tmp_path, automation), fake, pins_path))
    assert facts["status"] == "MEASURED" and facts["reason_code"] is None
    assert facts["project_inventory"]["unchanged"] is True
    assert (facts["totals"]["killed"], facts["totals"]["survived"]) == (11, 6)
    assert facts["target_tests"] == ["net.javaguides.springboot.controller.StudentControllerPipelineTest"] and facts["excluded_methods"] == []
    pit = fake.calls[-1]
    assert "--includedTestMethods" not in pit and Path(pit[pit.index("--reportDir") + 1]).is_relative_to(tmp_path / "run")
    assert [row["pinned"] for row in facts["tool"]["jars"]] == [True, True, False] and facts["tool"]["launcher_version"] == "5.11.4"
    summary = mutation.summary(facts)
    assert summary["score"] == round(11 / 17, 4) and summary["status"] == "MEASURED"


def test_a_failing_method_is_excluded_and_named(tmp_path: Path) -> None:
    automation, document = _step5()
    pins_path, jars = _fake_pins(tmp_path)
    fake = FakeBuild(tmp_path, mutation.load_pins(pins_path), jars, xml=_xml("step5-9340016c-mutations.xml.gz"))
    failing = automation["artifacts"]["generated_symbols"][0]["locator"]["method_name"]
    facts = mutation.measure(_inputs(tmp_path, automation, document, _report(tmp_path, automation, "FAIL", (failing,)), fake, pins_path))
    assert facts["status"] == "MEASURED" and facts["excluded_methods"] == [f"net.javaguides.springboot.controller.StudentControllerPipelineTest#{failing}"]
    included = fake.calls[-1][fake.calls[-1].index("--includedTestMethods") + 1].split(",")
    assert failing not in included and len(included) == 11


def test_a_tampered_jar_stops_before_pit(tmp_path: Path) -> None:
    automation, document = _step5()
    pins_path, jars = _fake_pins(tmp_path)
    fake = FakeBuild(tmp_path, mutation.load_pins(pins_path), {**jars, "pitest-1.30.0.jar": b"tampered"}, xml=_xml("step5-9340016c-mutations.xml.gz"))
    facts = mutation.measure(_inputs(tmp_path, automation, document, _report(tmp_path, automation), fake, pins_path))
    assert (facts["status"], facts["reason_code"]) == ("NOT_RUNNABLE", "MUTATION_TOOL_DIGEST_MISMATCH")
    assert not any("--reportDir" in call for call in fake.calls) and facts["totals"] is None


def test_a_project_change_during_the_stage_voids_the_measurement(tmp_path: Path) -> None:
    automation, document = _step5()
    pins_path, jars = _fake_pins(tmp_path)
    fake = FakeBuild(tmp_path, mutation.load_pins(pins_path), jars, xml=_xml("step5-9340016c-mutations.xml.gz"), touch=tmp_path / "project" / "src" / "main" / "java" / "New.java")
    facts = mutation.measure(_inputs(tmp_path, automation, document, _report(tmp_path, automation), fake, pins_path))
    assert (facts["status"], facts["reason_code"], facts["project_inventory"]["unchanged"]) == ("NOT_RUNNABLE", "MUTATION_PROJECT_CHANGED", False)


@pytest.mark.parametrize("change,code", [
    (lambda report: report.update(verdict="UNKNOWN"), "MUTATION_VERIFICATION_NOT_AUTHORITATIVE"),
    (lambda report: report.update(evidence_authoritative=False), "MUTATION_VERIFICATION_NOT_AUTHORITATIVE"),
    (lambda report: report.update(target={"language": "python"}), "MUTATION_LANGUAGE_UNSUPPORTED"),
    (lambda report: report["execution"].update(adapter_id="pytest:selected-symbols-v1"), "MUTATION_BUILD_TOOL_UNSUPPORTED"),
])
def test_inapplicable_runs_are_not_applicable_without_any_process(tmp_path: Path, change, code: str) -> None:
    automation, document = _step5()
    pins_path, jars = _fake_pins(tmp_path)
    fake = FakeBuild(tmp_path, mutation.load_pins(pins_path), jars, xml=b"")
    report = _report(tmp_path, automation)
    change(report)
    facts = mutation.measure(_inputs(tmp_path, automation, document, report, fake, pins_path))
    assert (facts["status"], facts["reason_code"]) == ("NOT_APPLICABLE", code) and fake.calls == []


def test_settings_need_an_explicit_enable() -> None:
    assert mutation.settings_of({}) is None
    assert mutation.settings_of({"mutation": {"enabled": False, "threads": 4}}) is None
    assert mutation.settings_of({"mutation": {"enabled": True, "threads": 4}}) == {**mutation.DEFAULTS, "threads": 4}
