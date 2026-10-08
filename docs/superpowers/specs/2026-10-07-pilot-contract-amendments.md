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
   `driver-log.jsonl`. *Clarified 2026-10-08 (independent review, item 7):* a rate or usage limit (HTTP 429,
   overload, a subscription limit message) is a pause, not a try: the pause doubles from 60 s up to 30 min,
   at most 8 pauses per task; only a limit that outlasts them counts as a failed try (`RUNNER_RATE_LIMITED`).
5. Evidence per review part: command digest without secrets, CLI name and version, model as reported by the
   CLI (or as configured, marked so), start and end time, exit code, CLI session or thread ID (distinct per
   part), stdout digest, tokens, and whether user-level CLI settings were loaded.
6. Isolation evidence level `isolation_evidence` ∈ {`DRIVER_PROCESS`, `HOST_DECLARED`, `NONE`} sits next to
   `review_independence`. `DRIVER_PROCESS` is derived only from the record of a process the driver
   launched and whose output the driver read; an answer submitted by the host is at most `HOST_DECLARED`.
   *Clarified 2026-10-08 (independent review, item 8):* the record is bound to the launch — the shim alone
   receives a one-time launch token (its environment, never the runner directory, which keeps only the
   token's digest), and `result.json` must carry that token and the digest of the stdout it captured; an
   unbound result is a failed try (`RUNNER_RESULT_UNBOUND`). This catches a mistaken or careless host that
   writes into the runner directory. It is **not** proof against a host acting as the machine's own user,
   which can rewrite both the launch record and the result: against such a host `DRIVER_PROCESS` is worth
   no more than `HOST_DECLARED`. The level states how the answer was obtained, not that the host could not
   have interfered.
7. Acceptance does not change by default. With `--require-driver-isolation` any level below
   `DRIVER_PROCESS` gives `accepted = false` with reason `REVIEW_ISOLATION_UNVERIFIED`.

### A2. §1 item 5 — pinned mutation tool jars may be resolved, by consent only

The pipeline still installs no dependency. Exception: when `.skillsrc` has `mutation.enabled: true`
**and** the run-scoped authorization records `mutation_requested: true`, the project's own build tool MAY
resolve the pinned mutation tool jars into its local repository:

- PIT command line `org.pitest:pitest-command-line` and `org.pitest:pitest-junit5-plugin`, versions and
  SHA-256 of every jar of their closure fixed by the package (`tools/mutation_tools.json`);
- `org.junit.platform:junit-platform-launcher` of the project's own JUnit Platform version (recorded with
  its digest, not pinned). *Clarified 2026-10-08 (independent review, item 13):* the launcher family
  (`junit-platform-*`, `opentest4j`, `apiguardian-api`, `jspecify`) is accepted by name, each jar's SHA-256
  is recorded in the mutation receipt, and a `junit-platform-*` jar must carry the project's Platform version;
  the resolver `maven-dependency-plugin` is fixed by version only.

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

### A4. §17 item 2 — disposition policy `quarantine`

§17 item 2 still holds by default: after `FAIL` or `NOT_RUNNABLE` every byte-identical pipeline-owned
materialized file receives `CLEANED`. As an option the run MAY use the disposition policy `quarantine`:

- `local-pilot-v1` — the default since the wave-3 gate (2026-10-08): a new run records
  `disposition_policy: quarantine` in its run-scoped authorization unless it is started with
  `--disposition-policy cleanup`, which keeps the frozen §17 item 2; a run created before (without the key)
  keeps cleanup;
- `suite-update-v1` — the profile's default.

Rules:

1. The policy applies only after an authoritative `FAIL` with per-method outcomes. `NOT_RUNNABLE`,
   `EXECUTION_UNKNOWN`, partial materialization and every other branch keep the frozen rules.
2. A file whose selected methods all passed receives `RETAINED`.
3. A file with failed methods receives `QUARANTINED`: the package rewrites only that file, only when its
   bytes equal the materialized digest, and changes only the failed methods — a JUnit 5 method gets
   `@org.junit.jupiter.api.Disabled` and a pytest function gets `@pytest.mark.xfail(strict=True)`, each with
   the reason and the reference of the run and method. The new bytes have their own digest in the
   disposition receipt; nothing else in the file changes.
4. A file whose bytes changed after materialization is preserved with the frozen conflict outcome.
5. Verification, coverage, acceptance and the result tuple do not change: a `FAIL` run is never accepted,
   with or without quarantine.
6. A quarantined method is run explicitly on the next suite run (JUnit 5:
   `junit.jupiter.conditions.deactivate`; pytest: `--runxfail`), so a fixed defect is seen.

### A5. §25 — the package may change only tests it owns, recorded in the suite manifest

§25 still forbids changing existing tests and auto-repair after a runtime failure, except as follows.

1. A **suite** is the project directory `.skillsrc` `suite.path` (default `test-cases/`): the canonical
   document, its human projections and the suite manifest (`suite-manifest.schema.json`). It is written only
   under run-scoped authorization (`local-pilot-v1 --suite`, or the `suite-update-v1` profile).
2. The manifest records, per test case, the requirement keys and text digests, the test method and the digest
   of its source slice, the status (`ACTIVE`, `QUARANTINED`, `RETIRED`), the quarantine reason and
   reference, the last green run and the kill ratio of mutants; per test file, the digest of its SUPPORT code.
3. The package MAY change a test method only when the method is listed in the manifest and its slice digest
   matches the manifest (and the SUPPORT digest, when SUPPORT changes). Permitted changes: **update** (the
   case changed because its requirement changed), **repair** and **quarantine**. Everything else is a human
   edit: it is never overwritten or quarantined; the proposed change goes to the pull request description.
4. **Repair** exists only in `suite-update-v1`: one try per failing method, only when the test does not
   compile or its own code fails while the case and its requirements are unchanged; it goes through the static
   review; expectations and assertion literals must stay the same, and the method's kill ratio must not drop.
5. The profile `suite-update-v1` updates an existing suite: migration, impact analysis, update and repair,
   review of the changed cases, automation of the changed cases, static review of the changed slices, a run
   of the whole suite, failure triage and quarantine, mutations, manifest, summary. Its result is a
   **proposal**: changes in the working tree, `pr-description.md` and a patch in the run directory. It is not a
   pilot attempt, has no `accepted` and no pilot result tuple, and never commits, pushes or opens a pull
   request.
6. Product code, project dependencies, build configuration, secrets and files outside the suite and the
   manifest's test files are never changed.

## What remains in force

- The package creates no project copy, worktree, sandbox or virtual environment.
- It changes no product source, project dependencies, build configuration or secrets.
- It touches no foreign or human-edited file.
- It creates no commit, push, pull request, CI or cron job.
- Model claims, host-declared isolation and absent runtime evidence are never treated as proof; reviewer
  independence is still proved by controller evidence (§2).
- Without the new options every profile, artifact, result tuple and exit code is unchanged.
- `implemented_unverified` and `ready_tuple = null` remain in force; lifting them is the user's decision.
