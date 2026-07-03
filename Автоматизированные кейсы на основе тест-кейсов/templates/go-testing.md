# Шаблон: Go + testing + net/http

> Загружается автоматически при `project.language = go`
>
> **Версия: 2.0** — Критический фикс: проверка test helpers + обязательная traceability (ТК-N) + анти-паттерны.

## ⚠️ STEP 0: ПРОВЕРИТЬ СУЩЕСТВОВАНИЕ test helpers (ОБЯЗАТЕЛЬНО перед генерацией)

```
1. НАЙТИ test helpers в проекте:
   - Поиск `*_test.go` файлов с общими функциями:
     `testhelpers_test.go`, `setup_test.go`, `common_test.go`, `apitest_test.go`
   - Поиск в <existing_project_context>.test_structure
2. ПРОЧИТАТЬ test helper-файлы и ИЗВЛЕЧЬ:
   - Константы: `testBaseURL`, `testToken`, `testTimeout`
   - Функции-хелперы: `newTestRequest()`, `doRequest()`, `parseResponse()`
   - Функции инициализации: `TestMain(m *testing.M)`
   - Мок-сервер: `httptest.NewServer()`, конфигурация `gomock`
3. ЕСЛИ test helpers НЕ НАЙДЕНЫ:
   → ПРЕДУПРЕДИТЬ пользователя: "Не найдены базовые test helpers. Тесты будут использовать локальные константы (может привести к дублированию)."
   → Сгенерировать константы в отдельном файле `*_test.go` с комментарием AUTO-GENERATED.
4. ПРОВЕРИТЬ, что константы НЕ дублируются в генерируемом коде:
   → `testBaseURL` — определяется в test helpers, НЕ переопределять
   → `testToken` — определяется в test helpers, НЕ хардкодить токен
```

## Соглашения

| Параметр | Значение |
|----------|----------|
| Test helpers | `*_test.go` общие функции (проверить существование в STEP 0!) |
| Тестовый фреймворк | `testing` (стандартная библиотека) + `github.com/stretchr/testify` |
| API-клиент | `net/http` + `io.ReadAll` |
| Проверки | `assert.Equal`, `assert.NotNil`, `require.NoError` (testify) |
| Моки | `gomock` (`go.uber.org/mock`) / `httptest.NewServer()` |
| HTTP-моки | `net/http/httptest` (встроенный) |
| Тестовая БД | `testcontainers-go` или `database/sql` + Docker |
| Именование файла | `*_test.go` |
| Именование функции | `Test_TK_{id}_Should_{Description}` |
| Префикс API | из `{{paths.api_prefix}}` или `/api` |

## 🔴 ОБЯЗАТЕЛЬНАЯ TRACEABILITY: ТК-N → имя тестовой функции

> **Каждая** тестовая функция обязана содержать `ТК-N` в имени.
> Это **НЕ опционально** — это контракт с `autotest-reviewer`, который проверяет traceability.

**Правила:**
1. Имя функции: `Test_TK_{id}_Should_{Description}` (PascalCase, underscore-разделитель).
   - Пример: `Test_TK_01_Should_Return200_ForValidRequest`
2. **Каждая** тестовая функция должна иметь doc-комментарий: `// ТК-{id}: {test_case_title}`.
3. **НИ ОДНА** тестовая функция не может быть сгенерирована без `ТК-N` в имени.
4. Если тест-кейс не имеет номера ТК-N в Markdown — **пропустить** генерацию функции.
5. Использовать `t.Run()` для субтестов, сохраняя traceability:
   ```go
   t.Run("ТК-01: Успешный GET-запрос возвращает HTTP 200", func(t *testing.T) {
       // тело теста
   })
   ```

**Проверка после генерации (traceability checklist):**
```
ДЛЯ КАЖДОЙ сгенерированной функции:
  1. Имя начинается с Test_TK_? Да / Нет
  2. Есть doc-комментарий с "ТК-N:"? Да / Нет
  3. Номер ТК-N есть в исходном Markdown тест-кейсов? Да / Нет
  4. Заголовок совпадает с ТК? Да / Нет
ЕСЛИ любая проверка FAIL → добавить // TODO: FIX TRACEABILITY
```

## Структура тестового модуля

```go
package {{test_package}}

import (
    "bytes"
    "encoding/json"
    "io"
    "net/http"
    "testing"
    "time"

    "github.com/stretchr/testify/assert"
    "github.com/stretchr/testify/require"
)

// Test_TK_{{id}}_Should_{{Description}} validates {{test_case_title}}.
// ТК-{{id}}: {{test_case_title}}
func Test_TK_{{id}}_Should_{{Description}}(t *testing.T) {
    // given: предусловия
    {{given_block}}

    // when: действие
    body := {{request_body_json_or_nil}}
    req, err := http.NewRequest(
        "{{HTTP_METHOD}}",
        testBaseURL+"{{endpoint}}",
        bytes.NewBuffer(body),
    )
    require.NoError(t, err, "failed to create request")
    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("Authorization", "Bearer "+testToken)

    client := &http.Client{Timeout: 10 * time.Second}
    resp, err := client.Do(req)
    require.NoError(t, err, "request failed")
    defer resp.Body.Close()

    // then: проверки
    assert.Equal(t, {{expected_status}}, resp.StatusCode,
        "expected status %d, got %d", {{expected_status}}, resp.StatusCode)

    respBody, err := io.ReadAll(resp.Body)
    require.NoError(t, err, "failed to read response body")

    var data map[string]interface{}
    err = json.Unmarshal(respBody, &data)
    require.NoError(t, err, "failed to parse JSON: %s", string(respBody))

    {{then_block}}
}
```

