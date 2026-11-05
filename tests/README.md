# Тесты пакета: что запускать после правки

## Smoke между пакетами

```bash
python -m pytest -m "contract and not native and not slow" -n 4
```

Набор `contract` — 33 файла (`tests/contract/members.py`, `pytestmark = pytest.mark.contract` в каждом) и тесты каталога `tests/contract/`:
счётчики и реестр контракта, бюджеты ролей, форма вывода CLI, тексты. `native` — прогоны настоящего pytest/Maven проекта (режим Б), в набор не входят (пока ни один тест не помечен). `slow` — 12 тестов набора дольше 30 с (список с секундами — `SLOW` в `tests/contract/members.py`); они идут отдельно:

```bash
python -m pytest -m "contract and slow" -n 4
```

После каждого rebase — первая команда; вторая — когда правка касается схем и движка схем или режима Б (см. таблицу). Полный набор гоняют гейт оркестратора и CI.
Любая правка пакета проходит этот набор до push; он ловит то, что падает в соседнем пакете, а не в вашем.
`-n` берётся только из командной строки (xdist стоит у разработчика, не в пакете).

## Класс правки → тесты → что сделать до запуска

| Класс правки | Тесты | До запуска |
| --- | --- | --- |
| любой файл `tools/`, `schemas/`, `skills/`, `evals/`, `contracts/` | `test_pack_profile`, `test_contract_registry`, `test_release_manifest` | пересобрать манифест **из `git archive HEAD`** (LF), не из CRLF-дерева: закоммитить, распаковать архив, `python -m tools.release_manifest` в нём, скопировать `release/manifest.json` обратно |
| новая или удалённая схема, правка движка схем | `test_schema_engine`, `test_schema_engine_differential`, `test_schema_validation_strict`, `test_schema_closed_world`; `SCHEMA_ROWS` в `test_contract_registry`, если схема в контракте; **плюс `-m "contract and slow"`** | схема — в `schema_registry` файла `contracts/pipeline.json` или, с причиной, в `OUTSIDE_THE_CONTRACT` (`tests/contract/schema_registry.py`); числа вроде «44» нигде править не надо |
| правка SKILL, references или assets роли | `tests/contract/test_role_bytes`, `test_review_view`, `test_skeleton_budget`, `test_p8a2_texts`, `test_texts_cases_only`, `test_fat3`, `test_test_design`, `test_context_batches`, тексты (11 файлов группы «texts» в `members.py`) | знать бюджет роли: `tests/contract/role_bytes.py`; при падении тест печатает файлы роли, размеры и превышение |
| новый или изменённый ключ вывода CLI | `tests/contract/test_api_form` (снимок), `test_api_cli`, `test_api_error_fields`, `test_next_step_commands` | `grep -rn "\"<ключ>\"" tests/` — читатели ключа (41 файл читает `result.*`, 19 подменяют `run_pipeline._print`); снимок: `UPDATE_CONTRACT_SNAPSHOT=1 python -m pytest tests/contract/test_api_form.py`, прогон один раз падает намеренно — посмотреть diff `tests/contract/api-result-keys.json`, закоммитить, прогнать снова |
| новая или переименованная команда `tools.api` | `tests/contract/test_api_commands`, `tests/contract/test_api_form`, `test_api_cli`, `test_next_step_commands`, `test_texts_cases_only`, `test_pt2` | назвать команду в SKILL (иначе `test_api_commands` требует причины в `SERVICE_COMMANDS`); если фикстуры не доводят команду до успеха — запись в `NOT_DRIVEN` с причиной |
| правка режима Б (`exec_*`, materialize/ingest, finalize, автотесты) | `test_exec_prepares`, `test_partial_automation`, `test_abandon_closure`, `test_quality_hints`, `test_cases_import`, `test_finish`; **плюс `-m "contract and slow"`** | режим Б не входит в поставку cases-only: в копии профиля его команд нет, тесты бегут на репозитории |
| перенос текста между файлами (SKILL ↔ references, mode-b) | тексты группы «texts», `test_texts_cases_only`, `test_p8a2_texts`, `test_clear_refusals` | grep по первой строке переносимого абзаца в `tests/`: читатели берут подстроку; ссылки `[…](references/…)` не должны оборваться |
| смена состава профиля поставки (что входит в cases-only) | `test_pack_profile`, `test_p8a2_*`, `test_sec1`, `test_mode_b_split`, `test_texts_cases_only` | `grep -rn "<имя модуля>" tests/ tools/` по каждому модулю, который уходит из профиля или приходит в него (урок F6: читатель на `test_p8a2_no_git` оборвался на модуле, которого в копии нет); манифест — как в первой строке |
| `contracts/pipeline.json` | `test_contract_registry`, `test_projection_drift`, `test_isolation_contract`, `test_release_manifest` | `python -m tools.render_contract_docs`, затем манифест |

## Правила набора

- Файл входит в smoke строкой в `tests/contract/members.py` **и** меткой в файле; `tests/contract/test_contract_set.py` не даёт им разойтись.
- Тест, который портит файл, портит **копию** в `tmp_path` (пример: `_copy_of_the_pack` в `test_contract_registry`), никогда репозиторий — иначе `-n` даёт ложные падения.
- Корень пакета в тесте — `Path(__file__).resolve().parents[1]`, а не `Path.cwd()`: тесты не зависят от каталога запуска.
- Бюджеты ролей проверяются в `tests/contract/test_role_bytes.py` везде; бюджет вида ревьюера в `test_review_view` — только там, где лежит прогон Petclinic.
