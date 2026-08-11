# Click chain-01 / attempt-01

Итог: **PASS**.

Полная цепочка выполнена на Click `9c4dfdaebe0e6b2aabc566eb81f6f10eb5cd6ea1` для `IntRange`: 8 source-backed требований → 8 принятых тест-кейсов → один файл `tests/test_int_range_real_chain.py` → 8 pytest/CliRunner-тестов.

- Все 5 pipeline JSON-артефактов schema-valid.
- Адресный прогон: 8/8.
- Полный набор: 1890 passed, 95 skipped, 31 000 deselected, 1 xfailed, 0 failed.
- Baseline: 1882 passed при тех же skipped/deselected/xfailed; разница — ровно 8 новых тестов.
- Project boundary соблюдён: implementation, существующие тесты, конфигурация, `pyproject.toml`, `uv.lock` и repo dependency state не менялись. Venv внешний; в проекте только один новый test-файл.

Вывод: переносимые скиллы сработали не только на REST/framework коде, но и на CLI type conversion — закрытые/открытые границы, ошибки и clamping сохранились до исполняемых оракулов без специальной адаптации Click.
