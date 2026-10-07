# Portable Testing Skills Pilot — contract amendments (2026-10-07)

Status: `ACCEPTED` (accepted by the user together with the waves 2–3 task,
`docs/review/2026-10-07-waves-2-3-prompt.md`; design `docs/review/2026-10-07-waves-2-3-design.md`, section 4).

`contracts/pipeline.json` remains the sole machine truth: section `contract_amendments` lists these
amendments, and the `optional_*`, `mutation_tooling` and `model_runner` sections carry their
machine-readable form. Like the [erratum of 2026-09-01](2026-09-01-portable-testing-skills-pilot-contract-erratum.md),
this document replaces only the statements listed below.

Every amendment is **opt-in**. A run that does not enable the new option is governed by the frozen
contract and the erratum exactly as before: the same lifecycle, artifacts, results and exit codes.

## Wave 2

### A1. §1 item 2 — the driver may launch a model CLI process

The pilot still provides no shell command that owns LLM credentials. As an option the deterministic
driver MAY launch a model CLI process itself:

- `next … --review-runner process` — the driver runs every review part; other model tasks still go to the
  orchestrating host;
- `run --runner process` — the driver gives every model task to the runner (no orchestrating session).

Rules:

1. Credentials stay with the CLI. The package never reads, stores or passes them.
2. Commands come from closed package presets (`claude`, `codex`). `.skillsrc` (`review_runner`) selects only
   the preset, models per role, `max_parallel` and `timeout_seconds`. A custom command template is accepted
   only as a launch flag of the run, never from a project file.
3. Each invocation is a new process without history, in a temporary working directory outside the project,
   with the role input on stdin and without write or shell tools. The driver writes the answer file; the model
   writes nothing.
4. The answer passes the same checks as `submit`. Transport failures and malformed output are retried
   (at most 3 tries per part, as in the review-part retry rule), rate limits pause; every step goes to
   `driver-log.jsonl`.
5. Evidence per review part: command digest without secrets, CLI name and version, model as reported by the
   CLI (or as configured, marked so), start and end time, exit code, CLI session or thread ID (distinct per
   part), stdout digest, tokens, and whether user-level CLI settings were loaded.
6. Isolation evidence level `isolation_evidence` ∈ {`DRIVER_PROCESS`, `HOST_DECLARED`, `NONE`} sits next to
   `review_independence`. `DRIVER_PROCESS` is derived only from durable evidence of a process the driver
   launched and whose output the driver read; an answer submitted by the host is at most `HOST_DECLARED`.
7. Acceptance does not change by default. With `--require-driver-isolation` any level below
   `DRIVER_PROCESS` gives `accepted = false` with reason `REVIEW_ISOLATION_UNVERIFIED`.

### A2. §1 item 5 — pinned mutation tool jars may be resolved, by consent only

The pipeline still installs no dependency. Exception: when `.skillsrc` has `mutation.enabled: true`
**and** the run-scoped authorization records `mutation_requested: true`, the project's own build tool MAY
resolve the pinned mutation tool jars into its local repository:

- PIT command line `org.pitest:pitest-command-line` and `org.pitest:pitest-junit5-plugin`, versions and
  SHA-256 of every jar of their closure fixed by the package (`tools/mutation_tools.json`);
- `org.junit.platform:junit-platform-launcher` of the project's own JUnit Platform version (recorded with
  its digest, not pinned).

The resolver descriptor, classpath files and reports live in the run directory. No project file, build
configuration or dependency declaration changes. A digest mismatch makes the mutation stage `NOT_RUNNABLE`
and changes nothing else. Without both consents nothing is downloaded.

### A3. §17 — optional lifecycle stage `MUTATION`

The linear lifecycle gains one optional stage:

`… execution trace -> [MUTATION] -> retain/cleanup decision -> …`

1. `MUTATION` runs only when A2's consents hold and verification is `PASS` or `FAIL` from an authoritative
   execution report. Generated test files are still in the project, even after `FAIL`.
2. Only passing generated methods are mutated; failing methods are excluded.
3. The stage writes only into the run directory and proves the project inventory identical before and after.
4. It publishes one mutation receipt. Its result is a separate axis `test_strength` ∈ {`MEASURED`,
   `NOT_RUNNABLE`, `NOT_APPLICABLE`}. It never changes verification, acceptance, dispositions or any earlier
   event, cause or receipt.
5. Survivor triage (`mutation-triage`) is a post-terminal report task: its proposals and questions change no
   test case and no test in wave 2.

## Wave 3

(Added at the start of wave 3: §17 item 2 — quarantine disposition policy; §25 — the package may change only
tests it owns whose digest matches the suite manifest.)

## What remains in force

- The package creates no project copy, worktree, sandbox or virtual environment.
- It changes no product source, project dependencies, build configuration or secrets.
- It touches no foreign or human-edited file.
- It creates no commit, push, pull request, CI or cron job.
- Model claims, host-declared isolation and absent runtime evidence are never treated as proof; reviewer
  independence is still proved by controller evidence (§2).
- Without the new options every profile, artifact, result tuple and exit code is unchanged.
- `implemented_unverified` and `ready_tuple = null` remain in force; lifting them is the user's decision.
