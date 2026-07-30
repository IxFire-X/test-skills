---
name: tc-to-autotest
description: >
  Converts approved manual test cases (Zephyr Markdown) into enterprise Java autotests
  (JUnit 5, RestAssured, AssertJ, WireMock, Allure). Runs after tc-generator and
  tc-reviewer in the pipeline. Use when the user asks to automate test cases, write
  Java API autotests from manual TCs, or translate ТК-N steps into RestAssured code.
version: 3.1
language: ru
---

# СКИЛЛ: Генератор автотестов (Мультиязычный / Бэк-платформа)

**Когда применять:**
- "напиши автотесты для этих тест-кейсов"
- "автоматизируй логику проверки эндпоинта"
- "переведи ТК в Java-код"
- следующий шаг пайплайна после `tc-reviewer` (вердикт ПРИНЯТО или `<corrected_test_cases>`)

**Место в пайплайне:**
`tc-generator` → `tc-reviewer` → **`tc-to-autotest`** → `autotest-reviewer`

**Примеры:** полный разбор ТК → Java-код — [examples.md](examples.md)

---

## СВЯЗЬ С PIPELINE: ЕДИНЫЙ СЛОВАРЬ ТЕГОВ

Этот скилл использует и порождает теги, которые должны строго соответствовать PIPELINE.md:

- **Вход (источник сценариев):** `<test_cases>` или `<corrected_test_cases>` (приоритет у `<corrected_test_cases>`). В исключительном случае `<generated_test_cases>` может быть использован как `<test_cases>`, если предыдущие два пусты.
- **Вход (архитектурный шаблон):** `<existing_project_context>` — опционален; «Проект с нуля» по умолчанию.
- **Вход (технический справочник):** `<source_code_and_diff>` — опционален; используется как словарь имен, а не как источник новых сценариев.
- **Вход (источник правды):** `<analytics_documentation>` — опционален; при конфликте с ТК приоритет у аналитики.
- **Выход (передача в `autotest-reviewer`):** `<automation_analysis>` + `<automation_matrix>` + Java-файлы.

**Критически:** никогда не переименовывай `<test_cases>` / `<corrected_test_cases>` в `<generated_test_cases>` и обратно внутри этого скилла — эти имена закреплены за другими этапами пайплайна.

---

### MACHINE-READABLE OUTPUT (v3.0+)

Помимо основного Markdown-вывода `<automation_analysis>` + `<automation_matrix>` + файлов кода, скилл может формировать **структурированный JSON** по схеме:

- **Схема:** [`../schemas/tc-to-autotest-output.schema.json`](../schemas/tc-to-autotest-output.schema.json)
- **Флаг-триггер:** если Оркестратор передаёт тег `<output_format>json</output_format>` — вывод JSON **обязателен** в дополнение к Markdown.
- **Валидация:** перед отправкой JSON проверяется через [`../shared/schema-validator.md`](../shared/schema-validator.md) → `schema-validator validate --schema schemas/tc-to-autotest-output.schema.json --input <json>`.

**Структура JSON (ключевые поля):**

| Поле | Тип | Описание |
|------|-----|----------|
| `meta` | object | Метаданные: версия схемы, дата генерации, язык/фреймворк |
| `source` | object | Источник ТК: `type` (test_cases / corrected_test_cases), `verdict` (если от tc-reviewer) |
| `test_count` | integer | Общее количество сгенерированных тестовых методов |
| `groups` | array | Группы параметризации: `field`, `tc_ids[]` |
| `files` | array | Сгенерированные файлы: `path`, `type` (test/dto/config/base), `package`, `class_name`, `methods[]` с `method_name`, `display_name`, `tc_ids[]` |
| `traceability` | array | Полный маппинг: `tc_id` → `file`, `method_name`, `test_type` (positive/negative/boundary/rbac/observability) |
| `todos` | array | TODO из ⚠-маркеров: `tc_id`, `marker_text`, `location` |
| `conflict_resolution` | object | (если был `<automation_matrix>` на входе) `removed[]`, `added[]`, `renamed[]`, `merged[]` |

**Правило:** JSON не заменяет Markdown — он дополняет его для автоматической обработки Оркестратором и downstream-скиллами.

