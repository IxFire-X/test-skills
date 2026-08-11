#!/usr/bin/env python3
"""Read-only deterministic inventory of runnable project modules."""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys, tomllib, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
if __package__:
    from .stack_catalog import *
else:
    from stack_catalog import *

WORKSPACE_MANIFESTS = {"settings.gradle", "settings.gradle.kts", "go.work"}
MARKERS = {"python": (PYTHON_FRAMEWORK_MARKERS, PYTHON_TEST_MARKERS, "pip"), "java": (JAVA_FRAMEWORK_MARKERS, JAVA_TEST_MARKERS, "maven"), "typescript": (JS_FRAMEWORK_MARKERS, JS_TEST_MARKERS, "npm"), "go": (GO_FRAMEWORK_MARKERS, GO_TEST_MARKERS, "go-mod")}
def _rel(root: Path, p: Path) -> str: return p.relative_to(root).as_posix() or "."
def _ok(root: Path, p: Path) -> bool:
    try: p.resolve().relative_to(root); return True
    except ValueError: return False
def _read(p: Path) -> str:
    try: return p.read_text(encoding="utf-8", errors="replace")
    except OSError: return ""
def _parse(p: Path) -> Any:
    text = _read(p)
    if p.name == "package.json": return json.loads(text)
    if p.name == "pyproject.toml": return tomllib.loads(text)
    if p.name == "pom.xml": return ET.fromstring(text)
    return text
def find_confined_manifests(root: Path) -> list[Path]:
    found=[]
    for current, dirs, files in os.walk(root, followlinks=False):
        base=Path(current); dirs[:]=sorted(d for d in dirs if d not in IGNORED_DIR_NAMES and not (base/d).is_symlink())
        found += [base/f for f in sorted(files) if f in MANIFEST_LANGUAGES or f in WORKSPACE_MANIFESTS if not (base/f).is_symlink() and _ok(root,base/f)]
    return sorted(found,key=lambda p:_rel(root,p))
def _refs(p: Path, data: Any) -> list[str]:
    if p.name=="package.json":
        work=data.get("workspaces",[]); return work.get("packages",[]) if isinstance(work,dict) else work
    if p.name=="pom.xml": return [x.text.strip() for x in data.findall(".//{*}module") if x.text]
    if p.name.startswith("settings.gradle") or p.name.startswith("build.gradle"): return [x.replace(":","/").lstrip("/") for x in re.findall(r"include\s*[('\"]+\s*([^'\")]+)",data)]
    if p.name=="go.work": return re.findall(r"\./[^\s)]+",data)
    return []
def _safe_parse(root: Path, manifests: Sequence[Path]) -> tuple[dict[Path,Any],list[str]]:
    parsed={}; errors=[]
    for p in manifests:
        try:
            value=_parse(p)
            if p.name in {"package.json","pyproject.toml"} and not isinstance(value,dict): raise ValueError
            if p.name=="package.json" and "workspaces" in value:
                work=value["workspaces"]
                refs=work.get("packages") if isinstance(work,dict) else work
                if not isinstance(refs,list) or not all(isinstance(item,str) for item in refs): raise ValueError
            parsed[p]=value
        except (json.JSONDecodeError,tomllib.TOMLDecodeError,ET.ParseError,ValueError): errors.append(f"invalid manifest: {_rel(root,p)}")
    return parsed,errors
def _id(rel: str) -> str:
    if rel==".": return "root"
    return "root--"+"--".join(part.encode("utf-8").hex() for part in rel.split("/"))
def _question(mid:str, field:str, values:dict[str,list[str]])->dict[str,Any]:
    return {"id":f"module:{mid}:{field}","field":f"modules.{mid}.{field}","impact":"Определяет шаблон генерации и средство запуска тестов","options":[{"id":v,"value":v,"evidence":sorted(values[v])} for v in sorted(values)]}
def _paths(root:Path, base:Path)->dict[str,list[str]]:
    sets={"source":("src","src/main/java","src/main/kotlin"),"tests":("tests","test","src/test/java","src/test/kotlin"),"resources":("resources","src/main/resources","src/test/resources")}; out={}
    for k,names in sets.items():
        vals=sorted(_rel(root,base/n) for n in names if (base/n).is_dir() and _ok(root,base/n))
        if vals: out[k]=vals
    return out
