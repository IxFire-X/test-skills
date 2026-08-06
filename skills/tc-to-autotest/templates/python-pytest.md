# Шаблон: Python + Pytest + httpx

> Загружается автоматически при `project.language = python` и `test.framework = pytest`
>
> **Версия: 2.0** — Критический фикс: проверка conftest.py + обязательная traceability (ТК-N) + анти-паттерны.

## ⚠️ STEP 0: ПРОВЕРИТЬ СУЩЕСТВОВАНИЕ conftest.py и базовых фикстур (ОБЯЗАТЕЛЬНО перед генерацией)

```
1. НАЙТИ `conftest.py` в тестовой директории:
   - Поиск в `tests/conftest.py`, `tests/**/conftest.py`
   - Поиск в <existing_project_context>.test_structure
2. ПРОЧИТАТЬ conftest.py и ИЗВЛЕЧЬ:
   - Фикстуры: `base_url`, `auth_token`, `client`, `test_db_session`, `mock_external_service`
   - Session-scoped фикстуры (БД, контейнеры)
   - Плагины: `pytest-asyncio`, `pytest-mock`, `pytest-testcontainers`
3. ЕСЛИ conftest.py НЕ НАЙДЕН:
   → ОСТАНОВИТЬ генерацию
   → ВЕРНУТЬ ошибку: "conftest.py не найден. Невозможно определить базовые фикстуры (base_url, auth_token)."
   → НЕ ГЕНЕРИРОВАТЬ код, который не запустится.
4. ЕСЛИ нет фикстуры `base_url`:
   → ЗАПРОСИТЬ у пользователя: "Укажите base_url для тестового окружения"
   → ИЛИ использовать значение по умолчанию `http://localhost:8000` с WARN в pipeline-notes.md
5. ПРОВЕРИТЬ, что фикстуры НЕ дублируются в генерируемом коде:
   → `base_url` — определяется в conftest.py, НЕ переопределять в тесте
   → `auth_token` — определяется в conftest.py, НЕ хардкодить токен в тесте
   → `client` (httpx.AsyncClient) — если есть фикстура, использовать её, иначе создать в тесте
```

## Соглашения

| Параметр | Значение |
|----------|----------|
| Базовые фикстуры | `conftest.py` (проверить существование в STEP 0!) |
| Тестовый фреймворк | pytest (`@pytest.mark`, фикстуры, `@pytest.mark.asyncio`) |
| API-клиент | httpx (асинхронный, предпочтительно) / requests (синхронный) |
| Проверки | встроенные `assert` |
| Моки | `unittest.mock` / `pytest-mock` (`mocker` fixture) |
| Тестовая БД | `testcontainers` (Postgres/MySQL) / SQLite in-memory (только для простых случаев) |
| HTTP-моки | `responses` / `pytest-httpx` / `aioresponses` |
| Именование файла | `test_*.py` |
| Именование функции | `test_should_*` |
| Префикс API | из `{{paths.api_prefix}}` или `/api` |

## 🔴 ОБЯЗАТЕЛЬНАЯ TRACEABILITY: ТК-N → имя тестовой функции и docstring

> **Каждая** тестовая функция обязана содержать `ТК-N` в имени и docstring.
> Это **НЕ опционально** — это контракт с `autotest-reviewer`, который проверяет traceability.

**Правила:**
1. Имя функции: `test_tk_{id}_should_{description}` (snake_case).
   - Пример: `test_tk_01_should_return_200_for_valid_request`
2. Docstring: первой строкой — `ТК-{id}: {test_case_title}`.
3. **НИ ОДНА** тестовая функция не может быть сгенерирована без `ТК-N` в имени.
4. Если тест-кейс не имеет номера ТК-N в Markdown — **пропустить** генерацию функции.
5. Использовать `@pytest.mark.tk("{id}")` как дополнительный маркер для фильтрации:
   ```python
   @pytest.mark.tk("01")
   async def test_tk_01_should_return_200(self):
       """ТК-01: Успешный GET-запрос возвращает HTTP 200"""
   ```

**Проверка после генерации (traceability checklist):**
```
ДЛЯ КАЖДОЙ сгенерированной функции:
  1. Имя начинается с test_tk_? Да / Нет
  2. Docstring начинается с "ТК-N:"? Да / Нет
  3. Номер ТК-N есть в исходном Markdown тест-кейсов? Да / Нет
  4. Заголовок в docstring совпадает с ТК? Да / Нет
ЕСЛИ любая проверка FAIL → добавить # TODO: FIX TRACEABILITY
```

## Структура тестового модуля

```python
import pytest
import httpx
from typing import Any


