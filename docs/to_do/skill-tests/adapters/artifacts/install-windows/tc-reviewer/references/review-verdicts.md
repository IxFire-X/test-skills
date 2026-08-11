# Tc-reviewer verdict and correction contract

## Decision table

| Highest defect class | Verdict | Findings | Corrections | Corrected cases |
|---|---|---|---|---|
| none | `ПРИНЯТО` | empty | empty | empty |
| mechanical only | `AUTO_FIX_APPLIED` | one or more `WARNING` or `INFO` | one or more | complete changed cases only |
| any blocking defect | `ТРЕБУЕТ ДОРАБОТКИ` | includes `BLOCKING` | empty | empty |

Blocking takes precedence over every mechanical issue. Do not partially auto-fix a review that also contains a blocking defect.

## Mechanical corrections

A correction is mechanical only when all of these are true:

1. The original value is visible in the supplied test case.
2. The replacement is visible or uniquely implied by another value in the same supplied envelope.
3. The replacement does not choose product behavior.
4. No role, endpoint, data value, state transition, status, or error code changes.
5. A reviewer can describe the change as one exact textual or formatting operation.

Typical mechanical defects:

- an unambiguous spelling error such as `sesion` where the same artifact consistently uses `session`;
- punctuation or whitespace that does not change meaning;
- a mechanically malformed label whose exact normalized form is already present in the input.

Not mechanical:

- filling `TBD`, `TODO`, `unknown`, or an empty semantic result;
- changing an expected status or response code;
- replacing an unsupported role with a supported role;
- choosing an endpoint, field, value, precondition, or state transition;
- resolving contradictory requirements;
- adding missing coverage or inventing a requirement association.

## Blocking findings

Use `BLOCKING` when the case cannot be executed or trusted without a product decision. Recommended stable codes include:

- `EXPECTED_RESULT_MISSING`: step or outcome is a placeholder or lacks an observable result;
- `UNSUPPORTED_AUTHORIZATION`: the case claims a role or permission absent from the supplied policy;
- `UNSUPPORTED_BEHAVIOR`: the case asserts an endpoint, status, field, or side effect absent from requirements;
- `DANGLING_REQUIREMENT_ID`: a case references an unknown requirement;
- `COVERAGE_MISMATCH`: case links and reverse coverage disagree;
- `CONTRADICTORY_ORACLE`: step results and final outcome conflict;
- `NONDETERMINISTIC_ORACLE`: success/failure is asserted without an observable condition.
- `HARNESS_ORACLE_MISMATCH`: the stated setup/action/harness cannot produce the claimed media type, body shape, status, or state.

Codes are descriptive, not a license to infer missing behavior. The finding message explains what is unsupported and what source information is needed.

## Evidence and identity

`reviewed_test_case_ids` contains every input test-case ID exactly once and preserves input order.

Every `related_ids` entry must be a requirement ID or test-case ID from the same input. Evidence should use field pointers such as:

- `TC-0042.steps[0].expected_result=TBD`
- `TC-0042.preconditions[0]=Authenticated as warehouse_operator`
- `REQ-AUTH-001.text permits only sales_manager`

Do not use fabricated log lines, database observations, HTTP responses, or execution claims as evidence.

## Corrected-case integrity

For `AUTO_FIX_APPLIED`, copy the complete changed case and preserve:

- `id` and `requirement_ids`;
- categories and priority;
- preconditions and test data;
- every unchanged step field;
- the unchanged expected outcome.

Only the exact field cited by the correction may differ. Do not include unchanged cases in `corrected_test_cases`.

## Examples

### Accept

A case matches its requirement, uses existing IDs, and contains an observable expected result. Return `ПРИНЯТО` with empty findings, corrections, and corrected cases.

### Mechanical correction

The title says `Delete active sesion`, while the requirement and all other fields use `session`. Return `AUTO_FIX_APPLIED`, cite the title, replace only `sesion` with `session`, and include the complete corrected case.

### Missing result

A step expected result and final outcome are `TBD`. Return `ТРЕБУЕТ ДОРАБОТКИ` with `EXPECTED_RESULT_MISSING`. Do not infer an HTTP status or success response.

### Unsupported authorization

A case authenticates as `warehouse_operator`, but the only supplied approval policy names `sales_manager`. Return `ТРЕБУЕТ ДОРАБОТКИ` with `UNSUPPORTED_AUTHORIZATION`. Do not replace the role or predict the denied response.
