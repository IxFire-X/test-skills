# Запуск test-skills на внутреннем проекте (Linux, свой CLI и модели)

Комплект — папка `kit/` ветки `demo-kit-20261015`, без архивов: `test-skills/` (пакет, коммит b3366e0, без `tests/`,
`docs/*.pptx`, `CLAUDE.md`, `.github/`), `test-skills-b3366e0.files.sha256` (суммы его файлов), `wrap/`, `bench/`,
`preflight.py`, `operate.py`, этот файл. Ниже `~/ts` — каталог комплекта; он может лежать и внутри проекта (раздел
«Размещение внутри проекта»). Из проекта сюда не возвращается ничего — только тексты ошибок и служебные логи (п. 7).

## Права и сеть

Что пакет читает, пишет, какие процессы запускает и куда при этом может ходить сеть — раздел USAGE.md «Права, сеть и
исполнение» (манифест прав пакета). Коротко, для этого комплекта:

- **Модель.** Задачи — части спеки и кода проекта — уходят модели через ваш CLI (`wrap/wrap.py` → команда из
  `wrap.json`) на тот адрес, который настроен в самом CLI. Сам `wrap.py` в сеть не ходит. Секреты в `extra_env` обёртка
  не принимает: учётные данные модели — в конфиге CLI. Если у CLI есть режим «без инструментов / read-only», его флаги —
  в `sandbox_args` (`--sandbox-args` у `operate.py`), иначе модель внутри CLI может сама читать и менять файлы.
- **Сборка и тесты.** Пакет запускает сборку проекта (Maven/Gradle), сгенерированные моделью тесты и PIT. Шаг 4
  `operate.py` пишет в `.skillsrc` `modules[].test.offline: true`: все сборки модуля и этапа мутаций идут с `-o` /
  `--offline`, только из `~/.m2` (кэша Gradle), без сети; нет артефакта — прогон останавливается
  (`BUILD_FAILED_BEFORE_TESTS` с «offline: артефакт не найден…» или `MUTATION_TOOL_MISSING`), а не скачивает его.
  `--online` — не включать offline: тогда сборка может ходить в репозитории из `~/.m2/settings.xml`.
- **Пины мутаций.** Jar'ы PIT и плагина-резолвера `maven-dependency-plugin` 3.8.1 закреплены по SHA-256
  (`test-skills/tools/mutation_tools.json`); резолвер и `junit-platform-launcher` версии проекта должны заранее лежать
  в `~/.m2` — preflight проверяет оба (резолвер — по SHA-256), пакет проверяет их до запуска Maven.
- **pip.** `operate.py` ничего не ставит: если нет `jsonschema`/`pyyaml`, он печатает команду
  `pip install --require-hashes -r test-skills/requirements.txt` (хеши всех файлов — в `requirements.txt`) и
  останавливается.
- **`operate.py` и `preflight.py`** сами в сеть не ходят; пишут только в каталог комплекта, а в проект — `.skillsrc`
  (шаг 4, после показа diff и подтверждения) и `chmod +x mvnw/gradlew` (шаг 1, с подтверждением). `--yes` эти два
  подтверждения не снимает — для них отдельные `--yes-skillsrc` и `--yes-chmod`.

## Размещение внутри проекта

Если писать вне проекта нельзя, комплект кладётся в проект, например в `<проект>/.ts`, и `operate.py` получает
`--home <проект>/.ts`. Тогда всё остаётся внутри:

- каталог процессов модели — `<home>/.runner` (вместо `<home>/runner`), временные файлы обёртки — `<home>/.tmp`
  (ключ `tmp_dir` в `wrap.json`); прогон пакета — как всегда, в `<проект>/.pilot-runs`;
- preflight печатает строку excludes линтера (nohttp, checkstyle, spotless …) сразу для `.pilot-runs` и `<home>` — её
  нужно вставить на ветке прогона, иначе линтер прочтёт файлы пакета и раннера и сборка упадёт. Исключается весь
  `<home>`, а не только `.runner` и `.tmp`: в нём лежит и сам пакет, а в его документации есть `http://` (nohttp);
- `<home>` добавить в `.gitignore` ветки прогона (или `.git/info/exclude`), чтобы комплект не попал в коммит.

Каталог раннера внутри проекта пакет b3366e0 ещё отвергает (`DRIVER_INPUT: --runner-dir must be outside the project`) —
это снимается обновлением пакета (пункт S2b). До него `operate.py` на этой ошибке останавливается с подсказкой:
обновить пакет или задать `--runner-dir` вне проекта, если это допустимо.

