# Flask chain-01 / attempt-01

Итог: **PASS с одной pre-execution коррекцией reviewer false-pass**.

Полная цепочка `context-marker → tc-generator → tc-reviewer → tc-to-autotest → autotest-reviewer → execution` выполнена на Flask `8b4fd5d18611e63080b826ecfefe8659dba7d2e4`.

- Выделено 8 source-backed JSON request/response требований.
- Сгенерировано 8 тест-кейсов и один отдельный `tests/test_json_real_chain.py` с 8 pytest-методами.
- Все 5 pipeline JSON-артефактов прошли свои схемы.
- Адресный прогон: 8/8.
- Полный pytest: 499/499; baseline до генерации — 491/491; разница — ровно 8 новых тестов.
- Project boundary: production, существующие тесты, `pyproject.toml`, `uv.lock` и зависимости репозитория не менялись. Единственный project diff — новый test-файл; venv находится вне проекта.

Перед генерацией кода parent semantic review обнаружил, что `TC-0002` одновременно говорил о прямом возврате scalar `request.get_json()` и ожидал JSON response. Прямой scalar return во Flask создаёт текстовый response. Precondition уточнён до source-compatible `jsonify(request.get_json())`; требование, parsed value и приложение не менялись. Исходный SHA и точное before/after сохранены в `03-parent-semantic-correction.json`.

Вывод по скиллам: генерация дала исполнимые и трассируемые 8→8 тесты; `tc-reviewer` нужно усилить общей проверкой «заявленный harness способен произвести заявленный response oracle». Это переносимая, а не Flask-специфичная поправка.
