"""Profile ``suite-update-v1``: update a living suite (wave 3, R; contract amendment A5).

Steps: migration → impact analysis → update of the affected cases (``tc-generator`` in ``update``
mode) → review of the changed cases only (``tc-reviewer``, compact-v1) → automation of the changed
cases (``tc-to-autotest`` in ``update`` mode; methods replaced by their slices) → static review of
the changed slices (``autotest-reviewer``) → the whole suite run (quarantined methods explicitly,
failed ones repeated) → failure triage and quarantine, one repair try (``tc-to-autotest`` in
``repair`` mode, through the static review, expectations unchanged) → mutations (opt-in) → manifest
→ summary (``pr-description.md``, ``suite.patch``, ``suite-update-result.json``).

This is not a pilot attempt: it has no ``accepted`` and no pilot result tuple.  It writes only the
suite directory and the test methods the manifest lists with matching digests; everything a person
edited stays and becomes a proposal.  It never commits, pushes or opens a pull request.

The run lives in ``.pilot-runs/<run_id>`` (only the marker ``suite-update.json`` with the step and
facts) and its driver directory ``.pilot-runs/<run_id>.driver`` (tasks, answers, log, results), so
``next``/``submit``/``run --runner process`` drive it like any run.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools import pipeline_driver as driver
from tools.suite_manifest import (MANIFEST, SUITE_FILES, SuiteError, build_manifest, canonical_bytes, case_digest, locator_text, method_digests,
                                  parse_locator, projections, read_suite, sha256_bytes, suite_directory, write_suite)

PROFILE = "suite-update-v1"
MARKER = "suite-update.json"
TRIES = 3
_REVIEW_MODE = "compact-v1"


# ----------------------------------------------------------------------------------- state

def is_suite_run(run_root: Path) -> bool:
    return (Path(run_root) / MARKER).is_file()


def _state(run_root: Path) -> dict[str, Any]:
    return json.loads((Path(run_root) / MARKER).read_text(encoding="utf-8"))


def _save(run_root: Path, state: Mapping[str, Any]) -> None:
    path = Path(run_root) / MARKER
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_bytes(state))
    temporary.replace(path)


def _work(run_root: Path) -> Path:
    directory = driver.work_dir(run_root) / "suite"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))
    return path


def _log(project: Path, run_root: Path, row: Mapping[str, Any]) -> None:
    driver._log(driver._log_path(project, run_root.name), dict(row))


# ----------------------------------------------------------------------------------- start

def start(project: Path, options: Mapping[str, Any], *, max_tasks: int = 1) -> dict[str, Any]:
    from tools.pipeline_driver_suite import _skillsrc

    project = Path(project).resolve()
    skillsrc = _skillsrc(project)
    suite_dir = suite_directory(project, skillsrc)
    from tools.suite_migrate import detect

    if detect(project, suite_dir) is None:
        return driver._done(None, {"status": "error", "stage": "suite-update", "exit_code": 2, "reason": "SUITE_MISSING",
                                   "message": f"{suite_dir} holds no suite: create one with local-pilot-v1 --suite first"})
    run_id = uuid.uuid4().hex
    run_root = project / ".pilot-runs" / run_id
    run_root.mkdir(parents=True)
    config = {"schema_version": "1.0.0", "profile": PROFILE, "docs": list(options.get("docs") or []), "junit": list(options.get("junit") or []),
              "model_id": options.get("model_id"), "host_cli": options.get("host_cli") or "unspecified",
              "host_cli_version": options.get("host_cli_version") or "unspecified", "reviewer_isolation": options.get("reviewer_isolation") or "fresh",
              "mutation": bool(options.get("mutation")), "repeats": 2, "suite_dir": suite_dir,
              "review_input_bytes": int(options.get("review_input_bytes") or driver._DEFAULT_REVIEW_INPUT_BYTES)}
    driver._save_config(run_root, config)
    _save(run_root, {"schema_version": "1.0.0", "run_id": run_id, "profile": PROFILE, "step": "MIGRATION", "facts": {}, "tries": {}})
    _snapshot_before(project, run_root, suite_dir)
    _log(project, run_root, {"event": "suite_update_started", "run_id": run_id})
    return advance(project, run_root, max_tasks=max_tasks)


def _owned_files(project: Path, suite_dir: str) -> list[str]:
    manifest = read_suite(project, suite_dir) or {}
    files = [row["path"] for row in manifest.get("files") or []]
    return sorted({*files, *(suite_dir + name for name in [*SUITE_FILES.values(), MANIFEST])})


def _snapshot_before(project: Path, run_root: Path, suite_dir: str) -> None:
    rows = {}
    for path in _owned_files(project, suite_dir):
        target = project / path
        rows[path] = target.read_bytes().hex() if target.is_file() else None
    _write(_work(run_root) / "before.json", rows)


# ----------------------------------------------------------------------------------- advance

def advance(project: Path, run_root: Path, *, max_tasks: int = 1) -> dict[str, Any]:
    project = Path(project).resolve()
    for _ in range(64):
        state = _state(run_root)
        step = state["step"]
        if step == "DONE":
            return driver._done(run_root, state["facts"]["result"])
        handler = _STEPS[step]
        outcome = handler(project, run_root, state, max_tasks)
        if outcome is None:
            continue
        return outcome
    raise driver.DriverError("DRIVER_FAILURE", "suite-update did not settle")


def _goto(run_root: Path, state: dict[str, Any], step: str, **facts: Any) -> None:
    state["facts"].update(facts)
    state["step"] = step
    _save(run_root, state)


def _stop(run_root: Path, state: dict[str, Any], reason: str, message: str) -> None:
    state["facts"]["stop"] = {"reason": reason, "message": message}
    _goto(run_root, state, "SUMMARY")


# ----------------------------------------------------------------------------------- 1. migration, 2. impact

def _migration(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.suite_migrate import migrate

    try:
        summary = migrate(project)
    except SuiteError as error:
        _stop(run_root, state, error.code, str(error))
        return None
    _goto(run_root, state, "IMPACT", migration=summary)
    return None


def _impact(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.suite_impact import impact

    config = driver._config(run_root)
    try:
        result = impact(project, docs=config["docs"] or None, junit=config["junit"])
    except SuiteError as error:
        _stop(run_root, state, error.code, str(error))
        return None
    _write(_work(run_root) / "impact.json", result)
    needs_update = bool(result["requirements"]["added"] or result["requirements"]["changed"] or result["requirements"]["removed"])
    # New endpoints without a requirement are questions for the analysts; test cases do not change for them.
    questions = [row["question"] for row in result.get("analyst_questions") or []]
    _goto(run_root, state, "UPDATE" if needs_update else "SUITE_RUN", impact=result, needs_update=needs_update, questions=questions)
    return None


def _suite(project: Path, run_root: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    suite_dir = driver._config(run_root)["suite_dir"]
    manifest = read_suite(project, suite_dir)
    document = json.loads((project / manifest["suite_files"]["canonical"]["path"]).read_text(encoding="utf-8"))
    return suite_dir, manifest, document


def _scan(project: Path, run_root: Path, manifest: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from tools.build_context import build_context
    from tools.pipeline_driver_suite import _skillsrc
    from tools.requirement_identity import identify
    from tools.run_pipeline import _docs_entries

    config = driver._config(run_root)
    id_pattern = (_skillsrc(project).get("requirements") or {}).get("id_pattern")
    entries = _docs_entries(project, config["docs"] or [row["path"] for row in manifest["documents"]])
    return identify(entries, id_pattern), build_context(project, docs_snapshot=entries, id_pattern=id_pattern)


# ----------------------------------------------------------------------------------- tasks

def _task(run_root: Path, label: str, *, stage: str, skill: str, inputs: Sequence[Path], schema: Mapping[str, Any], instructions: str,
          extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return driver._llm_task(run_root, run_root.name, label, stage=stage, skill=skill, inputs=inputs, schema=schema, instructions=instructions,
                            extra={"profile": PROFILE, "suite_label": label, **dict(extra or {})})


def _answer(run_root: Path, label: str) -> Any | None:
    path = driver.work_dir(run_root) / "outputs" / f"{driver._task_id(run_root.name, label)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def submit(project: Path, run_root: Path, task_id: str, *, output: Path | None = None, failed: str | None = None, reason: str | None = None,
           **_ignored: Any) -> dict[str, Any]:
    """Check one answer against its step; a rejected answer is reissued with the errors (at most three tries)."""
    project = Path(project).resolve()
    task = json.loads(driver._task_path(run_root, task_id).read_text(encoding="utf-8"))
    state = _state(run_root)
    label = str(task.get("suite_label"))
    accepted = state["facts"].setdefault("accepted", {})
    if label in accepted:
        return advance(project, run_root)
    if failed is not None:
        _stop(run_root, state, "SUITE_TASK_FAILED", f"{label}: {failed} {reason or ''}".strip())
        return advance(project, run_root)
    try:
        value = json.loads(Path(output or task["output_path"]).read_text(encoding="utf-8"))
        problems = _CHECKS[label.split(".", 1)[0]](project, run_root, state, label, value)
    except (OSError, ValueError) as error:
        problems = [{"path": "", "code": "TASK_OUTPUT_INVALID", "message": f"the answer is not strict UTF-8 JSON: {error}"}]
    if problems:
        tries = state["tries"].get(label, 0) + 1
        state["tries"][label] = tries
        _save(run_root, state)
        _log(project, run_root, {"event": "submit_rejected", "task_id": task_id, "try": tries, "codes": sorted({row.get("code") for row in problems})})
        if tries >= TRIES:
            _stop(run_root, state, "SUITE_TASK_REJECTED", f"{label}: rejected {tries} times")
            return advance(project, run_root)
        return {**task, "status": "rejected", "errors": problems[:40], "message": f"{label}: the answer was rejected"}
    state = _state(run_root)
    state["facts"].setdefault("accepted", {})[label] = True
    _save(run_root, state)
    _log(project, run_root, {"event": "submit_accepted", "task_id": task_id})
    return advance(project, run_root)


# ----------------------------------------------------------------------------------- 3. update

_UPDATE_INSTRUCTIONS = (
    "Режим update набора тестов. Первый файл — задание: изменённые, добавленные и удалённые требования (старый и новый текст по ключу), "
    "затронутые кейсы целиком, их канонические требования, предложения усиления TEST_GAP и id_prefixes; второй — весь текущий документ набора; "
    "третий — новый скан требований (source_requirements с новыми номерами SREQ). Верни только изменённое: test_cases — каждый обновлённый "
    "затронутый кейс целиком (тот же case_id, ID неизменённых шагов и проверок сохраняй) и каждый новый кейс (ID начиная с id_prefixes.case_id); "
    "requirements — канонические требования, текст которых меняется, и новые (ID начиная с id_prefixes.requirement_id); "
    "source_to_canonical_mappings — для каждого добавленного требования (по новому номеру SREQ) и для изменённого, если его связь меняется; "
    "retire — кейсы удалённых требований с причиной. Кейсы из edited_by_people и незатронутые кейсы не трогай: их правили люди или они не связаны "
    "с изменениями. Неизменённые кейсы, требования, связи, нумерацию SREQ, порядок и ревизию драйвер возьмёт сам.")


def _update_schema() -> dict[str, Any]:
    canonical = Path(driver.ROOT) / "schemas" / "canonical-test-document.schema.json"
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False, "required": ["test_cases"],
            "properties": {
                "test_cases": {"type": "array", "items": {"$ref": f"{canonical}#/$defs/test_case"}},
                "requirements": {"type": "array", "items": {"$ref": f"{canonical}#/$defs/requirement"}},
                "source_to_canonical_mappings": {"type": "array", "items": {"$ref": f"{canonical}#/$defs/source_to_canonical_mapping"}},
                "operation_capabilities": {"type": "array", "items": {"$ref": f"{canonical}#/$defs/operation_capability"}},
                "retire": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["case_id", "reason"],
                                                      "properties": {"case_id": {"type": "string"}, "reason": {"type": "string", "minLength": 1, "maxLength": 500}}}},
                "diagnostics": {"type": "array"}},
            "x-note": "References resolve against the pack schemas; the driver fills everything that did not change."}


def _update(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> dict[str, Any] | None:
    from tools.suite_merge import update_brief

    if state["facts"].get("accepted", {}).get("update"):
        _goto(run_root, state, "REVIEW")
        return None
    _suite_dir, manifest, document = _suite(project, run_root)
    scan, envelope = _scan(project, run_root, manifest)
    impact = state["facts"]["impact"]
    brief = update_brief(document=document, manifest=manifest, scan=scan, impact=impact, edited_cases=impact["edited"]["cases"],
                         proposals=_test_gap_proposals(project, manifest))
    if not brief["affected_cases"] and not brief["added_requirements"]:
        # Only renames or changes of cases people edited: nothing for the generator.
        _goto(run_root, state, "SUITE_RUN", update={"status": "NOT_NEEDED"})
        return None
    work = _work(run_root)
    inputs = [_write(work / "update-brief.json", brief), _write(work / "suite-document.json", document),
              _write(work / "scan.json", {"source_requirements": envelope["artifacts"]["analytics_documentation"]["requirements"],
                                          "keys": [{"source_requirement_id": row["source_requirement_id"], "key": row["key"]} for row in scan]})]
    return _task(run_root, "update", stage="tc-generator:update", skill="tc-generator", inputs=inputs, schema=_update_schema(),
                 instructions=_UPDATE_INSTRUCTIONS, extra={"mode": "update"})


def _test_gap_proposals(project: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """TEST_GAP proposals of the survivor triage of the attempts that measured the suite (wave 2)."""
    from tools.pipeline_driver_strength import proposals_path

    rows = []
    runs = {(row["run_id"], row["attempt_id"]) for row in (manifest["source_run"], manifest["last_run"]) if row.get("attempt_id")}
    for run_id, attempt_id in sorted(runs):
        path = proposals_path(Path(project) / ".pilot-runs" / run_id, attempt_id)
        if path.is_file():
            try:
                rows += [dict(row) for row in json.loads(path.read_text(encoding="utf-8")).get("proposals", [])]
            except (OSError, ValueError):
                continue
    return rows


def _check_update(project: Path, run_root: Path, state: dict[str, Any], _label: str, value: Any) -> list[dict[str, str]]:
    from tools.suite_merge import MergeError, merge_update

    _suite_dir, manifest, document = _suite(project, run_root)
    scan, envelope = _scan(project, run_root, manifest)
    impact = state["facts"]["impact"]
    try:
        merged, report = merge_update(document=document, manifest=manifest, envelope=envelope, scan=scan, impact=impact, answer=value,
                                      edited_cases=impact["edited"]["cases"])
    except MergeError as error:
        return error.diagnostics
    work = _work(run_root)
    _write(work / "document.json", merged)
    state = _state(run_root)
    state["facts"]["update"] = {"status": "MERGED", **report}
    _save(run_root, state)
    return []


# ----------------------------------------------------------------------------------- 4./6. reviews

def product_contexts(project: Path, manifest: Mapping[str, Any], document: Mapping[str, Any], code: str = "", *, budget: int = 120_000) -> list[dict[str, str]]:
    """Product sources a reviewer needs: files the document's provenance names, and classes the changed code refers to.

    A file with masked secret-like text stays out (as in the pilot's context selection); the order is the order of
    first mention, within ``budget`` bytes.
    """
    import re

    from tools.pipeline_driver_suite import _module, _skillsrc
    from tools.project_inventory import redact_text

    module = _module(_skillsrc(project), manifest["module_id"])
    root = Path(project) / str(module.get("root") or ".")
    tests = {str(item).strip("/") for item in ((module.get("paths") or {}).get("tests") or [])}
    provenance = json.dumps([document.get("operation_capabilities") or [], [row.get("provenance") for row in document.get("requirements") or []]], ensure_ascii=False)
    named: list[tuple[int, str, Path]] = []
    seen: set[Path] = set()
    for directory in ((module.get("paths") or {}).get("source") or ["src/main/java"]):
        base = root / str(directory)
        if not base.is_dir():
            continue
        for file in sorted(base.rglob("*")):
            if file in seen or not file.is_file() or file.suffix not in {".java", ".py"}:
                continue
            relative = file.relative_to(Path(project)).as_posix()
            if any(relative.startswith(prefix + "/") or f"/{prefix}/" in relative for prefix in tests):
                continue
            seen.add(file)
            positions = [position for position in (provenance.find(relative), provenance.find(file.name)) if position >= 0]
            match = re.search(rf"\b{re.escape(file.stem)}\b", code) if code else None
            if match:
                positions.append(match.start())
            if positions:
                named.append((min(positions) if not match else -1_000_000 + match.start(), relative, file))
    contexts, used = [], 0
    for _position, relative, file in sorted(named):
        try:
            content = file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        _masked, redactions = redact_text(content)
        size = len(content.encode("utf-8"))
        if redactions or used + size > budget:
            continue
        used += size
        contexts.append({"path": relative, "sha256": sha256_bytes(content.encode("utf-8")), "content": content})
    return contexts


def _review_snapshot(project: Path, run_root: Path, document: Mapping[str, Any], automation: Mapping[str, Any] | None, *, code: str = "") -> dict[str, Any]:
    from tools.run_pipeline import _docs_entries

    config = driver._config(run_root)
    _suite_dir, manifest, _document = _suite(project, run_root)
    entries = _docs_entries(project, config["docs"] or [row["path"] for row in manifest["documents"]])
    return {"document": copy.deepcopy(dict(document)), "automation": None if automation is None else copy.deepcopy(dict(automation)), "package_binding": None,
            "sources": [{"path": row["path"], "sha256": row["sha256"], "content": row["content"]} for row in entries],
            "contexts": product_contexts(project, manifest, document, code),
            "requirements_binding": {"module_id": manifest["module_id"], "selected_target": None, "docs": [{"path": row["path"], "sha256": row["sha256"]} for row in entries]}}


def subset_document(document: Mapping[str, Any], case_ids: Sequence[str]) -> dict[str, Any]:
    """The document restricted to some cases and the requirements they cover (for reviewing only what changed)."""
    keep = set(case_ids)
    cases = [copy.deepcopy(case) for case in document["test_cases"] if case["case_id"] in keep]
    creqs = {creq for case in cases for creq in case.get("requirement_ids") or []}
    mappings = [{"source_requirement_id": row["source_requirement_id"], "canonical_requirement_ids": [creq for creq in row["canonical_requirement_ids"] if creq in creqs]}
                for row in document["source_to_canonical_mappings"] if set(row["canonical_requirement_ids"]) & creqs]
    sreqs = {row["source_requirement_id"] for row in mappings}
    sources = [copy.deepcopy(row) for row in document["source_requirements"] if row["source_requirement_id"] in sreqs]
    for position, row in enumerate(sources, start=1):
        row["display_order"] = position
    requirements = [copy.deepcopy(row) for row in document["requirements"] if row["requirement_id"] in creqs]
    for position, row in enumerate(requirements, start=1):
        row["display_order"] = position
    capabilities = {step.get("operation", {}).get("capability_id") for case in cases for step in case.get("steps") or []}
    return {**{key: copy.deepcopy(value) for key, value in document.items() if key not in {"test_cases", "requirements", "source_requirements",
                                                                                          "source_to_canonical_mappings", "operation_capabilities"}},
            "source_requirements": sources, "requirements": requirements, "source_to_canonical_mappings": mappings,
            "operation_capabilities": [copy.deepcopy(row) for row in document.get("operation_capabilities") or [] if row["capability_id"] in capabilities],
            "test_cases": [dict(case, display_order=position) for position, case in enumerate(cases, start=1)]}


def _plan(project: Path, run_root: Path, key: str, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    from tools.review_modes import build_offline_plan

    path = _work(run_root) / f"review-{key}-plan.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    kind = "canonical" if key == "canonical" else "automation"
    plan = build_offline_plan(snapshot, mode=_REVIEW_MODE, review_key="canonical" if kind == "canonical" else "r1",
                              input_byte_budget=int(driver._config(run_root)["review_input_bytes"]), instructions=driver._REVIEW_INSTRUCTIONS[kind])
    _write(path, plan)
    _write(_work(run_root) / f"review-{key}-snapshot.json", snapshot)
    return plan


def _document(project: Path, run_root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    """The suite's document after this run: the merged revision once the update is applied, the suite's otherwise."""
    merged = _work(run_root) / "document.json"
    if state["facts"].get("update_applied") and merged.is_file():
        return json.loads(merged.read_text(encoding="utf-8"))
    return _suite(project, run_root)[2]


def _blocked(state: Mapping[str, Any]) -> bool:
    return any(not review["eligible"] for key, review in (state["facts"].get("reviews") or {}).items() if key in {"canonical", "static"})


def _review_step(project: Path, run_root: Path, state: dict[str, Any], key: str, snapshot_of, next_step: str, max_tasks: int,
                 blocked_step: str = "SUITE_RUN") -> dict[str, Any] | None:
    from tools.review_compact import compact_payload, review_policy
    from tools.review_modes import additional_parts, aggregate, part_input

    snapshot = snapshot_of()
    if snapshot is None:
        _goto(run_root, state, next_step)
        return None
    plan = _plan(project, run_root, key, snapshot)
    kind = "tc-reviewer" if key == "canonical" else "autotest-reviewer"
    payload = compact_payload(json.loads((_work(run_root) / f"review-{key}-snapshot.json").read_text(encoding="utf-8")),
                              review_policy(kind, instructions=driver._REVIEW_INSTRUCTIONS["canonical" if key == "canonical" else "automation"], model_id=None))
    accepted = state["facts"].get("accepted", {})
    parts = [*plan["parts"], *plan.get("additions", [])]
    pending = [part for part in parts if f"review-{key}.{part['part_id']}" not in accepted and not part.get("blocked_reason")]
    if pending:
        tasks = [_review_task(run_root, key, plan, part) for part in pending[:max(1, max_tasks)]]
        if len(tasks) == 1:
            return tasks[0]
        return {"action": "batch", "run_id": run_root.name, "attempt_id": run_root.name, "review_key": key, "tasks": tasks,
                "instructions": "Независимые части ревью: каждую задачу — в отдельном свежем вызове, затем submit по каждой."}
    results = [json.loads((_work(run_root) / f"review-{key}-{part['part_id']}.json").read_text(encoding="utf-8")) for part in parts if not part.get("blocked_reason")]
    additions = additional_parts(plan, results, payload)
    if additions:
        plan.setdefault("additions", []).extend(additions)
        _write(_work(run_root) / f"review-{key}-plan.json", plan)
        return _review_step(project, run_root, state, key, snapshot_of, next_step, max_tasks, blocked_step)
    verdict = aggregate(plan, results, payload)
    _write(_work(run_root) / f"review-{key}-aggregate.json", verdict)
    state = _state(run_root)
    review = {"eligible": verdict["eligible"], "blocked": verdict["blocked"], "findings": verdict["findings"], "corrections": verdict["corrections"],
              "parts": len(parts), "input_bytes": sum(len(part_input(plan, part)["text"].encode("utf-8")) for part in parts)}
    state["facts"].setdefault("reviews", {})[key] = review
    if not verdict["eligible"]:
        state["facts"].setdefault("review_findings", []).extend(
            {**row, "review": key} for row in ([row for row in verdict["findings"] if row["severity"] == "BLOCKING"] or verdict["findings"]))
        if key == "repair":
            state["facts"]["repair_ran"] = True
        _goto(run_root, state, blocked_step)
        return None
    _goto(run_root, state, next_step)
    return None


def _review_task(run_root: Path, key: str, plan: Mapping[str, Any], part: Mapping[str, Any]) -> dict[str, Any]:
    from tools.review_modes import part_input

    envelope = part_input(plan, part)
    source = _work(run_root) / f"review-{key}-{part['part_id']}.input.md"
    source.write_text(envelope["text"], encoding="utf-8", newline="\n")
    kind = "tc-reviewer" if key == "canonical" else "autotest-reviewer"
    stage = f"{kind}:suite-{key}:{part['part_id']}"
    scope = (" Это ревью только изменённых кейсов живого набора: остальные кейсы набора уже прошли ревью и не менялись, их отсутствие в части "
             "не пробел покрытия." if key == "canonical" else " Это ревью только изменённых или отремонтированных методов живого набора: "
             "остальные методы файла уже прошли ревью и не менялись.")
    return _task(run_root, f"review-{key}.{part['part_id']}", stage=stage, skill=kind, inputs=[source], schema=driver._review_schema(_REVIEW_MODE),
                 instructions=driver.review_task_instructions(compact=True, fresh=driver._config(run_root).get("reviewer_isolation") == "fresh") + scope,
                 extra={"review_key": key, "part_id": part["part_id"], "review_mode": _REVIEW_MODE})


def _check_review(project: Path, run_root: Path, state: dict[str, Any], label: str, value: Any) -> list[dict[str, str]]:
    from tools.review_compact import compact_payload, review_policy
    from tools.review_modes import ANSWER_FIELDS, output_version, part_input, validate_part
    from tools.review_parts import review_digest

    key, part_id = label.split(".", 1)[0].removeprefix("review-"), label.split(".", 1)[1]
    plan = json.loads((_work(run_root) / f"review-{key}-plan.json").read_text(encoding="utf-8"))
    part = next(row for row in [*plan["parts"], *plan.get("additions", [])] if row["part_id"] == part_id)
    if not isinstance(value, dict) or set(value) != set(ANSWER_FIELDS[_REVIEW_MODE]):
        return [{"path": "", "code": "REVIEW_ASSESSMENT_FIELDS", "message": "the answer has exactly: " + ", ".join(ANSWER_FIELDS[_REVIEW_MODE])}]
    kind = "tc-reviewer" if key == "canonical" else "autotest-reviewer"
    payload = compact_payload(json.loads((_work(run_root) / f"review-{key}-snapshot.json").read_text(encoding="utf-8")),
                              review_policy(kind, instructions=driver._REVIEW_INSTRUCTIONS["canonical" if key == "canonical" else "automation"], model_id=None))
    bound = {"schema_version": output_version(_REVIEW_MODE, value),
             "plan_digest": plan["digest"], "snapshot_digest": plan["snapshot"]["snapshot_digest"], "part_id": part_id,
             "input_digest": review_digest(part_input(plan, part)), **value}
    problems = validate_part(plan, part, bound, payload)
    if not problems:
        _write(_work(run_root) / f"review-{key}-{part_id}.json", bound)
    return problems


def _review(project: Path, run_root: Path, state: dict[str, Any], max_tasks: int) -> dict[str, Any] | None:
    def snapshot():
        merged = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8"))
        update = state["facts"]["update"]
        changed = update["changed_cases"] + update["new_cases"]
        if not changed:
            return None
        return _review_snapshot(project, run_root, subset_document(merged, changed), None)

    return _review_step(project, run_root, state, "canonical", snapshot, "AUTOMATION", max_tasks)


# ----------------------------------------------------------------------------------- 5. automation (update and repair)

def _current_automation(project: Path, manifest: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    """An automation artifact of the suite as it is: its files, every listed method, a relation per step and assertion."""
    files, symbols, relations = [], [], []
    file_of = {row["path"]: row for row in manifest["files"]}
    cases = {case["case_id"]: case for case in document["test_cases"]}
    for row in manifest["files"]:
        content = (Path(project) / row["path"]).read_text(encoding="utf-8")
        files.append({"file_id": row["file_id"], "path": row["path"], "language": row["language"], "framework": "pytest" if row["language"] == "python" else "junit5",
                      "content": content, "content_digest": sha256_bytes(content.encode("utf-8"))})
    seen = set()
    for case in manifest["cases"]:
        if case["status"] == "RETIRED":
            continue
        for method in case["methods"]:
            file_row = file_of[method["file"]]
            if method["symbol_id"] not in seen:
                seen.add(method["symbol_id"])
                symbols.append({"file_id": file_row["file_id"], "symbol_id": method["symbol_id"], "locator": parse_locator(method["locator"], file_row["language"])})
            relations += _relations(cases.get(case["case_id"]), file_row["file_id"], method["symbol_id"])
    return {"schema_version": "5.0.0", "stage": "tc-to-autotest", "warnings": [],
            "artifacts": {"automation_status": "GENERATED", "generated_files": files, "generated_symbols": symbols, "implementation_relations": relations,
                          "manual_dispositions": [], "diagnostics": []}}


def _relations(case: Mapping[str, Any] | None, file_id: str, symbol_id: str) -> list[dict[str, Any]]:
    if case is None:
        return []
    rows = []
    for step in case.get("steps") or []:
        if step.get("manual_only"):
            continue
        rows.append({"kind": "operation", "case_id": case["case_id"], "step_id": step["step_id"], "file_id": file_id, "symbol_id": symbol_id})
        for expectation in step.get("expectations") or []:
            for assertion in expectation.get("assertions") or []:
                rows.append({"kind": "assertion", "case_id": case["case_id"], "step_id": step["step_id"], "expectation_id": expectation["expectation_id"],
                             "assertion_id": assertion["assertion_id"], "file_id": file_id, "symbol_id": symbol_id})
    return rows


def _automation_schema() -> dict[str, Any]:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "additionalProperties": False, "required": ["methods"],
            "properties": {
                "methods": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["case_id", "method_name", "source"],
                                                       "properties": {"case_id": {"type": "string"}, "method_name": {"type": "string", "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
                                                                      "source": {"type": "string", "minLength": 1}}}},
                "helpers": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "imports": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 300}},
                "diagnostics": {"type": "array"}}}


_AUTOMATION_INSTRUCTIONS = {
    "update": ("Режим update автотестов набора. Первый файл — задание: обновлённые и новые кейсы, для обновлённого — его текущий метод (locator, "
               "текст), целевой файл и его текущий текст; второй — новая ревизия документа набора. Верни methods: для каждого кейса задания один метод "
               "целиком — для обновлённого с тем же method_name, для нового с новым уникальным именем; helpers — новые вспомогательные методы, если "
               "нужны; imports — недостающие строки import. Каждая проверка кейса сохраняет свою метку ASSERT-… и ожидаемое значение кейса. "
               "Остальные методы и общий код файла драйвер сохранит сам; файлы проекта не пиши."),
    "repair": ("Режим repair: тест не компилируется или падает его собственный код, а кейс и требования не менялись. Первый файл — задание: кейс, "
               "текущий метод, вывод ошибки, целевой файл. Верни methods с тем же method_name и исправленным кодом; ожидания, литералы и метки "
               "ASSERT-… не меняй — ремонт чинит код теста, а не ожидаемое поведение. Одна попытка."),
}


def _automation_brief(project: Path, run_root: Path, mode: str, case_ids: Sequence[str], document: Mapping[str, Any], failures: Mapping[str, str] | None = None) -> dict[str, Any]:
    _suite_dir, manifest, _old = _suite(project, run_root)
    method_of = {case["case_id"]: case["methods"][0] for case in manifest["cases"] if case["methods"]}
    target = manifest["files"][0]
    content = (Path(project) / target["path"]).read_text(encoding="utf-8")
    cases = {case["case_id"]: case for case in document["test_cases"]}
    rows = []
    for case_id in case_ids:
        method = method_of.get(case_id)
        row = {"case_id": case_id, "case": cases[case_id], "method": None}
        if method is not None:
            source = _method_source(project, method, manifest)
            row["method"] = {"locator": method["locator"], "method_name": method["locator"].rsplit("#", 1)[-1].rsplit(".", 1)[-1], "source": source}
        if failures and case_id in failures:
            row["failure"] = failures[case_id]
        rows.append(row)
    return {"mode": mode, "target_file": {"path": target["path"], "language": target["language"], "content": content}, "cases": rows}


def _method_source(project: Path, method: Mapping[str, Any], manifest: Mapping[str, Any]) -> str:
    from tools.suite_manifest import slices_of

    file_row = next(row for row in manifest["files"] if row["path"] == method["file"])
    content = (Path(project) / method["file"]).read_text(encoding="utf-8")
    slices = slices_of(method["file"], file_row["file_id"], content, [{"symbol_id": method["symbol_id"], "locator": parse_locator(method["locator"], file_row["language"])}])
    member = slices.symbols[method["symbol_id"]]
    return "\n".join(slices.lines[member.start - 1:member.end])


def _automation_cases(project: Path, run_root: Path, state: Mapping[str, Any]) -> list[str]:
    update = state["facts"]["update"]
    document = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8"))
    cases = {case["case_id"]: case for case in document["test_cases"]}
    edited = set(state["facts"]["impact"]["edited"]["cases"])
    return [case_id for case_id in update["changed_cases"] + update["new_cases"]
            if case_id in cases and case_id not in edited and any(not step.get("manual_only") for step in cases[case_id].get("steps") or [])]


def _automation(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> dict[str, Any] | None:
    if state["facts"].get("accepted", {}).get("automation"):
        _goto(run_root, state, "STATIC_REVIEW")
        return None
    case_ids = _automation_cases(project, run_root, state)
    if not case_ids and not state["facts"]["update"]["retired_cases"]:
        _goto(run_root, state, "APPLY")
        return None
    if not case_ids:
        state["facts"]["automation"] = {"content": None, "methods": {}, "removed": []}
        _goto(run_root, state, "APPLY")
        return None
    document = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8"))
    brief = _automation_brief(project, run_root, "update", case_ids, document)
    inputs = [_write(_work(run_root) / "automation-brief.json", brief), _work(run_root) / "document.json"]
    return _task(run_root, "automation", stage="tc-to-autotest:update", skill="tc-to-autotest", inputs=inputs, schema=_automation_schema(),
                 instructions=_AUTOMATION_INSTRUCTIONS["update"], extra={"mode": "update"})


def _spliced(project: Path, run_root: Path, value: Mapping[str, Any], case_ids: Sequence[str], document: Mapping[str, Any], *, mode: str,
             retired: Sequence[str] = ()) -> tuple[str | None, dict[str, Any], list[dict[str, str]]]:
    """The target file with the answer's methods in place, the new method table, and the problems."""
    from tools.suite_manifest import slices_of
    from tools.suite_merge import MergeError, literal_diagnostics, repair_diagnostics, splice

    _suite_dir, manifest, _old = _suite(project, run_root)
    target = manifest["files"][0]
    content = (Path(project) / target["path"]).read_text(encoding="utf-8")
    language = target["language"]
    method_of = {case["case_id"]: case["methods"][0] for case in manifest["cases"] if case["methods"]}
    cases = {case["case_id"]: case for case in document["test_cases"]}
    problems: list[dict[str, str]] = []
    rows = {row.get("case_id"): row for row in value.get("methods") or [] if isinstance(row, Mapping)}
    if set(rows) != set(case_ids):
        problems.append({"path": "/methods", "code": "AUTOMATION_METHODS", "message": "return exactly one method for every case of the task: " + ", ".join(case_ids)})
        return None, {}, problems
    replace, add, table = {}, [], {}
    owner = next((method["locator"].rsplit("#", 1)[0] for method in method_of.values() if "#" in method["locator"]), None)
    for case_id in case_ids:
        row = rows[case_id]
        name = row["method_name"]
        if language == "java" and f" {name}(" not in row["source"]:
            problems.append({"path": f"/methods/{case_id}", "code": "AUTOMATION_METHOD_NAME", "message": f"the source does not declare {name}(…)"})
            continue
        old = method_of.get(case_id)
        if old is not None:
            if old["locator"].rsplit("#", 1)[-1].rsplit(".", 1)[-1] != name:
                problems.append({"path": f"/methods/{case_id}", "code": "AUTOMATION_METHOD_NAME", "message": f"keep the method name of {case_id}"})
                continue
            replace[old["locator"]] = row["source"]
            table[case_id] = {"locator": old["locator"], "symbol_id": old["symbol_id"], "kind": "updated" if mode == "update" else "repaired",
                              "old_source": _method_source(project, old, manifest)}
        elif mode == "update":
            locator = f"{owner}#{name}" if language == "java" else name
            add.append(row["source"])
            table[case_id] = {"locator": locator, "symbol_id": "SYMBOL-" + case_id.removeprefix("TC-"), "kind": "new", "old_source": None}
        else:
            problems.append({"path": f"/methods/{case_id}", "code": "REPAIR_UNKNOWN_METHOD", "message": f"{case_id} has no method to repair"})
    if problems:
        return None, {}, problems
    locators = {method["locator"]: parse_locator(method["locator"], language) for method in method_of.values()}
    removed = [{"name": method_of[case_id]["locator"]} for case_id in retired if case_id in method_of]
    try:
        new_content = splice(target["path"], content, replace=replace, add=add + list(value.get("helpers") or []), remove=removed,
                             locators=locators, imports=list(value.get("imports") or []))
    except MergeError as error:
        return None, {}, error.diagnostics
    symbols = [{"symbol_id": row["symbol_id"], "locator": parse_locator(row["locator"], language)} for row in table.values()]
    try:
        slices = slices_of(target["path"], target["file_id"], new_content, symbols)
    except ValueError as error:
        return None, {}, [{"path": target["path"], "code": "AUTOMATION_FILE_INVALID", "message": f"the file does not split into methods: {error}"}]
    support = "\n".join(line for start, end in slices.support_ranges() for line in slices.lines[start - 1:end])
    for case_id, row in table.items():
        member = slices.symbols[row["symbol_id"]]
        source = "\n".join(slices.lines[member.start - 1:member.end])
        case = cases[case_id]
        found = repair_diagnostics(case, row["old_source"], source, support) if mode == "repair" else literal_diagnostics(case, source, support)
        problems += [{**item, "path": f"{case_id}:{item['path']}"} for item in found]
    return (None if problems else new_content), table, problems


def _check_automation(project: Path, run_root: Path, state: dict[str, Any], label: str, value: Any) -> list[dict[str, str]]:
    mode = "repair" if label == "repair" else "update"
    document = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8")) if mode == "update" else _document(project, run_root, state)
    if mode == "update":
        case_ids = _automation_cases(project, run_root, state)
        retired = [row["case_id"] for row in state["facts"]["update"]["retired_cases"]]
    else:
        case_ids = sorted(state["facts"]["repair"]["case_ids"])
        retired = []
    if not isinstance(value, Mapping):
        return [{"path": "", "code": "TASK_OUTPUT_INVALID", "message": "the answer is an object with methods"}]
    content, table, problems = _spliced(project, run_root, value, case_ids, document, mode=mode, retired=retired)
    if problems:
        return problems
    state = _state(run_root)
    state["facts"]["automation" if mode == "update" else "repair_automation"] = {"content": content, "methods": {case_id: {key: row[key] for key in ("locator", "symbol_id", "kind")} for case_id, row in table.items()},
                                                                                  "removed": retired}
    _save(run_root, state)
    return []


def _static_snapshot(project: Path, run_root: Path, state: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    facts = state["facts"]["automation" if key == "update" else "repair_automation"]
    if not facts.get("content"):
        return None
    _suite_dir, manifest, document = _suite(project, run_root)
    if key == "update":
        document = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8"))
    target = manifest["files"][0]
    cases = {case["case_id"]: case for case in document["test_cases"]}
    changed = sorted(facts["methods"])
    symbols = [{"file_id": target["file_id"], "symbol_id": row["symbol_id"], "locator": parse_locator(row["locator"], target["language"])} for row in facts["methods"].values()]
    relations = [relation for case_id in changed for relation in _relations(cases[case_id], target["file_id"], facts["methods"][case_id]["symbol_id"])]
    automation = {"schema_version": "5.0.0", "stage": "tc-to-autotest", "warnings": [],
                  "artifacts": {"automation_status": "GENERATED", "manual_dispositions": [], "diagnostics": [], "generated_symbols": symbols, "implementation_relations": relations,
                                "generated_files": [{"file_id": target["file_id"], "path": target["path"], "language": target["language"],
                                                     "framework": "pytest" if target["language"] == "python" else "junit5", "content": facts["content"],
                                                     "content_digest": sha256_bytes(facts["content"].encode("utf-8"))}]}}
    from tools.suite_manifest import slices_of

    try:
        slices = slices_of(target["path"], target["file_id"], facts["content"], [{"symbol_id": row["symbol_id"], "locator": row["locator"]} for row in symbols])
        code = "\n".join("\n".join(slices.lines[member.start - 1:member.end]) for member in slices.symbols.values())
    except ValueError:
        code = facts["content"]
    return _review_snapshot(project, run_root, subset_document(document, changed), automation, code=code)


def _static_review(project: Path, run_root: Path, state: dict[str, Any], max_tasks: int) -> dict[str, Any] | None:
    return _review_step(project, run_root, state, "static", lambda: _static_snapshot(project, run_root, state, "update"), "APPLY", max_tasks)


def _apply(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    """Write the reviewed test file; a method a person edited is never replaced (the whole change becomes a proposal)."""
    from tools.suite_manifest import verify

    facts = state["facts"].get("automation") or {}
    applied = True
    if facts.get("content") or facts.get("removed"):
        _suite_dir, manifest, _document = _suite(project, run_root)
        target = manifest["files"][0]
        path = Path(project) / target["path"]
        content = facts.get("content")
        method_of = {case["case_id"]: case["methods"][0] for case in manifest["cases"] if case["methods"]}
        if content is None:
            from tools.suite_merge import splice

            locators = {method["locator"]: parse_locator(method["locator"], target["language"]) for method in method_of.values()}
            content = splice(target["path"], path.read_text(encoding="utf-8"), remove=[{"name": method_of[case_id]["locator"]} for case_id in facts["removed"] if case_id in method_of],
                             locators=locators)
        edits = verify(project, manifest)
        touched = {row["locator"] for row in (facts.get("methods") or {}).values()} | {method_of[case_id]["locator"] for case_id in facts.get("removed") or [] if case_id in method_of}
        conflicts = sorted(touched & set(edits["methods"]))
        if conflicts:
            applied = False
            state["facts"].setdefault("proposals", []).append(
                f"`{target['path']}`: методы {', '.join(f'`{item}`' for item in conflicts)} правил человек — обновление тестов не записано, оно в предложении")
            _write(_work(run_root) / "proposed" / (target["path"].replace("/", "__") + ".json"), {"content": content})
        else:
            path.write_bytes(content.encode("utf-8"))
    state["facts"]["update_applied"] = applied
    state["facts"]["automation_applied"] = applied and bool(facts.get("content") or facts.get("removed"))
    _goto(run_root, state, "SUITE_RUN")
    return None


# ----------------------------------------------------------------------------------- 7.–9. run, triage, repair

def _module(project: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    from tools.pipeline_driver_suite import _module as module_of, _skillsrc

    return module_of(_skillsrc(project), manifest["module_id"])


def _effective_manifest(project: Path, run_root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    """The manifest with this run's methods: updated, new and retired cases (for the suite run and the triage)."""
    _suite_dir, manifest, _document = _suite(project, run_root)
    manifest = copy.deepcopy(manifest)
    automation = (state["facts"].get("automation") or {}) if state["facts"].get("automation_applied") else {}
    target = manifest["files"][0]
    rows = {case["case_id"]: case for case in manifest["cases"]}
    for case_id in automation.get("removed") or []:
        if case_id in rows:
            rows[case_id]["status"] = "RETIRED"
    for case_id, method in (automation.get("methods") or {}).items():
        entry = {"file": target["path"], "symbol_id": method["symbol_id"], "locator": method["locator"], "slice_digest": "sha256:" + "0" * 64}
        if case_id in rows:
            rows[case_id]["methods"] = [entry]
            rows[case_id]["automation"] = "AUTOMATED"
        else:
            manifest["cases"].append({"case_id": case_id, "title": "", "requirement_ids": [], "requirement_keys": [], "requirement_text_digests": {},
                                      "case_digest": "sha256:" + "0" * 64, "automation": "AUTOMATED", "methods": [entry], "zephyr_key": None,
                                      "status": "ACTIVE", "quarantine": None, "last_green_run": None, "strength": None})
    return manifest


def _suite_run(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.suite_run import run_suite

    if state["facts"].get("stop"):
        _goto(run_root, state, "SUMMARY")
        return None
    manifest = _effective_manifest(project, run_root, state)
    result = run_suite(project, manifest, _module(project, manifest), repeats=int(driver._config(run_root)["repeats"]))
    _write(_work(run_root) / "suite-run.json", result)
    _goto(run_root, state, "TRIAGE", suite_run={"methods": len(result["methods"]), "commands": len(result["commands"])})
    return None


def _triage(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.quarantine import quarantine as mark, release
    from tools.suite_failures import analyst_question, bug_report, classify
    from tools.suite_manifest import verify

    repaired_run = _work(run_root) / "repair-run.json"
    run = json.loads((repaired_run if state["facts"].get("repair_ran") and repaired_run.is_file() else _work(run_root) / "suite-run.json").read_text(encoding="utf-8"))
    manifest = _effective_manifest(project, run_root, state)
    _suite_dir, original, _old_document = _suite(project, run_root)
    document = _document(project, run_root, state)
    cases = {case["case_id"]: case for case in document["test_cases"]}
    impact = state["facts"]["impact"]
    changed_keys = set(impact["requirements"]["changed"]) | set(impact["requirements"]["removed"])
    rewritten_cases = set((state["facts"].get("automation") or {}).get("methods") or {}) if state["facts"].get("automation_applied") else set()
    repaired_cases = set((state["facts"].get("repair_automation") or {}).get("methods") or {}) if repaired_run.is_file() else set()
    rewritten = rewritten_cases | repaired_cases
    written = (["automation"] if state["facts"].get("automation_applied") else []) + (["repair_automation"] if (_work(run_root) / "repair-run.json").is_file() else [])
    own = {row["locator"] for key in written for row in ((state["facts"].get(key) or {}).get("methods") or {}).values()}
    edited = set(verify(project, original)["methods"]) - own  # the methods this run rewrote are the package's own
    decisions = dict(state["facts"].get("decisions") or {})
    for row in run["methods"]:
        case_id = row["case_ids"][0]
        requirement_changed = any(key in changed_keys for key in next((case["requirement_keys"] for case in original["cases"] if case["case_id"] == case_id), []))
        if case_id in rewritten:
            requirement_changed = False  # the update already followed the new requirement; a failure now is the product's
        decision = classify(row["runs"], compile_error=row["compile_error"], requirement_changed=requirement_changed, edited_by_person=row["locator"] in edited,
                            quarantined=row["quarantined"])
        if decision["outcome"] == "REPAIR" and state["facts"].get("repair_ran"):
            decision = {"outcome": "QUARANTINE", "reason": "REPAIR_FAILED", "proposal_only": decision["proposal_only"]}
        if decision["outcome"] == "UPDATE" and not decision["proposal_only"]:
            # The update step already ran: a case still failing on a changed requirement was not updated
            # (a person edited it, or the review stopped the update) — quarantine until a person updates it.
            decision = {"outcome": "QUARANTINE", "reason": "ASSERTION_FAILED", "proposal_only": False}
            state["facts"].setdefault("proposals", []).append(
                f"`{case_id}`: требование изменилось, а кейс не обновлён пакетом — обновите его вручную; тест `{row['locator']}` в карантине")
        decisions[row["locator"]] = {**decision, "case_ids": row["case_ids"], "runs": row["runs"], "failure": row["failure"], "file": row["file"]}
    repair = sorted({decisions[locator]["case_ids"][0] for locator, row in decisions.items() if row["outcome"] == "REPAIR" and not row["proposal_only"]
                     and decisions[locator]["case_ids"][0] in cases})
    state["facts"]["decisions"] = decisions
    if repair and not state["facts"].get("repair_ran"):
        state["facts"]["repair"] = {"case_ids": repair, "failures": {decisions[locator]["case_ids"][0]: str(decisions[locator]["failure"] or "")[:4000]
                                                                     for locator in decisions if decisions[locator]["outcome"] == "REPAIR"}}
        _goto(run_root, state, "REPAIR")
        return None
    # Quarantine marks and releases on the package's own methods; every other decision is a proposal.
    target_rows = {row["path"]: row for row in manifest["files"]}
    by_file: dict[str, dict[str, list]] = {}
    quarantined, released, questions, proposals = [], [], list(state["facts"].get("questions") or []), list(state["facts"].get("proposals") or [])
    for locator, row in sorted(decisions.items()):
        case = cases.get(row["case_ids"][0])
        if row["proposal_only"]:
            proposals.append(f"`{locator}` правил человек: предлагается {row['outcome'].lower()} ({row['reason'] or '—'}), пакет метод не менял")
            continue
        file_row = target_rows[row["file"]]
        if row["outcome"] == "QUARANTINE":
            ref = f"run {run_root.name[:8]} {row['case_ids'][0]}"
            by_file.setdefault(row["file"], {"mark": [], "release": []})["mark"].append((parse_locator(locator, file_row["language"]), row["reason"], ref))
            entry = {"locator": locator, "case_ids": row["case_ids"], "reason": row["reason"], "ref": ref}
            if row["reason"] == "BEHAVIOR_CHANGED_WITHOUT_SPEC" and case is not None:
                question = analyst_question(case, failure=row["failure"])
                entry["question"] = question
                entry["bug_report"] = bug_report(case, locator=locator, failure=row["failure"], run_id=run_root.name)
                questions.append(question)
            quarantined.append(entry)
        elif row["outcome"] == "FIXED":
            by_file.setdefault(row["file"], {"mark": [], "release": []})["release"].append(parse_locator(locator, file_row["language"]))
            released.append(locator)
    for path, todo in by_file.items():
        target = Path(project) / path
        content = target.read_text(encoding="utf-8")
        content = release(path, content, todo["release"]) if todo["release"] else content
        content = mark(path, content, todo["mark"]) if todo["mark"] else content
        target.write_bytes(content.encode("utf-8"))
    _goto(run_root, state, "MUTATION", quarantine=quarantined, released=released, questions=questions, proposals=proposals)
    return None


def _repair(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> dict[str, Any] | None:
    if state["facts"].get("accepted", {}).get("repair"):
        _goto(run_root, state, "REPAIR_REVIEW")
        return None
    _suite_dir, _manifest, document = _suite(project, run_root)
    brief = _automation_brief(project, run_root, "repair", state["facts"]["repair"]["case_ids"], document, failures=state["facts"]["repair"]["failures"])
    inputs = [_write(_work(run_root) / "repair-brief.json", brief)]
    return _task(run_root, "repair", stage="tc-to-autotest:repair", skill="tc-to-autotest", inputs=inputs, schema=_automation_schema(),
                 instructions=_AUTOMATION_INSTRUCTIONS["repair"], extra={"mode": "repair"})


def _repair_review(project: Path, run_root: Path, state: dict[str, Any], max_tasks: int) -> dict[str, Any] | None:
    # The static review refusing the repair sends the methods to quarantine as they are (``repair_ran`` without a rerun).
    return _review_step(project, run_root, state, "repair", lambda: _static_snapshot(project, run_root, state, "repair"), "REPAIR_RUN", max_tasks,
                        blocked_step="TRIAGE")


def _repair_run(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.suite_run import run_suite

    facts = state["facts"]["repair_automation"]
    _suite_dir, manifest, _document = _suite(project, run_root)
    target = Path(project) / manifest["files"][0]["path"]
    target.write_bytes(facts["content"].encode("utf-8"))
    effective = _effective_manifest(project, run_root, state)
    locators = {row["locator"] for row in facts["methods"].values()}
    # The whole suite runs again: a build that did not compile ran none of its methods.
    result = run_suite(project, effective, _module(project, effective), repeats=int(driver._config(run_root)["repeats"]))
    _write(_work(run_root) / "repair-run.json", result)
    state["facts"]["repair_ran"] = True
    state["facts"]["repaired"] = sorted(row["locator"] for row in result["methods"] if row["locator"] in locators and row["runs"]
                                        and all(status == "passed" for status in row["runs"]))
    # The triage of the repaired methods replaces their first decisions; the others keep theirs.
    _goto(run_root, state, "TRIAGE")
    return None


# ----------------------------------------------------------------------------------- 10. mutations (opt-in), 11. manifest, 12. summary

def _mutation(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    config = driver._config(run_root)
    if not config.get("mutation"):
        _goto(run_root, state, "MANIFEST")
        return None
    from tools.mutation import StageInputs, measure, settings_of
    from tools.pipeline_driver_suite import _skillsrc

    settings = settings_of(_skillsrc(project))
    if settings is None:
        _goto(run_root, state, "MANIFEST", strength={"status": "NOT_APPLICABLE", "reason": "MUTATION_NOT_ENABLED"})
        return None
    _suite_dir, manifest, document = _suite(project, run_root)
    if (_work(run_root) / "document.json").is_file():
        document = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8"))
    effective = _effective_manifest(project, run_root, state)
    automation = _current_automation(project, effective, document)
    decisions = state["facts"].get("decisions") or {}
    evidence = [{"file_id": symbol["file_id"], "symbol_id": symbol["symbol_id"],
                 "status": "PASSED" if decisions.get(locator_text(symbol["locator"]), {}).get("outcome") in {"PASS", "FIXED"} else "FAILED"}
                for symbol in automation["artifacts"]["generated_symbols"]]
    # PIT 1.30 core has no incremental history (a plugin would be needed): every measurement is complete, and the
    # service directory outside the suite keeps the previous measurements for comparison.
    history = Path(project) / ".pilot-runs" / "suite-strength" / manifest["suite_id"]
    history.mkdir(parents=True, exist_ok=True)
    verdict = "PASS" if all(row["status"] == "PASSED" for row in evidence) else "FAIL"  # the suite run read real test reports
    run = json.loads((_work(run_root) / ("repair-run.json" if (_work(run_root) / "repair-run.json").is_file() else "suite-run.json")).read_text(encoding="utf-8"))
    language = manifest["files"][0]["language"] if manifest["files"] else None
    report = {"verdict": verdict, "evidence_authoritative": True, "execution_evidence": evidence, "target": {"language": language}, "execution": run.get("execution") or {}}
    facts = measure(StageInputs(project=project, workdir=_work(run_root) / "mutation", report=report,
                                automation=automation, document=document, settings=settings))
    per_case = {row["case_id"]: row for row in facts.get("cases") or []}
    if facts["status"] == "MEASURED":
        _write(history / f"{run_root.name}.json", {"run_id": run_root.name, "totals": facts.get("totals"), "cases": facts.get("cases"),
                                                    "requirements": facts.get("requirements"), "source_requirements": facts.get("source_requirements")})
    drops = []
    for case in manifest["cases"]:
        before, after = case.get("strength"), per_case.get(case["case_id"])
        if before and after and before["covered"] and after["covered"] and after["killed"] * before["covered"] < before["killed"] * after["covered"]:
            drops.append({"case_id": case["case_id"], "before": f"{before['killed']}/{before['covered']}", "after": f"{after['killed']}/{after['covered']}"})
    _goto(run_root, state, "MANIFEST", strength={"status": facts["status"], "reason": facts.get("reason_code"), "message": facts.get("message"), "cases": per_case, "totals": facts.get("totals")},
          strength_drops=drops)
    return None


def _manifest(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.pipeline_driver_suite import _skillsrc, package_version
    from tools.code_surface import surface

    suite_dir, manifest, _old_document = _suite(project, run_root)
    document = _document(project, run_root, state)
    scan, _envelope = _scan(project, run_root, manifest)
    if not state["facts"].get("update_applied"):
        # Without an applied update the suite keeps its requirements: the manifest's keys stay (the next run sees the same changes).
        scan = [dict(row, text="", file_digest=next((item["sha256"] for item in manifest["documents"] if item["path"] == row["path"]), "")) for row in manifest["requirements"]]
    effective = _effective_manifest(project, run_root, state)
    automation = _current_automation(project, effective, document)
    decisions = state["facts"].get("decisions") or {}
    quarantine = {row["locator"]: row for row in state["facts"].get("quarantine") or []}
    state_of = {}
    old = {case["case_id"]: case for case in manifest["cases"]}
    for case in effective["cases"]:
        if case["status"] == "RETIRED":
            continue
        locators = [method["locator"] for method in case["methods"]]
        marked = [quarantine[locator] for locator in locators if locator in quarantine]
        green = locators and all(decisions.get(locator, {}).get("outcome") in {"PASS", "FIXED"} for locator in locators)
        previous = old.get(case["case_id"], {})
        if marked:
            entry = {"reason": marked[0]["reason"] if marked[0]["reason"] in {"ASSERTION_FAILED", "BEHAVIOR_CHANGED_WITHOUT_SPEC", "FLAKY", "ENVIRONMENT", "REPAIR_FAILED"} else "ASSERTION_FAILED",
                     "ref": marked[0]["ref"], "since_run": run_root.name}
            if marked[0].get("question"):
                entry["question"] = marked[0]["question"][:2000]
            state_of[case["case_id"]] = {"status": "QUARANTINED", "quarantine": entry, "last_green_run": previous.get("last_green_run")}
        elif previous.get("status") == "QUARANTINED" and not all(decisions.get(locator, {}).get("outcome") == "FIXED" for locator in locators):
            state_of[case["case_id"]] = {"status": "QUARANTINED", "quarantine": previous.get("quarantine"), "last_green_run": previous.get("last_green_run")}
        else:
            state_of[case["case_id"]] = {"status": "ACTIVE", "quarantine": None, "last_green_run": run_root.name if green else previous.get("last_green_run")}
    strength_cases = (state["facts"].get("strength") or {}).get("cases") or {}
    strength = {case_id: {"covered": int(row["covered"]), "killed": int(row["killed"]), "run_id": run_root.name} for case_id, row in strength_cases.items()}
    for case_id, row in old.items():
        if case_id not in strength and row.get("strength"):
            strength[case_id] = row["strength"]
    test_files = {row["file_id"]: {"path": row["path"], "content": row["content"]} for row in automation["artifacts"]["generated_files"]}
    rendered = projections(document)
    module = _module(project, manifest)
    sources = list(((module.get("paths") or {}).get("source")) or ["src/main/java", "src", "app"])
    stop = state["facts"].get("stop")
    new = build_manifest(
        suite_id=manifest["suite_id"], module_id=manifest["module_id"], package_version=package_version(), document=document, identities=scan,
        documents=manifest["documents"] if not scan else [{"path": row["path"], "sha256": row["file_digest"]} for row in {row["path"]: row for row in scan}.values()],
        id_pattern=(_skillsrc(project).get("requirements") or {}).get("id_pattern"), automation=automation, test_files=test_files, case_state=state_of,
        strength=strength, surface=surface(project / str(module.get("root") or "."), sources), suite_dir=suite_dir,
        suite_digests={name: sha256_bytes(data) for name, data in rendered.items()}, source_run=manifest["source_run"],
        last_run={"run_id": run_root.name, "attempt_id": None, "profile": PROFILE, "verification": None if stop else _verdict(decisions), "accepted": None},
        history=[*manifest["history"], {"run_id": run_root.name, "profile": PROFILE, "action": "UPDATED"}])
    # People's edits stay flagged: their cases and methods keep the digests the package last wrote.
    edited = state["facts"]["impact"]["edited"]
    for row in new["cases"]:
        if row["case_id"] in edited["cases"] and row["case_id"] in old:
            row["case_digest"] = old[row["case_id"]]["case_digest"]
        for method in row["methods"]:
            previous = next((item for item in old.get(row["case_id"], {}).get("methods", []) if item["locator"] == method["locator"]), None)
            if method["locator"] in edited["methods"] and previous is not None:
                method["slice_digest"] = previous["slice_digest"]
    retired = [dict(old[case_id], status="RETIRED", methods=[], automation="MANUAL", quarantine=None)
               for case_id in sorted(set(old) - {row["case_id"] for row in new["cases"]})]
    new["cases"] = sorted([*new["cases"], *retired], key=lambda row: row["case_id"])
    replace = {name: row["sha256"] for name, row in manifest["suite_files"].items()}
    edited_files = set(edited["suite_files"])
    payloads = {}
    for name, data in rendered.items():
        path = manifest["suite_files"][name]["path"]
        if path in edited_files and name != "canonical":
            state["facts"].setdefault("proposals", []).append(f"`{path}` правил человек: новая проекция не записана")
            new["suite_files"][name]["sha256"] = manifest["suite_files"][name]["sha256"]
            continue
        payloads[name] = data
    current = {name: sha256_bytes((Path(project) / row["path"]).read_bytes()) for name, row in manifest["suite_files"].items() if (Path(project) / row["path"]).is_file()}
    replace.update(current)
    payloads["manifest"] = canonical_bytes(new)
    replace["manifest"] = sha256_bytes((Path(project) / suite_dir / MANIFEST).read_bytes())
    write_suite(project, suite_dir, payloads, replace=replace)
    _goto(run_root, state, "SUMMARY", manifest={"cases": len(new["cases"]), "retired": len(retired)})
    return None


def _verdict(decisions: Mapping[str, Any]) -> str | None:
    if not decisions:
        return None
    return "PASS" if all(row["outcome"] in {"PASS", "FIXED", "NOT_RUN"} for row in decisions.values()) else "FAIL"


def summary_facts(project: Path, run_root: Path, state: Mapping[str, Any]) -> dict[str, Any]:
    facts = state["facts"]
    impact = facts.get("impact") or {"requirements": {"added": [], "changed": [], "removed": [], "renamed": []}, "cases": {"to_update": []}}
    update = facts.get("update") or {}
    before_document = json.loads((_work(run_root) / "suite-document.json").read_text(encoding="utf-8")) if (_work(run_root) / "suite-document.json").is_file() else None
    after_document = json.loads((_work(run_root) / "document.json").read_text(encoding="utf-8")) if (_work(run_root) / "document.json").is_file() else None
    applied = bool(after_document) and bool(facts.get("update_applied"))
    changed = sorted(update.get("changed_cases") or []) if applied else []
    new = sorted(update.get("new_cases") or []) if applied else []
    texts = {}
    for case_id in changed + new:
        before = next((case for case in (before_document or {}).get("test_cases", []) if case["case_id"] == case_id), None)
        after = next((case for case in (after_document or {}).get("test_cases", []) if case["case_id"] == case_id), None)
        texts[case_id] = (before, after)
    automation = facts.get("automation") or {}
    methods = automation.get("methods") or {}
    decisions = facts.get("decisions") or {}
    total = len(_document(project, run_root, state).get("test_cases", []))
    strength = None
    if (facts.get("strength") or {}).get("totals"):
        totals = facts["strength"]["totals"]
        strength = {"before": "—", "after": f"{totals.get('killed', 0)}/{totals.get('killed', 0) + totals.get('survived', 0)}"}
    return {
        "suite_id": (read_suite(project, driver._config(run_root)["suite_dir"]) or {}).get("suite_id", "?"), "run_id": run_root.name,
        "suite_dir": driver._config(run_root)["suite_dir"], "patch_name": "suite.patch", "migration": facts.get("migration"),
        "requirements": impact["requirements"],
        "cases": {"changed": changed, "new": new, "retired": (update.get("retired_cases") or []) if applied else [],
                  "unchanged": max(0, total - len(changed) - len(new))},
        "case_texts": texts,
        "tests": {"updated": sorted(row["locator"] for row in methods.values() if row["kind"] == "updated") if applied else [],
                  "new": sorted(row["locator"] for row in methods.values() if row["kind"] == "new") if applied else [],
                  "repaired": sorted(facts.get("repaired") or []), "removed": sorted(automation.get("removed") or []) if applied else [],
                  "run": {"methods": len(decisions), "passed": sum(row["outcome"] in {"PASS", "FIXED"} for row in decisions.values()),
                          "failed": sum(row["outcome"] not in {"PASS", "FIXED", "NOT_RUN"} for row in decisions.values())}},
        "quarantine": facts.get("quarantine") or [], "released": facts.get("released") or [], "questions": sorted(set(facts.get("questions") or [])),
        "proposals": facts.get("proposals") or [], "strength": strength, "strength_drops": facts.get("strength_drops") or [],
        "review_blocked": _blocked(state), "review_findings": facts.get("review_findings") or [],
        "review_notes": [row for review in (facts.get("reviews") or {}).values() for row in review.get("findings") or []],
        "stop": facts.get("stop"),
    }


def _summary(project: Path, run_root: Path, state: dict[str, Any], _max: int) -> None:
    from tools.suite_summary import pr_description, unified_patch

    work = _work(run_root)
    facts = summary_facts(project, run_root, state)
    before = {path: None if value is None else bytes.fromhex(value) for path, value in json.loads((work / "before.json").read_text(encoding="utf-8")).items()}
    after = {path: (Path(project) / path).read_bytes() if (Path(project) / path).is_file() else None for path in sorted(set(before) | set(_owned_files(project, facts["suite_dir"])))}
    description = pr_description(facts)
    (work / "pr-description.md").write_text(description, encoding="utf-8", newline="\n")
    (work / "suite.patch").write_text(unified_patch(before, after), encoding="utf-8", newline="\n")
    stop = facts["stop"]
    outcome = "STOPPED" if stop else "REVIEW_BLOCKED" if facts["review_blocked"] else "UPDATED" if any(after[path] != before.get(path) for path in after) else "NO_CHANGES"
    result = {"schema_version": "1.0.0", "status": "terminal", "profile": PROFILE, "run_id": run_root.name, "outcome": outcome, "exit_code": 2 if stop and stop["reason"] in {"SUITE_MISSING", "SUITE_MANIFEST_INVALID"} else 0 if outcome in {"UPDATED", "NO_CHANGES"} else 1,
              "reason_code": None if not stop else stop["reason"], "message": None if not stop else stop["message"],
              "counts": {"requirements": {name: len(value) for name, value in facts["requirements"].items()},
                         "cases": {name: (len(value) if isinstance(value, list) else value) for name, value in facts["cases"].items()},
                         "quarantined": len(facts["quarantine"]), "released": len(facts["released"]), "questions": len(facts["questions"]),
                         "proposals": len(facts["proposals"]), "repaired": len(facts["tests"]["repaired"])},
              "reviews": {key: {name: value[name] for name in ("eligible", "parts", "input_bytes")} for key, value in (state["facts"].get("reviews") or {}).items()},
              "paths": {"pr_description": str(work / "pr-description.md"), "patch": str(work / "suite.patch"), "result": str(work / "suite-update-result.json"),
                        "suite_dir": str(Path(project) / facts["suite_dir"])},
              "accepted": None}
    from tools.schema_validation import schema_diagnostics

    problems = schema_diagnostics(result, Path(driver.ROOT) / "schemas" / "suite-update-result.schema.json", Path(driver.ROOT))
    if problems:
        raise driver.DriverError("DRIVER_FAILURE", f"suite-update result is invalid: {problems[:3]}")
    _write(work / "suite-update-result.json", result)
    _goto(run_root, state, "DONE", result=result)
    return None


def status(project: Path, run_root: Path) -> dict[str, Any]:
    state = _state(run_root)
    return {"action": "status", "run_id": run_root.name, "profile": PROFILE, "step": state["step"],
            "result": state["facts"].get("result")}


_STEPS = {"MIGRATION": _migration, "IMPACT": _impact, "UPDATE": _update, "REVIEW": _review, "AUTOMATION": _automation,
          "STATIC_REVIEW": _static_review, "APPLY": _apply, "SUITE_RUN": _suite_run, "TRIAGE": _triage, "REPAIR": _repair,
          "REPAIR_REVIEW": _repair_review, "REPAIR_RUN": _repair_run, "MUTATION": _mutation, "MANIFEST": _manifest, "SUMMARY": _summary}
_CHECKS = {"update": _check_update, "review-canonical": _check_review, "review-static": _check_review, "review-repair": _check_review,
           "automation": _check_automation, "repair": _check_automation}
