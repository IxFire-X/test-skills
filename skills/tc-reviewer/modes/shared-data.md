# Shared data — review of cases that run where other people's and demo records live

Read only when the module's data are shared (`shared_data` in its `.skillsrc`; shared by default).
- BLOCKING: a case that changes, deletes or counts records it did not create; checks a total before and after; uses a constant id; gives a
  unique name without `<метка прогона>`; creates records without a last step «Уборка: …» that deletes them or says in words why they
  cannot be deleted.
- BLOCKING `SHARED_CLEANUP_LINK`: «Уборка» deletes a record that refers to a record the case did not create, and neither (a) the case
  created that record itself with `<метка прогона>`, nor (b) the code in the context shows no cascade to it — the ORM (`cascade`) and the
  schema (`ON DELETE CASCADE`) both; no code is no proof. Also BLOCKING: «Уборка» that expects a status of `DELETE`, not the record gone.
