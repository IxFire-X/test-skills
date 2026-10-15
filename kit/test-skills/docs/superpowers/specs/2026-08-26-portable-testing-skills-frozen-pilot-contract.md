# Portable Testing Skills Pilot — consolidated frozen normative contract

> **Accepted erratum (2026-09-01):**
> [Portable Testing Skills Pilot contract erratum](2026-09-01-portable-testing-skills-pilot-contract-erratum.md)
> supersedes only the four historical statements listed there.

Status:

- `product_frontier = CLOSED`
- `architecture_status = FROZEN`
- `shared_understanding = CONFIRMED`

This document is the single normative architecture for the pilot. It consolidates the
accepted Q50–Q78 decisions, candidate 2, replacement sections A–K, closure rules, and
the Final Normative Errata. There are no replacement layers after this text. Earlier
interview notes are historical evidence only.

The terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 1. Product boundary

1. The product is a portable skill-pack invoked by a compatible model-enabled CLI.
   The normative entry is one explicit invocation of `skills/orchestrate/SKILL.md` (or
   its exact registered equivalent) in such a CLI.
2. `scan`, `status`, execution helpers, schema validators, and other Python commands are
   deterministic controller/recovery primitives. The pilot does not provide a
   standalone shell `pipeline run` that owns LLM credentials or calls a model itself.
3. The CLI is the model controller. Project-native Maven, Gradle, or pytest is the local
   test executor. A remote company runner is a future adapter and MUST NOT block the
   core pilot. There is no automatic fallback between execution modes.
4. The pilot target is `pilot-ready`, not `production-ready`. Company-runner deployment,
   production rollback, and real Zephyr tenant compatibility are independent future
   readiness claims.
5. The skill-pack is copied into an ordinary, explicitly trusted working project. The
   pipeline creates no clone, worktree, disposable copy, sandbox, virtual environment,
   runtime, dependency installation, CI job, cron job, commit, push, or pull request.
6. The primary feature input is one requirements document for a new feature or a change
   to an existing feature. A path, target, module, or other explicit scope MAY narrow
   it. The project path alone authorizes read-only inventory, not invention of an
   arbitrary feature to test.

## 2. Compatibility and verification levels

A host is `compatible` only if it can:

1. read the exact registered Skills, references, schemas, and machine contract;
2. invoke every model stage with its declared immutable inputs;
3. run the declared deterministic tools;
4. persist exact artifacts and read them back before advancing;
5. create one separate fresh role-isolated reviewer invocation;
6. surface at most one unresolved user question at a time; and
7. resume the same nonterminal attempt from durable state in a new CLI session after
   snapshot and configuration validation.

A host/skill-pack tuple is `verified` only when host evidence additionally records the
host/runtime version, observable generator and reviewer model identities, invocation
IDs, roles, role policy, exact input/output digests, and release-eval evidence. Unknown
model or runtime details are never inferred.

Reviewer independence is proved by host/controller evidence, not by a model statement.
If the host cannot prove fresh role isolation, the receipt records
`independence_unverified`. The same model MAY be used for generation and review only in
separate invocations with fresh context and separate role policy.

## 3. Identities and immutable boundaries

The following identities are distinct and MUST be digest-bound:

- skill-pack release and digest;
- compatibility-contract version;
- pipeline-contract version;
- canonical-model/schema version;
- individual stage-schema versions;
- projection-profile versions;
- acceptance-policy profile and version;
- release-eval suite version;
- run ID;
- exact project/module identity;
- attempt ID and optional parent/retry lineage;
- baseline snapshot identity;
- inventory identity;
- context-selection and batch identities;
- source requirement IDs and canonical requirement IDs;
- candidate/effective canonical revisions and digests;
- reviewer session, invocation, and verdict identity;
- automation, generated file, execution, trace, disposition, finalization, and terminal
  result identities.

Package, pipeline, canonical, stage, projection, and eval versions evolve independently.
A release manifest binds their exact versions and digests.

## 4. Run and attempt model

### 4.1 Run boundary

Before project scan and before any model invocation, the controller MUST atomically
publish and read back:

- `run-manifest.json`;
- a durable run ID; and
- the append-only `RUN_CREATED` event.

The run starts before module selection, but becomes permanently bound to exactly one
verified project/module identity when the first attempt is created. All child attempts
in that run MUST use the same project/module identity. A different module requires a
different run; cross-module orchestration is outside the pilot.

One run MAY contain a sequential append-only lineage of child attempts. At most one
attempt in that lineage is nonterminal at a time.

### 4.2 Attempt boundary

