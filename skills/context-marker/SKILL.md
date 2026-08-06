---
name: context-marker
description: >
  Препроцессор: принимает сырой .md-файл или текст и оборачивает его в XML-теги
  согласно CONTRACTS.md. Поддерживает автоопределение типа контента по ключевым
  словам и batch-режим для массовой разметки директории.
version: 1.0
language: ru
---

# СКИЛЛ: Разметка контекста (Context Marker)

**Когда применять:**
- «Разметь аналитику для пайплайна»
- «Оберни этот .md в XML-теги»
- «Подготовь контекст для генератора тест-кейсов»
- «У меня SDD-аналитика без тегов — сделай валидный вход»
- «Batch-mark всю папку docs/»

**Место в пайплайне:**
`context-marker` → `tc-generator` → `tc-reviewer` → `tc-to-autotest` → `autotest-reviewer`

**Назначение:** решает проблему «у меня есть SDD-аналитика в `.md`, но нет XML-разметки → скиллы уходят в ручной режим». Этот скилл — мост между сырыми артефактами проекта и строгими XML-контрактами мультиагентной системы.

---

## КОНТРАКТ ВХОДА

| Тег | Обязательность | Описание |
|---|---|---|
| `<raw_content>` | **Обязателен** | Сырой текст / содержимое `.md`-файла без XML-разметки |
| `<content_type>` | Опционально (по умолчанию: `auto`) | Тип контента (см. таблицу ниже). Если не указан — применяется автоопределение (режим `auto`) |
| `<file_path>` | Опционально | Путь к исходному файлу/директории (для batch-режима) |


### Допустимые значения `<content_type>`

| Значение | Что делает |
|---|---|
| `analytics` | Оборачивает в `<analytics_documentation>` |
| `source_code` | Оборачивает в `<source_code_and_diff>` |
| `test_cases` | Оборачивает в `<test_cases>` |
| `requirements` | Оборачивает в `<analytics_documentation>` + добавляет заголовок «Функциональные требования» |
| `concept` | Оборачивает в `<concept_name>` |
| `auto` | Автоопределение типа по ключевым словам в тексте |
| `batch` | Сканирование директории (`<file_path>` обязателен) → автоопределение для каждого `.md` → пакет тегов |

---

## КОНТРАКТ ВЫХОДА

Один из XML-тегов из реестра [`CONTRACTS.md`](../CONTRACTS.md) §2.2 (Входные теги):

| Входной `content_type` | Выходной XML-тег |
|---|---|
| `analytics`, `requirements` | `<analytics_documentation>...</analytics_documentation>` |
| `source_code` | `<source_code_and_diff>...</source_code_and_diff>` |
| `test_cases` | `<test_cases>...</test_cases>` |
| `concept` | `<concept_name>...</concept_name>` |
| `auto` | Один из выше (по результатам автоопределения) |
| `batch` | `<batch_marking_result>` — сводка + все обёрнутые блоки |

---

## АЛГОРИТМ АВТООПРЕДЕЛЕНИЯ ТИПА (режим `auto`)

> **Применяется, когда `<content_type> = auto` или в batch-режиме для каждого файла.**

Проверяй содержимое `<raw_content>` в порядке приоритета:

```text
1. ЕСЛИ текст содержит:
     ("Acceptance Criteria" | "AC-" | "Бизнес-требования" | "User Story" | "US-" |
      "Функциональные требования" | "Цель:" | "Предусловия")
   → тип = analytics → <analytics_documentation>

2. ЕСЛИ текст содержит:
     ("class " | "def " | "function " | "@Override" | "import " | "package " |
      "@Autowired" | "@Service" | "@RestController")
   ИЛИ расширение файла (.java | .py | .ts | .go | .kt)
   → тип = source_code → <source_code_and_diff>

3. ЕСЛИ текст содержит:
     ("## ТК-" | "Тест-кейс" | "**Предусловия**" | "**Шаги**" |
      "Ожидаемый результат" | "| № | Действие |")
   → тип = test_cases → <test_cases>

4. ИНАЧЕ:
   → тип = analytics (по умолчанию)
   → добавить комментарий: <!-- ⚠ context-marker: тип не определён автоматически, assumed analytics -->
```

