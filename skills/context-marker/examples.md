# Примеры: Разметка контекста (context-marker)

## Пример 1: Прямой режим (analytics)

### Вход

```xml
<raw_content>
# Функциональные требования: Продление подписки

## AC-1: Успешное продление
При наступлении даты окончания подписки система должна автоматически
продлить подписку на следующий период, если способ оплаты валиден.

## AC-2: Неуспешное продление (недостаточно средств)
Если оплата отклонена — статус подписки переводится в PAST_DUE.
</raw_content>
<content_type>analytics</content_type>
```

### Выход

```xml
<analytics_documentation>
# Функциональные требования: Продление подписки

## AC-1: Успешное продление
При наступлении даты окончания подписки система должна автоматически
продлить подписку на следующий период, если способ оплаты валиден.

## AC-2: Неуспешное продление (недостаточно средств)
Если оплата отклонена — статус подписки переводится в PAST_DUE.
</analytics_documentation>
```

---

## Пример 2: Автоопределение (auto) — исходный код

### Вход

```xml
<raw_content>
@RestController
@RequestMapping("/api/v1/subscriptions")
public class SubscriptionRenewalController {

    @PostMapping("/{id}/renew")
    public ResponseEntity<RenewalResponse> renew(@PathVariable String id) {
        // ...
    }
}
</raw_content>
<content_type>auto</content_type>
```

### Выход

```xml
<source_code_and_diff>
@RestController
@RequestMapping("/api/v1/subscriptions")
public class SubscriptionRenewalController {

    @PostMapping("/{id}/renew")
    public ResponseEntity<RenewalResponse> renew(@PathVariable String id) {
        // ...
    }
}
</source_code_and_diff>
```

**Пояснение:** сработало правило №2 алгоритма автоопределения (найдены `@RestController`, `@PostMapping`, `public class`).

---

## Пример 3: Автоопределение без явного `<content_type>`

### Вход

```xml
<raw_content>
# Бизнес-требования: Отмена подписки

User Story: US-14
Как пользователь, я хочу отменить подписку в любой момент.

Acceptance Criteria:
- AC-1: Отмена доступна из личного кабинета
- AC-2: После отмены доступ сохраняется до конца оплаченного периода
</raw_content>
```

> `<content_type>` не передан → применяется значение по умолчанию `auto`.

### Выход

```xml
<analytics_documentation>
# Бизнес-требования: Отмена подписки

User Story: US-14
Как пользователь, я хочу отменить подписку в любой момент.

Acceptance Criteria:
- AC-1: Отмена доступна из личного кабинета
- AC-2: После отмены доступ сохраняется до конца оплаченного периода
</analytics_documentation>
```

---

## Пример 4: Fallback — тип не определён

### Вход

```xml
<raw_content>
Таблица миграции БД:
ALTER TABLE subscriptions ADD COLUMN grace_period_days INT DEFAULT 3;
</raw_content>
<content_type>auto</content_type>
```

### Выход

```xml
<!-- ⚠ context-marker: тип не определён автоматически, assumed analytics -->
<analytics_documentation>
Таблица миграции БД:
ALTER TABLE subscriptions ADD COLUMN grace_period_days INT DEFAULT 3;
</analytics_documentation>
```

**Пояснение:** текст не содержит ключевых слов ни аналитики, ни кода, ни тест-кейсов → fallback на `analytics` с предупреждающим комментарием.

---

## Пример 5: Batch-режим

### Вход

```xml
<file_path>docs/requirements</file_path>
<content_type>batch</content_type>
```

**Директория содержит:**
- `docs/requirements/feature-renewal.md` (бизнес-требования)
- `docs/architecture/adr-001.md` (архитектурное решение, тоже похоже на аналитику)
- `docs/requirements/legacy-snippet.md` (кусок кода, вставленный по ошибке)

### Выход

```xml
<batch_marking_result>
  <summary>
    <total_files>3</total_files>
    <marked>3</marked>
    <skipped>0</skipped>
    <errors>0</errors>
  </summary>
  <items>
    <item>
      <path>docs/requirements/feature-renewal.md</path>
      <detected_type>analytics</detected_type>
      <output_tag>analytics_documentation</output_tag>
    </item>
    <item>
      <path>docs/architecture/adr-001.md</path>
      <detected_type>analytics</detected_type>
      <output_tag>analytics_documentation</output_tag>
    </item>
    <item>
      <path>docs/requirements/legacy-snippet.md</path>
      <detected_type>source_code</detected_type>
      <output_tag>source_code_and_diff</output_tag>
    </item>
  </items>
</batch_marking_result>

<!-- ===== РАЗМЕЧЕННЫЕ БЛОКИ ===== -->

<!-- FILE: docs/requirements/feature-renewal.md -->
<analytics_documentation>
[содержимое]
</analytics_documentation>

<!-- FILE: docs/architecture/adr-001.md -->
<analytics_documentation>
[содержимое]
</analytics_documentation>

<!-- FILE: docs/requirements/legacy-snippet.md -->
<source_code_and_diff>
[содержимое]
</source_code_and_diff>
```

---

## Ключевые соответствия (чеклист эталона)

| Правило скилла | Как отражено в примерах |
|---|---|
| `<content_type>` опционален | Пример 3 — тег не передан, применён `auto` по умолчанию |
| Автоопределение по ключевым словам | Примеры 2, 3 — code / analytics определены корректно |
| Fallback с предупреждением | Пример 4 — `assumed analytics` + HTML-комментарий |
| Batch-режим — сводка + блоки | Пример 5 — `<batch_marking_result>` предшествует размеченным блокам |
| Содержимое передаётся без искажений | Во всех примерах — Markdown/код внутри XML сохранён as is |
