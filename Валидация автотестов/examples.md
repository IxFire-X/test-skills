# Примеры: Java-код автотестов → Вердикт (ПРИНЯТО / ТРЕБУЕТ ДОРАБОТКИ / AUTO_FIX_APPLIED)

Полный эталон для скилла `autotest-reviewer`. Показывает разбор traceability, анти-паттернов, TODO-корректности и стека.

---

## Пример 1: Вердикт ПРИНЯТО (код корректен)

### Вход: исходные ТК

```xml
<test_cases>
<![CDATA[
# Тест-кейсы метода POST /api/v1/orders

## ТК-1: Успешное создание заявки с обязательными полями
**Цель:** Проверить happy path.
**Шаги:**
1. POST /api/v1/orders с валидными данными → HTTP 201, status=CREATED
2. Проверить запись в таблице orders (order_id, status)
⚠ Запись в лог не проверяется на данном этапе интегрированного тестирования

## ТК-2: Отклонение запроса без amount
**Цель:** Проверить валидацию.
1. POST без amount → HTTP 400, VALIDATION_ERROR, текст "amount is required"

## ТК-4: Отклонение запроса с ролью VIEWER
**Цель:** Проверить ролевую модель.
1. POST от VIEWER → HTTP 403
]]>
</test_cases>

<automation_matrix>
ТК-1 → createOrder_shouldReturnCreated()
ТК-2 → createOrder_withoutAmount_shouldReturn400()
ТК-3 → createOrder_withZeroAmount_shouldReturn400()
ТК-4 → createOrder_withViewerRole_shouldReturn403()
ТК-5 → createOrder_shouldLogInfoMessage()
ТК-6 → createOrder_shouldIncrementMetric()
</automation_matrix>
```

### Вход: Java-код автотестов

```java
import static org.assertj.core.api.Assertions.assertThat;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import io.restassured.RestAssured;
import io.restassured.response.ValidatableResponse;
import static io.restassured.RestAssured.given;
import static com.github.tomakehurst.wiremock.client.WireMock.*;

// DTO
@lombok.Data
@lombok.Builder
@lombok.NoArgsConstructor
@lombok.AllArgsConstructor
@com.fasterxml.jackson.annotation.JsonInclude(com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL)
class CreateOrderRequest {
    String orderId;
    Integer amount;
    String currency;
}

@lombok.Data
@lombok.Builder
@lombok.NoArgsConstructor
@lombok.AllArgsConstructor
class CreateOrderResponse {
    String orderId;
    String status;
}

// Тест-класс
class OrderControllerTest {

    @Test
    @DisplayName("ТК-1: Успешное создание заявки с обязательными полями")
    void createOrder_shouldReturnCreated() {
        CreateOrderRequest request = CreateOrderRequest.builder()
            .orderId("ORD-001")
            .amount(100)
            .currency("RUB")
            .build();

        ValidatableResponse response = given()
            .contentType("application/json")
            .header("X-Role", "OPERATOR")
            .body(request)
            .when()
            .post("/api/v1/orders")
            .then()
            .statusCode(201);

        CreateOrderResponse body = response.extract().as(CreateOrderResponse.class);
        assertThat(body.getOrderId()).as("orderId в ответе").isEqualTo("ORD-001");
        assertThat(body.getStatus()).as("status в ответе").isEqualTo("CREATED");

        // TODO: ⚠ JDBC-проверка записи в таблице orders (order_id, status) — если коннект не настроен в BaseApiTest
        // TODO: ⚠ проверка записи в лог на данном этапе интегрированного тестирования
    }

    @Test
    @DisplayName("ТК-2: Отклонение запроса без amount")
    void createOrder_withoutAmount_shouldReturn400() {
        CreateOrderRequest request = CreateOrderRequest.builder()
            .orderId("ORD-002")
            .currency("RUB")
            .build();

        given()
            .contentType("application/json")
            .header("X-Role", "OPERATOR")
            .body(request)
            .when()
            .post("/api/v1/orders")
            .then()
            .statusCode(400)
            .body("code", org.hamcrest.Matchers.equalTo("VALIDATION_ERROR"))
            .body("message", org.hamcrest.Matchers.containsString("amount is required"));
    }

    @Test
    @DisplayName("ТК-4: Отклонение запроса с ролью VIEWER")
    void createOrder_withViewerRole_shouldReturn403() {
        CreateOrderRequest request = CreateOrderRequest.builder()
            .orderId("ORD-004")
            .amount(100)
            .currency("RUB")
            .build();

        given()
            .contentType("application/json")
            .header("X-Role", "VIEWER")
            .body(request)
            .when()
            .post("/api/v1/orders")
            .then()
            .statusCode(403);
    }
}
```

