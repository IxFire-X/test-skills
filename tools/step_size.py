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
``--stage tc-generator:<batch>|tc-reviewer:canonical|tc-to-autotest:rN|autotest-reviewer:rN`` takes the files behind the
``input_digests`` of the published model request of that stage (``stage_inputs``, FaT-U7: by the journal of the attempt, the
same answer as ``step-inputs`` and the run report): an input the journal does not bind is ``unresolved``
(``STEP_INPUT_UNRESOLVED``, not a stop), nothing is guessed.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

from tools.json_cli import JsonArgumentParser, cli_error

PACK = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = 500_000
DEFAULT_WINDOW = 1_000_000  # FaT-BO: DeepSeek 239k tokens / 0.239 tok/B
OVER_BUDGET = "STEP_OVER_BUDGET"
RECORD = "step-sizes.jsonl"
OUTPUT_LARGE = "STEP_OUTPUT_LARGE"
OUTPUT_TRUNCATED = "MODEL_OUTPUT_TRUNCATED"
OUTPUT_SIZES = "output-sizes.jsonl"
TRUNCATED_HINT = ("похоже, ответ упёрся в лимит выхода модели; повтори стадию один раз (как при ошибке схемы) и попроси "
                  "ответить тем же JSON целиком, компактно, без отступов и переносов — так ответ почти вдвое меньше")
TRUNCATED_FIX = ("повтори стадию один раз (--transport-attempts 2) и попроси модель ответить тем же JSON целиком, компактно, без отступов "
                 "и переносов; дальше — finish, если повтор тоже оборван")
DEFAULT_OUTPUT_WARN = 128_000  # ≈ 31 000 tokens at 0.24 tok/B; the real output limit of the work model is not known yet
TOKENS_PER_BYTE = 0.24
_STAGE_ROLES = {"context-marker": "context-marker"}
INPUT_UNRESOLVED = "STEP_INPUT_UNRESOLVED"
_REQUEST_STAGES = ("tc-generator", "tc-reviewer", "tc-to-autotest", "autotest-reviewer")


