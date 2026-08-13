# Domain glossary

## Managed Scenario

A user-, client-, or system-observable behavior that is independently executable and has a defined terminal outcome. A managed scenario may become one canonical test case and one Zephyr item.

## Technical Test Evidence

An existing runnable test symbol found in the project. It is evidence about the implementation, not a managed scenario and not a reason by itself to create a canonical test case.

## Scenario Key

The stable semantic boundary used to decide whether behavior belongs in the same canonical test case: setup or role, initial state, input partition or branch condition, primary action or cohesive dependent action chain, and terminal outcome.

## Test Scope

The architectural reach of a technical test symbol. The closed values are `unit`, `integration`, `e2e`, and `unknown`.

## Implementation Origin

Whether an executable test symbol existed in the pre-generation technical-evidence artifact (`existing`) or was created by the automation stage (`generated`). Origin is derived from artifact ownership, not chosen by the language model.

## Execution Mode

The derived case-level intent `manual`, `automated`, or `mixed`, computed only from canonical step `manual_only` values. It does not claim that automation exists or passed.

## Automation Coverage

The derived state `not_applicable`, `complete`, or `blocked`. It records whether all automation-intended operations and assertions have authoritative implementation relations; an incomplete ready relation set is invalid rather than a fourth state.

## Readiness

The derived state `ready` or `blocked`. Readiness is independent of execution mode and reflects unresolved blockers in the canonical document.

## Authoritative Coverage

Coverage that may satisfy a managed scenario's operation or assertion relation. Only independently reviewed and explicitly related `integration` or `e2e` symbols may be authoritative, whether they are existing or generated. Unit and unknown evidence remain non-authoritative.