### Выход: валидация

```xml
<autotest_review>
1. Traceability:
   - Всего ТК-N на входе: ТК-1, ТК-2, ТК-4 (3 ТК из <test_cases>)
   - Найдено в коде: ТК-1, ТК-2, ТК-4
   - Пропущено: не обнаружены
   - Примечание: ТК-3, ТК-5, ТК-6 из <automation_matrix> не переданы в <test_cases> — замечание к входу, но не к коду.
   - Покрытие шагов:
     - ТК-1 шаг 1 (POST → 201, orderId, status): ✅ покрыт (statusCode + assertThat с .as())
     - ТК-1 шаг 2 (проверка записи в orders): ✅ отражён как TODO (JDBC-коннект не настроен)
     - ТК-1 ⚠ (лог): ✅ отражён как TODO
     - ТК-2 шаг 1 (POST без amount → 400, code, message): ✅ покрыт (.body equalTo + containsString)
     - ТК-4 шаг 1 (POST от VIEWER → 403): ✅ покрыт (statusCode 403)

2. Анти-паттерны:
   - Thread.sleep: не найдено
   - Хардкод URL: не найдено (используется относительный путь /api/v1/orders)
   - Shared mutable state: не найдено
   - SoftAssertions: соблюдены (в ТК-1 два assertThat на разные поля DTO с .as() — допустимо)
   - Пустой catch: не найдено

3. TODO-корректность:
   - ⚠ во входных ТК: ТК-1 — "Запись в лог не проверяется на данном этапе"
   - Отражены в коде: да — `// TODO: ⚠ проверка записи в лог на данном этапе интегрированного тестирования`
   - Дополнительно: JDBC-проверка записи в orders также отражена как TODO (шаг 2 ТК-1)

4. WireMock-покрытие:
   - Внешние зависимости из ТК: отсутствуют (REST API напрямую)
   - Покрыты стабами: N/A.

5. Стек: соответствует.
   - JUnit 5 ✅, RestAssured ✅, AssertJ ✅, Lombok ✅, Jackson ✅
   - Hamcrest-матчеры в `.body()` (строки 119-120): допустимо — это часть API RestAssured, не отдельный assertion-фреймворк.
   - Нарушения: не обнаружены.
</autotest_review>

<review_verdict>ПРИНЯТО</review_verdict>
```

---

## Пример 2: Вердикт ТРЕБУЕТ ДОРАБОТКИ (с типовыми ошибками)

### Вход: те же ТК, что и в Примере 1

### Вход: Java-код с ошибками

```java
import static org.assertj.core.api.Assertions.assertThat;

class OrderControllerTest {

    private static String sharedOrderId;  // ← АНТИ-ПАТТЕРН: shared mutable state

    @Test
    @org.junit.jupiter.api.DisplayName("Создание заявки")  // ← НЕТ ТК-N в @DisplayName
    void testOrder() {  // ← невнятное имя метода
        // ← НЕТ ТК-4 (VIEWER) — traceability нарушена

        String json = "{\"orderId\":\"ORD-001\",\"amount\":100,\"currency\":\"RUB\"}";
        String response = given()
            .contentType("application/json")
            .header("X-Role", "OPERATOR")
            .body(json)
            .when()
            .post("http://localhost:8080/api/v1/orders")  // ← АНТИ-ПАТТЕРН: хардкод URL
            .then()
            .statusCode(201)
            .extract().asString();

        assertThat(response).contains("ORD-001");
        assertThat(response).contains("CREATED");
        assertThat(response).contains("status");  // ← ПРЕДУПРЕЖДЕНИЕ: 3 жёстких assertThat подряд без SoftAssertions

        try {
            Thread.sleep(5000);  // ← АНТИ-ПАТТЕРН: Thread.sleep вместо Awaitility
        } catch (Exception e) {
            // ← АНТИ-ПАТТЕРН: пустой catch
        }

        // ← НЕТ TODO-маркера для ⚠ "Запись в лог не проверяется на данном этапе"
    }

