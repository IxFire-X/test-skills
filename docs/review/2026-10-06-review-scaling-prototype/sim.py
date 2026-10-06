import json, math, sys
sys.path.insert(0, '.')
from projection import case_text, cap_signature

KB = 1024
BUDGET = 200_000; RESERVE = 20_000; OVERHEAD = 3_000
USABLE = BUDGET - RESERVE - OVERHEAD
OUT_BPS = 200          # measured: 150-260 bytes/s of reviewer output in step5
FIXED_S = 60           # reading, thinking, subagent start
ROW = 250              # proposed coverage row: status + up to 3 refs + short note
FINDINGS = 6_000       # findings per part (step5 parts carried 2.5-10 KB)

def blocks(sizes, cap):
    out, cur, used = [], [], 0
    for s in sizes:
        if cur and used + s > cap:
            out.append(cur); cur, used = [], 0
        cur.append(s); used += s
    if cur: out.append(cur)
    return out

def part_time(rows):
    return FIXED_S + (rows * ROW + FINDINGS) / OUT_BPS

def simulate(name, path, method_sizes, support_code):
    d = json.load(open(path)); doc = d.get('document', d)
    cases = [len(case_text(c).encode()) for c in doc['test_cases']]
    n = len(cases)
    creq = sum(len(f"{r['requirement_id']}: {r['text']}\n".encode()) for r in doc['requirements'])
    sreq = sum(len(s['text'].encode()) for s in doc['source_requirements'])
    caps_sig = sum(len(cap_signature(c).encode()) + 1 for c in doc['operation_capabilities'])
    caps_full = sum(len(json.dumps(c, ensure_ascii=False).encode()) for c in doc['operation_capabilities'])
    shared = creq + caps_sig + 2_000          # + deterministic lint flags
    # --- canonical review ---
    source_parts = math.ceil((sreq + creq + caps_full) / USABLE)
    if sum(cases) + sreq + caps_full <= USABLE - shared:   # whole review fits one part
        source_parts = 0
    if sum(cases) <= USABLE - shared:
        full_parts, b = 1, 1
    else:
        bl = blocks(cases, (USABLE - shared) // 2); b = len(bl)
        full_parts = b * (b - 1) // 2
    pruned_parts = len(blocks(cases, USABLE - shared)) + 1      # + one compact index part
    canon_full = source_parts + full_parts
    canon_pruned = source_parts + pruned_parts
    rows_per_part = n / max(1, full_parts) * (2 if b > 1 else 1) + 3
    t_full = canon_full * part_time(rows_per_part)
    t_pruned = canon_pruned * part_time(n / max(1, pruned_parts - 1) + 3)
    # --- automation review: per-case method slice, support code once, no pair scopes ---
    auto_case = [c + m for c, m in zip(cases, method_sizes)]
    auto_shared = caps_sig + 3_000
    if sum(auto_case) + support_code + caps_full <= USABLE - auto_shared:
        auto_parts = 1
    else:
        auto_parts = len(blocks(auto_case, USABLE - auto_shared)) + math.ceil((support_code + caps_full) / USABLE)
    t_auto = auto_parts * part_time(n / max(1, auto_parts - 1) + 3)
    print(f"== {name}: {n} cases, review projection {sum(cases)//KB} KB (avg {sum(cases)//n} B), CREQ {creq//KB} KB, SREQ {sreq//KB} KB, capabilities {caps_full//KB} KB")
    print(f"   canonical review, all pairs kept:  {canon_full} parts (source/capabilities {source_parts} + case blocks: b={b} -> {full_parts})  ~{t_full/60:.0f} min sequential")
    print(f"   canonical review, pairs pruned by lint: {canon_pruned} parts  ~{t_pruned/60:.0f} min")
    print(f"   automation review (slices, no pairs): {auto_parts} parts  ~{t_auto/60:.0f} min")
    return canon_full, auto_parts

if __name__ == "__main__":
    # python sim.py <name> <canonical.json> <test_file_bytes> <helper_bytes>
    # step5:     TCDOC-step5-java-demo-9340016c.r1.json 11137 3400
    # Petclinic: docs/examples/petclinic-owner-lifecycle/cases.json 250016 33644  (сентябрьский класс, 4288 строк)
    name, path, file_bytes, helpers = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
    d = json.load(open(path, encoding="utf-8")); n = len(d.get("document", d)["test_cases"])
    simulate(name, path, [(file_bytes - helpers) // n] * n, helpers)