def whole_limit(value: Any) -> int | None:
    """A ``.skillsrc`` limit as an int: an integer, or a float that is whole (``2000.0``, ``2e3``, ``0.0`` — the schema's ``type: integer`` accepts them,
    JSON Schema says they are integers); None for anything else (missing, boolean, fractional).  The one reader of the limits, so that a limit written
    as ``2e3`` is honoured and never silently replaced by the default."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


LIMITS_DRIFT_NOTE = ".skillsrc изменён после scan: пороги по умолчанию; новый прогон возьмёт новые"


def _live_limits(project: Path) -> Mapping[str, Any]:
    path = Path(project) / ".skillsrc"
    if not path.is_file():
        return {}
    from tools.skillsrc_manifest import load_skillsrc

    return load_skillsrc(path).get("limits") or {}


def run_limits(project: Path, root: Path, state: Any, attempt_id: str) -> tuple[Mapping[str, Any], str | None]:
    """V9a (A1-6): the ``limits`` of the ``.skillsrc`` the run froze — ``(limits, note)``.  The baseline keeps the digest of the file's bytes, not its
    values: the live file counts only while its bytes are those of the baseline; edited since the scan it gives ``({}, LIMITS_DRIFT_NOTE)`` — the defaults,
    named.  Warnings and measurements only: the limits that shape a plan are read at plan time, before it is published."""
    from tools.pilot_state import frozen_skillsrc_matches

    try:
        attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
    except (KeyError, StopIteration, TypeError):
        return {}, None
    matches = frozen_skillsrc_matches(project, root, attempt)
    if matches is None:
        return {}, None  # no .skillsrc or no frozen baseline to compare with: the defaults, as for a project without the file
    if not matches:
        return {}, LIMITS_DRIFT_NOTE
    try:
        return _live_limits(project), None
    except (OSError, KeyError, TypeError, ValueError):
        return {}, None


def _limit(project: Path, key: str, default: int, *, zero: bool = False, limits: Mapping[str, Any] | None = None) -> int:
    value = whole_limit((_live_limits(project) if limits is None else limits).get(key))
    return value if value is not None and (value > 0 or zero and value == 0) else default


def step_budget(project: Path, limits: Mapping[str, Any] | None = None) -> int:
    return _limit(project, "step_input_bytes", DEFAULT_BUDGET, limits=limits)


def step_window(project: Path) -> int:
    """FaT-BO: the model window in bytes (``.skillsrc`` ``limits.step_window_bytes``); 0 turns the tc-generator split off."""
    return _limit(project, "step_window_bytes", DEFAULT_WINDOW, zero=True)


class GeneratorContextNotImplemented(ValueError):
    code = "GENERATOR_CONTEXT_NOT_IMPLEMENTED"


def generator_context(project: Path) -> str:
    """FaT-P3: ``.skillsrc`` ``limits.generator_context`` — ``all`` (default: every generator batch reads the context receipts
    of its plan) or ``own``.  ``own`` (the receipts of the batch's own group plus an index of the others) waits for the
    measurement on the PQ1 line and a definition of «own group»; ``plan-batches`` refuses it with a code."""
    path = Path(project) / ".skillsrc"
    if not path.is_file():
        return "all"
    from tools.skillsrc_manifest import load_skillsrc

    value = (load_skillsrc(path).get("limits") or {}).get("generator_context")
    return value if value in {"all", "own"} else "all"


def require_generator_context_all(project: Path) -> None:
    if generator_context(project) == "own":
        raise GeneratorContextNotImplemented(
            "GENERATOR_CONTEXT_NOT_IMPLEMENTED: limits.generator_context: own ещё не реализовано — оно ждёт замера на линейке PQ1 "
            "и определения «своей группы»; fix: убери ключ из .skillsrc или поставь all (по умолчанию), см. plan-new P3")


def looks_truncated(text: str) -> bool:
    """FaT-O2: the JSON ends inside a string or with an open bracket — cut in the middle, not merely malformed."""
    depth, in_string, escape = 0, False, False
    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
    return bool(text.strip()) and (in_string or depth > 0)


def truncated_message(byte_count: int) -> str:
    return f"{OUTPUT_TRUNCATED}: ответ модели оборван на {byte_count} Б (JSON не закрыт) — {TRUNCATED_HINT}"


def record_output(project: Path, root: Path, step: str, response_digest: str, raw_bytes: int) -> None:
    """FaT-O2: the raw answer size by its journal digest, for the report (only sizes; never fails the step)."""
    from datetime import datetime, timezone

    from tools.confined_output import atomic_write_confined_bytes_at_root

    try:
        path = Path(root) / OUTPUT_SIZES
        before = path.read_bytes() if path.is_file() else b""
        row = {"step": step, "response_digest": response_digest, "raw_bytes": int(raw_bytes),
               "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
        atomic_write_confined_bytes_at_root(Path(project), Path(root), path, before + json.dumps(row, sort_keys=True).encode("utf-8") + b"\n")
    except (OSError, ValueError):
        pass


def output_sizes(root: Path) -> dict[str, int]:
    """FaT-O2: raw answer bytes by response digest (the last record of a digest wins)."""
    sizes: dict[str, int] = {}
    try:
        lines = (Path(root) / OUTPUT_SIZES).read_text(encoding="utf-8").splitlines()
    except OSError:
        return sizes
    for line in lines:
        try:
            row = json.loads(line)
            sizes[str(row["response_digest"])] = int(row["raw_bytes"])
        except (ValueError, KeyError, TypeError):
            continue
    return sizes


def output_warning(project: Path, byte_count: int, limits: Mapping[str, Any] | None = None) -> str | None:
    """FaT-O: ``STEP_OUTPUT_LARGE`` when one model answer is over the output warning threshold, else ``None``.  V9a: a run passes its frozen ``limits``
    (``run_limits``); without them the live ``.skillsrc`` is read."""
    limit = DEFAULT_OUTPUT_WARN
    try:  # called after the answer is published: an unreadable .skillsrc must not fail the step, the default applies
        value = whole_limit((_live_limits(project) if limits is None else limits).get("step_output_warn_bytes"))
        limit = value if value is not None and value > 0 else DEFAULT_OUTPUT_WARN
    except (OSError, ValueError):  # SkillsrcError is a ValueError
        limit = DEFAULT_OUTPUT_WARN
    return OUTPUT_LARGE if byte_count > limit else None


def role_files(role: str) -> list[Path]:
    base = PACK / "skills" / role
    if not (base / "SKILL.md").is_file():
        raise ValueError(f"unknown role: {role}")
    # FaT-B: tc-to-autotest also reads assets/ (the Java/Python conventions)
    return [base / "SKILL.md", *sorted(path for folder in ("references", "assets") for path in (base / folder).rglob("*") if path.is_file())]


def _requirement_files(project: Path, root: Path) -> list[Path]:
    """The documents a requirements digest stands for: ``--docs`` of the run's scan request (the baseline keeps only the
    digest of their projection, ``run_pipeline`` requirement) and, in an import with documents, the CSV whose cases the
    requirements step links (FaT-IMP3; without documents that step is answered by code from the CSV)."""
    from tools.cases_import import cases_path, docs_run
    from tools.pilot_state import read_run

    request = read_run(root)["manifest"].get("scan_request") or {}
    from tools.doc_sections import resolve_spec, view_name

    files: list[Path] = []
    for raw in request.get("docs") or []:
        path, heading = resolve_spec(project, raw)  # FaT-P3: the model reads the view of path#Section, not the file
        if heading is None:
            files.append(Path(path) if Path(path).is_absolute() else project / path)
        else:
            candidate = Path(path)
            rel = (candidate if candidate.is_absolute() else project / candidate).resolve().relative_to(project.resolve()).as_posix()
            files.append(root / "doc-sections" / view_name(rel, heading))
    if docs_run(root):
        csv = Path(str(cases_path(root)))
        files.append(csv if csv.is_absolute() else project / csv)
    return list(dict.fromkeys(files))


def journal_files(project: Path, run_id: str, stage: str, batch: int | None) -> list[Path]:
    """The input files of ``stage`` as the journal knows them before the call (context-marker only)."""
    if stage not in {"context-marker", "context-marker:requirements"}:
        raise ValueError("the journal knows the input of context-marker only; give --files for other steps")
    from tools.pilot_state import derive_state, read_context_selection

    project = Path(project).resolve()
    root = project / ".pilot-runs" / run_id
    attempts = derive_state(root)["attempts"]
    if not attempts:
        raise ValueError("the run has no attempt yet")
    attempt = attempts[-1]
    files: list[Path] = []
    selected = [str(event["artifact_digest"]) for event in derive_state(root)["events"]
                if event.get("attempt_id") == attempt["attempt_id"] and event["event_type"] == "CONTEXT_SELECTED"]
    for digest in sorted(dict.fromkeys(selected)):  # V9a: the receipts the journal selected, read the strict way — never a glob of the folder
        receipt = read_context_selection(root, str(attempt["attempt_id"]), digest)
        if batch is None or receipt.get("batch_index") == batch:
            files.extend(project / item["project_path"] for item in receipt["files"])
    answer = next((str(event["artifact_digest"]) for event in derive_state(root)["events"]
                   if event.get("attempt_id") == attempt["attempt_id"] and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "context-marker:requirements"), None)
    if stage == "context-marker:requirements":
        # FaT-Q2 (2.3): the requirements step of the marker in parts reads the requirement documents only
        return _requirement_files(project, root)
    if batch is not None and answer is not None:
        # FaT-5: a part of the marker reads the requirements answer, not the documents again.
        files.append(root / "model-stage-artifacts" / str(attempt["attempt_id"]) / (answer.removeprefix("sha256:") + ".json"))
    else:
        files.extend(_requirement_files(project, root))
    return list(dict.fromkeys(files))


def _compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


_RECEIPT_SLOTS = ("batch-plans/{a}.json", "generator-headers/{a}.json", "prior-capabilities/{a}.*.json", "imported-cases/{a}.*.json",
                  "imported-cases/{a}.json", "reviewer-session-boundaries/{a}.json", "automation-review-boundaries/{a}.*.json",
                  "artifacts/{a}/*")  # FaT-10d: run artifacts of the attempt (publish_run_artifact_bytes), also without a suffix
_BUNDLES = ("cases", "candidate-bundle")  # where publish_test_case_bundle writes the document of the run


class _Journal:
    """FaT-U7: what the journal of one attempt binds, by digest — files that are an input as a whole (more than one is
    ``ambiguous``), objects kept inside a carrier ``(carrier, key, bytes)``, inputs that stand for project files (the
    requirement documents; the files of a context receipt, ``context``) and the canonical documents the run keeps only by
    digest (the assembled candidate; the effective document, with its carrier).  Only the attempt's own slots are read —
    never the agent's ``tmp/``, never a walk of the run."""

    def __init__(self, project: Path, root: Path, state: Any, attempt_id: str) -> None:
        self.root = root
        self.project = project
        self.files: dict[str, list[Path]] = {}
        self.carried: dict[str, tuple[Path, str, int]] = {}
        self.expands: dict[str, tuple[list[Path], bool]] = {}
        self.documents: dict[str, Path | None] = {}
        self.bundled: dict[str, Path] = {}
        attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
        from tools.pilot_state import frozen_baseline

        baseline = frozen_baseline(root, attempt)
        self.expands[str(baseline["requirements"]["digest"])] = (_requirement_files(project, root), False)
        inventory = str(baseline["inventory_digest"])
        self.inventory_digest = inventory
        self.attempt_id = attempt_id
        self._file(inventory, root / "inventories" / (inventory.removeprefix("sha256:") + ".json"))
        events = [event for event in state["events"] if event.get("attempt_id") == attempt_id]
        for event in events:
            digest = str(event.get("artifact_digest"))
            if event["event_type"] == "CONTEXT_SELECTED":
                from tools.pilot_state import read_context_selection

                receipt = read_context_selection(root, attempt_id, digest)  # V9a: the strict reader, not the bytes of a file at that name
                self.expands[digest] = ([project / item["project_path"] for item in receipt["files"]], True)
            elif event["event_type"] in {"MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED"} and event.get("stage_instance_id") != "assembly":
                self._file(digest, root / "model-stage-artifacts" / attempt_id / (digest.removeprefix("sha256:") + ".json"))
            elif event["event_type"] == "CANDIDATE_PUBLISHED":
                self.documents[digest] = None
        for pattern in _RECEIPT_SLOTS:
            for path in sorted(root.glob(pattern.format(a=attempt_id))):
                if not path.is_file() or path.suffix not in {".json", ""}:
                    continue
                raw = path.read_bytes()
                if pattern.startswith("artifacts/"):  # publish_run_artifact_bytes names a run artifact by the sha256 of its bytes
                    self._file("sha256:" + hashlib.sha256(raw).hexdigest(), path)
                try:
                    value = json.loads(raw.decode("utf-8"))
                except ValueError:  # a run artifact that is no JSON names no other digest
                    continue
                if isinstance(value, dict):
                    for nested in (value, *(item for item in value.values() if isinstance(item, dict))):
                        if isinstance(nested.get("digest"), str):
                            self._file(nested["digest"], path)
        effective = root / "effective-canonicals" / f"{attempt_id}.json"
        if effective.is_file():
            value = json.loads(effective.read_text(encoding="utf-8"))
            self.documents[str(value["document_digest"])] = effective  # read-effective gives the document with its carrier
            receipt = value["effective_bundle_receipt"]
            if isinstance(receipt.get("json_path"), str) and receipt.get("document_sha256") == value["document_digest"]:
                self.bundled[str(value["document_digest"])] = Path(receipt["json_path"])  # the bundle file the receipt names
            self.carried[str(value["effective_bundle_receipt_digest"])] = (effective, "effective_bundle_receipt", len(_compact(value["effective_bundle_receipt"])))
        ledgers = [event for event in events if event.get("batch_id") == "reviewer-ledger-v1" and event["event_type"] == "ARTIFACT_READ_BACK"]
        if ledgers:
            ledger = root / "reviewer-session-ledgers" / attempt_id / (str(ledgers[0]["artifact_digest"]).removeprefix("sha256:") + ".json")
            package = json.loads(ledger.read_text(encoding="utf-8"))["package_binding"]
            self.carried[str(package["package_digest"])] = (ledger, "package_binding", len(_compact(package)))

    def plan(self, state: Any) -> Mapping[str, Any]:
        """The batch plan of the attempt as the journal binds it (V7); ``{}`` before it is published."""
        from tools.pilot_state import ModelInputsMismatch, generator_input

        if getattr(self, "_plan", None):
            return self._plan
        try:
            self._plan = generator_input(self.project, self.root, state, self.attempt_id, "plan")
        except (ModelInputsMismatch, OSError, KeyError, ValueError, IndexError):
            return {}
        return self._plan

    def plan_digest(self, state: Any) -> str | None:
        return self.plan(state).get("digest")

    def _file(self, digest: str, path: Path) -> None:
        paths = self.files.setdefault(digest, [])
        if path not in paths:
            paths.append(path)

    def outside(self, digest: str) -> bool:
        """The bundle file a receipt names is the agent's data: a path that leaves the project is never measured."""
        named = self.bundled.get(digest)
        if named is None:
            return False
        target = (named if named.is_absolute() else self.root / named).resolve()
        return not target.is_relative_to(self.project.resolve())

    def document(self, digest: str) -> Path | None:
        """A canonical document the run keeps by digest: the bundle file its receipt names (inside the project, see
        :meth:`outside`), else its file in the bundle folder of the run, else its carrier."""
        from tools.canonical_document import document_sha256

        named = self.bundled.get(digest)
        if named is not None and not self.outside(digest) and (named if named.is_absolute() else self.root / named).is_file():
            return named if named.is_absolute() else self.root / named
        for path in sorted(path for folder in _BUNDLES for path in (self.root / folder).rglob("*.json")):
            try:
                if document_sha256(json.loads(path.read_text(encoding="utf-8"))) == digest:
                    return path
            except (OSError, ValueError, TypeError, KeyError, AttributeError):  # a projection that is no canonical document
                continue
        return self.documents.get(digest)


