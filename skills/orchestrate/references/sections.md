# A document by sections, in one request

When `ready` printed `ВНИМАНИЕ` with runs or `submit plan` refused with `PLAN_REVIEW_TOO_LARGE`: lead the runs from the line (its `fix`) one by one yourself, do not ask the human about each; do not lead the run where the plan refused any further.

1. Where we are — not from memory: `python -m tools.assemble_sections --project <abs> --doc <document>` reads the journals and names per section `passed`, `no_csv` (with the code) or `not_started`. Ask it before each run and after the context is compacted.
2. A run is full: `scan --docs "path#Section" [--docs …]` → … → `finish` → `report`. The rest of the document is context: requirements only from the named sections, the code refuses a line outside them.
3. The end: `… --doc <document> --write` — one CSV and a summary in `<last run>/assembled/`; `not_started` — a refusal, carry out the run; `no_csv` does not go into the file. Part of the runs — `--run <id>` for each. The line `without_case` («без кейса: п. 4.3.4 (SREQ-…, прогон 3 …)») means: the run `passed`, but this requirement has no accepted case (for example, the review rejected its case); restart that run with the same `--docs` (the new one replaces the old one whole, `title_repeats` 0) or add the case to the CSV by hand; name such requirements to the human.
4. To the human in one summary: per run the review outcome, accepted of the generated, control errors caught (a weak review — plainly), `no_csv` with the code, `title_repeats`, the path to the CSV. You start the next run; the code does not start it.
