# Artifact-First, Fail-Soft Pipeline Design

**Status:** Approved direction; implementation handoff

**Date:** 2026-08-21

**Target:** `test-skills` Pipeline 6 front door and orchestration behavior

## 1. User outcome

The supported first-run workflow is:

```text
add the pipeline folder to a project
run one command
receive pipeline artifacts and generated test cases
inspect any errors in the same run output
```

The user must not create protocol carriers, run calibration campaigns, prepare a Git history, or restart the whole pipeline because one source item or reviewer failed.

The primary product metric is **time to first useful artifact**, not time to a perfectly reviewed terminal receipt.

## 2. Problem

The current flow is effectively all-or-nothing. Internal protocol gates, calibration, review carriers, promotion evidence, baseline authority, and terminal closure can prevent publication of test cases even after useful analysis has already completed.

This reverses the intended priority: protocol correctness exists to make artifacts trustworthy, but currently delays or prevents the artifacts themselves.

## 3. Design decision

Make the public runner **artifact-first** and **fail-soft**.

- Artifact-first: persist useful outputs as soon as their producing stage completes.
- Fail-soft: isolate recoverable failures to the affected module, source, requirement, test case, review, or automation target and continue processing independent work.
- Honest: never convert incomplete work into `COMPLETE`; expose errors and omissions explicitly.
- One-shot: all automatic generation, review, and one bounded correction happen inside one command invocation.

This is a front-door and orchestration correction, not a second pipeline layered over Pipeline 6. Existing canonical validators and artifact builders should remain internal implementation where they add trust.

## 4. Public interface

The public interface should be one deep module:

```python
run_project(project_path, output_path=None, module=None) -> RunSummary
```

The CLI is a thin adapter:

```text
pipeline run <project> [--output <path>] [--module <id>]
```

The caller must not know about behavior-context carriers, audit bindings, promotion records, baseline capabilities, fingerprint registries, or terminal closure.

Defaults:

- Missing `.skillsrc`: discover and create it automatically, then continue.
- One discovered module: select it automatically.
- Multiple discovered modules without `--module`: process all modules in deterministic order rather than stopping for input.
- Non-Git project: run normally from a frozen filesystem snapshot.
- Git project: record Git identity as provenance, but do not require a clean or committed checkout for first artifacts.
- Missing baseline: run `FULL`; a baseline is an optimization for later runs, never a first-run prerequisite.

## 5. Artifact lifecycle

The runner creates the run root before analysis and publishes append-only artifacts in this order:

1. `run-manifest.json`
   - Created immediately.
   - Binds project/module selection, pipeline version, invocation mode, and run identity.
2. `project-inventory.json`
   - Records discovered modules and readable source/test files.
3. `scope.json`
   - Records what will be processed and what was excluded.
4. Requirement/context artifacts
   - Published per completed source batch.
5. Test-case artifacts
   - Published per completed requirement batch; they do not wait for every batch or terminal closure.
6. Review and correction artifacts
   - Bind the already-published draft test cases.
7. Automation artifacts
   - Published where the environment supports them; missing automation support does not erase manual cases.
8. `diagnostics.json`
   - Contains every warning, skipped item, failed review, unavailable adapter, and internal stage error.
9. `run-summary.json`
   - Written at command completion and reports `COMPLETE`, `PARTIAL`, or `FATAL`.

Existing immutable/content-addressed artifacts remain immutable. Stable filenames are projections or indexes over those persisted values; the implementation must not rewrite previously published canonical evidence.

## 6. Completion and exit semantics

| Status | Exit code | Meaning |
|---|---:|---|
| `COMPLETE` | `0` | All selected work produced accepted outputs. |
| `PARTIAL` | `1` | Useful artifacts were produced, but one or more items failed, were skipped, remained unreviewed, or could not be automated. |
| `FATAL` | `2` | The project cannot be read, the run root cannot be created, or the runner cannot publish even its manifest/diagnostic boundary. |

Exit code `1` is intentional: local users keep the artifacts, while CI can still detect incomplete coverage.

The process should attempt to write `diagnostics.json` and `run-summary.json` for every non-storage fatal error.

## 7. Recoverable versus fatal errors

Recoverable errors are attached to an item and do not stop unrelated work:

- unreadable or unsupported individual source file;
- unsupported language/framework;
- missing test runner or automation adapter;
- invalid generated requirement or test case;
- reviewer disagreement or rejected claim;
- omitted behavior in one candidate;
- baseline mismatch or unavailable Git history;
- failure to create a durable successor baseline;
- failure of optional publication/export projections.