class Test{{ClassName}}:
    """{{description}}"""

    @pytest.fixture(autouse=True)
    def setup(self, base_url: str, auth_token: str):
        """
        Предусловия для всех тестов класса.
        base_url и auth_token — из conftest.py (STEP 0).
        """
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json"
        }

    @pytest.mark.asyncio
    @pytest.mark.tk("{{id}}")
    async def test_tk_{{id}}_should_{{method_name}}(self):
        """
        ТК-{{id}}: {{test_case_title}}
        """
        # given: предусловия
        {{given_block}}

        # when: действие
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            response = await client.{{http_method}}(
                "{{endpoint}}",
                headers=self.headers,
                json={{request_body}}
            )

        # then: проверки
        assert response.status_code == {{expected_status}}
        data = response.json()
        {{then_block}}
```

## Пример: базовый REST-тест

```python
@pytest.mark.asyncio
@pytest.mark.tk("01")
async def test_tk_01_should_return_200_for_valid_request(self):
    """
    ТК-01: Успешный GET-запрос возвращает HTTP 200
    """
    async with httpx.AsyncClient(base_url=self.base_url) as client:
        response = await client.get(
            "/api/v1/resource/12345",
            headers=self.headers
        )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == "12345"
    assert data["name"] != ""
    assert "created_at" in data
```

## Проверки по уровням

| Уровень | Ключевые проверки |
|---------|-------------------|
| HTTP | `status_code`, `headers["X-Request-Id"]`, `headers["Content-Type"]` |
| Тело ответа | `id`, поля, типы (`isinstance`), формат дат (ISO 8601) |
| Бизнес-логика | значение полей, расчёты, связи между сущностями |
| Безопасность | отсутствие паролей/токенов/секретов в теле ответа |
| Наблюдаемость | `response.elapsed.total_seconds() < 5.0`, `X-Response-Time` |
| Пустой список | `data == []`, `len(data) == 0` |
| Пагинация | `"next" in data`, `"previous" in data`, `"count" in data` |

## Обработка ошибок

```python
@pytest.mark.asyncio
@pytest.mark.tk("EX")
async def test_tk_ex_should_return_400_for_invalid_request(self):
    """
    ТК-EX: Некорректный запрос возвращает HTTP 400
    """
    async with httpx.AsyncClient(base_url=self.base_url) as client:
        response = await client.post(
            "/api/v1/resource",
            headers=self.headers,
            json={"invalid": True}
        )
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert "Invalid request" in data["message"]
```

## 🚫 АНТИ-ПАТТЕРНЫ: что НЕЛЬЗЯ делать в автотестах

| ❌ Запрещено | ✅ Правильно | Почему |
|---|---|---|
| `time.sleep(5)` | `await asyncio.sleep(0.1)` + retry / polling | Блокирует event loop, замедляет тесты |
| `base_url = "http://localhost:8080"` | `self.base_url` из фикстуры `conftest.py` | Хардкод порта ломает CI/параллельный запуск |
| `token = "eyJhbG..."` (хардкод) | `self.headers` из фикстуры `auth_token` | Токен протухает, не rotating |
| `requests.get(...)` (синхронный) | `httpx.AsyncClient(...)` | В асинхронном проекте синхронные вызовы блокируют event loop |
| `import unittest; class TestX(unittest.TestCase)` | `class TestX:` (pytest native) | Смешивание фреймворков, конфликт фикстур |
| `data = response.json(); print(data)` | `assert data["id"] == expected_id` | Вывод в stdout вместо assertion |
| `assert response.status_code == 200` (без сообщения) | `assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"` | Невозможно диагностировать падение без контекста |
| `mock.patch("module.func")` без очистки | `mocker.patch("module.func")` (pytest-mock, auto-cleanup) | Ручной `patch` без `stop()` засоряет другие тесты |
| `class TestAllInOne:` (50+ методов) | Разделение на `TestXxxCreate`, `TestXxxUpdate`, `TestXxxDelete` | Монолитный тестовый класс нечитаем |
| `assert data == {"id": 1, "name": "x", ...}` (полный dict) | Выборочные поля: `assert data["id"] == 1`, `assert data["name"] == "x"` | Один новый field в API — поломка всех тестов |
| `response.json()` без проверки Content-Type | `assert response.headers["Content-Type"] == "application/json"` → затем `.json()` | 500 HTML-страница вместо JSON → невнятная ошибка парсинга |

---

*См. также: `conftest.py` (базовые фикстуры), `Java-шаблон` (эталонный STEP 0 и traceability), `shared/stub-helper.md` (WireMock-мокирование).*