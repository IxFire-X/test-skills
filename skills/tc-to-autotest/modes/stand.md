# Stand mode — autotests against a raised stand

Read only when the `read` has `stand`: the module's tests call the external API of the application at the address from the environment
variable `stand.service_url_env`.
- The address only from `System.getenv("<NAME>")` (Python: `os.environ["<NAME>"]`); no MockMvc, no SpringBootTest, no application
  classes. With `stand.tests_only` the application lives in another directory: its classes are not on the module's test classpath.
- Every stand test class carries `@EnabledIfEnvironmentVariable(named = "<NAME>", matches = ".+")` (JUnit 5): without the variable JUnit
  skips the class and its reason names the variable. pytest: `pytest.mark.skipif(not os.environ.get("<NAME>"), reason="<NAME> is not set")`.
- The HTTP client the build already has (RestAssured), else `java.net.http.HttpClient`; no new dependency.
- Shared stand: `<метка прогона>` of a case is one suffix of letters per run of the class, the same in every name of that run — the only
  value that is not deterministic. Ids only from responses. The cleanup runs in `finally` / `@AfterEach`, also after a failed assertion,
  and deletes only what the test created; no check of a total before and after.
