# Shared data — the cases run where other people's and demo records live

Read only when the module's data are shared (`shared_data` in its `.skillsrc`).
- A record the case creates and whose name must be unique carries `<метка прогона>` in that name (one suffix of letters per run).
- Every id comes from a response (`step_output`); a missing id is a value the list response shows absent, never a constant.
- Never change, delete or count a record the case did not create; no check of a total before and after (a parallel client changes it).
- The last step «Уборка: …» deletes what the case created, by the ids from the responses. When the system cannot delete it, that step says
  so in words — the explicit refusal of the cleanup; the tester decides.
