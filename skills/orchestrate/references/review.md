# Ревью: свежая сессия, субагент, канарейки, бюджет

Стадия — §4 `skills/orchestrate/SKILL.md`, согласие на ревью в той же сессии (SELF) — его §1; флаги — `python -m tools.api prepare --help`, `submit --help`.

**Свежая сессия.** `prepare --stage tc-reviewer:canonical` печатает `for_human` — инструкцию человеку (новая сессия или `/clear`, вставить «проведи ревью test-skills
<run_id>», ответ ревьюера — файл в `answer_slot`): покажи как есть и жди файла, затем `submit`. Контрольные ошибки код вставил в вид ревьюера:
карту канареек не читай и ревьюеру не показывай. Ревьюер называет слово в `codeword_seen`; `submit` сообщает `review_canaries` и
`codeword`. Отказ `REVIEW_ONLY_CANARY_FIXES` — правки держатся на контрольных ошибках: ревьюер оставляет их находками `INFO`; `REVIEW_CANARY_MISMATCH` — свои файлы не подставляй, повтори `prepare`. «Ревью слабое: пропущено N из M» и
`REVIEW_UNRELIABLE` — человеку перечитать набор самому.

**Ревьюер-субагент.** В задаче назови `answer_slot` и `reviewer_view` (вид кейсов) и разреши ему читать файлы проекта. Не давай: диалог и reasoning генератора, карту канареек,
`nonce`, чужие прогоны. Файлы прогона общим запретом не закрывай: проверка происхождения обязательна. Файлы проекта сверх пакета — ключ `evidence: ["путь", …]`
верхнего уровня файла ответа (пути из inventory попытки; не помещаются в бюджет — `REVIEW_CONTEXT_LIMIT`). Схема ответа этого ключа не перечисляет: код вынимает его до проверки схемой, ключ не убирай.

Одна logical stage invocation: `transport_attempts` 1 или 2; повтор — у каждой model stage, только при ошибке схемы или валидации ответа,
вход проверь тем же `step_size`; третьей попытки нет — стоп. В сессии допустимы нуль и больше C-lite `EVIDENCE_REQUESTED/EVIDENCE_PROVIDED`;
запрещены per-batch/hierarchical reviewers, вторая сессия и больше одного authoritative verdict. Не хватает бюджета на evidence — `submit --abort REVIEW_CONTEXT_LIMIT`;
ответ дважды не прошёл проверку, а сессия ждёт вердикта — то же с `REVIEW_UNUSABLE`; затем `finish`.

**SELF заранее.** Согласие человека на ревью в той же сессии можно дать до `prepare` ревью: `scan --accept-self-review` (в выводе — `details.self_review_scan`) или `review: {"self": true}` в `.skillsrc` (его ставит человек).