The controller MUST select and verify the exact module before `ATTEMPT_CREATED`. The
execution baseline MUST already be complete and frozen before `ATTEMPT_CREATED` is
published. An attempt binds at least:

- run ID, attempt ID, parent attempt ID, and retry reason when applicable;
- exact project/module identity, including a nested module root when selected;
- acceptance-policy profile and version;
- exact requirements identity;
- `.skillsrc` bytes/digest and accepted receipt;
- skill-pack and compatibility-contract identities;
- model/role policies and any declared run budget;
- eligible inventory and execution-baseline identities; and
- the closed execution adapter selection when execution is in scope.

Attempt history is append-only. Existing artifact bytes MUST NOT be overwritten.
Terminal attempts are immutable. A changed requirement, module, policy, baseline input,
or intentional retry creates a child attempt.

### 4.3 Single-active-attempt rule

`ACTIVE`, `WAITING_FOR_INPUT`, and `WAITING_FOR_MODEL` are nonterminal and count as the
one active attempt for the exact project/module identity. A second active attempt for
that identity MUST be rejected with the existing attempt ID.

A pre-execution attempt MAY be explicitly closed by an append-only terminal transition.
After `EXECUTION_STARTED`, a lost or interrupted execution becomes
`EXECUTION_UNKNOWN`. No new attempt for that module may execute until process stoppage
has been proved and explicit recovery has completed. An execution retry is then a child
attempt; it is never an automatic rerun.

### 4.4 Resume

Interruption between model stages and `WAITING_FOR_INPUT` or `WAITING_FOR_MODEL` remains
nonterminal. `resume` continues the same attempt only after revalidating immutable
configuration, baseline, inventory, receipts, and all already-published artifact bytes.
Completed stages are not regenerated.

## 5. Append-only event journal and artifact-first order

The append-only event journal is the authority for ordering. Each controller-observed
event contains at least:

- a monotonically increasing `seq` within the run;
- `event_type`;
- `run_id`;
- optional `attempt_id` and `batch_id`;
- host-observed UTC `observed_at`;
- actor/role; and
- the digest of the related artifact or receipt when one exists.

The host records time; a model never attests its own timestamp. Derived summaries MAY
project run creation, attempt creation, inventory, snapshot, first model request, first
candidate, first reviewed candidate, execution start, and terminal time. Events that do
not apply to a branch are absent, not fabricated.

The following order is mandatory:

1. `RUN_CREATED` precedes scan and every model request.
2. `MODULE_SELECTED` precedes `ATTEMPT_CREATED`.
3. `ATTEMPT_CREATED` precedes transmission of module source bytes to a model.
4. `INVENTORY_READY`, `SNAPSHOT_BOUND`, and `CONTEXT_SELECTED` precede the related
   `MODEL_REQUESTED` event.
5. `MODEL_RESPONSE_RECEIVED` precedes publication of its schema-valid candidate or
   fragment.
6. A schema-valid candidate/fragment is published immediately as `UNREVIEWED`; later
   batches, review, correction, automation, and execution MUST NOT delay or delete it.
7. `CANDIDATE_PUBLISHED` precedes `REVIEW_REQUESTED` for that exact candidate digest.
8. Waiting, partial, rework, and terminal transitions retain every previously published
   immutable artifact.
9. A terminal event is emitted only after the finalization receipt is complete and has
   been read back.

`slo_status` is `NOT_ESTABLISHED`. Until measured baselines exist, the release gate
checks event-order invariants and telemetry completeness, not invented seconds or
minutes. The primary future metric is time to first useful artifact, such as normalized
requirements/context or the first schema-valid `UNREVIEWED` candidate.

## 6. `.skillsrc`, project discovery, and module selection

1. A missing `.skillsrc` is created automatically and atomically from deterministic
   discovery.
2. An existing valid `.skillsrc` is authoritative user configuration and MUST NOT be
   silently overwritten, merged, or normalized on disk.
3. Scanner drift is published as a proposed version and exact diff. Any
   execution-significant change requires `WAITING_FOR_INPUT`/`NEEDS_INPUT`.
4. After explicit confirmation, the original bytes are preserved as evidence and the
   new file is written atomically. Invalid or ambiguous configuration fails closed.
5. In a multi-module project, exactly one module is selected. Ambiguity surfaces one
   concrete question; probability-based selection is forbidden.
6. One attempt operates from the selected module root as its `cwd`, snapshot root, test
   root, and adapter context, including when that module is nested.
7. Unsupported stacks still proceed through context and Russian manual test-case
   generation. Unsupported automation/execution is represented truthfully, not as a
   fatal loss of useful manual artifacts.

## 7. Safe inventory, secrets, and model context