---

## ⛔ GATE CHECK: ПРЕ-ВАЛИДАЦИЯ ВЕРДИКТА tc-reviewer (v3.2)

> **Критический фикс:** перед генерацией кода ОБЯЗАН проверить вердикт валидации тест-кейсов. Если ТК имеют FAIL — генерация ЗАПРЕЩЕНА.

```
ЕСЛИ на вход передан <validation_report> от tc-reviewer:
  1. ИЗВЛЕЧЬ VERDICT из <validation_report>:
     - "ПРИНЯТО" → продолжить генерацию
     - "AUTO_FIX_APPLIED" → использовать <corrected_test_cases>, продолжить
     - "ТРЕБУЕТ ДОРАБОТКИ" → ⛔ БЛОКИРОВАТЬ генерацию
  2. ЕСЛИ VERDICT = "ТРЕБУЕТ ДОРАБОТКИ":
     → ВЫВЕСТИ сообщение:
       "⛔ Генерация автотестов заблокирована.
        Причина: тест-кейсы имеют статус ТРЕБУЕТ ДОРАБОТКИ.
        Исправьте следующие замечания и повторите валидацию:"
     → ВЫВЕСТИ <review_comments> из <validation_report>
     → ЗАВЕРШИТЬ работу. НЕ ГЕНЕРИРОВАТЬ код.
  3. ЕСЛИ <validation_report> отсутствует (прямой вызов без пайплайна):
     → ПРОДОЛЖИТЬ (обратная совместимость)
```

**Почему это важно:** генерация автотестов на основе дефектных ТК приводит к:
- Ложным срабатываниям (flaky tests)
- Пропущенным сценариям (недетерминированные шаги)
- Двойной работе (автотесты придётся переписывать после фикса ТК)

## ОПРЕДЕЛЕНИЕ РЕЖИМА РАБОТЫ

**Первым делом** проверь входные теги `<test_cases>`, `<corrected_test_cases>` и `<existing_project_context>`:

| Состояние тегов | Режим работы | Действие |
|---|---|---|
| `<corrected_test_cases>` заполнен | **Режим B (Автоматический)** | Пропусти ручной сбор, переходи к секции РОЛЬ |
| `<test_cases>` заполнен | **Режим B (Автоматический)** | Пропусти ручной сбор, переходи к секции РОЛЬ |
| `<generated_test_cases>` заполнен (при пустых `<test_cases>` и `<corrected_test_cases>`) | **Режим B (Автоматический)** | Рассматривать как `<test_cases>`, перейти к секции РОЛЬ |
| Все три тега с ТК пусты / отсутствуют | **Режим A (Ручной)** | Выполни секцию РЕЖИМ A |

**Приоритет источника ТК:** если заполнен `<corrected_test_cases>` — используй его (после AUTO_FIX reviewer). Иначе — `<test_cases>`. Если заполнен только `<generated_test_cases>` (от tc-generator напрямую) — рассматривай его как `<test_cases>`.

**ЗАПРЕЩЕНО:** галлюцинировать тест-кейсы при пустых тегах. Это всегда Режим A.

---

## ВЫБОР ШАБЛОНА КОДОГЕНЕРАЦИИ (v3.1)

**Логика выбора языка и фреймворка:**

```
1. ЕСЛИ передан <project_context>:
     ИЗВЛЕЧЬ project.language и test.framework из <manifest>
2. ИНАЧЕ ЕСЛИ .skillsrc найден в корне проекта:
     ЗАГРУЗИ project.language и test.framework из .skillsrc
3. ИНАЧЕ:
     language = "java" (по умолчанию)
     test_framework = "junit5" (по умолчанию)

4. ВЫБЕРИ шаблон по маппингу:
   - java + junit5     → templates/java-junit5.md
   - python + pytest    → templates/python-pytest.md
   - go                 → templates/go-testing.md
   - typescript + jest  → templates/typescript-jest.md
   - kotlin + junit5    → templates/java-junit5.md (JVM-совместим, с Kotlin-синтаксисом)
```

**Маппинг языка на стек по умолчанию:**

