# Шаблон: TypeScript + Jest + Supertest

> Загружается автоматически при `project.language = typescript` и `test.framework = jest`
>
> **Версия: 2.0** — Критический фикс: проверка setup.ts + обязательная traceability (ТК-N) + анти-паттерны.

## ⚠️ STEP 0: ПРОВЕРИТЬ СУЩЕСТВОВАНИЕ test setup и фикстур (ОБЯЗАТЕЛЬНО перед генерацией)

```
1. НАЙТИ test setup в проекте:
   - Поиск `jest.config.ts` / `jest.config.js` — глобальная конфигурация
   - Поиск `jest.setup.ts` / `test/setup.ts` — setup-файлы
   - Поиск фикстур / хелперов: `test/helpers.ts`, `test/fixtures/`, `test/utils/`
   - Поиск в <existing_project_context>.test_structure
2. ПРОЧИТАТЬ jest.config и ИЗВЛЕЧЬ:
   - `globalSetup` / `setupFilesAfterFramework` — глобальная инициализация
   - `testEnvironment` — `node` / `jsdom`
   - `moduleNameMapper` — алиасы путей
   - Переменные окружения: `API_URL`, `TEST_TOKEN`, `DB_URL`
3. ПРОЧИТАТЬ test helpers и ИЗВЛЕЧЬ:
   - Функции: `createTestUser()`, `getAuthToken()`, `seedDatabase()`, `cleanupDatabase()`
   - Типы: `TestContext`, `TestUser`
   - Моки: `mockExternalService()`, `mockPaymentGateway()`
4. ЕСЛИ jest.config НЕ НАЙДЕН:
   → ПРЕДУПРЕДИТЬ пользователя: "Не найден jest.config. Будут использованы значения по умолчанию."
   → ИСПОЛЬЗОВАТЬ: `API_URL = process.env.API_URL || 'http://localhost:3000'`
5. ПРОВЕРИТЬ, что переменные окружения НЕ дублируются:
   → `API_URL` — из `jest.config.globals` или `.env.test`, НЕ переопределять в тесте
   → `TEST_TOKEN` — из test helpers, НЕ хардкодить токен
```

## Соглашения

| Параметр | Значение |
|----------|----------|
| Test setup | `jest.config.ts`, `jest.setup.ts` (проверить существование в STEP 0!) |
| Тестовый фреймворк | Jest (`describe`, `it`, `beforeAll`, `afterAll`) |
| API-клиент | Supertest (`request(app)`) или `axios` / `node-fetch` + `nock` |
| Проверки | встроенные `expect()` |
| Моки | `jest.mock()`, `jest.spyOn()` |
| HTTP-моки | `nock` (`github.com/nock/nock`) — для внешних API |
| Тестовая БД | `testcontainers` (через `@testcontainers/postgresql`) / Docker Compose |
| TypeScript | Да (`.ts`, `.d.ts`) |
| Именование файла | `*.test.ts` / `*.spec.ts` |
| Именование теста | `it('ТК-{id}: {description}', async () => ...)` |
| Префикс API | из `{{paths.api_prefix}}` или `/api` |
| База URL | из `process.env.API_URL` |

## 🔴 ОБЯЗАТЕЛЬНАЯ TRACEABILITY: ТК-N → имя теста

> **Каждый** `it()` / `test()` обязан содержать `ТК-N` в первом аргументе (имя теста).
> Это **НЕ опционально** — это контракт с `autotest-reviewer`, который проверяет traceability.

**Правила:**
1. Имя теста: `'ТК-{id}: {test_case_title}'`.
   - Пример: `it('ТК-01: Успешный GET-запрос возвращает HTTP 200', async () => ...)`
2. **НИ ОДИН** тест не может быть сгенерирован без `ТК-N` в имени.
3. Если тест-кейс не имеет номера ТК-N в Markdown — **пропустить** генерацию теста.
4. `describe`-блок должен группировать тесты по сущности/контроллеру, например:
   ```typescript
   describe('POST /api/v1/transfers', () => {
     it('ТК-01: Создание заявки с валидными данными', async () => { ... });
     it('ТК-02: Отклонение заявки с amount = 0', async () => { ... });
   });
   ```

**Проверка после генерации (traceability checklist):**
```
ДЛЯ КАЖДОГО сгенерированного it():
  1. Имя начинается с "ТК-N:"? Да / Нет
  2. Номер ТК-N есть в исходном Markdown тест-кейсов? Да / Нет
  3. Заголовок совпадает с ТК? Да / Нет
ЕСЛИ любая проверка FAIL → добавить // TODO: FIX TRACEABILITY
```

## Структура тестового модуля