### 7.1 Eligible inventory

An explicit pipeline invocation permits local read-only inventory of the selected
module's eligible tree. The inventory excludes at least:

- `.git` and VCS internals;
- dependency/vendor directories;
- build outputs, caches, binaries, and generated artifacts;
- the resolved `skill_pack_root` wherever it sits inside the project; and
- files suspected of containing secrets.

The controller records opaque file ID, safe relative path, language/type, size, and
content digest for eligible files. Inventory is not equivalent to model context.

A potential-secret file is wholly excluded from model context; automatic redaction and
a corporate-provider exception are forbidden. If its semantics are required, the
pipeline requests a sanitized source, safe fixture, or opaque runtime handle.

Portable exclusion evidence contains only path, exclusion reason, scanner rule, and a
safe label. It contains no raw secret, ordinary secret-content hash, or secret value. An
optional drift fingerprint may use a machine-local keyed HMAC that is never exported.

### 7.2 C-lite context selection

The model receives normalized requirements, explicit target if present, and a safe
inventory. It returns only selected opaque file IDs. The controller verifies ownership,
containment, exclusions, digests, and context budget before reading/transmitting bytes.
It MAY add a small closed set of known required module manifests, test configuration,
and fixtures.

The pilot does not claim a universal dependency/symbol analyzer for Python reflection,
Java reflection, generated code, or framework wiring. Cheap deterministic relationships
already proved by the scanner MAY be used.

Every exact byte set sent to a model receives an immutable context-selection/batch
receipt. Files are never silently truncated. Exceeding one context batch continues in
additional batches. `NEEDS_INPUT` is used only for a real user choice, not merely because
one model context is too small. A genuinely exhausted predeclared run budget or observed
external host limit produces terminal `PARTIAL` with exact diagnostics and preserves all
artifacts. A budget MUST NOT be invented after model work begins.

`--target` narrows the initial selection scope but never authorizes leaving the selected
module or reading excluded files.

## 8. Frozen execution baseline

The execution baseline is the complete declared set of inputs whose bytes or identity
can affect exact reviewed test execution. It includes, when applicable:

- requirements and `.skillsrc` identities;
- selected eligible source, test, configuration, fixture, and resource files;
- selected module and parent build configuration;
- wrapper/interpreter path and bytes or stable identity;
- closed adapter ID/version, build profile, and typed adapter parameters; and
- locally proved dependency inputs, including cross-module files only when they are
  exact declared execution inputs.

The model-context selection is recorded separately and may be a strict subset of this
baseline. A different module attempt does not invalidate the current attempt merely by
existing; only a changed declared baseline input does.

Pipeline-owned generated files form an allowed, separately receipted delta over the
baseline. Any other baseline drift blocks execution/resume and requires a child attempt.

A late-discovered execution input MUST NOT be added to the current frozen baseline. The
current attempt becomes `NOT_RUNNABLE` with terminal reason
`BASELINE_INCOMPLETE`; continuation is allowed only through a child attempt with a new
complete baseline. If a local dependency cannot be safely and exactly related in the
pilot, the result is `NOT_RUNNABLE`/unsupported tuple. This does not add cross-module
orchestration.

## 9. Requirements, batching, and canonical assembly

### 9.1 Distinct requirement identities

Source requirements and canonical requirements are different identities.
`owned_source_requirement_ids` partition all in-scope normalized source requirements:
every source requirement belongs to exactly one batch, with no omissions or overlaps.

Each fragment contains explicit many-to-many mappings:

`source_requirement_id(s) -> canonical_requirement_id(s) -> case_id(s)`.

All source IDs mapped to one canonical requirement belong to the same batch. Every
canonical requirement referenced by a case belongs to that case's batch. One case MUST
NOT reference source or canonical requirements from another batch. An identifier such as
`REQ-001` cannot ambiguously mean both a source and canonical requirement without an
explicit mapping.

`out_of_scope` is valid only when fixed by the preselected feature/module scope; a
generator cannot discard a requirement by declaring it out of scope.

### 9.2 Batch plan

The deterministic default is one batch. Requirements are split only when normalized
input provides provable grouping evidence, such as separate named feature groups or
independent exact targets. If independence is unproved or a shared scenario is possible,
the controller uses one batch.

Each batch has a stable `batch_id`, ordinal, controller-owned ID namespace,
`owned_source_requirement_ids`, immutable header digest, context-selection digest, and
completeness state. Cross-batch test cases are forbidden.

### 9.3 Fragment contract

Each batch publishes exactly one schema-valid immutable candidate fragment. A fragment
references the controller-owned canonical header and contains:

