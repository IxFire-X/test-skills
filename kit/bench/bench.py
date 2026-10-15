#!/usr/bin/env python3
"""Benchmark of local models for the test-skills review roles, offline, through wrap.py.

    python3 bench.py --pack <test-skills copy> --models glm-5.3,deepseek-0731-flash,qwen3.6-27b,qwen3.8-27b
                     [--wrap ../wrap/wrap.py] [--wrap-config ../wrap/wrap.json] [--tries 3] [--out bench-result]

Parts (live Petclinic run, 2026-10-08): three case-review parts — the smallest, the one nearest 100 KB and the largest
under the run's byte budget — and one autotest-review part.  Each model answers each part through wrap.py exactly as
the package's process runner would call it (argv of preset codex, the composed task on stdin); a rejected answer is
asked again with the rejection appended (as `run --runner process` does), at most --tries times.

Per model and part: accepted (and on which try), the codes a real submit rejected it with, seconds, usage
(input/output tokens, estimated or from the CLI), bytes of input per input token, and the overlap of findings with
run d's (Sonnet) answer — |model ∩ Sonnet| / |Sonnet| on (code, related_ids), plus the counts.
Recommendation per role: the models that pass on the first try on every part of the role, the highest mean overlap.
Writes <out>.md and <out>.json.  Python 3.11+, stdlib and the package only.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import replay  # noqa: E402

ROLE = {"canonical": "tc-reviewer", "r1": "autotest-reviewer"}


def pick_parts() -> list[tuple[str, str, int]]:
    canonical = sorted(replay.base_parts("canonical"), key=lambda row: row[1])
    budget = replay._load("canonical-snapshot.json.gz")["input_byte_budget"]
    small = canonical[0]
    large = max((row for row in canonical if row[1] <= budget), key=lambda row: row[1])
    near = min((row for row in canonical if row not in (small, large)), key=lambda row: abs(row[1] - 100_000))  # three different parts
    automation = sorted(replay.base_parts("r1"), key=lambda row: row[1])
    middle = automation[len(automation) // 2]
    chosen = []
    for key, row in (("canonical", small), ("canonical", near), ("canonical", large), ("r1", middle)):
        if (key, row[0]) not in {(item[0], item[1]) for item in chosen}:
            chosen.append((key, row[0], row[1]))
    return chosen


def ask(wrap: Path, config: str | None, model: str, prompt: str, work: Path) -> tuple[int, str | None, dict, float, str]:
    last = work / "last.txt"
    last.unlink(missing_ok=True)
    argv = [sys.executable, str(wrap), "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "--json", "--model", model,
            "--output-last-message", str(last), "-"]
    env = {**os.environ, **({"WRAP_CONFIG": config} if config else {})}
    started = time.monotonic()
    done = subprocess.run(argv, input=prompt.encode("utf-8"), capture_output=True, env=env, timeout=3600, check=False)
    seconds = time.monotonic() - started
    usage, error = {}, ""
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
        elif event.get("type") == "error":
            error = str(event.get("message", ""))[:300]
    return done.returncode, last.read_text(encoding="utf-8") if last.is_file() else None, usage, seconds, error


def answer_of(text: str | None):
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def run_one(pack: Path, wrap: Path, config: str | None, model: str, key: str, part_id: str, tries: int, work: Path) -> dict:
    base = replay.task_input(pack, key, part_id, work / "inputs")
    prompt, row = base, {"model": model, "role": ROLE[key], "review": key, "part_id": part_id, "input_bytes": len(base.encode("utf-8")), "tries": []}
    for number in range(1, tries + 1):
        code, text, usage, seconds, error = ask(wrap, config, model, prompt, work)
        answer = answer_of(text)
        rejected = ([f"WRAP_EXIT_{code}: {error}"] if code else []) + ([] if answer is not None or code else ["NO_JSON_OBJECT"])
        if not rejected:
            rejected = replay.check(key, part_id, answer)
        row["tries"].append({"try": number, "seconds": round(seconds, 1), "usage": usage, "rejected": rejected})
        if not rejected:
            reference = replay.findings(replay.reference(key, part_id))
            mine = replay.findings(answer)
            row.update(accepted=True, accepted_try=number, overlap=round(len(mine & reference) / len(reference), 3) if reference else (1.0 if not mine else 0.0),
                       findings=len(mine), sonnet_findings=len(reference), common=len(mine & reference))
            break
        # As `run --runner process`: the next process gets the rejection appended to its task.
        prompt = base + "\n\nПредыдущий ответ отклонён проверкой: " + "; ".join(rejected[:6]) + ". Ответь заново строго по схеме."
    else:
        row.update(accepted=False, accepted_try=None, overlap=None)
    first = row["tries"][0]["usage"] if row["tries"] else {}
    row["bytes_per_input_token"] = round(row["input_bytes"] / first["input_tokens"], 2) if first.get("input_tokens") else None
    row["usage_estimated"] = bool(first.get("estimated"))
    row["seconds"] = round(sum(item["seconds"] for item in row["tries"]), 1)
    return row


def recommend(rows: list[dict]) -> dict[str, str | None]:
    best: dict[str, str | None] = {}
    for role in sorted({row["role"] for row in rows}):
        scores = {}
        for model in sorted({row["model"] for row in rows}):
            mine = [row for row in rows if row["role"] == role and row["model"] == model]
            if mine and all(row["accepted"] and row["accepted_try"] == 1 for row in mine):
                scores[model] = statistics.mean(row["overlap"] for row in mine)
        best[role] = max(scores, key=lambda model: (scores[model], -sorted(scores).index(model))) if scores else None
    return best


def markdown(rows: list[dict], best: dict, parts: list) -> str:
    lines = ["# Бенчмарк моделей для ролей ревью test-skills", "",
             "Части: " + ", ".join(f"{key} {part_id} ({size} Б текста части)" for key, part_id, size in parts) + ".", "",
             "| Модель | Роль | Часть | Вход, Б | Принят | Попытка | Отказы | Время, с | Токены вх/вых | Б на токен | Находок (Sonnet) | Совпало | Доля |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        usage = row["tries"][-1]["usage"] if row["tries"] else {}
        rejected = "; ".join(code for item in row["tries"] for code in item["rejected"])[:120] or "—"
        lines.append(f"| {row['model']} | {row['role']} | {row['part_id']} | {row['input_bytes']} | {'да' if row['accepted'] else 'нет'} | {row['accepted_try'] or '—'} | "
                     f"{rejected} | {row['seconds']} | {usage.get('input_tokens', '—')}/{usage.get('output_tokens', '—')}{' (оценка)' if row['usage_estimated'] else ''} | "
                     f"{row['bytes_per_input_token'] or '—'} | {row.get('findings', '—')} ({row.get('sonnet_findings', '—')}) | {row.get('common', '—')} | {row['overlap'] if row['overlap'] is not None else '—'} |")
    lines += ["", "**Рекомендация по ролям** (схема с первой попытки на всех частях роли, наибольшая доля совпадений с Sonnet):", ""]
    lines += [f"- `{role}`: {model or 'нет подходящей модели — проверить обёртку и меньший --review-input-bytes'}" for role, model in best.items()]
    lines += ["", "В `.skillsrc`: `review_runner: {preset: codex, models: {tc-reviewer: <модель>, autotest-reviewer: <модель>}}`.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pack", required=True)
    parser.add_argument("--models", required=True, help="comma-separated, as the in-house CLI names them")
    parser.add_argument("--wrap", default=str(HERE.parent / "wrap" / "wrap.py"))
    parser.add_argument("--wrap-config")
    parser.add_argument("--tries", type=int, default=3)
    parser.add_argument("--out", default="bench-result")
    args = parser.parse_args()
    pack = replay.use_pack(args.pack)
    parts = pick_parts()
    rows = []
    with tempfile.TemporaryDirectory(prefix="bench-") as temp:
        for model in [item.strip() for item in args.models.split(",") if item.strip()]:
            for key, part_id, _size in parts:
                row = run_one(pack, Path(args.wrap), args.wrap_config, model, key, part_id, args.tries, Path(temp))
                rows.append(row)
                print(f"{model:24} {key:9} {part_id}  принят={row['accepted']} попытка={row['accepted_try']} доля={row['overlap']} {row['seconds']} с", flush=True)
    best = recommend(rows)
    Path(args.out + ".md").write_bytes(markdown(rows, best, parts).encode("utf-8"))
    Path(args.out + ".json").write_bytes(json.dumps({"parts": parts, "rows": rows, "recommendation": best}, ensure_ascii=False, indent=1).encode("utf-8"))
    print(f"\nРекомендация: {best}\nТаблица: {args.out}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