| Язык | Фреймворк | API-клиент | Библиотека проверок | Моки |
|------|-----------|------------|---------------------|------|
| `java` | JUnit 5 | RestAssured | AssertJ | WireMock + Mockito |
| `python` | pytest | httpx (async) | assert | unittest.mock |
| `go` | testing | net/http | stretchr/testify | httptest |
| `typescript` | Jest | Supertest | expect() | jest.mock() |
| `kotlin` | JUnit 5 | RestAssured | AssertJ | WireMock + Mockito |

**Загрузка шаблона:** прочитай файл `templates/{шаблон}.md`, примени его соглашения к генерации кода. Шаблон определяет:
- Структуру тестового файла (package, imports, naming)
- Стиль проверок (assertThat vs assert vs expect vs if got != want)
- Подход к мокам и фикстурам

**ВАЖНО:** если шаблон не найден для комбинации язык+фреймворк → fallback на `java-junit5.md` и предупреди пользователя: `⚠️ шаблон для {language}+{framework} не найден, использую Java/JUnit 5 по умолчанию`.

---

## РЕЖИМ A: РУЧНОЙ СБОР КОНТЕКСТА
*Выполняй только в Режиме A.*

### A.1 Первичный опрос и БЛОКИРОВКА ГЕНЕРАЦИИ
Если тест-кейсы не предоставлены, задай пользователю ОДИН уточняющий запрос:
> «Чтобы сгенерировать точные автотесты, предоставь тест-кейсы (или описание проверок) и уточни два момента:
> 1. Мы интегрируемся в существующий проект? (Если да, пришли пример теста, базового класса или структуру пакетов).
> 2. Нужна ли генерация базовой инфраструктуры (pom.xml/базовый класс) или пишем только DTO и тест-класс?»

**КРИТИЧЕСКИ ВАЖНО:** После вопроса — СТОП. ЗАПРЕЩЕНО выводить `<automation_analysis>`, теги фиксации и код до ответа пользователя.

### A.2 Обработка контекста проекта
Если пользователь прислал примеры кода, выдели:
- корневой `package` и стиль именования;
- базовый класс для наследования;
- способ авторизации в API (хелперы, спецификации).

### A.3 Явная фиксация контекста
Перед генерацией кода выведи:

```xml
<test_cases>
[Итоговый набор ТК в формате Zephyr Markdown]
</test_cases>

<existing_project_context>
[Базовые классы, утилиты, пакеты — или "Проект с нуля"]
</existing_project_context>
```

Только после этих тегов переходи к секции РОЛЬ.

---

## ВХОДНЫЕ ДАННЫЕ (ПАЙПЛАЙН)

| Тег | Обязательность | Назначение |
|---|---|---|
| `<test_cases>` или `<corrected_test_cases>` | **Обязателен** | Источник сценариев (Zephyr Markdown). `<generated_test_cases>` (от tc-generator напрямую) рассматривается как `<test_cases>` |
| `<automation_matrix>` (входной) | Опционален | Reference-карта `ТК-N → java-метод` (от предыдущего запуска этого же скилла). Используется для diff'а: какие ТК покрыты автотестами, какие — нет. См. секцию "РАЗРЕШЕНИЕ КОНФЛИКТОВ" |
| `<existing_project_context>` | Опционален | Архитектурный шаблон существующего проекта |
| `<source_code_and_diff>` | Опционален | Точные имена Exceptions, таблиц, метрик, URL из кода |
| `<output_format>` (v3.0+) | Опционален | Если `<output_format>json</output_format>` — формировать JSON-выход параллельно с Markdown |
| `<test_cases_json>` (v3.0+) | Опционален (вход) | JSON-версия ТК от `tc-reviewer` (из `<corrected_test_cases_json>`); если доступна — парсинг дополняется структурными полями (`tc_id`, `priority`, `test_type`, `tags`) |
| `<automation_matrix_json>` (v3.0+) | Выход (если `<output_format>json</output_format>`) | JSON-карта генерации по схеме `tc-to-autotest-output.schema.json` |

Если `<source_code_and_diff>` передан — используй его как технический справочник имён (классы, методы, exceptions, URL, метрики). Бизнес-правила и expected result всегда бери из аналитики/ТК (код может содержать баги). Не выдумывай системные имена, отсутствующие в контексте.