- canonical requirements;
- source-to-canonical mappings;
- used operation capabilities or exact references to an immutable shared catalog;
- complete canonical test cases, including ordinary steps, operations, inputs, outputs,
  expectations/assertions, `manual_only`, manual reasons, and blockers; and
- envelope-level diagnostics.

There is no separate semantic `dispositions` language. Manual/blocker dispositions use
the same canonical structures that enter the final JSON.

Additional context retrieval occurs before fragment publication. One bounded retry may
repair an invalid transport/schema response because no complete candidate yet exists;
it is not a semantic revision. A published fragment has no batch-level semantic revision
2. If it needs semantic change before assembly, the attempt becomes `PARTIAL` and a new
batch plan is created only in a child attempt.

An independent batch failure SHOULD NOT block not-yet-started independent batches unless
there is a secret exposure, invalid plan/contract, or another fatal trust violation.

### 9.4 Deterministic assembler

The assembler may only:

- verify header, plan, fragment, context, and artifact digests;
- order fragments by batch ordinal;
- exact-union byte-identical shared capabilities;
- reject one capability ID with different bytes;
- copy requirement and case semantic fields without alteration;
- deterministically renumber only top-level `display_order` after union;
- build source/canonical/case provenance relations; and
- calculate the document digest and assembly receipt.

It MUST NOT change titles, objectives, preconditions, actions, test data, expected
results, operations, assertions, manual reasons, or blockers. Batch-specific ID
namespaces prevent accidental short-ID collisions.

Plan ownership overlap/missing ownership is a pre-model controller-contract error.
Cross-batch requirement references yield `BATCH_PARTITION_CONFLICT`; same ID with
different bytes yields `BATCH_ID_CONFLICT`; conflicting mappings/dispositions yield
`BATCH_SEMANTIC_CONFLICT`. These block assembly, preserve fragments, publish no canonical
candidate, and invoke no reviewer. They produce `PARTIAL`, or `FATAL` only for a proven
controller-contract defect. Fuzzy deduplication is outside the pilot. A deterministic
case fingerprint excluding identity/display-order fields may only emit
`POSSIBLE_DUPLICATE`; it never merges, deletes, or renames cases.

Before review the assembler performs batch-plan, canonical schema, canonical semantic,
requirements-coverage, and provenance/assembly audits. These are pre-review audits, not
the final pipeline trace.

## 10. Canonical document and projections

1. One assembled bare canonical JSON is the semantic source of truth.
2. It contains an explicit canonical model/schema version and
   `content_locale = "ru-RU"`.
3. Human test-case fields are Russian. Paths, identifiers, JSON keys, literals, and
   project-native technical names are not translated.
4. Deterministic validation enforces structure and may lint a total absence of Cyrillic;
   only the fresh-context reviewer judges idiomatic Russian, semantic fidelity, and
   completeness.
5. There is no fixed test-count limit. Every in-scope source requirement has a traceable
   case/step, manual, blocker, or predeclared out-of-scope disposition.
6. Canonical human meaning MUST be preserved. A projection may escape, lay out, and make
   explicitly specified substitutions, but MUST NOT silently change or drop meaning.
7. Confirmed machine literals and source evidence determine technical fact. A semantic
   correction of human fields is a complete successor revision; unresolved evidence
   yields rework, not an invented answer.

## 11. Canonical reviewer session and corrections

### 11.1 Reviewer package

The reviewer receives the full assembled candidate, normalized source requirements,
source/canonical/case mappings, inventory identity, batch/context receipts, exact
generator-visible source evidence, provenance, and immutable requirement/context
digests. It does not receive generator dialogue or hidden reasoning.

The reviewer may independently use C-lite retrieval to request additional permitted
source evidence from the same immutable inventory. Before each evidence transfer the
controller enforces the fixed review-context budget. There is no silent truncation.

If the full candidate plus the minimum required evidence cannot fit even after allowed
selection, the branch terminates `PARTIAL` with `REVIEW_CONTEXT_LIMIT`,
`verification = NOT_APPLICABLE`, `accepted = false`, and coverage preserved if already
established (otherwise `null`). The pipeline does not switch to per-batch review.

### 11.2 Exactly one logical reviewer

There is one fresh role-isolated reviewer session for the complete canonical branch.
Within that session, bounded multiple calls are permitted only for C-lite evidence
retrieval and the final response. The following are forbidden:

- per-batch model reviewers;
- hierarchical reviewer trees;
- multiple reviewer sessions for one canonical branch; and
- multiple authoritative reviewers or verdicts.

The allowed logical sequence is:

