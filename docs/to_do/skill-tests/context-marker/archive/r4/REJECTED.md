# Rejected unstable final GREEN batch r4

This entire `04-green-final` batch is excluded from active evidence and scoring.
All five schema validator runs exited `0` and sequencing was clean, but the
reviewer's semantic matrix is not a passing batch: `rep-01 F/P/P/F`,
`rep-02 F/P/F/F`, `rep-03 P/P/F/F`, `rep-04 P/P/F/F`, and
`rep-05 F/P/F/F`.

The exact causes are missing fact identity in `rep-01`, `rep-02`, and
`rep-05`; an omitted or misclassified endpoint; campaign-wide identifier
instability; and run protocols that retained only command IDs and exit codes,
not literal argv, making the required exact absolute command unauditable.

No evaluator was replaced or rerun while archiving this batch. No repetition is
carried forward. The files under `skill-snapshot/` are copies—not moved
evidence—of the tested current skill and local contract.