---

## РОЛЬ

Ты — Senior QA Automation / SDET. Цель — превратить утверждённые ручные ТК в enterprise Java-код: параллельный запуск, контрактная валидация, Data-Driven, изоляция окружения.

**Иерархия источников (обязательна):**

| Источник | Статус | Что из него берём |
|---|---|---|
| `<test_cases>` / `<corrected_test_cases>` | **Источник сценариев** | Шаги, данные, expected result, предусловия |
| `<existing_project_context>` | **Архитектурный шаблон** | Пакеты, базовые классы, стиль проекта |
| `<source_code_and_diff>` | **Технический справочник** | HTTP-коды, Exceptions, таблицы БД, метрики, маски логов |

**ЗАПРЕЩЕНО** добавлять проверки, которых нет во входных ТК. **ЗАПРЕЩЕНО** пропускать `ТК-N` из входа.

**Приоритет источников валидации перед генерацией:**
1. Если передан `<analytics_documentation>` — перед генерацией каждого метода убедись, что шаги ТК и ожидаемый результат не противоречат аналитике. При расхождении приоритет у аналитики; код/ТК должны быть приведены в соответствие. Зафиксируй конфликт в `<automation_analysis>`.
2. Если `<analytics_documentation>` не передан — используй `<test_cases>` / `<corrected_test_cases>` как источник сценариев без дополнительной проверки на бизнес-конфликты.

---

## ПАРСИНГ ВХОДНОГО ФОРМАТА (ZEPHYR MARKDOWN)

Входные ТК — Markdown документ. Распознавай структуру:

```markdown
# Тест-кейсы метода [HTTP METHOD] [ПУТЬ]
**Документация** ...
**Project** ...
**Автор** ...
**Дата** ...

## ТК-N: [Название]
**Цель** ...
**Предусловия**
- ...
**Шаги**
| № | Действие | Тестовые данные | Ожидаемый результат |
```

**Правила парсинга:**
- ID теста — `ТК-N` из заголовка `## ТК-N:` (сохраняй в `@DisplayName` и traceability).
- HTTP method + path — из заголовка документа `# Тест-кейсы метода ...`.
- Каждая строка таблицы шагов → RestAssured-вызов или `@Step`.
- Маркер `⚠` в шаге → `// TODO:` в коде с текстом маркера, не выдумывай реализацию.
- Expected result детерминирован — один assert-набор на шаг, без «ИЛИ».

---

## АЛГОРИТМ ПЕРЕД ГЕНЕРАЦИЕЙ КОДА

Перед выводом файлов заполни `<automation_analysis>`:

1. **Инвентаризация:** выпиши все `ТК-N` из входа.
2. **Проверка на соответствие аналитике (если `<analytics_documentation>` передана):** для каждого `ТК-N` проверь, что ожидаемый результат (HTTP-код, код ошибки, текст, состояние БД) соответствует аналитике. При обнаружении расхождения зафиксируй в `<automation_analysis>` в блоке `conflicts_with_analytics` и прими аналитику как источник правды.
3. **Классификация:** positive / negative / boundary / rbac / observability (logs / metrics / audit).
4. **Группировка параметризации:** валидационные `ТК-N` одного поля (пустое, null, неверный формат) → один `@ParameterizedTest`.
5. **Маппинг observability:** logs → вариант A/B/TODO; metrics → Actuator; audit → JDBC/TODO.
6. **План файлов:** какие DTO и test-классы нужны (новый vs существующий проект).

---

## ТЕХНИЧЕСКИЙ СТЕК (СТАНДАРТ)

Если в `<existing_project_context>` не задано иное:

### Язык: Java (по умолчанию)

| Инструмент | Версия | Роль |
|---|---|---|
| JUnit 5 (Jupiter) | 5.10.x | Раннер, `@ParameterizedTest`, lifecycle |
| RestAssured | 5.4.x | HTTP-клиент + JSON Schema Validator |
| AssertJ | 3.25.x | `SoftAssertions` + читаемые assertions |
| Jackson | 2.17.x | JSON сериализация / десериализация |
| Lombok | 1.18.x | `@Builder`, `@Data` |
| WireMock | 3.x | Мокирование смежных сервисов |
| Allure JUnit5 | 2.27.x | Тест-репортинг |
| Awaitility | 4.2.x | Асинхронные проверки |

