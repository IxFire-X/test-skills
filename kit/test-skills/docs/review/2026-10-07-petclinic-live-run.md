# Живой прогон Petclinic: приёмка волны 2 (инструкция для отдельной сессии)

Протокол — раздел 2.7 дизайна `docs/review/2026-10-07-waves-2-3-design.md`. Прогон делает
**отдельная** сессия Claude Code; сессия разработки его не запускает и не помогает ему по ходу.
Пакет — test-skills на коммите гейта волны 2: `66bd7b9d86b83d4d10c58a879bd54c32b9d4472b` (`66bd7b9`, отчёт `docs/review/2026-10-07-wave-2-report.md`).

Все команды — PowerShell 7 на Windows. Архив `D:\AI-Projects-archive` и каталог
`D:\AI-Projects\Claude outputs` только читаются.

## 0. Что должно быть готово до начала

- JDK 17: `D:\AI-Projects\.tools\jdk-17` (Temurin 17.0.10).
- Python пакета: `D:\AI-Projects\.runtime\test-skills-venv\Scripts\python.exe` (3.12).
- CLI для частей ревью: `claude.exe` desktop-приложения
  `C:\Users\User\AppData\Roaming\Claude\claude-code\2.1.289\e1f0154146bb\claude.exe`
  (или установленный `claude`), **залогиненный**: один раз в обычном терминале
  `& $claude auth login` (или `& $claude`, затем `/login`). Проверка — шаг 1.6.
- Доступ к Maven Central (PIT разрешается в `~\.m2` по согласию прогона).

## 1. Подготовка каталога `D:\AI-Projects\live\petclinic-w2`

```powershell
$ErrorActionPreference = "Stop"
$live    = "D:\AI-Projects\live"
$project = "D:\AI-Projects\live\petclinic-w2"
$archive = "D:\AI-Projects-archive\2026-10-05\.runtime\portable-windows-pilot\spring-petclinic-maven-safe"
$pack    = "D:\AI-Projects\test-skills"
$jdk     = "D:\AI-Projects\.tools\jdk-17"
$py      = "D:\AI-Projects\.runtime\test-skills-venv\Scripts\python.exe"
$claude  = "C:\Users\User\AppData\Roaming\Claude\claude-code\2.1.289\e1f0154146bb\claude.exe"
$results = "D:\AI-Projects\live\petclinic-w2-$(Get-Date -Format yyyyMMdd)-results"
New-Item -ItemType Directory -Force $live, $results | Out-Null
```

### 1.1. Клон и checkout

```powershell
git -c safe.directory='*' clone --no-hardlinks $archive $project
git -c safe.directory='*' -C $project checkout --quiet 818c4136ea971c21674525f9053de0d9c7ad8cfe
git -c safe.directory='*' -C $project log --oneline -1   # 818c413 docs: Fix formatting in Docker container instructions
```

### 1.2. `docker-compose.yml`: два демо-пароля → `PETCLINIC_DEMO_PASSWORD`

Замена побайтная, CRLF сохраняется (иначе SHA-256 не совпадёт):

```powershell
$compose = Join-Path $project "docker-compose.yml"
$text = [IO.File]::ReadAllText($compose)
$text = $text.Replace('MYSQL_PASSWORD=petclinic', 'MYSQL_PASSWORD=${PETCLINIC_DEMO_PASSWORD}').Replace('POSTGRES_PASSWORD=petclinic', 'POSTGRES_PASSWORD=${PETCLINIC_DEMO_PASSWORD}')
[IO.File]::WriteAllText($compose, $text, (New-Object Text.UTF8Encoding $false))
(Get-FileHash $compose -Algorithm SHA256).Hash.ToLower()
# ожидается ef9ddfbcddcd2bc4dcaaf054c84de080336f40bb807ee516c49e5056e8a751bd
```

### 1.3. Спецификация

```powershell
$spec = Join-Path $project "docs\portable-owner-lifecycle-requirements.md"
New-Item -ItemType Directory -Force (Split-Path $spec) | Out-Null
& $py -c "import gzip,sys; open(sys.argv[2],'wb').write(gzip.decompress(open(sys.argv[1],'rb').read()))" `
  (Join-Path $pack "evals\review-scaling\data\petclinic-requirements.md.gz") $spec
