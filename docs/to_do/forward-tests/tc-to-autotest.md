# Forward-проверка tc-to-autotest

Режим evidence: две исполнившиеся реальные цепочки и PocketBase fallback; новой model repetition нет. Canonical `SKILL.md` SHA-256: `b7d6ef0d7c379f8a36c4fb8ff6d4b0eab2a67581fc26b3dd6956e64114550a7a`.

## Наблюдения

- Позитив Java: project-native `@WebMvcTest + MockMvc`, 7 методов, targeted 7/7 и full 31/31.
- Позитив Python: project-native `InvenTreeAPITestCase`, fixtures и source-confirmed role, 6/6; paired regression 17/17.
- Давление: PocketBase Go output не скопировал fixture token/password и честно отложил compilation/execution при отсутствии `go.exe/gofmt.exe`. Java attempt-01 и Python attempt-02 ранее выявили template/base-class и permission gaps; исправленные правила подтверждены текущими outputs.

## Четыре gate

| Gate | Результат | Evidence |
|---|---|---|
| Правильный trigger | `PASS` | Вход — принятые ТК и project context; выходы имеют `stage=tc-to-autotest` |
| Точные артефакты | `PASS` | Java/Python automation JSON, generated source, trace и receipts hash-bound в acceptance indexes |
| Правильный stop/fallback | `PASS` | При отсутствии Go toolchain нет compile/run claim; warning сохранён |
| Нет выдуманного evidence | `PASS` | Java использует существующий MockMvc pattern; Python переносит подтверждённые fixtures/role; secrets отсутствуют |

Model-evaluator scaffold этой кампании формально pending. По принятому маршруту его заменяют два независимых real-project execution результата; это практическое, а не универсальное межмодельное доказательство.

Вердикт: `PASS_REAL_CHAIN`.
