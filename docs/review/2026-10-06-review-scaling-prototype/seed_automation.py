"""Seeded defects for the single-part autotest review experiment (2026-10-06).

Usage: python seed_automation.py <StudentControllerPipelineTest.java> <TCDOC-...9340016c.r1.json> <out.md>
Builds one envelope: capability contract, accepted cases (review projection) and the test file with
four injected defects and line numbers. Prototype for measurement only, not production code.
"""
import json, sys
from projection import case_text

java_path, canonical_path, out_path = sys.argv[1:4]
J = open(java_path, encoding="utf-8").read()
SEEDS = [
    # A1 literal: status 400 -> 404 in TC-B1-006
    ('assertThat(response.getStatus()).as("ASSERT-B1-006-01-1-1").isEqualTo(400);',
     'assertThat(response.getStatus()).as("ASSERT-B1-006-01-1-1").isEqualTo(404);'),
    # A2 input: path id 10 -> 42 in TC-B1-009
    ('exchange("PUT", "/students/10/update", null, body);', 'exchange("PUT", "/students/42/update", null, body);'),
    # A3 input data: the partial body gets an explicit lastName in TC-B1-008
    ('        body.put("firstName", "Olga");\n', '        body.put("firstName", "Olga");\n        body.put("lastName", null);\n'),
    # A4 support code: the shared helper returns three students (breaks TC-B1-002 and TC-B1-012)
    ('                student(3, "Ram", "Mishra"),\n                student(4, "Sanjay", "Mishra"));',
     '                student(3, "Ram", "Mishra"));'),
]
for before, after in SEEDS:
    assert before in J, before[:50]
    J = J.replace(before, after)
d = json.load(open(canonical_path)); doc = d.get("document", d)
L = ["# Конверт ревью автотестов (эксперимент)", "", "## A. Возможность"]
for c in doc["operation_capabilities"]:
    L.append(f"{c['capability_id']} action={c['action']} adapter={c['adapter']}")
    L.extend(f"  - {p}" for p in c["provenance"])
L.append("\n## B. Принятые кейсы")
for c in doc["test_cases"]:
    L += [case_text(c), ""]
L.append("## C. Сгенерированный файл (с номерами строк)")
L += [f"{i:4d}| {line}" for i, line in enumerate(J.split("\n"), 1)]
open(out_path, "w", encoding="utf-8").write("\n".join(L))
