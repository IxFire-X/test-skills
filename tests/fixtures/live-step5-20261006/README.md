# Живые прогоны step5-java-demo, 2026-10-06

Копия только для чтения из `D:\AI-Projects\Claude outputs\step5-java-demo-runs-20261006\project`.

- `project/` — проект без `.git` и `.pilot-runs`, байт в байт (`-text` в `.gitattributes`).
- `runs/<run_id>/driver/` — `config.json`, `tasks/` и `outputs/` драйвера: настоящие ответы
  context-marker, генератора, ревьюеров частей и автоматизации.
- `runs/<run_id>/review-state/<attempt>/` — срез, план, границы частей и агрегат ревью.
- `runs/<run_id>/reviewer-session-ledgers/<attempt>/<key>.json.gz` — последний ledger ревьюера.
- `runs/<run_id>/events.jsonl.gz` — журнал событий (метки времени для замеров).

Все JSON сжаты gzip: план ревью весит около 1,4 МБ, а урезать его нельзя, потому что
ответы привязаны к его дайджесту. Чтение и проигрывание: `tests/live_step5.py`.

| Прогон | Профиль | Итог |
| --- | --- | --- |
| `2c10d733…` | cases-only | ACCEPTED |
| `3e852e76…` | local-pilot | одна правка `management.components` → 66 проверочных частей |
| `9340016c…` | local-pilot | PASS, 12/12, ревью без правок |
| `d1834358…` | local-pilot | UNCHECKED → blocked → `DRIVER_FAILURE` |
