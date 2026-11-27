# Stand mode — review of cases for a stand

Read only when the run's module tests against a stand (`service_url_env` in its `.skillsrc`).
- BLOCKING: an HTTP step whose address comes from another environment variable than the one of the stand.
- BLOCKING on a shared stand: a case that changes, deletes or counts records it did not create; checks a total before and after; uses a
  constant id; gives a unique name without `<метка прогона>`; creates records without a last step «Уборка: …» that deletes them or says
  in words why they cannot be deleted.
