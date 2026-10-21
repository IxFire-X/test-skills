# Процессный режим и авто-исполнение (универсальная сборка)

Этот документ — только для универсальной сборки пакета (репозиторий, Petclinic, CI).
В хост-сборку (`python -m tools.release_manifest --list host`) он не входит, как и
модули, которые он описывает: `tools/process_launch.py` (запуск сборки, тестов, PIT и
набора пакетом), `tools/model_runner.py`, `tools/pipeline_driver_runner.py`,
`tools/pipeline_driver_run.py` (запуск CLI модели драйвером). Там драйвер отвечает на всё
ниже ошибками `EXECUTION_MODE_UNAVAILABLE` и `RUNNER_UNAVAILABLE`, а основной режим
описан в [USAGE.md](../USAGE.md) и `skills/orchestrate/SKILL.md`.

## Авто-исполнение (`execution.mode: auto`)

В универсальной сборке `auto` — умолчание: после одобрения сгенерированных тестов
(`execution.review`) пакет сам запускает обёртку сборки проекта (`mvnw`/`gradlew`),
системный `mvn` или Python модуля с pytest — компиляцию, гейт и прогон тестов, с
`--mutation` — Maven (резолвер classpath) и `java` с PIT, в `suite-update-v1` — прогон
набора. Все процессы — списком аргументов, без оболочки, без переменных окружения,
похожих на секреты (USAGE, «Права, сеть и исполнение»). `execution.mode: human` или
`--execution-mode human` выключает это и здесь. Ручной `tools.run_pipeline exec` и
`rerun-retained` запускают тесты так же.

## Ревью процессами драйвера

```yaml
# .skillsrc — только пресет, модели по ролям, параллельность, таймаут, интерпретатор обёртки .py и env_passthrough
review_runner:
  preset: claude            # или codex
  models: {tc-reviewer: claude-sonnet-5-5, autotest-reviewer: claude-sonnet-5-5}
  max_parallel: 4
  timeout_seconds: 1800
  env_passthrough: [HTTPS_PROXY, "GIGACODE_*"]   # сверх белого списка: настройки CLI и прокси, не ключи (ключи — в конфиге CLI)
```

```powershell
python -m tools.pipeline_driver next --project "$project" --profile local-pilot-v1 --docs docs/feature.md `
  --review-runner process --review-runner-cli "C:\path\to\claude.exe" --require-driver-isolation
