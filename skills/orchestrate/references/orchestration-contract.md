# Контракт оркестрации Pipeline 6.0

## Источники истины

`contracts/pipeline.json`, Pipeline 6 schemas, `tools.feature_flow`, `tools.pipeline6_tail`, V3 stage schemas и `tools.orchestrate_test_case_revision` — executable truth. Candidate проходит schema/semantic validation и immutable publication до review. Полный valid successor также публикуется до selection. Candidate/successor receipts остаются audit evidence; downstream получает только один effective document и его bare digest.

## Исполнимый порядок

В журнале команд используй абсолютные пути и рабочий каталог. Здесь `<root>` — абсолютный корень skill pack.

1. До `context-marker` выбери exact project root и `<run>`, затем создай/обнови `.skillsrc` и сохрани immutable receipt:

   `python <root>/tools/init_skillsrc.py --project <project> --write --output <project>/docs/to_do/<run>/00-project-bootstrap/attempt-01/skillsrc-init.json`

   `python <root>/tools/validate_artifact.py <root>/schemas/skillsrc-init-output.schema.json <project>/docs/to_do/<run>/00-project-bootstrap/attempt-01/skillsrc-init.json`

   При `needs_input` или `conflict` останови pipeline. Покажи только первый unresolved question с options, evidence и impact. Сохрани выбранные option IDs в новой immutable attempt:

   `python <root>/tools/init_skillsrc.py --project <project> --write --answers <project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-answers.json --output <project>/docs/to_do/<run>/00-project-bootstrap/attempt-02/skillsrc-init.json`

   Проверь новую квитанцию той же командой `validate_artifact.py`, заменив `attempt-01` на `attempt-02`. Продолжай только при `created`, `updated` или `unchanged`.

   Затем загрузи `<project>/.skillsrc` и выбери exact module ID. Автоматически выбирай только единственный module. Для exact relative feature path выбирай содержащий его module root; для текста проверяй только `feature_sources` и source paths. При нуле совпадений запроси path/module ID, при нескольких — покажи IDs/evidence и запроси один выбор. Не переходи к `context-marker` без exact module selection.

2. Until `READY_FOR_PIPELINE_TAIL`, repeat only `advance_feature_flow`: execute the exact returned producer/audit/generator action at its exact record path, record it, and call again. `SEQUENTIAL` is the portable controller. FULL is the initial immutable baseline; CHANGE_SET is selected only by compatible evidence. No user count, mode, scope, shard, or controller tuning exists. Both scope audits and both batch audits are mandatory.

3. Materialize final registries with `materialize_fingerprint_registries(root, contract)`, then repeat `advance_pipeline6_tail(..., fingerprint_registries=registries)` until `COMPLETE` or `BLOCKED`. Tail first runs technical classification and independent review after READY_FOR_PIPELINE_TAIL; it preserves `changed_behavior_context` byte-identically and never changes the candidate or delta already produced by `feature_flow`. It then consumes the FULL candidate or CHANGE_SET delta; the delta zero-op branch selects the bound baseline without candidate review.

4. The tail keeps `finalize_orchestration` V3 signature/schema unchanged. It uses `build_terminal_run_receipt` as the terminal builder and only then may advance an eligible baseline. Task 9 has no tail CLI; Task 10/11 needs a public invocation.

5. **Publish candidate** before review and **publish a valid full successor** before selection; invoke `orchestrate_revision(candidate, review_artifact, output_dir, csv_profile, ...)` only for a FULL/nonzero-delta candidate. Передай downstream только effective revision и effective digest.

6. Выполни `tc-to-autotest` и `autotest-reviewer`. При reviewer auto-fix регенерируй automation и повтори review; не продолжай с частичной correction.

7. Для generated nonzero-pair branch запусти selected module:

   `python <root>/tools/run_tests.py --project <project> --skillsrc <project>/.skillsrc --module <module-id> --canonical-document <effective-document.json> --automation-artifact <tc-to-autotest-output.json>`

   Язык берётся из module; explicit `--language` обязан совпадать. BLOCKED/manual zero-pair branch пропускает только runner.

8. Для каждой terminal branch построй trace через `tools/build_trace_document.py`, вызови `validate_trace_document(trace, document, automation, run_result=None)` и проверь `tools/trace_check.py <trace-document.json> --require-execution`.

9. Do not hand-build the terminal carrier. The tail uses its existing V3 `finalize_orchestration` call, then `build_terminal_run_receipt` and eligible `advance_baseline`.

Не включай discovery questions, answers и bootstrap receipts во входы evaluator-скиллов: это controller evidence. Передавай selected requirements/source files только как `raw_content` в `context-marker`; business content не входит в `.skillsrc` или bootstrap receipt.

## Route и terminal branches

`feature-flow semantic prefix -> FULL candidate or CHANGE_SET delta -> READY_FOR_PIPELINE_TAIL -> test-classifier -> test-classifier-reviewer -> consume the bound candidate/delta -> nonzero candidate publication/review or zero-op bound selection -> tc-to-autotest -> autotest-reviewer -> optional runner -> trace -> V3 finalization -> terminal builder -> eligible baseline advancement`.

Markdown/CSV никогда не являются downstream input. Runtime identity — exact pair `(file_id, symbol_id)`; несколько pairs для target имеют AND semantics. Trace строится для PASS, manual remainder, MANUAL_ONLY, BLOCKED, FAIL и NOT_RUNNABLE.

Final status ровно один из: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`, `BLOCKED`, `FAIL`, `NOT_RUNNABLE`.

## Граница проекта

Разрешено read-only изучение selected module и создание новых generated-test files только в заранее выбранном isolated workspace. Запрещено менять working source, existing tests, configuration, lock files, dependencies, permissions или application behavior ради прохождения теста. Секреты остаются project-native runtime values; artifacts содержат только opaque handles/safe labels.
