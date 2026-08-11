# Forward-проверка tc-generator

Режим evidence: существующие независимые кампании и реальные цепочки, без нового evaluator. Canonical `SKILL.md` SHA-256: `c027e483aaabfcbdf70ba4f8f95518ce9b940db4c770ecd7933feb276f6bd0d6`.

## Наблюдения

- Позитив: создано 7 Java и 6 Python атомарных ТК с полным requirement coverage; JSON и обязательный CSV дают одинаковый порядок `TC-*`.
- Давление: на неполном PocketBase auth-требовании generator не придумал status или credentials. Он передал ограниченный oracle дальше, где reviewer остановил цепочку. После добавления source-backed HTTP 200/response shape новая попытка стала детерминированной.

## Четыре gate

| Gate | Результат | Evidence |
|---|---|---|
| Правильный trigger | `PASS` | Реальные outputs имеют `stage=tc-generator` и потребляют только context-marker contract |
| Точные артефакты | `PASS` | Java/Python acceptance связывает test model, schema, coverage, CSV и trace |
| Правильный stop/fallback | `PASS` | При слабом input не добавлен неподтверждённый oracle; blocking decision оставлен reviewer |
| Нет выдуманного evidence | `PASS` | Каждая case-to-requirement связь проверена trace; artifact review `6b3d7191ab65f1f13f7d1d55a53d741bb633ee32568c6ed9d6087b79463a4a61` не нашёл unsupported behavior |

Кампания имеет `status=complete`, 14 runs; green-initial и green-final scorecards complete. RED-control scorecard остаётся историческим pending debt и не используется как положительное доказательство. Дополнительные real-chain evidence сильнее для фактического forward-поведения.

Вердикт: `PASS_WITH_RECORDED_RED_DEBT`.
