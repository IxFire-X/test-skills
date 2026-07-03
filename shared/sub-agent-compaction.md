# /shared — Sub-Agent Spawn & Compaction Protocol

> **Назначение:** формальный протокол делегирования шагов пайплайна изолированным sub-agent'ам и сжатия (compaction) их результатов.
>
> **Версия:** 1.0
> **Дата:** 2026-07-03
> **Цель:** Sprint 2 — A3 (Sub-Agent + Compaction): устранение "контекстной гнили" между шагами пайплайна.
>
> **Потребители:** `Оркестратор/SKILL.md` §"Механизм изоляции контекста", все скиллы тестового пайплайна.
>
> **Эталонные практики:**
> - **Anthropic Context Engineering:** Sub-Agent = изолированный контекст; Compaction = Note-Taking (сжатие до structured output)
> - **OpenAI Routines:** делегирование шагов с явными контрактами входа/выхода
> - **Google Gemini Agent Designer:** изоляция ответственности sub-agent ("устанавливай границы")
> - **Z.ai Plan-Before-Execution:** spawn sub-agent только после проверки контрактов

---

## 1. Концепция

### Проблема "контекстной гнили"

При последовательном выполнении скиллов в пайплайне без изоляции:
- Каждый следующий скилл видит ВЕСЬ контекст предыдущего (включая рассуждения, черновики, отладочные заметки)
- LLM "путается" в противоречивых данных из разных этапов
- Растёт расход токенов (каждый шаг дороже предыдущего)
- Сложнее отлаживать — ошибка на шаге N может быть вызвана "грязным" контекстом шага N-2

### Решение: Sub-Agent + Compaction

```
┌──────────────────────────────────────────────────────────────────┐
│                        ОРКЕСТРАТОР                                │
│                                                                    │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐           │
│  │  SPAWN       │───▶│  EXECUTE    │───▶│  COMPACT    │──▶ next   │
│  │  (чистый     │    │  (sub-agent │    │  (извлечь   │    step   │
│  │   контекст)  │    │   скилл)    │    │   контракт) │           │
│  └─────────────┘    └─────────────┘    └─────────────┘           │
│                                                                    │
│  Контекст sub-agent:                 Результат compaction:         │
│  • Системный промпт                  • Только корневой блок        │
│  • Контрактные теги (вход)           • Отброшены: <thought>,      │
│  • <analytics_documentation>           <analysis>, <draft>,        │
│  • <context> (опционально)             <notes>, <validation_*>     │
│                                                                    │
│  Экономия: 60–90% токенов на передаче между шагами                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 2. Sub-Agent Spawn Protocol

### 2.1 Формат spawn-запроса

Оркестратор порождает sub-agent через структурированный запрос:

```xml
<sub_agent_spawn version="1.0" timestamp="2026-07-03T17:00:00Z">
  <agent_id>tc-generator</agent_id>
  <agent_version>2.3.0</agent_version>
  <task_description>
    Сгенерировать тест-кейсы на основе аналитики subscription-renewal-service
  </task_description>
  <input_contracts>
    <!-- ТОЛЬКО контрактные теги из CONTRACTS.md §2 -->
    <analytics_documentation>...</analytics_documentation>
    <source_code_and_diff>...</source_code_and_diff>
  </input_contracts>
  <context_ceiling tokens="32000">
    <!-- Максимальный размер входного контекста для sub-agent -->
    <!-- Если вход > ceiling → compaction источника перед spawn -->
  </context_ceiling>
  <step_timeout seconds="300">
    <!-- Рекомендованный таймаут шага (декларативный) -->
  </step_timeout>
  <idempotency_key>
    <!-- sha256(source_hash + agent_id + task_description) -->
    a1b2c3d4e5f6...
  </idempotency_key>
</sub_agent_spawn>
```

### 2.2 Правила spawn

| Правило | Описание |
|---------|----------|
| **SPAWN-01: Чистый контекст** | Sub-agent получает ТОЛЬКО: системный промпт, `<input_contracts>`, `<analytics_documentation>`, опциональный `<context>`. Никаких артефактов предыдущих шагов кроме контрактных тегов. |
| **SPAWN-02: Контрактная изоляция** | Имена тегов в `<input_contracts>` строго соответствуют `CONTRACTS.md` §2. Оркестратор валидирует это на этапе Contract Check. |
| **SPAWN-03: Версионирование** | `<agent_version>` берётся из `version` поля SKILL.md скилла. Если версия < минимально требуемой → Fallback C (version_mismatch). |
| **SPAWN-04: Идемпотентность** | `<idempotency_key>` = `sha256(source_hash || agent_id || task_description)`. Повторный spawn с тем же ключом должен дать идентичный результат (декларативно). |
| **SPAWN-05: Таймаут** | `<step_timeout>` — рекомендация хосту. При превышении → Fallback G (retry, затем failed). |
| **SPAWN-06: Ceiling** | Если входной контекст > `<context_ceiling>` → compaction источника (суммаризация) перед spawn. Fallback I. |

### 2.3 Пример spawn для тестового пайплайна

```text
Оркестратор → Шаг 2 (tc-reviewer):

