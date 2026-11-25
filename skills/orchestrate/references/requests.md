# Human request → mode

- «…только ready…» — only `ready --profile cases-only-v1`, the table to the human; do not call `scan`.
- «тест-кейсы по изменению OpenSpec <имя>» — `cases-only-v1`; «Код: <папка>» — `--target <папка>`.
- «…в архиве: обнови кейсы из <csv>» — `cases-only-v1`, `--cases <csv> --docs openspec/specs --docs openspec/changes/archive/<date>-<name>` (find the folder by the name).
- «по документу <путь>» — `--docs <путь>`; autotests — by the line about the delivery in `SKILL.md`.

`--docs` of a change: `openspec/changes/<name>` and `openspec/specs/<area>` of each of its deltas `specs/<area>` that has a base `spec.md`; no name, or several fit — ask the human.
Run the pipeline yourself, in the main session; a subagent gets only the review in a fresh context.
The final answer: the review verdict, the gaps, the full paths to the HTML, Markdown and JSON of the accepted revision, to the review report and to the run folder; the review rejected the revision or a file is missing — say it plainly; a draft — «черновик».
