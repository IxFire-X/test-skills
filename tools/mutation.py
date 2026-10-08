"""Opt-in MUTATION stage: PIT from the command line on the passing generated tests.

Contract amendments 2026-10-07 (``docs/superpowers/specs/2026-10-07-pilot-contract-amendments.md``):

* A2 — the project's own Maven resolves the pinned PIT jars (``tools/mutation_tools.json``)
  into its local repository, only with ``.skillsrc`` ``mutation.enabled: true`` **and**
  ``mutation_requested`` in the run authorization; every jar of the closure is checked
  against its SHA-256 and a mismatch makes the stage ``NOT_RUNNABLE``;
* A3 — the stage runs after the execution trace and before the retain/cleanup decision,
  only after an authoritative ``PASS`` or ``FAIL``, mutates only through the passing
  generated methods, writes only into the run directory and proves the project
  inventory unchanged.  Its result is the separate axis ``test_strength``.

The stage never raises into finalization: every stop becomes a receipt status
(``MEASURED``, ``NOT_RUNNABLE`` or ``NOT_APPLICABLE``) with a reason code.  Attribution
uses the full mutation matrix: a test descriptor names a generated method, the
automation maps it to a symbol and the symbol to cases (``implementation_relations``),
a case to canonical requirements and those to source requirements.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
PINS_PATH = ROOT / "tools" / "mutation_tools.json"
DEFAULTS = {"threads": 2, "timeout_seconds": 1800, "timeout_const_ms": 4000, "mutators": "DEFAULTS", "triage_limit": 30}
PIT_STATUSES = ("KILLED", "SURVIVED", "NO_COVERAGE", "TIMED_OUT", "MEMORY_ERROR", "RUN_ERROR", "NON_VIABLE", "NOT_STARTED", "STARTED")
MAVEN_ADAPTERS = {"maven-wrapper:selected-symbols-v1", "maven:selected-symbols-v1"}
GRADLE_ADAPTER = "gradle-wrapper:selected-symbols-v1"
_EXCLUDED_TOP = {".pilot-runs", ".git"}
_METHOD = re.compile(r"\[(?:method|test-template|test-factory):([A-Za-z_$][A-Za-z0-9_$]*)\(")
_CLASS = re.compile(r"\[class:([A-Za-z_$][A-Za-z0-9_$.]*)\]")
_NESTED = re.compile(r"\[nested-class:([A-Za-z_$][A-Za-z0-9_$]*)\]")
_SOURCE_PATH = re.compile(r"(?:[A-Za-z0-9_.-]+/)*src/main/java/((?:[A-Za-z_$][A-Za-z0-9_$]*/)*[A-Za-z_$][A-Za-z0-9_$]*)\.java")


class MutationStop(Exception):
    """A planned stop of the stage: it becomes the receipt status, never an exception of finalization."""

    def __init__(self, status: str, code: str, message: str) -> None:
        self.status, self.code = status, code
        super().__init__(message)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# pins and settings
# --------------------------------------------------------------------------------------

def load_pins(path: Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or PINS_PATH).read_text(encoding="utf-8"))


def pins_digest(path: Path | None = None) -> str:
    return "sha256:" + hashlib.sha256(Path(path or PINS_PATH).read_bytes()).hexdigest()


def settings_of(skillsrc: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The ``mutation`` section with defaults, or None when the project did not enable mutations."""
    section = skillsrc.get("mutation") if isinstance(skillsrc, Mapping) else None
    if not isinstance(section, Mapping) or section.get("enabled") is not True:
        return None
    settings = {**DEFAULTS, **{key: value for key, value in section.items() if key != "enabled"}}
    return settings


# --------------------------------------------------------------------------------------
# project facts
# --------------------------------------------------------------------------------------

def project_snapshot(project: Path) -> str:
    """Digest of every project file (path, size, SHA-256) except ``.pilot-runs`` and ``.git``.

    Equal digests before and after the stage prove that it changed no project file
    (build outputs included).
    """
    project = Path(project)
    rows = []
    for current, directories, files in os.walk(project):
        relative = Path(current).relative_to(project)
        if relative == Path("."):
            directories[:] = [name for name in directories if name not in _EXCLUDED_TOP]
        directories.sort()
        for name in sorted(files):
            path = Path(current) / name
            try:
                rows.append([(relative / name).as_posix(), path.stat().st_size, _sha256_file(path)])
            except OSError:
                rows.append([(relative / name).as_posix(), None, None])
    return _canonical_digest(sorted(rows))


