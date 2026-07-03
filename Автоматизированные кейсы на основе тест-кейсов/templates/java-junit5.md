# Шаблон: Java + JUnit 5 + RestAssured

> Загружается автоматически при `project.language = java` и `test.framework = junit5`
>
> **Версия: 2.0** — Критический фикс: BaseApiTest-валидация + обязательная traceability (ТК-N).

## ⚠️ STEP 0: ПРОВЕРИТЬ СУЩЕСТВОВАНИЕ BaseApiTest (ОБЯЗАТЕЛЬНО перед генерацией)

```
1. НАЙТИ `BaseApiTest.java` в проекте:
   - Поиск в `src/test/java/**/base/BaseApiTest.java`
   - Поиск в <project_context>.existing_tests
2. ЕСЛИ BaseApiTest НЕ НАЙДЕН:
   → ОСТАНОВИТЬ генерацию
   → ВЕРНУТЬ ошибку: "BaseApiTest не найден в проекте. Невозможно сгенерировать API-тесты."
   → НЕ ГЕНЕРИРОВАТЬ код, который не скомпилируется.
3. ПРОЧИТАТЬ BaseApiTest.java и ИЗВЛЕЧЬ:
   - Пакет: `package com.example.billing.base;`
   - Поля: `token`, `objectMapper`, `restTemplate`...
   - Методы-хелперы: `stubPaymentGatewaySuccess()`, `stubPaymentGatewayDeclined()`...
   - WireMock-конфигурацию: порт, базовый URL
4. ИСПОЛЬЗОВАТЬ методы из BaseApiTest:
   → `stubPaymentGatewaySuccess(token)` — для мокирования успешного платежа
   → `stubPaymentGatewayDeclined(token)` — для мокирования отклонённого платежа
   → НЕ ДУБЛИРОВАТЬ stub-методы в генерируемом коде!
```

## Соглашения

| Параметр | Значение |
|----------|----------|
| Базовый класс теста | `BaseApiTest` (проверить существование в STEP 0!) |
| Тестовый фреймворк | JUnit 5 (`@Test`, `@DisplayName`) |
| API-клиент | RestAssured (`given()`, `when()`, `then()`) |
| Проверки | AssertJ (`assertThat()`) |
| Моки | Mockito (`@MockBean`, `when()`) |
| Тестовая БД | Testcontainers / H2 |
| Именование класса | `*Test.java` |
| Префикс API | из `{{paths.api_prefix}}` или `/api` |

## 🔴 ОБЯЗАТЕЛЬНАЯ TRACEABILITY: ТК-N → @DisplayName

> **Каждый** метод с `@Test` обязан иметь `@DisplayName("ТК-N: ...")`.
> Это **НЕ опционально** — это контракт с `autotest-reviewer`, который проверяет traceability.

**Правила:**
1. Префикс `ТК-N` **обязателен** в каждом `@DisplayName` (N — номер из тест-кейса).
2. Текст после `: ` должен совпадать с заголовком тест-кейса из Markdown.
3. **НИ ОДИН** метод не может быть сгенерирован без `@DisplayName("ТК-N: ...")`.
4. Если тест-кейс не имеет номера ТК-N в Markdown — **пропустить** генерацию метода.

**Проверка после генерации (traceability checklist):**
```
ДЛЯ КАЖДОГО сгенерированного метода:
  1. Есть @DisplayName? Да / Нет
  2. Начинается с "ТК-N"? Да / Нет
  3. Номер ТК-N есть в исходном Markdown тест-кейсов? Да / Нет
  4. Заголовок совпадает с ТК? Да / Нет
ЕСЛИ любая проверка FAIL → добавить // TODO: FIX TRACEABILITY
```

## Структура тестового класса

```java
package {{test_package}};

import {{base_package}}.base.BaseApiTest;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import static org.assertj.core.api.Assertions.assertThat;
import static io.restassured.RestAssured.given;

@DisplayName("{{description}}")
class {{ClassName}}Test extends BaseApiTest {

    @Test
    @DisplayName("ТК-{{id}}: {{test_case_title}}")
    void should_{{method_name}}() {
        // given: предусловия
        {{given_block}}

        // when: действие
        var response = given()
            .contentType("application/json"){{auth_header}}
            .body({{request_body}})
        .when()
            .{{http_method}}("{{api_prefix}}{{endpoint}}")
        .then()
            .statusCode({{expected_status}})
            .extract().as({{response_type}}.class);

        // then: проверки
        {{then_block}}
    }
}
```

## Пример: базовый REST-тест

```java
@Test
@DisplayName("ТК-01: Успешный GET-запрос возвращает HTTP 200")
void should_return_200_for_valid_request() {
    var response = given()
        .contentType("application/json")
        .header("Authorization", "Bearer " + token)
    .when()
        .get("/api/v1/resource/12345")
    .then()
        .statusCode(200)
        .extract().as(ResourceDto.class);

    assertThat(response.getId()).isEqualTo("12345");
    assertThat(response.getName()).isNotBlank();
    assertThat(response.getCreatedAt()).isNotNull();
}
```

## Проверки по уровням

| Уровень | Ключевые проверки |
|---------|-------------------|
| HTTP | statusCode, header("X-Request-Id", notNullValue()), contentType |
| Тело ответа | id, поля, типы, формат дат |
| Бизнес-логика | значение полей, расчёты, связи |
| Безопасность | отсутствие паролей/токенов в ответе |
| Наблюдаемость | time(lessThan(...)), header("X-Response-Time") |

## Обработка ошибок

```java
@Test
@DisplayName("ТК-EX: Некорректный запрос возвращает HTTP 400")
void should_return_400_for_invalid_request() {
    given()
        .contentType("application/json")
        .body("{ \"invalid\": true }")
    .when()
        .post("/api/v1/resource")
    .then()
        .statusCode(400)
        .body("error", notNullValue())
        .body("message", containsString("Invalid request"));
}