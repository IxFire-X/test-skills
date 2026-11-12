# Импорт готовых кейсов

`scan --cases <csv>` без `--docs`: шаг `context-marker:requirements` делает код — `python -m tools.api cases-import --run-root <run> --attempt <attempt_id>`
(план частей называет его в `by_code`). Файл `<run>/imported-cases/<attempt_id>.json` передай в `plan-batches --imported`.
Последний вход пачки `tc-generator` — `batch_skeletons[<batch_id>]` из вывода `publish-generator-input --kind plan`: скелет только её кейсов. Человеческие поля кейсов не меняются.

`scan --cases <csv> --docs <док>`: шаг `context-marker:requirements` делает модель — по документам и CSV, со связями кейсов `case_links`
(отказ связей — `IMPORT_CASE_LINKS`, единственный повтор стадии). После её ответа — `cases-import` (он только публикует скелет), дальше части
и `merge`, как обычно. Маркер слияния берёт требования со связью с кейсом и добавляет кодом `SREQ-CSV-<строка>` на кейс вне документов;
требование без кейса в CSV — пробел: кейс для него не пишется, отчёт и `questions-for-analyst.md` называют его.
