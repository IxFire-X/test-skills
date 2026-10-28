#!/usr/bin/env python3
"""Read-only deterministic inventory of runnable project modules."""
from __future__ import annotations
import argparse, hashlib, json, os, re, tomllib, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from .confined_output import atomic_write_confined_bytes
    from .stack_catalog import (
        GO_FRAMEWORK_MARKERS,
        GO_TEST_MARKERS,
        JAVA_FRAMEWORK_MARKERS,
        JAVA_TEST_MARKERS,
        JS_FRAMEWORK_MARKERS,
        JS_TEST_MARKERS,
        MANIFEST_LANGUAGES,
        PYTHON_FRAMEWORK_MARKERS,
        PYTHON_TEST_MARKERS,
        confined_files,
        is_ignored_dir,
        manifest_language,
    )
    from .schema_validation import StrictJsonError, load_json_strict
else:
    from confined_output import atomic_write_confined_bytes
    from stack_catalog import (
        GO_FRAMEWORK_MARKERS,
        GO_TEST_MARKERS,
        JAVA_FRAMEWORK_MARKERS,
        JAVA_TEST_MARKERS,
        JS_FRAMEWORK_MARKERS,
        JS_TEST_MARKERS,
        MANIFEST_LANGUAGES,
        PYTHON_FRAMEWORK_MARKERS,
        PYTHON_TEST_MARKERS,
        confined_files,
        is_ignored_dir,
        manifest_language,
    )
    from schema_validation import StrictJsonError, load_json_strict

WORKSPACE_MANIFESTS = {"settings.gradle", "settings.gradle.kts", "go.work"}
MARKERS = {"python": (PYTHON_FRAMEWORK_MARKERS, PYTHON_TEST_MARKERS, "pip"), "java": (JAVA_FRAMEWORK_MARKERS, JAVA_TEST_MARKERS, "maven"), "typescript": (JS_FRAMEWORK_MARKERS, JS_TEST_MARKERS, "npm"), "go": (GO_FRAMEWORK_MARKERS, GO_TEST_MARKERS, "go-mod")}
# FaT-H2b P2a: JUnit 4 in the same build — spring-boot-starter-test alone is then no proof of JUnit 5.
_JUNIT4_MARKERS = re.compile(r"junit-vintage-engine|<artifactid>\s*junit\s*</artifactid>|['\"]junit:junit[:'\"]")
SOURCE_SUFFIXES = {"python": frozenset({".py"}), "java": frozenset({".java"}), "typescript": frozenset({".ts", ".tsx", ".js", ".jsx"}), "go": frozenset({".go"})}
def _parts(root: Path, path: Path) -> tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return ()
def _rel(root: Path, p: Path) -> str: return p.relative_to(root).as_posix() or "."
def _ok(root: Path, p: Path) -> bool:
    try: p.resolve().relative_to(root); return True
    except ValueError: return False
def _is_reparse(path: Path) -> bool:
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    attributes = getattr(details, "st_file_attributes", 0)
    return bool(path.is_symlink() or attributes & 0x400)
def _usable_dir(root: Path, path: Path) -> bool:
    if _is_reparse(path):
        return False
    try:
        return path.is_dir() and _ok(root, path)
    except OSError:
        return False
def _read(p: Path) -> str:
    try: return p.read_text(encoding="utf-8", errors="replace")
    except OSError: return ""
def _parse(p: Path) -> Any:
    text = _read(p)
    if p.name == "package.json": return load_json_strict(p)
    if p.name == "pyproject.toml": return tomllib.loads(text)
    if p.name == "pom.xml": return ET.fromstring(text)
    return text
def _xml_name(element: ET.Element) -> str: return element.tag.rsplit("}", 1)[-1]
def _safe_module_ref(value: Any) -> bool:
    if not isinstance(value, str): return False
    value=value.strip()
    if not value or "\\" in value or value.startswith("/") or re.match(r"^[A-Za-z]:",value): return False
    return not any(part in {"", ".", ".."} or any(char in part for char in ':*?"<>|') for part in value.split("/"))
def _maven_refs(data: ET.Element) -> list[str]:
    return [module.text.strip() for modules in data if _xml_name(modules)=="modules" for module in modules if _xml_name(module)=="module"]
