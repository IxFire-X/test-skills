# Import of ready cases and export for Zephyr

## Import

`scan --cases <csv>` without `--docs`: the step `context-marker:requirements` is answered by the code — `prepare --stage context-marker:requirements` (`status` names it; there is no slot,
a repeat changes nothing); it publishes the case skeleton itself. The last input of a `tc-generator` batch is the skeleton of its cases only: the code puts it into the batch request.
The human fields of the cases do not change.

`scan --cases <csv> --docs <doc>`: the step `context-marker:requirements` is done by the model — by the documents and the CSV, with the case links `case_links`
(a refusal of the links — `IMPORT_CASE_LINKS`, the only repeat of the stage); parts and `merge` — as usual. The case skeleton is published by `submit --stage plan` as its first action, before the plan is written.
The merge marker takes the requirements linked to a case and adds by code `SREQ-CSV-<row>` for a case outside the documents;
a requirement without a case in the CSV stays a requirement: the generator writes new cases for it next to the imported ones (Key empty; the batch skeleton names them in `write`), the report and `questions-for-analyst.md` name it.

A `case_links` link carries a `verdict`: `fits` / `rewrite` / `obsolete` (the refusal `IMPORT_CASE_LINKS` named the rows to judge; `rewrite` — the generator rewrites the text, Key and priority stay; `obsolete` — the code puts the label `устарел` into `labels`).
The human names an archived change in `--docs` by the folder `openspec/changes/archive/<name>` or by the files of all its deltas; an incomplete one is a refusal before the model.

## Export for Zephyr

If `.skillsrc` sets `projection.zephyr.template`, after the run is closed, once:
`python -m tools.zephyr_template_export --project <abs> --run <id> --cases <bundle .zephyr-scale.csv>` — next to it lies `.zephyr-import.csv` for the import into Zephyr.
Do not import the rejected cases (`*.REJECTED-CASES.*`) into Zephyr. Errors `ZEPHYR_TEMPLATE_*`, `ZEPHYR_EXPORT_*` — show the `hint` to the human as is; do not edit the template.