def _requested(root: Path, state: Any, attempt_id: str, stage: str) -> tuple[str, list[str]]:
    """The digest and the input digests of the last published request of ``stage``, from its journal slot.  V9a: the bytes are the ones the journal
    sealed (the body hashes to the digest field and to the digest of the event); the predecessor check of ``read_model_request`` is not repeated
    — the request was checked when it was published."""
    from tools.pilot_state import _model_request_target, check_sealed_value

    requested = [event for event in state["events"] if event.get("attempt_id") == attempt_id
                 and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage]
    if not requested:
        raise ValueError(f"no model request of {stage} yet: publish it first with tools.api model-request")
    request_digest = str(requested[-1]["artifact_digest"])
    request = json.loads(_model_request_target(root, attempt_id, request_digest).read_text(encoding="utf-8"))
    check_sealed_value(request, "model request")
    if request.get("digest") != request_digest or request.get("stage_instance_id") != stage:
        raise ValueError(f"the model request of {stage} does not match its journal event")
    return request_digest, [str(digest) for digest in request.get("input_digests") or []]


def stage_inputs(run_root: Path, stage: str, *, attempt_id: str | None = None, write_views: bool = True,
                 state: Any = None, journals: dict[str, Any] | None = None) -> dict[str, Any]:
    """FaT-U7 (audit U7, F5): the files and bytes behind the ``input_digests`` of the published model request of ``stage``,
    from the journal — the one answer of ``step-inputs``, ``tools.step_size --run --stage`` and the run report.

    A context receipt or a requirements digest opens into the project files it stands for; an object kept inside a carrier
    is a ``binding`` with its own bytes, or 0 when the step reads the carrier anyway.  The view of the document replaces it
    (``basis: вид``: the reviewer's candidate, the autotest view of ``tc-to-autotest``); a generator batch also reads the map
    of existing project tests (``basis: карта``, FaT-EC).  ``tc-to-autotest:r2`` also reads the answers of r1 and of its
    review (``basis: r1``/``ревью r1``) — they bind the correction, not the request.  An input the journal does not bind is
    ``unresolved`` (``not-in-journal``), never guessed.  ``write_views=False`` (the report) measures the map without
    writing it; the report also passes its ``state`` and one ``journals`` cache for all its calls."""
    from tools.pilot_state import derive_state

    root = Path(run_root).resolve()
    project = root.parent.parent
    state = state if state is not None else derive_state(root)
    if not state["attempts"]:
        raise ValueError("the run has no attempt yet")
    attempt_id = attempt_id or str(state["attempts"][-1]["attempt_id"])
    request_digest, digests = _requested(root, state, attempt_id, stage)
    journals = {} if journals is None else journals
    journal = journals.get(attempt_id) or journals.setdefault(attempt_id, _Journal(project, root, state, attempt_id))
    views: dict[int, Path] = {}
    if digests and stage.startswith("tc-to-autotest:") and autotest_view(root, digests[0]).is_file():
        views[0] = autotest_view(root, digests[0])
    elif digests and stage.startswith("tc-reviewer:") and (root / "review-views" / (digests[0].removeprefix("sha256:") + ".md")).is_file():
        views[0] = root / "review-views" / (digests[0].removeprefix("sha256:") + ".md")  # FaT-CV2: the reviewer reads the view
    files: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    contexts: list[str] = []
    for position, digest in enumerate(digests):
        if position in views:
            files.append({"digest": digest, "path": str(views[position]), "bytes": views[position].stat().st_size, "basis": "вид"})
        elif digest == journal.inventory_digest:  # V7 (O-15): the model reads the projection of the inventory, not the receipt
            data = inventory_view_bytes(project, root, digest)
            target = inventory_view_path(root, digest)
            _write_view(project, root, target, data, write_views)
            files.append({"digest": digest, "path": str(target), "bytes": len(data), "basis": INVENTORY_VIEW})
        elif stage.startswith("tc-generator:") and digest == journal.plan_digest(state):  # V7 (O-16): the plan without the texts
            data = plan_view_bytes(journal.plan(state))
            target = plan_view_path(root, digest)
            _write_view(project, root, target, data, write_views)
            files.append({"digest": digest, "path": str(target), "bytes": len(data), "basis": PLAN_VIEW})
        elif digest in journal.expands:
            targets, context = journal.expands[digest]
            files.extend({"digest": digest, "path": str(target), "bytes": target.stat().st_size} for target in targets)
            if context and stage.startswith("tc-generator:"):
                contexts.append(digest)
        elif len(paths := [path for path in journal.files.get(digest, []) if path.is_file()]) == 1:
            files.append({"digest": digest, "path": str(paths[0]), "bytes": paths[0].stat().st_size})
        elif paths:
            unresolved.append({"digest": digest, "reason": "ambiguous"})  # two journal files claim one digest: none is chosen
        elif journal.outside(digest):
            unresolved.append({"digest": digest, "reason": "outside-project"})  # the receipt names a file outside the project
        elif digest in journal.documents and (document := journal.document(digest)) is not None:
            files.append({"digest": digest, "path": str(document), "bytes": document.stat().st_size})
        elif digest in journal.carried:
            carrier, key, size = journal.carried[digest]
            read = any(row["path"] == str(carrier) for row in files)
            bindings.append({"digest": digest, "carrier": str(carrier), "key": key, "bytes": 0 if read else size})
        else:
            unresolved.append({"digest": digest, "reason": "not-in-journal"})
    if stage == "tc-to-autotest:r2":
        for basis, source, event_type in (("r1", "tc-to-autotest:r1", "CANDIDATE_PUBLISHED"), ("ревью r1", "autotest-reviewer:r1", "MODEL_RESPONSE_RECEIVED")):
            answer = next((str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id
                           and event.get("stage_instance_id") == source and event["event_type"] == event_type), None)
            paths = [path for path in journal.files.get(answer or "", []) if path.is_file()]
            if len(paths) == 1:
                files.append({"digest": answer, "path": str(paths[0]), "bytes": paths[0].stat().st_size, "basis": basis})
            else:
                unresolved.append({"digest": answer, "source": source, "reason": "not-in-journal"})  # e.g. an r2 requested before r1
    if contexts:
        # FaT-EC: the map of existing project tests for the context — built (again, when its bytes differ) from the
        # frozen inventory and the receipts; no input digest of its own, like the autotest view.  FaT-AU3: one map for
        # all the context receipts of the batch, named by the first
        from tools.existing_tests import context_view, ensure_view, map_path

        try:  # the map is a hint: a context the code cannot read back leaves it out, the step stays measured (as the report did)
            if write_views:
                existing = ensure_view(root, contexts)
                size = existing.stat().st_size if existing is not None else 0
            else:
                size = len(context_view(root, contexts, attempt_id))
                existing = map_path(root, contexts[0]) if size else None
        except (KeyError, OSError, TypeError, ValueError):
            existing = None
        if existing is not None:
            files.append({"digest": contexts[0], "path": str(existing), "bytes": size, "basis": "карта"})
    return {"stage": stage, "attempt_id": attempt_id, "request_digest": request_digest, "files": files, "bindings": bindings,
            "unresolved": unresolved}


