# Процессный режим (только универсальная сборка)

Этот файл не входит в хост-сборку пакета (`python -m tools.release_manifest --list host`):
там нет модулей раннера, и драйвер отвечает на всё ниже ошибкой `RUNNER_UNAVAILABLE`.
В универсальной сборке (репозиторий пакета, Petclinic, CI) он дополняет
[SKILL.md](../SKILL.md).

## Ревью процессами драйвера

- `--review-runner process` — части ревью драйвер запускает сам, отдельными процессами
  CLI (пресет `claude` или `codex` из `.skillsrc` `review_runner`); `--review-runner-cli`
  — путь CLI, если его нет в PATH. Свой шаблон команды — только флагом
  `--review-runner-command`, никогда из файла проекта.
- `--require-driver-isolation` — принять результат только при `isolation_evidence:
  DRIVER_PROCESS` (части ревью шли процессами драйвера); иначе
  `REVIEW_ISOLATION_UNVERIFIED`: хотя бы одна часть ревью шла не процессом драйвера
  (`HOST_DECLARED`) или без изоляции (`NONE`).
- `wait` — части ревью выполняет сам драйвер. Ничего не запускай: подожди
  `poll_seconds` секунд и снова вызови `next --project ... --run ...`. Команда не ждёт
  модель и возвращается сразу; если сессия оборвалась, процессы продолжают работу, а
  следующий `next` соберёт их результаты.

## Запуск без оркестратора

`python -m tools.pipeline_driver run --project "$project" --runner process --profile ... --docs ...`
проходит весь run без сессии-оркестратора: каждую задачу модели (разметку, генераторы,
ревью, автоматизацию, разбор мутантов) драйвер отдаёт процессу CLI и сам отправляет
ответ. Ответы на `ask_user` — флагами `--answer <метка>=<значение>` или файлом
`--answers answers.json`; вопрос без ответа останавливает run с кодом 3. Это основа
запуска из CI; в сессии-оркестраторе эту команду не вызывай.

## Ручная последовательность

Прежние команды остаются для разбора сбоев и для run, начатого вручную:
`tools.run_pipeline` (`scan`, `status`, `exec`, `rerun-retained`),
`tools.orchestrate_test_case_revision`, `tools.review_budget`. Ручная
последовательность шагов — в [manual-sequence.md](manual-sequence.md), карта вызовов и
правила контракта — в [orchestration-contract.md](orchestration-contract.md).