(Get-FileHash $spec -Algorithm SHA256).Hash.ToLower()
# ожидается 79e7fe60566fbe401fb43b4b81f107849098f19c83c90d05eff781a00f40d7f8
```

### 1.4. Пакет на коммите гейта в `.tools\test-skills`

```powershell
$tools = Join-Path $project ".tools\test-skills"
git -c safe.directory='*' clone --quiet $pack $tools
git -c safe.directory='*' -C $tools checkout --quiet 66bd7b9d86b83d4d10c58a879bd54c32b9d4472b
& $py -m tools.doctor --root $tools        # integrity valid, qualification implemented_unverified
& $py -m tools.contract_check --root $tools --full
```

Каталог `.tools` исключён из инвентаря проекта так же, как в сентябре; разработка волны 3
его не трогает.

### 1.5. `.skillsrc`

Полный текст (`$project\.skillsrc`, UTF-8 без BOM):

```yaml
schema_version: 5.1.0
version: '3.0'
project:
  name: spring-petclinic
discovery:
  on_missing: automatic
  conflict_policy: ask_user
modules:
- id: root
  root: .
  stack:
    language: java
    framework: spring-boot
    build_tool: maven
  detected_from:
  - build.gradle
  - pom.xml
  paths:
    source:
    - src
    - src/main/java
    tests:
    - src/test/java
    resources:
    - src/main/resources
  test:
    framework: junit5
    adapter_id: maven-wrapper:selected-symbols-v1
    wrapper: mvnw.cmd
    build_profile: default
    adapter_parameters: {}
mutation:
  enabled: true
  threads: 4
  timeout_seconds: 3600
  timeout_const_ms: 4000
  mutators: DEFAULTS
  triage_limit: 30
review_runner:
  preset: claude
  models:
    tc-reviewer: claude-sonnet-5-5
    autotest-reviewer: claude-sonnet-5-5
  max_parallel: 4
  timeout_seconds: 1800
```

Модели ролей — минимальные по E5: Sonnet (`claude-sonnet-5-5`) для обоих генераторов, обоих
ревьюеров и разбора выживших (Sonnet прошёл W2-Р6 4/4, как и Fable).

### 1.6. Проверки окружения

```powershell
$env:JAVA_HOME = $jdk
$env:Path = "$jdk\bin;$env:Path"
Push-Location $project
& .\mvnw.cmd -q -B "-Dtest=PetClinicIntegrationTests#ownerDetails+ownerList" "-Dsurefire.failIfNoSpecifiedTests=false" test
if ($LASTEXITCODE -ne 0) { throw "PetClinicIntegrationTests are not green" }
Pop-Location
'Return exactly {"ok": true}.' | & $claude -p --output-format json --tools "" --model claude-sonnet-5-5 --no-session-persistence
# в ответе is_error: false и result с {"ok": true}; "Not logged in" — сначала залогиньте CLI
git -c safe.directory='*' -C $project status --short   # только docker-compose.yml, docs/, .skillsrc, .tools/
```

## 2. Сессия-оркестратор

- Новая сессия Claude Code, рабочий каталог — `$tools` (`D:\AI-Projects\live\petclinic-w2\.tools\test-skills`).
- Модель — Opus 5.5 (`claude-opus-5-5`), effort `high`. Роли без ревью (context-marker,
  генераторы, разбор выживших) — субагенты `model: sonnet`; части ревью запускает сам
  драйвер процессами `claude -p` на Sonnet (`.skillsrc` `review_runner`).
- Засеките время начала (`Get-Date -Format o` → `$results\started-at.txt`).

Стартовый промпт (полностью):

```text
Выполни D:/AI-Projects/live/petclinic-w2/.tools/test-skills/skills/orchestrate/SKILL.md для документа
D:/AI-Projects/live/petclinic-w2/docs/portable-owner-lifecycle-requirements.md.
Точный корень проекта: D:/AI-Projects/live/petclinic-w2. Модуль root, Maven, профиль local-pilot-v1.
Пользователь явно разрешил весь pipeline: кейсы, независимые ревью, создание автотестов и запуск
проверенных тестов на Windows в новом run, а также этап мутаций (флаг --mutation) с загрузкой
закреплённых jar PIT в локальный репозиторий Maven.