## Запуск одним скриптом

`operate.py` выполняет пункты 0–8 ниже по порядку: Python 3.11+, только stdlib; сам в сеть не ходит и ничего не
ставит, а что делают запущенные им процессы — в разделе «Права и сеть». Шаги: 0, 1, 2, 3, 4, 5a, 5, 7, 8.
Каждый шаг печатает свои команды, коды выхода и главные строки, а весь вывод пишет в `~/ts/operate.log`.
Подтверждения y/n снимает `--yes` (кроме правки `.skillsrc` и `chmod +x` файлов проекта — см. выше). Если шаг упал,
скрипт печатает подсказку и останавливается; после исправления продолжить с того же шага — `--from-step N`.
`--dry-run` только печатает команды, ничего не запускает.

```bash
mkdir -p ~/ts && cp -r <комплект>/. ~/ts/ && cd ~/ts
python3 operate.py --home ~/ts --project <проект> --docs <спека в проекте> --subject "<тема по-русски>" \
  --models glm-5.3,deepseek-0731-flash,qwen3.6-27b,qwen3.8-27b \
  --cli-command '/opt/llm/bin/cli chat --model {model}' --prompt-via stdin --answer-from stdout --cli-version '<cli> <версия>'
```

- **Шаг 0.** Сверяет папку `test-skills/` с `test-skills-*.files.sha256` (лишние `.py` — тоже стоп), проверяет Python и
  `jsonschema`/`pyyaml` (нет — печатает команду pip и останавливается; `--no-pip` — без команды, ставить средствами
  окружения), запускает doctor.
- **Шаг 2.** Записывает `wrap/wrap.json` из `--cli-command`/`--prompt-via`/`--answer-from`/`--answer-file`/`--sandbox-args`
  (плейсхолдеры — как в п. 2; `extra_env` прежнего `wrap.json` сохраняется), прогоняет селфтест и делает один живой вызов
  первой модели с показом ответа.
- **Шаг 3.** После бенчмарка предлагает модели по ролям. Для ревью кейсов и автотестов — по правилу п. 3, для
  остальных ролей — первая из `--models` (или `--default-model`). Свои назначения — `--role-model роль=модель`.
- **Шаг 4.** Пишет в `.skillsrc`:
  - `review_runner` и `requirements.id_pattern` (шаблон выводится из спеки или задаётся `--id-pattern`);
  - `mutation.enabled`, если мутации включены;
  - `test.offline: true` в каждом модуле (`--online` — не писать; если ключ уже есть — `false`).

  Перед записью показывает diff и спрашивает (`--yes` не отвечает, без терминала — `--yes-skillsrc`). Прежний
  `.skillsrc` копирует в `~/ts/skillsrc.bak-*`.
- **Мутации.** `--mutation` ставится, если preflight нашёл PIT в `~/.m2`; выключить — `--no-mutation`.
- **Шаг 5.** На `ask_user` показывает вопрос и ждёт ответа. На `regenerate-after-gate` и `BUILD_FAILED_BEFORE_TESTS`
  печатает хвост `runner-output.txt` и останавливается. Прерванный прогон продолжается так:
  `--from-step 5 --run <run_id> [--answer метка=значение]`. Если в проекте несколько целей — `--target <цель>`
  (передаётся драйверу).
- **Шаги 7–8.** Шаг 7 собирает `~/ts/report/`: файлы п. 7 и `summary.md` с цифрами прогона. Шаг 8 показывает путь
  к CSV для Zephyr.

## 0. Проверка комплекта

```bash
mkdir -p ~/ts && cp -r <комплект>/. ~/ts/ && cd ~/ts               # пакет уже папкой: ~/ts/test-skills
sha256sum -c --quiet test-skills-b3366e0.files.sha256             # молчит = все файлы пакета совпадают
chmod +x wrap/wrap.py preflight.py bench/bench.py
python3 --version                                                 # нужен 3.11+
python3 -c 'import jsonschema, yaml'                              # ошибка → поставить зависимости (ниже)
cd test-skills && python3 -m tools.doctor --root . && cd ..       # status PASS, integrity valid
```

Зависимости (`jsonschema`, `pyyaml`), если их нет: только
`python3 -m pip install --require-hashes -r test-skills/requirements.txt` — pip поставит лишь файлы, чьи SHA-256
записаны в `requirements.txt` (колёса CPython 3.11–3.13 для Linux x86_64/aarch64 и Windows x64, sdist); зеркало
(`pip config list`) должно отдавать те же файлы. Либо пакетами ОС (`python3-jsonschema`, `python3-yaml`).