1. SPAWN:
   <sub_agent_spawn>
     <agent_id>tc-reviewer</agent_id>
     <agent_version>2.1.0</agent_version>
     <task_description>Провалидировать тест-кейсы</task_description>
     <input_contracts>
       <generated_test_cases><![CDATA[ ... ТК-1 ... ТК-31 ... ]]></generated_test_cases>
       <analytics_documentation>...</analytics_documentation>
     </input_contracts>
   </sub_agent_spawn>

2. EXECUTE: tc-reviewer выполняет валидацию

3. COMPACT: извлечь <validation_report> + <corrected_test_cases> (если есть)
   ОТБРОСИТЬ: <thought>, <analysis>, <draft_notes>

4. COLLECT: сохранить в step_results[1], передать на Шаг 3 (tc-to-autotest)
```

---

## 3. Compaction Protocol

### 3.1 Формат compaction

После выполнения sub-agent, Оркестратор применяет compaction:

```xml
<compaction_result version="1.0" agent_id="tc-reviewer">
  <contract_root_block>
    <!-- ТОЛЬКО корневой блок по CONTRACTS.md §2 -->
    <validation_report>...</validation_report>
  </contract_root_block>
  <aux_blocks>
    <!-- Дополнительные контрактные блоки (если есть) -->
    <corrected_test_cases>...</corrected_test_cases>
  </aux_blocks>
  <discarded_blocks>
    <!-- Перечень отброшенных служебных тегов -->
    <block name="thought" reason="internal_reasoning"/>
    <block name="analysis" reason="draft"/>
    <block name="draft_notes" reason="debug"/>
  </discarded_blocks>
  <compaction_ratio>72%</compaction_ratio>
  <!-- Экономия: (размер_до - размер_после) / размер_до * 100% -->
</compaction_result>
```

### 3.2 Правила compaction

| Правило | Описание |
|---------|----------|
| **CMP-01: Только контрактные теги** | Сохраняются ТОЛЬКО блоки из `CONTRACTS.md` §2. Всё остальное отбрасывается. |
| **CMP-02: Служебные теги — discard** | Безусловно отбрасываются: `<thought>`, `<thinking>`, `<analysis>`, `<draft>`, `<notes>`, `<scratchpad>`, `<debug>`, `<scratch>`, `<chain_of_thought>`. |
| **CMP-03: Промежуточные ревью — discard** | `<validation_report>`, `<review_comments>`, `<autotest_review>` НЕ передаются следующему скиллу (только оркестратору для quality gate). |
| **CMP-04: Приоритет аналитики** | `<analytics_documentation>` сохраняется на всём протяжении пайплайна как "источник правды". Не отбрасывается. |
| **CMP-05: Compaction-логирование** | `compaction_ratio` и список `discarded_blocks` пишутся в `step_results[i].compaction`. |
| **CMP-06: CDATA-сохранность** | CDATA-секции внутри контрактных тегов передаются "как есть", без изменений. |

### 3.3 Матрица: что сохраняется / отбрасывается

| Блок | Судьба | Причина |
|------|--------|--------|
| `<analysis_result>` | ✅ Сохранить | Контракт `concept-analysis` |
| `<review_result>` | ✅ Сохранить | Контракт `docs-review` |
| `<fix_result>` | ✅ Сохранить | Контракт `doc-fix` |
| `<generated_test_cases>` | ✅ Сохранить | Контракт `tc-generator` |
| `<validation_report>` | ❌ Discard | Промежуточный артефакт ревьюера; не нужен downstream |
| `<corrected_test_cases>` | ✅ Сохранить | Контракт `tc-reviewer` (при `AUTO_FIX_APPLIED`) |
| `<automation_analysis>` | ✅ Сохранить | Контракт `tc-to-autotest` |
| `<automation_matrix>` | ✅ Сохранить | Контракт `tc-to-autotest` |
| `<trace_map>` | ✅ Сохранить | `shared/trace-mapper.md` |
| `<autotest_review>` | ❌ Discard | Промежуточный артефакт ревьюера |
| `<review_verdict>` | ✅ Сохранить | Нужен оркестратору для quality gate |
| `<review_comments>` | ❌ Discard* | НЕ передаётся downstream, но сохраняется в отчёт оркестратора |
| `<corrected_autotest_code>` | ✅ Сохранить | Контракт `autotest-reviewer` (при `AUTO_FIX_APPLIED`) |
| `<orchestration_result>` | ✅ Сохранить | Финальный результат |
| `<thought>`, `<analysis>`, `<draft>`, `<notes>` | ❌ Discard | Внутренние рассуждения LLM |
| `<analytics_documentation>` | ✅ Сохранить | Источник правды (сквозной) |
| `<context>` | ✅ Сохранить | Пользовательский контекст (если передан) |

---

## 4. Agent Identity & Version Check

### 4.1 Agent Identity

Каждый sub-agent (скилл) идентифицируется:

```xml
<agent_identity>
  <agent_id>tc-generator</agent_id>
  <agent_version>2.3.0</agent_version>
  <capabilities>
    <capability>generate_test_cases</capability>
    <capability>zephyr_markdown</capability>
    <capability>positive_negative_boundary</capability>
  </capabilities>
  <contract_version>CONTRACTS.md v1.0</contract_version>