Fatal errors are deliberately narrow:

- project root cannot be opened or enumerated at all;
- output root cannot be safely created or written;
- the runner cannot establish a trustworthy run identity;
- internal corruption prevents publication of any honest artifact or diagnostic.

## 8. Review behavior

Review remains part of the same invocation and must not require externally authored JSON.

For each generated item:

1. Publish the draft artifact.
2. Run the configured reviews.
3. Apply at most one automatic correction when findings are mechanically actionable.
4. Review the corrected value once.
5. If findings remain, retain the latest artifact, mark the item `REVIEW_FAILED`, add diagnostics, and continue.

There are no user-visible `generation-2`/`generation-3` carrier workflows. If generation identifiers remain necessary for provenance, they are private implementation details.

## 9. Calibration

The 24-control calibration campaign is a release/CI qualification check for a pipeline build. It is not part of a normal project run.

At runtime the runner may perform a cheap compatibility check of its own version, schemas, and fingerprints. It must not execute the calibration corpus before producing project artifacts.

A pipeline release that has not passed calibration may report that fact in `run-manifest.json`, but a local exploratory run can still produce `PARTIAL` artifacts unless safe execution is impossible.

## 10. Performance requirements

Measured from process start on a normal local project:

- run root and `run-manifest.json`: within 1 second;
- inventory and initial scope: target within 10 seconds;
- first requirement/context artifact: as soon as the first source batch completes;
- first draft test case: as soon as its first requirement batch completes;
- no waiting for full calibration, all-source review, terminal receipt, or baseline advancement before publishing draft cases.

Performance reporting should capture:

- time to manifest;
- time to inventory;
- time to first requirement;
- time to first test case;
- total duration;
- counts of complete, partial, failed, and skipped items.

## 11. Acceptance scenarios

The change is accepted only when these public one-command scenarios pass:

1. **Fresh non-Git project**
   - Missing `.skillsrc` is created automatically.
   - The command produces manifest, inventory, scope, test cases or honest no-fact results, diagnostics, and summary without another command.
2. **Dirty Git checkout**
   - The current filesystem is frozen and processed.
   - Useful artifacts do not require a commit.
3. **Partially unreadable project**
   - Readable sources produce test cases.
   - Unreadable sources appear in diagnostics.
   - Result is `PARTIAL`, exit `1`.
4. **Reviewer rejects one item**
   - Other items complete.
   - The rejected item and finding remain visible.
   - Previously published artifacts remain available.
5. **Automation unavailable**
   - Manual test cases are still published.
   - Automation status is `PARTIAL` with an actionable diagnostic.
6. **Multiple modules**
   - All modules run deterministically by default.
   - `--module` narrows the work when explicitly supplied.
7. **No valid observable behavior**
   - The run publishes a valid no-fact result rather than failing or fabricating a test case.
8. **Unexpected late-stage failure**
   - All earlier artifacts remain intact.
   - Summary/diagnostics identify the failed stage and affected items.

Every scenario must assert time to first artifact. A test that only checks the final summary is insufficient.

## 12. Migration order

Implement in the smallest useful sequence:

1. Add the one-command runner contract, immediate manifest, final summary, diagnostics, and exit codes.
2. Publish requirement and test-case artifacts per batch before review/terminal closure.
3. Convert recoverable global `BLOCKED` outcomes into item-level `PARTIAL` diagnostics.
4. Internalize review carriers and enforce one bounded automatic correction.
5. Remove calibration from the runtime critical path.
6. Make baselines, Git durability, automation, and final publication optional enrichments after useful artifacts exist.

Do not begin by expanding schemas or adding another orchestration layer. First prove that one command on a fresh local project creates useful artifacts even when later stages fail.

## 13. Non-goals

- Hiding errors or returning success for incomplete work.
- Weakening source provenance, canonical validation, secret scanning, or artifact confinement.
- Requiring online services, autonomous self-modification, or remote calibration.
- Guaranteeing automation for unsupported stacks.
- Rewriting all existing Pipeline 6 modules before the new public behavior works.

## 14. Merge gate

Do not call this behavior complete until a fresh, previously unseen non-Git project is processed by one public command and produces test-case artifacts plus a final summary without manual carrier creation, calibration, Git preparation, or command restart.