def _validate_maven(data: ET.Element) -> None:
    if _xml_name(data)!="project": raise ValueError
    for modules in data:
        if _xml_name(modules)!="modules": continue
        if modules.text and not modules.text.isspace(): raise ValueError
        for module in modules:
            if _xml_name(module)!="module" or list(module) or not _safe_module_ref(module.text): raise ValueError
            if module.tail and not module.tail.isspace(): raise ValueError
def find_confined_manifests(root: Path) -> list[Path]:
    found=[path for path in confined_files(root) if path.name in MANIFEST_LANGUAGES or path.name in WORKSPACE_MANIFESTS]
    return sorted(found,key=lambda p:_rel(root,p))
def _refs(p: Path, data: Any) -> list[str]:
    if p.name=="package.json":
        work=data.get("workspaces",[]); return work.get("packages",[]) if isinstance(work,dict) else work
    if p.name=="pom.xml": return _maven_refs(data)
    if p.name.startswith("settings.gradle") or p.name.startswith("build.gradle"): return [x.replace(":","/").lstrip("/") for x in re.findall(r"include\s*[('\"]+\s*([^'\")]+)",data)]
    if p.name=="go.work": return re.findall(r"\./[^\s)]+",data)
    return []
def _safe_parse(root: Path, manifests: Sequence[Path]) -> tuple[dict[Path,Any],list[str]]:
    parsed={}; errors=[]
    for p in manifests:
        try:
            value=_parse(p)
            if p.name in {"package.json","pyproject.toml"} and not isinstance(value,dict): raise ValueError
            if p.name=="pom.xml": _validate_maven(value)
            if p.name=="package.json" and "workspaces" in value:
                work=value["workspaces"]
                refs=work.get("packages") if isinstance(work,dict) else work
                if not isinstance(refs,list) or not all(isinstance(item,str) for item in refs): raise ValueError
            parsed[p]=value
        except (json.JSONDecodeError, tomllib.TOMLDecodeError, ET.ParseError, StrictJsonError, ValueError): errors.append(f"invalid manifest: {_rel(root,p)}")
    return parsed,errors
def _id(rel: str) -> str:
    if rel==".": return "root"
    return "root--"+"--".join(part.encode("utf-8").hex() for part in rel.split("/"))
def _question(mid:str, field:str, values:dict[str,list[str]])->dict[str,Any]:
    return {"id":f"module:{mid}:{field}","field":f"modules.{mid}.{field}","impact":"Определяет шаблон генерации и средство запуска тестов","options":[{"id":v,"value":v,"evidence":sorted(values[v])} for v in sorted(values)]}
def _test_values(lang: str, texts: Mapping[str, str]) -> dict[str, list[str]]:
    """Test framework values found in lowered build files, with their evidence (path:marker.value)."""
    text = "\n".join(texts.values())
    values = {v: [f"{rel}:marker.{v}" for rel, body in texts.items() if needle in body] for needle, v in MARKERS[lang][1] if needle in text}
    if lang == "java" and "junit5" in values and "junit-jupiter" not in text and _JUNIT4_MARKERS.search(text):
        values.pop("junit5")
    return values
def module_test_evidenced(project_dir: Path, module: Mapping[str, Any]) -> bool:
    """FaT-H2b P2b: is the module's test.framework seen in its own build files (not taken by default)?"""
    lang = (module.get("stack") or {}).get("language")
    framework = (module.get("test") or {}).get("framework")
    if lang not in MARKERS or lang == "go" or framework is None:
        return True
    root = Path(project_dir).resolve()
    texts = {}
    for rel in module.get("detected_from") or []:
        path = root / str(rel)
        if _ok(root, path) and path.is_file():
            texts[str(rel)] = _read(path).lower()
    return framework in _test_values(lang, texts)
_COMPILER_LEVEL_FIELDS = ("release", "testRelease", "source", "testSource", "target", "testTarget")
_GRADLE_LEVEL = (
    (re.compile(r"\b(?:source|target)Compatibility\s*=\s*(?:JavaVersion\.VERSION_)?['\"]?(1[._]\d+|\d+)"), "{}"),
    (re.compile(r"\blanguageVersion(?:\.set\(\s*|\s*=\s*)JavaLanguageVersion\.of\(\s*(\d+)\s*\)"), "toolchain {}"),
    (re.compile(r"\bjvmToolchain\(\s*(\d+)\s*\)"), "jvmToolchain {}"),
    (re.compile(r"\brelease(?:\.set\(\s*|\s*=\s*)(\d+)"), "options.release {}"),
)
def _java_version(value: str | None) -> int | None:
    """``1.8``/``8``/``VERSION_1_8`` → 8; anything else (an unresolved property) → None."""
    match = re.fullmatch(r"\s*(?:1[._])?(\d+)\s*", value or "")
    return int(match.group(1)) if match and int(match.group(1)) >= 5 else None
