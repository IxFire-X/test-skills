# tc-generator v2 stale-lock recovery limit

Date: 2026-08-10

This addendum narrows one statement in the immutable, hash-pinned `10-finalization-v2-amendment.md`. It does not change the controller used for the completed campaign or any run evidence.

`finalize_campaign_v2.py` can truthfully recover a missing success attestation from a fully completed campaign only when it can acquire `.06-run-metadata.json.record.lock`. Catchable failures remove that lock in `finally`. An uncatchable process termination can leave the empty exclusive-create lock behind, so a later invocation will refuse before reaching completed-state recovery. Automatic stale-lock recovery is not claimed.

## Verified manual clearance

An operator may clear the exact lock only after all of these conditions are verified:

1. No `record_successful_run.py`, `finalize_campaign.py`, or `finalize_campaign_v2.py` process or native task is still operating on this campaign.
2. The exact lock path is `<campaign>/.06-run-metadata.json.record.lock`; no broader file pattern or directory is targeted.
3. `06-run-metadata.json` is already `complete`, the terminal scorecard is `complete`, the terminal protocol exists, and `artifacts/controller/finalization-v2-success.json` is absent. A `pending` or internally inconsistent campaign requires diagnosis, not lock removal or recovery.
4. The operator removes only that exact empty lock file, then invokes the pinned v2 controller once with the original absolute campaign and draft paths. The controller must revalidate the completed campaign, pinned amendment/refusal evidence, terminal protocol, scorecard, metadata, and hashes before creating an attestation with `attestation_recovered: true` and `completion_preexisted: true`.

The representative focused test creates a completed campaign with a missing success record and a crash-residue lock. It verifies the first recovery attempt refuses without writes, explicitly removes only the exact simulated stale lock, and verifies the next invocation creates the truthful recovery attestation. This is a manual recovery contract; it is not automatic stale-lock detection.
