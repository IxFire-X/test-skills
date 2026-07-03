# /shared — Trace Mapper (ТК-N ↔ Код)

> **Назначение:** единый формат маппинга ТК-N → метод теста для всех скиллов тестирования.
>
> **Версия:** 1.0

## Проблема

`tc-to-autotest` генерирует `<automation_matrix>` для маппинга ТК-N → java-методы. Но `autotest-reviewer` должен проверять traceability, а между ними нет формального контракта для формата этого маппинга.

## Решение

Определяем единый machine-readable формат для `<trace_map>`, который:
- Используется `tc-to-autotest` при генерации output
- Используется `autotest-reviewer` при проверке traceability
- Используется Оркестратором для передачи контекста между скиллами

## ФОРМАТ `<trace_map>` (v1.0)

```xml
<trace_map version="1.0" source="tc-to-autotest" timestamp="2026-07-03T17:00:00Z">
  <entry>
    <tc_id>ТК-1</tc_id>
    <class>SubscriptionRenewalClientApiTest</class>
    <method>shouldRenewSubscriptionSuccessfully</method>
    <test_type>positive</test_type>
    <status>generated</status>  <!-- generated | updated | removed | todo -->
    <notes></notes>
  </entry>
  <entry>
    <tc_id>ТК-2</tc_id>
    <class>SubscriptionRenewalServiceTest</class>
    <method>shouldAutoRetryAfterFirstFailure</method>
    <test_type>positive</test_type>
    <status>generated</status>
    <notes>Awaitility для асинхронной проверки retry</notes>
  </entry>
  <entry>
    <tc_id>ТК-4</tc_id>
    <class>SubscriptionRenewalAdminApiTest</class>
    <method>shouldRecoverSubscriptionFromGrace</method>
    <test_type>positive</test_type>
    <status>todo</status>
    <notes>TODO: уточнить expected status после recovery (ACTIVE vs IN_GRACE)</notes>
  </entry>
  <entry>
    <tc_id>ТК-EX</tc_id>
    <class>SubscriptionRenewalClientApiTest</class>
    <method>shouldReturn400ForInvalidRequest</method>
    <test_type>negative</test_type>
    <status>generated</status>
    <notes></notes>
  </entry>
</trace_map>
```

## Поля

| Поле | Тип | Обязательность | Описание |
|------|-----|---------------|----------|
| `tc_id` | string | **Да** | Идентификатор тест-кейса (ТК-N) |
| `class` | string | **Да** | Полное имя тестового класса (без package) |
| `method` | string | **Да** | Имя метода в camelCase |
| `test_type` | enum | **Да** | `positive`, `negative`, `boundary`, `security`, `observability`, `concurrency` |
| `status` | enum | **Да** | `generated`, `updated`, `removed`, `todo` |
| `notes` | string | Нет | Дополнительная информация (TODO-причины, особенности) |

## ПРАВИЛА ИСПОЛЬЗОВАНИЯ

### Для `tc-to-autotest`

При генерации автотестов, **ОБЯЗАН** сгенерировать `<trace_map>` в дополнение к `<automation_analysis>` и `<automation_matrix>`:

```xml
<automation_analysis>...</automation_analysis>
<automation_matrix>...</automation_matrix>
<trace_map version="1.0" source="tc-to-autotest">
  <!-- для каждого сгенерированного ТК-N -->
  <entry>...</entry>
</trace_map>
```

**Правила:**
1. Каждый ТК-N из входа должен иметь запись в `<trace_map>`.
2. Если ТК-N не удалось сгенерировать → `status="todo"` + `notes` с причиной.
3. Если ТК-N был удалён (конфликт case B) → `status="removed"`.
4. `class` и `method` должны точно совпадать с фактически сгенерированным кодом.

### Для `autotest-reviewer`

При проверке автотестов, **ОБЯЗАН** использовать `<trace_map>` как источник истины для проверки traceability:

```
1. ЗАГРУЗИТЬ <trace_map> из выхода tc-to-autotest
2. ДЛЯ КАЖДОГО entry в trace_map:
   a. ПРОВЕРИТЬ, что класс <class> существует в тестовом пакете
   b. ПРОВЕРИТЬ, что метод <method> существует в классе
   c. ПРОВЕРИТЬ, что @DisplayName содержит "ТК-N: ..."
   d. ПРОВЕРИТЬ, что status != "todo" (todo-методы не проверяются на traceability)
3. ЕСЛИ entry отсутствует в фактическом коде → CRITICAL: MISSING_TRACE
4. ЕСЛИ фактический @Test метод не имеет записи в trace_map → WARN: UNTRACED_TEST
```

### Для Оркестратора

При передаче контекста между `tc-to-autotest` → `autotest-reviewer`:

```
1. СОХРАНИТЬ <trace_map> в pipeline-notes.md (секция context.trace_map)
2. ПЕРЕДАТЬ как входной тег autotest-reviewer:
   <trace_map>...</trace_map>
```

## ПРИМЕР ПОЛНОГО ЦИКЛА

```
tc-generator → ТК (Markdown)
    ↓
tc-reviewer → validation_report (ПРИНЯТО)
    ↓
tc-to-autotest → <trace_map> + Java-файлы
    ↓
autotest-reviewer → использует <trace_map> для проверки traceability
```

## КОНТРАКТ С ЭТАЛОННЫМИ ПРАКТИКАМИ

- **Anthropic Context Engineering (reference:2):** `<trace_map>` — это компактная форма Note-Taking. Вместо передачи полного контекста между скиллами, передаётся только структурированный маппинг.
- **OpenAI Routines (reference:4):** `<trace_map>` обеспечивает передачу состояния между шагами рутины.
- **Machine-Readable (DeepSeek/Qwen, reference:5):** XML-формат позволяет автоматическую валидацию без LLM-парсинга.

---

*См. также: `CONTRACTS.md` (контракты скиллов), `PIPELINE.md` (последовательность выполнения).*