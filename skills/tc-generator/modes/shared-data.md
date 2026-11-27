# Shared data — the cases run where other people's and demo records live

Read only when the module's data are shared (`shared_data` in its `.skillsrc`; shared by default).
- A record the case creates and whose name must be unique carries `<метка прогона>` in that name (one suffix of letters per run).
- Every id comes from a response (`step_output`); a missing id is a value the list response shows absent, never a constant.
- Never change, delete or count a record the case did not create; no check of a total before and after (a parallel client changes it).
- A record the cleanup deletes refers to no record the case did not create (a dictionary, a demo record), unless (a) the case creates that
  record itself, with `<метка прогона>`, and deletes it last, or (b) the code in the context shows no cascade from the deleted record to it:
  neither in the ORM (`cascade` ALL/REMOVE) nor in the schema (`ON DELETE CASCADE`). No code is no proof.
- Without (a) or (b) the last step «Уборка: …» does not delete that record: it says in words what is left, what it refers to and why.
- «Уборка» deletes what the case created, leaves first, by the ids from the responses, and expects the record gone (`GET` by id: 404),
  not a status of `DELETE`; after a delete under (b), `GET` of the referred record by its id: 200. A record the system cannot delete — say so.
- A cascade to a record of others seen in the code is a remark for the analyst, one sentence.
