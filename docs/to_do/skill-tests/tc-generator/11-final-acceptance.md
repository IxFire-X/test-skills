# tc-generator final acceptance

Date: 2026-08-10

## Outcome

The portable `tc-generator` delivery is complete and its active campaign ledger is complete.

- Canonical delivery: `skills/tc-generator/SKILL.md` plus `references/case-generation-contract.md`.
- Active successful sequence: 3 RED-v2 diagnostic runs, 5 initial-GREEN-v3 runs, 1 pressure-v4 run, and 5 FINAL-GREEN runs.
- Metadata: `complete`, 14 active runs, 2 preserved historical runs, and 7 immutable invalidated attempts.
- Initial and final GREEN scorecards: `complete`, all five repetitions pass all three rubric items.
- RED scorecard: intentionally `pending`, empty, and non-comparable under the accepted bounded change-and-GREEN amendment. It is diagnostic evidence, not a deployment gate.
- Final controller attestation: `artifacts/controller/finalization-v2-success.json`, `status: completed`, `writes_committed: true`.

The skill demonstrates material impact: without the skill, the bounded diagnostic controls exposed traceability, executable-oracle, and authorization gaps; with the canonical skill, all ten normal GREEN repetitions and the pressure repetition passed the same schema and semantic contracts.

## Scale evidence

The supplemental scale checks are preserved separately from the main campaign.

- `08-scale-acceptance` produced 36 cases from 18 requirements. Its exact-string checker rejected semantically equivalent titles and grounded preconditions, so the attempt remains formally failed but was independently classified `diagnostic-ship`; it was not retried or rewritten.
- `09-blind-scale-acceptance` used a new subscription-invoicing feature. The evaluator prompt did not disclose the required count, distribution, hidden matrix, or final ID. The skill independently produced exactly 36 grounded cases in the required category order and passed schema validation. The hidden checker returned two test-data errors, so the attempt remains formally `semantic-failed`, immutable, and excluded from formal acceptance. Fresh Sol classified both errors as checker false negatives: each affected case used one input-grounded representative value exactly as permitted by the canonical contract. The bounded verdict is therefore `diagnostic-ship`, not a semantic pass.

Together with the formally passing main campaign, this provides diagnostic breadth evidence on a new 36-case feature without training the evaluator on the expected count. It does not promote the blind attempt into the formal acceptance ledger.

## Finalization recovery

The first controller version refused before writes because its byte-only scorecard guard did not accept a semantically equivalent trailing newline. The immutable refusal is recorded in `artifacts/controller/finalization-v1-refusal.json`; the evaluator output, validator result, semantic result, and repetition were not rerun.

The versioned v2 controller was independently reviewed before use. It commits protocol, scorecard, and metadata before writing the success attestation; it handles catchable `BaseException` paths and rolls back partial writes. Its single real invocation completed the existing `04-green-final/rep-05` evidence without changing evaluator output.

Completed-state attestation recovery is truthful but not automatic after an uncatchable termination: such a termination can leave the exclusive lock file behind. `12-stale-lock-recovery-limit.md` supersedes the broader recovery wording in the hash-pinned amendment and defines a verified manual clearance procedure. A representative focused test proves that the stale lock first causes a no-write refusal and that recovery succeeds only after explicit removal of that exact simulated stale lock.

## Verification

- Focused campaign suite: `251 passed`.
- Full repository test suite after the final recovery-contract test: `596 passed, 2 skipped`.
- Ruff: all checks passed for `tools` and `tests`.
- `skill-creator` quick validation: `Skill is valid!`.
- Portable contract check: `status: passed`, no errors.
- Generated contract documentation check: passed with no drift.
- Scenario, RED scorecard, initial-GREEN scorecard, final-GREEN scorecard, and run metadata each validate against `schemas/skill-test-evidence.schema.json` with no errors.
- Final fresh Sol acceptance: `ship`; verification is sufficient.

Every evaluator, schema-validator, semantic-checker, controller, and recorder command is stored as a literal argv array with an absolute cwd in its immutable run protocol or controller record. The commands below are the additional final-checkpoint commands.

## Final-checkpoint command ledger

Unless another cwd is shown, the absolute cwd is:

`D:\AI-Projects\.worktrees\portable-testing-skills\test-orchestration-skills`

1. Full verification:

   ```json
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q"]
   ```

   Exit `0`: `595 passed, 2 skipped in 88.18s`.

2. Git state inspection:

   ```json
   ["git","status","--short"]
   ["git","log","-5","--oneline","--decorate"]
   ["git","diff","--stat"]
   ["git","diff","--name-only"]
   ["git","ls-files","--others","--exclude-standard"]
   ["git","diff","--","docs/to_do/CONTINUATION.md","docs/superpowers/plans/2026-08-06-portable-skills-adapters.md"]
   ```

   The first parallel inspection attempt was not accepted as evidence because Windows refused one process spawn with `CreateProcessAsUserW failed: 5`; all five commands were then rerun sequentially and exited `0`. The plan/continuation diff command exited `0`.

