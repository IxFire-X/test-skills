---
name: orchestrate
description: Use when запрос содержит «создай тест-кейсы», «сгенерируй автотесты», «запусти тестовый пайплайн», «создай тесты», «инициализируй проект», «quickstart» или «настрой проект» и требуется координация нескольких канонических testing skills.
---

# Оркестрация тестового пайплайна

## Принцип

Координируй только канонический маршрут из `contracts/pipeline.json`. Считай JSON-артефакты машинной истиной, runner — единственным доказательством исполнения, а Markdown/CSV/generated source — только сопутствующими материалами.

## Обязательная подготовка

1. Полностью прочитай `contracts/pipeline.json` и [контракт оркестрации](references/orchestration-contract.md).
2. Разрешай пути скиллов только через `skill_files`; не используй alias, legacy-каталоги или plugin-копии.
3. Прочитай только нужные текущему этапу `SKILL.md`, его declared inputs и schema.
4. Осмотри проект read-only. Проектные файлы, конфигурацию, зависимости и production code не меняй ради тестов.
5. Выбери отдельный каталог артефактов в `docs/to_do/` и изолированное место для generated test companions.

## Канонический маршрут

Выполняй этапы строго последовательно:

1. `context-marker`
2. `tc-generator`
3. lossless JSON → CSV export
4. `tc-reviewer`
5. `tc-to-autotest`
6. `autotest-reviewer`
7. `tools/run_tests.py`
8. `tools/build_trace_document.py`
9. `tools/trace_check.py --require-execution`
10. `orchestrator-output.json` + повторный `trace_check.py --orchestrator-artifact`

После каждого JSON-этапа сначала запускай `tools/validate_artifact.py` с его канонической schema. При nonzero exit остановись до следующего этапа.

После валидного `tc-generator-output.json` всегда создай соседний CSV через `skills/tc-generator/scripts/export_test_cases_csv.py`, затем проверь его `--verify-only`. Передавай дальше JSON, не CSV.

## Передача и изоляция

- Передавай этапу только артефакты из его `accepts`/предыдущего `forwards` в pipeline contract и необходимые source-backed факты проекта.
- Не передавай внутренние рассуждения, ожидаемый ответ, evaluator scorecards или выводы прежних агентов.
- Сохраняй provenance, requirement IDs, test-case IDs, setup, role/permission/auth facts, action, data и oracle без ослабления.
- Считай generated source companion-артефактом. Машинной истиной остаётся соответствующий JSON с путями и SHA-256.
- Никогда не сохраняй credentials, tokens, cookies или другие secrets в prompt, JSON, CSV, generated source либо evidence.

## Решения на воротах

| Ворота | Продолжить | Остановиться |
|---|---|---|
| schema | validator exit 0 | любой nonzero |
| `tc-reviewer` | `ПРИНЯТО` с original или `AUTO_FIX_APPLIED` с corrected cases | `ТРЕБУЕТ ДОРАБОТКИ`, неизвестный verdict |
| `autotest-reviewer` | `ПРИНЯТО` или контрактно разрешённый corrected output | `ТРЕБУЕТ ДОРАБОТКИ`, неизвестный verdict |
| runner | authoritative `PASS`, exit 0 | `FAIL`, `NOT_RUNNABLE`, nonzero |
| trace | `PASS`, native exit 0, exact orchestrator cross-check | `FAIL`, mismatch, nonzero |

Для Java/Python `NOT_RUNNABLE` не является успехом. Для experimental capability сохрани честный schema-valid `NOT_RUNNABLE`, но не объявляй цепочку принятой.

## Ошибки и новые попытки

- Не ремонтируй и не перезаписывай неудачный логический артефакт.
- Сохрани его, validation result, literal argv/cwd/exit и root cause.
- Запускай новую попытку только после фактического изменения входа, скилла или controller contract; старую оставляй immutable и unscored.
- Повтор того же command допустим лишь при host/transport failure до появления stage output; запиши обе попытки.
- Если стек, setup, role/permission/auth или observable oracle не подтверждены источниками, верни rework вместо догадки.

## Финальное принятие

Объявляй pipeline `PASS` только когда одновременно:

- все stage JSON прошли свои schemas;
- reviewer выбрал original или явно corrected authority;
- autotest review принят;
- `run_tests.py` вернул authoritative `PASS` с method-level evidence;
- trace document прошёл `--require-execution`;
- `orchestrator-output.json` прошёл schema и exact trace cross-check;
- production/config/dependency files проекта не менялись.

Человеческий отчёт разрешён, но он не заменяет ни один JSON receipt. Для минимальных проверяемых форм смотри `assets/orchestration-fixtures/`.
