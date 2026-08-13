# Test Skills

Портативный AI-пайплайн для проектирования тест-кейсов, независимого ревью,
проектно-нативной автоматизации, исполнения и проверяемой трассировки.

## Модель Pipeline 5.0 и V3 tail

`bare canonical JSON` — единственный семантический источник истины. Один документ
может содержать произвольное число последовательных шагов, stable IDs, ссылки на
выходы более ранних шагов, человеческие ожидания, машинные assertions, причины
manual-only и точные blockers. Schema и semantic validator проверяют его до
публикации.

Из каждой валидной revision детерминированно публикуется неизменяемый bundle:

```text
<document-id>.r<revision>.json
<document-id>.r<revision>.md
<document-id>.r<revision>.zephyr-scale.csv
```

Markdown и CSV — derived projections: они не редактируются отдельно и не читаются
downstream. Candidate публикуется до review; полный valid `AUTO_FIX_APPLIED`
successor публикуется отдельно. Одна `effective revision` и её receipt становятся
единственным входом automation, runner и trace; оба bundle остаются audit evidence.

Markdown показывает title, goal, preconditions и столько шагов, сколько нужно. У
каждого шага есть `Action` и `Expected Result`; человекочитаемая таблица содержит
ровно `№`, `Действие`, `Ожидаемый результат` и не имеет отдельной Test Data column.

Zephyr projection использует фиксированный профиль
`zephyr-scale-step-row-24-v1`: 24 headers, одна строка на canonical step, case
metadata только в первой строке, human-readable и formula-safe cells. Это намеренно
неполная внешняя проекция и не может восстановить JSON. Workbook/export structure
was observed, but a real tenant import round trip remains unverified.

## Пайплайн

```text
requirements and allowed project context
  -> source-inventory (technical test inventory + authorized behavior sources)
  -> context-marker (managed behavior context)
  -> test-classifier -> test-classifier-reviewer (persisted technical evidence sidecar)
  -> tc-generator (candidate bare canonical JSON; managed behavior context only)
  -> publish candidate JSON/Markdown/Zephyr CSV bundle
  -> tc-reviewer and effective revision selection
  -> tc-to-autotest -> autotest-reviewer
  -> optional runner -> build trace -> trace check -> finalization
```

`source-inventory` сохраняет `technical_test_inventory` и
`authorized_behavior_sources`. `context-marker` создаёт
`managed_behavior_context`; `test-classifier` и
`test-classifier-reviewer` сохраняют принятое `effective_technical_evidence` только
как sidecar attempt. В V3 automation, trace и finalization это техническое evidence
не передаётся; `tc-generator` получает только `managed_behavior_context`.

Automation описывает generated files, pair-addressed symbols и atomic
operation/assertion relations. Runtime identity — `(file_id, symbol_id)`; несколько
пар для одной semantic target имеют AND-semantics. Global provider/adapter preflight
проходит до запуска любого symbol, а static autotest review покрывает каждую
required pair.

Trace всегда строится и валидируется, включая BLOCKED, manual no-run, FAIL и
NOT_RUNNABLE branches:

```text
requirement -> case -> step -> expectation -> assertion -> file -> symbol -> current-run evidence
```

Final statuses: `PASS`, `PASS_WITH_MANUAL_REMAINDER`, `MANUAL_ONLY`, `BLOCKED`,
`FAIL`, `NOT_RUNNABLE`. Manual/blocked zero-pair branch пропускает только runner;
он не выбрасывается из trace.

## Быстрый старт

```powershell
git clone https://github.com/IxFire-X/test-skills.git
cd test-skills
python -m venv .venv
python -m pip install -r requirements.txt
python tools\doctor.py --root .
python tools\contract_check.py --root . --full
```

При первой команде оркестратор read-only сканирует структуру проекта и
автоматически создаёт `.skillsrc` v3. Если критическое значение неоднозначно, он
останавливается, показывает один вопрос и продолжает только в новом immutable
attempt после ответа. В монорепозитории затем выбирается exact module ID; его root,
язык и пути становятся единственным execution context.

Ручная инициализация нужна только для диагностики или CI:

```powershell
python tools\init_skillsrc.py --project <project> --write --output <project>\docs\to_do\skillsrc-init.json
```

`.skillsrc` описывает стек и пути, но не разрешает изменения проекта. Артефакты
запуска размещайте внутри `<project>/docs/to_do/`; рабочий код, существующие
тесты, конфигурация, зависимости и lock files не изменяются.

Не передавайте учётные данные, токены, cookie, закрытые ключи или реальные пароли.
Используйте только project-native fixtures, environment settings или opaque secret
handles с safe labels.

Передайте AI-агенту путь `skills/orchestrate/SKILL.md`, корни пакета и проекта,
явно разрешённые источники и новый каталог attempt. Подробнее: [USAGE.md](USAGE.md)
и [HOW-IT-WORKS.md](HOW-IT-WORKS.md).

## Реестр контрактов

`contracts/pipeline.json` — единственный machine registry маршрута и возможностей.
`CONTRACTS.md` и `PIPELINE.md` — его generated projections; не редактируйте их
вручную.

## V2.1 is unsupported

V2.1 artifacts explicitly reject with a breaking-change diagnostic. There is no
automatic semantic migration and no mixed-version pipeline.