@dataclass(frozen=True)
class BuildTool:
    """The build tool that ran the tests, recovered from the durable execution request."""

    adapter_id: str
    executable: Path
    cwd: Path
    module_path: str
    module_root: Path
    profile_args: tuple[str, ...] = ()

    @classmethod
    def from_execution(cls, execution: Mapping[str, Any]) -> "BuildTool":
        from tools.execution_adapters import UNDECLARED_PROFILE

        adapter = execution.get("adapter_id")
        argv = [str(item) for item in execution.get("argv") or []]
        if adapter not in MAVEN_ADAPTERS | {GRADLE_ADAPTER} or not argv or not execution.get("cwd"):
            raise MutationStop("NOT_APPLICABLE", "MUTATION_BUILD_TOOL_UNSUPPORTED", f"mutations need Maven or Gradle, not {adapter}")
        module_path = ""
        if adapter in MAVEN_ADAPTERS and "-pl" in argv:
            module_path = argv[argv.index("-pl") + 1]
        cwd = Path(str(execution["cwd"]))
        profile = str(execution.get("build_profile") or UNDECLARED_PROFILE)
        profile_args = () if profile == UNDECLARED_PROFILE else ((f"-P{profile}",) if adapter in MAVEN_ADAPTERS else (f"-Pprofile={profile}",))
        return cls(str(adapter), Path(str(execution["executable_path"])), cwd, module_path,
                   cwd.joinpath(*module_path.split("/")) if module_path else cwd, profile_args)