def _pom_levels(pom: Path, inherited: Mapping[str, str], external: str | None = None) -> tuple[list[tuple[int, str]], dict[str, str]]:
    """Java levels a pom states: compiler properties and plugin configuration, animal-sniffer java1N when it checks test classes;
    under an external parent (``external``: its artifactId) also ``java.version``/``java.release.version`` — the convention of
    spring-boot-starter-parent, mybatis-parent and their like, which pass it to the compiler."""
    data = ET.fromstring(_read(pom))
    own = {_xml_name(item): (item.text or "").strip() for block in data if _xml_name(block) == "properties" for item in block}
    properties = {**inherited, **own}
    def resolve(text: str | None) -> str | None:
        for _ in range(3):
            match = re.fullmatch(r"\s*\$\{([^}]+)\}\s*", text or "")
            if not match: return text
            text = properties.get(match.group(1))
        return None
    levels: list[tuple[int, str]] = []
    for name in ("maven.compiler." + field for field in _COMPILER_LEVEL_FIELDS):
        level = _java_version(resolve(own.get(name)))
        if level is not None: levels.append((level, f"{pom.name}: {name}={properties[name]}"))
    for name in ("java.version", "java.release.version") if external else ():
        level = _java_version(resolve(own.get(name)))
        if level is not None: levels.append((level, f"{pom.name}: {name}={own[name]} (родитель {external})"))
    for plugin in (node for node in data.iter() if _xml_name(node) == "plugin"):
        artifact = next(((child.text or "").strip() for child in plugin if _xml_name(child) == "artifactId"), "")
        configurations = [node for node in plugin.iter() if _xml_name(node) == "configuration"]
        if artifact == "maven-compiler-plugin":
            for configuration in configurations:
                for item in configuration:
                    level = _java_version(resolve(item.text)) if _xml_name(item) in _COMPILER_LEVEL_FIELDS else None
                    if level is not None: levels.append((level, f"{pom.name}: maven-compiler-plugin {_xml_name(item)}={(item.text or '').strip()}"))
        elif artifact == "animal-sniffer-maven-plugin":
            plugin_level = [item for item in plugin if _xml_name(item) == "configuration"]
            for configuration in configurations:
                for signature in (node for node in configuration.iter() if _xml_name(node) == "signature"):
                    name = next(((child.text or "").strip() for child in signature if _xml_name(child) == "artifactId"), "")
                    match = re.fullmatch(r"java1(\d)", name)
                    tests = any((node.text or "").strip() == "true" for scope in [configuration, *plugin_level] for node in scope.iter() if _xml_name(node) == "checkTestClasses")
                    if match and tests: levels.append((int(match.group(1)), f"{pom.name}: animal-sniffer {name} (checkTestClasses)"))
    return levels, properties
def _reactor_chain(root: Path) -> tuple[list[Path], str | None]:
    """The module pom and its reactor parents (each lists its child), and the artifactId of the external parent at the top."""
    chain, pom, external = [], root / "pom.xml", None
    while pom.is_file() and len(chain) < 6:
        chain.append(pom)
        try: data = ET.fromstring(_read(pom))
        except ET.ParseError: break
        parent = next((child for child in data if _xml_name(child) == "parent"), None)
        relative = next(((child.text or "").strip() for child in parent if _xml_name(child) == "relativePath"), "../pom.xml") if parent is not None else ""
        candidate = (pom.parent / (relative or "../pom.xml")).resolve()
        candidate = candidate / "pom.xml" if candidate.is_dir() else candidate
        try: listed = {(candidate.parent / ref).resolve() for ref in _maven_refs(ET.fromstring(_read(candidate)))} if parent is not None and candidate.is_file() else set()
        except ET.ParseError: listed = set()
        if pom.parent.resolve() not in listed:  # only a reactor parent of this project, never a pom outside it
            external = next(((child.text or "").strip() for child in parent if _xml_name(child) == "artifactId"), None) if parent is not None else None
            break
        pom = candidate
    return chain, external
