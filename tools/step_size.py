"""The size of one model step's input before the call, against the step budget.

``python -m tools.step_size --project <abs> --files <path>… [--role <role>]`` sums the files a step reads;
``--run <id> --stage context-marker [--batch N]`` takes them from the journal: the selected context files of the
batch (all batches without ``--batch``) and the requirement documents of the run.  ``--role`` (by default the role of
``--stage``) adds ``skills/<role>/SKILL.md`` and its references.  The budget is ``.skillsrc`` ``limits.step_input_bytes``,
else 500 000 bytes: the window is shared with the session history.  Prints
``{bytes, tokens_est, budget_bytes, fits, warning, hint, recorded, …}``. A step over the budget is the warning
``STEP_OVER_BUDGET`` with the hint, not a stop (exit 0).  With ``--run`` the measurement — the step (``--step``, else
the stage or the role), sizes and the code, no file paths — is appended to ``<run>/step-sizes.jsonl`` for the report.
``output_warning`` — an answer larger than ``.skillsrc`` ``limits.step_output_warn_bytes`` (default
``DEFAULT_OUTPUT_WARN``) is the warning ``STEP_OUTPUT_LARGE``; only a measurement, nothing is split or rewritten.
``--stage context-marker:requirements`` is the requirements step of the marker in parts (the documents only);
``--stage tc-generator:<batch>|tc-reviewer:canonical|tc-to-autotest:rN|autotest-reviewer:rN`` takes the files behind the
``input_digests`` of the published model request of that stage (``stage_inputs``, by the journal of the attempt, the
same answer as the list of reading of ``prepare`` and the run report): an input the journal does not bind is ``unresolved``
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
from tools.output_guard import OutputRefused, input_file, project_path, run_name
from tools.revision_selection import REWORK_REVIEW_STAGE, REWORK_STAGE, REWORK_VIEW

PACK = Path(__file__).resolve().parents[1]
DEFAULT_BUDGET = 500_000
DEFAULT_WINDOW = 1_000_000  # the working model: 239k tokens / 0.239 tok/B
OVER_BUDGET = "STEP_OVER_BUDGET"
RECORD = "step-sizes.jsonl"
OUTPUT_LARGE = "STEP_OUTPUT_LARGE"
OUTPUT_TRUNCATED = "MODEL_OUTPUT_TRUNCATED"
OUTPUT_SIZES = "output-sizes.jsonl"
TRUNCATED_HINT = ("похоже, ответ упёрся в лимит выхода модели; повтори стадию один раз (как при ошибке схемы) и попроси "
                  "ответить тем же JSON целиком, компактно, без отступов и переносов — так ответ почти вдвое меньше")
JSON_INVALID_FIX = ('ответ целый, но не разбирается как JSON (место ошибки — в сообщении): запиши его заново целиком одним JSON-объектом в UTF-8 — без текста до и после, без комментариев и дублей ключей, кавычки внутри строк экранированы; подай новый файл единственным повтором стадии (--transport-attempts 2)')  # a whole answer that does not read as JSON: not cut, so the repeat asks for it written again, not shortened
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
    """The ``limits`` of the ``.skillsrc`` the run froze — ``(limits, note)``.  The baseline keeps the digest of the file's bytes, not its
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
    """The model window in bytes (``.skillsrc`` ``limits.step_window_bytes``); 0 turns the tc-generator split off."""
    return _limit(project, "step_window_bytes", DEFAULT_WINDOW, zero=True)


class GeneratorContextNotImplemented(ValueError):
    code = "GENERATOR_CONTEXT_NOT_IMPLEMENTED"


def generator_context(project: Path) -> str:
    """``.skillsrc`` ``limits.generator_context`` — ``all`` (default: every generator batch reads the context receipts
    of its plan) or ``own``.  ``own`` (the receipts of the batch's own group plus an index of the others) waits for a
    measurement on a project with known defects and a definition of «own group»; the plan refuses it with a code."""
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
    """The JSON ends inside a string or with an open bracket and reads as a valid beginning to its end — cut in the middle, not merely malformed."""
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
    if not (text.strip() and (in_string or depth > 0)):
        return False
    try:  # a cut text is a valid beginning: the parser reaches its end; a mistake in the middle (an unescaped quote, a comma) stops it there, and the answer is whole
        json.loads(text)
    except json.JSONDecodeError as error:
        return error.pos >= len(text.rstrip()) - 6 or error.msg.startswith("Unterminated string")  # 6: a cut inside true, false, null
    except ValueError:  # a nesting past the recursion limit and the like: as before, open at the end is cut
        pass
    return True


def truncated_message(byte_count: int) -> str:
    return f"{OUTPUT_TRUNCATED}: ответ модели оборван на {byte_count} Б (JSON не закрыт) — {TRUNCATED_HINT}"


