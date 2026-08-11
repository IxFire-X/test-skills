# Настройка portable testing skills

Это руководство описывает установку и детерминированный runtime. Пользовательские сценарии находятся в [USER-GUIDE.md](USER-GUIDE.md), canonical routing — в [CONTRACTS.md](CONTRACTS.md) и [PIPELINE.md](PIPELINE.md), дальнейшие работы — в [ROADMAP.md](ROADMAP.md).

## Состав

Пакет содержит шесть канонических skills:

| ID | Путь |
|---|---|
| context-marker | skills/context-marker/SKILL.md |
| tc-generator | skills/tc-generator/SKILL.md |
| tc-reviewer | skills/tc-reviewer/SKILL.md |
| tc-to-autotest | skills/tc-to-autotest/SKILL.md |
| autotest-reviewer | skills/autotest-reviewer/SKILL.md |
| orchestrate | skills/orchestrate/SKILL.md |

Единственный registry этих путей — contracts/pipeline.json. Package-level README, SKILL-LITE и templates не используются.

## Требования

- Python 3.10+.
- Зависимости из requirements-dev.txt для runtime validation и локальных тестов.
- Для Java execution: project-local Maven/Gradle wrapper или доступный runner и подходящий JDK.
- Для Python execution: project-native pytest environment.
- Для Windows adapter: PowerShell 7 или Windows PowerShell.

Проверь среду:

~~~text
python -m pip install -r requirements-dev.txt
python tools/doctor.py --root .
python tools/contract_check.py --root . --full
~~~

NOT_RUNNABLE означает недоступную capability, а не успешное выполнение.

## Установка

### Прямое использование

Скопируй пакет целиком или загружай SKILL.md по путям из contracts/pipeline.json. Host-specific plugin не требуется.

### Generic adapter

~~~text
python adapters/generic/install_skills.py --source skills --destination <skill-directory>
python adapters/generic/install_skills.py --source skills --destination <skill-directory> --dry-run
~~~

### Windows adapter

~~~powershell
pwsh -NoProfile -File adapters/windows/install.ps1 -SkillPackRoot . -Destination <skill-directory>
pwsh -NoProfile -File adapters/windows/install.ps1 -SkillPackRoot . -Destination <skill-directory> -WhatIf
~~~

Оба adapter копируют только шесть канонических packages, сохраняют bytes и timestamps, не меняют contracts/pipeline.json и при повторной установке не трогают совпадающие файлы.

## Project manifest

.skillsrc опционален. Если он существует, проверь его схемой schemas/skillsrc.schema.json. Он сообщает stack, test framework и project paths, но не разрешает менять production code, existing tests, configuration, lockfiles или dependencies.

При отсутствии manifest используй tools/scan_project.py только для read-only project discovery. Любой постоянный scan output размещай внутри docs/to_do/.

## Машинные артефакты

Каждый LLM-stage возвращает JSON envelope версии 2.1.0 и валидируется своей schema из schemas/. Не передавай Markdown или CSV вместо JSON.

После успешной валидации tc-generator всегда создай соседний CSV:

~~~text
python skills/tc-generator/scripts/export_test_cases_csv.py --input <tc-generator-output.json> --output <tc-generator-output.csv>
python skills/tc-generator/scripts/export_test_cases_csv.py --input <tc-generator-output.json> --output <tc-generator-output.csv> --verify-only
~~~

CSV — lossless transport для просмотра и Jira Zephyr-ориентированного импорта. JSON остаётся downstream authority.

## Execution и trace

Только tools/run_tests.py может подтвердить исполнение:

~~~text
python tools/run_tests.py --project <isolated-project> --language <java|python> --automation-artifact <tc-to-autotest-output.json>
~~~

Затем построй и проверь trace:

~~~text
python tools/build_trace_document.py --requirements <context.json> --test-cases <cases.json> --automation-artifact <automation.json> --run-result <run-result.json> --output <trace-document.json>
python tools/trace_check.py <trace-document.json> --require-execution
python tools/trace_check.py <trace-document.json> --orchestrator-artifact <orchestrator-output.json> --require-execution
~~~

PASS возможен только при runner exit 0, method-level execution evidence и trace PASS.

## Безопасность и CI

- Не сохраняй credentials, tokens, cookies и environment secrets в prompts, JSON, CSV, generated source или evidence.
- Не устанавливай dependencies и не меняй проект автоматически ради прохождения generated tests.
- Сохраняй literal argv, absolute cwd, native exit и stdout каждого material command.
- Останавливай pipeline на schema failure, reviewer rework, runner non-PASS или trace mismatch.
- Предыдущий failed artifact не перезаписывай; новая попытка получает новый каталог.

Минимальный CI gate:

~~~text
python tools/contract_check.py --root . --full
python tools/render_contract_docs.py --root . --check
python -m pytest tests -q
python -m ruff check tools tests skills/tc-generator/scripts adapters/generic
~~~
