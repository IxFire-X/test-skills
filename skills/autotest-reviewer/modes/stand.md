# Stand mode — review of autotests for a stand

Read only when the `read` has `stand`. BLOCKING when a stand test:
- reads the address otherwise than from the variable `stand.service_url_env`, or uses MockMvc, SpringBootTest or application classes;
- lacks `@EnabledIfEnvironmentVariable(named = "<NAME>", matches = ".+")` on its class (pytest: `skipif` on the variable).
- leaves records it created without a cleanup that runs after a failed assertion too (unless the case's «Уборка:» says they cannot be
  deleted), or changes, deletes or counts records it did not create, or checks a total before and after;
- uses a constant id, or a name without the run's suffix where the case has `<метка прогона>`.
- `SHARED_CLEANUP_LINK`: deletes a record the case's «Уборка» does not name; deletes a record that refers to one the test did not create
  when the code shows a cascade (ORM `cascade`, schema `ON DELETE CASCADE`) or there is no code; fails on the status of `DELETE`.
