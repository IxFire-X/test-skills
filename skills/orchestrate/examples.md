# Примеры: Оркестратор (test-pipeline)

## Пример 1: Полный цикл тестирования (успех с первой итерации)

### Вход

```
Создай тест-кейсы и автотесты для TransferService
```

### Выполнение

```
Contract Check: PASS

Итерация 1:
  Шаг 0 (опционально): context-marker
    → пропущен — аналитика уже в <analytics_documentation>

  Шаг 1: tc-generator
    → <generated_test_cases>: 12 ТК (8 positive, 3 negative, 1 boundary)

  Шаг 2: tc-reviewer
    → <review_verdict>: ПРИНЯТО
    → <generated_test_cases> переименован в <test_cases>

  Шаг 3: tc-to-autotest
    → <automation_analysis>: 12 ТК → 8 тестовых методов, 2 DTO
    → <automation_matrix>: ТК-1 → shouldCreateTransfer_ValidRequest()...
    → Java-файлы: TransferRequestDto.java, TransferResponseDto.java, TransferApiTest.java

  Шаг 4: autotest-reviewer
    → <review_verdict>: ПРИНЯТО
    → <autotest_review>: Traceability 100%, анти-паттернов нет, WireMock покрытие 100%

Результат: completed
```

### Выход

```xml
<orchestration_result version="2.0">
  <execution_id>a1b2c3d4-...</execution_id>
  <status>completed</status>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>1</iterations>
  <steps>
    <step>
      <name>Генерация тест-кейсов</name>
      <skill>tc-generator</skill>
      <status>success</status>
      <output>docs/to_do/test-cases-TransferService.md</output>
    </step>
    <step>
      <name>Валидация тест-кейсов</name>
      <skill>tc-reviewer</skill>
      <status>success</status>
      <output>docs/to_do/test-cases-review-TransferService.md</output>
    </step>
    <step>
      <name>Генерация автотестов</name>
      <skill>tc-to-autotest</skill>
      <status>success</status>
      <output>src/test/java/.../TransferApiTest.java</output>
    </step>
    <step>
      <name>Валидация автотестов</name>
      <skill>autotest-reviewer</skill>
      <status>success</status>
      <output>docs/to_do/autotest-review-TransferService.md</output>
    </step>
  </steps>
  <final_result>src/test/java/.../TransferApiTest.java</final_result>
  <warnings>[]</warnings>
</orchestration_result>
```

---

## Пример 2: С автофиксом на этапе валидации тест-кейсов

### Вход

```
Сгенерируй тест-кейсы и автотесты для RenewalService, аналитика в docs/requirements/renewal.md
```

### Выполнение

```
Contract Check: PASS

Итерация 1:
  Шаг 0: context-marker
    → обнаружен сырой .md без XML-разметки → разметка выполнена
    → <analytics_documentation>: docs/requirements/renewal.md обёрнут в тег

  Шаг 1: tc-generator
    → <generated_test_cases>: 15 ТК

  Шаг 2: tc-reviewer
    → <review_verdict>: AUTO_FIX_APPLIED
    → <corrected_test_cases>: 15 ТК (исправлены лимиты шагов в ТК-4, ТК-9)

  Шаг 3: tc-to-autotest
    → <automation_matrix>: 15 ТК → 10 тестовых методов
    → Java-файлы: RenewalRequestDto.java, RenewalApiTest.java

  Шаг 4: autotest-reviewer
    → <review_verdict>: ПРИНЯТО
```

### Выход

```xml
<orchestration_result version="2.0">
  <status>completed</status>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>1</iterations>
  <final_result>src/test/java/.../RenewalApiTest.java</final_result>
  <warnings>
    <warning>tc-reviewer: применён AUTO_FIX (2 дефекта исправлены автоматически)</warning>
  </warnings>
</orchestration_result>
```

---

## Пример 3: Эскалация — ТРЕБУЕТ ДОРАБОТКИ

### Вход

```
Запусти тестовый пайплайн для LegacyAuthService
```

### Выполнение

```
Contract Check: PASS

Итерация 1:
  Шаг 1: tc-generator
    → <generated_test_cases>: 9 ТК

  Шаг 2: tc-reviewer
    → <review_verdict>: ТРЕБУЕТ ДОРАБОТКИ (2 критических дефекта без автофикса)
    → <review_comments>: ТК-3 — недетерминированный шаг ("ИЛИ"); ТК-7 — отсутствует техническая точность (код ошибки не из кодовой базы)

Пайплайн приостановлен: требуется ручная доработка или подтверждение пользователя.
```

### Выход

```xml
<orchestration_result version="2.0">
  <status>partial</status>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>1</iterations>
  <warnings>
    <warning>tc-reviewer: ТРЕБУЕТ ДОРАБОТКИ — пайплайн приостановлен на шаге 2</warning>
  </warnings>
  <recommendations>
    <recommendation>Исправить ТК-3 и ТК-7 вручную по <review_comments></recommendation>
    <recommendation>Подтвердить продолжение пайплайна после правки</recommendation>
  </recommendations>
</orchestration_result>
```

---

## Пример 4: Достигнут лимит итераций

### Вход

```
Создай тест-кейсы и автотесты для ComplexPaymentModule
```

### Выполнение

```
Итерация 1:
  tc-generator → 20 ТК
  tc-reviewer → ТРЕБУЕТ ДОРАБОТКИ (5 критических)
  Пользователь подтвердил правку → повторный запуск

Итерация 2:
  tc-reviewer (повторно) → ТРЕБУЕТ ДОРАБОТКИ (2 критических)
  Пользователь подтвердил правку → повторный запуск

Итерация 3 (max_iterations = 3):
  tc-reviewer (повторно) → ТРЕБУЕТ ДОРАБОТКИ (1 критический)
```

### Выход

```xml
<orchestration_result version="2.0">
  <status>retry</status>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>3</iterations>
  <warnings>
    <warning>Достигнут лимит итераций (3/3)</warning>
    <warning>Осталась 1 критическая проблема в tc-reviewer</warning>
  </warnings>
  <recommendations>
    <recommendation>Увеличить max_iterations до 5</recommendation>
    <recommendation>Вмешаться вручную для решения оставшейся проблемы</recommendation>
  </recommendations>
</orchestration_result>
```

---

## Пример 5: Ошибка Contract Check (contract_mismatch)

### Вход

```
Запусти тестовый пайплайн для NewFeatureModule
```

### Выполнение

```
Contract Check: FAILED
  Пара: tc-reviewer → tc-to-autotest
  Ожидаемый тег (CONTRACTS.md §2): <corrected_test_cases> / <test_cases>
  Фактический тег в SKILL.md tc-to-autotest: <validated_cases> (устаревшее имя)

Пайплайн заблокирован до исправления контракта.
```

### Выход

```xml
<orchestration_result version="2.0">
  <status>failed</status>
  <reason>contract_mismatch</reason>
  <detail>skill=tc-reviewer → tc-to-autotest: missing <test_cases>, найден устаревший тег <validated_cases></detail>
  <pipeline_name>test-pipeline</pipeline_name>
  <iterations>0</iterations>
</orchestration_result>
```

---

*См. также: [SKILL.md](SKILL.md) — полная спецификация Оркестратора, [`../PIPELINE.md`](../PIPELINE.md) — схема пайплайна, [`../CONTRACTS.md`](../CONTRACTS.md) — канон тегов и статус-маркеров.*