INVENTORY_VIEW = "inventory-view-v1"
PLAN_VIEW = "plan-view-v1"


def inventory_view_path(root: Path, inventory_digest: str) -> Path:
    return Path(root) / "review-views" / (str(inventory_digest).removeprefix("sha256:") + ".inventory-view.json")


def plan_view_path(root: Path, plan_digest: str) -> Path:
    return Path(root) / "review-views" / (str(plan_digest).removeprefix("sha256:") + ".plan-view.json")


def inventory_view_bytes(project: Path, root: Path, inventory_digest: str) -> bytes:
    """V7 (O-15): what the model reads of the inventory - the path, the kind and the size of each file.  A pure function of the
    inventory receipt the journal binds (INVENTORY_READY); the digests and opaque ids stay in the receipt and in ``input_digests``."""
    from tools.pilot_state import frozen_inventory

    inventory = frozen_inventory(project, root, inventory_digest)
    return _compact({"view": INVENTORY_VIEW, "inventory_digest": inventory_digest, "module": inventory.get("module"),
                     "columns": ["path", "kind", "size"],
                     "files": [[row["project_path"], row.get("kind"), row["size"]]
                               for row in sorted(inventory["files"], key=lambda item: str(item["project_path"]))]})


def plan_view_bytes(plan: Mapping[str, Any]) -> bytes:
    """V7 (O-16): the batch plan without the texts of the source requirements - the generator reads them in the markup (the plan
    rows are those of the marker, byte for byte).  The plan the journal keeps is not changed."""
    rows = [{key: value for key, value in row.items() if key != "text"} for row in plan.get("source_requirements") or []]
    return _compact({**{key: value for key, value in plan.items() if key != "source_requirements"}, "source_requirements": rows,
                     "view": PLAN_VIEW, "texts": "тексты требований — в ответе разметки (context-marker): artifacts.analytics_documentation.requirements[].text по source_requirement_id; в source_requirements фрагмента копируй их без изменений"})


