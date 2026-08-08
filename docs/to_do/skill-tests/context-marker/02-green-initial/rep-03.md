# Initial green evaluator — repetition 03

## Objective

Apply the frozen original context-marker snapshot to the supplied raw order-change input.

## Observation

The frozen snapshot specifies XML wrapping of raw content, while the application instruction requires a schema-bound JSON artifact containing requirement identifiers, source splits, provenance, and unsupported-policy warnings. The JSON artifact therefore applies the explicit application instruction using only the supplied input, with quoted facts retained verbatim and each absent policy reported as unsupported.