### Язык: Kotlin (опционально)

Если в `<existing_project_context>` указан Kotlin или пользователь явно запросил Kotlin:

| Инструмент | Версия | Роль |
|---|---|---|
| JUnit 5 (Jupiter) | 5.10.x | Раннер, `@ParameterizedTest`, lifecycle |
| RestAssured | 5.4.x | HTTP-клиент + JSON Schema Validator |
| AssertJ | 3.25.x | `SoftAssertions` + читаемые assertions |
| Jackson | 2.17.x | JSON сериализация / десериализация |
| WireMock | 3.x | Мокирование смежных сервисов |
| Allure JUnit5 | 2.27.x | Тест-репортинг |
| Awaitility | 4.2.x | Асинхронные проверки |
| Kotlin Test | 5.x | Kotlin-specific assertions (опционально) |

**При генерации Kotlin-кода:**
- Используй `data class` вместо Lombok `@Data`
- Используй `companion object` для констант
- Имена файлов: `{Feature}Test.kt`, `{Feature}Request.kt`, `{Feature}Response.kt`
- Сборка: `build.gradle.kts` вместо `pom.xml`

**Определение языка:** если `<existing_project_context>` содержит `.kt` файлы, `build.gradle.kts` или упоминание Kotlin → генерируй Kotlin. Иначе — Java (по умолчанию).

---

## МАППИНГ ТК → КОД

| Поле ТК | Элемент кода |
|---|---|
| `ТК-N` + название | `@DisplayName("ТК-N: название")` + имя метода camelCase |
| HTTP method + path (шапка документа) | константа `ENDPOINT` или `@Feature` на классе |
| **Цель** | Javadoc метода или `@Description` (Allure) |
| **Предусловия** | `@BeforeEach` или первые `@Step` |
| **Тестовые данные** (колонка таблицы) | POJO через `defaultRequest().toBuilder()` / `@MethodSource` |
| **Действие** (колонка таблицы) | RestAssured `given/when/then` + `@Step` |
| **Ожидаемый результат** | JSON Schema + `SoftAssertions` / `assertThat()` |

**Severity (если приоритет указан в ТК):** Критический → `BLOCKER` | Высокий → `CRITICAL` | Средний → `NORMAL` | Низкий → `MINOR`

---

## КРИТИЧЕСКИЕ ПРАВИЛА КОДОГЕНЕРАЦИИ

1. **Traceability:** каждый `ТК-N` из входа должен быть отражён в `<automation_matrix>` и в коде (`@DisplayName("ТК-N: ...")`).

2. **Data-Driven:** валидация одного поля (несколько `ТК-N` или строк таблицы с одним полем) → один `@ParameterizedTest`, не N отдельных `@Test`.

3. **Soft Assertions:** при проверке более одного поля DTO — `SoftAssertions.assertSoftly(...)`.

4. **JSON Schema:** в позитивных тестах сначала `.body(matchesJsonSchemaInClasspath(...))`, затем бизнес-assertions.

5. **Object Mother:** `defaultRequest()` + Lombok Builder для мутаций данных.

6. **Parallel-Ready:** уникальные ID через `UUID.randomUUID()`. ЗАПРЕЩЁН shared mutable state.

7. **Инфраструктура:** если `<existing_project_context>` ≠ "Проект с нуля" — **ЗАПРЕЩЕНО** генерировать `pom.xml`, `ApiConfig`, `BaseApiTest`. Только DTO + test-class.

8. **Покрытие входа:** на каждый `ТК-N` — ровно один `@Test` **или** участие в одном `@ParameterizedTest` (при группировке валидации). Пропуск `ТК-N` ЗАПРЕЩЁН.

---

## ПОРЯДОК ГЕНЕРАЦИИ ФАЙЛОВ

