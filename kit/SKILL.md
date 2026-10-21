---
name: test-skills-host
description: >-
  Комплект test-skills для запуска в сессии CLI компании (хост-сборка): по требованиям к фиче агент CLI готовит
  тест-кейсы, их ревью, автотесты и доводит прогон до итога. Это инструкция агенту плюс локальные Python-помощники:
  агент запускает только локальные команды драйвера пакета (next/submit/status/answer/doctor), каждую задачу модели
  выполняет в своём окне; сборку, тесты и мутации запускает человек по напечатанным командам. operate.py,
  preflight.py и pack_query.py — инструменты человека до и после прогона, агент их не вызывает.
metadata:
  version: "0.5.0-pilot (пакет 20c8cd4 ветки main, профиль поставки host; комплект — ветка kit-host)"
  display_name: test-skills — хост-сборка для внутреннего прогона
  owner: IxFire-X
  skill_type: executable
  read_paths:
    - "<проект>: файлы выбранного модуля по inventory (git ls-files с учётом .gitignore), документы требований — агент читает их как входы задач"
    - "<проект>/.skillsrc: читается пакетом (конфигурация), в задачи модели не передаётся"
    - "<проект>: всё дерево текстовых файлов — только preflight.py (поиск ПДн/секретов, линтеров, сервисов), без передачи наружу"
    - "~/.m2/repository: jar PIT, плагина-резолвера и junit-platform-launcher для сверки SHA-256"
    - "переменная JAVA_HOME"
    - "<home> (папка комплекта внутри проекта): пакет test-skills/ и его skills/** (инструкции ролей и оркестратора — читает агент), состояние и журнал комплекта"
  write_paths:
    - "<проект>/.pilot-runs/: журнал, задачи, ответы, квитанции, отчёты прогона"
    - "<проект>: сгенерированные тесты — только в тестовом корне модуля и только *Test.java, *Tests.java, *IT.java, test_*.py, *_test.py"
    - "<проект>/test-cases/: проекции кейсов, экспорт Zephyr, манифест набора"
    - "<проект>/.skillsrc: создаётся или правится operate.py init только после показа diff и подтверждения (--yes-skillsrc)"
    - "<проект>: chmod +x mvnw/gradlew — только с подтверждением (--yes-chmod; --yes его не даёт)"
    - "<home>: operate-state.json, init.json, init-skillsrc.json, agent-message.txt, context-files.txt, preflight.json, init-measure.*, report-status.*, run-result.json, report/, skillsrc.bak-*"
    - "<home>/operate.log: команды и хвост их вывода (200 строк на команду, значения, похожие на секреты, замаскированы)"
    - "сборка проекта (запускает человек): target/, build/, ~/.m2, кэш Gradle — как обычная сборка"
  network_requirement: >-
    В коде нет сетевых вызовов и запуска CLI; модель вызывает ведущая сессия CLI по команде человека.
  external_delivery: >-
    Пакет ничего не отправляет; файлы проекта читает агент CLI в своей сессии.
  dependencies:
    - "Python 3.11+; jsonschema, PyYAML и транзитивные — версии и SHA-256 в test-skills/requirements.txt (ставит человек: pip install --require-hashes)"
    - "CLI компании (gigacode) в интерактивной сессии с инструментом выполнения команд — запускает человек"
    - "JDK 17+; сборка проекта: mvnw/gradlew или системный mvn — запускает человек"
    - "для мутаций: PIT, maven-dependency-plugin 3.8.1, junit-platform-launcher в ~/.m2 (SHA-256 закреплены в tools/mutation_tools.json)"
  rollback: >-
    Удалить папку комплекта (<home>); в проекте — .pilot-runs/, test-cases/, сгенерированные тесты (видны в git status),
    вернуть .skillsrc из <home>/skillsrc.bak-*, убрать строку комплекта из .git/info/exclude. В систему ничего не
    устанавливается. Подробно — PROVENANCE.md, «Откат».
---

# test-skills — хост-сборка

Ты — ведущий агент прогона test-skills. Прочитай `test-skills/skills/orchestrate/SKILL.md` (рядом с этим файлом)
целиком и следуй ему; первую команду, проект, модуль, спеку и `--target` даёт сообщение человека (его печатает
`operate.py init`).

- Запускай только команды пакета в виде из сообщения человека: `<интерпретатор> <пакет>/ts.py next|submit|status|answer|doctor …`.
  Никаких `cd`, другого `python`, сборки, тестов и других программ.
- `human_exec` — никогда не исполняй сам: покажи человеку команды и жди путь к отчёту.
- Гейты (`ask_user`: confirm-context, approve-generated-tests и другие) — отвечай только тем, что написал человек.
- `operate.py`, `preflight.py` и `pack_query.py` — инструменты человека, ты их не вызываешь.

Права, сеть и исполнение — `test-skills/USAGE.md`, «Права, сеть и исполнение», и `RUN.md`; происхождение, сверка и
откат — `PROVENANCE.md`.
