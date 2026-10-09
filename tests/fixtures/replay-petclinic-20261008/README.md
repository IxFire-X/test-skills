# Повтор живых прогонов Petclinic 2026-10-08 (b и d)

Записи живых прогонов Petclinic для офлайн-тестов планировщика ревью (`tests/replay_petclinic.py`,
`tests/test_replay_petclinic.py`); план — `docs/superpowers/plans/2026-10-08-live-fixes.md`.

- Прогон d: run `0f779fd621bf43ed833bc5eab67d1fbc`, attempt `b14327e2…`, пакет `10ae352`.
  Прогон b: run `8fb318032d9b45a0ba822d81851cf41d`, attempt `d3416c8c…`, пакет `e02eb63`.
- `canonical-snapshot.json.gz`, `r1-snapshot.json.gz` — payload снимка ревью кейсов и автотестов прогона d,
  спецификация плана, бюджет, дайджест плана d, тексты основных и добавочных частей, как их выдал прогон.
- `canonical-answers.json.gz` — ответы модели на 47 частей ревью кейсов d (37 основных перенесены из b, часть 2 и 9
  добавочных — свежие d) и ответ b на часть 2 (`b_part_000002`: «сверка всех SREQ»).
- `r1-answers.json.gz` — ответы на 10 основных частей ревью автотестов d.
- `automation-output.json.gz` — принятый ответ `tc-to-autotest:r1` d.
- `analyst-report-g.json.gz` — `analyst-report.json` живого прогона g (2026-10-09, run `42951bc1…`, пакет `0a9d96f`) как он
  вышел: 47 вопросов с источниками — вход теста группировки вопросов по требованию (каталог результатов
  `D:\AI-Projects\live\petclinic-20261009g-results\`); путей проекта и адресов нет, `@` — только аннотации Java.

Выгружено `export_replay.py` (рядом) кодом копии пакета прогона d (`<проект>\.tools\test-skills`: новый код отклоняет старые планы); пути
проекта в записях не встречаются (проверено grep: нет путей `C:\Users`, `D:\AI-Projects`, адресов почты).
Детерминированный gzip (`mtime=0`). SHA-256:

```
9eac3c9b244c022f9a69e63d827ae7a35c8aae4c8fdcdb9b6800ef5d55748b79 *analyst-report-g.json.gz
9e568cb82dd866e0ba6763b6571b1c34297d266c583e9f95e445ea842561fa09 *automation-output.json.gz
61d60beff1db9b165a88ba5a338b73872f9754c59a8460cd70db713a7645844c *canonical-answers.json.gz
bb70971c71e1a3db780b9c2756cbd976a2d19adbc8b7497e709b9d6cc2c16c5b *canonical-snapshot.json.gz
10b76a98a8047e91f97ddf73476ef7c11076bb309873ada7dc86852acbf37cd0 *r1-answers.json.gz
fdcfbc03845b5719f0f941d6bfbfb213f69f393cf0d17088fcc759e2a406ca4a *r1-snapshot.json.gz
```