1. `REVIEW_SESSION_STARTED`;
2. zero or more bounded `EVIDENCE_REQUESTED -> EVIDENCE_PROVIDED` pairs;
3. either exactly one `AUTHORITATIVE_VERDICT` followed by successful session completion,
   or an explicit terminal pre-verdict abort; and
4. immutable session/readback receipts.

At all times there is at most one `AUTHORITATIVE_VERDICT` on the canonical branch.
Exactly one is required for a successfully completed reviewer session and for any
accepted/effective canonical. At terminal state, zero is permitted only for an explicit
pre-verdict abort, including `REVIEW_CONTEXT_LIMIT`. A nonterminal waiting session may
temporarily have zero until resume.

### 11.3 Semantic revision budget

Test-case generation has two complete semantic versions at most:

- assembled canonical candidate revision 1; and
- one optional complete reviewer-produced successor revision 2.

The successor preserves identity/lineage and may be selected only after schema,
semantic, provenance, and readback validation. A destructive, partial, or choice-bearing
change yields `REWORK`, not an authoritative partial patch. A further rejection closes
the branch `TERMINAL + PARTIAL` with all revisions retained.

Automation generation separately permits an initial version and at most one correction
followed by review. There are no unbounded generate/review loops. Every version and
review is append-only.

## 12. Automation and traceability

Automation is generated only from the exact effective canonical revision and bundle
receipt. Static review binds the complete automation digest, every generated file and
symbol, and the exact canonical relations.

Traceability starts with deterministic source requirement identity and provenance, then
links source requirements to canonical requirements, cases/manual/blocker dispositions,
steps, expectations, assertions, generated files/symbols, and execution evidence.
Mappings may be many-to-many but must obey the single-batch case boundary.

An unsupported automation capability preserves useful manual cases and produces a
truthful non-accepted branch. Execution failure never authorizes automatic test repair.
A fresh diagnostic review may recommend a classification but cannot change test bytes,
product bytes, the frozen baseline, or authoritative verification. Any such change uses
a child attempt. Later human diagnosis is a separate append-only artifact and does not
rewrite the terminal attempt.

## 13. Run-scoped local execution authorization

There is no permanent trust store in the pilot. An explicit request for the full
pipeline grants run-scoped authorization to execute only the exact reviewed targets of
that run in the ordinary configured project. A request for cases only grants no code
execution. Merely placing the skill-pack folder in a project grants no execution.

The authorization receipt records a safe run-scoped fact and digest; it does not persist
the user's raw dialogue or credentials.

Project-native execution is not a sandbox. Pytest plugins, `conftest.py`, fixtures,
Maven/Gradle plugins, and lifecycle hooks may execute trusted project code with the
current user's permissions.

## 14. Closed execution adapters

The pilot exposes only these versioned adapter IDs:

- `pytest:selected-symbols-v1`;
- `maven-wrapper:selected-symbols-v1`; and
- `gradle-wrapper:selected-symbols-v1`.

`.skillsrc` and the selected module determine the adapter, module-selected
interpreter/venv or module-local wrapper, ordinary project configuration, declared build
profile, typed adapter parameters, module `cwd`, exact reviewed selectors, timeout, and
report locations. Ambiguity produces `NEEDS_INPUT` before execution.

The project-facing interface MUST NOT contain `build_command`, `argv_template`, shell
strings, pipes, redirections, substitutions, or an unsafe escape hatch. The adapter may
internally build a list of argv tokens. The execution receipt records adapter ID/version,
resolved executable, exact argv tokens, module `cwd`, selector set, timeout, report
locations/digests, and safe environment labels.

The environment receipt contains only allowlisted safe key IDs/labels and never values.
Non-allowlisted raw environment key names and all secret values are omitted. Runtime
secret use is through project-native safe fixtures or opaque handles.

The pipeline installs nothing. Missing executable, ambiguous interpreter/wrapper,
unprepared configuration, unrelatable dependency, or unsupported tuple yields
`NOT_RUNNABLE` before a test result is claimed.

## 15. Generated file set and materialization

Generated output is a set, `generated_delta`, never a singular generated file. Every
file has an exact path, content digest, source automation/review digests, baseline-absence
proof, and individual materialization receipt.

After accepted static automation review, the controller may materialize the complete set
into the active module test root. It may create only files absent from the baseline. It
MUST NOT overwrite an existing different file. A byte-identical pipeline-owned file is
idempotent only when its ownership receipt is valid.

Execution MUST NOT begin after partial materialization. That branch becomes:

- `attempt_state = TERMINAL`;
- `completion = PARTIAL`;
- `verification = NOT_APPLICABLE`;
- `reason_code = MATERIALIZATION_INCOMPLETE`; and
- `accepted = false`.

