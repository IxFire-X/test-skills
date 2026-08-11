# InvenTree chain-01 / attempt-02

Первые пять стадий дошли до execution gate:

- context-marker: PASS, 6 требований, schema-valid;
- tc-generator: PASS, 6 атомарных кейсов, schema-valid;
- tc-reviewer: `ПРИНЯТО`, schema-valid;
- tc-to-autotest: создал 6 project-native Django/DRF методов, schema-valid;
- autotest-reviewer: `ПРИНЯТО`, schema-valid;
- targeted execution: FAIL, 0/6, все ответы HTTP 403.

Причина: в generated class перенесены `InvenTreeAPITestCase` и fixtures, но не перенесён обязательный permission setup. Соседние проектные тесты подтверждают минимальную роль `part_category.view`.

Это реальный portability gap генератора и false-pass ревьюера. Attempt-03 меняет только permission setup, затем повторяет code review и execution.
