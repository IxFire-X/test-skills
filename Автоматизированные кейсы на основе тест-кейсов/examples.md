# Примеры: ТК (Zephyr) → Java автотест

Полный эталон для скилла `tc-to-autotest`. Показывает парсинг входа, `<automation_analysis>`, `<automation_matrix>` и целевой код.

---

## Вход: ручной тест-кейс (после tc-reviewer)

```xml
<corrected_test_cases>
<![CDATA[
# Тест-кейсы метода POST /api/v1/orders

**Документация:** https://wiki.example.com/ORD-100
**Project:** BILLING
**Автор:** Gigacode
**Дата:** 2026-06-30

---

## ТК-1: Успешное создание заявки с обязательными полями

**Цель:** Проверить happy path создания заявки через API.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` с телом запроса | `{ "orderId": "ORD-001", "amount": 100, "currency": "RUB" }` | HTTP 201, в теле `orderId=ORD-001`, `status=CREATED` |
| 2 | Проверить запись в таблице `orders` | `SELECT status FROM orders WHERE order_id = 'ORD-001'` | Одна строка, `status = 'CREATED'` |

## ТК-2: Отклонение запроса без обязательного поля amount

**Цель:** Проверить валидацию обязательного поля `amount`.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` без поля `amount` | `{ "orderId": "ORD-002", "currency": "RUB" }` | HTTP 400, код ошибки `VALIDATION_ERROR`, текст содержит `amount is required` |
]]>
</corrected_test_cases>

<existing_project_context>
Проект с нуля
</existing_project_context>

<source_code_and_diff>
- Controller: OrderController.createOrder()
- Exception: ValidationException → HTTP 400, code VALIDATION_ERROR
- Таблица БД: orders (order_id, status, amount, currency)
</source_code_and_diff>
```

---

## Выход: анализ и traceability

```xml
<automation_analysis>
1. Источник ТК: corrected_test_cases
2. Список ТК-N: ТК-1, ТК-2
3. Группы @ParameterizedTest: нет (разные поля/сценарии)
4. TODO из ⚠: нет
5. План файлов: CreateOrderRequest, CreateOrderResponse, CreateOrderTest (+ инфраструктура — проект с нуля)
</automation_analysis>

<automation_matrix>
ТК-1 -> shouldCreateOrderSuccessfully()
ТК-2 -> shouldRejectMissingAmount()
</automation_matrix>
```

---

## Выход: DTO запроса

> `src/test/java/ru/company/billing/model/request/CreateOrderRequest.java`

```java
package ru.company.billing.model.request;

import com.fasterxml.jackson.annotation.JsonInclude;
import lombok.Builder;
import lombok.Data;

@Data
@Builder(toBuilder = true)
@JsonInclude(JsonInclude.Include.NON_NULL)
public class CreateOrderRequest {
    private String orderId;
    private Integer amount;
    private String currency;
}
```

---

## Выход: DTO ответа

> `src/test/java/ru/company/billing/model/response/CreateOrderResponse.java`

```java
package ru.company.billing.model.response;

import lombok.Data;

@Data
public class CreateOrderResponse {
    private String orderId;
    private String status;
}
```

---

## Выход: тест-класс

> `src/test/java/ru/company/billing/tests/CreateOrderTest.java`