Successfully materialized unchanged files are safely cleaned; files never written receive
`NOT_MATERIALIZED`. Disposition and finalization receipts are still produced before the
terminal transition.

## 16. Execution result semantics

The execution verifier runs only exact reviewed targets and derives authoritative status
from exact framework evidence.

- An authoritative framework result for an exact-test timeout is `FAIL`.
- A controller/process timeout without an authoritative framework result is `UNKNOWN`.
- Lost/invalid report, process crash after execution start, unproved process termination,
  or execution-time digest drift is `UNKNOWN`, not test `FAIL`.
- Missing prerequisites before start are `NOT_RUNNABLE`.
- `FAIL` requires authoritative exact-test evidence.

An authoritative `FAIL` may later receive a separate diagnosis of `UNCLASSIFIED`,
`PRODUCT_DEFECT`, `TEST_DEFECT`, `FIXTURE_DEFECT`, or `ENVIRONMENT_DEFECT`. Diagnosis
does not determine or rewrite verification. `INFRASTRUCTURE_FAILURE` is operational
diagnosis, not proof of a failed exact test.

## 17. Linear post-materialization lifecycle and dispositions

For every execution-capable branch, the physical lifecycle is strictly:

`materialization -> execution -> execution trace -> retain/cleanup decision ->`
`disposition receipts -> pre-finalization trace -> finalization verification ->`
`completed/read-back finalization receipt -> terminal result -> derived terminal trace ->`
`terminal event`.

No later stage rewrites an earlier event, cause, receipt, or disposition.

Disposition is defined for the entire generated delta and every file:

1. After authoritative `PASS`, valid pre-finalization trace, and exact path/digest/ownership
   checks, a materialized file may receive `RETAINED`. This is a physical
   pre-finalization disposition and does not itself mean `accepted = true`. `MIXED`
   coverage is allowed.
2. After `FAIL` or `NOT_RUNNABLE`, every byte-identical pipeline-owned materialized file
   MUST receive `CLEANED`. If safe cleanup is impossible, the file is preserved with an
   exact conflict/reason receipt and `accepted = false`.
3. After `EXECUTION_UNKNOWN`, cleanup is forbidden until process stoppage is proved:
   - unchanged file -> `PRESERVED_EXECUTION_UNKNOWN`;
   - changed file -> `PRESERVED_CONTENT_CONFLICT` plus execution-unknown evidence.
4. Cleanup is permitted only for a byte-identical, pipeline-owned file that did not exist
   before materialization.
5. A partial-materialization branch uses `CLEANED` for safely removed files and
   `NOT_MATERIALIZED` for files never created.

The generated test remains in the active test root for future ordinary regression only
when its recorded disposition is `RETAINED`. Retention does not cause commit, push, CI,
or schedule changes.

## 18. Orthogonal result axes and reason preservation

The authoritative structured result has orthogonal fields:

- `attempt_state`: `ACTIVE | WAITING_FOR_INPUT | WAITING_FOR_MODEL | TERMINAL`;
- `completion`: `COMPLETE | PARTIAL | FATAL | null/absent`;
- `verification`: `PASS | FAIL | UNKNOWN | NOT_RUNNABLE | NOT_APPLICABLE | null/absent`;
- `coverage`: `FULL | MIXED | MANUAL_ONLY | null/absent`;
- `reason_code`: terminal primary reason, absent until established; and
- `accepted`: policy result, absent before terminal and boolean at terminal.

`completion`, `verification`, and `coverage` are null/absent until their respective fact
is established. `ACTIVE` and waiting attempts have no completion. Coverage is null for
an early `FATAL` before canonical coverage exists. `REVIEW_CONTEXT_LIMIT` before
execution uses `verification = NOT_APPLICABLE`. `accepted = false` for all terminal
pre-verdict aborts and invalid finalization.

`COMPLETE` may coexist with `FAIL`: pipeline completeness and product/test outcome are
different facts. `EXECUTION_UNKNOWN` is `TERMINAL + PARTIAL + UNKNOWN`. Pre-execution
`REWORK` is `TERMINAL + PARTIAL + NOT_APPLICABLE`. `FATAL` is allowed only after a
trustworthy attempt boundary; a failure before that boundary is a controller-level error
with no invented attempt result.

Stage causes remain immutable in events and trace. `result.reason_code` is derived and
written exactly once during terminal projection. It is never overwritten. If
finalization is invalid, the primary terminal reason is `FINALIZATION_INVALID`; the
earlier stage cause remains in immutable evidence, while verification and coverage remain
unchanged.

## 19. Acceptance profiles