def _has_code(root:Path,p:Path)->bool: return bool(_paths(root,p))
def analyze_module_roots(root:Path, manifests:Sequence[Path])->tuple[list[dict[str,Any]],list[dict[str,Any]],list[str],list[str]]:
    parsed,errors=_safe_parse(root,manifests)
    refs=set()
    for p,data in parsed.items():
        for ref in _refs(p,data):
            if isinstance(ref,str) and ".." not in Path(ref).parts:
                for child in sorted(p.parent.glob(ref)) if any(x in ref for x in "*?[") else [(p.parent/ref)]:
                    if child.is_dir() and _ok(root,child): refs.add(child.resolve())
    by={}
    for p in parsed:
        if p.name not in WORKSPACE_MANIFESTS: by.setdefault(p.parent,[]).append(p)
    modules=[]; qs=[]
    for base,owned in sorted(by.items(),key=lambda x:_rel(root,x[0])):
        # A workspace declaration with no real confined code is evidence-only even when nested.
        all_here=[p for p in parsed if p.parent==base]
        if any(_refs(p,parsed[p]) for p in all_here) and not _has_code(root,base): continue
        rel=_rel(root,base); mid=_id(rel); languages={}
        for p in owned:
            lang=manifest_language(p.name); languages.setdefault(lang,[]).append(f"{_rel(root,p)}:manifest")
        stack={}
        if len(languages)==1: stack["language"]=next(iter(languages))
        else: qs.append(_question(mid,"stack.language",languages))
        lang=stack.get("language"); tool_values={}
        if lang=="java":
            for p in owned: tool_values.setdefault("gradle" if p.name.startswith("build.gradle") else "maven",[]).append(f"{_rel(root,p)}:build-tool")
        elif lang: tool_values={MARKERS[lang][2]:[f"{_rel(root,p)}:build-tool" for p in owned]}
        if len(tool_values)==1: stack["build_tool"]=next(iter(tool_values))
        elif tool_values: qs.append(_question(mid,"stack.build_tool",tool_values))
        module={"id":mid,"root":rel,"stack":stack,"detected_from":sorted(_rel(root,p) for p in owned)}
        paths=_paths(root,base)
        if paths: module["paths"]=paths
        if lang:
            fm,tm,_=MARKERS[lang]; text="\n".join(_read(p) for p in owned); vals={}
            for needle,v in fm:
                hits=[f"{_rel(root,p)}:marker.{v}" for p in owned if needle in _read(p).lower()]
                if hits: vals.setdefault(v,[]).extend(hits)
            vals={v:sorted(set(hits)) for v,hits in vals.items()}
            if len(vals)==1: stack["framework"]=next(iter(vals))
            elif vals: qs.append(_question(mid,"stack.framework",vals))
            tv={v:[f"{_rel(root,p)}:marker.{v}" for p in owned if needle in _read(p).lower()] for needle,v in tm if needle in text.lower()}
            if lang=="go": tv={"go-testing":[f"{_rel(root,p)}:go-testing" for p in owned]}
            test={}
            if len(tv)==1:test["framework"]=next(iter(tv))
            elif tv:qs.append(_question(mid,"test.framework",tv))
            wrappers={}
            if len(tool_values)==1:
                build=next(iter(tool_values)); choices=(("windows",("mvnw.cmd",) if build=="maven" else ("gradlew.bat",)),("linux",("mvnw",) if build=="maven" else ("gradlew",)))
                for platform,names in choices:
                    for name in names:
                        if (base/name).is_file() and _ok(root,base/name): wrappers[platform]=name; break
            if wrappers:test["wrapper"]=wrappers
            if test:module["test"]=test
        modules.append(module)
    return modules,qs,[],errors
def project_fingerprint(root:Path,evidence_paths:Sequence[str])->str:
    root=root.resolve(); h=hashlib.sha256()
    for rel in sorted(set(evidence_paths)):
        p=root/rel
        if _ok(root,p) and p.is_file() and not p.is_symlink(): h.update(rel.encode());h.update(b"\0");h.update(p.read_bytes());h.update(b"\0")
    return h.hexdigest()
def validate_report(report:dict[str,Any])->list[str]:
    errors=["duplicate option id" for q in report.get("questions",[]) if len({o["id"] for o in q["options"]})!=len(q["options"])]
    if report.get("status")=="ready" and any(not m.get("stack",{}).get("language") or not m.get("stack",{}).get("build_tool") for m in report.get("modules",[])): errors.append("ready report has unresolved critical field")
    if report.get("status")=="needs_input":
        modules={m.get("id"):m for m in report.get("modules",[])}; fields=[q.get("field") for q in report.get("questions",[])]
        for mid,module in modules.items():
            if not module.get("stack",{}).get("language") and fields.count(f"modules.{mid}.stack.language")!=1: errors.append("missing language question")
        for field in fields:
            parts=field.split(".");
            if len(parts)<3 or parts[1] not in modules: errors.append("dangling question")
    return errors
def build_report(status,project_name,modules,questions,warnings,errors,fingerprint=None): return {"status":status,"project_name":project_name,"modules":modules,"questions":questions,"warnings":warnings,"errors":errors,"fingerprint":fingerprint or hashlib.sha256(b"").hexdigest(),"scanned_at":datetime.now(timezone.utc).isoformat()}
def discover_project(project_dir:Path)->dict[str,Any]:
    root=project_dir.resolve(); manifests=find_confined_manifests(root)
    if not manifests:return build_report("error",root.name,[],[],[],["build manifests were not found"])
    modules,qs,warnings,errors=analyze_module_roots(root,manifests); evidence=[_rel(root,p) for p in manifests]
    if errors:return build_report("error",root.name,[],[],warnings,sorted(errors),project_fingerprint(root,evidence))
    if not modules:return build_report("error",root.name,[],[],warnings,["runnable modules were not found"],project_fingerprint(root,evidence))
    report=build_report("needs_input" if qs else "ready",root.name,sorted(modules,key=lambda m:(m["root"],m["id"])),sorted(qs,key=lambda q:q["id"]),warnings,[],project_fingerprint(root,evidence)); semantic=validate_report(report)
    if semantic: return build_report("error",root.name,[],[],warnings,semantic,report["fingerprint"])
    return report
def main()->int:
    p=argparse.ArgumentParser(description="Read-only project module discovery; never updates .skillsrc.");p.add_argument("--project",required=True);p.add_argument("--output");a=p.parse_args();root=Path(a.project).resolve();r=discover_project(root) if root.is_dir() else build_report("error",root.name,[],[],[],["--project does not exist"])
    if a.output:
        out=Path(a.output).resolve()
        try:out.relative_to(root/"docs"/"to_do")
        except ValueError:p.error("--output must be below exact docs/to_do")
        out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r["status"]!="error" else 1
if __name__=="__main__":raise SystemExit(main())