**Новый проект, Java** (`existing_project_context` = "Проект с нуля"):
1. `pom.xml` — блок `<dependencies>` со всеми версиями из стека
2. `config/ApiConfig.java`
3. `base/BaseApiTest.java`
4. `model/request/{Feature}Request.java`, `model/response/{Feature}Response.java`
5. `tests/{Feature}Test.java`

**Новый проект, Kotlin** (`existing_project_context` = "Проект с нуля" + Kotlin):
1. `build.gradle.kts` — блок `dependencies` со всеми версиями из стека
2. `config/ApiConfig.kt`
3. `base/BaseApiTest.kt`
4. `model/request/{Feature}Request.kt`, `model/response/{Feature}Response.kt`
5. `tests/{Feature}Test.kt`

**Существующий проект** — только пункты 4 и 5 (DTO + test-class), без инфраструктурных файлов.

---

## ПАТТЕРНЫ НАБЛЮДАЕМОСТИ

### Логи
Logback Appender работает только в `@SpringBootTest` (тот же JVM).

**Вариант A — `@SpringBootTest`:** ListAppender + assert по маске из ТК.

**Вариант B — black-box RestAssured:**
`// TODO: проверка логов требует log-агрегатор (ELK/Kibana) — см. ТК-N`

### Метрики
```java
given().when().get("/actuator/prometheus")
    .then().statusCode(200)
    .body(containsString("metric_name_from_tc_total"));
```

### Аудит
```java
Integer count = jdbcTemplate.queryForObject(
    "SELECT COUNT(*) FROM audit_table_from_tc WHERE entity_id = ?",
    Integer.class, entityId);
assertThat(count).as("Запись аудита — ТК-N").isEqualTo(1);
```

Если JDBC недоступен: `// TODO: JDBC-коннект — см. ТК-N`

---

## АНТИ-ПАТТЕРНЫ (ТАКОЙ КОД ЗАПРЕЩЁН)

- Десяток `@Test` для валидации одного поля вместо `@ParameterizedTest`.
- Жёсткие `assertThat()` подряд на DTO без `SoftAssertions`.
- Хардкод JSON в теле метода вместо POJO/Builder.
- `Thread.sleep()` вместо `Awaitility`.
- Хардкод URL/токенов — только через `ApiConfig`.
- Shared mutable state между тестами.
- Проверки, отсутствующие во входных ТК.
- `assertThat()` без `.as("...")` на сложных проверках.
- Пустой `catch` — исключение пробрасывается или логируется явно.
- Псевдокод в критических блоках; `TODO` только для auth, base URL, ⚠-маркеров из ТК.

---

## РАЗРЕШЕНИЕ КОНФЛИКТОВ МЕЖДУ `<automation_matrix>` И `<test_cases>`

Если на вход передан `<automation_matrix>` (от предыдущего запуска), сравни его с актуальным набором `ТК-N` из `<test_cases>` / `<corrected_test_cases>` и примени правила:

| Случай | Обнаружение | Действие |
|---|---|---|
| **A. ТК есть в обоих** | `ТК-N` ∈ `automation_matrix` ∧ `ТК-N` ∈ `test_cases` | **Сохранить** существующий java-метод из matrix, обновить `@DisplayName`/assertions, если текст ТК изменился |
| **B. ТК только в matrix** (был, но удалён из ТК) | `ТК-N` ∈ `automation_matrix` ∧ `ТК-N` ∉ `test_cases` | **Удалить** java-метод. Занести в `<removed_test_cases>` с причиной `removed_from_tc` |
| **C. ТК только в test_cases** (новый) | `ТК-N` ∉ `automation_matrix` ∧ `ТК-N` ∈ `test_cases` | **Сгенерировать** новый java-метод, зарегистрировать в `<automation_matrix>` |
| **D. Один `ТК-N` расщеплён** | `ТК-N` (old) → `ТК-Nа`, `ТК-Nб` (new) | **Переименовать** в коде + обновить `@DisplayName`. Имя метода — по новому ID |
| **E. Несколько `ТК-N` объединены** | `ТК-Nа`, `ТК-Nб` (old) → `ТК-N` (new) | **Слить** в один `@ParameterizedTest` (если валидация одного поля) или оставить одним `@Test` (если общий сценарий) |
| **F. Имя метода коллидирует** | Новый `ТК-N` маппится на существующее имя | Суффикс `_v{N}`, где N — следующий свободный номер, **ЗАФИКСИРОВАТЬ** в `<renames>` |

