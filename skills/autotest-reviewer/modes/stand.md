# Stand mode — review of autotests for a stand

Read only when the `read` has `stand`. BLOCKING when a stand test:
- reads the address otherwise than from the variable `stand.service_url_env`, or uses MockMvc, SpringBootTest or application classes;
- lacks `@EnabledIfEnvironmentVariable(named = "<NAME>", matches = ".+")` on its class (pytest: `skipif` on the variable).
