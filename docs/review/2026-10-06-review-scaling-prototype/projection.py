"""Prototype: compact, ID-anchored review projection of a canonical test document (measurement only)."""
import json, sys

def lit(v):
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))

def val(src):
    k = src.get("kind")
    if k == "literal": return lit(src.get("value"))
    if k in ("output", "step_output", "project_result"): return f"{k}:{src.get('name') or src.get('output_id') or ''}"
    return lit(src)

def case_text(c):
    out = [f"{c['case_id']} [{c.get('priority')}; {','.join(c.get('categories', []))}] {c['title']}",
           f" reqs: {', '.join(c['requirement_ids'])}",
           f" objective: {c['objective']}"]
    for p in c.get("preconditions", []): out.append(f" pre: {p}")
    m = c.get("management") or {}
    if m.get("components") or m.get("folder"): out.append(f" mgmt: folder={m.get('folder')} components={','.join(m.get('components') or [])}")
    for s in c["steps"]:
        op = s.get("operation") or {}
        args = ", ".join(f"{i['target']['name']}={val(i['source'])}" for i in s.get("inputs", []))
        out.append(f" {s['step_id']}: {s['action']}")
        if s.get("test_data"): out.append(f"   data: {s['test_data']}")
        out.append(f"   op: {op.get('capability_id') or op.get('kind')}({args})")
        if s.get("manual_only"): out.append(f"   MANUAL: {s.get('manual_reason')}")
        for b in s.get("automation_blockers", []): out.append(f"   BLOCKER: {lit(b)}")
        for o in s.get("outputs", []): out.append(f"   out: {lit(o)}")
        for e in s["expectations"]:
            out.append(f"   {e['expectation_id']}: {e['text']}")
            for a in e["assertions"]:
                act = a["actual"]; exp = a.get("expected")
                out.append(f"     {a['assertion_id']}: {act.get('kind')}:{act.get('name')} {a['operator']} {val(exp) if isinstance(exp, dict) else lit(exp)}")
    return "\n".join(out)

def cap_signature(cp):
    args = ", ".join(f"{a['name']}{'' if a.get('required') else '?'}" for a in cp.get("arguments", []))
    first = (cp.get("provenance") or [""])[0]
    return f"{cp['capability_id']} {cp.get('action')}({args}) adapter={cp.get('adapter')}"

if __name__ == "__main__":
    d = json.load(open(sys.argv[1])); doc = d.get("document", d)
    cases = [case_text(c) for c in doc["test_cases"]]
    sizes = [len(t.encode()) for t in cases]
    reqs = "\n".join(f"{r['requirement_id']}: {r['text']}" for r in doc["requirements"])
    caps_sig = "\n".join(cap_signature(c) for c in doc["operation_capabilities"])
    caps_full = sum(len(json.dumps(c, ensure_ascii=False).encode()) for c in doc["operation_capabilities"])
    sreq = sum(len(s["text"].encode()) for s in doc["source_requirements"])
    js = sum(len(json.dumps(c, ensure_ascii=False, separators=(",", ":")).encode()) for c in doc["test_cases"])
    print(f"cases={len(cases)} projection total={sum(sizes)} avg={sum(sizes)//len(sizes)} max={max(sizes)}  (compact JSON cases={js}, ratio {js/sum(sizes):.1f}x)")
    print(f"CREQ text={len(reqs.encode())}  SREQ text={sreq}  capability signatures={len(caps_sig.encode())}  capabilities full JSON={caps_full}")
    if len(sys.argv) > 2: print(cases[int(sys.argv[2])])
