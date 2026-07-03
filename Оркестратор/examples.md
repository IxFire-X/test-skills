# Примеры: Оркестратор

## Пример 1: Полный цикл документации (documentation-pipeline)

### Вход

```
Создай документацию для FeatureFlag и проверь её
```

### Определение пайплайна

```
Анализ цели: "создай документацию" → documentation-pipeline
```

### Выполнение

**Итерация 1:**

| Шаг | Скилл | Статус | Выход |
|-----|-------|--------|-------|
| 1. Анализ концепции | concept-analysis | ✅ production-ready | docs/to_do/FEATURE-FLAG.md |
| 2. Ревью документации | docs-review | ⚠️ not-ready (2 critical) | docs/to_do/doc-review-FEATURE-FLAG.md |
| 3. Фикс документации | doc-fix | ✅ fixed | docs/to_do/FEATURE-FLAG.md |
| 4. Верификация | docs-review | ✅ production-ready | docs/to_do/doc-review-FEATURE-FLAG-v2.md |

### Выход

```xml
<orchestration_result>
  <status>completed</status>
  <pipeline_name>documentation-pipeline</pipeline_name>
  <iterations>1</iterations>
  <steps>
    <step>
      <name>Анализ концепции</name>
      <skill>concept-analysis</skill>
      <status>success</status>
      <output>docs/to_do/FEATURE-FLAG.md</output>
    </step>
    <step>
      <name>Ревью документации</name>
      <skill>docs-review</skill>
      <status>success</status>
      <output>docs/to_do/doc-review-FEATURE-FLAG.md</output>
    </step>
    <step>
      <name>Фикс документации</name>
      <skill>doc-fix</skill>
      <status>success</status>
      <output>docs/to_do/FEATURE-FLAG.md</output>
    </step>
    <step>
      <name>Верификация</name>
      <skill>docs-review</skill>
      <status>success</status>
      <output>docs/to_do/doc-review-FEATURE-FLAG-v2.md</output>
    </step>
  </steps>
  <final_result>docs/to_do/FEATURE-FLAG.md</final_result>
  <warnings>[]</warnings>
</orchestration_result>
```

---

## Пример 2: С итерациями (повторный фикс)

### Вход

```
Проанализируй LegacyAuth и исправь все ошибки
```

### Выполнение

**Итерация 1:**

| Шаг | Скилл | Статус | Выход |
|-----|-------|--------|-------|
| 1. Анализ концепции | concept-analysis | ⚠️ partial (45%) | docs/to_do/LEGACY-AUTH.md |
| 2. Ревью документации | docs-review | ❌ not-ready (3 critical) | docs/to_do/doc-review-LEGACY-AUTH.md |
| 3. Фикс документации | doc-fix | ⚠️ partial-fixed (2/3) | docs/to_do/LEGACY-AUTH.md |
| 4. Верификация | docs-review | ❌ not-ready (1 critical) | — |

**Решение:** Запустить итерацию 2

**Итерация 2:**

| Шаг | Скилл | Статус | Выход |
|-----|-------|--------|-------|
| 1. Фикс документации | doc-fix | ✅ fixed | docs/to_do/LEGACY-AUTH.md |
| 2. Верификация | docs-review | ✅ production-ready | docs/to_do/doc-review-LEGACY-AUTH-v2.md |

### Выход

```xml
<orchestration_result>
  <status>completed</status>
  <pipeline_name>documentation-pipeline</pipeline_name>
  <iterations>2</iterations>
  <final_result>docs/to_do/LEGACY-AUTH.md</final_result>
  <warnings>
    <warning>Потребовалось 2 итерации для достижения production-ready</warning>
  </warnings>
</orchestration_result>
```

---

## Пример 3: Только ревью (review-pipeline)

### Вход

```
Проверь документацию API.md
```

### Определение пайплайна

```
Анализ цели: "проверь документацию" → review-pipeline
```

### Выполнение

**Итерация 1:**

| Шаг | Скилл | Статус | Выход |
|-----|-------|--------|-------|
| 1. Ревью документации | docs-review | ✅ production-ready | docs/to_do/doc-review-API.md |

### Выход

```xml
<orchestration_result>
  <status>completed</status>
  <pipeline_name>review-pipeline</pipeline_name>
  <iterations>1</iterations>
  <steps>
    <step>
      <name>Ревью документации</name>
      <skill>docs-review</skill>
      <status>success</status>
      <output>docs/to_do/doc-review-API.md</output>
    </step>
  </steps>
  <final_result>docs/API.md</final_result>
  <warnings>[]</warnings>
</orchestration_result>
```

---

## Пример 4: Достигнут лимит итераций

### Вход

```
Создай документацию для ComplexModule
```

### Выполнение

**Итерация 1:**
- concept-analysis → partial (30%)
- docs-review → not-ready (5 critical)
- doc-fix → partial-fixed (2/5)
- Верификация → not-ready

**Итерация 2:**
- doc-fix → partial-fixed (3/5)
- Верификация → not-ready

**Итерация 3 (max_iterations = 3):**
- doc-fix → partial-fixed (4/5)
- Верификация → not-ready

### Выход

```xml
<orchestration_result>
  <status>retry</status>
  <pipeline_name>documentation-pipeline</pipeline_name>
  <iterations>3</iterations>
  <final_result>docs/to_do/COMPLEX-MODULE.md</final_result>
  <warnings>
    <warning>Достигнут лимит итераций (3/3)</warning>
    <warning>Осталась 1 критическая проблема</warning>
  </warnings>
  <recommendations>
    <recommendation>Увеличить max_iterations до 5</recommendation>
    <recommendation>Вмешаться вручную для решения оставшейся проблемы</recommendation>
  </recommendations>
</orchestration_result>
```

---

## Пример 5: Ошибка скилла

### Вход

```
Создай документацию для NonExistentClass
```

### Выполнение

**Итерация 1:**

| Шаг | Скилл | Статус | Выход |
|-----|-------|--------|-------|
| 1. Анализ концепции | concept-analysis | ❌ failed | — |

**Ошибка:** Класс `NonExistentClass` не найден в кодовой базе.

### Выход

```xml
<orchestration_result>
  <status>failed</status>
  <pipeline_name>documentation-pipeline</pipeline_name>
  <iterations>1</iterations>
  <errors>
    <error>
      <step>Анализ концепции</step>
      <message>Класс NonExistentClass не найден</message>
      <resolution>Проверьте правильность имени класса</resolution>
    </error>
  </errors>
</orchestration_result>