def method_outcomes(report: Mapping[str, Any], automation: Mapping[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Passing and failing generated methods ``(class_fqn, method)`` from the authoritative report."""
    symbols = {row["symbol_id"]: row["locator"] for row in automation["artifacts"]["generated_symbols"]}
    passing, failing = [], []
    for row in report.get("execution_evidence") or []:
        locator = symbols.get(row.get("symbol_id"))
        if not isinstance(locator, Mapping) or locator.get("kind") != "java_class_method":
            continue
        key = (str(locator["class_fqn"]), str(locator["method_name"]))
        (passing if row.get("status") == "PASSED" else failing).append(key)
    return sorted(set(passing)), sorted(set(failing) - set(passing))


def target_tests(automation: Mapping[str, Any]) -> list[str]:
    return sorted({str(row["locator"]["class_fqn"]) for row in automation["artifacts"]["generated_symbols"]
                   if row.get("locator", {}).get("kind") == "java_class_method"})


def target_classes(settings: Mapping[str, Any], document: Mapping[str, Any], module_root: Path, tests: Sequence[str]) -> list[str]:
    """Classes to mutate: ``mutation.target_classes``, else packages of the product files capability provenance names."""
    if settings.get("target_classes"):
        return sorted(settings["target_classes"])
    packages = set()
    for capability in document.get("operation_capabilities", []):
        for line in capability.get("provenance", []):
            for match in _SOURCE_PATH.finditer(str(line).replace("\\", "/")):
                parts = match.group(1).split("/")
                if (module_root / "src" / "main" / "java").joinpath(*parts).with_suffix(".java").is_file():
                    packages.add(".".join(parts[:-1]))
    if not packages:
        packages = {test.rpartition(".")[0] for test in tests if "." in test}
    return sorted(f"{package}.*" if package else "*" for package in packages)


def java_executable(environment: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if environment is None else environment
    home = environment.get("JAVA_HOME")
    if home:
        candidate = Path(home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.is_file():
            return candidate
    found = shutil.which("java")
    if not found:
        raise MutationStop("NOT_RUNNABLE", "MUTATION_JAVA_MISSING", "no java executable (JAVA_HOME or PATH)")
    return Path(found)


# --------------------------------------------------------------------------------------
# resolution of the pinned tool and the project classpath
# --------------------------------------------------------------------------------------

def resolver_pom(pins: Mapping[str, Any], launcher_version: str) -> str:
    """A throwaway descriptor in the run directory: the pinned PIT roots plus the project's launcher."""
    java = pins["java"]
    rows = [*java["roots"], f"{java['launcher']}:{launcher_version}"]
    dependencies = "\n".join(
        "    <dependency><groupId>{}</groupId><artifactId>{}</artifactId><version>{}</version></dependency>".format(*row.split(":")) for row in rows)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<project xmlns=\"http://maven.apache.org/POM/4.0.0\">\n  <modelVersion>4.0.0</modelVersion>\n"
            "  <groupId>local.test-skills</groupId>\n  <artifactId>mutation-tool-resolver</artifactId>\n  <version>1</version>\n  <packaging>pom</packaging>\n"
            f"  <dependencies>\n{dependencies}\n  </dependencies>\n</project>\n")


def _classpath_file(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return [entry for entry in text.split(os.pathsep) if entry]


def verify_tool_jars(entries: Sequence[str], pins: Mapping[str, Any], launcher_version: str | None = None) -> list[dict[str, Any]]:
    """Every pinned jar exactly once with its SHA-256; the rest only the project's JUnit Platform launcher family.

    The family is not pinned (amendment A2: "recorded, not pinned"): each jar's SHA-256 goes to the
    receipt, and a ``junit-platform-*`` jar must be the project's own Platform version (review 2.1 item 13).
    """
    java = pins["java"]
    pinned = {row["file"]: row["sha256"] for row in java["jars"]}
    family = tuple(java["launcher_family"])
    rows, seen = [], set()
    for entry in entries:
        path = Path(entry)
        name = path.name
        if not path.is_file():
            raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_MISSING", f"resolved tool jar is missing: {name}")
        digest = _sha256_file(path)
        if name in pinned:
            if digest != pinned[name]:
                raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_DIGEST_MISMATCH", f"{name}: SHA-256 {digest} differs from the pinned {pinned[name]}")
            seen.add(name)
            rows.append({"file": name, "sha256": digest, "pinned": True})
        elif any(name.startswith(prefix + "-") for prefix in family):
            if launcher_version and name.startswith("junit-platform-") and not name.endswith(f"-{launcher_version}.jar"):
                raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_UNPINNED", f"{name} is not the project's JUnit Platform {launcher_version}")
            rows.append({"file": name, "sha256": digest, "pinned": False})
        else:
            raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_UNPINNED", f"the resolved closure has an unpinned jar: {name}")
    missing = sorted(set(pinned) - seen)
    if missing:
        raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_MISSING", "pinned jars are missing from the resolved closure: " + ", ".join(missing))
    return rows


def junit_platform_version(entries: Iterable[str]) -> str:
    """The JUnit Platform version of the project classpath (engine, else commons)."""
    for prefix in ("junit-platform-engine-", "junit-platform-commons-", "junit-platform-launcher-"):
        for entry in entries:
            name = Path(entry).name
            if name.startswith(prefix) and name.endswith(".jar"):
                return name[len(prefix):-len(".jar")]
    raise MutationStop("NOT_RUNNABLE", "MUTATION_JUNIT_PLATFORM_MISSING", "the project test classpath has no JUnit Platform")


Runner = Callable[..., Any]


def _run(run: Runner, argv: Sequence[str], cwd: Path, timeout: int) -> Any:
    from tools.run_tests import run_subprocess

    return (run or run_subprocess)(list(argv), cwd, None, timeout=max(1, int(timeout)))


def _tail(outcome: Any) -> str:
    text = f"{getattr(outcome, 'stdout', '')}\n{getattr(outcome, 'stderr', '')}".strip()
    return text[-600:]


def _maven_build_classpath(build: BuildTool, pins: Mapping[str, Any], output: Path, *, run: Runner, timeout: int) -> list[str]:
    scope = (*build.profile_args, "-pl", build.module_path) if build.module_path else build.profile_args
    argv = [str(build.executable), *scope, "-q", "-B", "-ntp", f"{pins['java']['resolver_plugin']}:build-classpath",
            "-Dmdep.includeScope=test", f"-Dmdep.outputFile={output}"]
    outcome = _run(run, argv, build.cwd, timeout)
    entries = _classpath_file(output)
    if getattr(outcome, "exit_code", 1) != 0 or not output.is_file():
        raise MutationStop("NOT_RUNNABLE", "MUTATION_CLASSPATH_FAILED", "the project classpath could not be built: " + _tail(outcome))
    return entries


_GRADLE_INIT = """// test-skills mutation stage: prints the test runtime classpath of one project; changes no build file.
allprojects {
    tasks.register("testSkillsMutationClasspath") {
        doLast {
            def out = new File(System.getProperty("testSkills.classpathFile"))
            def sets = project.extensions.findByName("sourceSets")
            if (sets == null) { return }
            def test = sets.getByName("test")
            def main = sets.getByName("main")
            def files = new LinkedHashSet()
            files.addAll(main.output.files)
            files.addAll(test.output.files)
            files.addAll(test.runtimeClasspath.files)
            out.text = files.collect { it.absolutePath }.join(File.pathSeparator)
        }
    }
}
"""


def _gradle_build_classpath(build: BuildTool, output: Path, workdir: Path, *, run: Runner, timeout: int) -> list[str]:
    script = workdir / "mutation-classpath.gradle"
    script.write_text(_GRADLE_INIT, encoding="utf-8", newline="\n")
    task = ":" + ":".join([*(build.module_path.split("/") if build.module_path else ()), "testSkillsMutationClasspath"])
    argv = [str(build.executable), "--init-script", str(script), task, f"-DtestSkills.classpathFile={output}", *build.profile_args, "--no-daemon", "-q"]
    outcome = _run(run, argv, build.cwd, timeout)
    if getattr(outcome, "exit_code", 1) != 0 or not output.is_file():
        raise MutationStop("NOT_RUNNABLE", "MUTATION_CLASSPATH_FAILED", "the project classpath could not be built: " + _tail(outcome))
    return _classpath_file(output)


def project_classpath(build: BuildTool, pins: Mapping[str, Any], workdir: Path, *, run: Runner, timeout: int) -> list[str]:
    """Compiled main and test classes of the module plus its test dependencies, written to the run directory."""
    output = workdir / "project-classpath.txt"
    if build.adapter_id in MAVEN_ADAPTERS:
        dependencies = _maven_build_classpath(build, pins, output, run=run, timeout=timeout)
        compiled = [build.module_root / "target" / "classes", build.module_root / "target" / "test-classes"]
        if not all(path.is_dir() for path in compiled):
            raise MutationStop("NOT_RUNNABLE", "MUTATION_CLASSES_MISSING", "compiled classes of the module are missing (target/classes, target/test-classes)")
        return [str(path) for path in compiled] + dependencies
    return _gradle_build_classpath(build, output, workdir, run=run, timeout=timeout)


def resolve_tool(build: BuildTool, pins: Mapping[str, Any], workdir: Path, launcher_version: str, *, run: Runner, timeout: int) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve the pinned PIT closure with the project's Maven and verify every jar."""
    resolver = workdir / "resolver"
    resolver.mkdir(parents=True, exist_ok=True)
    pom = resolver / "pom.xml"
    pom.write_text(resolver_pom(pins, launcher_version), encoding="utf-8", newline="\n")
    output = resolver / "classpath.txt"
    if build.adapter_id in MAVEN_ADAPTERS:
        maven = build.executable
    else:
        located = shutil.which("mvn")
        if located is None:
            raise MutationStop("NOT_RUNNABLE", "MUTATION_RESOLVER_MISSING", "a Gradle project needs Maven on PATH to resolve the pinned PIT jars")
        maven = Path(located)
    argv = [str(maven), "-q", "-B", "-ntp", "-f", str(pom), f"{pins['java']['resolver_plugin']}:build-classpath", f"-Dmdep.outputFile={output}"]
    outcome = _run(run, argv, build.cwd, timeout)
    if getattr(outcome, "exit_code", 1) != 0 or not output.is_file():
        raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_RESOLUTION_FAILED", "the pinned mutation tool could not be resolved: " + _tail(outcome))
    entries = _classpath_file(output)
    return entries, verify_tool_jars(entries, pins, launcher_version)


# --------------------------------------------------------------------------------------
# PIT
# --------------------------------------------------------------------------------------

def pit_argv(java: Path, tool_classpath: Sequence[str], *, report_dir: Path, classes: Sequence[str], tests: Sequence[str], source_dirs: Sequence[Path],
             classpath_file: Path, settings: Mapping[str, Any], pins: Mapping[str, Any], included_methods: Sequence[str] = ()) -> list[str]:
    argv = [str(java), "-cp", os.pathsep.join(tool_classpath), pins["java"]["main_class"],
            "--reportDir", str(report_dir), "--targetClasses", ",".join(classes), "--targetTests", ",".join(tests),
            "--sourceDirs", ",".join(str(path) for path in source_dirs), "--classPathFile", str(classpath_file),
            "--outputFormats", "XML", "--fullMutationMatrix", "--timestampedReports=false", "--failWhenNoMutations=false",
            "--skipFailingTests", "--threads", str(int(settings["threads"])), "--timeoutConst", str(int(settings["timeout_const_ms"])),
            "--mutators", str(settings["mutators"]), "--inputEncoding", "UTF-8", "--outputEncoding", "UTF-8", "--jvmPath", str(java)]
    if included_methods:
        argv += ["--includedTestMethods", ",".join(included_methods)]
    return argv


def parse_report(data: bytes) -> list[dict[str, Any]]:
    """Mutations of one ``mutations.xml`` with the full matrix."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise MutationStop("NOT_RUNNABLE", "MUTATION_REPORT_INVALID", f"mutations.xml is not XML: {error}") from error
    if root.tag != "mutations":
        raise MutationStop("NOT_RUNNABLE", "MUTATION_REPORT_INVALID", "mutations.xml has no <mutations> root")
    rows = []
    for node in root.iter("mutation"):
        def text(tag: str) -> str:
            child = node.find(tag)
            return (child.text or "").strip() if child is not None and child.text else ""

        status = node.get("status", "")
        if status not in PIT_STATUSES:
            raise MutationStop("NOT_RUNNABLE", "MUTATION_REPORT_INVALID", f"unknown mutation status {status}")
        try:
            line = int(text("lineNumber"))
        except ValueError as error:
            raise MutationStop("NOT_RUNNABLE", "MUTATION_REPORT_INVALID", "a mutation has no line number") from error
        rows.append({
            "status": status, "source_file": text("sourceFile"), "class": text("mutatedClass"), "method": text("mutatedMethod"),
            "method_description": text("methodDescription"), "line": line, "mutator": text("mutator").rsplit(".", 1)[-1],
            "description": text("description"),
            "killing": [item for item in text("killingTests").split("|") if item],
            "succeeding": [item for item in text("succeedingTests").split("|") if item],
            "covering": [item for item in text("coveringTests").split("|") if item],
        })
    return rows


def descriptor_method(descriptor: str) -> tuple[str, str] | None:
    """``(class_fqn, method)`` of a JUnit Platform test descriptor PIT prints."""
    method = _METHOD.search(descriptor)
    owner = _CLASS.search(descriptor)
    if method is None or owner is None:
        return None
    nested = _NESTED.findall(descriptor)
    return ("$".join([owner.group(1), *nested]), method.group(1))


def attribute(mutants: Sequence[Mapping[str, Any]], automation: Mapping[str, Any], document: Mapping[str, Any], *, triage_limit: int = DEFAULTS["triage_limit"]) -> dict[str, Any]:
    """Counts per run, case, canonical and source requirement, and survivor groups (counts only: no floats in receipts)."""
    artifacts = automation["artifacts"]
    symbol_of = {(str(row["locator"]["class_fqn"]), str(row["locator"]["method_name"])): row["symbol_id"]
                 for row in artifacts["generated_symbols"] if row.get("locator", {}).get("kind") == "java_class_method"}
    cases_of: dict[str, set[str]] = {}
    for row in artifacts["implementation_relations"]:
        cases_of.setdefault(row["symbol_id"], set()).add(row["case_id"])
    requirements_of = {case["case_id"]: list(case.get("requirement_ids", [])) for case in document.get("test_cases", [])}
    sources_of: dict[str, set[str]] = {}
    for row in document.get("source_to_canonical_mappings", []):
        for creq in row.get("canonical_requirement_ids", []):
            sources_of.setdefault(creq, set()).add(row["source_requirement_id"])

    def methods(descriptors: Iterable[str]) -> set[tuple[str, str]]:
        found = set()
        for descriptor in descriptors:
            key = descriptor_method(descriptor)
            if key is not None and key in symbol_of:
                found.add(key)
        return found

    totals = {status.lower(): 0 for status in PIT_STATUSES}
    per_case: dict[str, dict[str, Any]] = {}
    groups: dict[tuple[str, str, int], dict[str, Any]] = {}
    for mutant in mutants:
        totals[mutant["status"].lower()] += 1
        covering = methods([*mutant["covering"], *mutant["killing"], *mutant["succeeding"]])
        killing = methods(mutant["killing"])
        counted = mutant["status"] in {"KILLED", "SURVIVED"}
        case_ids = sorted({case for key in covering for case in cases_of.get(symbol_of[key], ())})
        for case in case_ids:
            row = per_case.setdefault(case, {"case_id": case, "covered": 0, "killed": 0, "survived_ids": []})
            if counted:
                row["covered"] += 1
                own = {key for key in covering if case in cases_of.get(symbol_of[key], ())}
                if own & killing:
                    row["killed"] += 1
        if mutant["status"] == "SURVIVED":
            key = (mutant["class"], mutant["method"], mutant["line"])
            group = groups.setdefault(key, {"class": mutant["class"], "method": mutant["method"], "line": mutant["line"], "source_file": mutant["source_file"],
                                            "mutants": [], "covering_methods": set(), "case_ids": set()})
            group["mutants"].append({"mutator": mutant["mutator"], "description": mutant["description"]})
            group["covering_methods"].update(f"{owner}#{name}" for owner, name in covering)
            group["case_ids"].update(case_ids)
    for row in per_case.values():
        row.pop("survived_ids")

    def requirement_rows(key_of: Callable[[str], Iterable[str]], name: str) -> list[dict[str, Any]]:
        table: dict[str, dict[str, int]] = {}
        for mutant in mutants:
            if mutant["status"] not in {"KILLED", "SURVIVED"}:
                continue
            covering = methods([*mutant["covering"], *mutant["killing"], *mutant["succeeding"]])
            killing = methods(mutant["killing"])
            touched: dict[str, bool] = {}
            for method_key in covering:
                for case in cases_of.get(symbol_of[method_key], ()):
                    for requirement in key_of(case):
                        touched[requirement] = touched.get(requirement, False) or method_key in killing
            for requirement, killed in touched.items():
                row = table.setdefault(requirement, {"covered": 0, "killed": 0})
                row["covered"] += 1
                row["killed"] += int(killed)
        return [{name: requirement, **counts} for requirement, counts in sorted(table.items())]

    survivor_groups = []
    for index, key in enumerate(sorted(groups), start=1):
        group = groups[key]
        case_ids = sorted(group["case_ids"])
        requirement_ids = sorted({requirement for case in case_ids for requirement in requirements_of.get(case, [])})
        survivor_groups.append({
            "group_id": f"MUT-{index:04d}", "class": group["class"], "method": group["method"], "line": group["line"],
            "source_file": group["source_file"], "mutants": sorted(group["mutants"], key=lambda row: (row["mutator"], row["description"])),
            "covering_methods": sorted(group["covering_methods"]), "case_ids": case_ids, "requirement_ids": requirement_ids,
            "source_requirement_ids": sorted({source for requirement in requirement_ids for source in sources_of.get(requirement, ())}),
            "triage": index <= int(triage_limit),
        })
    return {
        "totals": totals,
        "cases": sorted(per_case.values(), key=lambda row: row["case_id"]),
        "requirements": requirement_rows(lambda case: requirements_of.get(case, []), "requirement_id"),
        "source_requirements": requirement_rows(lambda case: {source for requirement in requirements_of.get(case, []) for source in sources_of.get(requirement, ())},
                                                "source_requirement_id"),
        "survivor_groups": survivor_groups,
    }


def score(killed: int, survived: int) -> float | None:
    """Killed / (killed + survived) among covered mutants; None when nothing was counted."""
    total = killed + survived
    return None if total == 0 else round(killed / total, 4)


# --------------------------------------------------------------------------------------
# the stage
# --------------------------------------------------------------------------------------

@dataclass
class StageInputs:
    project: Path
    workdir: Path
    report: Mapping[str, Any]
    automation: Mapping[str, Any]
    document: Mapping[str, Any]
    settings: Mapping[str, Any]
    run: Runner | None = None
    environment: Mapping[str, str] | None = None
    clock: Callable[[], float] = time.monotonic
    pins_path: Path | None = None
    timings: dict[str, float] = field(default_factory=dict)


def measure(inputs: StageInputs) -> dict[str, Any]:
    """Run the stage and return the receipt facts; every stop is a status with a reason, never an exception."""
    started = inputs.clock()
    pins = load_pins(inputs.pins_path)
    facts: dict[str, Any] = {
        "status": "NOT_RUNNABLE", "reason_code": None, "message": None, "verification": inputs.report.get("verdict"),
        "tool": {"name": "pitest", "version": pins["java"]["pit_version"], "plugin_version": pins["java"]["plugin_version"], "pins_digest": pins_digest(inputs.pins_path), "jars": []},
        "settings": dict(inputs.settings), "target_classes": [], "target_tests": [], "excluded_methods": [],
        "project_inventory": {"before": None, "after": None, "unchanged": None}, "durations_ms": {}, "report": None,
        "totals": None, "cases": [], "requirements": [], "source_requirements": [], "survivor_groups": [],
    }
    before = project_snapshot(inputs.project)
    facts["project_inventory"]["before"] = before

    def remaining() -> int:
        return int(max(1.0, float(inputs.settings["timeout_seconds"]) - (inputs.clock() - started)))

    def lap(name: str, since: float) -> None:
        facts["durations_ms"][name] = int((inputs.clock() - since) * 1000)

    try:
        if inputs.report.get("verdict") not in {"PASS", "FAIL"} or inputs.report.get("evidence_authoritative") is not True:
            raise MutationStop("NOT_APPLICABLE", "MUTATION_VERIFICATION_NOT_AUTHORITATIVE", f"mutations need an authoritative PASS or FAIL, not {inputs.report.get('verdict')}")
        if (inputs.report.get("target") or {}).get("language") != "java":
            raise MutationStop("NOT_APPLICABLE", "MUTATION_LANGUAGE_UNSUPPORTED", "wave 2 mutates Java projects only (Python mutation is not implemented)")
        build = BuildTool.from_execution(inputs.report.get("execution") or {})
        passing, failing = method_outcomes(inputs.report, inputs.automation)
        facts["excluded_methods"] = [f"{owner}#{name}" for owner, name in failing]
        if not passing:
            raise MutationStop("NOT_APPLICABLE", "MUTATION_NO_PASSING_METHODS", "no generated method passed")
        tests = target_tests(inputs.automation)
        facts["target_tests"] = tests
        inputs.workdir.mkdir(parents=True, exist_ok=True)
        step = inputs.clock()
        classpath = project_classpath(build, pins, inputs.workdir, run=inputs.run, timeout=remaining())
        lap("project_classpath", step)
        launcher = junit_platform_version(classpath)
        step = inputs.clock()
        tool_classpath, jars = resolve_tool(build, pins, inputs.workdir, launcher, run=inputs.run, timeout=remaining())
        lap("tool_resolution", step)
        facts["tool"]["jars"] = jars
        facts["tool"]["launcher_version"] = launcher
        classes = target_classes(inputs.settings, inputs.document, build.module_root, tests)
        facts["target_classes"] = classes
        classpath_file = inputs.workdir / "pit-classpath.txt"
        classpath_file.write_text("\n".join(classpath) + "\n", encoding="utf-8", newline="\n")
        report_dir = inputs.workdir / "report"
        if report_dir.exists():
            shutil.rmtree(report_dir)
        java = java_executable(inputs.environment)
        source_dirs = [path for path in (build.module_root / "src" / "main" / "java",) if path.is_dir()]
        # With a failing method in the class only the passing ones challenge the mutants.
        included = sorted({name for _owner, name in passing}) if failing else []
        argv = pit_argv(java, tool_classpath, report_dir=report_dir, classes=classes, tests=tests, source_dirs=source_dirs,
                        classpath_file=classpath_file, settings=inputs.settings, pins=pins, included_methods=included)
        facts["argv_digest"] = _canonical_digest(argv)
        step = inputs.clock()
        outcome = _run(inputs.run, argv, build.module_root, remaining())
        lap("pit", step)
        if getattr(outcome, "kind", "EXIT") == "TIMEOUT":
            raise MutationStop("NOT_RUNNABLE", "MUTATION_TIMEOUT", f"PIT exceeded the stage timeout of {inputs.settings['timeout_seconds']} s")
        xml_path = report_dir / "mutations.xml"
        if getattr(outcome, "exit_code", 1) != 0 or not xml_path.is_file():
            raise MutationStop("NOT_RUNNABLE", "MUTATION_TOOL_FAILED", "PIT did not produce a report: " + _tail(outcome))
        data = xml_path.read_bytes()
        mutants = parse_report(data)
        facts["report"] = {"sha256": "sha256:" + hashlib.sha256(data).hexdigest(), "bytes": len(data), "mutations": len(mutants)}
        facts.update(attribute(mutants, inputs.automation, inputs.document, triage_limit=int(inputs.settings["triage_limit"])))
        facts["status"] = "MEASURED"
    except MutationStop as stop:
        facts.update({"status": stop.status, "reason_code": stop.code, "message": str(stop)[:600]})
    except Exception as error:  # noqa: BLE001 — review 2.1 item 9: every stop of the stage is a status with a reason
        facts.update({"status": "NOT_RUNNABLE", "reason_code": "MUTATION_STAGE_ERROR", "message": f"{type(error).__name__}: {error}"[:600],
                      "report": None, "totals": None, "cases": [], "requirements": [], "source_requirements": [], "survivor_groups": []})
    try:
        after = project_snapshot(inputs.project)
    except OSError as error:
        after = None
        facts.update({"status": "NOT_RUNNABLE", "reason_code": "MUTATION_STAGE_ERROR", "message": f"the project inventory after the stage failed: {error}"[:600],
                      "report": None, "totals": None, "cases": [], "requirements": [], "source_requirements": [], "survivor_groups": []})
    facts["project_inventory"].update({"after": after, "unchanged": after == before})
    if after is not None and after != before:
        facts.update({"status": "NOT_RUNNABLE", "reason_code": "MUTATION_PROJECT_CHANGED",
                      "message": "the project inventory changed during the mutation stage", "totals": None, "cases": [], "requirements": [],
                      "source_requirements": [], "survivor_groups": []})
    facts["durations_ms"]["stage"] = int((inputs.clock() - started) * 1000)
    return facts


def summary(facts: Mapping[str, Any]) -> dict[str, Any]:
    """The short ``test_strength`` view for the driver summary and reports."""
    totals = facts.get("totals") or {}
    killed, survived = int(totals.get("killed", 0)), int(totals.get("survived", 0))
    return {"status": facts.get("status"), "reason_code": facts.get("reason_code"), "killed": killed, "survived": survived,
            "no_coverage": int(totals.get("no_coverage", 0)), "timed_out": int(totals.get("timed_out", 0)), "memory_error": int(totals.get("memory_error", 0)),
            "score": score(killed, survived), "survivor_groups": len(facts.get("survivor_groups") or []),
            "duration_seconds": round(int((facts.get("durations_ms") or {}).get("stage", 0)) / 1000, 1)}


# A test seam: the runner of build-tool and PIT processes (None = the scoped project process runner).
_RUNNER: Runner | None = None


def _project_settings(project: Path, run_root: Path, attempt: Mapping[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """``mutation`` settings from the frozen ``.skillsrc`` (its bytes must still match the baseline)."""
    from tools.pilot_state import read_execution_baseline_for_attempt
    from tools.skillsrc_manifest import parse_skillsrc_bytes

    baseline = read_execution_baseline_for_attempt(run_root, str(attempt["attempt_id"]))
    try:
        data = (Path(project) / ".skillsrc").read_bytes()
    except OSError:
        return None, "MUTATION_SKILLSRC_UNREADABLE"
    authority = baseline.get("skillsrc_authority") or {}
    if "sha256:" + hashlib.sha256(data).hexdigest() != authority.get("skillsrc_digest"):
        return None, "MUTATION_SKILLSRC_DRIFT"
    return settings_of(parse_skillsrc_bytes(data)), None


def mutation_stage(run_root: Path, attempt_id: str) -> dict[str, Any] | None:
    """Run (or read back) the MUTATION stage of a local attempt; None when the run did not consent."""
    from tools import pilot_state

    run = pilot_state.read_run(run_root)
    if run["authorization"].get("mutation_requested") is not True:
        return None
    existing = pilot_state.read_mutation_receipt_if_present(run_root, attempt_id)
    if existing is not None:
        return existing
    attempt = next(row for row in pilot_state.derive_state(run_root)["attempts"] if row["attempt_id"] == attempt_id)
    project = Path(attempt["project"])
    execution = pilot_state.read_attempt_receipt(run_root, attempt_id, "execution-receipt", "ARTIFACT_READ_BACK")["record"]
    report = execution["payload"]
    settings, problem = _project_settings(project, run_root, attempt)
    if settings is None:
        status, code = ("NOT_RUNNABLE", problem) if problem else ("NOT_APPLICABLE", "MUTATION_NOT_ENABLED")
        before = project_snapshot(project)
        pins = load_pins()
        facts: dict[str, Any] = {
            "status": status, "reason_code": code, "message": "mutation.enabled is not true in .skillsrc" if problem is None else "the frozen .skillsrc is unavailable",
            "verification": report.get("verdict"),
            "tool": {"name": "pitest", "version": pins["java"]["pit_version"], "plugin_version": pins["java"]["plugin_version"], "pins_digest": pins_digest(), "jars": []},
            "settings": dict(DEFAULTS), "target_classes": [], "target_tests": [], "excluded_methods": [],
            "project_inventory": {"before": before, "after": before, "unchanged": True}, "durations_ms": {"stage": 0}, "report": None,
            "totals": None, "cases": [], "requirements": [], "source_requirements": [], "survivor_groups": [],
        }
    else:
        inputs = pilot_state.read_execution_inputs(run_root, attempt_id)
        effective = pilot_state.read_effective_canonical(run_root, attempt_id)
        workdir = Path(run_root).with_name(Path(run_root).name + ".driver") / "mutation" / attempt_id[:8]
        facts = measure(StageInputs(project=project, workdir=workdir, report=report, automation=inputs["automation_artifact"],
                                    document=effective["document"], settings=settings, run=_RUNNER))
        if facts["status"] == "MEASURED":
            try:
                data = (workdir / "report" / "mutations.xml").read_bytes()
                stored = pilot_state.publish_run_artifact_bytes(run_root, attempt_id, f"mutation/mutations-{facts['report']['sha256'][7:19]}.xml", data)
                facts["report"]["path"] = stored["path"]
            except (OSError, ValueError) as error:
                facts.update({"status": "NOT_RUNNABLE", "reason_code": "MUTATION_STAGE_ERROR", "message": f"the PIT report could not be kept: {error}"[:600],
                              "report": None, "totals": None, "cases": [], "requirements": [], "source_requirements": [], "survivor_groups": []})
    facts["execution_receipt_digest"] = execution["digest"]
    return dict(pilot_state.publish_mutation_receipt(run_root, attempt_id, facts)["record"])