def record_output(project: Path, root: Path, step: str, response_digest: str, raw_bytes: int) -> None:
    """The raw answer size by its journal digest, for the report (only sizes; never fails the step)."""
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
    """Raw answer bytes by response digest (the last record of a digest wins)."""
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
    """``STEP_OUTPUT_LARGE`` when one model answer is over the output warning threshold, else ``None``. A run passes its frozen ``limits``
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
    # tc-to-autotest also reads assets/ (the Java/Python conventions)
    return [base / "SKILL.md", *sorted(path for folder in ("references", "assets") for path in (base / folder).rglob("*") if path.is_file())]


def _requirement_files(project: Path, root: Path) -> list[Path]:
    """The documents a requirements digest stands for: ``--docs`` of the run's scan request (the baseline keeps only the
    digest of their projection, ``run_pipeline`` requirement) and, in an import with documents, the CSV whose cases the
    requirements step links (without documents that step is answered by code from the CSV)."""
    from tools.cases_import import cases_path, docs_run
    from tools.pilot_state import read_run

    request = read_run(root)["manifest"].get("scan_request") or {}
    from tools.doc_sections import resolve_spec, view_name

    files: list[Path | str] = []
    headings: dict[str, list[str]] = {}
    for raw in request.get("docs") or []:
        path, heading = resolve_spec(project, raw)  # the model reads the view of path#Section (all the sections of the file), not the file
        if heading is None:
            files.append(Path(path) if Path(path).is_absolute() else project / path)
        else:
            candidate = Path(path)
            rel = (candidate if candidate.is_absolute() else project / candidate).resolve().relative_to(project.resolve()).as_posix()
            if rel not in headings:
                files.append(rel)  # the place of the view, named below once every heading of the file is known
            if heading not in headings.setdefault(rel, []):
                headings[rel].append(heading)
    files = [root / "doc-sections" / view_name(item, headings[item]) if isinstance(item, str) else item for item in files]
    if docs_run(root):
        csv = Path(str(cases_path(root)))
        files.append(csv if csv.is_absolute() else project / csv)
    return list(dict.fromkeys(files))


def journal_files(project: Path, run_id: str, stage: str, batch: int | None) -> list[Path]:
    """The input files of ``stage`` as the journal knows them before the call (context-marker only); the view of a part not asked yet
    has no file — its bytes are the second value of ``journal_inputs``."""
    return journal_inputs(project, run_id, stage, batch)[0]


def journal_inputs(project: Path, run_id: str, stage: str, batch: int | None) -> tuple[list[Path], int]:
    """The input files of ``stage`` as the journal knows them before the call (context-marker only), and the bytes it reads beyond them:
    the view of the requirements answer of a part not asked yet, measured in memory; a part asked with no view on the disk read the
    answer whole.  A measurement writes no view: on the disk a view is the sign that a part was asked with it
    (``context_marker_parts.plan``, status and report read the run so)."""
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
    for digest in sorted(dict.fromkeys(selected)):  # the receipts the journal selected, read the strict way — never a glob of the folder
        receipt = read_context_selection(root, str(attempt["attempt_id"]), digest)
        if batch is None or receipt.get("batch_index") == batch:
            files.extend(project_path(project, item["project_path"]) for item in receipt["files"])
    answer = next((str(event["artifact_digest"]) for event in derive_state(root)["events"]
                   if event.get("attempt_id") == attempt["attempt_id"] and event["event_type"] == "MODEL_RESPONSE_RECEIVED"
                   and event.get("stage_instance_id") == "context-marker:requirements"), None)
    if stage == "context-marker:requirements":
        # the requirements step of the marker in parts reads the requirement documents only
        return _requirement_files(project, root), 0
    held = 0
    if batch is not None and answer is not None:
        # a part of the marker reads the view of the requirements answer, not the documents again.
        source = root / "model-stage-artifacts" / str(attempt["attempt_id"]) / (answer.removeprefix("sha256:") + ".json")
        target = requirements_view_path(root, answer)
        data = _requirements_view_data(project, root, answer, source)
        asked = {str(event.get("stage_instance_id")) for event in derive_state(root)["events"]
                 if event.get("attempt_id") == attempt["attempt_id"] and event["event_type"] == "MODEL_REQUESTED"}
        if data is None:
            files.append(source)  # not the answer's bytes: the answer itself stands
        elif target.is_file() and target.read_bytes() == data:
            files.append(target)  # the view the request of the part wrote
        elif not target.is_file() and f"context-marker:batch-{batch:03d}" in asked:
            files.append(source)  # asked before the view (no view on the disk, as context_marker_parts.plan reads it): the answer whole
        else:
            held = len(data)  # not asked yet (or a view an agent changed, which the request rewrites): its bytes, no file
    else:
        files.extend(_requirement_files(project, root))
    return list(dict.fromkeys(files)), held


def _compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


_RECEIPT_SLOTS = ("batch-plans/{a}.json", "generator-headers/{a}.json", "prior-capabilities/{a}.*.json", "imported-cases/{a}.*.json",
                  "imported-cases/{a}.json", "reviewer-session-boundaries/{a}.json", "automation-review-boundaries/{a}.*.json",
                  "artifacts/{a}/*")  # run artifacts of the attempt (publish_run_artifact_bytes), also without a suffix
_BUNDLES = ("cases", "candidate-bundle")  # where publish_test_case_bundle writes the document of the run


def _sealed(value: Mapping[str, Any]) -> bool:
    """Whether ``value['digest']`` is the digest of the rest of ``value``: the seal of the journal on a receipt, or the self-digest the plan and the header carry
    (``batch_assembly.with_digest``)."""
    from tools.batch_assembly import with_digest
    from tools.pilot_state import check_sealed_value

    try:
        check_sealed_value(value, "receipt")
        return True
    except ValueError:
        return isinstance(value.get("digest"), str) and with_digest({key: item for key, item in value.items() if key != "digest"})["digest"] == value["digest"]


class _Journal:
    """What the journal of one attempt binds, by digest — files that are an input as a whole (more than one is
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
        self.sha256: dict[Path, str] = {}  # the sha256 of the bytes of each file of the context receipts (their content_digest)
        self.documents: dict[str, Path | None] = {}
        self.bundled: dict[str, Path] = {}
        self.requirements_answer: str | None = None  # the answer of context-marker:requirements: a part reads its view
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

                receipt = read_context_selection(root, attempt_id, digest)  # the strict reader, not the bytes of a file at that name
                self.expands[digest] = ([project_path(project, item["project_path"]) for item in receipt["files"]], True)
                self.sha256.update({project_path(project, item["project_path"]): item["content_digest"] for item in receipt["files"]})
            elif event["event_type"] in {"MODEL_RESPONSE_RECEIVED", "CANDIDATE_PUBLISHED"} and event.get("stage_instance_id") != "assembly":
                self._file(digest, root / "model-stage-artifacts" / attempt_id / (digest.removeprefix("sha256:") + ".json"))
                if event["event_type"] == "MODEL_RESPONSE_RECEIVED" and event.get("stage_instance_id") == "context-marker:requirements":
                    self.requirements_answer = digest
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
                if isinstance(value, dict) and _sealed(value):  # the ``digest`` field of a file nobody sealed is a claim, not a receipt
                    for nested in (value, *(item for item in value.values() if isinstance(item, dict) and _sealed(item))):
                        self._file(nested["digest"], path)
        effective = root / "effective-canonicals" / f"{attempt_id}.json"
        if effective.is_file():
            value = json.loads(effective.read_text(encoding="utf-8"))
            self.documents[str(value["document_digest"])] = effective  # the effective canonical gives the document with its carrier
            receipt = value["effective_bundle_receipt"]
            if isinstance(receipt.get("json_path"), str) and receipt.get("document_sha256") == value["document_digest"]:
                self.bundled[str(value["document_digest"])] = Path(receipt["json_path"])  # the bundle file the receipt names
            self.carried[str(value["effective_bundle_receipt_digest"])] = (effective, "effective_bundle_receipt", len(_compact(value["effective_bundle_receipt"])))
        ledgers = [event for event in events if event.get("batch_id") == "reviewer-ledger-v1" and event["event_type"] == "ARTIFACT_READ_BACK"]
        if ledgers:  # the last revision of the ledger, by the strict reader of the journal: an older revision is a file nobody reads again
            from tools.pilot_state import read_reviewer_session_ledger

            ledger = root / "reviewer-session-ledgers" / attempt_id / (str(ledgers[-1]["artifact_digest"]).removeprefix("sha256:") + ".json")
            try:
                package = read_reviewer_session_ledger(root, attempt_id)["package_binding"]
                self.carried[str(package["package_digest"])] = (ledger, "package_binding", len(_compact(package)))
            except (KeyError, OSError, TypeError, ValueError):  # a ledger the journal does not stand behind binds no input
                pass

    def plan(self, state: Any) -> Mapping[str, Any]:
        """The batch plan of the attempt as the journal binds it; ``{}`` before it is published."""
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
    """The digest and the input digests of the last published request of ``stage``, from its journal slot. The bytes are the ones the journal
    sealed (the body hashes to the digest field and to the digest of the event); the predecessor check of ``read_model_request`` is not repeated
    — the request was checked when it was published."""
    from tools.pilot_state import _model_request_target, check_sealed_value

    requested = [event for event in state["events"] if event.get("attempt_id") == attempt_id
                 and event["event_type"] == "MODEL_REQUESTED" and event.get("stage_instance_id") == stage]
    if not requested:
        raise ValueError(f"no model request of {stage} yet: publish it first with prepare --stage {stage}")
    request_digest = str(requested[-1]["artifact_digest"])
    request = json.loads(_model_request_target(root, attempt_id, request_digest).read_text(encoding="utf-8"))
    check_sealed_value(request, "model request")
    if request.get("digest") != request_digest or request.get("stage_instance_id") != stage:
        raise ValueError(f"the model request of {stage} does not match its journal event")
    return request_digest, [str(digest) for digest in request.get("input_digests") or []]


def stage_inputs(run_root: Path, stage: str, *, attempt_id: str | None = None, write_views: bool = True,
                 state: Any = None, journals: dict[str, Any] | None = None) -> dict[str, Any]:
    """The files and bytes behind the ``input_digests`` of the published model request of ``stage``,
    from the journal — the one answer of the list of reading of ``prepare``, ``tools.step_size --run --stage`` and the run report.

    A context receipt or a requirements digest opens into the project files it stands for; an object kept inside a carrier
    is a ``binding`` with its own bytes, or 0 when the step reads the carrier anyway.  The view of the document replaces it
    (``basis: вид``: the reviewer's candidate, the autotest view of ``tc-to-autotest``); a generator batch also reads the map
    of existing project tests (``basis: карта``).  ``tc-to-autotest:r2`` also reads the answer of r1 as the reviewer of r1 did
    (``код``, ``таблицы``, ``вид кейсов``; the whole view and the JSON — ``вид``, ``r1`` — only when those files cannot be built)
    and its review (``ревью r1``) — they bind the correction, not the request.  An input the journal does not bind is
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
        views[0] = root / "review-views" / (digests[0].removeprefix("sha256:") + ".md")  # the reviewer reads the view
    files: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    contexts: list[str] = []
    effective = None
    if stage.startswith("autotest-reviewer:") or stage == "tc-to-autotest:r2":  # the document the answer of the generator is checked against; its view of the cases goes among the files of the answer (r2 reads r1 so)
        from tools.pilot_state import read_effective_canonical_if_present

        effective = read_effective_canonical_if_present(root, attempt_id)
    for position, digest in enumerate(digests):
        if stage in (REWORK_STAGE, REWORK_REVIEW_STAGE) and position < 2:  # two carriers, one view built by the code from the journal: the rejected cases (for the rework),
            if position == 0:  # the reworked ones (for its review)
                from tools.orchestrate_test_case_revision import rework_round_data
                from tools.revision_selection import rework_request, rework_review_request

                basis = rework_round_data(root, attempt_id, need_answer=stage == REWORK_REVIEW_STAGE)
                reviewing = stage == REWORK_REVIEW_STAGE
                data = _compact(rework_review_request(basis["candidate"], basis["outcome"], basis["answer"], basis["r3"]) if reviewing else rework_request(basis["candidate"], basis["outcome"]))
                target = rework_review_view_path(root, digest) if reviewing else rework_view_path(root, digest)
                _write_view(project, root, target, data, write_views)
                files.append({"digest": digest, "path": str(target), "bytes": len(data), "basis": "rework-review-view-v1" if reviewing else REWORK_VIEW})
            continue
        if position in views:
            files.append({"digest": digest, "path": str(views[position]), "bytes": views[position].stat().st_size, "basis": "вид"})
        elif position == 0 and stage.startswith("autotest-reviewer:") and (text := automation_text_rows(project, root, journal.files.get(digest, []), digest, write_views, effective and effective["document"])) is not None:
            files.extend(text)  # the answer of the automation as files of code and tables, not its JSON
        elif digest == journal.inventory_digest:  # the model reads the projection of the inventory, not the receipt
            data = inventory_view_bytes(project, root, digest)
            target = inventory_view_path(root, digest)
            _write_view(project, root, target, data, write_views)
            files.append({"digest": digest, "path": str(target), "bytes": len(data), "basis": INVENTORY_VIEW})
        elif stage.startswith("tc-generator:") and digest == journal.plan_digest(state):  # the plan without the texts
            data = plan_view_bytes(journal.plan(state))
            target = plan_view_path(root, digest)
            _write_view(project, root, target, data, write_views)
            files.append({"digest": digest, "path": str(target), "bytes": len(data), "basis": PLAN_VIEW})
            if (diff := _change_diff(project, root, state, attempt_id, digest, write_views)) is not None:  # a run of the change only with a MODIFIED
                files.append(diff)
        elif digest in journal.expands:
            targets, context = journal.expands[digest]
            if context and stage.startswith("tc-generator:") and (named := journal.plan(state).get("context_files")):  # the generator and its rework read the files the plan names
                keep = {project_path(project, name) for name in named}
                targets = [target for target in targets if target in keep]
            files.extend({"digest": digest, "path": str(target), "bytes": target.stat().st_size, **({"sha256": journal.sha256[target]} if target in journal.sha256 else {})}
                         for target in targets)  # digest: the input of the request the file stands for; sha256: the file itself (the source digest of a requirement is this one)
            if position == 0 and stage in ("context-marker:requirements", "context-marker:baseline"):  # the step that reads the documents also reads the numbers of them, as the code counts them
                from tools.build_context import numbering_view
                from tools.pilot_state import label_texts

                numbers = numbering_view(label_texts(root, attempt_id, state), project)  # the documents as the receipts hold them (by their digests, no link), the numbers as the check of the submit counts them
                if numbers:
                    view = Path(root) / "marker-views" / (digest.removeprefix("sha256:") + ".md")
                    _write_view(project, root, view, numbers.encode("utf-8"), write_views)
                    files.append({"digest": digest, "path": str(view), "bytes": len(numbers.encode("utf-8")), "basis": "номера"})
            if context and stage.startswith("tc-generator:"):
                contexts.append(digest)
        elif (stage.startswith("context-marker:batch-") and digest == journal.requirements_answer and len(paths := journal.files.get(digest, [])) == 1
              and (view := _requirements_view(project, root, digest, paths[0], write_views)) is not None):
            files.append(view)  # a part reads the view of the requirements answer, not the answer (R&D-12, variant A)
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
    if stage in (REWORK_STAGE, REWORK_REVIEW_STAGE):  # the text of the rework: a file of the role that no other round reads
        text = PACK / "skills" / ("tc-generator" if stage == REWORK_STAGE else "tc-reviewer") / "rework.md"
        files.append({"digest": "", "path": str(text), "bytes": text.stat().st_size, "basis": "роль доработки"})
    if stage == "tc-to-autotest:r2":
        document = effective and effective["document"]  # with it r2 reads r1 as the reviewer did (files of code, tables, the view in parts); without it, or when they cannot be built, as before
        for basis, source, event_type in (("r1", "tc-to-autotest:r1", "CANDIDATE_PUBLISHED"), ("ревью r1", "autotest-reviewer:r1", "MODEL_RESPONSE_RECEIVED")):
            answer = next((str(event["artifact_digest"]) for event in state["events"] if event.get("attempt_id") == attempt_id
                           and event.get("stage_instance_id") == source and event["event_type"] == event_type), None)
            paths = [path for path in journal.files.get(answer or "", []) if path.is_file()]
            if basis == "r1" and document and (text := automation_text_rows(project, root, paths, answer or "", write_views, document)) is not None:
                files[:] = [row for row in files if row.get("basis") != "вид"] + text  # the files instead of the JSON of r1 and the whole view
            elif len(paths) == 1:
                files.append({"digest": answer, "path": str(paths[0]), "bytes": paths[0].stat().st_size, "basis": basis})
            else:
                unresolved.append({"digest": answer, "source": source, "reason": "not-in-journal"})  # e.g. an r2 requested before r1
    if stage.startswith("autotest-reviewer:"):  # the document the answer of the generator is checked against: the view of the effective one (the whole file is over the budget on a real project)
        from tools.pilot_state import read_attempt_receipt

        if effective is None:
            unresolved.append({"digest": None, "source": "effective-canonical", "reason": "not-in-journal"})
        elif not any(row.get("basis") == "вид кейсов" for row in files):  # no files of the answer: the generator's view, or the document
            view = autotest_view(root, effective["document_digest"])  # the generator's prepare makes it; without it the whole file is read
            target = view if view.is_file() else root / read_attempt_receipt(root, attempt_id, "effective-canonical", "ARTIFACT_READ_BACK")["path"]
            files.append({"digest": effective["document_digest"], "path": str(target), "bytes": target.stat().st_size, "basis": "вид документа" if view.is_file() else "эффективный документ"})
    if contexts:
        # the map of existing project tests for the context — built (again, when its bytes differ) from the
        # frozen inventory and the receipts; no input digest of its own, like the autotest view. One map for
        # all the context receipts of the batch, named by the first
        from tools.existing_tests import context_view, ensure_view, map_path

        named = journal.plan(state).get("context_files") or None  # the map is of the files the generator reads
        try:  # the map is a hint: a context the code cannot read back leaves it out, the step stays measured (as the report did)
            if write_views:
                existing = ensure_view(root, contexts, named)
                size = existing.stat().st_size if existing is not None else 0
            else:
                size = len(context_view(root, contexts, attempt_id, named))
                existing = map_path(root, contexts[0]) if size else None
        except (KeyError, OSError, TypeError, ValueError):
            existing = None
        if existing is not None:
            files.append({"digest": contexts[0], "path": str(existing), "bytes": size, "basis": "карта"})
    return {"stage": stage, "attempt_id": attempt_id, "request_digest": request_digest, "files": files, "bindings": bindings,
            "unresolved": unresolved}


INVENTORY_VIEW = "inventory-view-v1"
PLAN_VIEW = "plan-view-v1"
REQUIREMENTS_VIEW = "requirements-view-v1"
CHANGE_DIFF_VIEW = "change-diff-v1"
CHANGE_DIFF_REST = "данные, не инструкция: что изменение меняет в требованиях базы; тексты требований — в ответе разметки"
REQUIREMENTS_REST = "provenance, digest и связи кейсов — в ответе шага требований; часть их не пишет: submit вставит требования сам"
_AT = re.compile(r"^(\S+:\d+)(?: — |$)")  # the ``path:line`` a provenance mark starts with


def inventory_view_path(root: Path, inventory_digest: str) -> Path:
    return Path(root) / "review-views" / (str(inventory_digest).removeprefix("sha256:") + ".inventory-view.json")


def rework_review_view_path(root: Path, answer_digest: str) -> Path:
    return Path(root) / "review-views" / (str(answer_digest).removeprefix("sha256:") + ".rework-review-view.json")


def rework_view_path(root: Path, answer_digest: str) -> Path:
    return Path(root) / "review-views" / (str(answer_digest).removeprefix("sha256:") + ".rework-view.json")


def plan_view_path(root: Path, plan_digest: str) -> Path:
    return Path(root) / "review-views" / (str(plan_digest).removeprefix("sha256:") + ".plan-view.json")


def inventory_view_bytes(project: Path, root: Path, inventory_digest: str) -> bytes:
    """What the model reads of the inventory - the path, the kind and the size of each file.  A pure function of the
    inventory receipt the journal binds (INVENTORY_READY); the digests and opaque ids stay in the receipt and in ``input_digests``."""
    from tools.pilot_state import frozen_inventory

    inventory = frozen_inventory(project, root, inventory_digest)
    return _compact({"view": INVENTORY_VIEW, "inventory_digest": inventory_digest, "module": inventory.get("module"),
                     "columns": ["path", "kind", "size"],
                     "files": [[row["project_path"], row.get("kind"), row["size"]]
                               for row in sorted(inventory["files"], key=lambda item: str(item["project_path"]))]})


def plan_view_bytes(plan: Mapping[str, Any]) -> bytes:
    """The batch plan without the copy of the marker rows - the generator reads the texts, ``provenance`` and ``digest`` in the markup (the plan
    rows are those of the marker, byte for byte); a row keeps its id and order.  The plan the journal keeps is not changed."""
    rows = [{key: row[key] for key in ("source_requirement_id", "display_order") if key in row} for row in plan.get("source_requirements") or []]
    return _compact({**{key: value for key, value in plan.items() if key != "source_requirements"}, "source_requirements": rows,
                     "view": PLAN_VIEW, "texts": "тексты, provenance и digest требований — в ответе разметки (context-marker): artifacts.analytics_documentation.requirements[] по source_requirement_id"})


def change_diff_path(root: Path, plan_digest: str) -> Path:
    return Path(root) / "marker-views" / (str(plan_digest).removeprefix("sha256:") + ".change-diff.json")


def _change_diff(project: Path, root: Path, state: Any, attempt_id: str, plan_digest: str, write: bool) -> dict[str, Any] | None:
    """The view «было → стало» of the MODIFIED of a run of the change only, beside the plan view of a generator batch; None for any
    other run.  Built from the OpenSpec sources of the receipts (read as the marker reads them) when the request is published; the
    report (``write=False``) counts it only when it is on the disk — a batch asked before the view read none."""
    target = change_diff_path(root, plan_digest)
    if not write:
        return {"digest": plan_digest, "path": str(target), "bytes": target.stat().st_size, "basis": CHANGE_DIFF_VIEW} if target.is_file() else None
    from tools.build_context import openspec_change_diff
    from tools.model_records import openspec_snapshots
    from tools.model_steps import openspec_marker_by_code

    try:
        if not openspec_marker_by_code(root):  # no change only without OpenSpec alone: the sources are not read
            return None
        attempt = next(row for row in state["attempts"] if row["attempt_id"] == attempt_id)
        rows = openspec_change_diff(openspec_snapshots(project, root, state, attempt))
    except (KeyError, OSError, StopIteration, ValueError):  # the sources do not read: the generator reads the markup as before
        return None
    if not rows:
        return None
    data = _compact({"view": CHANGE_DIFF_VIEW, "plan_digest": plan_digest, "requirements": rows, "rest": CHANGE_DIFF_REST})
    _write_view(project, root, target, data, True)
    return {"digest": plan_digest, "path": str(target), "bytes": len(data), "basis": CHANGE_DIFF_VIEW}


def requirements_view_path(root: Path, answer_digest: str) -> Path:
    return Path(root) / "marker-views" / (str(answer_digest).removeprefix("sha256:") + ".requirements-view.json")


def requirements_view_bytes(answer: Mapping[str, Any], answer_digest: str) -> bytes:
    """What a part of the marker reads of the requirements answer - per requirement its id, order, ``path:line`` and text.  The
    provenance (in OpenSpec the whole block once more), the digests and the case links stay in the answer: a part does not write
    the requirements, ``submit`` puts them in (``context_marker_merge.with_requirements``)."""
    def at(row: Mapping[str, Any]) -> str | None:
        return next((found.group(1) for mark in row.get("provenance") or [] if (found := _AT.match(str(mark)))), None)

    rows = answer["artifacts"]["analytics_documentation"]["requirements"]
    return _compact({"view": REQUIREMENTS_VIEW, "answer_digest": answer_digest, "columns": ["source_requirement_id", "display_order", "at", "text"],
                     "requirements": [[row.get("source_requirement_id"), row.get("display_order"), at(row), row.get("text")] for row in rows],
                     "rest": REQUIREMENTS_REST})


def _requirements_view(project: Path, root: Path, digest: str, source: Path, write: bool) -> dict[str, Any] | None:
    """The view of the requirements answer ``digest`` (its file ``source``) as a row of the files of a part: written with the request
    (``write``), measured as it was written otherwise.  None - the answer itself stands: a part of a run before the view (its report
    stays as it was) or a file that is not the answer's bytes."""
    target = requirements_view_path(root, digest)
    if not write and not target.is_file():
        return None
    data = _requirements_view_data(project, root, digest, source)
    if data is None:
        return None
    _write_view(project, root, target, data, write)
    return {"digest": digest, "path": str(target), "bytes": len(data), "basis": REQUIREMENTS_VIEW}


def _requirements_view_data(project: Path, root: Path, digest: str, source: Path) -> bytes | None:
    """The bytes of the view of the requirements answer ``digest`` from its file ``source``, read through the core like any file of the
    run; None — unreadable, refused by the core, or not the answer's bytes (its sha256)."""
    from tools.confined_output import read_confined_bytes

    try:
        raw = read_confined_bytes(project, root, source)
        if raw is None or "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            return None
        return requirements_view_bytes(json.loads(raw.decode("utf-8")), digest)
    except (OSError, ValueError, KeyError, TypeError):  # ValueError: not JSON, or the core refused the path
        return None


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


def automation_text_rows(project: Path, root: Path, paths: Iterable[Path], digest: str, write: bool, document: Any = None) -> list[dict[str, Any]] | None:
    """The rows of ``tools.automation_text`` for the answer ``digest`` (and the effective *document*, whose view of the cases they hold), written like the other views;
    None: its JSON stays (not one file, bytes not its digest, no render)."""
    paths = list(paths)
    try:
        from tools.execution_gate import from_mode_b
        render, = from_mode_b("automation_text", "render")
        from tools.confined_output import read_confined_bytes

        raw = (read_confined_bytes(project, root, paths[0]) or b"") if len(paths) == 1 else b""  # a file of the run is read through the core
        parts = render(json.loads(raw), digest, document) if "sha256:" + hashlib.sha256(raw).hexdigest() == digest else None
    except (RuntimeError, OSError, ValueError):  # RuntimeError: a delivery without mode B; OSError: unreadable; ValueError: not JSON, or the core refused the path
        return None
    for name, _basis, data in parts or []:
        _write_view(project, root, Path(root) / "review-views" / name, data, write)
    return [{"digest": digest, "path": str(Path(root) / "review-views" / name), "bytes": len(data), "basis": basis} for name, basis, data in parts or []] or None


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


def input_size(found: Mapping[str, Any]) -> int:
    """The bytes a step reads behind :func:`stage_inputs`: each file once (as ``measure`` counts them) and the bindings."""
    return sum({row["path"]: int(row["bytes"]) for row in found["files"]}.values()) + sum(int(row["bytes"]) for row in found["bindings"])


def autotest_view(root: Path, document_digest: str) -> Path:
    """Where ``prepare`` of the generator writes the view of the effective document."""
    return root / "review-views" / (str(document_digest).removeprefix("sha256:") + ".autotest.md")


def request_files(run_root: Path, stage: str) -> dict[str, Any]:
    """:func:`stage_inputs` of the published model request of ``stage``, plus for the
    automation stages the Java level of the module's build and whether it has junit-jupiter-params."""
    from tools.execution_gate import from_mode_b
    from tools.pilot_state import derive_state, frozen_baseline

    root = Path(run_root).resolve()
    project = root.parent.parent
    result = stage_inputs(root, stage)
    if stage.startswith(("tc-to-autotest:", "autotest-reviewer:")):
        # the Java level of the module's build, so that the generator and its reviewer keep to its API
        from tools.discover_project import java_api_level, junit_params_available

        attempt = next(item for item in derive_state(root)["attempts"] if item["attempt_id"] == result["attempt_id"])
        module = str(attempt.get("module") or ".")
        module_root = project if module == "." else project.joinpath(*module.split("/"))
        level = java_api_level(module_root, project)
        if level is not None:
            result["java_level"] = {**level, "rule": f"только API Java {level['level']}: новее `submit` отклонит (RUNNER_API_LEVEL)"}
        if (module_root / "pom.xml").is_file() or (module_root / "build.gradle").is_file() or (module_root / "build.gradle.kts").is_file():
            # a param_group becomes one @ParameterizedTest only where the tests have junit-jupiter-params
            params = junit_params_available(module_root, project)
            result["junit_params"] = {"available": params, "rule": (
                "группа param_group — один @ParameterizedTest(name = \"[{index}] {0}\"), строки литералами по display_order"
                if params else "junit-jupiter-params нет в зависимостях тестов: по методу @Test на каждый кейс группы, зависимость не добавлять")}
        if stage.startswith("tc-to-autotest:") and (held := from_mode_b("generated_delta", "occupied_paths")[0](frozen_baseline(root, attempt))):  # the paths ``submit`` refuses (the same frozen baseline)
            result["occupied_paths"] = occupied_field(held)
    return with_test_env(root, result)


def build_file_rows(project: Path, inventory: Mapping[str, Any], module_root: Path) -> list[dict[str, Any]]:
    """The build files of the module and its reactor parents as step inputs — only those of the frozen *inventory* whose bytes still match it."""
    from tools.discover_project import build_files
    from tools.existing_tests import held_bytes

    rows = {str(row["project_path"]): row for row in inventory["files"]}
    found = [(rows.get(path.relative_to(Path(project).resolve()).as_posix()), path) for path in build_files(module_root, project) if path.is_relative_to(Path(project).resolve())]  # a module root behind a link is no file of the project
    return [{"digest": row["content_digest"], "path": str(path), "bytes": len(raw), "basis": "файл сборки"}
            for row, path in found if row and (raw := held_bytes(project, row)) is not None]


def with_test_env(root: Path, found: dict[str, Any], *, write: bool = True, state: Any = None) -> dict[str, Any]:
    """*found* of a generator batch plus the ``adapter`` the run froze (when it froze one); *found* of an automation stage plus it, the build files of the module and the imports of the project's tests
    (``basis: файл сборки`` / ``окружение тестов``); the frozen baseline and inventory are read strictly — a bad receipt is a refusal."""
    from tools.existing_tests import env_view
    from tools.pilot_state import derive_state, frozen_baseline, frozen_inventory

    batch = found["stage"].startswith("tc-generator:BATCH-")  # the stage that writes ``capability.adapter``
    if not batch and not found["stage"].startswith(("tc-to-autotest:", "autotest-reviewer:")):
        return found
    project = (root := Path(root).resolve()).parent.parent
    attempt = next(item for item in (state or derive_state(root))["attempts"] if item["attempt_id"] == found["attempt_id"])
    if batch and not attempt.get("baseline_digest"):
        return found
    baseline = frozen_baseline(root, attempt)  # strict: a receipt that is not the frozen one is a refusal
    if batch:  # a cases-only baseline freezes no adapter: nothing to tell
        return {**found, "adapter": {"id": baseline["adapter_id"], "rule": "`capability.adapter` — этот id"}} if baseline.get("adapter_id") else found
    inventory = frozen_inventory(project, root, baseline["inventory_digest"])
    module = str(attempt.get("module") or ".")
    files = build_file_rows(project, inventory, project.joinpath(*module.split("/")))
    data = env_view(project, inventory, ["# Окружение тестов проекта", f"- модуль: {module}; test_root: {baseline.get('test_root')}; адаптер: {baseline['adapter_id']}"])
    if data:  # written like the map of existing tests: by the code, again when its bytes differ
        target = root / "review-views" / (hashlib.sha256(data).hexdigest() + ".test-env.md")
        _write_view(project, root, target, data, write)
        files.append({"digest": "sha256:" + hashlib.sha256(data).hexdigest(), "path": str(target), "bytes": len(data), "basis": "окружение тестов"})
    return {**found, "adapter": {"id": baseline["adapter_id"], "rule": "адаптер запуска модуля (справка)"}, "files": [*found["files"], *files]}


def occupied_field(paths: list[str], limit: int = 4096) -> dict[str, Any]:  # printed within ``limit`` bytes: the rest is counted, the guard is the refusal of ``submit``
    size, shown = 360, 0  # the keys, the count and the rule
    while shown < len(paths) and (size := size + len(paths[shown].encode("utf-8")) + 4) <= limit:
        shown += 1
    return {"paths": paths[:shown], **({"more": f"и ещё {len(paths) - shown}"} if shown < len(paths) else {}), "rule": "эти пути заняты файлами проекта: новый файл называй иначе; `submit` откажет на занятый путь, в том числе отличающийся только регистром букв"}


def measure(project: Path, files: Iterable[Path], role: str | None = None, *, tokens_per_byte: float = TOKENS_PER_BYTE,
            budget: int | None = None, extra_bytes: int = 0) -> dict[str, Any]:
    """``extra_bytes``: what a step reads beyond the listed files — bindings inside a carrier, the map of existing
    project tests before its file is written."""
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
                              "paths/include модуля в .skillsrc, файлы, которые модели не нужны, — в .skillsrc paths.exclude (только контекст; опись их держит) "
                              "или меньше файлов шага; бюджет — .skillsrc limits.step_input_bytes")
    return {"bytes": total, "input_bytes": input_bytes, "role_bytes": role_bytes, "role": role, "files": len(paths),
            "tokens_est": round(total * tokens_per_byte), "tokens_per_byte": tokens_per_byte, "budget_bytes": budget_bytes,
            "fits": fits, "warning": None if fits else OVER_BUDGET, "hint": hint}


