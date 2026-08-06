# Portable Testing Skills — Design Specification

**Status:** draft for user review  
**Date:** 2026-08-06  
**Working base:** `test-orchestration-skills/`

## Objective

Create a testing skill pack that can be copied into different software projects and used by capable AI-agent hosts without depending on Codex-specific behavior. The pack must generate complete manual test models, review them, produce runnable automated tests, execute them, and prove end-to-end traceability.

Success means that generated test cases are logically complete, generated code follows the target project's conventions, and the resulting tests actually compile or import and pass in the declared environment.

## Chosen approach

Use a **host-neutral core with optional thin host adapters**.

- The core owns contracts, schemas, deterministic tools, skill instructions, templates, and validation.
- Adapters only translate host discovery or invocation conventions. They do not redefine pipeline semantics.
- A missing adapter must not make the skills unusable through a capable generic agent host, or the deterministic tools unusable through the command line.
- Codex packaging is an optional adapter, not the architecture of the product.

Markdown-only skills were rejected because they cannot reliably prevent contract drift or false-positive reviews. A standalone testing platform was rejected because its operational weight conflicts with the copy-and-run goal.

## Architectural boundaries

### Machine contract

`contracts/pipeline.json` is the sole normative definition of pipeline steps, accepted/forwarded/produced/rejected artifacts, verdicts, branch behavior, language capabilities, artifact-location policy, and the SDD traceability chain.

JSON Schema validates the contract before semantic checks. Markdown may explain the contract but cannot establish alternate names, verdicts, or transitions.

### Skills

The core contains six focused skills:

1. `context-marker` preserves and labels supplied requirements and code context.
2. `tc-generator` derives deterministic manual cases and requirement coverage.
3. `tc-reviewer` checks completeness, traceability, oracles, data, and unsupported assumptions.
4. `tc-to-autotest` creates project-native automated tests from accepted cases.
5. `autotest-reviewer` reviews traceability, assertions, isolation, stubs, secrets, and flakiness.
6. `orchestrate` controls the workflow and invokes deterministic gates.

Each `SKILL.md` is a concise operational instruction with only `name` and `description` frontmatter. Details and examples belong in supporting files. Skills project the machine contract; they do not duplicate it.

### Deterministic tools

The portable runtime contains independent machine-readable tools:

- a project scanner, read-only by default;
- a schema-first contract checker;
- an artifact validator;
- a trace checker for requirement → test case → file/method → execution evidence;
- an execution gate returning `PASS`, `FAIL`, or `NOT_RUNNABLE`.

Tools never use LLM inference to turn missing evidence into success.

### Host adapters

Adapters live under `adapters/<host>/` and may provide discovery metadata, invocation wrappers, or installation notes. They may not change artifact schemas, verdict meanings, language support, output-location rules, or quality gates. The core must work without an adapter; Codex is the first optional adapter.

### Distribution layout

The canonical portable layout uses stable ASCII paths:

```text
test-orchestration-skills/
  contracts/
  schemas/
  skills/<skill-id>/
  tools/
  adapters/<host>/
  tests/
  docs/to_do/
```

Existing human-language directory names may be read during migration, but cannot remain the canonical API. Skill identity comes from the stable machine identifier, not a localized folder label.

The deterministic runtime targets Python 3.10+ and declares all non-standard dependencies in a checked-in dependency manifest. Java, Python, and other project toolchains remain target-project dependencies. No external plugin is required for core operation.

## Capability policy

Java/JUnit 5 and Python/pytest are the mandatory end-to-end baseline. A language is supported only when evidence exists for project detection, generation rules/templates, language-aware review, runner discovery, real fixture execution, and inspection of generated tests and traceability artifacts.

TypeScript and Go remain experimental until the same evidence exists. Missing execution returns `NOT_RUNNABLE`, never success.

## Data flow and SDD traceability

The normative flow is:

`requirements/specification → normalized context → manual test cases → test-case review → automated tests → code review → execution → trace audit`

Traceability is mandatory:

1. Every source requirement has a stable identifier and provenance.
2. Every required test case maps to one or more requirements.
3. Every accepted test case maps to a generated file and test method.
4. Every generated method is represented in execution evidence.

When requirements lack identifiers, stable local identifiers may be created only with explicit `INFERRED` provenance. The system must not invent endpoints, status codes, schemas, permissions, or business rules.

## Artifact policy

Persistent reports, generated test models, run manifests, and isolated E2E workspaces are written only under `docs/to_do/`. Path checks reject traversal and near-match directories.

Generated test source used for verification either lives in an isolated copied workspace under `docs/to_do/`, or is written to the normal test tree only when that mutation is explicitly requested. Tools return JSON to stdout by default and never silently mutate `.skillsrc` or source files.

## Verdict and error semantics

- `PASS`: tests were discovered, executed, and passed.
- `FAIL`: the runner executed and reported failures, errors, or compilation/import failures.
- `NOT_RUNNABLE`: execution could not start because the project, toolchain, dependencies, or runner were unavailable.

`NOT_RUNNABLE` is never acceptance, and LLM review cannot override execution.

Review verdicts are `ПРИНЯТО`, `AUTO_FIX_APPLIED`, and `ТРЕБУЕТ ДОРАБОТКИ`. Autofix is limited to safe, local, semantics-preserving corrections. Semantic changes, invented requirements, destructive project mutations, and retries after material rework require explicit user confirmation.

## Testing strategy

1. Unit-test parsers, confinement, runner selection, aggregation, schema validation, and trace algorithms.
2. Mutation-test invalid transitions, verdict branches, capabilities, forwarding, and artifact roots.
3. Validate positive and negative fixtures for every artifact schema.
4. Forward-test skills with fresh agents that receive no hidden conversation state.
5. Execute a real Java/JUnit 5 full-path fixture and verify method traceability.
6. Execute a real Python/pytest full-path fixture and verify method traceability.
7. Require an incomplete fixture to produce actionable `NOT_RUNNABLE`.
8. Manually inspect generated test models and code for completeness, assertions, data quality, isolation, and hallucinations.
9. Require a fresh Sol review after implementation lanes finish.

## Acceptance criteria

The pack is complete only when all of the following are proven:

- the canonical contract and every schema validate;
- every core `SKILL.md` passes structural and projection checks;
- all deterministic-tool regression tests pass;
- Java and Python full-path fixture runs pass with real execution evidence;
- the failure fixture produces `NOT_RUNNABLE` rather than a false positive;
- SDD checking finds no missing requirement, orphan case, orphan method, or duplicate mapping;
- manual tests cover positive, negative, boundary, authorization/security, and applicable concurrency/idempotency/observability behavior without invented facts;
- automated tests follow project conventions and contain meaningful assertions;
- persistent test artifacts are confined to `docs/to_do/`;
- fresh-agent forward tests succeed without hidden context;
- the completion audit maps every requirement to authoritative evidence;
- fresh Sol review has no unresolved critical finding.

No subset of passing unit tests is sufficient to claim completion.

## Delivery order

1. Stabilize the canonical contract and deterministic runtime.
2. Synchronize concise core skills and schemas.
3. Implement SDD artifact and trace validation.
4. Add packaging, dependency, and optional adapter documentation.
5. Run Java, Python, and failure-fixture forward tests.
6. Inspect generated artifacts and fix semantic defects.
7. Run independent forward tests and fresh Sol review.
8. Perform a requirement-by-requirement completion audit.

This order keeps language-support claims evidence-based and prevents documentation from outrunning executable behavior.
