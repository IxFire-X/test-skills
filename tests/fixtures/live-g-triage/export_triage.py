"""Export the inputs of run g's survivor triage (run 42951bc1, attempt 30cbe3b3) into ``triage-g.json.gz``.

Run once from the pack root with the extracted run archive and the run's project:
``python tests/fixtures/live-g-triage/export_triage.py <run root> <project>``.  The fixture keeps the mutation
receipt's survivor groups, the effective canonical document, the automation artifact, the context marker's source
requirements, the mutation rows (status, class, method, line, killing tests) and the product Java files of the
attempt's context.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

ATTEMPT = "30cbe3b301c940c0ae0174f598a14b22"


def main(run_root: Path, project: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from tools.mutation import parse_report

    def load(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    receipt = load(run_root / "mutation-receipts" / f"{ATTEMPT}.json")
    document = load(run_root / "effective-canonicals" / f"{ATTEMPT}.json")["document"]
    automation = load(run_root / "execution-inputs" / f"{ATTEMPT}.json")["automation_artifact"]
    marker = []
    for path in sorted((run_root / "model-stage-artifacts" / ATTEMPT).glob("*.json")):
        text = path.read_text(encoding="utf-8")
        if "analytics_documentation" in text:
            payload = json.loads(text)
            found = payload
            for key in ("artifact", "artifacts", "analytics_documentation", "requirements"):
                found = found.get(key, found) if isinstance(found, dict) else found
            if isinstance(found, list):
                marker = found
    paths = sorted({row["project_path"] for path in (run_root / "context-selections" / ATTEMPT).glob("*.json")
                    for row in load(path)["files"] if str(row.get("project_path", "")).startswith("src/main/java/")})
    sources = {path: (project / path).read_text(encoding="utf-8") for path in paths}
    mutants = [{key: row[key] for key in ("status", "class", "method", "line", "killing")}
               for row in parse_report((run_root / receipt["report"]["path"]).read_bytes())]
    fixture = {"receipt": {"status": receipt["status"], "survivor_groups": receipt["survivor_groups"]}, "document": document,
               "automation": automation, "marker_requirements": marker, "mutants": mutants, "sources": sources}
    out = Path(__file__).with_name("triage-g.json.gz")
    with gzip.GzipFile(out, "wb", mtime=0) as stream:
        stream.write(json.dumps(fixture, ensure_ascii=False, sort_keys=True).encode("utf-8"))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