```

`--review-runner process` — части ревью драйвер запускает сам, отдельным процессом CLI на
каждую (пресеты `claude` и `codex`, аргументы — в `tools/model_runner.py`), во временном
каталоге вне проекта,
с задачей на stdin. `--review-runner-preset claude|codex` (по умолчанию — `.skillsrc`
`review_runner.preset`, иначе `claude`); `--review-runner-cli <путь>` — CLI не из PATH.
Свой шаблон команды — только флагом `--review-runner-command '["…", "{model}"]'` (хранится
в `.pilot-runs/<run>.driver/config.json` для возобновления — секретов в шаблон не класть).
Если `--review-runner-cli` указывает на скрипт `.py` (обёртка над CLI), драйвер запускает
его интерпретатором: `[<интерпретатор>, <скрипт>, …аргументы пресета]`, где интерпретатор
— абсолютный путь из `.skillsrc` `review_runner.interpreter`, иначе Python самого драйвера.

На `{"action": "wait"}` подождите `poll_seconds` и снова вызовите `next`: команда не ждёт
модель; если сессия оборвалась, процессы продолжают работу, а следующий `next` соберёт
их результаты. CLI должен быть залогинен (подписка или ключ самого CLI); пакет учётных
данных не видит.

С `--require-driver-isolation` результат ниже `DRIVER_PROCESS` не принимается
(`REVIEW_ISOLATION_UNVERIFIED`): хотя бы одна часть ревью шла не процессом драйвера
(`HOST_DECLARED`) или без изоляции (`NONE`).

**Окружение CLI модели.** CLI получает окружение по белому списку: `PATH`, `HOME`,
`JAVA_HOME`, `LANG`, `LANGUAGE`, `LC_*`, `TMP*`, `TEMP`, `PYTHONUTF8`, `PYTHONIOENCODING`
(Python-CLI на Windows без них падает на кириллице) и минимум Windows, без которого
процесс не стартует или CLI не найдёт свой профиль (`SYSTEMROOT`, `SYSTEMDRIVE`, `WINDIR`,
`COMSPEC`, `PATHEXT`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `HOMEDRIVE`, `HOMEPATH`; на
Windows без учёта регистра). Всё остальное — настройки самого CLI (`GIGACODE_*`), прокси
(`HTTPS_PROXY`) — только если названо в `.skillsrc` `review_runner.env_passthrough`: точные
имена или префиксы со `*` в конце (`*` в середине не допускается). Учётные данные пакет не
передаёт ни по имени, ни по префиксу: имя или префикс, похожие на секрет, и префиксы короче
трёх символов — ошибка конфигурации; ключи и токены CLI держите в его собственном конфиге.
Значения на диск не пишутся; имена переданных переменных — в `result.json` процесса и в
`driver-log.jsonl` (`runner_launched.env_names`), имена не переданных — там же
(`env_withheld`). То же применяется к `--version` CLI. Сеть: CLI обращается к провайдеру
модели из своей конфигурации; уходит текст задачи с выбранными файлами проекта.

**Каталог процессов — по умолчанию вне проекта.** Файлы процессного раннера (вход задачи
`stdin.txt` — копии файлов проекта, вывод CLI, результат) лежат в
`<--runner-dir>/<run_id>/runner/<задача>/`; по умолчанию `--runner-dir` —
`<временный каталог ОС>/test-skills`, путь — в `config.json` прогона и в `driver-log.jsonl`
(`runner_launched.directory`). Явный `--runner-dir` внутри проекта допускается — тогда в
`driver-log.jsonl` пишется предупреждение `RUNNER_DIR_INSIDE_PROJECT`: исключите этот
каталог в линтерах, читающих всё дерево. Корень проекта, `.pilot-runs` и `.git` не
принимаются; неверные опции запуска отклоняются JSON-ответом `DRIVER_INPUT` до создания
прогона. Каталоги `<run_id>` и `runner` создаются только для владельца (0o700). Для долгого
прогона задайте долговечный путь вне дерева проекта (не `/tmp`): очистка временного
каталога между прерыванием и `run --run` превращает запущенные процессы в потерянные, и
задачи запускаются заново.

## Без оркестратора

```powershell
python -m tools.pipeline_driver run --project "$project" --runner process --profile local-pilot-v1 `
  --docs docs/feature.md --answer reviewer-isolation=fresh
```

`run --runner process [--run <run_id>]` проходит весь run без сессии-оркестратора: каждую
задачу модели драйвер отдаёт процессу CLI и сам отправляет ответ (`--run` продолжает
прерванный). Ответы на `ask_user` — флагами `--answer <метка>=<значение>` (гейты:
`--answer confirm-context=<confirmation_digest>`,
`--answer approve-generated-tests=<approval_digest>`) или файлом `--answers answers.json`;
вопрос без ответа останавливает run с кодом 3. В `execution.mode: human` прогон
останавливается на `human_exec` с `"stopped": "HUMAN_EXECUTION"` (код 3) и продолжается
`run --runner process --run <id> --junit <отчёт>`. Это основа запуска из CI.

## Ручная последовательность

Прежние команды остаются для диагностики и ручного хода: `tools.run_pipeline` (`scan`,
`status`, `exec`, `rerun-retained`), `tools.orchestrate_test_case_revision`
(`prepare-review`, `open-part`, `submit-part`, `fail-part`, `finish-review` и другие),
`tools.review_budget`. Последовательность — в
[skills/orchestrate/references/manual-sequence.md](../skills/orchestrate/references/manual-sequence.md),
карта вызовов — в
[orchestration-contract.md](../skills/orchestrate/references/orchestration-contract.md),
процессный режим оркестратора — в
[process-mode.md](../skills/orchestrate/references/process-mode.md).