_JUNIT_PARAMS_ARTIFACTS = frozenset({"junit-jupiter", "junit-jupiter-params", "spring-boot-starter-test"})
_BOOT_TEST_STARTER = re.compile(r"spring-boot-starter-[\w.-]+-test")  # Spring Boot 4 module test starters pull spring-boot-starter-test
def junit_params_available(module_root: Path) -> bool:
    """FaT-PT: does the module's build give its tests junit-jupiter-params — a dependency (not a managed version) on
    junit-jupiter, junit-jupiter-params, spring-boot-starter-test or a Boot 4 `spring-boot-starter-*-test` in the module pom, its reactor parents or build.gradle.
    Read only; an external parent pom is not read, so what only it adds counts as absent."""
    root = Path(module_root).resolve()
    for pom in _reactor_chain(root)[0]:
        try: data = ET.fromstring(_read(pom))
        except ET.ParseError: continue
        for dependencies in (child for child in data.iter() if _xml_name(child) == "dependencies"):
            if any(_xml_name(node) == "dependencyManagement" for node in data.iter() if dependencies in list(node)):
                continue
            for dependency in dependencies:
                artifact = next(((child.text or "").strip() for child in dependency if _xml_name(child) == "artifactId"), "")
                if artifact in _JUNIT_PARAMS_ARTIFACTS or _BOOT_TEST_STARTER.fullmatch(artifact):
                    return True
    for name in ("build.gradle", "build.gradle.kts"):
        text = _read(root / name) if (root / name).is_file() else ""
        if re.search(r"""['"](?:org\.junit\.jupiter:junit-jupiter(?:-params)?|org\.springframework\.boot:spring-boot-starter-test)\b""", text):
            return True
    return False
def java_api_level(module_root: Path) -> dict[str, Any] | None:
    """FaT-JL: the Java level the module's build compiles and checks its tests against — ``{"level", "evidence"}`` or None.

    Read from the build files only, never by running them: Maven ``maven.compiler.release/source/target`` (and ``test*``)
    in properties or the compiler plugin, an animal-sniffer ``java1N`` signature that checks test classes, the parent poms
    of the reactor that list this module; Gradle ``sourceCompatibility``/``targetCompatibility``, toolchain, ``release``.
    Several values (profiles, executions) give the lowest: an API allowed by it compiles under every one of them."""
    root = Path(module_root).resolve()
    levels: list[tuple[int, str]] = []
    chain, external = _reactor_chain(root)
    properties: dict[str, str] = {}
    for index, pom in enumerate(reversed(chain)):
        try: found, properties = _pom_levels(pom, properties, external)
        except ET.ParseError: continue
        prefix = "" if index == len(chain) - 1 else f"{pom.parent.name}/"
        levels += [(level, prefix + evidence) for level, evidence in found]
    for name in ("build.gradle", "build.gradle.kts"):
        text = _read(root / name) if (root / name).is_file() else ""
        for pattern, label in _GRADLE_LEVEL:
            for match in pattern.finditer(text):
                level = _java_version(match.group(1))
                if level is not None: levels.append((level, f"{name}: {label.format(match.group(1))}"))
    if not levels:
        return None
    lowest = min(level for level, _evidence in levels)
    return {"level": lowest, "evidence": list(dict.fromkeys(evidence for level, evidence in levels if level == lowest))[:3]}
