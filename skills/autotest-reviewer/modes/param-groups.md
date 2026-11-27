# Parameter groups — review of the automation

Группа `param_group` (Java): один `@ParameterizedTest(name = "[{index}] {0}")`, строки-литералы в том же классе с case ID первым аргументом по display_order группы; `submit` генератора проверил это сам (`RUNNER_PARAM_ROWS`, `RUNNER_PARAMS_UNAVAILABLE`).
