# Контракт обёртки модели для `--runner process` (пресет `codex`), пакет `ee8b969`

Основа для `wrap.py` (S3). Проверено сквозным прогоном S2 на Windows: `python -m tools.pipeline_driver run --runner process`
на чистом клоне Petclinic с фальшивым CLI `transfer\s2\codex.cmd` → `stub_codex.py`, который отдаёт записанные ответы
прогона g. Журнал всех вызовов — `transfer\s2\stub-log.jsonl`. **Linux локально не проверен** (docker/WSL здесь нет):
на POSIX пакет берёт ту же ветку `subprocess.Popen` с `start_new_session=True` и `flock` вместо `msvcrt`.

## 1. Как пакет вызывает обёртку

Запуск прогона без оркестратора:

```bash
python3 -m tools.pipeline_driver run --project <проект> --runner process \
  --profile local-pilot-v1 --docs <спека> --subject "<тема>" --model-id <модель по умолчанию> \
  --host-cli <имя> --host-cli-version <версия> --reviewer-isolation fresh \
  --review-runner-preset codex --review-runner-cli /abs/path/wrap \
  --max-tasks 4 [--mutation --analyst-report --suite]
```

`--review-runner-cli` — исполняемый файл обёртки (на Linux — скрипт с shebang и `chmod +x`). Пакет запускает его так —
**точный argv из S2**:

```
<wrap> exec --skip-git-repo-check --sandbox read-only --ephemeral --json \
       --model <M> --output-last-message <runner-dir>/last-message.txt - \
       [--output-schema <runner-dir>/schema.json]
```

- `--model <M>` — модель роли: `.skillsrc` → `review_runner.models.<роль>` (роли: `context-marker`, `tc-generator`,
  `tc-reviewer`, `tc-to-autotest`, `autotest-reviewer`, `mutation-triage`), иначе `--model-id`. Если модели нет вовсе —
  пара `--model …` не передаётся.
- `--output-schema` — только когда схема ответа самодостаточна (без внешних `$ref`). В S2: у частей ревью есть, у
  разметки и генератора нет. Схема всё равно приходит **в тексте stdin** (раздел «Схема ответа»).
- `-` — «задача на stdin».
- Отдельным вызовом пакет спрашивает версию: `<wrap> --version` (stdin пустой) — ответить одной строкой и кодом 0.

## 2. Вход: stdin (UTF-8)

