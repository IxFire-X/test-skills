# InvenTree chain-01 / attempt-03

## Результат цепочки

- context-marker: reuse hash-bound attempt-02, PASS;
- tc-generator: reuse hash-bound attempt-02, 6 кейсов, PASS;
- tc-reviewer: reuse hash-bound attempt-02, `ПРИНЯТО`;
- tc-to-autotest: PASS после bounded permission fix;
- autotest-reviewer: `ПРИНЯТО` после проверки runtime setup seams;
- generated execution: 6/6 PASS;
- PartCategory regression: 17/17 PASS.

Generated file: `src/backend/InvenTree/part/test_real_chain.py`, SHA-256 `f6717f77aa59b1ef528ab93b7d00370b8b533feaca7f01440ad14865f9e85973`.

## Что нашла реальная цепочка

Attempt-02 получил 403 во всех шести тестах: базовый класс выполнял login, но generated class не получил endpoint-specific роль. Исправлены portable skills:

- `tc-to-autotest` теперь обязан переносить source-confirmed roles/permissions/fixtures/setup hooks/client initialization;
- `autotest-reviewer` теперь статически сверяет эти runtime setup seams и не считает одного имени base class достаточным.

Узкая регрессия skills: 5 passed.

## Полный backend suite

Полный официальный app list действительно выполнен: 1471 тест. При CI-подобных `INVENTREE_DEBUG=true` и `INVENTREE_PLUGINS_ENABLED=true` итог — 1433 passed, 11 skipped, 27 failed, 0 errors. Оставшиеся падения относятся к неполному локальному CI setup на Windows (Unix-path expectations, не собранные static/media assets, Python app alias и сохранённое DB state), не к generated class. Поэтому feature-chain принят, но project-wide green не заявляется.

Ruff отсутствует в существующем venv; зависимости не устанавливались.
