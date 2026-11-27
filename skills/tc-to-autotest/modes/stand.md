# Stand mode — autotests against a raised stand

Read only when the `read` has `stand`: the module's tests call the external API of the application at the address from the environment
variable `stand.service_url_env`.
- The address only from `System.getenv("<NAME>")` (Python: `os.environ["<NAME>"]`); no MockMvc, no SpringBootTest, no application
  classes. With `stand.tests_only` the application lives in another directory: its classes are not on the module's test classpath.
- Every stand test class carries `@EnabledIfEnvironmentVariable(named = "<NAME>", matches = ".+")` (JUnit 5): without the variable JUnit
  skips the class and its reason names the variable. pytest: `pytest.mark.skipif(not os.environ.get("<NAME>"), reason="<NAME> is not set")`.
- The HTTP client the build already has (RestAssured), else `java.net.http.HttpClient`; no new dependency.