    // ← НЕТ метода для ТК-2 (without amount)
    // ← НЕТ метода для ТК-4 (viewer role)
}
```

### Выход: валидация

```xml
<autotest_review>
1. Traceability:
   - Всего ТК-N на входе: ТК-1, ТК-2, ТК-4 (3 ТК)
   - Найдено в коде: ТК-1 (косвенно — @DisplayName("Создание заявки") без номера)
   - Пропущено: ТК-2, ТК-4
   - КРИТИЧЕСКИ: @DisplayName не содержит «ТК-N:» — невозможно установить traceability

2. Анти-паттерны:
   - Thread.sleep: найдено — строка (Thread.sleep(5000) после extract)
   - Хардкод URL: найдено — строка (post("http://localhost:8080/api/v1/orders"))
   - Shared mutable state: найдено — private static String sharedOrderId
   - SoftAssertions: не соблюдены — 3 assertThat подряд на одну строку response без `.as(...)` и без SoftAssertions
   - Пустой catch: найдено — catch (Exception e) { } без логирования

3. TODO-корректность:
   - ⚠ во входных ТК: ТК-1 — "Запись в лог не проверяется на данном этапе"
   - Отражены в коде: НЕТ — в коде отсутствует // TODO-маркер

4. WireMock-покрытие:
   - Внешние зависимости из ТК: отсутствуют
   - Покрыты стабами: N/A

5. Стек: формально соответствует (JUnit 5, RestAssured, AssertJ).
   - Нарушения: Thread.sleep — должен быть Awaitility
</autotest_review>

<review_verdict>ТРЕБУЕТ ДОРАБОТКИ</review_verdict>

<review_comments>
### КРИТИЧЕСКИ:
- **Traceability:** @DisplayName("Создание заявки") не содержит «ТК-N:». Должен быть «ТК-1: Успешное создание заявки с обязательными полями».
- **Traceability:** ТК-2 (Отклонение без amount) и ТК-4 (VIEWER → 403) отсутствуют в коде.
- **Shared mutable state:** `private static String sharedOrderId` — тесты не должны разделять состояние.
- **Хардкод URL:** `http://localhost:8080/api/v1/orders` — должен быть относительный путь `/api/v1/orders`.
- **Thread.sleep:** `Thread.sleep(5000)` — заменить на Awaitility `await().atMost(5, SECONDS).untilAsserted(...)`.
- **Пустой catch:** `catch (Exception e) { }` — либо убрать, либо добавить логирование.
- **TODO-маркер:** ⚠ «Запись в лог не проверяется» не отражён в коде — добавить `// TODO: ⚠ проверка записи в лог на данном этапе`.

### ПРЕДУПРЕЖДЕНИЯ:
- **SoftAssertions:** 3 жёстких `assertThat(response).contains(...)` подряд — использовать `SoftAssertions.assertSoftly(soft -> { ... })` или добавить `.as("...")` для читаемости.
</review_comments>
```

---

## Пример 3: Вердикт AUTO_FIX_APPLIED (частичный автофикс)

### Вход: исходные ТК

```xml
<test_cases>
<![CDATA[
# Тест-кейсы метода POST /api/v1/orders

## ТК-1: Успешное создание заявки с обязательными полями
**Цель:** Проверить happy path.
**Шаги:**
1. POST /api/v1/orders с валидными данными → HTTP 201, status=CREATED
2. Проверить запись в таблице orders (order_id, status)

## ТК-2: Отклонение запроса без amount
**Цель:** Проверить валидацию.
1. POST без amount → HTTP 400, VALIDATION_ERROR, текст "amount is required"
]]>
</test_cases>

<automation_matrix>
ТК-1 → createOrder_shouldReturnCreated()
ТК-2 → createOrder_withoutAmount_shouldReturn400()
</automation_matrix>
```

### Вход: Java-код автотестов (с исправимыми дефектами)

```java
class OrderControllerTest {

