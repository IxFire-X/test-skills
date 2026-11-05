# Ревью: свежая сессия, субагент, канарейки, бюджет

Стадия — §4 `skills/orchestrate/SKILL.md`; команды и их флаги — `python -m tools.api <команда> --help`.

**Свежая сессия.** `review-open` печатает `for_human` — инструкцию человеку (новая сессия или `/clear`, вставить «проведи ревью test-skills
<run_id>», ответ — файл, затем `review-select --verdict <файл>`): покажи как есть и жди файла. Контрольные ошибки код вставил в вид ревьюера:
карту канареек не читай и ревьюеру не показывай. Ревьюер называет слово в `codeword_seen`; `review-select` сообщает `review_canaries` и
`codeword`. Отказы `REVIEW_CANARY_ONLY_VERDICT` / `REVIEW_ONLY_CANARY_FIXES` — вердикт держится на контрольных ошибках: ревьюер оставляет
их находками `INFO`; `REVIEW_CANARY_MISMATCH` — свои файлы не подставляй, повтори `review-open`. «Ревью слабое: пропущено N из M» и
`REVIEW_UNRELIABLE` — человеку перечитать набор самому.

**Ревьюер-субагент.** В задаче назови `run_root` и `attempt_id` и разреши только эти команды (все читают): `python -m tools.api read-receipt
--run-root <run> --attempt <id> --kind reviewer-session-boundary --event ARTIFACT_READ_BACK` (оба `--kind` и `--event` обязательны),
`read-effective` (до вердикта — `effective: null`), `read-ledger [--digest <d>]`, `review-evidence --path <путь в проекте>`. Дай вид
`review-views/<sha>.md`. Не давай: диалог и reasoning генератора, карту канареек, `nonce`, чужие прогоны. Файлы прогона общим запретом
не закрывай: проверка происхождения обязательна.

Одна logical stage invocation: `transport_attempts` 1 или 2; повтор — у каждой model stage, только при ошибке схемы или валидации ответа,
вход проверь тем же `step_size`; третьей попытки нет — стоп. В сессии допустимы нуль и больше C-lite `EVIDENCE_REQUESTED/EVIDENCE_PROVIDED`;
запрещены per-batch/hierarchical reviewers, вторая сессия и больше одного authoritative verdict. Не хватает бюджета на evidence — закрой
сессию `review-abort --reason REVIEW_CONTEXT_LIMIT --out <run>/tmp/session.json` и вызови `review-select` без `--verdict` (последняя ревизия журнала; флага `--reviewer-session` в этой поставке нет); ответ
дважды не прошёл проверку, а сессия ждёт вердикта — то же с `--reason REVIEW_UNUSABLE`.