def _write_view(project: Path, root: Path, target: Path, data: bytes, write: bool) -> None:
    """The view is written by the code, again when its bytes differ (like the map of existing tests): a file an agent changed is
    rewritten, never trusted."""
    if not write:
        return
    from tools.confined_output import atomic_write_confined_bytes_at_root

    try:
        current = target.read_bytes()
    except OSError:
        current = None
    if current != data:
        atomic_write_confined_bytes_at_root(project, root, target, data)


LIST_HEAD = 5  # names of the files printed in the summary; the whole list is in the file


def compact_inputs(run_root: Path, stage: str, found: Mapping[str, Any]) -> dict[str, Any]:
    """PT2: what the commands of the agent print for the files of a step.  A batch of a generator reads a hundred files, so the
    printed answer carries the summary — how many, how many bytes, by what basis, the first few — and the path of the file that
    holds the whole list (``<run>/tmp/step-inputs-<stage>.json``, written here; nothing reads it back as a fact: it is for the agent).

    The answer keeps ``bindings`` and ``unresolved`` (short) and, for the automation stages, whatever else ``found`` carries."""
    from tools.confined_output import atomic_write_confined_bytes_at_root, create_confined_directory_exclusive

    root = Path(run_root).resolve()
    project = root.parent.parent
    files = list(found["files"])
    tmp = root / "tmp"
    if not tmp.is_dir():  # a run made before the scan made it
        create_confined_directory_exclusive(project, root, tmp)
    target = tmp / ("step-inputs-" + re.sub(r"[^A-Za-z0-9._-]", "-", stage) + ".json")
    atomic_write_confined_bytes_at_root(project, root, target, json.dumps({key: found[key] for key in ("stage", "attempt_id", "request_digest", "files", "bindings", "unresolved")},
                                                                         ensure_ascii=False, sort_keys=True).encode("utf-8"))
    by_basis: dict[str, int] = {}
    for row in files:
        by_basis[str(row.get("basis") or "файл")] = by_basis.get(str(row.get("basis") or "файл"), 0) + 1
    summary = {"count": len(files), "bytes": sum({row["path"]: int(row["bytes"]) for row in files}.values()), "by_basis": by_basis,
               "head": [row["path"] for row in files[:LIST_HEAD]], "list_path": str(target)}
    return {**{key: value for key, value in found.items() if key != "files"}, "files_summary": summary}