---

## РЕЖИМЫ РАБОТЫ

### Режим 1: analytics / requirements / source_code / test_cases / concept (прямой)

Самый простой режим: берёт `<raw_content>`, оборачивает в указанный тег.

```xml
<analytics_documentation>
[содержимое <raw_content> без изменений — Markdown внутри XML]
</analytics_documentation>
```

### Режим 2: auto

Применяет алгоритм автоопределения. Если тип определён — оборачивает. Если нет — `assumed analytics` + комментарий-предупреждение.

### Режим 3: batch

**Требует:** `<content_type>batch</content_type>` + `<file_path>` (путь к директории).

```text
1. Прочитать список всех *.md в <file_path> (рекурсивно)
2. ДЛЯ КАЖДОГО файла:
   а. Прочитать содержимое
   б. Применить алгоритм автоопределения (режим auto)
   в. Запомнить: (путь → тип → выходной тег)
3. Вывести сводку + все обёрнутые блоки
```

**Формат вывода batch:**

```xml
<batch_marking_result>
  <summary>
    <total_files>N</total_files>
    <marked>N</marked>
    <skipped>0</skipped>
    <errors>0</errors>
  </summary>
  <items>
    <item>
      <path>docs/requirements/feature-renewal.md</path>
      <detected_type>analytics</detected_type>
      <output_tag>analytics_documentation</output_tag>
    </item>
    <!-- ... -->
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
```

---

## ПРИМЕРЫ

### Пример 1: Прямой режим (analytics)

**Вход:**
```xml
<raw_content>
# Функциональные требования: Продление подписки

## AC-1: Успешное продление
При наступлении даты окончания подписки система должна...
</raw_content>
<content_type>analytics</content_type>
```

**Выход:**
```xml
<analytics_documentation>
# Функциональные требования: Продление подписки

## AC-1: Успешное продление
При наступлении даты окончания подписки система должна...
</analytics_documentation>
```

### Пример 2: Автоопределение (auto)

**Вход:**
```xml
<raw_content>
@Test
void shouldRenewActiveSubscription() {
    // ...
}
</raw_content>
<content_type>auto</content_type>
```

**Выход:**
```xml
<source_code_and_diff>
@Test
void shouldRenewActiveSubscription() {
    // ...
}
</source_code_and_diff>
```

### Пример 3: Batch-режим

**Вход:**
```xml
<file_path>docs/requirements</file_path>
<content_type>batch</content_type>
```

...скилл читает все `.md` в `docs/requirements/`, автоопределяет тип каждого и выдаёт `<batch_marking_result>` + все обёрнутые блоки.

---

## ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ВЫВОДОМ

- [ ] Выходной тег соответствует реестру CONTRACTS.md §2.2
- [ ] Содержимое `<raw_content>` передано без искажений (Markdown внутри XML — as is)
- [ ] В режиме `auto`: тип определён или явно указан `assumed analytics` с комментарием
- [ ] В режиме `batch`: сводка `<batch_marking_result>` предшествует размеченным блокам
- [ ] Все файлы из batch-директории обработаны (нет пропущенных без `skipped`/`errors`)

---

## ИЗОЛЯЦИЯ КОНТЕКСТА

Этот скилл — **pre-processing only**. Он не знает:
- О downstream-скиллах (tc-generator, tc-reviewer и т.д.)
- О результатах предыдущих шагов пайплайна
- О семантике контента (не валидирует бизнес-логику)

Его единственная задача: **принять сырой текст → выдать обёрнутый в правильный XML-тег**.

---

## СВЯЗЬ С CONTRACTS

См. [`CONTRACTS.md`](../CONTRACTS.md):
- §2.2 — реестр входных тегов (все выходные теги этого скилла)
- §2 — реестр выходных тегов (для downstream-скиллов)

## Дополнительные ресурсы

- JSON-схема контракта (batch-режим): [`../schemas/context-marker-output.schema.json`](../schemas/context-marker-output.schema.json)