## Пример: базовый REST-тест

```go
// Test_TK_01_Should_Return200_ForValidRequest проверяет успешный GET-запрос.
// ТК-01: Успешный GET-запрос возвращает HTTP 200
func Test_TK_01_Should_Return200_ForValidRequest(t *testing.T) {
    req, err := http.NewRequest("GET", testBaseURL+"/api/v1/resource/12345", nil)
    require.NoError(t, err, "failed to create request")
    req.Header.Set("Authorization", "Bearer "+testToken)

    client := &http.Client{Timeout: 10 * time.Second}
    resp, err := client.Do(req)
    require.NoError(t, err, "request failed")
    defer resp.Body.Close()

    assert.Equal(t, 200, resp.StatusCode)

    respBody, _ := io.ReadAll(resp.Body)
    var data map[string]interface{}
    json.Unmarshal(respBody, &data)

    assert.Equal(t, "12345", data["id"])
    assert.NotEmpty(t, data["name"])
    assert.Contains(t, data, "created_at")
}
```

## Проверки по уровням

| Уровень | Ключевые проверки |
|---------|-------------------|
| HTTP | `StatusCode`, `Header.Get("X-Request-Id")`, `Header.Get("Content-Type")` |
| Тело ответа | `id`, поля, типы (`assert.IsType`), формат дат (RFC 3339) |
| Бизнес-логика | значение полей, расчёты, связи между сущностями |
| Безопасность | отсутствие паролей/токенов/секретов в теле ответа |
| Наблюдаемость | `resp.Header.Get("X-Response-Time")`, таймаут `http.Client` |
| Пустой список | `assert.Empty(t, data)`, `assert.Len(t, data, 0)` |
| Пагинация | `assert.Contains(t, data, "next")`, `assert.Contains(t, data, "count")` |

## Обработка ошибок

```go
// Test_TK_EX_Should_Return400_ForInvalidRequest проверяет ответ на некорректный запрос.
// ТК-EX: Некорректный запрос возвращает HTTP 400
func Test_TK_EX_Should_Return400_ForInvalidRequest(t *testing.T) {
    body := []byte(`{"invalid": true}`)
    req, err := http.NewRequest("POST", testBaseURL+"/api/v1/resource", bytes.NewBuffer(body))
    require.NoError(t, err, "failed to create request")
    req.Header.Set("Content-Type", "application/json")

    client := &http.Client{Timeout: 10 * time.Second}
    resp, err := client.Do(req)
    require.NoError(t, err, "request failed")
    defer resp.Body.Close()

    assert.Equal(t, 400, resp.StatusCode)

    respBody, _ := io.ReadAll(resp.Body)
    var data map[string]interface{}
    json.Unmarshal(respBody, &data)

    assert.Contains(t, data, "error")
    assert.Contains(t, data["message"], "Invalid request")
}
```

## 🚫 АНТИ-ПАТТЕРНЫ: что НЕЛЬЗЯ делать в автотестах

| ❌ Запрещено | ✅ Правильно | Почему |
|---|---|---|
| `time.Sleep(5 * time.Second)` | `require.Eventually(t, condition, 10*time.Second, 100*time.Millisecond)` | Блокирует тест, замедляет suite |
| `testBaseURL = "http://localhost:8080"` (в каждом тесте) | Константа в `testhelpers_test.go` | Хардкод в каждом файле — сложно менять |
| `testToken = "eyJhbG..."` (хардкод) | `os.Getenv("TEST_TOKEN")` или `testToken` из test helpers | Токен протухает, не rotating, утечка в git |
| `if err != nil { t.Fatal(err) }` (без контекста) | `require.NoError(t, err, "failed to create request for resource %s", id)` | Сообщение без контекста — невозможно понять, где упало |
| `if resp.StatusCode != 200 { t.Errorf("bad status") }` | `assert.Equal(t, 200, resp.StatusCode, "expected 200, got %d: %s", resp.StatusCode, body)` | Нет тела ответа — неизвестна причина ошибки |
| `json.Unmarshal(body, &data)` без проверки ошибки | `err := json.Unmarshal(body, &data); require.NoError(t, err, "invalid JSON: %s", string(body))` | 500 HTML вместо JSON — невнятная ошибка |
| `client := &http.Client{}` (без таймаута) | `client := &http.Client{Timeout: 10 * time.Second}` | Без таймаута тест может висеть бесконечно |
| Игнорирование `resp.Body.Close()` | `defer resp.Body.Close()` | Утечка соединений, исчерпание файловых дескрипторов |
| `data["field"].(string)` (без проверки типа) | `val, ok := data["field"].(string); assert.True(t, ok, "field is not a string")` | Panic при неожиданном типе |
| `TestAPI(t *testing.T)` (все тесты в одной функции) | `Test_TK_01_Should_X`, `Test_TK_02_Should_Y` (отдельные функции) | Монолитная функция — нечитаема, сложно дебажить |
| `httptest.NewServer` без `defer srv.Close()` | `defer srv.Close()` | Утечка соединений, порт остаётся занятым |

---

*См. также: `testhelpers_test.go` (базовые константы и хелперы), `Java-шаблон` (эталонный STEP 0 и traceability), `shared/stub-helper.md` (WireMock-мокирование).*