3. Read-only campaign summary:

   ```json
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-c","import json,pathlib; root=pathlib.Path(r'docs/to_do/skill-tests/tc-generator'); m=json.loads((root/'06-run-metadata.json').read_text(encoding='utf-8')); s=json.loads((root/'00-scenario.json').read_text(encoding='utf-8')); cards={p.stem:json.loads(p.read_text(encoding='utf-8')) for p in (root/'05-scorecards').glob('*.json')}; success=json.loads((root/'artifacts/controller/finalization-v2-success.json').read_text(encoding='utf-8')); print(json.dumps({'scenario_status':s.get('status'),'effective_phases':{k:v for k,v in s.items() if k.startswith('effective_')},'metadata_status':m.get('status'),'run_count':len(m.get('runs',[])),'run_keys':[r['phase']+'/'+r['repetition'] for r in m.get('runs',[])],'historical_count':len(m.get('historical_runs',[])),'invalidated_count':len(m.get('invalidated_attempts',[])),'scorecards':{k:{'status':v.get('status'),'repetitions':len(v.get('repetitions',[]))} for k,v in cards.items()},'success_record':success},ensure_ascii=False,indent=2))"]
   ```

   Exit `0`; it reported complete metadata with the expected 14-run order and a completed v2 controller attestation.

4. Scorecard read:

   ```json
   ["Get-Content","-LiteralPath","docs\\to_do\\skill-tests\\tc-generator\\05-scorecards\\green-initial.json","docs\\to_do\\skill-tests\\tc-generator\\05-scorecards\\green-final.json","docs\\to_do\\skill-tests\\tc-generator\\05-scorecards\\red.json","-Raw"]
   ```

   Exit `0`. The path list above represents the three literal paths passed to PowerShell's `-LiteralPath` parameter.

5. A filename-search attempt was refused at process creation and produced no accepted evidence:

   ```json
   ["rg","--files","docs/to_do/skill-tests/tc-generator"]
   ["rg","(ACCEPTANCE|acceptance|manifest|FINAL)"]
   ```

   Result: `CreateProcessAsUserW failed: 5`; no repository state changed.

6. Sol Advisor role preflight before the read-only scope delegation:

   ```json
   ["C:\\Program Files\\Git\\bin\\sh.exe","C:/Users/User/.codex/plugins/cache/sol-advisor/sol-advisor/0.5.0/scripts/install-agents.sh","--check"]
   ```

   Exit `0`: `CHECK PASSED`.

7. Delivery and evidence validators:

   ```json
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","C:\\Users\\User\\.codex\\skills\\.system\\skill-creator\\scripts\\quick_validate.py","skills\\tc-generator"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\contract_check.py","--full"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\render_contract_docs.py","--check"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\validate_artifact.py","schemas\\skill-test-evidence.schema.json","docs\\to_do\\skill-tests\\tc-generator\\00-scenario.json"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\validate_artifact.py","schemas\\skill-test-evidence.schema.json","docs\\to_do\\skill-tests\\tc-generator\\05-scorecards\\red.json"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\validate_artifact.py","schemas\\skill-test-evidence.schema.json","docs\\to_do\\skill-tests\\tc-generator\\05-scorecards\\green-initial.json"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\validate_artifact.py","schemas\\skill-test-evidence.schema.json","docs\\to_do\\skill-tests\\tc-generator\\05-scorecards\\green-final.json"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","tools\\validate_artifact.py","schemas\\skill-test-evidence.schema.json","docs\\to_do\\skill-tests\\tc-generator\\06-run-metadata.json"]
   ```

   All eight commands exited `0`; all five evidence documents returned `{"errors":[],"status":"valid"}`.

8. Exact scope discovery:

   ```json
   ["git","status","--porcelain=v1","--untracked-files=all"]
   ["rg","--files","-g","changed-files-manifest.txt"]
   ["git","rev-parse","--show-toplevel","--show-prefix"]
   ["git","diff","--cached","--name-only"]
   ```

   The porcelain and `rev-parse` commands exited `0`; the repository top-level is `D:\AI-Projects\.worktrees\portable-testing-skills` and the campaign cwd prefix is `test-orchestration-skills/`. The manifest search exited `1` because no prior manifest existed. The cached-name check exited `0` and was empty before staging.

