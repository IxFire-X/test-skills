# Tc-reviewer initial-GREEN mixed v3 route decision

Fresh Sol accepted a bounded per-repetition route. The scenario's `green_initial_phase_by_repetition` is authoritative: `rep-01` and `rep-02` score only from `02-green-initial-v2`; `rep-03`, `rep-04`, and `rep-05` score only from `02-green-initial-v3`.

`02-green-initial-v2/rep-03` is protocol-invalid because its evaluator executed the recorded unauthorized ambient skill read. It is excluded from score and preserved at `artifacts/invalidated/02-green-initial-v2/rep-03/green-initial-v2-rep-03-unauthorized-skill-read/`. Its prompt, observation, invalidated protocol, and four output files are byte-bound archive evidence. It is not rerun, repaired, replaced, or reused.

The continuation does not create v2 `rep-04..05` or v3 `rep-01..02`. The only new paths are inert v3 report, output, and protocol scaffolds for `rep-03..05`. No evaluator, validator, or semantic checker is authorized by this routing decision. Every initial-GREEN phase uses the canonical application-prompt SHA-256 `c40e8330f28f8bec786dc5bcf93fbc51641d799bf78fe8a1de9b79665e5c9bfe`; old and v2 hashes remain preserved.
