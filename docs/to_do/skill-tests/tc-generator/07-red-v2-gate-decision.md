# RED-v2 gate decision

Fresh Sol verdict: `change-and-green`.

The successful diagnostic baseline is `01-red-control-v2/rep-01`, `rep-02`, and `rep-03`. `rep-04` is immutable protocol-invalid and unscored; its archived/reserved output SHA-256 is `632723f0a197f4403677a5ef83a4933c3311019f5f937426718b23eaeba4b2f8`.

Root cause: the controller abbreviated verbatim canonical JSON and lost the provenance array type. The schema validator exited 1 as the last command; no semantic command ran. Therefore there is no `rep-05`, retry, repair, or RED-v3.

The user preference is fewer RED runs and more GREEN coverage. The previous tc-generator-only five-success RED implementation gate is superseded: preserve the RED scorecard as pending, empty, and non-comparable; implement the canonical skill next; then begin `02-green-initial/rep-01` and complete five initial-GREEN runs, pressure, and five FINAL-GREEN runs. Other campaign scorecard rules remain unchanged.