9. Transient-file inspection and cleanup:

   ```json
   ["Get-ChildItem","-LiteralPath","docs\\to_do\\skill-tests\\tc-generator","-Recurse","-Force","|","Where-Object","{ $_.Name -eq '__pycache__' -or $_.Name -like '*.pyc' -or $_.Name -like '*.lock' -or $_.Name -like '*.tmp' }","|","Select-Object","-ExpandProperty","FullName"]
   ["Remove-Item","-LiteralPath","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-generator\\__pycache__","-Recurse","-Force"]
   ["Remove-Item","-LiteralPath","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-generator\\08-scale-acceptance\\__pycache__","-Recurse","-Force"]
   ["Remove-Item","-LiteralPath","D:\\AI-Projects\\.worktrees\\portable-testing-skills\\test-orchestration-skills\\docs\\to_do\\skill-tests\\tc-generator\\09-blind-scale-acceptance\\__pycache__","-Recurse","-Force"]
   ```

   Inspection exited `0`; it found only those three generated cache directories inside campaign scope and no lock/tmp file. Each exact removal exited `0`. No permanent evidence was removed.

10. Manifest creation input and exact staging:

   ```json
   ["git","status","--porcelain=v1","--untracked-files=all"]
   ["git","add","--pathspec-from-file=docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt"]
   ```

   The first `git add` used the campaign cwd and exited `1` before changing the index because Git prefixed each manifest entry with `test-orchestration-skills/` a second time. The same exact manifest was then used from the Git top-level cwd `D:\AI-Projects\.worktrees\portable-testing-skills`:

   ```json
   ["git","add","--pathspec-from-file=test-orchestration-skills/docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt"]
   ```

   Exit `0`. At that checkpoint the manifest contained 215 unique exact file paths, included itself, and excluded `docs/to_do/CONTINUATION.md`. The bounded Sol fix below later extended it to 216 paths.

11. Staged-scope verification, using the Git top-level cwd:

   ```json
   ["git","diff","--cached","--check"]
   ["git","diff","--cached","--shortstat"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-c","import json,pathlib,subprocess; root=pathlib.Path(r'D:\\AI-Projects\\.worktrees\\portable-testing-skills'); manifest=(root/'test-orchestration-skills/docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt').read_text(encoding='utf-8').splitlines(); staged=subprocess.check_output(['git','diff','--cached','--name-only'],cwd=root,text=True,encoding='utf-8').splitlines(); status=subprocess.check_output(['git','status','--porcelain=v1','--untracked-files=all'],cwd=root,text=True,encoding='utf-8').splitlines(); unstaged=[line for line in status if len(line)>=2 and (line[1] != ' ' or line[:2] == '??')]; print(json.dumps({'manifest_count':len(manifest),'manifest_unique_count':len(set(manifest)),'staged_count':len(staged),'missing_from_stage':sorted(set(manifest)-set(staged)),'extra_in_stage':sorted(set(staged)-set(manifest)),'unstaged':unstaged},ensure_ascii=False,indent=2))"]
   ```

   The full cached diff check exited `1` only for trailing blank lines in already-recorded immutable run/evidence files. These bytes were deliberately not normalized after execution. The shortstat exited `0`: 215 files changed. Exact set comparison exited `0`: 215 manifest paths equal 215 staged paths, with no missing/extra entries; the only unstaged path is `docs/to_do/CONTINUATION.md`.

   A second cached diff check listed the 36 mutable source, schema, contract, test, plan, and acceptance paths explicitly and exited `0`. Run outputs, observations, drafts, reports, and repetition records were intentionally excluded from that whitespace-only check because they are immutable after execution. Protocol-linked bytes remain covered by recorded SHA-256; applicable JSON artifacts remain covered by schema/semantic checks and the full test suite.

12. Sol Advisor role preflight before final acceptance:

   ```json
   ["C:\\Program Files\\Git\\bin\\sh.exe","C:/Users/User/.codex/plugins/cache/sol-advisor/sol-advisor/0.5.0/scripts/install-agents.sh","--check"]
   ```

   Exit `0`: `CHECK PASSED`.

13. Fresh Sol fix-first diagnosis and bounded closure:

   ```json
   ["Get-Content","-LiteralPath","docs\\to_do\\skill-tests\\tc-generator\\09-blind-scale-acceptance\\REPORT.md","docs\\to_do\\skill-tests\\tc-generator\\09-blind-scale-acceptance\\SOL-ACCEPTANCE.md","docs\\to_do\\skill-tests\\tc-generator\\09-blind-scale-acceptance\\output\\semantic-result.json","-Raw"]
   ["rg","-n","def _lock|record\\.lock|attestation_recovered|completion_preexisted|hard|uncatch|stale|lock","docs/to_do/skill-tests/tc-generator/finalize_campaign_v2.py","docs/to_do/skill-tests/tc-generator/10-finalization-v2-amendment.md","tests/test_tc_generator_campaign_finalizer_v2.py"]
   ["rg","-n","^def _lock|METADATA_NAME","docs/to_do/skill-tests/tc-generator/record_successful_run.py"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests\\test_tc_generator_campaign_finalizer_v2.py","-q"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","ruff","check","tests\\test_tc_generator_campaign_finalizer_v2.py"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-m","pytest","tests","-q"]
   ```

   Read-only diagnosis confirmed that an uncatchable termination can leave the exclusive lock before completed-state recovery. The finalizer source and hash-pinned amendment were not changed. The bounded addendum and representative stale-lock/manual-clearance test were added instead. Focused result: `9 passed`; Ruff: `All checks passed!`; final full result: `596 passed, 2 skipped in 89.29s`.

