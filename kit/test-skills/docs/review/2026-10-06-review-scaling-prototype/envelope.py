import json, sys, copy
from projection import case_text

def build(doc, title):
    L = [f"# Конверт ревью кейсов: {doc['document_id']} r{doc['revision']} ({title})", "",
         "Всё, что нужно для ревью, находится в этом конверте. Другие файлы не читай.", "",
         "## A. Исходные требования (SREQ, дословно из спецификации)"]
    for s in doc["source_requirements"]:
        L.append(f"[{s['source_requirement_id']}]\n{s['text'].strip()}\n")
    L.append("## B. Канонические требования (CREQ) и связь SREQ → CREQ")
    for r in doc["requirements"]:
        L.append(f"{r['requirement_id']}: {r['text']}")
    L.append("")
    for m in doc["source_to_canonical_mappings"]:
        L.append(f"{m['source_requirement_id']} → {', '.join(m['canonical_requirement_ids'])}")
    L.append("\n## C. Возможности (как тест вызывает приложение)")
    for c in doc["operation_capabilities"]:
        args = ", ".join(f"{a['name']}{'' if a.get('required') else '?'}:{a['semantic_type'].get('type')}" for a in c.get("arguments", []))
        L.append(f"{c['capability_id']} action={c.get('action')} adapter={c.get('adapter')} args=({args})")
        for p in c.get("provenance", []): L.append(f"  - {p}")
    L.append("\n## D. Кейсы")
    for c in doc["test_cases"]:
        L.append(case_text(c)); L.append("")
    return "\n".join(L)

if __name__ == "__main__":
    # python envelope.py <canonical.json> <out.md> real|seeded
    src, out, mode = sys.argv[1], sys.argv[2], sys.argv[3]
    d = json.load(open(src)); doc = d.get("document", d)
    if mode == "seeded":
        doc = copy.deepcopy(doc)
        tc = {c["case_id"]: c for c in doc["test_cases"]}
        # S1 local: assertion literal differs from input and expectation text
        tc["TC-B1-005"]["steps"][0]["expectations"][0]["assertions"][1]["expected"]["value"]["id"] = 8
        # S2 source + cross: path-variable case expects the query-param name, consistently in text and assertion
        e = tc["TC-B1-004"]["steps"][0]["expectations"][0]
        e["text"] = e["text"].replace("Ramsesh", "Ramesh"); e["assertions"][1]["expected"]["value"]["firstName"] = "Ramesh"
        # S3 source: confirmation text changed consistently in text and assertion
        e = tc["TC-B1-010"]["steps"][0]["expectations"][0]
        e["text"] = e["text"].replace("Student Successfully Deleted!", "Student Deleted!"); e["assertions"][1]["expected"]["value"] = "Student Deleted!"
        # S5 mechanical: wrong component label
        tc["TC-B1-010"]["management"]["components"] = ["HelloWorldController"]
        # S4 cross/state: list grows after create, case retitled consistently, still linked to the stateless requirement
        c = tc["TC-B1-012"]
        c["title"] = "Созданный студент появляется в списке GET /students"
        c["objective"] = "Убедиться, что после создания студента список GET /students содержит нового студента."
        five = [{"firstName": "Ramesh", "id": 1, "lastName": "Mishra"}, {"firstName": "Umesh", "id": 2, "lastName": "Mishra"},
                {"firstName": "Ram", "id": 3, "lastName": "Mishra"}, {"firstName": "Sanjay", "id": 4, "lastName": "Mishra"},
                {"firstName": "Pavel", "id": 9, "lastName": "Orlov"}]
        for st in c["steps"][1:]:
            ex = st["expectations"][0]
            ex["text"] = "В списке пять студентов: четверо исходных и созданный студент с id 9.\n\nHTTP 200 OK\n\n" + json.dumps(five, ensure_ascii=False, indent=2)
            ex["assertions"][1]["expected"]["value"] = five
        # S6 coverage: drop the only case for GET /hello-world
        doc["test_cases"] = [c for c in doc["test_cases"] if c["case_id"] != "TC-B1-011"]
    text = build(doc, mode)
    open(out, "w", encoding="utf-8").write(text)
    print(out, len(text.encode()), "bytes")
