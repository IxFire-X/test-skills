# Импорт готовых кейсов и экспорт для Zephyr

## Импорт

`scan --cases <csv>` без `--docs`: шаг `context-marker:requirements` отвечает код — `prepare --stage context-marker:requirements` (`status` называет его; слота нет,
повтор ничего не меняет); скелет кейсов он публикует сам. Последний вход пачки `tc-generator` — скелет только её кейсов: код кладёт его в запрос пачки.
Человеческие поля кейсов не меняются.

`scan --cases <csv> --docs <док>`: шаг `context-marker:requirements` делает модель — по документам и CSV, со связями кейсов `case_links`
(отказ связей — `IMPORT_CASE_LINKS`, единственный повтор стадии); части и `merge` — как обычно. Скелет кейсов публикует `submit --stage plan` первым действием, до записи плана.
Маркер слияния берёт требования со связью с кейсом и добавляет кодом `SREQ-CSV-<строка>` на кейс вне документов;
требование без кейса в CSV — пробел: кейс для него не пишется, отчёт и `questions-for-analyst.md` называют его.

## Экспорт для Zephyr

Если в `.skillsrc` задан `projection.zephyr.template`, после закрытия прогона один раз:
`python -m tools.zephyr_template_export --project <abs> --run <id> --cases <bundle .zephyr-scale.csv>` — рядом ляжет `.zephyr-import.csv` для импорта в Zephyr.
Отклонённые кейсы (`*.REJECTED-CASES.*`) в Zephyr не импортируй. Ошибки `ZEPHYR_TEMPLATE_*`, `ZEPHYR_EXPORT_*` — покажи `hint` человеку как есть; шаблон не правь.
