"""The size of one model step's input before the call, against the step budget (FaT-3; only reads).

``python -m tools.step_size --project <abs> --files <path>… [--role <role>]`` sums the files a step reads;
``--run <id> --stage context-marker [--batch N]`` takes them from the journal: the selected context files of the
batch (all batches without ``--batch``) and the requirement documents of the run.  ``--role`` (by default the role of
``--stage``) adds ``skills/<role>/SKILL.md`` and its references.  The budget is ``.skillsrc`` ``limits.step_input_bytes``,
else 500 000 bytes: the window is shared with the session history.  Prints
``{bytes, tokens_est, budget_bytes, fits, hint, …}``; exit 3 when the step does not fit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

PACK = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = 500_000
TOKENS_PER_BYTE = 0.24
_STAGE_ROLES = {"context-marker": "context-marker"}


def step_budget(project: Path) -> int:
    path = Path(project) / ".skillsrc"
    if not path.is_file():
        return DEFAULT_BUDGET
    from tools.skillsrc_manifest import load_skillsrc

    value = (load_skillsrc(path).get("limits") or {}).get("step_input_bytes")
    return int(value) if isinstance(value, int) and value > 0 else DEFAULT_BUDGET


def role_files(role: str) -> list[Path]:
    base = PACK / "skills" / role
    if not (base / "SKILL.md").is_file():
        raise ValueError(f"unknown role: {role}")
    return [base / "SKILL.md", *sorted(path for path in (base / "references").rglob("*") if path.is_file())]


def journal_files(project: Path, run_id: str, stage: str, batch: int | None) -> list[Path]:
    """The input files of ``stage`` as the journal knows them before the call (context-marker only)."""
    if stage != "context-marker":
        raise ValueError("the journal knows the input of context-marker only; give --files for other steps")
    from tools.pilot_state import derive_state
    from tools.project_inventory import read_execution_baseline

    project = Path(project).resolve()
    root = project / ".pilot-runs" / run_id
    attempts = derive_state(root)["attempts"]
    if not attempts:
        raise ValueError("the run has no attempt yet")
    attempt = attempts[-1]
    files: list[Path] = []
    for receipt_path in sorted((root / "context-selections" / str(attempt["attempt_id"])).glob("*.json")):
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if batch is None or receipt.get("batch_index") == batch:
            files.extend(project / item["project_path"] for item in receipt["files"])
    baseline = read_execution_baseline(root / "baselines" / (str(attempt["baseline_digest"]).removeprefix("sha256:") + ".json"))
    files.extend(project / row["path"] for row in baseline["requirements"].get("docs", []))
    return list(dict.fromkeys(files))


def measure(project: Path, files: Iterable[Path], role: str | None = None, *, tokens_per_byte: float = TOKENS_PER_BYTE,
            budget: int | None = None) -> dict[str, Any]:
    paths = [Path(path) for path in files]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"files are missing: {missing[:5]}")
    input_bytes = sum(path.stat().st_size for path in paths)
    instructions = role_files(role) if role else []
    role_bytes = sum(path.stat().st_size for path in instructions)
    total = input_bytes + role_bytes
    budget_bytes = step_budget(project) if budget is None else budget
    fits = total <= budget_bytes
    hint = None if fits else (f"вход шага {total} Б больше бюджета {budget_bytes} Б: сузьте контекст — --target <путь> модуля, "
                              "paths/include модуля в .skillsrc или меньше файлов шага; бюджет — .skillsrc limits.step_input_bytes")
    return {"bytes": total, "input_bytes": input_bytes, "role_bytes": role_bytes, "role": role, "files": len(paths),
            "tokens_est": round(total * tokens_per_byte), "tokens_per_byte": tokens_per_byte, "budget_bytes": budget_bytes,
            "fits": fits, "hint": hint}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Size of one model step's input against the step budget.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--files", nargs="+", default=[])
    parser.add_argument("--run")
    parser.add_argument("--stage", choices=sorted(_STAGE_ROLES))
    parser.add_argument("--batch", type=int)
    parser.add_argument("--role")
    parser.add_argument("--tokens-per-byte", type=float, default=TOKENS_PER_BYTE)
    parser.add_argument("--budget-bytes", type=int)
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    try:
        files = [Path(item) if Path(item).is_absolute() else project / item for item in args.files]
        if args.run:
            if not args.stage:
                raise ValueError("--run needs --stage")
            files += journal_files(project, args.run, args.stage, args.batch)
        role = args.role or (_STAGE_ROLES.get(args.stage) if args.stage else None)
        report = measure(project, files, role, tokens_per_byte=args.tokens_per_byte, budget=args.budget_bytes)
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["fits"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
