# Test Skills Portable Pilot

## Презентация и обзор

- [Настоящие тест-кейсы Spring Petclinic: 64 кейса, 290 шагов](docs/examples/petclinic-owner-lifecycle/cases.md) — [HTML, исходный JSON и пояснение к результатам](docs/examples/petclinic-owner-lifecycle/README.md).
- [Подробный справочник всех файлов и схемы взаимодействия модулей](docs/pipeline-file-guide-ru.md).
- [Презентация на русском: 16 слайдов, около 20 минут](docs/portable-testing-pipeline-ru.pptx).
- [Заметки докладчика](docs/presentation-notes-ru.md).
- [Что сделано, структура пакета и причины попробовать](docs/portable-pipeline-overview-ru.md).
- [Результаты большого пилота и границы подтверждённого](docs/pilot-evidence.md).

В этом репозитории опубликован переносимый пакет тестирования. Старые версии
сохранены в истории Git.

Портативный skill-pack для проектирования тест-кейсов, независимого ревью,
генерации автотестов, project-native выполнения и проверяемой трассировки.
Пакет не является самостоятельным LLM runner: моделью управляет совместимая CLI,
а Python tools только читают, проверяют, публикуют и исполняют закрытые операции.

## Точка входа

Скопируйте пакет целиком в обычный рабочий проект и явно попросите model-enabled
CLI выполнить `skills/orchestrate/SKILL.md`. Совместимая CLI обязана прочитать exact
Skills, references, schemas и `contracts/pipeline.json`, сохранять и читать обратно
артефакты, создавать отдельный fresh reviewer invocation и уметь продолжить
nonterminal attempt из durable state.

Первичный вход — один документ требований для новой или изменяемой фичи. Project,
module, path или `--target` могут только сузить scope. Сам путь к проекту не разрешает
придумать фичу.

## Два профиля

- `cases-only-v1` создаёт и независимо проверяет canonical test cases. Код проекта не
  запускается; materialization и execution имеют `NOT_APPLICABLE`.
- `local-pilot-v1` после accepted canonical и automation review материализует exact
  generated file set и запускает только reviewed targets через project-native
  pytest, Maven wrapper или Gradle wrapper.

Явный запрос полного pipeline даёт run-scoped разрешение на такое выполнение только
для текущего run. Простое наличие папки или запрос тест-кейсов код не запускают.
Пайплайн не создаёт clone/worktree/sandbox, не ставит JDK, Python, зависимости или
plugins и не меняет CI, cron, Git history или remote.

## Как идёт run

```text
explicit Skill invocation
  -> run manifest + authorization + RUN_CREATED
  -> read-only inventory -> exact module -> frozen execution baseline
  -> append-only attempt
  -> deterministic requirement batches
  -> canonical candidate -> one fresh authoritative review
  -> cases-only finalization
     или
     automation -> static review -> generated delta -> materialization
       -> project-native execution -> trace -> file dispositions
       -> finalization -> terminal result
```

Один run навсегда связан с одной exact project/module identity. Он может содержать
последовательную append-only lineage child attempts, но одновременно nonterminal
может быть только один. `WAITING_FOR_INPUT` и `WAITING_FOR_MODEL` продолжаются через
resume; terminal attempt неизменяем.

Canonical JSON — единственный семантический источник. HTML, Markdown и
`zephyr-scale-step-row-24-v4` CSV — derived human projections. Опциональный
`zephyr-scale-xml-observed-v1` остаётся observed/unverified: реальный Zephyr tenant
import/re-export не доказан.

При публикации кейсов рядом с `<document_id>.r<revision>.html` автоматически
создаётся `<document_id>.r<revision>.md`: цель, предусловия и шаги с данными и
ожидаемыми результатами. Markdown строится из того же JSON и не служит входом
автоматизации. Повторная публикация сверяет его байты и не перезаписывает изменённую
вручную копию. Формат существующих receipts сохранён: `--verify-only` проверяет
JSON/HTML/CSV, а Markdown проверяется при публикации и не входит в receipt.

## Execution и generated files

Executor получает закрытый adapter ID, exact interpreter/wrapper path, build profile
и typed parameters — не shell string. Он запускается из выбранного module cwd и не
исправляет тест после FAIL без отдельного доказанного решения.

Generated output — множество файлов, и у каждого есть materialization и disposition
receipt. Основные правила:

- authoritative `PASS` и valid trace позволяют записать pre-finalization
  `RETAINED`; acceptance дополнительно требует valid finalization;
- `FAIL` и `NOT_RUNNABLE` очищают только byte-identical pipeline-owned files;
- `UNKNOWN` никогда не очищает generated delta;
- partial materialization запрещает execution и безопасно закрывается как
  `PARTIAL/NOT_APPLICABLE`.

## Результат

Результат не является одним старым status. Он содержит независимые оси:

- lifecycle: `ACTIVE`, `WAITING_FOR_INPUT`, `WAITING_FOR_MODEL`, `TERMINAL`;
- completion: `COMPLETE`, `PARTIAL`, `FATAL` или ещё не установлен;
- verification: `PASS`, `FAIL`, `UNKNOWN`, `NOT_RUNNABLE`, `NOT_APPLICABLE` или ещё
  не установлен;
- coverage: `FULL`, `MIXED`, `MANUAL_ONLY` или ещё не установлено;
- terminal `reason_code` и вычисленное `accepted`.

Invalid finalization всё равно завершает attempt, но даёт `accepted=false` и
`FINALIZATION_INVALID`. Фактические verification/coverage при этом не переписываются.

## Секреты и границы

Файл с потенциальным secret целиком исключается из model context. Артефакты содержат
только safe labels или opaque runtime handles, никогда raw values или обычный hash
секрета. Окружение хранит только allowlisted safe key IDs/labels без values.

Пайплайн может создавать только свои новые generated-test files и собственные evidence
artifacts. Application source, существующие тесты, настройки, lock files и зависимости
не меняются.

## Проверка пакета

Linux и Windows — целевые платформы реализации. CI этого репозитория запускает один
и тот же implementation gate на Ubuntu и Windows; он проверяет пакет, а не квалификацию
всех CLI/моделей/проектов. Настройка CI целевого проекта остаётся за его владельцем.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
python -m tools.ci_gate --root .
python -m tools.doctor --root .
```

На Linux используйте `export PYTHONDONTWRITEBYTECODE=1`, затем те же команды Python.
`tools.ci_gate` уже включает contract check, проверку проекций и весь pytest; отдельно
повторять их не требуется. Сама команда не создаёт CI-сервис или workflow.
Весь этот раздел предназначен для maintainer/release-проверки и автоматически из
обычного production pipeline run не вызывается.

`contracts/pipeline.json` — machine truth. `CONTRACTS.md` и `PIPELINE.md` — generated
projections. `release/manifest.json` связывает package `0.5.0-pilot` с exact runtime
bytes и сейчас честно имеет `implemented_unverified`; readiness допустима только как
`core-pilot-ready for <exact verified tuple>` после внешнего adaptive release eval.
Release eval запускается только отдельной явной командой квалификации и не входит в
обычный production pipeline run. Пакет не создаёт и не изменяет CI целевого проекта.

Company runner, production rollback и реальный Zephyr tenant round-trip не входят в
core pilot readiness и остаются `N/A`, пока не появится отдельное evidence.

Подробнее: [USAGE.md](USAGE.md), [HOW-IT-WORKS.md](HOW-IT-WORKS.md) и
[RELEASE.md](RELEASE.md).
