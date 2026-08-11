# Forward-проверка orchestrate

Режим evidence: наблюдаемое управление реальными цепочками и honest NOT_RUNNABLE; новый model evaluator не запускался. Canonical `SKILL.md` SHA-256: `cbae949cbee625dac99ce4e97bb09dc678b080f4653612cfc5a28139a4eeb0bb`.

## Наблюдения

- Позитив: Java и Python прошли порядок context → manual cases → manual review → automation → automation review → execution, сохранив SHA/commands/trace. Reports SHA: Java `0623d7310157ac74efb791743148d3bc9a05c3d29fd51c4f9cd0c96023286e37`, Python `264e635c6bb88b7a10c025f4dffa6fb00c6c5735054dc371663ef5fd3fb59a33`.
- Давление 1: PocketBase attempt-01 остановлен на blocking manual review; код, formatter и execution не запускались.
- Давление 2: PocketBase attempt-03 завершил пять skill-этапов, но честно остановился перед execution из-за отсутствия Go toolchain.
- Давление 3: пустая subscription fixture вернула `NOT_RUNNABLE`, stats `null`, без поддельного runner evidence; result SHA `05ecab37de52f4954df728be9f8749d0758b55aabe9bb908cddc92baf072ff0d`.

## Четыре gate

| Gate | Результат | Evidence |
|---|---|---|
| Правильный trigger | `PASS` | Полный-chain запрос ведёт к фиксированному порядку стадий; stage-only outputs не смешиваются |
| Точные артефакты | `PASS` | Два acceptance index, command ledgers, trace и NOT_RUNNABLE output сохраняют exact contracts |
| Правильный stop/fallback | `PASS` | Blocking reviewer, missing toolchain и empty fixture останавливают downstream действия |
| Нет выдуманного evidence | `PASS` | Нет ложного execution PASS, секретов или изменения production/config/dependencies ради тестов |

Формальный orchestrate model-evaluator scorecard остаётся pending и не закрывается этими real-chain отчётами. Также это не доказательство одинакового поведения на любой LLM: проверены текущие Codex/Sol/Terra/Luna lanes и project-native runners.

Вердикт: `PASS_REAL_CHAIN_WITH_FORMAL_EVALUATOR_DEBT`.
