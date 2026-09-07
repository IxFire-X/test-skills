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
        is_ignored_dir_name,
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
        is_ignored_dir_name,
        manifest_language,
    )
    from schema_validation import StrictJsonError, load_json_strict

WORKSPACE_MANIFESTS = {"settings.gradle", "settings.gradle.kts", "go.work"}
MARKERS = {"python": (PYTHON_FRAMEWORK_MARKERS, PYTHON_TEST_MARKERS, "pip"), "java": (JAVA_FRAMEWORK_MARKERS, JAVA_TEST_MARKERS, "maven"), "typescript": (JS_FRAMEWORK_MARKERS, JS_TEST_MARKERS, "npm"), "go": (GO_FRAMEWORK_MARKERS, GO_TEST_MARKERS, "go-mod")}
SOURCE_SUFFIXES = {"python": frozenset({".py"}), "java": frozenset({".java"}), "typescript": frozenset({".ts", ".tsx", ".js", ".jsx"}), "go": frozenset({".go"})}
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
def _source_dir_has_code(root: Path, directory: Path, suffixes: frozenset[str]) -> bool:
    for current, directories, files in os.walk(directory, followlinks=False):
        parent = Path(current)
        directories[:] = sorted(name for name in directories if not is_ignored_dir_name(name) and _usable_dir(root, parent / name))
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
            tv={v:[f"{_rel(root,p)}:marker.{v}" for p in owned if needle in texts[p]] for needle,v in tm if needle in text}
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
        expected={f"modules.{mid}.stack.{field}" for mid,module in modules.items() for field in required if not isinstance(module.get("stack",{}).get(field),str) or not module["stack"][field].strip()}
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
def discover_project(project_dir:Path)->dict[str,Any]:
    root=project_dir.resolve(); manifests=find_confined_manifests(root)
    if not manifests:return build_report("error",root.name,[],[],[],["build manifests were not found"])
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
