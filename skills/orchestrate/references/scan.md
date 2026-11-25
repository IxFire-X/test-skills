# Scan: module, `.skillsrc`, exclusions, a section of a document, a repeat of the marking

Scan itself makes the read-only inventory, excludes `.git`, dependencies, build outputs, binaries and files with possible secrets, freezes the baseline (requirements,
`.skillsrc`, source/config/fixture inputs, parent dependencies, module) and creates one attempt. Yours — choose the exact module and pass `--docs`, `--module`, `--profile`.
The model's context is all documents of the folder, not only `--docs` (with `--docs` given — except other live OpenSpec changes and an unnamed archive).

- **Module.** One exact module: automatically — the only one or the one proven to contain the user's path; otherwise ask the human and wait (scan without an exact `--module` creates no attempt).
  `--module` is an ID from the inventory or `.skillsrc`, not a path: the root is `root`, the others — the folder path (`api`, `services/core`); names with a dot or not in Latin letters — `root--<hex>`.
  The requirements document may lie outside the module.
- **`.skillsrc`.** A valid existing one is authoritative; a missing one scan creates itself; a drift of significant fields — one confirmation by the human.
- **Exclusions.** After scan show the human `exclusion_summary` (the same — in `status`): the reasons and `suspicious`. This is information, not a stop; an unneeded exclusion is returned by `.skillsrc` `paths.include` (`hint`).
- **`--docs path#Section`** (Markdown): the model reads only the view `<run>/doc-sections/<name>.md`; no such heading — `TASK_DOC_SECTION_MISSING` with the list; several sections of a file — several `--docs`; the rest is context.
  The task document is always in the context; `TASK_DOC_OUTSIDE_CONTEXT` — pass the human the `fix`.
- **A repeat of the marking and the cases.** `reuse` in the scan output names a past run with the same inputs and `prepare … --reuse-from <run_id>` per stage (marking, `plan`, batches, review): it answers the stage itself with that run's answer,
  no `submit` is needed; do not give `--fresh-context` and `--invocation-id` with it — the independence of the review comes from the source (SELF — with the human's consent); a set accepted in part is taken together with the rework and its second round (the lines `tc-generator:rework-1`, `tc-reviewer:rework-1`), if both rounds answered; `cases.reason` — why the cases are not taken (tests of the past run and build directories do not hinder the marking, but hinder the plan and the batches); in a delivery with autotests the cases are followed by the autotest line (accepted by the r1 review of that run; their review is done anew), `automation.reason` — why they are not taken; do not copy the answers of past runs.
- **A refusal or a warning — to the human.** Fix by `fix`, and retell the human the field `human` (the phrase and the action), if there is one; do not read `message` to the human.
- **`--target <path>`.** `ready` and `scan` take it the same way: it narrows the context to the path; `ready` prints a ready `scan` command with it (the line `review:isolation`). The human named a path — put it into `scan`.