def step_inputs(run_root: Path, stage: str) -> dict[str, Any]:
    """``tools.api step-inputs``: the files of the step as :func:`request_files` finds them, printed as :func:`compact_inputs` — one function."""
    return compact_inputs(run_root, stage, request_files(run_root, stage))


def input_size(found: Mapping[str, Any]) -> int:
    """The bytes a step reads behind :func:`stage_inputs`: each file once (as ``measure`` counts them) and the bindings."""
    return sum({row["path"]: int(row["bytes"]) for row in found["files"]}.values()) + sum(int(row["bytes"]) for row in found["bindings"])


def autotest_view(root: Path, document_digest: str) -> Path:
    """FaT-AV: where ``python -m tools.review_view --effective`` writes the view of the effective document."""
    return root / "review-views" / (str(document_digest).removeprefix("sha256:") + ".autotest.md")


def request_files(run_root: Path, stage: str) -> dict[str, Any]:
    """FaT-Q2 (2.2): ``step-inputs`` — :func:`stage_inputs` of the published model request of ``stage``, plus for the
    automation stages the Java level of the module's build and whether it has junit-jupiter-params."""
    from tools.pilot_state import derive_state

    root = Path(run_root).resolve()
    project = root.parent.parent
    result = stage_inputs(root, stage)
    if stage.startswith(("tc-to-autotest:", "autotest-reviewer:")):
        # FaT-JL: the Java level of the module's build, so that the generator and its reviewer keep to its API
        from tools.discover_project import java_api_level, junit_params_available

        attempt = next(item for item in derive_state(root)["attempts"] if item["attempt_id"] == result["attempt_id"])
        module = str(attempt.get("module") or ".")
        module_root = project if module == "." else project.joinpath(*module.split("/"))
        level = java_api_level(module_root)
        if level is not None:
            result["java_level"] = {**level, "rule": f"только API Java {level['level']}: новее отклонит runner-compat (RUNNER_API_LEVEL)"}
        if (module_root / "pom.xml").is_file() or (module_root / "build.gradle").is_file() or (module_root / "build.gradle.kts").is_file():
            # FaT-PT: a param_group becomes one @ParameterizedTest only where the tests have junit-jupiter-params
            params = junit_params_available(module_root)
            result["junit_params"] = {"available": params, "rule": (
                "группа param_group — один @ParameterizedTest(name = \"[{index}] {0}\"), строки литералами по display_order"
                if params else "junit-jupiter-params нет в зависимостях тестов: по методу @Test на каждый кейс группы, зависимость не добавлять")}
    return result


