# Pipeline 6 change-scope contract

`tools.feature_flow.advance_feature_flow` is the only common semantic-prefix facade. It returns one immutable action with `kind`, `artifact`, `record_path`, and `diagnostics`; execute only that action, persist only at its exact create-only path, pass it back as the record, and repeat.

The initial accepted run is a complete immutable `FULL` baseline. A later compatible committed change is `CHANGE_SET`; absent, stale, foreign, or fingerprint-incompatible baseline evidence falls back to `FULL`. There is no user-selected count, mode, scope, shard, retry, or controller setting. Portable execution is `SEQUENTIAL`.

Both scope audits and both batch audits are mandatory. Technical inventory is mechanical impact evidence only and never creates a behavior requirement, a changed semantic claim, or a canonical case. `COMPLETE` is the closed `READY_FOR_PIPELINE_TAIL` handoff; the technical classifier/reviewer gate, terminal receipt, and baseline advancement are later tail work.