## 1. Preflight проекта

```bash
export JAVA_HOME=<JDK 17+>
python3 preflight.py --pack ~/ts/test-skills --project <проект> --docs <спека в проекте>
```

Код 1 = есть «стоп». Частые: `mvnw` не исполняемый → `chmod +x mvnw`; нет PIT, резолвера
`maven-dependency-plugin-3.8.1.jar` (или его SHA-256 не совпадает с закреплённым) либо `junit-platform-launcher` версии
проекта в `~/.m2` → без `--mutation` или положить артефакты (`mvn dependency:get -Dartifact=…` из внутреннего
репозитория — preflight печатает координаты). При offline-сборке (шаг 4) в `~/.m2` должно быть всё, что нужно
проекту: собрать его один раз `mvnw test` с доступом к внутреннему репозиторию. **Линтер по всему дереву** (nohttp, spotless, checkstyle с `${basedir}`) → на ветке прогона
вставить строку excludes, которую печатает preflight (`…,**/.pilot-runs/**/*`). Без неё сборка падает на файлах пакета.

## 2. Обёртка CLI

Отредактировать `wrap/wrap.json` под свой CLI — одна из трёх форм (полные файлы — `wrap/examples/`; режим `arg` для
частей ревью не годится: задача 126–552 КБ, а Linux ограничивает один аргумент 128 КиБ):

```json
{"command": ["/opt/llm/bin/cli", "chat", "--model", "{model}"], "prompt_via": "stdin", "answer_from": "stdout"}
{"command": ["/opt/llm/bin/cli", "run", "-m", "{model}", "-i", "{prompt_file}", "-o", "{answer_file}"], "prompt_via": "file", "answer_from": "file", "answer_file": "{tmp}/answer.txt"}
{"command": ["/opt/llm/bin/cli", "ask", "--model", "{model}", "{prompt}"], "prompt_via": "arg", "answer_from": "stdout"}
```

Плюс по желанию `"timeout_seconds": 1700`, `"extra_env": {"LLM_ENDPOINT": "…"}` (имена с TOKEN, SECRET, PASSWORD,
API_KEY, `AWS_*`, `AZURE_*` обёртка отвергает — секреты в конфиг CLI), `"version": "<cli> <версия>"`, `"tmp_dir": "…"`
(где создавать временный каталог вызова; относительный — от `wrap.json`), `"sandbox_args": ["…"]` (флаги CLI «без
инструментов / read-only»: встают на место элемента `"{sandbox_args}"` в `command`, иначе в конец). Если CLI
печатает JSON-конверт (`result`/`text`/`content`/`choices[0].message.content` и `usage`) — обёртка возьмёт текст и токены
сама. Проверка до бенчмарка:

```bash
python3 wrap/wrap.py --selftest --pack ~/ts/test-skills            # фальшивым CLI: «Итог: ок»
echo '{"x":1}' | wrap/wrap.py exec --json --model <модель> --output-last-message /tmp/w.txt - && cat /tmp/w.txt   # живой CLI
```

## 3. Бенчмарк моделей

```bash
python3 bench/bench.py --pack ~/ts/test-skills --wrap-config wrap/wrap.json \
  --models glm-5.3,deepseek-0731-flash,qwen3.6-27b,qwen3.8-27b --out bench-$(date +%m%d)
```

Смотреть `bench-*.md`: «Принят» на первой попытке и «Доля» совпадения с Sonnet. Модель, не прошедшая схему, — не
для этой роли; «Б на токен» нужен для бюджета части.

## 4. `.skillsrc` проекта

```yaml
review_runner:
  preset: codex
  models: {context-marker: <м>, tc-generator: <м>, tc-reviewer: <м по бенчу>, tc-to-autotest: <м>, autotest-reviewer: <м по бенчу>, mutation-triage: <м>}
  max_parallel: 2          # локальные модели на одной машине: 1–2 одновременных вызова, иначе тайм-ауты
  timeout_seconds: 3600     # часть ревью у бюджета на локальной модели генерируется минутами
requirements: {id_pattern: '<шаблон ID требований спеки>'}
mutation: {enabled: true, threads: 2, timeout_seconds: 3600}   # для --mutation (без этого test_strength = NOT_APPLICABLE)
```

И в блоке `test` каждого модуля (`modules: - id: … test: …`) — `offline: true`: сборка и мутации только из `~/.m2`.

