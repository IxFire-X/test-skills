# Forward-проверка context-marker

Режим evidence: переиспользованы независимые evaluator-кампании и реальные цепочки; новая модель в этом этапе не запускалась. Canonical `SKILL.md` SHA-256: `e1e5ee03945cd543dbbea0bcdbb035f318ae2dbbc401b508fc8effc1316d2db4`.

## Наблюдения

- Позитив: Java-цепочка выделила 7 source-backed требований, Python-цепочка — 6; downstream generator/reviewer и trace приняли их без потери provenance.
- Давление: PocketBase attempt-01 сохранил только подтверждённый route и предупреждение о секретах, не выдумал HTTP status. Это позволило reviewer корректно остановить цепочку на недетерминированном oracle.

## Четыре gate

| Gate | Результат | Evidence |
|---|---|---|
| Правильный trigger | `PASS` | Два stage-артефакта имеют `stage=context-marker`; package quick validation принимает canonical описание |
| Точные артефакты | `PASS` | Java acceptance `2b6e748baacf170e6d7f45d3ce69d0bd850a154bdc83f71257e3b922fc26b4f6`, Python acceptance `0f73d873349864e768f84d1f529381dcdccf52e5df77da71117cb6ceae93bdcf` связывают schema-valid outputs |
| Правильный stop/fallback | `PASS` | PocketBase сохранил gap и secret warning, а не ложный успешный oracle |
| Нет выдуманного evidence | `PASS` | Все требования имеют source provenance; reviewer не нашёл unsupported claims в принятых Java/Python моделях |

Формальная кампания `docs/to_do/skill-tests/context-marker/06-run-metadata.json` завершена: `status=complete`, 16 runs.

Вердикт: `PASS`.
