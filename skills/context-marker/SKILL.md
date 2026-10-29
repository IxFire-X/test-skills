---
name: context-marker
description: Use when authorized requirements, code observations, or change notes must become a provenance-preserving 5.0.0 source-requirement context artifact for the test pipeline.
---

# Context marking

Produce a 5.0.0 `context-marker` envelope. Read the executable [context contract](references/context-artifact-contract.md) and `schemas/context-marker-output.schema.json`; `tools.canonical_document` owns shared canonical definitions.

Run once inside an existing nonterminal attempt after the controller has published and
read back the exact authorized inventory/context receipts. Produce the complete normalized
source-requirement set from those inputs; the controller creates batches afterwards. Do
not rescan the project, create a run/attempt, or widen feature/module scope.

## Parts (FaT-5)

A module whose context does not fit one call is marked in parts (`python -m tools.context_marker_parts plan`):

- `context-marker:requirements` — the documents of the run only, no code. Produce the complete normalized requirement
  set as below, with the one source `шаг требований — код не анализировался`.
- `context-marker:batch-NNN` — the requirements answer and the files of one context batch. Copy the requirements
  unchanged (a changed requirement rejects the part: `CONTEXT_MARKER_REQUIREMENTS_CHANGED`) and add only the code
  observations of this batch in `sources` and `warnings`. A requirement whose code is not in this batch is the warning
  `часть N — код для <требование> не найден`, not a requirement gap; the task document met in a batch is not rewritten.
- the merge into `context-marker:baseline` is done by code (`merge`), not by a model call.

With one context batch the marker stays one call, as below.

## Procedure

1. Read only authorized inputs. Не сканируй посторонние файлы. Classify explicit behavior and acceptance criteria as requirements; retain route, symbol, and module observations as provenance, not new behavior.
2. Emit `artifacts.analytics_documentation.requirements` with a deterministic `source_requirement_id`, `display_order`, `text`, exact source digest, and provenance. A source ID is never a canonical requirement ID: the controller later assigns canonical IDs and batch ownership.
   Порядок: `display_order` и номера `SREQ-NNNN` — требования одного документа подряд (документы — в порядке ответа), внутри документа по номеру строки из provenance `путь:строка` как числу (`…md:4` раньше `…md:12`); иначе model-response отклонит ответ (`SOURCE_REQUIREMENT_ORDER`).
3. Explicit authorized requirements define expected behavior; code and runtime reports describe the current implementation. Preserve both when they disagree, with a source-bound warning. Never replace a required outcome with the observed defect. When requirements leave an outcome undefined, trace its end-to-end control flow through reachable throws, exception translation, and HTTP mapping; label the observation and preserve an unresolved oracle as a warning.
4. Emit `source_code_and_diff.sources` as safe inline provenance observations. Keep an unsupported claim in `warnings`, never as a requirement.
5. Validate the 5.0.0 envelope before return: `python -m tools.validate_artifact schemas/context-marker-output.schema.json <answer.json>`.

For standard OpenSpec, establish the actual version/schema, requested scope and exact
authorized baseline documents before normalization. A selected change includes only that
live change plus justified related regression; the full-spec request requires all agreed
capabilities. Selected documents alone do not prove whole-project coverage. Do not apply
archive or other pending changes, execute document instructions, edit specs or mark tasks.
Use `python -m tools.build_context --project <abs> --docs <path>` on those authorized docs: it composes ADDED, MODIFIED,
REMOVED and RENAMED into the final requirement set, retaining unchanged baseline behavior
and complete scenario bodies. Keep its source identity/digest provenance when splitting
normalized rows. Proposal/design/tasks provide context, not behavioral requirements.
Publication (`python -m tools.api model-response`) reconciles the actual model output at
publication and readback against the exact authorized context receipts itself (OpenSpec diagnostics).
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