14. Bounded-fix staging and re-verification:

   ```json
   ["git","add","--","docs/to_do/skill-tests/tc-generator/11-final-acceptance.md","docs/to_do/skill-tests/tc-generator/12-stale-lock-recovery-limit.md","docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt","tests/test_tc_generator_campaign_finalizer_v2.py"]
   ["git","diff","--cached","--check","--","test-orchestration-skills/docs/to_do/skill-tests/tc-generator/11-final-acceptance.md","test-orchestration-skills/docs/to_do/skill-tests/tc-generator/12-stale-lock-recovery-limit.md","test-orchestration-skills/docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt","test-orchestration-skills/tests/test_tc_generator_campaign_finalizer_v2.py"]
   ["git","diff","--cached","--shortstat"]
   ["D:\\AI-Projects\\.tools\\skill-audit-venv\\Scripts\\python.exe","-c","import json,pathlib,subprocess; root=pathlib.Path(r'D:\\AI-Projects\\.worktrees\\portable-testing-skills'); manifest=(root/'test-orchestration-skills/docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt').read_text(encoding='utf-8').splitlines(); staged=subprocess.check_output(['git','diff','--cached','--name-only'],cwd=root,text=True,encoding='utf-8').splitlines(); status=subprocess.check_output(['git','status','--porcelain=v1','--untracked-files=all'],cwd=root,text=True,encoding='utf-8').splitlines(); unstaged=[line for line in status if len(line)>=2 and (line[1] != ' ' or line[:2] == '??')]; print(json.dumps({'manifest_count':len(manifest),'manifest_unique_count':len(set(manifest)),'staged_count':len(staged),'missing_from_stage':sorted(set(manifest)-set(staged)),'extra_in_stage':sorted(set(staged)-set(manifest)),'unstaged':unstaged},ensure_ascii=False,indent=2))"]
   ["C:\\Program Files\\Git\\bin\\sh.exe","C:/Users/User/.codex/plugins/cache/sol-advisor/sol-advisor/0.5.0/scripts/install-agents.sh","--check"]
   ```

   The exact four-path staging exited `0`. The four-path cached diff check exited `0`; shortstat reports 216 files. Exact set comparison reports 216 unique manifest paths equal 216 staged paths with no missing/extra entries; only `CONTINUATION.md` is unstaged. The mandatory Sol Advisor preflight exited `0` with `CHECK PASSED`.

15. Read-only final-review handoff:

   ```json
   ["Get-Content","-LiteralPath","docs\\to_do\\skill-tests\\tc-generator\\11-final-acceptance.md"]
   ["Get-Content","-LiteralPath","docs\\to_do\\skill-tests\\tc-generator\\record_successful_run.py","docs\\to_do\\skill-tests\\tc-generator\\finalize_campaign_v2.py","tests\\test_tc_generator_campaign_finalizer_v2.py"]
   ["Get-Content","-LiteralPath","docs\\to_do\\skill-tests\\tc-generator\\record_successful_run.py"]
   ["git","add","--","docs/to_do/skill-tests/tc-generator/11-final-acceptance.md"]
   ["git","diff","--cached","--unified=3","--","test-orchestration-skills/docs/to_do/skill-tests/tc-generator/11-final-acceptance.md","test-orchestration-skills/docs/to_do/skill-tests/tc-generator/12-stale-lock-recovery-limit.md","test-orchestration-skills/docs/to_do/skill-tests/tc-generator/changed-files-manifest.txt","test-orchestration-skills/tests/test_tc_generator_campaign_finalizer_v2.py"]
   ```

   The reads and exact staging exited `0`. The staged diff command exited `0`; its display was truncated by the tool, so the four exact staged fix fragments were supplied separately to the read-only reviewer. Final Sol verdict: `ship`. The reviewer confirmed both prior blockers closed, hash-pinned controller evidence preserved, blind-scale wording truthful, and verification sufficient.

## Acceptance decision

`ship`. The canonical portable skill, main campaign, supplemental scale evidence, controller recovery record, bounded stale-lock clarification, verification, and exact staged scope are accepted for the scoped implementation/evidence commit.
