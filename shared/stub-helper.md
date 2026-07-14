# /shared — Stub Helper Reference

> **Назначение:** единая точка правды для stub-методов. Все скиллы генерации автотестов должны использовать методы из `BaseApiTest`, НЕ дублировать их.
>
> **Версия:** 2.0 — универсализирован (убрана жёсткая привязка к конкретному проекту)

## Проблема

Аудит выявил, что stub-методы для мокирования внешних зависимостей (например, платёжных шлюзов, сервисов уведомлений и т.д.) часто дублируются в нескольких тестовых классах одного проекта:
- `{Feature}ClientApiTest.java`
- `{Feature}AdminApiTest.java`
- `{Feature}ServiceTest.java`
- `{Feature}ObservabilityTest.java`

Это нарушает принцип **DRY** и усложняет поддержку.

## Решение

Все stub-методы должны быть вынесены в `BaseApiTest.java` (или его аналог в другом языке/фреймворке) и вызываться оттуда через `this.stubXxx(...)` или напрямую (если `protected`).

## КАК ИСПОЛЬЗОВАТЬ (инструкция для скиллов)

### Шаг 1: Найти BaseApiTest

```
1. ПРОЧИТАТЬ <project_context>.existing_tests
2. НАЙТИ файл: src/test/java/**/base/BaseApiTest.java (или эквивалент для другого стека)
3. ПРОЧИТАТЬ его содержимое
```

### Шаг 2: Извлечь доступные stub-методы

Общий паттерн сигнатуры stub-метода для WireMock (пример — обобщённый внешний сервис):

```java
// Успешный ответ внешнего сервиса
protected void stubExternalServiceSuccess(String token) {
    stubFor(post(urlEqualTo("/external-service/endpoint"))
        .withHeader("Authorization", equalTo("Bearer " + token))
        .willReturn(aResponse()
            .withStatus(200)
            .withHeader("Content-Type", "application/json")
            .withBody("""
                {
                  "id": "test-id-001",
                  "status": "SUCCESS"
                }
                """)));
}

// Ошибочный ответ внешнего сервиса
protected void stubExternalServiceDeclined(String token) {
    stubFor(post(urlEqualTo("/external-service/endpoint"))
        .withHeader("Authorization", equalTo("Bearer " + token))
        .willReturn(aResponse()
            .withStatus(402)
            .withHeader("Content-Type", "application/json")
            .withBody("""
                {
                  "id": "test-id-declined",
                  "status": "DECLINED",
                  "reason": "REASON_CODE"
                }
                """)));
}
```

> **Примечание:** конкретные имена методов, эндпоинты и структуры тела ответа зависят от предметной области проекта. Скилл ОБЯЗАН извлекать реальные сигнатуры из `BaseApiTest.java` конкретного проекта (Шаг 1), а не использовать имена из этого примера буквально.

### Шаг 3: Вызывать в тестах

```java
@Test
@DisplayName("ТК-01: Успешный сценарий с внешней зависимостью")
void shouldProcessSuccessfully() {
    // given: внешний сервис возвращает SUCCESS
    stubExternalServiceSuccess(token);  // ← вызываем из BaseApiTest!

    // when: запрос
    var response = given()
        .contentType("application/json")
        .header("Authorization", "Bearer " + token)
        .body(requestBody)
    .when()
        .post("/api/v1/resource")
    .then()
        .statusCode(200)
        .extract().as(ResponseDto.class);

    // then: проверки
    assertThat(response.getStatus()).isEqualTo("SUCCESS");
}
```

### Шаг 4: НЕ ДУБЛИРОВАТЬ

**Запрещено:**
```java
// ❌ НЕ ДЕЛАТЬ: копирование stub-метода в тестовый класс
class FeatureClientApiTest extends BaseApiTest {
    private void stubExternalServiceSuccess(String token) {
        // ... дубликат кода из BaseApiTest
    }
}
```

**Правильно:**
```java
// ✅ ВЫЗЫВАТЬ метод из BaseApiTest
class FeatureClientApiTest extends BaseApiTest {
    @Test
    void test() {
        this.stubExternalServiceSuccess(token);
        // ...
    }
}
```

## КОНТРАКТ ДЛЯ autotest-reviewer

При проверке автотестов, `autotest-reviewer` должен:
1. Проверить, что stub-методы для внешних зависимостей (по паттерну `stub*Success` / `stub*Declined`/`stub*Failed` и аналогичным) НЕ дублируются в нескольких тестовых классах одного проекта.
2. Если дублирование обнаружено → предупреждение DRY (категория `dry_violation`, см. `autotest-reviewer-output.schema.json`).
3. Проверить, что тестовые классы вызывают `this.stubXxx(...)` или `stubXxx(...)` из `BaseApiTest`, а не определяют собственную копию метода.

## КОНТРАКТ ДЛЯ tc-to-autotest

При генерации новых тестов, `tc-to-autotest` должен:
1. Использовать stub-методы из `BaseApiTest` (НЕ генерировать свои), если такой метод уже существует в проекте.
2. Если нужного stub-метода нет в `BaseApiTest` → добавить его туда (предложить пользователю), НЕ создавать дубликат в тестовом классе.
3. Ссылаться на этот файл `shared/stub-helper.md` в комментариях к сгенерированному коду.

---

*См. также: `BaseApiTest.java` (или эквивалент) в конкретном проекте — реальные имена stub-методов и эндпоинтов извлекаются оттуда, а не из этого документа.*