Всё, что может читать роль, одним текстом (`model_runner.compose_input`): SKILL роли (с `name: <роль>` во фронтматтере),
`## Задача` (инструкции задачи; при повторе после отказа в конец дописан текст отказа), `## Схема ответа` (JSON-схема в
блоке ```json), `## Входные данные` — каждый файл как `### Файл <имя>` и его содержимое, и последняя строка
«Ответ — только один JSON-объект по схеме, без пояснений и без обрамления.» Размер в S2: 126–552 КБ
(`--review-input-bytes` задаёт бюджет частей ревью; генератор и автоматизация — больше, до ~2,4 МБ у автоматизации Petclinic).
Обёртке не нужен доступ к файлам проекта — всё в stdin.

## 3. Выход: что пакет читает

1. **Файл `--output-last-message`** — главный: текст ответа модели. Пакет берёт из него JSON-объект (весь текст,
   блок ```json``` или внешний `{…}`).
2. **stdout — события JSONL** (как `codex exec --json`), по строке:
   - `{"type": "thread.started", "thread_id": "<id сессии>"}` — в квитанцию (`session_id`);
   - `{"type": "item.completed", "item": {"type": "agent_message", "text": "<ответ>"}}` — запасной источник ответа;
   - `{"type": "turn.completed", "usage": {"input_tokens": N, "output_tokens": N, "cached_input_tokens": N}}` — токены
     в квитанцию;
   - `{"type": "error", ...}` или `{"type": "turn.failed", ...}` — отказ транспорта (`RUNNER_CLI_ERROR`), текст события
     проверяется на лимиты.
   Строки, которые не JSON, игнорируются.
3. **Код выхода**: 0 — успех; ненулевой — `RUNNER_EXIT_<код>` с хвостом stderr/stdout (транспортный отказ).
4. Нет ответа → `RUNNER_OUTPUT_EMPTY`; ответ без JSON-объекта → `RUNNER_ANSWER_NOT_JSON`.

Модель в квитанции — «configured» (пресет codex модель из событий не читает).

## 4. Окружение процесса и изоляция

- Каждый вызов — **новый процесс** без истории, рабочий каталог — свежий временный вне проекта
  (`/tmp/test-skills-model-XXXX`), удаляется после вызова. Это и есть `--reviewer-isolation fresh` через процессы:
  в S2 у всех 41 части ревью `isolation_evidence: DRIVER_PROCESS` и квитанция `review-part-process-…`.
- Окружение — белый список (S6): обёртка и CLI получают `PATH`, `HOME`, `JAVA_HOME`, `LANG`, `LANGUAGE`, `LC_*`, `TMP*`,
  `TEMP`, `PYTHONUTF8`, `PYTHONIOENCODING`, системный минимум Windows и только явно названные в `env_passthrough`
  (`wrap.json`; секретоподобные имена и префиксы короче 3 символов отвергаются) плюс `extra_env`; учётные данные модели —
  в конфиге самого CLI. Одноразовый токен запуска обёртка не видит (`TEST_SKILLS_RUNNER_LAUNCH` отсутствует).
- Процесс запускает шим `python -m tools.model_runner shim <runner-dir>`: stdin из `stdin.txt`, stdout/stderr — в
  `stdout.txt`/`stderr.txt`, итог — `result.json`. Каталог вызова — в каталоге раннера вне `.pilot-runs`
  (`--runner-dir`: `<home>/runner`, при `--home` внутри проекта — `<home>/.runner`): `<runner-dir>/<run>/runner/<task>[-runN]/` (`spec.json`, `schema.json`, `stdin.txt`, `stdout.txt`,
  `stderr.txt`, `last-message.txt`, `result.json`, `launch.json`, `lock`).

## 5. Тайм-аут, повторы, параллельность, лимиты

- Тайм-аут вызова — `.skillsrc` `review_runner.timeout_seconds` (по умолчанию 1800 с). По истечении шим убивает дерево
  процессов (`killpg` на POSIX) → `RUNNER_TIMEOUT` (транспорт).
- До 3 попыток на задачу, каждая — новый процесс; после отказа валидации текст ошибок дописывается в инструкции.
- Параллельно — до `--max-tasks` (и `review_runner.max_parallel`, по умолчанию 4) процессов.
- Лимиты/перегрузка (`429`, `rate limit`, `quota exceeded`, `resource exhausted`, `limit reached` и т. п. в выводе или
  событии ошибки) — пауза без траты попытки: 60 с, удваивается до 30 мин, не больше 8 пауз на задачу.

## 6. Что должна делать `wrap.py` для внутреннего CLI

1. Принять argv пресета codex (п. 1), прочитать stdin целиком.
2. Вызвать внутренний CLI/модель `<M>` с этим текстом (без инструментов записи и shell; без истории).
3. Записать текст ответа в путь `--output-last-message` (UTF-8).
4. Напечатать в stdout минимум `{"type":"item.completed","item":{"type":"agent_message","text":…}}` и, если известны,
   `thread.started` и `turn.completed` с `usage`.
5. Код 0 при ответе; при ошибке модели — ненулевой код и текст в stderr (слова `rate limit`/`429`, если это лимит).
6. `wrap --version` → строка версии, код 0.

Образец — `transfer\s2\stub_codex.py` (та же форма, вместо модели — записанный ответ).

## 7. Результат S2

Прогон `a6c71dcf…` (клон `live\petclinic-20261014s2`, пакет `ee8b969`): разметка и генератор через процесс, затем
41 часть ревью кейсов — все приняты, `DRIVER_PROCESS`; цикл `next`/`submit` вёл сам драйвер (`run`), без оркестратора.
Итог прогона до конца — в `transfer\s2\run-result.json` (дописывается по завершении).

**Итог S2 до конца.** Все модельные задачи (разметка, генератор, 41 часть ревью кейсов, автоматизация, 8 частей ревью
автотестов) прошли через процессный раннер. Гейт исполнения упал на nohttp Petclinic: `stdin.txt` в каталогах
раннера (`.pilot-runs/<run>.driver/runner/…`) содержит копии файлов проекта, в том числе SVG с `http://www.w3.org/…`, а
подавление nohttp действует только по расширению `.svg`. Вывод: **при `--runner process` линтер, читающий всё дерево,
гарантированно ломает исполнение** — до прогона исключить `.pilot-runs/**` в его конфигурации (preflight S4 такие линтеры
находит). `run` без ответа на вопрос драйвера останавливается с кодом 3 (`ASK_USER_UNANSWERED`).