Exactly two runtime acceptance profiles exist in the pilot and are fixed before
generation as part of attempt identity.

### `cases-only-v1`

This profile permits `verification = NOT_APPLICABLE` and coverage `FULL`, `MIXED`, or
`MANUAL_ONLY`. `accepted = true` requires a complete schema/semantic/provenance-valid
canonical bundle, successful authoritative full-document review, verified reviewer
isolation, no unresolved blocker, branch-valid trace, and valid finalization. Execution,
materialization, and their evidence are `NOT_APPLICABLE`, not missing. An unresolved
blocker remains useful `PARTIAL` but is not accepted.

### `local-pilot-v1`

This profile requires an accepted canonical, accepted automation, complete generated
delta materialization, authoritative exact-target `PASS`, valid trace, every required
generated file `RETAINED`, valid finalization, verified reviewer isolation, and no
unresolved blocker. `MIXED` coverage is permitted when manual coverage is explicitly
traceable. `FAIL`, `UNKNOWN`, `NOT_RUNNABLE`, `MANUAL_ONLY`, unresolved blockers,
unverified independence, a non-retained required file, or invalid finalization is not
accepted.

`accepted` is calculated by a versioned policy profile and never replaces the factual
axes.

## 20. Exit-code projection

Exit code is a deterministic projection of the complete structured result and selected
profile, with this priority:

1. Controller/tool/contract error without a trustworthy attempt result -> `2`.
2. Nonterminal `WAITING_FOR_INPUT` or `WAITING_FOR_MODEL` -> `3`.
3. Terminal `accepted = true` -> `0`.
4. Terminal `verification = UNKNOWN | NOT_RUNNABLE`, `completion = FATAL`, invalid
   trace/finalization, or operationally unreliable evidence -> `2`.
5. Every other trustworthy terminal but unaccepted result -> `1`.

There is no exit code 4 in the pilot. JSON is authoritative. Status after successful
readback uses the same projection; unreadable state returns `2`.

Examples:

- `cases-only-v1 + MANUAL_ONLY + COMPLETE`, with no blockers and valid review/bundle/
  finalization -> `0`;
- the same factual result under `local-pilot-v1` -> `1`;
- authoritative test `FAIL` -> `1`;
- reviewer `REWORK/PARTIAL` -> `1`;
- `EXECUTION_UNKNOWN`, `NOT_RUNNABLE`, or invalid finalization -> `2`; and
- a durable waiting state -> `3`.

## 21. Trace and finalization without a cycle

The finalization verifier consumes `pre_finalization_trace`. That trace ends with the
established execution and generated-delta dispositions. Materialization, execution, and
disposition evidence is required only when the current branch/profile requires it. For
`cases-only-v1` and pre-execution partial branches it is explicitly `NOT_APPLICABLE`, not
missing.

Finalization verifies exact artifact digests, canonical/effective lineage, reviewer
verdict cardinality and isolation evidence, branch requirements, trace relations,
execution evidence when applicable, and the already-recorded dispositions. It then
publishes a complete finalization receipt and reads it back.

A terminal transition requires a completed and read-back finalization receipt, not
necessarily `valid = true`. An invalid receipt yields terminal `accepted = false` and
primary reason `FINALIZATION_INVALID`. Successful finalization is mandatory only for
`accepted = true`.

After finalization, the controller publishes a derived terminal trace revision that
references the exact finalization receipt and terminal result. Finalization does not
re-verify a trace that already depends on finalization, so there is no trace/finalization
cycle.

## 22. Zephyr and human projections

1. Canonical JSON remains the sole semantic truth; previews and transports are immutable
   derived projections and never downstream model input.
2. `zephyr-scale-step-row-24-v4` is the only default for new pilot bundles, examples, and
   CLI behavior. Legacy V1–V3 bundles retain their original immutable bytes and profile
   identity and are verified only with a compatible historical verifier.
3. Technical fields appear in a Zephyr transport only when required by an approved
   anonymized sample/template. Without that evidence, the profile is observed/unverified.
4. `zephyr-scale-xml-observed-v1` is a separate opt-in experimental/observed projection.
5. Core pilot readiness does not require real Zephyr round-trip. CSV and XML tenant
   readiness are separate and may be claimed only after real import/re-export evidence.
   Universal Zephyr compatibility is not claimed.

## 23. Versioning and pre-1.0 compatibility

Breaking changes are allowed before 1.0. Every artifact, package, contract, schema, and
projection version is checked explicitly. Unsupported versions fail closed with an exact
diagnostic. No general migrator is required before a real consumer exists.