Первый вызов драйвера:
python -m tools.pipeline_driver next --project D:/AI-Projects/live/petclinic-w2 --profile local-pilot-v1
  --docs docs/portable-owner-lifecycle-requirements.md --subject "Жизненный цикл владельца, питомцев и визитов"
  --model-id claude-sonnet-5-5 --host-cli "Claude Code" --host-cli-version <версия>
  --mutation --review-runner process --review-runner-cli
  C:/Users/User/AppData/Roaming/Claude/claude-code/2.1.289/e1f0154146bb/claude.exe
  --require-driver-isolation --analyst-report --max-tasks 4

Команды драйвера запускай из D:/AI-Projects/live/petclinic-w2/.tools/test-skills Python-ом
D:/AI-Projects/.runtime/test-skills-venv/Scripts/python.exe. Перед каждым вызовом выставь
JAVA_HOME=D:/AI-Projects/.tools/jdk-17 и добавь его bin в начало PATH.

Задачи llm context-marker, tc-generator, tc-to-autotest и mutation-triage выполняй свежими
субагентами model: sonnet с SKILL роли из skill_path; каждый пишет ответ в output_path своей
задачи, submit делаешь ты. Части ревью драйвер выполняет сам: на action wait подожди poll_seconds
и снова вызови next. Не отвечай за модель скриптами и не пиши ответы ролей сам.

Цель: проверить фактическое покрытие всех условий документа, качество кейсов, автоматизацию и
обнаружение дефектов. Квоты на число кейсов нет; не сокращай варианты до одного happy path.
Возможный реальный дефект приложения (например, V05) не повод удалить требование, ослабить
проверку, заменить её skip или менять приложение. При FAIL сохрани всё и штатно заверши run.

Не читай прошлые run, cases, отчёты и любые перечни условий вне документа; не меняй пакет,
приложение, существующие тесты, документы, сборку, .skillsrc и docker-compose.yml. Не создавай
clone, worktree, venv, зависимости, CI, commit, push или PR.

Если драйвер вернул error или прогон упал на дефекте пакета — остановись, запиши состояние
(run_id, последнюю задачу, ответ драйвера) и не чини прогон руками.

