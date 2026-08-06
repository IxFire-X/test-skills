# Portable Testing Skills Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a host-neutral testing-skill pack with optional adapters, real Java/Python execution, complete SDD traceability, and evidence-backed acceptance.

**Architecture:** Execute three separately reviewable plans in dependency order. The portable runtime establishes machine contracts and gates; the skills/adapters plan projects those interfaces into concise agent instructions; the E2E plan proves the combined system on real fixtures and inspects its artifacts.

**Tech Stack:** Python 3.10+, JSON Schema Draft 2020-12, PyYAML, pytest, Java 17/JUnit 5/Maven, Python/pytest/Django, Markdown skills.

## Global Constraints

- `contracts/pipeline.json` is the only normative pipeline definition.
- Persistent test artifacts and isolated E2E workspaces live only under `docs/to_do/`.
- Scanner behavior is read-only unless a write flag is explicit.
- `NOT_RUNNABLE` never counts as acceptance.
- Java/JUnit 5 and Python/pytest are the required end-to-end baseline.
- TypeScript and Go remain experimental until real generation, review, and execution evidence exists.
- The core works without a Codex plugin; adapters cannot redefine core semantics.
- Preserve unrelated user files and review the current dirty worktree before every task.

---

## Plan sequence

### Plan 1: Portable core/runtime

Implement and verify the contract, dependency bootstrap, deterministic tools, schemas, projection generation, and SDD trace checker.

Document: `docs/superpowers/plans/2026-08-06-portable-core-runtime.md`

Acceptance interface exported to Plan 2:

```text
contracts/pipeline.json
schemas/*.schema.json
python tools/contract_check.py --root <skill-pack> --full
python tools/validate_artifact.py <schema> <artifact>
python tools/trace_check.py <trace-document> --require-execution
python tools/scan_project.py --project <root> --target <path>
python tools/run_tests.py --project <root> --language <java|python>
```

### Plan 2: Portable skills and adapters

Move skill packages to canonical ASCII paths, align all instructions/templates with the runtime contract, and add optional host adapters.

Document: `docs/superpowers/plans/2026-08-06-portable-skills-adapters.md`

Acceptance interface exported to Plan 3:

```text
skills/context-marker/SKILL.md
skills/tc-generator/SKILL.md
skills/tc-reviewer/SKILL.md
skills/tc-to-autotest/SKILL.md
skills/autotest-reviewer/SKILL.md
skills/orchestrate/SKILL.md
adapters/codex/
```

### Plan 3: E2E and completion evidence

Forward-test the skills with fresh agents on Java and Python fixtures, verify an incomplete fixture, inspect all artifacts, run independent review, and build the completion audit.

Document: `docs/superpowers/plans/2026-08-06-e2e-acceptance.md`

## Execution policy

- Finish and review Plan 1 before Plan 2 consumes its interfaces.
- Finish and review Plan 2 before generating final E2E artifacts.
- Use a fresh implementation agent for each task and a fresh review context at every task gate.
- Reuse an implementer only for corrections to its own task.
- Commit only files owned by the completed task; never sweep unrelated dirty-worktree changes into a commit.
- Run the complete regression suite after every plan and before final acceptance.

## Final evidence set

The completed work must include:

```text
docs/to_do/e2e/java/
docs/to_do/e2e/python/
docs/to_do/e2e/not-runnable/
docs/to_do/forward-tests/
docs/to_do/artifact-review.md
docs/to_do/completion-audit.md
```

The completion audit maps every design-spec acceptance criterion to a file, command output, or independently reviewed result. Missing or indirect evidence leaves the goal incomplete.
