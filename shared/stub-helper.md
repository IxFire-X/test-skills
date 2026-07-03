# /shared — Stub Helper Reference

> **Назначение:** единая точка правды для stub-методов. Все скиллы генерации автотестов должны использовать методы из `BaseApiTest`, НЕ дублировать их.
>
> **Версия:** 1.0

## Проблема

Аудит (`autotest-review-report.md`) выявил, что `stubPaymentGatewaySuccess` и `stubPaymentGatewayDeclined` дублируются в:
- `SubscriptionRenewalClientApiTest.java`
- `SubscriptionRenewalAdminApiTest.java`
- `SubscriptionRenewalServiceTest.java`
- `SubscriptionRenewalObservabilityTest.java`

Это нарушает принцип **DRY** и усложняет поддержку.

## Решение

Все stub-методы должны быть вынесены в `BaseApiTest.java` и вызываться оттуда через `this.stubXxx(...)` или напрямую (если `protected`).

## КАК ИСПОЛЬЗОВАТЬ (инструкция для скиллов)

### Шаг 1: Найти BaseApiTest

```
1. ПРОЧИТАТЬ <project_context>.existing_tests
2. НАЙТИ файл: src/test/java/**/base/BaseApiTest.java
3. ПРОЧИТАТЬ его содержимое
```

### Шаг 2: Извлечь доступные stub-методы

Ожидаемые сигнатуры (пример для subscription-renewal-service):

```java
// Успешный платёж
protected void stubPaymentGatewaySuccess(String token) {
    stubFor(post(urlEqualTo("/payment-gateway/charge"))
        .withHeader("Authorization", equalTo("Bearer " + token))
        .willReturn(aResponse()
            .withStatus(200)
            .withHeader("Content-Type", "application/json")
            .withBody("""
                {
                  "transactionId": "txn-test-001",
                  "status": "SUCCESS",
                  "amount": 299.00,
                  "currency": "RUB"
                }
                """)));
}

// Отклонённый платёж
protected void stubPaymentGatewayDeclined(String token) {
    stubFor(post(urlEqualTo("/payment-gateway/charge"))
        .withHeader("Authorization", equalTo("Bearer " + token))
        .willReturn(aResponse()
            .withStatus(402)
            .withHeader("Content-Type", "application/json")
            .withBody("""
                {
                  "transactionId": "txn-test-declined",
                  "status": "DECLINED",
                  "reason": "INSUFFICIENT_FUNDS"
                }
                """)));
}
```

### Шаг 3: Вызывать в тестах

```java
@Test
@DisplayName("ТК-01: Успешное автоматическое продление подписки")
void shouldRenewSubscriptionSuccessfully() {
    // given: платёжный шлюз возвращает SUCCESS
    stubPaymentGatewaySuccess(token);  // ← вызываем из BaseApiTest!

    // when: запрос на продление
    var response = given()
        .contentType("application/json")
        .header("Authorization", "Bearer " + token)
        .body(requestBody)
    .when()
        .post("/api/v1/subscriptions/renew")
    .then()
        .statusCode(200)
        .extract().as(RenewalResponseDto.class);

    // then: проверки
    assertThat(response.getStatus()).isEqualTo("ACTIVE");
}
```

### Шаг 4: НЕ ДУБЛИРОВАТЬ

**Запрещено:**
```java
// ❌ НЕ ДЕЛАТЬ: копирование stub-метода в тестовый класс
class SubscriptionRenewalClientApiTest extends BaseApiTest {
    private void stubPaymentGatewaySuccess(String token) {
        // ... дубликат кода из BaseApiTest
    }
}
```

**Правильно:**
```java
// ✅ ВЫЗЫВАТЬ метод из BaseApiTest
class SubscriptionRenewalClientApiTest extends BaseApiTest {
    @Test
    void test() {
        this.stubPaymentGatewaySuccess(token);
        // ...
    }
}
```

## КОНТРАКТ ДЛЯ autotest-reviewer

При проверке автотестов, `autotest-reviewer` должен:
1. Проверить, что `stubPaymentGatewaySuccess` / `stubPaymentGatewayDeclined` НЕ дублируются в тестовых классах.
2. Если дублирование обнаружено → предупреждение DRY.
3. Проверить, что тестовые классы вызывают `this.stubXxx(...)` или `stubXxx(...)` из `BaseApiTest`.

## КОНТРАКТ ДЛЯ tc-to-autotest

При генерации новых тестов, `tc-to-autotest` должен:
1. Использовать stub-методы из `BaseApiTest` (НЕ генерировать свои).
2. Если нужного stub-метода нет в `BaseApiTest` → добавить его туда (предложить пользователю), НЕ создавать дубликат в тестовом классе.
3. Ссылаться на этот файл `shared/stub-helper.md` в комментариях к сгенерированному коду.

---

*См. также: `BaseApiTest.java` в проекте.*