Бюджет части ревью: `--review-input-bytes` ≈ окно модели (токены) × «Б на токен» × 0,4; при окне 262k и ~3,5 Б/токен —
порядка 100 000 (меньше, если модель слабеет после 64–128k).

## 5a. Пробный прогон (сначала)

Ранний сигнал по схеме и качеству за 30–60 мин: только разметка, генератор и ревью кейсов.

```bash
cd ~/ts/test-skills
python3 -m tools.pipeline_driver run --project <проект> --runner process --profile cases-only-v1 \
  --docs <спека> --subject "<тема>" --model-id <модель> --host-cli <имя CLI> --host-cli-version <версия> \
  --reviewer-isolation fresh --review-runner-preset codex --review-runner-cli ~/ts/wrap/wrap.py \
  --runner-dir ~/ts/runner --review-input-bytes 100000 --max-tasks 2
```

Итог `completion: COMPLETE` и кейсы в `test-cases`-проекциях — можно идти в полный прогон (п. 5). Код выхода 1 у
`cases-only-v1` — норма (черновик).

## 5. Прогон

```bash
cd ~/ts/test-skills
python3 -m tools.pipeline_driver run --project <проект> --runner process \
  --profile local-pilot-v1 --docs <спека> --subject "<тема по-русски>" --model-id <модель по умолчанию> \
  --host-cli <имя CLI> --host-cli-version <версия> --reviewer-isolation fresh \
  --review-runner-preset codex --review-runner-cli ~/ts/wrap/wrap.py \
  --runner-dir ~/ts/runner --review-input-bytes 100000 --max-tasks 2 \
  --mutation --analyst-report --suite
```

- `--runner-dir` — долговечный каталог (не `/tmp`): там входы и выходы процессов модели. В пакете b3366e0 — только
  **вне** проекта; внутри проекта (`<home>/.runner`) — с обновлением пакета (раздел «Размещение внутри проекта»).
- Прервался (Ctrl+C, обрыв)? Продолжить тот же прогон: `… run --project <проект> --runner process --run <run_id>`. Каталог
  процессов прогон помнит сам (`config.json`), флаг повторять не нужно — но `~/ts/runner/<run_id>` не удалять и не
  переносить: уже запущенные вызовы модели собираются оттуда.
- Без PIT в `~/.m2` — убрать `--mutation`.

## 6. Если остановился

| Что в выводе | Что делать |
| --- | --- |
| `"action": "ask_user"`, код 3 | прочитать `question`; ответить повтором с `--answer <метка>=<значение>` (метка — хвост `task_id` после `.ask.`) |
| `ask.regenerate-after-gate` / `GENERATED_TEST_INVALID` | **не перегенерировать сразу**: открыть `.pilot-runs/<run>/artifacts/<attempt>/runner-output.txt` — если там линтер/сборка проекта, а не ошибки тестов, исправить окружение (п. 1) и начать новый прогон; перегенерация — только при ошибках компиляции самих тестов |
| `"action": "error"`, `code` | код и `message` — сюда (п. 7); `DRIVER_FAILURE` — дефект пакета |
| `RUNNER_*` в журнале (`RUNNER_TIMEOUT`, `RUNNER_EXIT_n`, `RUNNER_ANSWER_NOT_JSON`) | смотреть `~/ts/runner/<run>/runner/<задача>/stderr.txt` и `last-message.txt`; модель не держит схему → другая модель для роли или меньший `--review-input-bytes` |
| `completion: PARTIAL`, `reason_code` | `reason_code` — сюда |
| verification `FAIL` | ожидаемо при дефекте продукта: `quarantine.md` (вопрос аналитику и черновик баг-репорта) |

## 7. Что принести

Из `<проект>/.pilot-runs/<run>.driver/`: `driver-log.jsonl`, `config.json` (без секретов), итоговый JSON прогона (вывод
`run`), коды и тексты ошибок, хвост `runner-output.txt` (только строки ошибок Maven, без кода проекта), сводки
`candidate-bundle/test-strength.md` и число вопросов `analyst-report.md`. Не приносить: код, спеку, кейсы, ответы
моделей, `stdin.txt`.

## 8. Zephyr

Импортировать `test-cases/test-cases.zephyr-scale.csv` (не XML). Приоритеты в экспорте — High / Normal / Low.
Обязательные пользовательские поля Zephyr (АС, Вид тестирования, Команда, Приоритет теста) проверить в первой строке
перед массовым импортом.