</agent_identity>
```

### 4.2 Version Check (Contract Check, шаг 5)

Перед spawn, Оркестратор проверяет:

```text
1. ИЗВЛЕЧЬ version из SKILL.md скилла
2. СРАВНИТЬ с минимально требуемой (из CONTRACTS.md или PIPELINE.md)
3. ЕСЛИ version < min_required:
     → Fallback C (version_mismatch)
     → strict_mode: блокировать пайплайн
     → !strict_mode: продолжить с предупреждением
4. ЕСЛИ version >= min_required:
     → SPAWN
```

### 4.3 SemVer для скиллов

| Компонент | Когда меняется |
|-----------|---------------|
| **MAJOR** | Изменение структуры контрактов (новые обязательные теги, изменение семантики статус-маркеров) |
| **MINOR** | Добавление опциональных возможностей (новые теги, новые категории проверок) |
| **PATCH** | Исправления логики, уточнения инструкций, не затрагивающие контракты |

---

## 5. Error Boundary Protocol

### 5.1 Изоляция сбоев

Sub-agent сбой **не должен** ронять весь пайплайн:

```
ЕСЛИ sub-agent вернул ошибку:
  1. ЗАФИКСИРОВАТЬ ошибку в step_results[i].error
  2. ПРИМЕНИТЬ Fallback-стратегию (CONTRACTS.md §6)
  3. ЕСЛИ retry исчерпаны:
       → status = failed
       → СОХРАНИТЬ partial_results
       → ЭСКАЛАЦИЯ пользователю
  4. НЕ передавать ошибку downstream скиллам
```

### 5.2 Типы сбоев sub-agent

| Тип сбоя | Пример | Fallback |
|----------|--------|----------|
| **Contract Violation** | Отсутствует ожидаемый корневой блок | Fallback A: retry (2x), затем failed |
| **Timeout** | Sub-agent не ответил за `step_timeout` | Fallback G: retry (1x), затем failed |
| **Invalid Output** | XML не парсится, битые теги | Fallback H: retry (2x), затем failed |
| **Context Overflow** | Вход > `context_ceiling` | Fallback I: compaction источника, retry |
| **Version Mismatch** | `agent_version` < `min_required` | Fallback C: предупреждение / блокировка |
| **Unknown Status** | Статус-маркер не из реестра | Fallback B: останов, эскалация |

---

## 6. Интеграция с пайплайном

### 6.1 Жизненный цикл sub-agent в пайплайне

```
Шаг N:
  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
  │ Contract │────▶│  SPAWN   │────▶│ EXECUTE  │────▶│ COMPACT  │──▶ Шаг N+1
  │  Check   │     │ (чистый  │     │ (sub-    │     │ (извлечь │
  │          │     │ контекст)│     │  agent)  │     │ контракт)│
  └──────────┘     └──────────┘     └──────────┘     └──────────┘
                         │                │                │
                         ▼                ▼                ▼
                    spawn_log       agent_output     compaction_log
                    (step_results)  (raw XML)        (step_results)
```

### 6.2 Пример: тестовый пайплайн с sub-agent

```text
Пайплайн: test-pipeline

Шаг 1: tc-generator
  SPAWN:  agent_id=tc-generator, input=<analytics_documentation>
  COMPACT: сохранить <generated_test_cases>, discard <thought>, <analysis>
  RESULT: step_results[0].output = <generated_test_cases>