В конце сообщи result целиком (completion, verification, accepted, reason_code,
review_independence, isolation_evidence, test_strength, strength_triage, analyst_questions,
paths) и время по стадиям из driver-log.jsonl.
```

### 2.1. Если CLI залогинить нельзя

Прогон идёт так же, но части ревью выполняют субагенты сессии: в первом вызове драйвера убрать
`--review-runner process --review-runner-cli … --require-driver-isolation` и добавить
`--reviewer-isolation fresh` (иначе драйвер спросит об изоляции задачей `ask_user`), в стартовом промпте
вместо «Части ревью драйвер выполняет сам…» — «Части ревью выполняй свежими субагентами
model: sonnet, по одному на часть». Тогда `isolation_evidence` будет `HOST_DECLARED`, критерий
`DRIVER_PROCESS` из раздела 5 не проверяется (в `interventions.md` это записать), токены частей
ревью — по счётчикам субагентов. Остальные критерии в силе.

## 3. Что собрать и куда

Всё — в `$results` (`D:\AI-Projects\live\petclinic-w2-<дата>-results\`):

```powershell
$run = "<run_id из результата>"
$runs = Join-Path $project ".pilot-runs"
Compress-Archive -Path (Join-Path $runs $run), (Join-Path $runs "$run.driver") -DestinationPath (Join-Path $results "run-$run.zip")
Copy-Item (Join-Path $runs "$run.driver\driver-log.jsonl") $results
Copy-Item (Join-Path $runs "$run.driver\candidate-bundle\test-strength.md"), (Join-Path $runs "$run.driver\candidate-bundle\test-strength.json") $results -ErrorAction SilentlyContinue
Copy-Item (Join-Path $runs "$run.driver\candidate-bundle\analyst-report.md"), (Join-Path $runs "$run.driver\candidate-bundle\analyst-report.json") $results -ErrorAction SilentlyContinue
(Get-Date -Format o) | Set-Content (Join-Path $results "finished-at.txt")
```

- **Время по стадиям.** Из `driver-log.jsonl`: `task_issued` → `command`/`submit` с тем же
  `task_id` (генераторы), `runner_launched` → `runner_collected` (части ревью), время этапа
  мутаций — `durations_ms.stage` квитанции `mutation-receipts\<attempt>.json`. Итог — в
  `stage-timing.json` (`stage`, `started`, `finished`, `seconds`).
- **Токены по ролям.** Части ревью — `tokens` в квитанциях `review-state\<attempt>\review-part-process-*.json`;
  субагенты — счётчики, которые показывает сессия по каждому субагенту (`role-tokens.json`:
  `role`, `task_id`, `model`, `tokens`, `seconds`).
- **Ручные вмешательства** — `interventions.md`: каждое вмешательство человека с временем и
  причиной. Скриптов, которые пишут ответы за модель, быть не должно.
- Отчёт мутаций (`test-strength.*`), отчёт для аналитиков (`analyst-report.*`),
  сопоставление условий (`coverage-oracle.json`, раздел 4).
- Сгенерированный класс после FAIL уходит из проекта, как и раньше (карантин — волна 3); его
  копия остаётся в архиве прогона (`artifacts/`, `.driver\outputs`).

## 4. Сопоставление 75 условий (только после конца прогона)

Перечень — `D:\AI-Projects-archive\2026-10-05\.runtime\portable-windows-pilot\large-java-lifecycle-20260906\coverage-baseline.json`
(только чтение). До конца прогона его не открывать и не передавать пайплайну.

`coverage-oracle.json`:

```json
{
  "run_id": "<run_id>", "attempt_id": "<attempt_id>", "baseline": "coverage-baseline.json",
  "conditions": [
    {"id": "O01-01", "requirement": "O01", "condition": "<текст условия из перечня>",
     "case_ids": ["TC-B1-001"], "assertion_ids": ["ASSERT-B1-001-01-1-2"],
     "method": "org.springframework.samples.petclinic.owner.<Class>#<method>",
     "result": "PASS",
     "evidence": "surefire TEST-<class>.xml: <method> passed; assertion ASSERT-… проверяет …"}
  ],
  "summary": {"covered_pass": 0, "covered_fail": 0, "not_covered": 0}
}
```

`result` — `PASS` (условие проверено прошедшим тестом), `FAIL` (проверено упавшим тестом),
`NOT_COVERED` (нет кейса или проверки). Доказательство — имя метода и ID проверки, которая
наблюдает условие, плюс строка отчёта Surefire.

## 5. Критерии

- V05 пойман как FAIL продукта, остальные тесты зелёные.
- Покрытие не хуже сентября: 74 из 75 условий — прошедшими тестами, V05 — упавшим.
- Отчёт мутаций по кейсам и требованиям есть. Если в `PetValidator` есть проверка длины
  имени, мутант границы на ней убит (это мутация `>` → `>=` малого пилота).
- У всех частей ревью `isolation_evidence: DRIVER_PROCESS` (результат прогона и квитанции
  процессов).
- Время и токены сравнены с сентябрём: 87 мин 14 с на финальную попытку, до неё — две
  неудачные.
- Sonnet как ревьюер кейсов на большом документе: каждую BLOCKING записать с разметкой
  «верная/ложная». Повтор с ревьюером кейсов на Fable — только по решению пользователя.

## 6. Если прогон упал на дефекте пакета

Сессия останавливается и **не чинит** прогон: записывает `run_id`, последнюю задачу, ответ
драйвера (`error`/`stopped`), хвост `driver-log.jsonl` и traceback в `$results\stop.md`.
Исправление делает сессия разработки отдельным коммитом с тестом; затем прогон повторяется
с начала (новый клон по разделу 1, пакет на коммите исправления).
