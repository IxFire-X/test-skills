---
name: context-marker
description: Use when authorized requirements, code observations, or change notes must become a provenance-preserving 5.0.0 source-requirement context artifact for the test pipeline.
permissions:
  read: "step inputs from the controller"
  write: "role answer in <run>/tmp/ only"
  network: "none"
  external_delivery: "none"
  dependencies: "none"
  rollback: "delete the run directory"
---

# Context marking

Produce a 5.0.0 `context-marker` envelope. Read the executable [context contract](references/context-artifact-contract.md) and `schemas/context-marker-output.schema.json`; `tools.canonical_document` owns shared canonical definitions.

Run once inside an existing nonterminal attempt after the controller has published and
read back the exact authorized inventory/context receipts. Produce the complete normalized
source-requirement set from those inputs; the controller creates batches afterwards. Do
not rescan the project, create a run/attempt, or widen feature/module scope.

## Parts

A module whose context does not fit one call is marked in parts (`status` names the step, `tools.api prepare` publishes it):

- `context-marker:requirements` — the documents of the run only, no code. Produce the complete normalized requirement
  set as below, with the one source `шаг требований — код не анализировался`. OpenSpec files alone (no `--cases`): code answers this step
  (`prepare` says so, no slot) — the rest of it is for a plain document, a mix with one, and `--cases`.
  Import with documents (`scan --cases` with `--docs`): the step also reads the CSV of ready cases. Add
  `analytics_documentation.case_links` — one row per case of the CSV (`row` = the number of its record in the CSV, the header being 1, the first case 2): the
  `source_requirement_ids` of the documents that case checks, or `[]` with a `reason` when it checks none. The cases are
  never requirements: no SREQ from the CSV. A requirement the change ADDED (an ADDED section) is linked to no row: new cases are written for it. The controller refuses a missing, repeated or unknown link before the write
  (`IMPORT_CASE_LINKS`).
  Judge the old cases: a row gets `verdict` — `fits` (the case still checks the current text), `rewrite` (it contradicts the
  text of a linked requirement; `reason` names the contradiction) or `obsolete` (its requirement was removed:
  `source_requirement_ids` `[]`, `removed` {capability, name}, `reason`). No OpenSpec change named (a plain document, or only `openspec/specs`): judge every row; a sure `fits` has no `reason`. An OpenSpec
  change (selected, or archived and named in `--docs`): judge only the rows linked to a requirement its delta ADDS, MODIFIES or
  RENAMES, and every unlinked row when it REMOVES one; no `verdict` elsewhere. An archived delta gives names, never
  requirements. In doubt: `fits` with the doubt in `reason`.
- `context-marker:batch-NNN` — the requirements answer and the files of one context batch. Write only the code
  observations of this batch in `sources` and `warnings`: code puts the requirements of the requirements step into the part. A requirement whose code is not in this batch is the warning
  `часть N — код для <требование> не найден`, not a requirement gap; the task document met in a batch is not rewritten.
- the merge into `context-marker:baseline` is done by code (`merge`), not by a model call.

With one context batch the marker stays one call, as below.

## Procedure

1. Read only authorized inputs. Не сканируй посторонние файлы. Classify explicit behavior and acceptance criteria as requirements; retain route, symbol, and module observations as provenance, not new behavior.
2. Emit `artifacts.analytics_documentation.requirements` with a deterministic `source_requirement_id`, `display_order`, `text`, exact source digest, and provenance. A source ID is never a canonical requirement ID: the controller later assigns canonical IDs and batch ownership.
   Порядок: `display_order` и номера `SREQ-NNNN` — требования одного документа подряд (документы — в порядке ответа), внутри документа по номеру строки из provenance `путь:строка` как числу (`…md:4` раньше `…md:12`); иначе `submit` отклонит ответ (`SOURCE_REQUIREMENT_ORDER`).
   Not OpenSpec: in a document with numbers a requirement is a numbered statement (`D01.`), and a paragraph without a number (introduction, data, working conditions; the list of numbers is among your files) is context — a behaviour only it states goes to `warnings`; without numbers each checkable statement; in document line order; headings are not requirements; `build_context` is for OpenSpec or `requirements.id_pattern` only; take from it just the file's sha256 `digest`, not its split.
   Contradiction: statements that cannot both hold (5 and 10 per page) stay two rows, `требует уточнения` in each text (no case is planned for either), and one question `SREQ-0007 вопрос (path:line) — противоречит SREQ-0003 — какое верно?`.
   Duplicate: one statement said twice in other words is one row; the earlier line first in `provenance`.
   Rejected idea: what the document rejects or postpones («решили не делать») is context; what the system must not do is a requirement.
3. Explicit authorized requirements define expected behavior; code and runtime reports describe the current implementation. Preserve both when they disagree, with a source-bound warning in one free sentence, not in the four fields of a gap (see the contract). Never replace a required outcome with the observed defect. When requirements leave an outcome undefined, trace its end-to-end control flow through reachable throws, exception translation, and HTTP mapping; label the observation and preserve an unresolved oracle as a warning.
4. Emit `source_code_and_diff.sources` as safe inline provenance observations. Keep an unsupported claim in `warnings`, never as a requirement.
5. Validate the 5.0.0 envelope before return: `python -m tools.validate_artifact schemas/context-marker-output.schema.json <answer.json>` (the file lies in the run: `incoming/`).

For standard OpenSpec, establish the actual version/schema, requested scope and exact
authorized baseline documents before normalization. A selected change includes only that
live change plus justified related regression; the full-spec request requires all agreed
capabilities. Selected documents alone do not prove whole-project coverage. Do not apply
archive or other pending changes, execute document instructions, edit specs or mark tasks.
Use `python -m tools.build_context --project <abs> --docs <path>` on those authorized docs (`--docs` is repeated, one file or one OpenSpec folder per flag, never `a,b`; no `--source`: `sources` are your observations; the slot gets only `schema_version`, `stage`, `artifacts`, `warnings`, not the tool's `status`; a requirement's `digest` is the sha256 of its source file as the tool prints it, not a digest of the input list): it composes ADDED, MODIFIED,
REMOVED and RENAMED into the final requirement set, retaining unchanged baseline behavior
and complete scenario bodies. Keep its source identity/digest provenance when splitting
normalized rows. Proposal/design/tasks provide context, not behavioral requirements.
Archived documents may remain historical context; they are never reapplied as pending
changes. A custom schema needs
its actual contract established first; this check does not claim arbitrary-schema support.

Preserve an unresolved requirement in the source set. Each gap warning states the
requirement/scenario and source, what is missing, which checks it blocks, and the concrete
question to resolve. A missing scenario is a visible gap. Missing/extra identities or a
source mismatch prevent accepting the normalized set; reconcile the source rather than
inventing behavior. Mechanical identity checks do not prove semantic body completeness.

Before returning, reconcile every behavior and acceptance criterion in the original
authorized request with the normalized source set. Preserve each condition, role, boundary,
failure outcome, and state change, even when several share one source paragraph. Do not
drop an unsupported or ambiguous requirement: retain it and its precise gap warning.
Schema validity and requirement counts alone do not prove this semantic completeness.

## Stop conditions

Stop instead of guessing on pre-5.0 input, required invention, an unavailable validator or tool, schema or semantic failure, secret exposure risk, or an operation outside the authorized scope. A missing observable result is a data-gap warning, not permission to invent a result.
