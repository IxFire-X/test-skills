# Примеры: Аналитика + Код → Ручные тест-кейсы (Zephyr Markdown)

Полный эталон для скилла `tc-generator`. Показывает парсинг входа, `<analysis>` и целевые ТК.

---

## Вход: аналитика + код

```xml
<analytics_documentation>
Фича: Создание заявки через REST API

Бизнес-требования:
- POST /api/v1/orders — создание заявки
- Обязательные поля: orderId (строка, до 50 симв.), amount (целое, > 0), currency (RUB, USD, EUR)
- Возвращает HTTP 201. Тело: { "orderId": "...", "status": "CREATED" }
- При отсутствии amount → HTTP 400, код VALIDATION_ERROR, текст "amount is required"
- При amount ≤ 0 → HTTP 400, код VALIDATION_ERROR, текст "amount must be positive"
- Роль OPERATOR — создание разрешено; роль VIEWER — HTTP 403
- Запись в таблицу orders (колонки: order_id, status, amount, currency)
- Лог INFO при успешном создании: "Order created: {orderId}"
- Метрика: orders_created_total
</analytics_documentation>

<source_code_and_diff>
- Controller: OrderController.createOrder()
- Таблица БД: orders (order_id, status, amount, currency)
- Exception: ValidationException → HTTP 400, code VALIDATION_ERROR
- Exception: AccessDeniedException → HTTP 403
- Лог: OrderService — INFO "Order created: {}"
- Метрика: orders_created_total (Counter)
</source_code_and_diff>
```

---

## Выход: анализ

```xml
<analysis>
1. Источник правды — ЧТО тестируем:
   - Создание заявки POST /api/v1/orders
   - Обязательные поля: orderId, amount, currency
   - Валидация: amount обязателен, amount > 0, currency ∈ {RUB, USD, EUR}
   - Роли: OPERATOR (успех), VIEWER (403)
   - Observability: лог INFO, метрика orders_created_total

2. Технический справочник — КАК верифицируем:
   - Таблица: orders (order_id, status, amount, currency)
   - Exceptions: ValidationException (400, VALIDATION_ERROR), AccessDeniedException (403)
   - Лог: OrderService INFO "Order created: {}"
   - Метрика: orders_created_total

3. Конфликты кода и аналитики: не обнаружены

4. Чеклист тест-дизайна: Happy Path, Валидация полей, Ролевая модель, Негатив, Наблюдаемость
</analysis>
```

---

## Выход: тест-кейсы

```xml
<generated_test_cases>
<![CDATA[
# Тест-кейсы метода POST /api/v1/orders

**Документация:** Фича: Создание заявки через REST API
**Project:** BILLING
**Автор:** tc-generator
**Дата:** 2026-07-01

---

## ТК-1: Успешное создание заявки с обязательными полями

**Цель:** Проверить happy path создания заявки через API с ролью OPERATOR.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` с телом запроса | `{ "orderId": "ORD-001", "amount": 100, "currency": "RUB" }` | HTTP 201, в теле `orderId=ORD-001`, `status=CREATED` |
| 2 | Проверить запись в таблице `orders` | `SELECT order_id, status, amount, currency FROM orders WHERE order_id = 'ORD-001'` | Одна строка: `order_id=ORD-001`, `status=CREATED`, `amount=100`, `currency=RUB` |

---

## ТК-2: Отклонение запроса без обязательного поля amount

**Цель:** Проверить валидацию обязательного поля `amount`.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` без поля `amount` | `{ "orderId": "ORD-002", "currency": "RUB" }` | HTTP 400, код ошибки `VALIDATION_ERROR`, текст содержит `amount is required` |

---

## ТК-3: Отклонение запроса с amount = 0

**Цель:** Проверить граничное значение amount (0 — некорректно, так как > 0).

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` с `amount = 0` | `{ "orderId": "ORD-003", "amount": 0, "currency": "RUB" }` | HTTP 400, код ошибки `VALIDATION_ERROR`, текст содержит `amount must be positive` |

---

## ТК-4: Отклонение запроса с ролью VIEWER

**Цель:** Проверить блокировку запроса для пользователя без прав на создание.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `VIEWER`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` от пользователя с ролью `VIEWER` | `{ "orderId": "ORD-004", "amount": 100, "currency": "RUB" }` | HTTP 403, класс исключения `AccessDeniedException` |

---

## ТК-5: Проверка записи в лог при успешном создании заявки

**Цель:** Проверить, что успешное создание заявки фиксируется в логе с правильным уровнем и текстом.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`
- Настроен сбор логов `OrderService`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Отправить POST `/api/v1/orders` с валидными данными | `{ "orderId": "ORD-005", "amount": 200, "currency": "USD" }` | HTTP 201 |
| 2 | Проверить лог `OrderService` | Уровень `INFO`, маска `Order created: ORD-005` | В логе присутствует строка с уровнем `INFO` и текстом `Order created: ORD-005` |

---

## ТК-6: Проверка метрики orders_created_total

**Цель:** Проверить инкремент метрики `orders_created_total` при успешном создании заявки.

**Предусловия**
- Сервис `order-service` доступен
- Пользователь авторизован с ролью `OPERATOR`
- Метрики доступны через `/actuator/prometheus`

**Шаги**

| № | Действие | Тестовые данные | Ожидаемый результат |
|---|----------|-----------------|---------------------|
| 1 | Зафиксировать текущее значение метрики `orders_created_total` | GET `/actuator/prometheus` | Значение `X` (сохранить) |
| 2 | Отправить POST `/api/v1/orders` с валидными данными | `{ "orderId": "ORD-006", "amount": 300, "currency": "EUR" }` | HTTP 201 |
| 3 | Проверить метрику `orders_created_total` | GET `/actuator/prometheus` | Значение = `X + 1` |
]]>
</generated_test_cases>
```

---

## Ключевые соответствия (чеклист эталона)

| Правило скилла | Как отражено в примере |
|---|---|
| Аналитика — источник сценариев | Все 6 ТК выведены строго из `<analytics_documentation>` |
| Код — технический справочник | Имена таблиц (`orders`), колонок, исключений (`ValidationException`, `AccessDeniedException`), маска лога взяты из кода *as is* |
| Детерминированность | Каждый ожидаемый результат — конкретен, без «ИЛИ» |
| Нумерация | `ТК-N` сквозная, без пропусков |
| Happy Path | ТК-1 |
| Валидация полей | ТК-2 (пропуск обязательного), ТК-3 (граница 0) |
| Ролевая модель | ТК-4 (VIEWER → 403) |
| Observability | ТК-5 (лог), ТК-6 (метрика) — только на упомянутые в аналитике аспекты |
| Нет фраз-заглушек | Все имена таблиц, колонок, исключений — точные |