# Context-marker campaign protocol

This pending campaign follows commit 6375a05: five RED controls, five initial GREEN repetitions, one adversarial pressure run, and five final GREEN repetitions. Every evaluator receives only artifacts/inputs/raw-content.json, records actual commands and UTC timing, and writes one schema-bound context-marker-output.json to its reserved output directory. The snapshot below is harness material only and is excluded from application inputs.

Validate every envelope with `tools/validate_artifact.py`, schema `schemas/context-marker-output.schema.json`, and its exact reserved output path: `artifacts/outputs/<phase>/<rep>/context-marker-output.json` (or `artifacts/outputs/03-pressure/pressure/context-marker-output.json` for pressure). The phase/repetition matrix is the exact list in `00-scenario.json`.