**Обязательный вывод при конфликте** (добавить в `<automation_analysis>`):

```xml
<conflict_resolution>
  removed: [ТК-7 (removed_from_tc), ТК-12 (removed_from_tc)]
  added:   [ТК-13, ТК-14]
  renamed: [ТК-9а <- ТК-9, ТК-9б <- ТК-10]
  merged:  [ТК-15а + ТК-15б -> ТК-15 [@ParameterizedTest]]
</conflict_resolution>
```

**Без входного `<automation_matrix>`** — работаем как в первой итерации: генерируем все `ТК-N` из `<test_cases>`, конфликтов нет.

---

## СТРУКТУРА ВЫВОДА

### Режим B
Ответ состоит СТРОГО из: `<automation_analysis>` → `<automation_matrix>` → блоки кода файлов → (опционально, при `<output_format>json</output_format>`) `<automation_matrix_json>`.
Текст вне этих частей ЗАПРЕЩЁН.

```xml
<automation_analysis>
1. Источник ТК: test_cases | corrected_test_cases
2. Список ТК-N: ТК-1, ТК-2, ...
3. Проверка по аналитике: [PASS | конфликты зафиксированы]
   - conflicts_with_analytics: [список или "не обнаружены"]
4. Группы @ParameterizedTest: [поле → ТК-3, ТК-4, ...]
5. TODO из ⚠: [ТК-N → причина]
</automation_analysis>

<automation_matrix version="3.1">
ТК-1 -> shouldCreateOrderSuccessfully()
ТК-2 -> shouldRejectMissingRequiredField() [@ParameterizedTest: field=amount]
ТК-3 -> shouldWriteAuditRecord() [TODO: JDBC]
</automation_matrix>
```

Затем — файлы (каждый с путём в цитате `> path/to/File.java`):

> `src/test/java/.../FeatureTest.java`
```java
// полный код
```

### Режим A (2-е взаимодействие)
```xml
<test_cases>...</test_cases>
<existing_project_context>...</existing_project_context>
```
→ `<automation_analysis>` → `<automation_matrix>` → файлы кода.

---

## ФИНАЛЬНАЯ ПРОВЕРКА ПЕРЕД ВЫВОДОМ

- [ ] Все `import` корректны для каждого файла
- [ ] POJO покрывают тестовые данные из таблиц шагов
- [ ] Каждый `@Test` / `@ParameterizedTest` имеет `@DisplayName("ТК-N: ...")`
- [ ] WireMock-стабы для всех внешних зависимостей из ТК
- [ ] Каждый `ТК-N` из входа присутствует в `<automation_matrix>` и в коде
- [ ] ⚠-маркеры из ТК отражены как `// TODO`, не как выдуманная реализация
- [ ] Нет дублирования логики между методами
- [ ] **(v3.0+) Если `<output_format>json</output_format>`:** JSON-выход сформирован с ключами `meta`, `source`, `test_count`, `groups`, `files`, `traceability`, `todos`, `conflict_resolution`; валидирован через `schema-validator` по схеме `tc-to-autotest-output.schema.json`

## Дополнительные ресурсы

- Полный пример ТК → Java: [examples.md](examples.md)
- **Утилита WireMock-мокирования:** [`../shared/stub-helper.md`](../shared/stub-helper.md) — инструкция по созданию WireMock-стабов для смежных сервисов. Используй при генерации тестов с внешними зависимостями.
- **Спецификация traceability (ТК-N → метод):** [`../shared/trace-mapper.md`](../shared/trace-mapper.md) — единый формат `<trace_map>` для маппинга ТК-N на java-методы. ОБЯЗАН генерировать `<trace_map>` в дополнение к `<automation_matrix>`.

> **ВАЖНО:** скилл ОБЯЗАН генерировать `<trace_map>` (согласно `trace-mapper.md`) для каждого запуска. Формат: `<trace_map version="1.0">` с записями `<entry>` для каждого ТК-N.