    @Test
    @DisplayName("Создание заявки")  // ← НЕТ ТК-N
    void testOrder() {  // ← невнятное имя
        String json = "{\"orderId\":\"ORD-001\",\"amount\":100,\"currency\":\"RUB\"}";

        given()
            .contentType("application/json")
            .header("X-Role", "OPERATOR")
            .body(json)
            .when()
            .post("http://localhost:8080/api/v1/orders")  // ← хардкод URL
            .then()
            .statusCode(201)
            .extract().asString();

        // ← НЕТ TODO для шага 2 (проверка записи в orders)
    }

    @Test
    @DisplayName("ТК-2: Отклонение запроса без amount")
    void createOrder_withoutAmount_shouldReturn400() {
        // ... корректная реализация ...
    }
}
```

### Выход: валидация с автофиксом

```xml
<autotest_review>
1. Traceability:
   - ТК-1: FAIL — @DisplayName("Создание заявки") без номера → AUTO_FIX: заменено на "ТК-1: Успешное создание заявки с обязательными полями"
   - ТК-2: PASS
   - Покрытие шагов:
     - ТК-1 шаг 1: ✅ покрыт
     - ТК-1 шаг 2: FAIL — нет TODO → AUTO_FIX: добавлен `// TODO: ⚠ JDBC-проверка записи в таблице orders (order_id, status)`

2. Анти-паттерны:
   - Хардкод URL: найдено → AUTO_FIX: `http://localhost:8080/api/v1/orders` → `/api/v1/orders`
   - Имя метода: `testOrder()` → AUTO_FIX: `createOrder_shouldReturnCreated()`
   - Остальные: не найдено

3. TODO-корректность:
   - ТК-1 шаг 2 не отражён → AUTO_FIX: добавлен TODO-маркер

4. WireMock-покрытие: N/A
5. Стек: соответствует
</autotest_review>

<review_verdict>AUTO_FIX_APPLIED</review_verdict>

<corrected_code>
```java
class OrderControllerTest {

    @Test
    @DisplayName("ТК-1: Успешное создание заявки с обязательными полями")
    void createOrder_shouldReturnCreated() {
        String json = "{\"orderId\":\"ORD-001\",\"amount\":100,\"currency\":\"RUB\"}";

        given()
            .contentType("application/json")
            .header("X-Role", "OPERATOR")
            .body(json)
            .when()
            .post("/api/v1/orders")
            .then()
            .statusCode(201)
            .extract().asString();

        // TODO: ⚠ JDBC-проверка записи в таблице orders (order_id, status)
    }

    @Test
    @DisplayName("ТК-2: Отклонение запроса без amount")
    void createOrder_withoutAmount_shouldReturn400() {
        // ... корректная реализация ...
    }
}
```
</corrected_code>

<review_comments>
### Исправлено автоматически:
- @DisplayName: добавлен номер ТК
- Имя метода: `testOrder` → `createOrder_shouldReturnCreated`
- URL: убран хардкод `http://localhost:8080`
- Добавлен TODO для шага 2 ТК-1

### Остаточных дефектов: нет
</review_comments>
```

---

## Ключевые соответствия (чеклист эталона)

| Правило скилла | Как отражено в примерах |
|---|---|
| Traceability — главная проверка | Пример 2: показан разбор отсутствующих ТК-2 и ТК-4, @DisplayName без номера |
| Анти-паттерны — все 7 категорий | Пример 2: Thread.sleep, хардкод URL, shared state, пустой catch, отсутствие SoftAssertions |
| TODO-корректность | Пример 2: ⚠ из ТК не отражён → КРИТИЧЕСКИ; Пример 1: отражён → ✅ |
| WireMock-покрытие | N/A в обоих примерах (нет внешних зависимостей) |
| Стек | Пример 2: Thread.sleep (должен быть Awaitility) — зафиксировано |
| Автофикс (AUTO_FIX_APPLIED) | Пример 3: исправимые дефекты (хардкод URL, @DisplayName, имя метода, TODO) исправлены автоматически, результат в `<corrected_code>` |
| Симметрия с tc-reviewer | Та же структура: `<review>` → `<verdict>` → `<comments>`; AUTO_FIX_APPLIED аналогичен tc-reviewer |