def record(project: Path, run_id: str, step: str, report: dict[str, Any]) -> str:
    """Append one measurement to ``<run>/step-sizes.jsonl`` (only the step, sizes and the code)."""
    from datetime import datetime, timezone

    from tools.confined_output import atomic_write_confined_bytes_at_root

    project = Path(project).resolve()
    try:
        root = project / ".pilot-runs" / run_name(run_id)
    except OutputRefused as error:
        raise ValueError(f"unknown run: {run_id}") from error
    if not (root / "run-manifest.json").is_file():
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
    parser.add_argument("--stage", help="context-marker (or context-marker:baseline), context-marker:requirements, tc-generator:<batch>, tc-to-autotest:rN, autotest-reviewer:rN")
    parser.add_argument("--batch", type=int)
    parser.add_argument("--role")
    parser.add_argument("--step", help="the step name recorded in the run (default: --stage, else --role)")
    parser.add_argument("--tokens-per-byte", type=float, default=TOKENS_PER_BYTE)
    parser.add_argument("--budget-bytes", type=int)
    args = parser.parse_args(argv)
    if args.stage == "context-marker:baseline":  # the name of the stage of the request; the journal and the report know the step as context-marker
        args.stage = "context-marker"
    project = Path(args.project).resolve()
    try:
        files = [input_file(item, project, base=project) for item in args.files]  # inside the project, checked before it is measured
        requested, held = None, 0
        stage_role = args.stage.split(":", 1)[0] if args.stage else None
        if args.stage and stage_role not in _STAGE_ROLES and stage_role not in _REQUEST_STAGES:
            raise ValueError(f"unknown --stage: {args.stage}")
        if args.run:
            run_name(args.run)  # a run id, not a path: before a path is built from it, to read a journal or to write a measurement
            if not args.stage and not files:
                raise ValueError("--run needs --stage or --files")
            if stage_role in _REQUEST_STAGES:
                requested = request_files(project / ".pilot-runs" / args.run, args.stage)
                files += [Path(row["path"]) for row in requested["files"]]
            elif args.stage:
                found, held = journal_inputs(project, args.run, args.stage, args.batch)
                files += found
        role = args.role or stage_role
        budget = args.budget_bytes
        if budget is None and args.run:  # the budget of the .skillsrc the run froze
            from tools.pilot_state import derive_state

            run_root = project / ".pilot-runs" / args.run
            state = derive_state(run_root)
            if state["attempts"]:
                budget = step_budget(project, run_limits(project, run_root, state, str(state["attempts"][-1]["attempt_id"]))[0])
        report = measure(project, list(dict.fromkeys(files)), role, tokens_per_byte=args.tokens_per_byte, budget=budget,
                         extra_bytes=(sum(row["bytes"] for row in requested["bindings"]) if requested else 0) + held)
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