Historical bundles retain immutable bytes, receipts, digests, and version identity and
remain verifiable with their original compatible tool version. A new pre-1.0 runtime is
not required to semantically read every older format. After 1.0, an N-1 promise requires
a deterministic migrator, fixtures, and round-trip tests.

## 24. Readiness, release evals, and evidence

Readiness is always qualified: `core-pilot-ready for <exact verified tuple>`. A tuple
contains at least:

- skill-pack version/digest;
- compatibility-contract and execution-profile versions;
- CLI host/runtime;
- role policy;
- observable generator and reviewer model identities;
- OS;
- language runtime;
- framework;
- build tool; and
- closed execution adapter/profile.

One project run proves only that run's immutable project snapshot. A tuple becomes
verified only through the release-eval gate. Other combinations are
`implemented_unverified` or `unsupported`, never implicitly `supported`. A chosen real
project is an acceptance fixture, not product identity; Shopizer has no special status.

The release-eval repetition policy is versioned and adaptive:

1. one smoke run;
2. three independent fresh-context runs for every critical scenario after smoke; and
3. five runs when any instability or protocol violation is observed.

The gate requires zero protocol violations and preserves every repetition's exact
evidence. The first previous-known-good release requires explicit human approval;
automatic promotion is forbidden.

The core-pilot gate requires:

- the entire current test suite;
- full contract/schema/semantic checks;
- `git diff --check` and exact changed-file audit;
- end-to-end execution on an authorized real target project using its actual JDK/Maven,
  Gradle, or pytest environment for the claimed tuple;
- early-artifact and event-order evidence;
- interruption/resume/child-retry acceptance;
- terminal immutability and no repeated execution;
- an ordinary project-native rerun of retained generated tests outside the pipeline;
- fresh-context reviewer evidence; and
- the adaptive release-eval campaign.

Company runner, production rollback, and real Zephyr tenant round-trip may remain `N/A`
for core pilot readiness, and must be reported as such.

## 25. Retention and forbidden side effects

The normal run deletes no historical attempt. External retention policy may delete
attempt artifacts by an explicit maintenance operation and SHOULD retain a minimal
ledger of run/attempt lineage, digests, terminal result, and deletion reason.

The pipeline MUST NOT:

- change product source, existing tests, dependencies, build configuration, or secrets;
- overwrite an unowned or content-conflicting generated path;
- execute after partial materialization;
- clean a generated delta after `EXECUTION_UNKNOWN` without proved process stoppage;
- auto-repair a test after runtime failure;
- create commits, push, open PRs, or alter CI/cron configuration; or
- treat model claims, unverified host isolation, or absent runtime evidence as proof.

## 26. Final architecture invariants

1. One run is permanently bound to one exact project/module identity and may contain a
   sequential append-only child-attempt lineage; at most one attempt is nonterminal.
2. The execution baseline is complete and frozen before `ATTEMPT_CREATED`; a late
   undeclared input yields `NOT_RUNNABLE/BASELINE_INCOMPLETE` and only a child attempt may
   continue.
3. Eligible inventory, model context, and execution baseline are distinct artifacts.
4. Source and canonical requirement identities are distinct and explicitly mapped;
   cross-batch cases are forbidden.
5. A canonical branch has at most one `AUTHORITATIVE_VERDICT`; a successfully completed
   reviewer session and every accepted/effective canonical have exactly one; zero at
   terminal is allowed only for an explicit pre-verdict abort.
6. There is one schema-valid fragment per batch, deterministic assembly, one full
   role-isolated reviewer session with bounded C-lite retrieval, and one authoritative
   canonical JSON.
7. Test-case semantics have at most candidate revision 1 and optional complete successor
   revision 2; automation separately has initial plus at most one correction.
8. Generated output is a set with one materialization and disposition receipt per file;
   disposition is complete for the whole set.
9. The lifecycle from materialization through terminal event is linear; cleanup is
   forbidden for every `EXECUTION_UNKNOWN` file.
10. `RETAINED` is a pre-finalization physical disposition and never means acceptance by
    itself.
11. Terminalization requires a completed/read-back finalization receipt; invalid
    finalization is terminal, unaccepted, and preserves prior factual axes and causes.
12. Finalization verifies a pre-finalization trace; a derived terminal trace references
    finalization, eliminating the cycle.
13. Result axes remain orthogonal and nullable until established; terminal reason is
    written once while all earlier causes remain append-only evidence.
14. Only closed versioned adapters build argv; no user-facing build command or shell
    string exists.
15. Core readiness is always scoped to exact verified tuples and adaptive release-eval
    evidence.

Architecture is frozen at this boundary. Implementation must realize these rules without
opening new product or architecture questions and without adding speculative mechanisms.
