# Бенчмарк моделей для ролей ревью test-skills

Части: canonical part-000010 (112292 Б текста части), canonical part-000028 (113249 Б текста части), canonical part-000001 (173503 Б текста части), r1 part-000004 (179024 Б текста части).

| Модель | Роль | Часть | Вход, Б | Принят | Попытка | Отказы | Время, с | Токены вх/вых | Б на токен | Находок (Sonnet) | Совпало | Доля |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sonnet-replay | tc-reviewer | part-000010 | 125603 | да | 1 | — | 0.2 | 33361/556 | 3.76 | 2 (2) | 2 | 1.0 |
| sonnet-replay | tc-reviewer | part-000028 | 126560 | да | 1 | — | 0.2 | 34548/253 | 3.66 | 1 (1) | 1 | 1.0 |
| sonnet-replay | tc-reviewer | part-000001 | 186814 | да | 1 | — | 0.2 | 44240/1190 | 4.22 | 3 (3) | 3 | 1.0 |
| sonnet-replay | autotest-reviewer | part-000004 | 194465 | да | 1 | — | 0.2 | 56658/10847 | 3.43 | 3 (3) | 3 | 1.0 |
| lazy-model | tc-reviewer | part-000010 | 125603 | да | 1 | — | 0.2 | 33361/278 | 3.76 | 0 (2) | 0 | 0.0 |
| lazy-model | tc-reviewer | part-000028 | 126560 | да | 1 | — | 0.2 | 34548/126 | 3.66 | 0 (1) | 0 | 0.0 |
| lazy-model | tc-reviewer | part-000001 | 186814 | да | 1 | — | 0.2 | 44240/663 | 4.22 | 0 (3) | 0 | 0.0 |
| lazy-model | autotest-reviewer | part-000004 | 194465 | да | 1 | — | 0.2 | 56658/10366 | 3.43 | 0 (3) | 0 | 0.0 |
| prose-model | tc-reviewer | part-000010 | 125603 | нет | — | NO_JSON_OBJECT; NO_JSON_OBJECT; NO_JSON_OBJECT | 0.6 | 33389/10 | 3.76 | — (—) | — | — |
| prose-model | tc-reviewer | part-000028 | 126560 | нет | — | NO_JSON_OBJECT; NO_JSON_OBJECT; NO_JSON_OBJECT | 0.5 | 34576/10 | 3.66 | — (—) | — | — |
| prose-model | tc-reviewer | part-000001 | 186814 | нет | — | NO_JSON_OBJECT; NO_JSON_OBJECT; NO_JSON_OBJECT | 0.5 | 44268/10 | 4.22 | — (—) | — | — |
| prose-model | autotest-reviewer | part-000004 | 194465 | нет | — | NO_JSON_OBJECT; NO_JSON_OBJECT; NO_JSON_OBJECT | 0.4 | 56686/10 | 3.43 | — (—) | — | — |

**Рекомендация по ролям** (схема с первой попытки на всех частях роли, наибольшая доля совпадений с Sonnet):

- `autotest-reviewer`: sonnet-replay
- `tc-reviewer`: sonnet-replay

В `.skillsrc`: `review_runner: {preset: codex, models: {tc-reviewer: <модель>, autotest-reviewer: <модель>}}`.