Шаг 2: tc-reviewer
  SPAWN:  agent_id=tc-reviewer, input=<generated_test_cases> + <analytics_documentation>
  COMPACT: сохранить <validation_report> + <corrected_test_cases> (если AUTO_FIX_APPLIED)
           discard <thought>, <analysis>, <draft>
  RESULT: VERDICT=ПРИНЯТО → step_results[1].output = <corrected_test_cases>

Шаг 3: tc-to-autotest
  SPAWN:  agent_id=tc-to-autotest, input=<corrected_test_cases> + <analytics_documentation>
  COMPACT: сохранить <automation_analysis> + <automation_matrix> + <trace_map>
           discard <thought>, <draft>, <scratch>
  RESULT: step_results[2].output = <automation_matrix> + <trace_map> + Java-файлы

Шаг 4: autotest-reviewer
  SPAWN:  agent_id=autotest-reviewer, input=<automation_matrix> + <trace_map> + Java-файлы + <analytics_documentation>
  COMPACT: сохранить <autotest_review> + <review_verdict> + <corrected_autotest_code> (если AUTO_FIX_APPLIED)
           discard <thought>, <analysis>
  RESULT: VERDICT=ПРИНЯТО → completed
```

### 6.3 Ожидаемая экономия контекста

| Переход | До compaction (оценка) | После compaction (оценка) | Экономия |
|---------|----------------------|--------------------------|----------|
| `tc-generator` → `tc-reviewer` | ~8000 токенов | ~2000 токенов | **75%** |
| `tc-reviewer` → `tc-to-autotest` | ~12000 токенов | ~3000 токенов | **75%** |
| `tc-to-autotest` → `autotest-reviewer` | ~15000 токенов | ~5000 токенов | **67%** |
| **Среднее по пайплайну** | | | **72%** |

---

## 7. Совместимость с существующей архитектурой

### 7.1 Что уже есть (НЕ менять)

- **CONTRACTS.md** — канон тегов и статус-маркеров (источник истины)
- **Оркестратор SKILL.md §"Механизм изоляции контекста"** — шаги 1-8 (изоляция через чистый контекст)
- **Оркестратор SKILL.md §"Контракт передачи контекста"** — правила передачи контрактных тегов
- **shared/trace-mapper.md** — формат `<trace_map>` для traceability

### 7.2 Что добавляет этот протокол

- **Формальный spawn-формат** (ранее был неявный)
- **Compaction-правила** (какие блоки discard, какие сохранить — ранее было текстовое описание)
- **Agent Identity & Version Check** (ранее не было)
- **Error Boundary Protocol** (ранее был только Fallback-реестр без spawn-специфики)
- **Compaction-логирование** (compaction_ratio, discarded_blocks)
- **Матрица сохранить/отбросить** (явная таблица вместо текстового описания)

### 7.3 Что меняется в Оркестраторе

Только **одна ссылка** в §"Механизм изоляции контекста" между строками 716 и 717:

```markdown
> **Sub-Agent Spawn & Compaction:** формальный протокол делегирования и сжатия контекста описан в [`shared/sub-agent-compaction.md`](shared/sub-agent-compaction.md).
> Каждый шаг пайплайна — это spawn sub-agent с последующим compaction результата.
```

---

## 8. Эталонные практики (reference mapping)

| Практика | Источник | Реализация в этом протоколе |
|----------|----------|----------------------------|
| **Context Engineering / Note-Taking** | Anthropic (ref:2) | Compaction = сохранение только structured notes, отбрасывание рассуждений |
| **Agent as Tool** | OpenAI (ref:14) | Sub-agent spawn = вызов tool с формальной сигнатурой |
| **Progressive Disclosure** | Anthropic (ref:1) | Sub-agent получает только контрактные теги; детали подгружаются при необходимости |
| **Boundary Setting** | Google Gemini (ref:8) | Error Boundary Protocol — изоляция сбоев sub-agent |
| **Plan-Before-Execution** | Z.ai (ref:9) | Contract Check перед spawn; spawn только после валидации |
| **JSON Schema for Parameters** | DeepSeek/Qwen (ref:5) | Структурированный spawn-формат с явными полями |
| **Routines & State Transfer** | OpenAI (ref:4) | Compaction = передача состояния между шагами рутины |

---

*См. также: `CONTRACTS.md` (канон тегов и статусов), `Оркестратор/SKILL.md` §"Механизм изоляции контекста", `shared/trace-mapper.md` (формат trace_map).*