def measure(project: Path, files: Iterable[Path], role: str | None = None, *, tokens_per_byte: float = TOKENS_PER_BYTE,
            budget: int | None = None, extra_bytes: int = 0) -> dict[str, Any]:
    """``extra_bytes``: what a step reads beyond the listed files — bindings inside a carrier (FaT-AV), the map of existing
    project tests before its file is written (FaT-EC)."""
    paths = [Path(path) for path in files]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"files are missing: {missing[:5]}")
    input_bytes = sum(path.stat().st_size for path in paths) + extra_bytes
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
        budget = args.budget_bytes
        if budget is None and args.run:  # V9a: the budget of the .skillsrc the run froze
            from tools.pilot_state import derive_state

            run_root = project / ".pilot-runs" / args.run
            state = derive_state(run_root)
            if state["attempts"]:
                budget = step_budget(project, run_limits(project, run_root, state, str(state["attempts"][-1]["attempt_id"]))[0])
        report = measure(project, list(dict.fromkeys(files)), role, tokens_per_byte=args.tokens_per_byte, budget=budget,
                         extra_bytes=sum(row["bytes"] for row in requested["bindings"]) if requested else 0)
        if requested is not None:
            report.update(files_summary=compact_inputs(project / ".pilot-runs" / args.run, args.stage, requested)["files_summary"],
                          input_bindings=requested["bindings"], unresolved=requested["unresolved"], size_complete=not requested["unresolved"],
                          input_warning=INPUT_UNRESOLVED if requested["unresolved"] else None)
            report.update({key: requested[key] for key in ("java_level", "junit_params") if key in requested})
        report["recorded"] = False
        if args.run:
            stage = args.stage and (f"{args.stage}:batch-{args.batch:03d}" if args.batch is not None else args.stage)
            report["recorded"] = record(project, args.run, args.step or stage or role or "step", report)
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps(cli_error(error, fix="--project и --run — проект и id прогона (их создаёт scan); --stage — стадия прогона из списка в сообщении; "
                                   "журнал прогона: `python -m tools.run_pipeline status --project <проект> --run <run_id>`"), ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