```java
package ru.company.billing.tests;

import io.qameta.allure.Feature;
import io.qameta.allure.Step;
import io.restassured.http.ContentType;
import org.assertj.core.api.SoftAssertions;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import ru.company.billing.base.BaseApiTest;
import ru.company.billing.model.request.CreateOrderRequest;
import ru.company.billing.model.response.CreateOrderResponse;

import java.util.UUID;

import static io.restassured.RestAssured.given;
import static io.restassured.module.jsv.JsonSchemaValidator.matchesJsonSchemaInClasspath;
import static org.assertj.core.api.Assertions.assertThat;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.equalTo;

@Feature("Orders API")
class CreateOrderTest extends BaseApiTest {

    private static final String ENDPOINT = "/api/v1/orders";

    private CreateOrderRequest defaultRequest() {
        return CreateOrderRequest.builder()
                .orderId("ORD-" + UUID.randomUUID())
                .amount(100)
                .currency("RUB")
                .build();
    }

    @Test
    @Tag("positive")
    @DisplayName("ТК-1: Успешное создание заявки с обязательными полями")
    void shouldCreateOrderSuccessfully() {
        CreateOrderRequest request = defaultRequest().toBuilder()
                .orderId("ORD-001")
                .build();

        CreateOrderResponse response = sendCreateOrder(request);

        SoftAssertions.assertSoftly(softly -> {
            softly.assertThat(response.getOrderId())
                    .as("orderId в ответе")
                    .isEqualTo("ORD-001");
            softly.assertThat(response.getStatus())
                    .as("status в ответе")
                    .isEqualTo("CREATED");
        });

        // TODO: JDBC-проверка orders.status — см. ТК-1 шаг 2 (если коннект не настроен в BaseApiTest)
    }

    @Test
    @Tag("negative")
    @DisplayName("ТК-2: Отклонение запроса без обязательного поля amount")
    void shouldRejectMissingAmount() {
        CreateOrderRequest request = defaultRequest().toBuilder()
                .orderId("ORD-002")
                .amount(null)
                .build();

        given()
                .contentType(ContentType.JSON)
                .body(request)
        .when()
                .post(ENDPOINT)
        .then()
                .statusCode(400)
                .body("code", equalTo("VALIDATION_ERROR"))
                .body("message", org.hamcrest.Matchers.containsString("amount is required"));
    }

    @Step("POST {endpoint}")
    private CreateOrderResponse sendCreateOrder(CreateOrderRequest request) {
        return given()
                .contentType(ContentType.JSON)
                .body(request)
        .when()
                .post(ENDPOINT)
        .then()
                .statusCode(201)
                .body(matchesJsonSchemaInClasspath("schemas/create_order_response.json"))
                .extract()
                .as(CreateOrderResponse.class);
    }
}
```

---

## Ключевые соответствия (чеклист эталона)

| Правило скилла | Как отражено в примере |
|---|---|
| `ТК-N` → `@DisplayName` | `"ТК-1: ..."`, `"ТК-2: ..."` |
| HTTP path из шапки документа | `ENDPOINT = "/api/v1/orders"` |
| Object Mother | `defaultRequest()` + `toBuilder()` |
| Soft Assertions на DTO | `assertSoftly` в ТК-1 |
| JSON Schema до бизнес-проверок | `matchesJsonSchemaInClasspath` в `sendCreateOrder` |
| Имена из `<source_code_and_diff>` | `VALIDATION_ERROR`, `orders`, `CREATED` |
| JDBC из ТК → TODO если нет коннекта | комментарий в ТК-1 |
| `<automation_matrix>` | каждый `ТК-N` имеет метод |

---

## Пример группировки @ParameterizedTest

Если на входе три ТК на валидацию одного поля `amount`:

- ТК-3: `amount` отсутствует (поле не в JSON) → 400
- ТК-4: `amount = 0` (граничное значение, должно быть > 0) → 400
- ТК-5: `amount = -1` (отрицательное значение) → 400

Один метод:

```java
@ParameterizedTest(name = "ТК-{0}: amount validation → {2}")
@MethodSource("invalidAmountCases")
@Tag("negative")
void shouldRejectInvalidAmount(String tcId, CreateOrderRequest request, int expectedStatus, String expectedCode) {
    given().contentType(ContentType.JSON).body(request)
        .when().post(ENDPOINT)
        .then().statusCode(expectedStatus).body("code", equalTo(expectedCode));
}

static Stream<Arguments> invalidAmountCases() {
    return Stream.of(
        Arguments.of("3", defaultRequest().toBuilder().amount(null).build(), 400, "VALIDATION_ERROR"),
        Arguments.of("4", defaultRequest().toBuilder().amount(0).build(), 400, "VALIDATION_ERROR"),
        Arguments.of("5", defaultRequest().toBuilder().amount(-1).build(), 400, "VALIDATION_ERROR")
    );
}
```

В `<automation_matrix>`:

```text
ТК-3, ТК-4, ТК-5 -> shouldRejectInvalidAmount() [@ParameterizedTest: field=amount]