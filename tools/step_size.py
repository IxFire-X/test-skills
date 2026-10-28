"""The size of one model step's input before the call, against the step budget (FaT-3).

``python -m tools.step_size --project <abs> --files <path>… [--role <role>]`` sums the files a step reads;
``--run <id> --stage context-marker [--batch N]`` takes them from the journal: the selected context files of the
batch (all batches without ``--batch``) and the requirement documents of the run.  ``--role`` (by default the role of
``--stage``) adds ``skills/<role>/SKILL.md`` and its references.  The budget is ``.skillsrc`` ``limits.step_input_bytes``,
else 500 000 bytes: the window is shared with the session history.  Prints
``{bytes, tokens_est, budget_bytes, fits, warning, hint, recorded, …}``.  FaT-RV: a step over the budget is the warning
``STEP_OVER_BUDGET`` with the hint, not a stop (exit 0).  With ``--run`` the measurement — the step (``--step``, else
the stage or the role), sizes and the code, no file paths — is appended to ``<run>/step-sizes.jsonl`` for the report.
FaT-O: ``output_warning`` — an answer larger than ``.skillsrc`` ``limits.step_output_warn_bytes`` (default
``DEFAULT_OUTPUT_WARN``) is the warning ``STEP_OUTPUT_LARGE``; only a measurement, nothing is split or rewritten.
FaT-Q2: ``--stage context-marker:requirements`` is the requirements step of the marker in parts (the documents only);
``--stage tc-generator:<batch>|tc-to-autotest:rN|autotest-reviewer:rN`` takes the files behind the ``input_digests`` of
the published model request of that stage (``request_files``): an input that no file or more than one file of the run
answers is ``unresolved`` (``STEP_INPUT_UNRESOLVED``, not a stop), nothing is chosen.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from tools.json_cli import JsonArgumentParser

PACK = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = 500_000
OVER_BUDGET = "STEP_OVER_BUDGET"
RECORD = "step-sizes.jsonl"
OUTPUT_LARGE = "STEP_OUTPUT_LARGE"
DEFAULT_OUTPUT_WARN = 128_000  # ≈ 31 000 tokens at 0.24 tok/B; the real output limit of the work model is not known yet
TOKENS_PER_BYTE = 0.24
_STAGE_ROLES = {"context-marker": "context-marker"}
INPUT_UNRESOLVED = "STEP_INPUT_UNRESOLVED"
_REQUEST_STAGES = ("tc-generator", "tc-to-autotest", "autotest-reviewer")
_HEX = re.compile(r"[0-9a-f]{64}")


def step_budget(project: Path) -> int:
    path = Path(project) / ".skillsrc"
    if not path.is_file():
        return DEFAULT_BUDGET
    from tools.skillsrc_manifest import load_skillsrc

    value = (load_skillsrc(path).get("limits") or {}).get("step_input_bytes")
    return int(value) if isinstance(value, int) and value > 0 else DEFAULT_BUDGET


def output_warning(project: Path, byte_count: int) -> str | None:
    """FaT-O: ``STEP_OUTPUT_LARGE`` when one model answer is over the output warning threshold, else ``None``."""
    limit = DEFAULT_OUTPUT_WARN
    path = Path(project) / ".skillsrc"
    try:  # called after the answer is published: an unreadable .skillsrc must not fail the step, the default applies
        if path.is_file():
            from tools.skillsrc_manifest import load_skillsrc

            value = (load_skillsrc(path).get("limits") or {}).get("step_output_warn_bytes")
            limit = int(value) if isinstance(value, int) and value > 0 else DEFAULT_OUTPUT_WARN
    except (OSError, ValueError):  # SkillsrcError is a ValueError
        limit = DEFAULT_OUTPUT_WARN
    return OUTPUT_LARGE if byte_count > limit else None


def role_files(role: str) -> list[Path]:
    base = PACK / "skills" / role
    if not (base / "SKILL.md").is_file():
        raise ValueError(f"unknown role: {role}")
    # FaT-B: tc-to-autotest also reads assets/ (the Java/Python conventions)
    return [base / "SKILL.md", *sorted(path for folder in ("references", "assets") for path in (base / folder).rglob("*") if path.is_file())]


def journal_files(project: Path, run_id: str, stage: str, batch: int | None) -> list[Path]:
    """The input files of ``stage`` as the journal knows them before the call (context-marker only)."""
    if stage not in {"context-marker", "context-marker:requirements"}:
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
    answer = next((str(event["artifact_digest"]) for event in derive_state(root)["events"]
                   if event.get("attempt_id") == attempt["attempt_id"] and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "context-marker:requirements"), None)
    if stage == "context-marker:requirements":
        # FaT-Q2 (2.3): the requirements step of the marker in parts reads the requirement documents only
        return list(dict.fromkeys(project / row["path"] for row in baseline["requirements"].get("docs", [])))
    if batch is not None and answer is not None:
        # FaT-5: a part of the marker reads the requirements answer, not the documents again.
        files.append(root / "model-stage-artifacts" / str(attempt["attempt_id"]) / (answer.removeprefix("sha256:") + ".json"))
    else:
        files.extend(project / row["path"] for row in baseline["requirements"].get("docs", []))
    return list(dict.fromkeys(files))


def _digest_paths(root: Path) -> tuple[dict[str, set[Path]], dict[str, set[Path]]]:
    """``(whole, inside)``: the keys a run file answers to as a whole — its name, the sha256 of its bytes, its own
    ``digest``, a canonical digest — and the keys of an object it carries one level inside."""
    from tools.canonical_document import document_sha256

    index: dict[str, set[Path]] = {}
    inside: dict[str, set[Path]] = {}
    for path in sorted(root.rglob("*.json")):
        if path.relative_to(root).parts[0] == "tmp":
            continue
        try:
            raw = path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError):
            continue
        keys = {hashlib.sha256(raw).hexdigest()}
        if _HEX.fullmatch(path.stem):
            keys.add(path.stem)
        if isinstance(value, dict):
            if isinstance(value.get("digest"), str):
                keys.add(value["digest"].removeprefix("sha256:"))
            if isinstance(value.get("test_cases"), list):
                try:
                    keys.add(document_sha256(value).removeprefix("sha256:"))
                except Exception:  # noqa: BLE001 - a non-canonical document only lacks this key
                    pass
            # a receipt kept inside its carrier (the effective bundle receipt in effective-canonicals/): its own
            # ``digest`` or the sha256 of its canonical JSON, with and without the journal's trailing newline
            for nested in value.values():
                if isinstance(nested, dict):
                    compact = json.dumps(nested, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    carried = {hashlib.sha256(compact).hexdigest(), hashlib.sha256(compact + b"\n").hexdigest()}
                    if isinstance(nested.get("digest"), str):
                        carried.add(nested["digest"].removeprefix("sha256:"))
                    for key in carried:
                        inside.setdefault(key, set()).add(path)
        for key in keys:
            index.setdefault(key, set()).add(path)
    return index, inside


def request_files(run_root: Path, stage: str) -> dict[str, Any]:
    """FaT-Q2 (2.2): the files behind the ``input_digests`` of the published model request of ``stage``.

    Only a measurement: a context-selection receipt opens into the project files it lists; a file that is the input as a
    whole answers before a file that only carries it inside; an input digest that no file or more than one file of the
    same kind answers is ``unresolved`` (``not-found``/``ambiguous``), never chosen."""
    from tools.pilot_state import derive_state, read_model_request

    root = Path(run_root).resolve()
    project = root.parent.parent
    state = derive_state(root)
    if not state["attempts"]:
        raise ValueError("the run has no attempt yet")
    attempt_id = str(state["attempts"][-1]["attempt_id"])
    requested = [event for event in state["events"] if event.get("attempt_id") == attempt_id
                 and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage]
    if not requested:
        raise ValueError(f"no model request of {stage} yet: publish it first with tools.api model-request")
    request_digest = str(requested[-1]["artifact_digest"])
    request = read_model_request(root, attempt_id, stage, request_digest)
    whole, inside = _digest_paths(root)
    selections = root / "context-selections"
    files: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for digest in request.get("input_digests") or []:
        key = str(digest).removeprefix("sha256:")
        paths = sorted(whole.get(key) or inside.get(key) or ())
        if len(paths) != 1:
            unresolved.append({"digest": str(digest), "reason": "not-found" if not paths else "ambiguous"})
            continue
        path = paths[0]
        if selections in path.parents:
            receipt = json.loads(path.read_text(encoding="utf-8"))
            targets = [project / item["project_path"] for item in receipt["files"]]
        else:
            targets = [path]
        files.extend({"digest": str(digest), "path": str(target), "bytes": target.stat().st_size} for target in targets)
    return {"stage": stage, "attempt_id": attempt_id, "request_digest": request_digest, "files": files, "unresolved": unresolved}


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
            "fits": fits, "warning": None if fits else OVER_BUDGET, "hint": hint}


def record(project: Path, run_id: str, step: str, report: dict[str, Any]) -> str:
    """FaT-RV: append one measurement to ``<run>/step-sizes.jsonl`` (only the step, sizes and the code)."""
    from datetime import datetime, timezone

    from tools.confined_output import atomic_write_confined_bytes_at_root

    project = Path(project).resolve()
    root = project / ".pilot-runs" / run_id
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."} or not (root / "run-manifest.json").is_file():
        raise ValueError(f"unknown run: {run_id}")
    row = {key: report[key] for key in ("bytes", "input_bytes", "role_bytes", "role", "files", "tokens_est", "budget_bytes",
                                        "fits", "warning")}
    row.update(step=step, at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    path = root / RECORD
    before = path.read_bytes() if path.is_file() else b""
    line = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8") + b"\n"
    atomic_write_confined_bytes_at_root(project, root, path, before + line)
    return str(path)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = JsonArgumentParser(description="Size of one model step's input against the step budget.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--files", nargs="+", default=[])
    parser.add_argument("--run")
    parser.add_argument("--stage", help="context-marker, context-marker:requirements, tc-generator:<batch>, tc-to-autotest:rN, autotest-reviewer:rN")
    parser.add_argument("--batch", type=int)
    parser.add_argument("--role")
    parser.add_argument("--step", help="the step name recorded in the run (default: --stage, else --role)")
    parser.add_argument("--tokens-per-byte", type=float, default=TOKENS_PER_BYTE)
    parser.add_argument("--budget-bytes", type=int)
    args = parser.parse_args(argv)
    project = Path(args.project).resolve()
    try:
        files = [Path(item) if Path(item).is_absolute() else project / item for item in args.files]
        requested = None
        stage_role = args.stage.split(":", 1)[0] if args.stage else None
        if args.stage and stage_role not in _STAGE_ROLES and stage_role not in _REQUEST_STAGES:
            raise ValueError(f"unknown --stage: {args.stage}")
        if args.run:
            if not args.stage and not files:
                raise ValueError("--run needs --stage or --files")
            if stage_role in _REQUEST_STAGES:
                requested = request_files(project / ".pilot-runs" / args.run, args.stage)
                files += [Path(row["path"]) for row in requested["files"]]
            elif args.stage:
                files += journal_files(project, args.run, args.stage, args.batch)
        role = args.role or stage_role
        report = measure(project, list(dict.fromkeys(files)), role, tokens_per_byte=args.tokens_per_byte, budget=args.budget_bytes)
        if requested is not None:
            report.update(input_files=requested["files"], unresolved=requested["unresolved"], size_complete=not requested["unresolved"],
                          input_warning=INPUT_UNRESOLVED if requested["unresolved"] else None)
        report["recorded"] = False
        if args.run:
            stage = args.stage and (f"{args.stage}:batch-{args.batch:03d}" if args.batch is not None else args.stage)
            report["recorded"] = record(project, args.run, args.step or stage or role or "step", report)
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "error", "message": str(error)}, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
