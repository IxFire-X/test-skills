# Stand mode — autotests against a raised stand

Read only when the `read` has `stand`: the tests call the application's external API at the address in the variable `stand.service_url_env`.
- The address only from `System.getenv("<NAME>")` (Python: `os.environ["<NAME>"]`); no MockMvc, SpringBootTest or application classes
  (with `stand.tests_only` they are not on the module's test classpath).
- Every stand test class carries `@EnabledIfEnvironmentVariable(named = "<NAME>", matches = ".+")`: without the variable JUnit skips it and
  names it. pytest: `pytest.mark.skipif(not os.environ.get("<NAME>"), reason="<NAME> is not set")`.
- Shared stand: `<метка прогона>` is one suffix of letters per run of the class — the only value that is not deterministic. Ids only from
  responses; no check of a total before and after.
- The cleanup (`finally` / `@AfterEach`, also after a failed assertion) is the case's «Уборка» exactly: it deletes only the records named
  there, leaves first; where the case says it in words, no delete call. A record whose delete the code shows cascading to a record the test
  did not create (ORM `cascade`, schema `ON DELETE CASCADE`) is not deleted, whatever the case says.
- The cleanup expects the record gone (`GET`: 404); a failure of that is one line of output, not the test's failure. After a delete of a
  record that referred to another's, `GET` of that one: 200, else the test fails.