```typescript
import request from 'supertest';
import { app } from '../app';

const BASE_URL = process.env.API_URL || 'http://localhost:3000';

describe('{{description}}', () => {
  const headers = {
    Authorization: `Bearer ${process.env.TEST_TOKEN}`,
    'Content-Type': 'application/json',
  };

  beforeAll(async () => {
    // Setup: seed database, create test user, etc.
    {{before_all_block}}
  });

  afterAll(async () => {
    // Teardown: cleanup database, delete test user, etc.
    {{after_all_block}}
  });

  it('ТК-{{id}}: {{test_case_title}}', async () => {
    // given: предусловия
    {{given_block}}

    // when: действие
    const response = await request(BASE_URL)
      .{{httpMethod}}('{{endpoint}}')
      .set(headers)
      .send({{request_body}});

    // then: проверки
    expect(response.status).toBe({{expected_status}});
    {{then_block}}
  });
});
```

## Пример: базовый REST-тест

```typescript
import request from 'supertest';

const BASE_URL = process.env.API_URL || 'http://localhost:3000';

describe('GET /api/v1/resource/:id', () => {
  const headers = {
    Authorization: `Bearer ${process.env.TEST_TOKEN}`,
  };

  it('ТК-01: Успешный GET-запрос возвращает HTTP 200', async () => {
    const response = await request(BASE_URL)
      .get('/api/v1/resource/12345')
      .set(headers);

    expect(response.status).toBe(200);
    expect(response.body.id).toBe('12345');
    expect(response.body.name).toBeDefined();
    expect(response.body.createdAt).toBeDefined();
  });
});
```

## Проверки по уровням

| Уровень | Ключевые проверки |
|---------|-------------------|
| HTTP | `response.status`, `response.headers['x-request-id']`, `response.headers['content-type']` |
| Тело ответа | `response.body.id`, поля, типы (`typeof`, `instanceof`), формат дат (ISO 8601) |
| Бизнес-логика | значение полей, расчёты, связи между сущностями |
| Безопасность | отсутствие паролей/токенов/секретов в `response.body` |
| Наблюдаемость | `response.headers['x-response-time']`, таймаут Jest (`jest.setTimeout`) |
| Пустой ответ / 204 | `expect(response.body).toEqual({})` или `expect(response.text).toBe('')` |
| Пустой список | `expect(response.body).toEqual([])`, `expect(response.body).toHaveLength(0)` |
| Пагинация | `expect(response.body).toHaveProperty('next')`, `expect(response.body).toHaveProperty('count')` |

## Обработка ошибок

```typescript
it('ТК-EX: Некорректный запрос возвращает HTTP 400', async () => {
  const response = await request(BASE_URL)
    .post('/api/v1/resource')
    .set(headers)
    .send({ invalid: true });

  expect(response.status).toBe(400);
  expect(response.body.error).toBeDefined();
  expect(response.body.message).toContain('Invalid request');
});
```

## 🚫 АНТИ-ПАТТЕРНЫ: что НЕЛЬЗЯ делать в автотестах

| ❌ Запрещено | ✅ Правильно | Почему |
|---|---|---|
| `await new Promise(r => setTimeout(r, 5000))` | `await waitFor(() => expect(...), { timeout: 10000 })` / polling | Блокирует поток, замедляет suite ненадёжно |
| `const BASE_URL = 'http://localhost:3000'` (в каждом файле) | `process.env.API_URL` (из `jest.config.globals`) | Хардкод порта ломает CI/параллельный запуск |
| `const token = 'eyJhbG...'` (хардкод) | `process.env.TEST_TOKEN` из `jest.setup.ts` | Токен протухает, не rotating, утечка в git |
| `jest.mock('./service')` без `beforeEach(() => jest.clearAllMocks())` | `jest.clearAllMocks()` в `beforeEach` | Состояние моков протекает между тестами |
| `expect(response.body).toEqual({ id: 1, name: 'x', ... })` (полный объект) | `expect(response.body).toMatchObject({ id: 1, name: 'x' })` | Новый field в API — поломка всех тестов |
| `console.log(response.body)` | `expect(response.body).toBeDefined()` + проверки полей | Вывод в консоль вместо assertion |
| `expect(response.status).toBe(200)` (без сообщения) | `expect(response.status).toBe(200); if (response.status !== 200) console.error(response.body)` — ИЛИ вынести в кастомный matcher | Падение без контекста — не видно тело ответа |
| `response.body` без проверки `response.headers['content-type']` | `expect(response.headers['content-type']).toMatch(/json/)` → затем `.body` | 500 HTML вместо JSON → невнятная ошибка |
| `test(...)` и `it(...)` вперемешку | Использовать только `it()` (консистентность) | Путаница в отчётах |
| Один `describe` на 50+ `it()` | Группировка: `POST /api/xxx`, `GET /api/xxx/:id`, `PUT /api/xxx/:id` | Монолитный блок нечитаем |
| `request(app)` напрямую (без BASE_URL) | `request(BASE_URL)` — единообразно, поддерживает удалённые окружения | Невозможно запустить против staging-окружения |

---

*См. также: `jest.config.ts` (глобальная конфигурация), `jest.setup.ts` (фикстуры и моки), `Java-шаблон` (эталонный STEP 0 и traceability), `shared/stub-helper.md` (WireMock-мокирование).*