def _source_dir_has_code(root: Path, directory: Path, suffixes: frozenset[str]) -> bool:
    for current, directories, files in os.walk(directory, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(name for name in directories if not is_ignored_dir(_parts(root, parent), name) and _usable_dir(root, parent / name))
        for name in sorted(files):
            path = parent / name
            if path.suffix in suffixes and not _is_reparse(path) and _ok(root, path) and path.is_file():
                return True
    return False
def _paths(root:Path, base:Path)->dict[str,list[str]]:
    """Return source/test/resource dirs relative to the module root, not the project."""
    sets={"source":("src","src/main/java","src/main/kotlin"),"tests":("tests","test","src/test/java","src/test/kotlin"),"resources":("resources","src/main/resources","src/test/resources")}; out={}
    for k,names in sets.items():
        vals=sorted(name.replace("\\", "/") for name in names if _usable_dir(root, base/name))
        if vals: out[k]=vals
    return out
def _has_code(root:Path,p:Path)->bool:
    suffixes=frozenset().union(*SOURCE_SUFFIXES.values())
    return any(_source_dir_has_code(root,p/name,suffixes) for name in ("src","src/main/java","src/main/kotlin") if _usable_dir(root,p/name))
def _module_readiness(module: dict[str, Any]) -> str:
    paths = module.get("paths") if isinstance(module.get("paths"), dict) else {}
    if module.get("_source_ready"):
        return "source_ready"
    if paths.get("source") or paths.get("tests") or paths.get("resources"):
        return "needs_source"
    return "manifest_detected"
def _source_question(module: dict[str, Any]) -> dict[str, Any]:
    mid = module["id"]
    evidence: list[str] = []
    paths = module.get("paths") if isinstance(module.get("paths"), dict) else {}
    root = module.get("root") or "."
    for name in paths.get("tests") or []:
        evidence.append(f"{name}:tests-only" if root in {".", ""} else f"{root}/{name}:tests-only")
    if not evidence:
        evidence.extend(f"{item}:manifest" for item in module.get("detected_from") or [] if isinstance(item, str))
    if not evidence:
        evidence.append("manifest:detected")
    return {
        "id": f"module:{mid}:paths.source",
        "field": f"modules.{mid}.paths.source",
        "impact": "Следующий этап не может собрать непустой source snapshot без объявленного source root",
        "options": [
            {"id": "provide-source-root", "value": "provide-source-root", "evidence": sorted(set(evidence))},
        ],
    }
def analyze_module_roots(root:Path, manifests:Sequence[Path])->tuple[list[dict[str,Any]],list[dict[str,Any]],list[str],list[str],set[str]]:
    parsed,errors=_safe_parse(root,manifests)
    by={}
    for p in parsed:
        if p.name not in WORKSPACE_MANIFESTS: by.setdefault(p.parent,[]).append(p)
    modules=[]; qs=[]; unresolved=set()
    for base,owned in sorted(by.items(),key=lambda x:_rel(root,x[0])):
        # A workspace declaration with no real confined code is evidence-only even when nested.
        all_here=[p for p in parsed if p.parent==base]
        if any(_refs(p,parsed[p]) for p in all_here) and not _has_code(root,base): continue
        rel=_rel(root,base); mid=_id(rel); languages={}
        for p in owned:
            lang=manifest_language(p.name); languages.setdefault(lang,[]).append(f"{_rel(root,p)}:manifest")
        stack={}
        if len(languages)==1: stack["language"]=next(iter(languages))
        else:
            field=f"modules.{mid}.stack.language"; qs.append(_question(mid,"stack.language",languages)); unresolved.add(field)
        lang=stack.get("language"); tool_values={}
        if lang=="java":
            for p in owned: tool_values.setdefault("gradle" if p.name.startswith("build.gradle") else "maven",[]).append(f"{_rel(root,p)}:build-tool")
        elif lang: tool_values={MARKERS[lang][2]:[f"{_rel(root,p)}:build-tool" for p in owned]}
        if len(tool_values)==1: stack["build_tool"]=next(iter(tool_values))
        elif tool_values:
            field=f"modules.{mid}.stack.build_tool"; qs.append(_question(mid,"stack.build_tool",tool_values)); unresolved.add(field)
        module={"id":mid,"root":rel,"stack":stack,"detected_from":sorted(_rel(root,p) for p in owned)}
        paths=_paths(root,base)
        if paths: module["paths"]=paths
        suffixes=SOURCE_SUFFIXES.get(lang, frozenset().union(*SOURCE_SUFFIXES.values()))
        module["_source_ready"]=any(_source_dir_has_code(root,base/name,suffixes) for name in paths.get("source",[]))
        if lang:
            fm,tm,_=MARKERS[lang]; texts={p:_read(p).lower() for p in owned}; text="\n".join(texts.values()); vals={}
            for needle,v in fm:
                hits=[f"{_rel(root,p)}:marker.{v}" for p in owned if needle in texts[p]]
                if hits: vals.setdefault(v,[]).extend(hits)
            vals={v:sorted(set(hits)) for v,hits in vals.items()}
            stack["framework"] = next(iter(vals)) if len(vals) == 1 else "unknown"
            supported_test = {"python": "pytest", "java": "junit5"}.get(lang)
            tv=_test_values(lang,{_rel(root,p):texts[p] for p in owned})
            if supported_test:
                tv = {value: evidence for value, evidence in tv.items() if value == supported_test}
            if lang=="go": tv={"go-testing":[f"{_rel(root,p)}:go-testing" for p in owned]}
            test={}
            if len(tv)==1:test["framework"]=next(iter(tv))
            elif tv:
                field=f"modules.{mid}.test.framework"; qs.append(_question(mid,"test.framework",tv)); unresolved.add(field)
            elif supported_test:
                field=f"modules.{mid}.test.framework"
                qs.append(_question(mid, "test.framework", {supported_test: ["host:supported-runner"]}))
                unresolved.add(field)
            wrappers={}
            if len(tool_values)==1:
                build=next(iter(tool_values)); choices=(("windows",("mvnw.cmd",) if build=="maven" else ("gradlew.bat",)),("linux",("mvnw",) if build=="maven" else ("gradlew",)))
                for platform,names in choices:
                    for name in names:
                        if (base/name).is_file() and _ok(root,base/name): wrappers[platform]=name; break
            if wrappers:
                test["wrapper"] = wrappers
            if test:
                module["test"] = test
        modules.append(module)
    return modules,qs,[],errors,unresolved
def _module_evidence(modules: Sequence[Mapping[str, Any]] | Sequence[dict[str, Any]]) -> list[str]:
    extra: list[str] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        rel_root = module.get("root") or "."
        prefix = "" if rel_root in {".", ""} else f"{rel_root}/"
        paths = module.get("paths") if isinstance(module.get("paths"), dict) else {}
        for group in paths.values():
            if not isinstance(group, list):
                continue
            extra.extend(f"{prefix}{name}" for name in group if isinstance(name, str))
        wrappers = ((module.get("test") or {}).get("wrapper") or {})
        if isinstance(wrappers, dict):
            extra.extend(f"{prefix}{name}" for name in wrappers.values() if isinstance(name, str))
    return extra
def project_fingerprint(root:Path,evidence_paths:Sequence[str])->str:
    root=root.resolve(); h=hashlib.sha256()
    for rel in sorted(set(evidence_paths)):
        p=root/rel
        if not _ok(root,p) or p.is_symlink() or _is_reparse(p):
            continue
        h.update(rel.encode()); h.update(b"\0")
        if p.is_file():
            h.update(b"file\0"); h.update(p.read_bytes())
        elif p.is_dir():
            h.update(b"dir\0")
        h.update(b"\0")
    return h.hexdigest()
_QUESTION_SUFFIXES=("stack.language","stack.build_tool","test.framework","paths.source")
def validate_report(report:dict[str,Any], unresolved_fields:set[str]|None=None)->list[str]:
    questions=report.get("questions",[])
    errors=["duplicate option id" for q in questions if len({o["id"] for o in q["options"]})!=len(q["options"])]
    if report.get("status") not in {"ready","needs_input","needs_source"}: return errors
    modules={}
    for module in report.get("modules",[]):
        mid=module.get("id")
        if not isinstance(mid,str) or mid in modules: errors.append("duplicate module id")
        else: modules[mid]=module
    if unresolved_fields is None:
        required=("language","build_tool") if report.get("status")=="ready" else ("language",)
        expected={f"modules.{mid}.stack.{field}" for mid,module in modules.items() for field in required
                  if not (field=="build_tool" and module.get("stack",{}).get("language")=="documents")  # FaT-DOC: nothing to build
                  and (not isinstance(module.get("stack",{}).get(field),str) or not module["stack"][field].strip())}
        if report.get("status") == "ready":
            expected |= {
                f"modules.{mid}.test.framework"
                for mid, module in modules.items()
                if module.get("stack", {}).get("language") in {"python", "java"}
                and module.get("test", {}).get("framework") not in {"pytest", "junit5"}
            }
    else: expected=set(unresolved_fields)
    for field in expected:
        if not any(field==f"modules.{mid}.{suffix}" for mid in modules for suffix in _QUESTION_SUFFIXES): errors.append("invalid unresolved field")
    fields=[]
    for question in questions:
        field=question.get("field")
        if not isinstance(field,str) or not any(field==f"modules.{mid}.{suffix}" for mid in modules for suffix in _QUESTION_SUFFIXES): errors.append("invalid question field")
        elif field not in expected: errors.append("question for resolved field")
        fields.append(field)
    if len(fields)!=len(set(fields)): errors.append("duplicate question")
    missing=expected-set(fields)
    if missing: errors.append("missing critical question")
    status=report["status"]
    if not modules: errors.append(f"{status} report has no modules")
    if status=="ready":
        if questions or expected: errors.append("ready report has unresolved critical field")
        if any(module.get("readiness") != "source_ready" for module in modules.values()): errors.append("ready report has module without source")
    if status in {"needs_input","needs_source"} and not questions: errors.append(f"{status} report has no questions")
    return errors
def build_report(status,project_name,modules,questions,warnings,errors,fingerprint=None): return {"status":status,"project_name":project_name,"modules":modules,"questions":questions,"warnings":warnings,"errors":errors,"fingerprint":fingerprint or hashlib.sha256(b"").hexdigest(),"scanned_at":datetime.now(timezone.utc).isoformat()}
DOCUMENT_SUFFIXES = frozenset({".md", ".markdown", ".txt", ".adoc", ".rst"})
def _documents_module(root: Path) -> dict[str, Any] | None:
    """FaT-DOC: a project of requirement documents only — no build manifest and no source file — is one module `documents`
    at the root: cases from its documents (cases-only-v1), nothing to build or run. Any source file keeps the old refusal."""
    from tools.scan_rules import SOURCE_SUFFIXES as ANY_SOURCE

    files = confined_files(root)
    if any(path.suffix.lower() in ANY_SOURCE and path.suffix.lower() not in DOCUMENT_SUFFIXES for path in files):
        return None
    documents = sorted(_rel(root, path) for path in files if path.suffix.lower() in DOCUMENT_SUFFIXES)
    if not documents:
        return None
    return {"id": "root", "root": ".", "stack": {"language": "documents"}, "detected_from": documents[:20], "readiness": "source_ready"}
def discover_project(project_dir:Path)->dict[str,Any]:
    root=project_dir.resolve(); manifests=find_confined_manifests(root)
    if not manifests:
        documents = _documents_module(root)
        if documents is not None:  # FaT-DOC
            return build_report("ready", root.name, [documents], [], [], [], project_fingerprint(root, documents["detected_from"]))
        return build_report("error",root.name,[],[],[],["build manifests were not found"])
    modules,qs,warnings,errors,unresolved=analyze_module_roots(root,manifests); evidence=[_rel(root,p) for p in manifests]
    if errors:return build_report("error",root.name,[],[],warnings,sorted(errors),project_fingerprint(root,evidence))
    if not modules:return build_report("error",root.name,[],[],warnings,["runnable modules were not found"],project_fingerprint(root,evidence))
    modules=sorted(modules,key=lambda m:(m["root"],m["id"]))
    for module in modules:
        module["readiness"]=_module_readiness(module)
        module.pop("_source_ready",None)
    evidence=list(evidence)+_module_evidence(modules)
    source_blockers=[module for module in modules if module.get("readiness")!="source_ready"]
    fingerprint=project_fingerprint(root,evidence)
    source_questions=[_source_question(module) for module in source_blockers]
    questions=sorted(qs+source_questions,key=lambda q:q["id"])
    expected=unresolved|{question["field"] for question in source_questions}
    status="needs_input" if qs else "needs_source" if source_questions else "ready"
    report=build_report(status,root.name,modules,questions,warnings,[],fingerprint); semantic=validate_report(report,expected)
    if semantic: return build_report("error",root.name,[],[],warnings,semantic,report["fingerprint"])
    return report
def main()->int:
    p=argparse.ArgumentParser(description="Read-only project module discovery; never updates .skillsrc.");p.add_argument("--project",required=True);p.add_argument("--output");a=p.parse_args();root=Path(a.project).resolve();r=discover_project(root) if root.is_dir() else build_report("error",root.name,[],[],[],["--project does not exist"])
    if a.output:
        try: atomic_write_confined_bytes(root,a.output,json.dumps(r,ensure_ascii=False,indent=2).encode("utf-8"))
        except (OSError, ValueError) as error:p.error(str(error))
    print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r["status"]!="error" else 1
if __name__=="__main__":raise SystemExit(main())
