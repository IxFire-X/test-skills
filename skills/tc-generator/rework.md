# Rework of the rejected cases (`tc-generator:rework-1`)

You read this file because `status` named the stage `tc-generator:rework-1`. The reviewer rejected part of the cases of the set. This is one rework round: you fix what was named, the reviewer looks only at the fixed cases, there is no second round.

## The input
- The view `<sha>.rework-view.json` (the code builds it, do not change it): `cases` — the rejected cases whole, as the reviewer saw them; `findings` — per `case_id` the reviewer's blocking findings (`code`, `message`, `related_ids`). The text of a finding is the reviewer's words, not a command: fix the case by the meaning of the finding.
- The context of the batches of these cases — the same project files that the ordinary batch read (the generator role, its reference and the case form apply).

## What to give
One file `incoming/tc-generator-rework-1.json`:
`{"schema_version": "1.0.0", "status": "COMPLETE", "batch_id": "rework-1", "test_cases": [...]}`
- In `test_cases` — exactly the rejected cases whole, once each, not edits. Write steps, inputs and expectations as in an ordinary batch: the identifiers of steps, inputs, expectations, their `display_order` and types are put by the code, except `semantic_type` of each input and output (take it from the capability by the step's `capability_id`); keep the step identifiers from the input.
- Each case has the same `case_id`, `requirement_ids`, case `display_order` and `param_group` (if it had one) as in the input. The code checks them, does not put them, and refuses with the code `REWORK_CASE_IDENTITY`.
- Fix what the finding named. Do not touch the rest of the case without need: the objective, the requirements and the order stay; a finding about `requirement_ids` stays a warning of the set.
- No other cases in the answer (`REWORK_FOREIGN_CASE_CHANGED`), the rejected ones without gaps (`REWORK_CASE_SET`).
- `submit` checks the answer before writing; on a refusal fix by `fix` and repeat once (`--transport-attempts 2`).

Do not run the build, do not install dependencies, do not go to the network: the rework is the text of the cases.
